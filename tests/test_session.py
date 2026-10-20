"""T-010, T-017: the satellite session state machine, with a fake driver, STT and TTS."""

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.hub import session as session_module
from piotr_voice_hub.hub.commands import CommandError, ErrorCode
from piotr_voice_hub.hub.events import (
    HubEvent,
    PlaybackFinished,
    SatelliteState,
    SatelliteStatus,
    TranscriptFinal,
    TranscriptPartial,
    Wake,
    WakeCancelled,
)
from piotr_voice_hub.hub.session import SatelliteSession
from piotr_voice_hub.satellites.interfaces import MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    CueFinished,
    CueType,
    ListeningStarted,
    ListeningStopped,
    MicrophoneCommand,
    MicrophoneEvent,
    PlaybackBegin,
    PlaybackChunk,
    PlaybackEnd,
    PlayCue,
    ResetWakeCandidate,
    SetVisualState,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
    VisualState,
)
from piotr_voice_hub.satellites.messages import PlaybackFinished as DriverPlaybackFinished
from piotr_voice_hub.satellites.types import MicrophoneContext, PlaybackTarget
from piotr_voice_hub.stt.whisper import StreamingTextEvent, TextEnd, TextPartial
from piotr_voice_hub.tts.interfaces import AudioFormat, TtsError

TIMEOUT = 2.0
CHUNK = b"\x01\x00" * 320  # 20 ms


def config(reply_timeout: float = 0.05) -> SatelliteConfig:
    return SatelliteConfig("kitchen", "kitchen", "10.0.0.5", "a2V5", 0.8, 15, reply_timeout, 8)


# Fakes


class FakeSttSession:
    def __init__(self, partials: list[str], final: str, final_delay: float) -> None:
        self.partials = deque(partials)
        self.final = final
        self.final_delay = final_delay
        self.audio = bytearray()
        self.ended = asyncio.Event()
        self.closed = False
        self.linger = 0.0  # seconds the partial reader takes to unwind when cancelled
        self.cancel_seen = asyncio.Event()
        self.late: list[tuple[float, str]] = []  # (delay, text): partials that arrive later
        self.close_delay = 0.0
        self.final_started = False
        self.close_started = False

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def receive_text(self) -> StreamingTextEvent:
        if self.partials:
            await asyncio.sleep(0)
            return TextPartial(self.partials.popleft(), 0.0, 1.0)
        if self.late:
            delay, text = self.late.pop(0)
            await asyncio.sleep(delay)
            return TextPartial(text, 0.0, 1.0)
        try:
            await self.ended.wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await asyncio.sleep(self.linger)
            raise
        return TextEnd()

    async def transcribe_final(self) -> str:
        self.final_started = True
        while self.partials:  # like the real session: in-flight partials come first
            await asyncio.sleep(0)
        await asyncio.sleep(self.final_delay)
        self.ended.set()
        return self.final

    async def close(self) -> None:
        self.closed = True
        self.ended.set()
        self.close_started = True
        await asyncio.sleep(self.close_delay)


class FakeStt:
    def __init__(self) -> None:
        self.scripts: dict[str, tuple[list[str], str, float]] = {}
        self.sessions: dict[str, tuple[float, FakeSttSession]] = {}
        self.linger = 0.0
        self.setup: Callable[[FakeSttSession], None] = lambda _session: None

    def say(
        self, utterance_id: str, final: str, partials: tuple[str, ...] = (), delay: float = 0.0
    ) -> None:
        self.scripts[utterance_id] = (list(partials), final, delay)

    def create_streaming_session(self, session_id: str, max_audio_seconds: float) -> FakeSttSession:
        partials, final, delay = self.scripts[session_id]
        session = FakeSttSession(partials, final, delay)
        session.linger = self.linger
        self.setup(session)
        self.sessions[session_id] = (max_audio_seconds, session)
        return session


PIPER = AudioFormat(22050, 2, 1)


class FakeSpeech:
    def __init__(self, tts: "FakeTts") -> None:
        self.format = tts.format
        self._tts = tts

    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        for chunk in self._tts.chunks:
            await asyncio.sleep(self._tts.chunk_delay)
            yield chunk
        if self._tts.fail_after_chunks:
            raise TtsError("connection closed before audio-stop")


class FakeTts:
    """Yields `chunks` in `format`. `fail_at_start` raises before the format (no audio);
    `fail_after_chunks` raises after the chunks."""

    def __init__(self) -> None:
        self.format = PIPER
        self.chunks: list[bytes] = [b"\x00\x01" * 2205] * 3  # 3 x 0.1 s
        self.chunk_delay = 0.0
        self.fail_at_start = False
        self.fail_after_chunks = False
        self.texts: list[str] = []
        self.open = 0  # syntheses entered and not yet exited

    async def start(self) -> None:
        pass

    @asynccontextmanager
    async def synthesize(self, text: str) -> AsyncIterator[FakeSpeech]:
        self.texts.append(text)
        await asyncio.sleep(0)
        if self.fail_at_start:
            raise TtsError("could not connect to the TTS service: refused")
        self.open += 1
        try:
            yield FakeSpeech(self)
        finally:
            self.open -= 1


type Item = MicrophoneEvent | Exception
type Script = Callable[[str], list[Item]]  # listen id -> items queued after that arm


