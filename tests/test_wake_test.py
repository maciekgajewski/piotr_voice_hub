"""T-013: the live wake-test runner, with a fake microphone and a fake STT."""

import asyncio
from collections import deque
from collections.abc import Callable
from pathlib import Path

import pytest

from piotr_voice_hub.config import load_config
from piotr_voice_hub.satellites.interfaces import MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    ListeningStarted,
    MicrophoneCommand,
    MicrophoneEvent,
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
from piotr_voice_hub.wake_test import (
    SatelliteRunner,
    main,
    run_until_cancelled,
    select_satellites,
)

EXAMPLE_CONFIG = Path(__file__).parents[1] / "config" / "hub.example.yaml"
MAX_AUDIO = 15.1


class ScriptDone(BaseException):  # not Exception: the runner recovers from those
    pass


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


class FakeSession:
    def __init__(self, clock: Clock, partials: list[tuple[float, str]], final: tuple[float, str]):
        self.clock = clock
        self.partials = deque(partials)
        self.final = final
        self.audio = bytearray()
        self.ended = asyncio.Event()
        self.closed = False
        self.linger = 0.0  # seconds the partial reader takes to unwind when cancelled
        self.cancel_seen = asyncio.Event()
        self.late: list[tuple[float, str]] = []  # (delay, text): partials that arrive later
        self.final_delay = 0.0
        self.final_started = False

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def receive_text(self) -> StreamingTextEvent:
        if self.partials:
            at, text = self.partials.popleft()
            await asyncio.sleep(0)
            self.clock.now = at
            return TextPartial(text, 0.0, at)
        if self.late:
            delay, text = self.late.pop(0)
            await asyncio.sleep(delay)
            return TextPartial(text, 0.0, delay)
        try:
            await self.ended.wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await asyncio.sleep(self.linger)
            raise
        return TextEnd()

    async def transcribe_final(self) -> str:
        self.final_started = True
        await asyncio.sleep(self.final_delay)
        self.ended.set()
        self.clock.now, text = self.final
        return text

    async def close(self) -> None:
        self.closed = True
        self.ended.set()


type SegmentScript = tuple[list[tuple[float, str]], tuple[float, str]]  # (partials, final)


class FakeStt:
    def __init__(self, clock: Clock, segments: list[SegmentScript], linger: float = 0.0) -> None:
        self.clock = clock
        self.segments = deque(segments)
        self.linger = linger
        self.setup: Callable[[FakeSession], None] = lambda _session: None
        self.sessions: list[tuple[str, float, FakeSession]] = []

    def create_streaming_session(self, session_id: str, max_audio_seconds: float) -> FakeSession:
        partials, final = self.segments.popleft()
        session = FakeSession(self.clock, partials, final)
        session.linger = self.linger
        self.setup(session)
        self.sessions.append((session_id, max_audio_seconds, session))
        return session


type Step = tuple[float, MicrophoneEvent | Exception] | str


class FakeMicrophone:
    """`script` items: (time, event or exception) for wait_for_event, or "listening" for the
    ListeningStarted of the latest StartListening. `fail_starts` StartListening calls raise."""

    def __init__(
        self, clock: Clock, script: list[Step], fail_starts: int = 0, block_at_end: bool = False
    ) -> None:
        self.context = MicrophoneContext("fake", "kitchen", "kitchen")
        self.playback_target = PlaybackTarget("fake", "kitchen", "10.0.0.5", "a2V5")
        self.clock = clock
        self.script = deque(script)
        self.fail_starts = fail_starts
        self.commands: list[MicrophoneCommand] = []
        self.closes = 0
        self.in_flight = False
        self.overlapped = False
        self.block_at_end = block_at_end
        self.fail_commands: dict[type, Exception] = {}
        self.fail_exact: dict[MicrophoneCommand, Exception] = {}

    @property
    def listen_id(self) -> str:
        return [c for c in self.commands if isinstance(c, StartListening)][-1].listen_id

    async def wait_for_event(self) -> MicrophoneEvent:
        await settle()
        if not self.script:
            if self.block_at_end:
                await asyncio.Event().wait()
            raise ScriptDone
        step = self.script.popleft()
        if step == "listening":
            return ListeningStarted(self.listen_id)
        assert not isinstance(step, str)
        at, item = step
        self.clock.now = at
        if isinstance(item, Exception):
            raise item
        return item

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self.overlapped |= self.in_flight  # recorded: the runner recovers from assertions
        self.in_flight = True
        try:
            await settle()
            self.commands.append(event)
            if type(event) in self.fail_commands:
                raise self.fail_commands[type(event)]
            if event in self.fail_exact:
                raise self.fail_exact[event]
            if isinstance(event, StartListening) and self.fail_starts:
                self.fail_starts -= 1
                raise MicrophoneUnavailable("connection refused")
        finally:
            self.in_flight = False

    async def close(self) -> None:
        self.closes += 1


