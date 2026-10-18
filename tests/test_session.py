"""T-010: the satellite session state machine, with a fake driver and a fake STT."""

import asyncio
import logging
from collections import deque
from collections.abc import Callable

import pytest

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.hub import session as session_module
from piotr_voice_hub.hub.events import (
    HubEvent,
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
    PlayCue,
    ResetWakeCandidate,
    SetVisualState,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
    VisualState,
)
from piotr_voice_hub.satellites.types import MicrophoneContext, PlaybackTarget
from piotr_voice_hub.stt.whisper import StreamingTextEvent, TextEnd, TextPartial

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

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def receive_text(self) -> StreamingTextEvent:
        if self.partials:
            await asyncio.sleep(0)
            return TextPartial(self.partials.popleft(), 0.0, 1.0)
        await self.ended.wait()
        return TextEnd()

    async def transcribe_final(self) -> str:
        while self.partials:  # like the real session: in-flight partials come first
            await asyncio.sleep(0)
        await asyncio.sleep(self.final_delay)
        self.ended.set()
        return self.final

    async def close(self) -> None:
        self.closed = True
        self.ended.set()


class FakeStt:
    def __init__(self) -> None:
        self.scripts: dict[str, tuple[list[str], str, float]] = {}
        self.sessions: dict[str, tuple[float, FakeSttSession]] = {}

    def say(
        self, utterance_id: str, final: str, partials: tuple[str, ...] = (), delay: float = 0.0
    ) -> None:
        self.scripts[utterance_id] = (list(partials), final, delay)

    def create_streaming_session(self, session_id: str, max_audio_seconds: float) -> FakeSttSession:
        partials, final, delay = self.scripts[session_id]
        session = FakeSttSession(partials, final, delay)
        self.sessions[session_id] = (max_audio_seconds, session)
        return session


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
        self.closes = 0
        self.in_flight = False
        self.overlapped = False

    def push(self, *items: Item) -> None:
        for item in items:
            self.events.put_nowait(item)

    async def wait_for_event(self) -> MicrophoneEvent:
        item = await self.events.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self.overlapped |= self.in_flight
        self.in_flight = True
        try:
            await asyncio.sleep(0.001)
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
        self.events: list[HubEvent] = []
        self.sleeps: list[float] = []
        self.session = SatelliteSession(
            config(reply_timeout), self.driver, self.stt, self.events.append, self.sleep
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
    await h.until(lambda: h.events[-1] == IDLE)
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
    await h.until(lambda: h.events[-1] == IDLE)
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


def test_wake_word_with_nothing_after_it_is_rejected() -> None:
    asyncio.run(_test_wake_word_with_nothing_after_it_is_rejected())


async def _test_wake_word_with_nothing_after_it_is_rejected() -> None:
    h = Harness()
    h.stt.say("u1", "Ryszardzie...", ("Ryszardzie",))
    h.driver.after_arm[0] = segments("u1")
    h.start()
    await h.until(lambda: bool(h.hub(WakeCancelled)))
    await h.until(lambda: h.driver.without_ids()[-1] == VisualState.IDLE)
    await h.stop()
    assert not h.hub(TranscriptFinal, TranscriptPartial)
    assert h.driver.without_ids()[-2:] == [("reset", "u1"), VisualState.IDLE]
    reset = next(c for c in h.driver.commands if isinstance(c, ResetWakeCandidate))
    assert reset.listen_id == h.driver.starts()[0].listen_id


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
    await h.until(lambda: h.events[-1] == IDLE)
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
    await h.until(lambda: h.events[-1] == IDLE)
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
    await h.until(lambda: h.events[-1] == IDLE)
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