class FakeDriver:
    """Answers StartListening, StopListening and PlayCue like the real driver. `after_arm[n]`
    is queued after the n-th successful arm (0-based)."""

    def __init__(self) -> None:
        self.context = MicrophoneContext("fake", "kitchen", "kitchen")
        self.playback_target = PlaybackTarget("fake", "kitchen", "10.0.0.5", "a2V5")
        self.events: asyncio.Queue[Item] = asyncio.Queue()
        self.commands: list[MicrophoneCommand] = []
        self.after_arm: dict[int, Script] = {}
        self.arms = 0
        self.fail_starts = 0
        self.silent: set[type] = set()  # command types the driver never answers
        self.fail_on: set[type] = set()  # command types that fail (once each) as a disconnect
        self.slow: dict[type, float] = {}  # command type -> seconds its send takes
        self.closes = 0
        self.in_flight = False
        self.overlapped = False

    def push(self, *items: Item) -> None:
        for item in items:
            self.events.put_nowait(item)

    async def wait_for_event(self) -> MicrophoneEvent:
        await asyncio.sleep(0)  # like a real reader: other tasks run between events
        item = await self.events.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self.overlapped |= self.in_flight
        self.in_flight = True
        try:
            await asyncio.sleep(self.slow.get(type(event), 0.001))
            if type(event) in self.fail_on:
                self.fail_on.discard(type(event))
                raise MicrophoneUnavailable("connection lost")
            self.commands.append(event)
            if type(event) in self.silent:
                return
            if isinstance(event, StartListening):
                if self.fail_starts:
                    self.fail_starts -= 1
                    raise MicrophoneUnavailable("connection refused")
                script = self.after_arm.get(self.arms, lambda _listen_id: [])
                self.push(ListeningStarted(event.listen_id), *script(event.listen_id))
                self.arms += 1
            elif isinstance(event, StopListening):
                self.push(ListeningStopped(event.listen_id, event.reason))
            elif isinstance(event, PlayCue):
                self.push(CueFinished(event.cue_id))
            elif isinstance(event, PlaybackEnd):
                self.push(DriverPlaybackFinished(event.playback_id))
        finally:
            self.in_flight = False

    async def close(self) -> None:
        self.closes += 1
        while not self.events.empty():
            self.events.get_nowait()

    def starts(self) -> list[StartListening]:
        return [c for c in self.commands if isinstance(c, StartListening)]

    def without_ids(self) -> list[object]:
        def strip(c: MicrophoneCommand) -> object:
            if isinstance(c, StartListening):
                return "start"
            if isinstance(c, StopListening):
                return ("stop", c.reason)
            if isinstance(c, PlayCue):
                return c.cue_type
            if isinstance(c, ResetWakeCandidate):
                return ("reset", c.utterance_id)
            if isinstance(c, PlaybackBegin):
                return ("begin", c.rate, c.width, c.channels, c.volume)
            if isinstance(c, PlaybackChunk):
                return ("chunk", len(c.data))
            if isinstance(c, PlaybackEnd):
                return "end"
            assert isinstance(c, SetVisualState)
            return c.state

        return [strip(c) for c in self.commands]


def segments(*utterances: str, chunks: int = 2, reason: str = "completed") -> Script:
    def script(listen_id: str) -> list[Item]:
        items: list[Item] = []
        for utterance_id in utterances:
            items.append(SpeechStarted(listen_id, utterance_id, 16000, 2, 1))
            items += [AudioChunk(listen_id, utterance_id, CHUNK) for _ in range(chunks)]
            items.append(SpeechEnded(listen_id, utterance_id, reason))
        return items

    return script


class Harness:
    def __init__(self, reply_timeout: float = 0.05) -> None:
        self.driver = FakeDriver()
        self.stt = FakeStt()
        self.tts = FakeTts()
        self.events: list[HubEvent] = []
        self.sleeps: list[float] = []
        self.session = SatelliteSession(
            config(reply_timeout),
            self.driver,
            self.stt,
            self.events.append,
            self.sleep,
            tts=self.tts,
        )
        self.task: asyncio.Task[None] | None = None

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)

    def start(self) -> None:
        self.task = asyncio.create_task(self.session.run())

    async def until(self, condition: Callable[[], bool]) -> None:
        assert self.task is not None
        async with asyncio.timeout(TIMEOUT):
            while not condition():
                if self.task.done():
                    self.task.result()  # re-raise
                    raise AssertionError("session ended")
                await asyncio.sleep(0.001)

    async def stop(self) -> None:
        assert self.task is not None
        self.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.task
        assert not self.driver.overlapped, "commands to one satellite overlapped"

    def hub(self, *types: type) -> list[HubEvent]:
        return [e for e in self.events if not types or isinstance(e, types)]


def status(connected: bool, state: SatelliteState) -> SatelliteStatus:
    return SatelliteStatus("kitchen", "kitchen", connected, state)


OFFLINE = status(False, SatelliteState.ERROR)
IDLE = status(True, SatelliteState.IDLE)
LISTENING = status(True, SatelliteState.LISTENING)
PROCESSING = status(True, SatelliteState.PROCESSING)


# Accept, reply timeout, re-arm


def test_partial_wake_then_accept_then_reply_timeout_rearms() -> None:
    asyncio.run(_test_partial_wake_then_accept_then_reply_timeout_rearms())


async def _test_partial_wake_then_accept_then_reply_timeout_rearms() -> None:
    h = Harness()
    h.stt.say("u1", "Ryszardzie, która godzina?", ("Ryszardzie", "Ryszardzie, która"))
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.events == [
        OFFLINE,
        IDLE,
        Wake("kitchen", "u1"),
        LISTENING,
        TranscriptPartial("kitchen", "u1", "która"),
        PROCESSING,
        TranscriptFinal(
            "kitchen",
            "u1",
            text="która godzina",
            raw_text="Ryszardzie, która godzina?",
            follow_up=False,
            speaker=None,
            audio_ms=len(CHUNK) * 2 // 32,
        ),
        IDLE,
    ]
    assert h.driver.without_ids() == [
        VisualState.IDLE,
        "start",
        VisualState.LISTENING,
        ("stop", "accepted"),
        CueType.UTTERANCE_ACCEPTED,
        VisualState.PROCESSING,
        CueType.TIMEOUT,
        VisualState.IDLE,
        "start",
    ]
    first, second = h.driver.starts()
    stop = next(c for c in h.driver.commands if isinstance(c, StopListening))
    assert stop.listen_id == first.listen_id != second.listen_id
    max_audio, stt = h.stt.sessions["u1"]
    assert max_audio == pytest.approx(15.1)
    assert bytes(stt.audio) == CHUNK * 2 and stt.closed