def segment(utterance: str, start: float, end: float, reason: str = "completed") -> list[Step]:
    return [
        (start, SpeechStarted("L", utterance, 16000, 2, 1)),
        (start, AudioChunk("L", utterance, b"\x01\x00")),
        (end, AudioChunk("L", utterance, b"\x02\x00")),
        (end, SpeechEnded("L", utterance, reason)),
    ]


class Run:
    def __init__(
        self, script: list[Step], segments: list[SegmentScript], fail_starts: int = 0
    ) -> None:
        self.clock = Clock()
        self.microphone = FakeMicrophone(self.clock, script, fail_starts)
        self.stt = FakeStt(self.clock, segments)
        self.lines: list[str] = []
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def __call__(self) -> None:
        runner = SatelliteRunner(
            "kitchen",
            self.microphone,
            self.stt,
            MAX_AUDIO,
            out=lambda satellite, message: self.lines.append(f"[{satellite}] {message}"),
            clock=self.clock,
            sleep=self.sleep,
        )
        with pytest.raises(ScriptDone):
            await runner.run()
        assert not self.microphone.overlapped, "commands to one satellite overlapped"


def commands_without_ids(run: Run) -> list[object]:
    return [c if not isinstance(c, StartListening) else "start" for c in run.microphone.commands]


def test_wake_on_partial_then_accepted_final() -> None:
    asyncio.run(_test_wake_on_partial_then_accepted_final())


async def _test_wake_on_partial_then_accepted_final() -> None:
    run = Run(
        ["listening", *segment("u1", 1.0, 2.0)],
        [([(1.5, "Ryszardzie, która")], (2.4, "Ryszardzie, która godzina?"))],
    )
    await run()
    listen_id = run.microphone.listen_id
    assert run.lines == [
        f"[kitchen] listening (listen_id={listen_id})",
        "[kitchen] speech started (u1)",
        "[kitchen] wake partial +0.50s after speech start: 'Ryszardzie, która'",
        "[kitchen] final 'Ryszardzie, która godzina?': WAKE remainder='która godzina' | "
        "segment 1.00s (completed), end→final 0.40s, start→wake partial 0.50s",
    ]
    assert commands_without_ids(run) == [
        SetVisualState(VisualState.IDLE),
        "start",
        SetVisualState(VisualState.LISTENING),
        SetVisualState(VisualState.IDLE),
    ]
    [(session_id, max_audio, session)] = run.stt.sessions
    assert (session_id, max_audio, bytes(session.audio)) == ("u1", MAX_AUDIO, b"\x01\x00\x02\x00")
    assert session.closed


def test_partial_wake_rejected_by_final_resets_candidate() -> None:
    asyncio.run(_test_partial_wake_rejected_by_final_resets_candidate())


async def _test_partial_wake_rejected_by_final_resets_candidate() -> None:
    run = Run(
        ["listening", *segment("u1", 1.0, 2.0)],
        [([(1.3, "Ryszardzie")], (2.2, "Ryszard idzie do domu."))],
    )
    await run()
    assert run.lines[-1] == (
        "[kitchen] final 'Ryszard idzie do domu.': no wake (partial candidate rejected by the "
        "final) | segment 1.00s (completed), end→final 0.20s, start→wake partial 0.30s"
    )
    assert run.microphone.commands[-2:] == [
        ResetWakeCandidate(run.microphone.listen_id, "u1"),
        SetVisualState(VisualState.IDLE),
    ]


