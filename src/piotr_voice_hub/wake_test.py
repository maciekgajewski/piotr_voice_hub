"""T-013 live wake test: `piotr-voice-hub-wake-test --config hub.yaml`.

Connects to the configured satellites and prints, per satellite, what the hub would see:
wake candidates from STT partials, the final transcript, whether it wakes, the remainder, and
latencies. No session, agent or playback (T-010, T-012, T-014). Ctrl-C to stop.
"""

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from piotr_voice_hub.cli import EXIT_CONFIG_ERROR, LOG_FORMAT
from piotr_voice_hub.config import LOG_LEVELS, ConfigError, HubConfig, load_config
from piotr_voice_hub.hub.wake import match_wake_phrase
from piotr_voice_hub.ids import new_id
from piotr_voice_hub.satellites.interfaces import Microphone, MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    ListeningStarted,
    MicrophoneCommand,
    ResetWakeCandidate,
    SetVisualState,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    VisualState,
)
from piotr_voice_hub.stt.whisper import StreamingTextEvent, TextEnd

BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
SEGMENT_MARGIN_SECONDS = 0.1  # the driver ends a segment on a whole 32 ms frame

type Printer = Callable[[str, str], None]
type Sleep = Callable[[float], Awaitable[None]]


class SttSession(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...
    async def receive_text(self) -> StreamingTextEvent: ...
    async def transcribe_final(self) -> str: ...
    async def close(self) -> None: ...


class Stt(Protocol):
    def create_streaming_session(self, session_id: str, max_audio_seconds: float) -> SttSession: ...


def print_line(satellite_id: str, message: str) -> None:
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{now} [{satellite_id}] {message}", flush=True)


@dataclasses.dataclass
class _Segment:
    utterance_id: str
    session: SttSession
    started_at: float
    partials: "asyncio.Task[None] | None" = None
    wake_partial: str | None = None
    wake_partial_at: float | None = None


@dataclasses.dataclass
class SatelliteRunner:
    """One satellite's loop: arm, segment, transcribe, match, print; re-arm on failure."""

    satellite_id: str
    microphone: Microphone
    stt: Stt
    max_audio_seconds: float
    out: Printer = print_line
    clock: Callable[[], float] = time.monotonic
    sleep: Sleep = asyncio.sleep
    _commands: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)

    async def run(self) -> None:
        backoff = BACKOFF_FIRST_SECONDS
        while True:
            listen_id = new_id("listen")
            try:
                await self._command(SetVisualState(VisualState.IDLE))
                await self._command(StartListening(listen_id))
                await self._expect_listening_started(listen_id)
                backoff = BACKOFF_FIRST_SECONDS
                self.out(self.satellite_id, f"listening (listen_id={listen_id})")
                await self._read_segments(listen_id)
            except MicrophoneUnavailable as error:
                self.out(self.satellite_id, f"unavailable: {error}; retry in {backoff:g} s")
                await self.microphone.close()
                await self.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

    async def _command(self, command: MicrophoneCommand) -> None:
        async with self._commands:  # the partial reader also sends visuals
            await self.microphone.send_output_event(command)

    async def _expect_listening_started(self, listen_id: str) -> None:
        event = await self.microphone.wait_for_event()
        assert event == ListeningStarted(listen_id), event

    async def _read_segments(self, listen_id: str) -> None:
        segment: _Segment | None = None
        try:
            while True:
                event = await self.microphone.wait_for_event()
                if isinstance(event, SpeechStarted):
                    segment = self._start_segment(event.utterance_id)
                elif isinstance(event, AudioChunk):
                    assert segment is not None and event.utterance_id == segment.utterance_id
                    await segment.session.send_audio(event.data)
                elif isinstance(event, SpeechEnded):
                    assert segment is not None and event.utterance_id == segment.utterance_id
                    current, segment = segment, None
                    await self._finish_segment(listen_id, current, event.reason)
                else:
                    raise AssertionError(f"unexpected event while listening: {event}")
        finally:
            if segment is not None:
                await self._abandon(segment)

    def _start_segment(self, utterance_id: str) -> _Segment:
        session = self.stt.create_streaming_session(utterance_id, self.max_audio_seconds)
        segment = _Segment(utterance_id, session, self.clock())
        segment.partials = asyncio.create_task(self._read_partials(segment))
        self.out(self.satellite_id, f"speech started ({utterance_id})")
        return segment

    async def _read_partials(self, segment: _Segment) -> None:
        while not isinstance(event := await segment.session.receive_text(), TextEnd):
            if segment.wake_partial is not None:
                continue
            match = match_wake_phrase(event.text)
            if match is None:
                continue
            segment.wake_partial = event.text
            segment.wake_partial_at = self.clock()
            latency = segment.wake_partial_at - segment.started_at
            self.out(
                self.satellite_id,
                f"wake partial +{latency:.2f}s after speech start: {event.text!r}",
            )
            await self._command(SetVisualState(VisualState.LISTENING))

    async def _finish_segment(self, listen_id: str, segment: _Segment, reason: str) -> None:
        ended_at = self.clock()
        try:
            text = await segment.session.transcribe_final()
        finally:
            await segment.session.close()
        final_latency = self.clock() - ended_at
        assert segment.partials is not None
        await segment.partials  # the session emitted TextEnd; re-raises a partial failure
        match = match_wake_phrase(text)
        length = ended_at - segment.started_at
        if match is not None:
            verdict = f"WAKE remainder={match.remainder!r}"
        elif segment.wake_partial is not None:
            verdict = "no wake (partial candidate rejected by the final)"
        else:
            verdict = "no wake"
        wake_latency = (
            "-"
            if segment.wake_partial_at is None
            else f"{segment.wake_partial_at - segment.started_at:.2f}s"
        )
        self.out(
            self.satellite_id,
            f"final {text!r}: {verdict} | segment {length:.2f}s ({reason}), "
            f"end→final {final_latency:.2f}s, start→wake partial {wake_latency}",
        )
        if segment.wake_partial is not None:
            if match is None:
                await self._command(ResetWakeCandidate(listen_id, segment.utterance_id))
            await self._command(SetVisualState(VisualState.IDLE))

    async def _abandon(self, segment: _Segment) -> None:
        await segment.session.close()
        if segment.partials is not None:
            segment.partials.cancel()
            with contextlib.suppress(asyncio.CancelledError, MicrophoneUnavailable):
                await segment.partials


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="piotr-voice-hub-wake-test",
        description="Live wake test: print wake candidates and final transcripts per satellite.",
    )
    parser.add_argument("--config", required=True, type=Path, help="path to the hub YAML config")
    parser.add_argument(
        "--satellite",
        action="append",
        default=[],
        metavar="ID",
        help="test only this satellite id (repeatable; default: all)",
    )
    parser.add_argument("--log-level", choices=LOG_LEVELS, help="overrides log_level")
    return parser.parse_args(argv)