def test_accept_on_the_final_alone_still_sends_wake_first() -> None:
    asyncio.run(_test_accept_on_the_final_alone_still_sends_wake_first())


async def _test_accept_on_the_final_alone_still_sends_wake_first() -> None:
    h = Harness(reply_timeout=10)
    h.stt.say("u1", "Ryszardzie, światło.", ("światło",))
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(TranscriptFinal)))
    await h.stop()
    assert h.hub(Wake, TranscriptFinal, TranscriptPartial) == [
        Wake("kitchen", "u1"),
        TranscriptFinal("kitchen", "u1", "światło", "Ryszardzie, światło.", False, None, 40),
    ]
    assert VisualState.LISTENING not in h.driver.without_ids()  # no candidate was shown


# Cancel, no wake, ordering


def test_partial_candidate_rejected_by_the_final_is_cancelled() -> None:
    asyncio.run(_test_partial_candidate_rejected_by_the_final_is_cancelled())


async def _test_partial_candidate_rejected_by_the_final_is_cancelled() -> None:
    h = Harness()
    h.stt.say("u1", "Ryszard idzie do domu.", ("Ryszardzie",))
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(WakeCancelled)))
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.events == [
        OFFLINE,
        IDLE,
        Wake("kitchen", "u1"),
        LISTENING,
        WakeCancelled("kitchen", "u1"),
        IDLE,
    ]
    assert h.driver.without_ids() == [
        VisualState.IDLE,
        "start",
        VisualState.LISTENING,
        ("reset", "u1"),
        VisualState.IDLE,
    ]


# The wake word alone, a pause, then the request (T-017 fix, hardware test 2026-10-20)


@pytest.mark.parametrize(
    ("first", "partials", "second", "text"),
    [
        ("Ryszardzie.", ("Ryszardzie",), "Która godzina?", "Która godzina"),
        ("Ryszardzie,", (), "która godzina", "która godzina"),  # only the final matched
        ("Ryszardzie...", ("Ryszardzie",), "Ryszardzie, która godzina?", "która godzina"),
    ],
)
def test_the_wake_word_alone_then_the_request_is_one_utterance(
    first: str, partials: tuple[str, ...], second: str, text: str
) -> None:
    asyncio.run(_test_wake_word_alone_then_request(first, partials, second, text))


async def _test_wake_word_alone_then_request(
    first: str, partials: tuple[str, ...], second: str, text: str
) -> None:
    h = Harness()
    h.stt.say("u1", first, partials)
    h.stt.say("u2", second, (second.split()[0],))
    h.driver.after_arm[0] = segments("u1", "u2")
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    final = TranscriptFinal("kitchen", "u1", text, f"{first} {second}", False, None, 80)
    assert h.hub(Wake, WakeCancelled, TranscriptFinal) == [Wake("kitchen", "u1"), final]
    # The LEDs stay listening from the wake word to the accept; no reset, no second wake.
    assert h.driver.without_ids() == [
        VisualState.IDLE,
        "start",
        VisualState.LISTENING,
        ("stop", "accepted"),
        CueType.UTTERANCE_ACCEPTED,
        VisualState.PROCESSING,
        CueType.TIMEOUT,  # the reply timeout
        VisualState.IDLE,
        "start",
    ]
    assert h.events.count(LISTENING) == 1
    assert all(e.utterance_id == "u1" for e in h.events if isinstance(e, TranscriptPartial))


def test_the_wake_word_alone_and_then_silence_cancels_with_the_timeout_cue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "WAKE_PAUSE_SECONDS", 0.05)
    asyncio.run(_test_wake_word_alone_then_silence())


async def _test_wake_word_alone_then_silence() -> None:
    h = Harness(reply_timeout=10)
    h.stt.say("u1", "Ryszardzie.", ("Ryszardzie",))
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.hub(Wake, WakeCancelled, TranscriptFinal, TranscriptPartial) == [
        Wake("kitchen", "u1"),
        WakeCancelled("kitchen", "u1"),
    ]
    assert h.driver.without_ids() == [
        VisualState.IDLE,
        "start",
        VisualState.LISTENING,
        ("reset", "u1"),
        ("stop", "no_request"),
        CueType.TIMEOUT,
        VisualState.IDLE,
        "start",
    ]
    first, second = h.driver.starts()
    reset = next(c for c in h.driver.commands if isinstance(c, ResetWakeCandidate))
    assert reset.listen_id == first.listen_id != second.listen_id


@pytest.mark.parametrize("second", ["", "♪", "..."])
def test_the_wake_word_alone_then_no_speech_cancels_with_the_timeout_cue(second: str) -> None:
    asyncio.run(_test_wake_word_alone_then_no_speech(second))


async def _test_wake_word_alone_then_no_speech(second: str) -> None:
    h = Harness(reply_timeout=10)
    h.stt.say("u1", "Ryszardzie.", ("Ryszardzie",))
    h.stt.say("u2", second)
    h.driver.after_arm[0] = segments("u1", "u2")
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.hub(Wake, WakeCancelled, TranscriptFinal, TranscriptPartial) == [
        Wake("kitchen", "u1"),
        WakeCancelled("kitchen", "u1"),
    ]
    assert h.driver.without_ids()[2:6] == [
        VisualState.LISTENING,
        ("reset", "u1"),
        ("stop", "no_request"),
        CueType.TIMEOUT,
    ]
    assert all(session.closed for _, session in h.stt.sessions.values())


def test_a_disconnect_while_waiting_for_the_request_cancels_the_wake_once() -> None:
    asyncio.run(_test_disconnect_while_waiting_for_the_request())