def test_no_wake_and_max_length_segment() -> None:
    asyncio.run(_test_no_wake_and_max_length_segment())


async def _test_no_wake_and_max_length_segment() -> None:
    run = Run(
        ["listening", *segment("u1", 1.0, 16.0, "max_length"), *segment("u2", 17.0, 18.0)],
        [
            ([(2.0, "dzień dobry")], (16.5, "dzień dobry wszystkim")),
            ([], (18.3, "Ryszardzie, światło.")),
        ],
    )
    await run()
    assert run.lines[2] == (
        "[kitchen] final 'dzień dobry wszystkim': no wake | segment 15.00s (max_length), "
        "end→final 0.50s, start→wake partial -"
    )
    assert run.lines[4] == (
        "[kitchen] final 'Ryszardzie, światło.': WAKE remainder='światło' | "
        "segment 1.00s (completed), end→final 0.30s, start→wake partial -"
    )
    assert commands_without_ids(run) == [SetVisualState(VisualState.IDLE), "start"]


def test_unavailable_backs_off_and_rearms_with_new_listen_ids() -> None:
    asyncio.run(_test_unavailable_backs_off_and_rearms_with_new_listen_ids())


async def _test_unavailable_backs_off_and_rearms_with_new_listen_ids() -> None:
    run = Run(
        ["listening", (5.0, MicrophoneUnavailable("connection lost")), "listening"],
        [],
        fail_starts=2,
    )
    await run()
    assert run.sleeps == [1.0, 2.0, 1.0]  # reset after the successful arm
    assert run.microphone.closes == 3
    starts = [c.listen_id for c in run.microphone.commands if isinstance(c, StartListening)]
    assert len(starts) == len(set(starts)) == 4
    assert [line for line in run.lines if "unavailable" in line] == [
        "[kitchen] unavailable: connection refused; retry in 1 s",
        "[kitchen] unavailable: connection refused; retry in 2 s",
        "[kitchen] unavailable: connection lost; retry in 1 s",
    ]


def test_backoff_is_capped() -> None:
    asyncio.run(_test_backoff_is_capped())


async def _test_backoff_is_capped() -> None:
    run = Run([], [], fail_starts=8)
    await run()
    assert run.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_failure_mid_segment_abandons_the_stt_session() -> None:
    asyncio.run(_test_failure_mid_segment_abandons_the_stt_session())


async def _test_failure_mid_segment_abandons_the_stt_session() -> None:
    run = Run(
        [
            "listening",
            (1.0, SpeechStarted("L", "u1", 16000, 2, 1)),
            (1.1, MicrophoneUnavailable("device ended its voice-assistant stream")),
            "listening",
        ],
        [([], (0.0, "unused"))],
    )
    await run()
    [(_, _, session)] = run.stt.sessions
    assert session.closed
    assert run.sleeps == [1.0]


def test_select_satellites() -> None:
    config = load_config(EXAMPLE_CONFIG)
    assert select_satellites(config, []) is config
    subset = select_satellites(config, ["voice-pe-03", "voice-pe-02"])
    assert [s.id for s in subset.satellites] == ["voice-pe-02", "voice-pe-03"]


def test_unknown_satellite_is_a_config_error(caplog: pytest.LogCaptureFixture) -> None:
    assert main(["--config", str(EXAMPLE_CONFIG), "--satellite", "garage"]) == 2
    assert "unknown --satellite garage" in caplog.text


def _test_runner(
    satellite_id: str, microphone: FakeMicrophone, stt: FakeStt, lines: list[str]
) -> SatelliteRunner:
    async def no_sleep(_seconds: float) -> None:
        pass

    return SatelliteRunner(
        satellite_id,
        microphone,
        stt,
        MAX_AUDIO,
        out=lambda satellite, message: lines.append(f"[{satellite}] {message}"),
        clock=microphone.clock,
        sleep=no_sleep,
    )