def select_satellites(config: HubConfig, ids: Sequence[str]) -> HubConfig:
    known = [s.id for s in config.satellites]
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise ConfigError(f"unknown --satellite {', '.join(unknown)} (known: {', '.join(known)})")
    if not ids:
        return config
    return dataclasses.replace(
        config, satellites=tuple(s for s in config.satellites if s.id in ids)
    )


async def run(config: HubConfig) -> None:
    # Imported here so the unit tests and --help do not load onnxruntime or faster-whisper.
    from piotr_voice_hub.satellites.esphome import EsphomeSatellite
    from piotr_voice_hub.satellites.vad import SileroVad, load_session
    from piotr_voice_hub.stt.whisper import FasterWhisperSpeechToText

    stt = FasterWhisperSpeechToText(config.stt)
    await stt.start()
    vad_session = load_session()
    satellites = [
        (s, EsphomeSatellite(s, SileroVad(vad_session, s.end_silence_seconds)))
        for s in config.satellites
    ]
    try:
        async with asyncio.TaskGroup() as group:
            for satellite, microphone in satellites:
                print_line(satellite.id, f"connecting to {satellite.address}")
                runner = SatelliteRunner(
                    satellite.id,
                    microphone,
                    stt,
                    satellite.max_segment_seconds + SEGMENT_MARGIN_SECONDS,
                )
                group.create_task(runner.run())
    finally:
        for _, microphone in satellites:
            await microphone.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(format=LOG_FORMAT, stream=sys.stderr)
    overrides: dict[str, object] = {} if args.log_level is None else {"log_level": args.log_level}
    try:
        config = select_satellites(load_config(args.config, overrides), args.satellite)
    except ConfigError as error:
        logging.getLogger("piotr_voice_hub").error("Invalid config: %s", error)
        return EXIT_CONFIG_ERROR
    logging.getLogger().setLevel(config.log_level)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass
    return 0