async def _test_disconnect_while_waiting_for_the_request() -> None:
    h = Harness(reply_timeout=10)
    h.stt.say("u1", "Ryszardzie.", ("Ryszardzie",))
    h.stt.say("u3", "która godzina")  # after the re-arm: no wake word, so nothing
    h.driver.after_arm[0] = lambda listen_id: [
        *segments("u1")(listen_id),
        MicrophoneUnavailable("connection lost"),
    ]
    h.driver.after_arm[1] = segments("u3")
    h.start()
    await h.until(lambda: "u3" in h.stt.sessions and h.stt.sessions["u3"][1].closed)
    await h.stop()
    assert h.hub(Wake, WakeCancelled, TranscriptFinal, TranscriptPartial) == [
        Wake("kitchen", "u1"),
        WakeCancelled("kitchen", "u1"),
    ]
    assert OFFLINE in h.events[3:] and CueType.TIMEOUT not in h.driver.without_ids()
    assert h.driver.closes == 1


def test_no_wake_emits_nothing_and_segments_are_strictly_ordered() -> None:
    asyncio.run(_test_no_wake_emits_nothing_and_segments_are_strictly_ordered())


async def _test_no_wake_emits_nothing_and_segments_are_strictly_ordered() -> None:
    h = Harness(reply_timeout=10)
    # All three segments are queued at once; u1's final is slow, u2 has a fast candidate.
    h.stt.say("u1", "dzień dobry", ("dzień",), delay=0.05)
    h.stt.say("u2", "Ryszard nie", ("Ryszardzie",), delay=0.05)
    h.stt.say("u3", "Ryszardzie, światło", ("Ryszardzie",))
    h.driver.after_arm[0] = segments("u1", "u2", "u3", reason="max_length")
    h.start()
    await h.until(lambda: bool(h.hub(TranscriptFinal)))
    await h.stop()
    assert h.hub(Wake, WakeCancelled, TranscriptFinal, TranscriptPartial) == [
        Wake("kitchen", "u2"),
        WakeCancelled("kitchen", "u2"),
        Wake("kitchen", "u3"),
        TranscriptFinal("kitchen", "u3", "światło", "Ryszardzie, światło", False, None, 40),
    ]
    assert all(session.closed for _, session in h.stt.sessions.values())


# Unavailable and recovery


def test_unavailable_backs_off_doubling_and_resets_after_an_arm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_test_unavailable_backs_off(caplog))


async def _test_unavailable_backs_off(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    h = Harness()
    h.driver.fail_starts = 3
    h.driver.after_arm[0] = lambda _listen_id: [MicrophoneUnavailable("connection lost")]
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 5)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.sleeps == [1.0, 2.0, 4.0, 1.0]
    assert h.driver.closes == 4
    assert h.events == [OFFLINE, IDLE, OFFLINE, IDLE]
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert lines == [
        "unavailable: connection refused; re-arming in 1 s",
        "unavailable: connection refused; re-arming in 2 s",
        "unavailable: connection refused; re-arming in 4 s",
        "unavailable: connection lost; re-arming in 1 s",
    ]
    listen_ids = [c.listen_id for c in h.driver.starts()]
    assert len(set(listen_ids)) == 5 and all(i.startswith("listen-") for i in listen_ids)


def test_backoff_is_capped_at_30_seconds() -> None:
    asyncio.run(_test_backoff_is_capped_at_30_seconds())


async def _test_backoff_is_capped_at_30_seconds() -> None:
    h = Harness()
    h.driver.fail_starts = 8
    h.start()
    await h.until(lambda: len(h.sleeps) == 8)
    await h.stop()
    assert h.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_disconnect_while_waiting_for_a_reply_recovers_at_once() -> None:
    asyncio.run(_test_disconnect_while_waiting_for_a_reply_recovers_at_once())


async def _test_disconnect_while_waiting_for_a_reply_recovers_at_once() -> None:
    h = Harness(reply_timeout=60)
    h.stt.say("u1", "Ryszardzie, światło")
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(TranscriptFinal)))
    h.driver.push(MicrophoneUnavailable("ESPHome transport stopped"))
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.sleeps == [1.0]
    assert CueType.TIMEOUT not in h.driver.without_ids()


def test_failure_mid_segment_closes_the_stt_session() -> None:
    asyncio.run(_test_failure_mid_segment_closes_the_stt_session())


async def _test_failure_mid_segment_closes_the_stt_session() -> None:
    h = Harness()
    h.stt.say("u1", "unused")
    h.driver.after_arm[0] = lambda listen_id: [
        SpeechStarted(listen_id, "u1", 16000, 2, 1),
        AudioChunk(listen_id, "u1", CHUNK),
        MicrophoneUnavailable("device ended its voice-assistant stream"),
    ]
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.stop()
    _, stt = h.stt.sessions["u1"]
    assert stt.closed


@pytest.mark.parametrize("silent", [StartListening, StopListening, PlayCue])
def test_a_missing_driver_answer_times_out_into_recovery(
    monkeypatch: pytest.MonkeyPatch, silent: type
) -> None:
    monkeypatch.setattr(session_module, "EVENT_TIMEOUT_SECONDS", 0.02)
    asyncio.run(_test_missing_answer(silent))


async def _test_missing_answer(silent: type) -> None:
    h = Harness()
    h.stt.say("u1", "Ryszardzie, światło")
    h.driver.after_arm[0] = segments("u1")
    h.driver.silent.add(silent)
    h.start()
    await h.until(lambda: h.driver.closes == 1)
    h.driver.silent.clear()
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.sleeps[0] == 1.0


# Invariants


def test_an_out_of_order_event_fails_loudly() -> None:
    asyncio.run(_test_an_out_of_order_event_fails_loudly())


async def _test_an_out_of_order_event_fails_loudly() -> None:
    h = Harness()
    h.driver.after_arm[0] = lambda listen_id: [AudioChunk(listen_id, "u1", CHUNK)]
    h.start()
    assert h.task is not None
    with pytest.raises(AssertionError, match="AudioChunk invalid in listening"):
        await asyncio.wait_for(h.task, TIMEOUT)


def test_an_event_while_waiting_for_a_reply_fails_loudly() -> None:
    asyncio.run(_test_an_event_while_waiting_for_a_reply_fails_loudly())