async def _run_then_cancel(runners: list[SatelliteRunner], mics: list[FakeMicrophone]) -> None:
    task = asyncio.create_task(run_until_cancelled(runners))
    for _ in range(100):
        await settle()
        if not any(m.script for m in mics):
            break
    assert not any(m.script for m in mics), "scripts did not run to the end"
    await settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(m.overlapped for m in mics), "commands to one satellite overlapped"


def test_cancel_stops_listening_and_goes_idle() -> None:
    asyncio.run(_test_cancel_stops_listening_and_goes_idle())


async def _test_cancel_stops_listening_and_goes_idle() -> None:
    clock, lines = Clock(), []
    armed = FakeMicrophone(clock, ["listening"], block_at_end=True)
    backing_off = FakeMicrophone(clock, [], fail_starts=1000, block_at_end=True)
    runners = [
        _test_runner("kitchen", armed, FakeStt(clock, []), lines),
        _test_runner("office", backing_off, FakeStt(clock, []), lines),
    ]
    task = asyncio.create_task(run_until_cancelled(runners))
    for _ in range(5):
        await settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    listen_id = armed.listen_id
    assert armed.commands[-2:] == [
        StopListening(listen_id, "shutdown"),
        SetVisualState(VisualState.IDLE),
    ]
    assert armed.closes == 1
    assert "[kitchen] stopped" in lines
    # Not armed: closed only, no reconnect for the idle visual.
    assert not any(isinstance(c, StopListening) for c in backing_off.commands)
    assert backing_off.closes >= 1 and not backing_off.in_flight
    assert not armed.overlapped and not backing_off.overlapped


def test_shutdown_failure_still_closes() -> None:
    asyncio.run(_test_shutdown_failure_still_closes())


async def _test_shutdown_failure_still_closes() -> None:
    clock, lines = Clock(), []
    microphone = FakeMicrophone(clock, ["listening"], block_at_end=True)
    microphone.fail_commands[StopListening] = MicrophoneUnavailable("connection lost")
    runner = _test_runner("kitchen", microphone, FakeStt(clock, []), lines)
    await _run_then_cancel([runner], [microphone])
    assert lines[-1] == (
        "[kitchen] shutdown: could not stop and go idle: MicrophoneUnavailable('connection lost')"
    )
    assert microphone.closes == 1


def test_unexpected_error_rearms_that_satellite_only() -> None:
    asyncio.run(_test_unexpected_error_rearms_that_satellite_only())


async def _test_unexpected_error_rearms_that_satellite_only() -> None:
    clock, lines = Clock(), []
    broken = FakeMicrophone(
        clock, ["listening", (1.0, RuntimeError("bug")), "listening"], block_at_end=True
    )
    healthy = FakeMicrophone(clock, ["listening", *segment("u1", 1.0, 2.0)], block_at_end=True)
    stt = FakeStt(clock, [([], (2.3, "Ryszardzie, światło."))])
    runners = [
        _test_runner("kitchen", broken, FakeStt(clock, []), lines),
        _test_runner("office", healthy, stt, lines),
    ]
    await _run_then_cancel(runners, [broken, healthy])
    assert "[kitchen] unexpected error: RuntimeError('bug'); retry in 1 s" in lines
    assert len([c for c in broken.commands if isinstance(c, StartListening)]) == 2
    assert any(line.startswith("[office] final 'Ryszardzie, światło.': WAKE") for line in lines)


def test_second_speech_start_with_a_segment_open_fails_loudly() -> None:
    asyncio.run(_test_second_speech_start_with_a_segment_open_fails_loudly())


async def _test_second_speech_start_with_a_segment_open_fails_loudly() -> None:
    run = Run(
        [
            "listening",
            (1.0, SpeechStarted("L", "u1", 16000, 2, 1)),
            (1.1, SpeechStarted("L", "u2", 16000, 2, 1)),
            "listening",
        ],
        [([], (0.0, "unused"))],
    )
    await run()
    # pytest may rewrite the assert and append its explanation to the message.
    [error] = [line for line in run.lines if "unexpected error" in line]
    assert error.startswith("[kitchen] unexpected error: AssertionError(")
    assert "segment u1 is still open" in error
    assert error.endswith("; retry in 1 s")
    [(_, _, session)] = run.stt.sessions
    assert session.closed