async def _test_an_event_while_waiting_for_a_reply_fails_loudly() -> None:
    h = Harness(reply_timeout=60)
    h.stt.say("u1", "Ryszardzie, światło")
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(TranscriptFinal)))
    h.driver.push(CueFinished("cue-x"))
    assert h.task is not None
    with pytest.raises(AssertionError):
        await asyncio.wait_for(h.task, TIMEOUT)


def test_cue_ids_come_from_the_process_wide_factory() -> None:
    asyncio.run(_test_cue_ids())


async def _test_cue_ids() -> None:
    h = Harness()
    h.stt.say("u1", "Ryszardzie, światło")
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.stop()
    cue_ids = [c.cue_id for c in h.driver.commands if isinstance(c, PlayCue)]
    assert len(cue_ids) == len(set(cue_ids)) == 2
    assert all(i.startswith("cue-") for i in cue_ids)


def test_cancel_during_recovery_exits_at_once() -> None:
    asyncio.run(_test_cancel_during_recovery_exits_at_once())


async def _test_cancel_during_recovery_exits_at_once() -> None:
    """A cancellation while recovery waits for the partial reader to unwind is not swallowed
    (it would otherwise re-arm and keep running)."""
    h = Harness()
    h.stt.say("u1", "unused")
    h.stt.linger = 0.2
    h.driver.after_arm[0] = lambda listen_id: [
        SpeechStarted(listen_id, "u1", 16000, 2, 1),
        MicrophoneUnavailable("connection lost"),
    ]
    h.start()
    await h.until(lambda: "u1" in h.stt.sessions)
    _, stt = h.stt.sessions["u1"]
    await asyncio.wait_for(stt.cancel_seen.wait(), TIMEOUT)
    assert h.task is not None
    h.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(h.task, TIMEOUT)
    assert len(h.driver.starts()) == 1 and h.sleeps == []
    assert not h.driver.overlapped


def test_partial_reader_unavailable_is_logged_when_abandoned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_test_partial_reader_unavailable_is_logged(caplog))


async def _test_partial_reader_unavailable_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness()
    h.stt.say("u1", "unused", ("Ryszardzie",))
    original = h.driver.send_output_event

    async def failing_listening_visual(event: MicrophoneCommand) -> None:
        await original(event)
        if event == SetVisualState(VisualState.LISTENING):
            raise MicrophoneUnavailable("visual failed")

    h.driver.send_output_event = failing_listening_visual  # type: ignore[method-assign]
    h.driver.after_arm[0] = lambda listen_id: [
        SpeechStarted(listen_id, "u1", 16000, 2, 1),
        *[AudioChunk(listen_id, "u1", CHUNK) for _ in range(5)],
    ]
    h.start()
    await h.until(lambda: SetVisualState(VisualState.LISTENING) in h.driver.commands)
    await asyncio.sleep(0.01)
    h.driver.push(MicrophoneUnavailable("connection lost"))
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.stop()
    assert "partial reader stopped: visual failed" in caplog.text


def test_partial_reader_bug_propagates_when_abandoned() -> None:
    asyncio.run(_test_partial_reader_bug_propagates_when_abandoned())


async def _test_partial_reader_bug_propagates_when_abandoned() -> None:
    h = Harness()
    h.stt.say("u1", "unused", ("Ryszardzie",))
    original = h.driver.send_output_event

    async def buggy_listening_visual(event: MicrophoneCommand) -> None:
        await original(event)
        if event == SetVisualState(VisualState.LISTENING):
            raise RuntimeError("bug")

    h.driver.send_output_event = buggy_listening_visual  # type: ignore[method-assign]
    h.driver.after_arm[0] = lambda listen_id: [SpeechStarted(listen_id, "u1", 16000, 2, 1)]
    h.start()
    await h.until(lambda: SetVisualState(VisualState.LISTENING) in h.driver.commands)
    await asyncio.sleep(0.01)
    h.driver.push(MicrophoneUnavailable("connection lost"))
    assert h.task is not None
    with pytest.raises(RuntimeError, match="bug"):
        await asyncio.wait_for(h.task, TIMEOUT)


# B1 (PR #12 review): the session owns a segment until it is finished or abandoned


async def _assert_quiet_after_cancel(h: Harness) -> None:
    """Cancel run(); afterwards nothing is sent or emitted and no task is left."""
    assert h.task is not None
    h.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(h.task, TIMEOUT)
    commands, events = len(h.driver.commands), len(h.events)
    await asyncio.sleep(0.3)  # past the late partial
    assert h.driver.commands[commands:] == []
    assert h.events[events:] == []
    assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []
    assert not h.driver.overlapped


def test_cancel_during_a_final_leaves_no_partial_task() -> None:
    asyncio.run(_test_cancel_during_a_final_leaves_no_partial_task())


async def _test_cancel_during_a_final_leaves_no_partial_task() -> None:
    h = Harness()
    h.stt.say("u1", "Ryszardzie, światło", delay=1.0)
    h.stt.setup = lambda session: session.late.append((0.1, "Ryszardzie"))
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: "u1" in h.stt.sessions and h.stt.sessions["u1"][1].final_started)
    await _assert_quiet_after_cancel(h)
    assert not h.hub(Wake)  # the late partial never got through
    assert h.stt.sessions["u1"][1].closed


def test_cancel_during_an_abandon_leaves_no_partial_task() -> None:
    asyncio.run(_test_cancel_during_an_abandon_leaves_no_partial_task())


async def _test_cancel_during_an_abandon_leaves_no_partial_task() -> None:
    h = Harness()
    h.stt.say("u1", "unused")

    def slow_close(session: FakeSttSession) -> None:
        session.late.append((0.1, "Ryszardzie"))
        session.close_delay = 1.0

    h.stt.setup = slow_close
    h.driver.after_arm[0] = lambda listen_id: [
        SpeechStarted(listen_id, "u1", 16000, 2, 1),
        MicrophoneUnavailable("connection lost"),
    ]
    h.start()
    await h.until(lambda: "u1" in h.stt.sessions and h.stt.sessions["u1"][1].close_started)
    await _assert_quiet_after_cancel(h)
    assert not h.hub(Wake)


# Every wake ends with transcript_final or wake_cancelled


def test_disconnect_after_wake_before_the_final_cancels_the_wake() -> None:
    asyncio.run(_test_disconnect_after_wake_before_the_final_cancels_the_wake())


async def _test_disconnect_after_wake_before_the_final_cancels_the_wake() -> None:
    h = Harness()
    h.stt.say("u1", "unused", ("Ryszardzie",))
    h.driver.after_arm[0] = lambda listen_id: [
        SpeechStarted(listen_id, "u1", 16000, 2, 1),
        *[AudioChunk(listen_id, "u1", CHUNK) for _ in range(5)],
    ]
    h.start()
    await h.until(lambda: h.events[-1:] == [LISTENING])
    h.driver.push(MicrophoneUnavailable("connection lost"))
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.events == [
        OFFLINE,
        IDLE,
        Wake("kitchen", "u1"),
        LISTENING,
        WakeCancelled("kitchen", "u1"),
        OFFLINE,
        IDLE,
    ]


def test_failure_between_wake_and_transcript_final_cancels_the_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "EVENT_TIMEOUT_SECONDS", 0.02)
    asyncio.run(_test_failure_between_wake_and_transcript_final())


async def _test_failure_between_wake_and_transcript_final() -> None:
    h = Harness()
    h.stt.say("u1", "Ryszardzie, światło")
    h.driver.after_arm[0] = segments("u1")
    h.driver.silent.add(PlayCue)  # the accepted cue never finishes
    h.start()
    await h.until(lambda: h.driver.closes == 1)
    h.driver.silent.clear()
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.hub(Wake, WakeCancelled, TranscriptFinal) == [
        Wake("kitchen", "u1"),
        WakeCancelled("kitchen", "u1"),
    ]


# Clean stop on exit


def test_shutdown_stops_listening_goes_idle_and_closes() -> None:
    asyncio.run(_test_shutdown_stops_listening_goes_idle_and_closes())


async def _test_shutdown_stops_listening_goes_idle_and_closes() -> None:
    h = Harness()
    h.start()
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    events = len(h.events)
    await h.session.shutdown()
    listen_id = h.driver.starts()[-1].listen_id
    assert h.driver.commands[-2:] == [
        StopListening(listen_id, "shutdown"),
        SetVisualState(VisualState.IDLE),
    ]
    assert h.driver.closes == 1
    assert h.events[events:] == []


def test_shutdown_while_processing_goes_idle_without_a_stop() -> None:
    asyncio.run(_test_shutdown_while_processing_goes_idle_without_a_stop())


async def _test_shutdown_while_processing_goes_idle_without_a_stop() -> None:
    h = Harness(reply_timeout=60)
    h.stt.say("u1", "Ryszardzie, światło")
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(TranscriptFinal)))
    await h.stop()
    stops = len([c for c in h.driver.commands if isinstance(c, StopListening)])
    await h.session.shutdown()
    assert len([c for c in h.driver.commands if isinstance(c, StopListening)]) == stops
    assert h.driver.commands[-1] == SetVisualState(VisualState.IDLE)
    assert h.driver.closes == 1


def test_shutdown_offline_only_closes() -> None:
    asyncio.run(_test_shutdown_offline_only_closes())


async def _test_shutdown_offline_only_closes() -> None:
    h = Harness()
    h.driver.fail_starts = 1000
    h.start()
    await h.until(lambda: len(h.sleeps) >= 2)
    await h.stop()
    commands, closes = len(h.driver.commands), h.driver.closes
    await h.session.shutdown()
    assert len(h.driver.commands) == commands
    assert h.driver.closes == closes + 1


def test_shutdown_failure_is_logged_and_still_closes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(session_module, "SHUTDOWN_TIMEOUT_SECONDS", 0.02)
    asyncio.run(_test_shutdown_failure_is_logged_and_still_closes())
    assert "shutdown: could not stop listening and go idle: TimeoutError()" in caplog.text


async def _test_shutdown_failure_is_logged_and_still_closes() -> None:
    h = Harness()
    h.start()
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    original = h.driver.send_output_event

    async def hang(event: MicrophoneCommand) -> None:
        await asyncio.sleep(10)
        await original(event)

    h.driver.send_output_event = hang  # type: ignore[method-assign]
    await h.session.shutdown()
    assert h.driver.closes == 1


def test_shutdown_while_running_is_refused() -> None:
    asyncio.run(_test_shutdown_while_running_is_refused())


async def _test_shutdown_while_running_is_refused() -> None:
    h = Harness()
    h.start()
    await h.until(lambda: h.events[-1:] == [IDLE])
    commands = len(h.driver.commands)
    with pytest.raises(AssertionError, match="cancel run"):
        await h.session.shutdown()
    assert len(h.driver.commands) == commands
    assert h.driver.closes == 0
    await h.stop()
    await h.session.shutdown()  # allowed once run() has stopped
    assert h.driver.closes == 1


def test_shutdown_swallows_an_assertion_and_still_closes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_test_shutdown_swallows_an_assertion_and_still_closes())
    assert "shutdown: could not stop listening and go idle: AssertionError" in caplog.text


async def _test_shutdown_swallows_an_assertion_and_still_closes() -> None:
    h = Harness()
    h.start()
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()

    async def refuse(event: MicrophoneCommand) -> None:
        raise AssertionError(f"invalid in this state: {event}")

    h.driver.send_output_event = refuse  # type: ignore[method-assign]
    await h.session.shutdown()
    assert h.driver.closes == 1


# Speak and release (T-017)

SPEAKING = status(True, SatelliteState.SPEAKING)
CHUNK_BYTES = 2205 * 2
BEGIN = ("begin", 22050, 2, 1, 0.7)


async def accepted(h: Harness) -> None:
    """Start the session and wait until it waits for a reply."""
    h.stt.say("u1", "Ryszardzie, która godzina?")
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(TranscriptFinal)))
    await asyncio.sleep(0.01)  # into the reply wait