def test_cancel_during_error_recovery_exits_at_once() -> None:
    asyncio.run(_test_cancel_during_error_recovery_exits_at_once())


async def _test_cancel_during_error_recovery_exits_at_once() -> None:
    """A Ctrl-C while recovery waits for the partial reader to unwind is not swallowed."""
    clock, lines = Clock(), []
    microphone = FakeMicrophone(
        clock,
        [
            "listening",
            (1.0, SpeechStarted("L", "u1", 16000, 2, 1)),
            (1.1, MicrophoneUnavailable("connection lost")),
            "listening",
        ],
    )  # without block_at_end: a swallowed cancel would end the run with ScriptDone instead
    stt = FakeStt(clock, [([], (0.0, "unused"))], linger=0.2)
    runner = _test_runner("kitchen", microphone, stt, lines)
    task = asyncio.create_task(runner.run())
    while not stt.sessions:
        await asyncio.sleep(0)
    _, _, session = stt.sessions[0]
    await asyncio.wait_for(session.cancel_seen.wait(), 2)  # recovery is abandoning u1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert not microphone.overlapped
    assert len([c for c in microphone.commands if isinstance(c, StartListening)]) == 1


def test_partial_reader_failure_is_logged_when_abandoned(caplog: pytest.LogCaptureFixture) -> None:
    asyncio.run(_test_partial_reader_failure_is_logged(caplog))


async def _test_partial_reader_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The reader fails (its LISTENING visual) while the main loop is still reading audio; the
    segment is then abandoned on a disconnect. The reader's error is logged, not dropped."""
    clock, lines = Clock(), []
    microphone = FakeMicrophone(
        clock,
        [
            "listening",
            (1.0, SpeechStarted("L", "u1", 16000, 2, 1)),
            *[(1.0, AudioChunk("L", "u1", b"\x01\x00")) for _ in range(5)],
            (1.1, MicrophoneUnavailable("connection lost")),
            "listening",
        ],
        block_at_end=True,
    )
    microphone.fail_exact[SetVisualState(VisualState.LISTENING)] = MicrophoneUnavailable("visual")
    stt = FakeStt(clock, [([(1.05, "Ryszardzie")], (0.0, "unused"))])
    runner = _test_runner("kitchen", microphone, stt, lines)
    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(_until(lambda: not microphone.script), 2)
    await settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "partial reader failed: MicrophoneUnavailable('visual')" in caplog.text
    assert "[kitchen] unavailable: connection lost; retry in 1 s" in lines


async def _until(condition: Callable[[], bool]) -> None:
    while not condition():
        await asyncio.sleep(0.001)


def test_cancel_during_a_final_leaves_no_partial_task() -> None:
    asyncio.run(_test_cancel_during_a_final_leaves_no_partial_task())


async def _test_cancel_during_a_final_leaves_no_partial_task() -> None:
    """PR #12 review B1, same pattern here: a Ctrl-C while a final runs must also end the
    segment's partial reader, which would otherwise print and send LISTENING later."""
    clock, lines = Clock(), []
    microphone = FakeMicrophone(
        clock,
        [
            "listening",
            (1.0, SpeechStarted("L", "u1", 16000, 2, 1)),
            (2.0, SpeechEnded("L", "u1", "completed")),
        ],
        block_at_end=True,
    )
    stt = FakeStt(clock, [([], (2.5, "Ryszardzie, światło"))])

    def slow(session: FakeSession) -> None:
        session.late.append((0.1, "Ryszardzie"))
        session.final_delay = 1.0

    stt.setup = slow
    runner = _test_runner("kitchen", microphone, stt, lines)
    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(_until(lambda: bool(stt.sessions)), 2)
    _, _, session = stt.sessions[0]
    await asyncio.wait_for(_until(lambda: session.final_started), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    commands, printed = len(microphone.commands), len(lines)
    await asyncio.sleep(0.3)  # past the late partial
    assert microphone.commands[commands:] == [] and lines[printed:] == []
    assert not any("wake partial" in line for line in lines)
    assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []
    assert session.closed and not microphone.overlapped