def after_processing(h: Harness) -> list[object]:
    commands = h.driver.without_ids()
    return commands[commands.index(VisualState.PROCESSING) + 1 :]


def busy(call: Callable[[], object]) -> ErrorCode:
    with pytest.raises(CommandError) as failure:
        call()
    return failure.value.code


def test_speak_plays_the_reply_then_rearms() -> None:
    asyncio.run(_test_speak_plays_the_reply_then_rearms())


async def _test_speak_plays_the_reply_then_rearms() -> None:
    h = Harness(reply_timeout=10)
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    assert playback_id.startswith("playback-")
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.tts.texts == ["Jest piąta."] and h.tts.open == 0
    assert after_processing(h) == [BEGIN] + [("chunk", CHUNK_BYTES)] * 3 + [
        "end",
        VisualState.IDLE,
        "start",
    ]
    begin = next(c for c in h.driver.commands if isinstance(c, PlaybackBegin))
    assert begin.playback_id == playback_id
    assert h.events[h.events.index(PROCESSING) + 2 :] == [
        SPEAKING,
        PlaybackFinished("kitchen", playback_id, False),
        IDLE,
    ]


def test_release_rearms_without_a_cue() -> None:
    asyncio.run(_test_release_rearms_without_a_cue())


async def _test_release_rearms_without_a_cue() -> None:
    h = Harness(reply_timeout=10)
    await accepted(h)
    h.session.release()
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.stop()
    assert after_processing(h) == [VisualState.IDLE, "start"]
    assert not h.hub(PlaybackFinished) and h.tts.texts == []


def test_commands_outside_the_reply_wait_are_busy_or_offline() -> None:
    asyncio.run(_test_commands_outside_the_reply_wait_are_busy_or_offline())


async def _test_commands_outside_the_reply_wait_are_busy_or_offline() -> None:
    h = Harness(reply_timeout=10)
    assert busy(lambda: h.session.speak("x", 1.0)) == ErrorCode.SATELLITE_OFFLINE  # not started
    h.tts.chunk_delay = 0.05
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    with pytest.raises(CommandError, match="kitchen already has a reply") as taken:
        h.session.speak("Drugi raz.", 0.7)
    assert taken.value.code == ErrorCode.BUSY
    await h.until(lambda: h.events[-1:] == [SPEAKING])
    assert busy(h.session.release) == ErrorCode.BUSY  # speaking
    await h.until(lambda: h.events[-1:] == [IDLE])
    assert busy(lambda: h.session.speak("x", 1.0)) == ErrorCode.BUSY  # idle: T-019
    h.driver.fail_starts = 10**9  # stays offline
    h.driver.push(MicrophoneUnavailable("connection lost"))
    await h.until(lambda: h.events[-1:] == [OFFLINE])
    assert busy(lambda: h.session.speak("x", 1.0)) == ErrorCode.SATELLITE_OFFLINE
    await h.stop()
    assert h.hub(PlaybackFinished) == [PlaybackFinished("kitchen", playback_id, False)]


@pytest.mark.parametrize("when", ["before-audio-start", "after-audio-start", "cap-first"])
def test_tts_failing_before_any_audio_plays_the_timeout_cue_and_still_finishes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, when: str
) -> None:
    if when == "cap-first":  # PR #22 review N1: the cap fires before the first chunk
        monkeypatch.setattr(session_module, "PLAYBACK_PRODUCE_TIMEOUT_SECONDS", 0.1)
    asyncio.run(_test_tts_failing_before_any_audio(caplog, when))


async def _test_tts_failing_before_any_audio(caplog: pytest.LogCaptureFixture, when: str) -> None:
    h = Harness(reply_timeout=10)
    if when == "before-audio-start":
        h.tts.fail_at_start = True
        reason = "could not connect to the TTS service: refused"
    elif when == "after-audio-start":  # then a failure before the first chunk
        h.tts.chunks = []
        h.tts.fail_after_chunks = True
        reason = "connection closed before audio-stop"
    else:  # audio-start, then no chunk within the cap
        h.tts.chunk_delay = 1.0
        reason = ""
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    # No playback at all, then the timeout cue, so the person does not hear only silence.
    assert after_processing(h) == [CueType.TIMEOUT, VisualState.IDLE, "start"]
    assert h.events[h.events.index(PROCESSING) + 2 :] == [
        SPEAKING,
        PlaybackFinished("kitchen", playback_id, False),
        IDLE,
    ]
    assert h.tts.open == 0
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    failure = f"TTS failed: {reason}" if reason else "audio not complete within 0.1 s"
    assert errors == [
        f"speak playback_id={playback_id}: {failure}; no audio: playing the timeout cue"
    ]


@pytest.mark.parametrize("begin_seconds", [0.15, 0.3])
def test_a_slow_playback_begin_is_not_cut_by_the_tts_cap(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, begin_seconds: float
) -> None:
    monkeypatch.setattr(session_module, "PLAYBACK_PRODUCE_TIMEOUT_SECONDS", 0.2)
    asyncio.run(_test_slow_playback_begin(caplog, begin_seconds))


async def _test_slow_playback_begin(caplog: pytest.LogCaptureFixture, seconds: float) -> None:
    """PR #22 review S1: the cap covers only the TTS reads, against one deadline. A slow
    PlaybackBegin (e.g. a reconnect) is never cancelled. Within the deadline everything plays;
    past it the begin still completes, the next read hits the deadline and what was produced
    is played. Before, the cap cancelled the begin and the timeout cue tripped the protocol."""
    h = Harness(reply_timeout=10)
    h.driver.slow[PlaybackBegin] = seconds
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    chunks = 3 if seconds < 0.2 else 1
    assert after_processing(h) == [BEGIN, *[("chunk", CHUNK_BYTES)] * chunks] + [
        "end",
        VisualState.IDLE,
        "start",
    ]
    assert h.hub(PlaybackFinished) == [PlaybackFinished("kitchen", playback_id, False)]
    assert h.tts.open == 0
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    capped = f"speak playback_id={playback_id}: audio not complete within 0.2 s"
    assert errors == ([] if chunks == 3 else [f"{capped}; playing what was produced"])


def test_a_speak_without_a_volume_leaves_the_satellite_volume_alone() -> None:
    asyncio.run(_test_a_speak_without_a_volume())


async def _test_a_speak_without_a_volume() -> None:
    h = Harness(reply_timeout=10)
    await accepted(h)
    h.session.speak("Jest piąta.", None)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert after_processing(h)[0] == ("begin", 22050, 2, 1, None)


def test_shutdown_while_waiting_for_the_playback_to_finish() -> None:
    asyncio.run(_test_shutdown_while_waiting_for_the_playback_to_finish())


async def _test_shutdown_while_waiting_for_the_playback_to_finish() -> None:
    h = Harness(reply_timeout=10)
    h.driver.silent.add(PlaybackEnd)  # the device is still playing
    await accepted(h)
    h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: "end" in h.driver.without_ids())
    await h.stop()
    async with asyncio.timeout(session_module.SHUTDOWN_TIMEOUT_SECONDS + 1.0):
        await h.session.shutdown()
    assert h.driver.closes == 1
    assert not h.hub(PlaybackFinished) and h.tts.open == 0


def test_tts_failing_mid_stream_plays_what_was_produced(caplog: pytest.LogCaptureFixture) -> None:
    asyncio.run(_test_tts_failing_mid_stream(caplog))


async def _test_tts_failing_mid_stream(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness(reply_timeout=10)
    h.tts.chunks = h.tts.chunks[:2]
    h.tts.fail_after_chunks = True
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert after_processing(h) == [BEGIN, ("chunk", CHUNK_BYTES), ("chunk", CHUNK_BYTES), "end"] + [
        VisualState.IDLE,
        "start",
    ]
    assert h.hub(PlaybackFinished) == [PlaybackFinished("kitchen", playback_id, False)]
    assert h.tts.open == 0
    assert any("playing what was produced" in r.getMessage() for r in caplog.records)


def test_audio_past_the_produce_bound_is_cut_and_played(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(session_module, "PLAYBACK_PRODUCE_TIMEOUT_SECONDS", 0.1)
    asyncio.run(_test_produce_bound(caplog))


async def _test_produce_bound(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness(reply_timeout=10)
    h.tts.chunks = h.tts.chunks * 10
    h.tts.chunk_delay = 0.04
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    played = after_processing(h)
    assert played[0] == BEGIN and played[-3:] == ["end", VisualState.IDLE, "start"]
    assert 1 <= played.count(("chunk", CHUNK_BYTES)) <= 3
    assert h.hub(PlaybackFinished) == [PlaybackFinished("kitchen", playback_id, False)]
    assert h.tts.open == 0
    assert any("audio not complete within 0.1 s" in r.getMessage() for r in caplog.records)


def test_a_playback_that_never_finishes_recovers_and_still_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "PLAYBACK_FINISHED_MARGIN_SECONDS", 0.05)
    asyncio.run(_test_playback_never_finishes())


async def _test_playback_never_finishes() -> None:
    h = Harness(reply_timeout=10)
    h.driver.silent.add(PlaybackEnd)
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    # 0.3 s of audio: bound 2 x 0.3 + 0.05 s.
    assert h.events[h.events.index(SPEAKING) :] == [
        SPEAKING,
        PlaybackFinished("kitchen", playback_id, False),
        OFFLINE,
        IDLE,
    ]
    assert h.driver.closes == 1 and h.sleeps == [1.0]


def test_a_disconnect_mid_speak_recovers_and_still_finishes() -> None:
    asyncio.run(_test_a_disconnect_mid_speak())


async def _test_a_disconnect_mid_speak() -> None:
    h = Harness(reply_timeout=10)
    h.driver.fail_on.add(PlaybackChunk)
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert h.events[h.events.index(SPEAKING) :] == [
        SPEAKING,
        PlaybackFinished("kitchen", playback_id, False),
        OFFLINE,
        IDLE,
    ]
    assert h.tts.open == 0


def test_a_disconnect_right_after_the_ack_still_finishes() -> None:
    asyncio.run(_test_a_disconnect_right_after_the_ack())


async def _test_a_disconnect_right_after_the_ack() -> None:
    h = Harness(reply_timeout=10)
    await accepted(h)
    playback_id = h.session.speak("Jest piąta.", 0.7)
    h.driver.push(MicrophoneUnavailable("connection lost"))  # in the same step as the ack
    await h.until(lambda: len(h.driver.starts()) == 2)
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    finished = h.events.index(PlaybackFinished("kitchen", playback_id, False))
    assert len(h.hub(PlaybackFinished)) == 1
    assert h.events[finished + 1 :] == [OFFLINE, IDLE]


def test_after_the_reply_timeout_a_speak_is_busy() -> None:
    asyncio.run(_test_after_the_reply_timeout_a_speak_is_busy())


async def _test_after_the_reply_timeout_a_speak_is_busy() -> None:
    h = Harness(reply_timeout=0.05)
    await accepted(h)
    await h.until(lambda: CueType.TIMEOUT in h.driver.without_ids())
    assert busy(lambda: h.session.speak("Za późno.", 0.7)) == ErrorCode.BUSY
    await h.until(lambda: h.events[-1:] == [IDLE])
    await h.stop()
    assert not h.hub(PlaybackFinished)


def test_shutdown_mid_speak_closes_the_synthesis_and_emits_nothing() -> None:
    asyncio.run(_test_shutdown_mid_speak())


async def _test_shutdown_mid_speak() -> None:
    h = Harness(reply_timeout=10)
    h.tts.chunk_delay = 1.0
    await accepted(h)
    h.session.speak("Jest piąta.", 0.7)
    await h.until(lambda: h.tts.open == 1)
    await h.stop()
    assert h.tts.open == 0 and not h.hub(PlaybackFinished)
