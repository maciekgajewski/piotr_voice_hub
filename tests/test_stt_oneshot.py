"""One-shot STT capture and the study client (T-023). No GPU and no socket server."""

import asyncio
import json
import os
import stat
import threading
from pathlib import Path
from typing import Any, NoReturn

import numpy as np
import pytest

from piotr_voice_hub.config import SttCaptureConfig, SttConfig, SttPartialConfig, load_config
from piotr_voice_hub.hub.commands import CommandRouter
from piotr_voice_hub.hub.main import build_oneshot
from piotr_voice_hub.stt import capture as capture_module
from piotr_voice_hub.stt.capture import (
    ARM_MAX_AGE_SECONDS,
    ARM_NAME,
    MAX_PCM_BYTES,
    RETAIN_SECONDS,
    SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
    OneShotSttCapture,
    arm_body,
)
from piotr_voice_hub.stt.whisper import (
    BYTES_PER_SECOND,
    FasterWhisperSpeechToText,
    FasterWhisperTranscriber,
    pcm_to_float,
)
from piotr_voice_hub.stt.whisper import SAMPLE_RATE as WHISPER_RATE
from piotr_voice_hub.stt_oneshot_client import (
    ClientError,
    StudyWatch,
    arm,
    capture_outcome,
    disarm,
    main,
    parse_args,
    release_command,
    write_play_id,
)
from piotr_voice_hub.stt_oneshot_play import on_play_message, play_command

STUDY = "voice-pe-03"
PLAYBACK = "pb-1"
EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "hub.example.yaml"
_HUB = """\
log_level: INFO
api: {host: 127.0.0.1, port: 8750}
playback_http: {port: 8751}
satellites:
  defaults: {end_silence_seconds: 0.8, max_segment_seconds: 15,
             reply_timeout_seconds: 30, follow_up_seconds: 8}
  devices:
    - {id: voice-pe-03, area: kitchen, address: a.local, api_key: "key-a"}
wake_phrase: Ryszardzie
stt: {model: large-v3-turbo, device: cuda, compute_type: float16, beam_size: 5,
      partial: {interval_seconds: 0.75, window_seconds: 4.0, beam_size: 1}}
speaker_recognition:
  url: http://127.0.0.1:2141
  timeout_seconds: 1.0
  profiles: {Maciek: /profiles/maciek.npz}
tts: {uri: tcp://127.0.0.1:10201, voice: pl_PL-bass-high}
"""


def _disabled(directory: Path) -> str:
    return (
        "stt_capture: {enabled: false, satellite: voice-pe-03, directory: " + str(directory) + "}\n"
    )


class _Fatal(Exception):
    pass


def _fatal(message: str) -> NoReturn:
    raise _Fatal(message)


def _stt_config(interval: float) -> SttConfig:
    return SttConfig(
        "large-v3-turbo",
        "cuda",
        "float16",
        5,
        SttPartialConfig(interval, 4.0, 1),
    )


def _write_hub(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hub.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _config(directory: Path, *, enabled: bool = True) -> SttCaptureConfig:
    return SttCaptureConfig(enabled, STUDY, str(directory))


def _ready(directory: Path, **kwargs: Any) -> OneShotSttCapture:
    capture = OneShotSttCapture(_config(directory), **kwargs)
    capture.prepare()
    return capture


def _arm(directory: Path, body: bytes | None = None, mode: int = 0o600) -> Path:
    if body is None:
        body = arm_body(PLAYBACK)
    path = directory / ARM_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.chmod(path, mode)
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _names(directory: Path) -> set[str]:
    return {entry.name for entry in os.scandir(directory)}


async def _observe(
    capture: OneShotSttCapture,
    pcm: bytes,
    *,
    satellite: str = STUDY,
    utterance_id: str = "utt-1",
    raw: str = "Wszystkie prawa zastrzeżone.",
    cleaned: str | None = None,
    playback_id: str = PLAYBACK,
) -> None:
    await capture.observe_final(
        satellite,
        playback_id,
        utterance_id,
        pcm,
        raw,
        (),
        cleaned if cleaned is not None else raw,
        5,
        {"language": "pl"},
    )


def test_sample_format_matches_the_stt_decoder() -> None:
    assert (SAMPLE_RATE, SAMPLE_WIDTH_BYTES) == (WHISPER_RATE, 2)
    assert SAMPLE_RATE * SAMPLE_WIDTH_BYTES == BYTES_PER_SECOND


def test_default_off_does_not_touch_a_directory(tmp_path: Path) -> None:
    config = load_config(EXAMPLE)
    assert config.stt_capture is None
    assert build_oneshot(config) is None
    target = tmp_path / "not-created"
    parsed = load_config(_write_hub(tmp_path, _HUB + _disabled(target)))
    assert parsed.stt_capture is not None and parsed.stt_capture.enabled is False
    assert build_oneshot(parsed) is None
    assert not target.exists()


def test_prepare_requires_mode_0700_and_refuses_a_symlink(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    assert _mode(directory) == 0o700
    capture.close()
    loose = tmp_path / "loose"
    os.mkdir(loose, 0o755)
    with pytest.raises(capture_module.CaptureError, match="0700"):
        _ready(loose)
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(capture_module.CaptureError, match="symlink"):
        _ready(link)


def test_wrong_satellite_does_not_consume_the_arm(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    _arm(directory)
    pcm = b"\x01\x00" * 16

    async def run() -> None:
        await _observe(capture, pcm, satellite="voice-pe-02")

    asyncio.run(run())
    assert (directory / ARM_NAME).is_file()
    assert _names(directory) == {ARM_NAME}
    capture.close()


def test_overflow_and_stale_arm_disarm_without_audio(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    clock = {"now": 0.0}
    capture = _ready(directory, clock=lambda: clock["now"])
    _arm(directory)
    clock["now"] = os.lstat(directory / ARM_NAME).st_mtime + ARM_MAX_AGE_SECONDS + 1

    async def stale() -> None:
        await _observe(capture, b"\x00\x00")

    asyncio.run(stale())
    assert _names(directory) == set()
    _arm(directory)
    clock["now"] = os.lstat(directory / ARM_NAME).st_mtime

    async def overflow() -> None:
        await _observe(capture, b"\x00" * (MAX_PCM_BYTES + 2))

    asyncio.run(overflow())
    assert _names(directory) == set()
    capture.close()


def test_loose_or_wrong_arm_is_removed_and_not_written(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    _arm(directory, mode=0o644)

    async def loose() -> None:
        await _observe(capture, b"\x00\x00")

    asyncio.run(loose())
    assert _names(directory) == set()
    _arm(directory, body=b"voice-pe-02\n")

    async def wrong_body() -> None:
        await _observe(capture, b"\x00\x00")

    asyncio.run(wrong_body())
    assert _names(directory) == set()
    capture.close()


def test_a_second_final_does_not_write_again(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    _arm(directory)
    pcm = b"\x02\x00" * 32

    async def run() -> None:
        await _observe(capture, pcm, utterance_id="utt-1")
        await _observe(capture, b"\x03\x00" * 8, utterance_id="utt-2", raw="again")

    asyncio.run(run())
    files = _names(directory)
    assert files == {"stt-oneshot-utt-1.pcm", "stt-oneshot-utt-1.json"}
    assert (directory / "stt-oneshot-utt-1.pcm").read_bytes() == pcm
    capture.close()
    assert _names(directory) == set()  # shutdown deletes the pair; copy it before then


def test_cancel_and_timeout_leave_no_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory, write_timeout=0.05)
    _arm(directory)

    async def hang(fn: Any, *args: Any) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(capture_module.asyncio, "to_thread", hang)

    async def timed_out() -> None:
        await _observe(capture, b"\x00\x00")

    asyncio.run(timed_out())
    assert _names(directory) == set()

    wrote: list[Path] = []

    async def write_then_cancel(fn: Any, *args: Any) -> None:
        fn(*args)
        wrote.append(directory)
        raise asyncio.CancelledError

    monkeypatch.setattr(capture_module.asyncio, "to_thread", write_then_cancel)
    _arm(directory)

    async def cancelled() -> None:
        with pytest.raises(asyncio.CancelledError):
            await _observe(capture, b"\x04\x00" * 4)

    asyncio.run(cancelled())
    assert wrote  # the write ran, then the cancel path deleted it
    assert _names(directory) == set()
    capture.close()


def test_a_cancelled_writer_cannot_publish_after_the_next_final(tmp_path: Path) -> None:
    """Cancelling the asyncio wrapper must not let the thread publish once abort is set,
    including after another final has run."""
    directory = tmp_path / "cap"
    capture = _ready(directory, write_timeout=0.05)
    _arm(directory)
    started = threading.Event()
    release = threading.Event()
    real = capture._write_files

    def blocked(stem: str, pcm: bytes, payload: dict[str, Any], abort: threading.Event) -> None:
        started.set()
        release.wait(timeout=2)
        real(stem, pcm, payload, abort)

    capture._write_files = blocked  # type: ignore[method-assign]

    async def run() -> None:
        task = asyncio.create_task(_observe(capture, b"\x07\x00" * 8, utterance_id="utt-1"))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _observe(capture, b"\x08\x00" * 8, utterance_id="utt-2")
        release.set()
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert _names(directory) == set()
    capture.close()


def test_expired_pairs_and_symlinks_are_removed(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    clock = {"now": 0.0}
    capture = _ready(directory, clock=lambda: clock["now"])
    _arm(directory)
    clock["now"] = os.lstat(directory / ARM_NAME).st_mtime
    pcm = b"\x05\x00" * 8

    async def run() -> None:
        await _observe(capture, pcm, utterance_id="utt-9")

    asyncio.run(run())
    meta = directory / "stt-oneshot-utt-9.json"
    body = json.loads(meta.read_text(encoding="utf-8"))
    assert body["remove_after_unix"] == clock["now"] + RETAIN_SECONDS
    other = tmp_path / "elsewhere"
    other.write_text("keep", encoding="utf-8")
    (directory / "stt-oneshot-utt-9.pcm.link").symlink_to(other)
    capture.sweep_expired()
    assert other.read_text(encoding="utf-8") == "keep"
    assert not (directory / "stt-oneshot-utt-9.pcm.link").exists()
    clock["now"] = body["remove_after_unix"]
    capture.sweep_expired()
    assert _names(directory) == set()
    capture.close()


class _Segment:
    def __init__(self, text: str, **scores: float) -> None:
        self.text = text
        for key, value in scores.items():
            setattr(self, key, value)


class _Info:
    language = "pl"
    language_probability = 0.91
    duration = 1.28
    duration_after_vad = 1.0
    all_language_probs = [("pl", 0.91)]
    transcription_options = {"initial_prompt": "SECRET"}


class _Model:
    def __init__(self, segments: list[_Segment]) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []
        self.audio: np.ndarray | None = None

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[list[_Segment], _Info]:
        self.calls.append(kwargs)
        self.audio = audio
        return self.segments, _Info()


def test_final_stores_the_pcm_and_the_text_from_before_cleanup(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    _arm(directory)
    model = _Model(
        [
            _Segment(
                " Wszystkie prawa zastrzeżone. ",
                start=0.0,
                end=1.2,
                avg_logprob=-0.2,
                no_speech_prob=0.71,
                compression_ratio=1.1,
            ),
            _Segment(
                " (muzyka) ",
                start=1.2,
                end=1.28,
                avg_logprob=-0.4,
                no_speech_prob=0.2,
                compression_ratio=1.4,
            ),
        ]
    )

    async def run() -> str:
        stt = FasterWhisperSpeechToText(
            _stt_config(10), lambda _config: FasterWhisperTranscriber(model), on_fatal=_fatal
        )
        await stt.start()
        model.calls.clear()
        session = stt.create_streaming_session(
            "utt-7", 16.0, oneshot=capture.bind(STUDY).for_playback(PLAYBACK)
        )
        pcm = b"\x01\x00" * 800
        await session.send_audio(pcm)
        text = await session.transcribe_final()
        await session.close()
        return text

    text = asyncio.run(run())
    assert text == "Wszystkie prawa zastrzeżone."
    assert model.calls == [{"language": "pl", "beam_size": 5}]
    assert model.audio is not None
    assert np.array_equal(model.audio, pcm_to_float(b"\x01\x00" * 800))
    pcm_path = directory / "stt-oneshot-utt-7.pcm"
    meta_path = directory / "stt-oneshot-utt-7.json"
    assert pcm_path.read_bytes() == b"\x01\x00" * 800
    assert _mode(pcm_path) == 0o600 and _mode(meta_path) == 0o600
    body = json.loads(meta_path.read_text(encoding="utf-8"))
    assert body["format"] == {
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
        "sample_width_bytes": 2,
    }
    assert body["raw_joined"] == "Wszystkie prawa zastrzeżone. (muzyka)"
    assert body["cleaned_text"] == "Wszystkie prawa zastrzeżone."
    assert body["segments"][0]["text"] == " Wszystkie prawa zastrzeżone. "
    assert body["segments"][1]["text"] == " (muzyka) "
    assert body["segments"][0]["no_speech_prob"] == 0.71
    assert body["info"]["language"] == "pl"
    assert "all_language_probs" not in body["info"]
    assert "SECRET" not in meta_path.read_text(encoding="utf-8")
    assert "api_key" not in meta_path.read_text(encoding="utf-8")
    assert not (directory / ARM_NAME).exists()
    capture.close()


def test_a_final_without_the_hook_does_not_read_an_arm_file(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    os.mkdir(directory, 0o700)
    _arm(directory)
    model = _Model([_Segment(" czesc ")])

    async def run() -> None:
        stt = FasterWhisperSpeechToText(
            _stt_config(10), lambda _config: FasterWhisperTranscriber(model), on_fatal=_fatal
        )
        await stt.start()
        session = stt.create_streaming_session("utt-0", 16.0)
        await session.send_audio(b"\x00\x00" * 160)
        assert await session.transcribe_final() == "czesc"
        await session.close()

    asyncio.run(run())
    assert _names(directory) == {ARM_NAME}


def test_partials_do_not_consume_the_arm(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    _arm(directory)
    model = _Model([_Segment(" czesc ")])

    async def run() -> None:
        stt = FasterWhisperSpeechToText(
            _stt_config(0.02),
            lambda _config: FasterWhisperTranscriber(model),
            on_fatal=_fatal,
        )
        await stt.start()
        model.calls.clear()
        session = stt.create_streaming_session(
            "utt-3", 16.0, oneshot=capture.bind(STUDY).for_playback(PLAYBACK)
        )
        await session.send_audio(b"\x00\x00" * 400)
        while not any(call.get("beam_size") == 1 for call in model.calls):
            await asyncio.sleep(0.005)
        assert (directory / ARM_NAME).is_file()
        assert not any(name.endswith(".pcm") for name in _names(directory))
        await session.transcribe_final()
        await session.close()

    asyncio.run(run())
    assert (directory / ARM_NAME).exists() is False
    assert (directory / "stt-oneshot-utt-3.pcm").is_file()
    capture.close()


class _Session:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def speak(self, text: str, volume: float | None, listen_after: bool) -> str:
        self.calls.append("speak")
        return "pb"

    def play(self, url: str, listen_after: bool) -> str:
        self.calls.append("play")
        return "pb"

    def release(self) -> None:
        self.calls.append("release")

    def stop(self) -> None:
        self.calls.append("stop")


def test_the_client_refuses_to_arm_without_the_flags_or_for_another_satellite(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "cap"
    ws = "ws://127.0.0.1:9/ws"
    assert main(["--satellite", STUDY, "--ws", ws, "--directory", str(directory)]) == 2
    assert not directory.exists()
    assert (
        main(
            [
                "--arm",
                "--confirm-one-shot",
                "--satellite",
                "voice-pe-02",
                "--ws",
                ws,
                "--directory",
                str(directory),
            ]
        )
        == 2
    )
    assert not directory.exists()
    with pytest.raises(ClientError, match="120"):
        arm_args = [
            "--arm",
            "--confirm-one-shot",
            "--satellite",
            STUDY,
            "--ws",
            ws,
            "--directory",
            str(directory),
            "--timeout",
            "121",
        ]
        parse_args(arm_args)


def test_a_different_playback_does_not_consume_the_arm(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    capture = _ready(directory)
    _arm(directory, arm_body("pb-intended"))

    async def run() -> None:
        await _observe(capture, b"\x01\x00" * 8, playback_id="pb-other", utterance_id="utt-other")
        await _observe(capture, b"\x02\x00" * 8, playback_id="pb-intended", utterance_id="utt-ok")

    asyncio.run(run())
    assert _names(directory) == {"stt-oneshot-utt-ok.pcm", "stt-oneshot-utt-ok.json"}
    body = json.loads((directory / "stt-oneshot-utt-ok.json").read_text(encoding="utf-8"))
    assert body["playback_id"] == "pb-intended"
    capture.close()


def test_disabled_restart_deletes_unexpired_files_and_partials(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    directory.mkdir(mode=0o700)
    (directory / "stt-oneshot-utt-old.pcm").write_bytes(b"\x00\x00")
    (directory / "stt-oneshot-utt-old.json").write_text(
        json.dumps({"remove_after_unix": 10**12}), encoding="utf-8"
    )
    (directory / "stt-oneshot-utt-old.pcm.partial").write_bytes(b"\x01\x00")
    (directory / ARM_NAME).write_bytes(arm_body(PLAYBACK))
    text = _HUB + _disabled(directory)
    loaded = load_config(_write_hub(tmp_path, text))
    assert build_oneshot(loaded) is None
    assert _names(directory) == set()


def test_client_arms_only_after_the_named_playback_and_matches_the_utterance(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "cap"
    directory.mkdir(mode=0o700)
    source = Path(release_command.__code__.co_filename).read_text(encoding="utf-8")
    assert '"type": "play"' not in source
    assert '"type": "speak"' not in source
    watch = StudyWatch(STUDY)
    assert watch.on_message(
        {
            "type": "transcript_final",
            "satellite": STUDY,
            "data": {"utterance_id": "utt-early", "raw_text": "earlier", "follow_up": True},
        }
    ) == release_command(STUDY, next(iter(watch.releases.values())))
    assert not (directory / ARM_NAME).exists()
    assert (
        watch.on_message(
            {
                "type": "playback_finished",
                "satellite": STUDY,
                "data": {"playback_id": "pb-other", "listening_for_follow_up": True},
            }
        )
        is None
    )
    assert watch.should_arm() is False
    write_play_id(directory, "pb-study")
    watch.playback_id = "pb-study"
    assert watch.should_arm() is False  # PLAY is not enough; the finish event is required
    watch.on_message(
        {
            "type": "playback_finished",
            "satellite": STUDY,
            "data": {"playback_id": "pb-study", "listening_for_follow_up": True},
        }
    )
    assert watch.should_arm() is True
    arm(directory, "pb-study")
    watch.armed = True
    meta = {
        "playback_id": "pb-study",
        "utterance_id": "utt-study",
        "cleaned_text": "Wszystkie prawa zastrzeżone.",
    }
    (directory / "stt-oneshot-utt-study.json").write_text(json.dumps(meta), encoding="utf-8")
    assert capture_outcome(directory, "pb-study", {}, set()) == "waiting"
    command = watch.on_message(
        {
            "type": "transcript_final",
            "satellite": STUDY,
            "data": {
                "utterance_id": "utt-study",
                "raw_text": meta["cleaned_text"],
                "follow_up": True,
            },
        }
    )
    assert command is not None and command["type"] == "release"
    session = _Session()
    assert CommandRouter({STUDY: session}).handle(json.dumps(command))["type"] == "ack"
    assert session.calls == ["release"]
    watch.on_message({"type": "ack", "reply_to": command["id"], "satellite": STUDY, "data": {}})
    assert capture_outcome(directory, "pb-study", watch.transcripts, watch.acked) == "matched"
    meta["cleaned_text"] = ""
    (directory / "stt-oneshot-utt-study.json").write_text(json.dumps(meta), encoding="utf-8")
    assert capture_outcome(directory, "pb-study", {}, set()) == "empty"


def test_play_client_sends_one_play_and_ignores_transcripts() -> None:
    source = Path(play_command.__code__.co_filename).read_text(encoding="utf-8")
    assert '"type": "speak"' not in source
    assert '"type": "release"' not in source
    command = play_command(STUDY, "https://example.com/study.wav")
    assert command["type"] == "play" and command["data"]["listen_after"] is True
    playback_id = None
    steps = []
    messages = [
        {
            "type": "transcript_final",
            "satellite": STUDY,
            "data": {"text": "again", "follow_up": True},
        },
        {
            "type": "ack",
            "reply_to": command["id"],
            "satellite": STUDY,
            "data": {"playback_id": "pb-study"},
        },
        {
            "type": "transcript_final",
            "satellite": STUDY,
            "data": {"text": "again", "follow_up": True},
        },
        {
            "type": "playback_finished",
            "satellite": STUDY,
            "data": {"playback_id": "pb-study", "listening_for_follow_up": True},
        },
    ]
    for message in messages:
        step = on_play_message(message, playback_id, STUDY)
        steps.append(step)
        if step == "ack":
            playback_id = message["data"]["playback_id"]
    assert steps == ["ignore", "ack", "ignore", "finished"]
    assert playback_id == "pb-study"


def test_runbook_uses_the_container_mount_not_a_host_only_path() -> None:
    root = Path(__file__).resolve().parent.parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    design = (root / "docs" / "design.md").read_text(encoding="utf-8")
    runbook = (root / "docs" / "tasks" / "T-023-stt-oneshot-capture" / "runbook.md").read_text(
        encoding="utf-8"
    )
    assert "target: /stt-oneshot" in compose
    assert "directory: /stt-oneshot" in design
    assert "directory: /home/maciek/probe" not in design
    assert "/stt-oneshot" in runbook
    assert "speak_test" in runbook


def test_arm_file_names_the_playback_and_disarm_removes_it(tmp_path: Path) -> None:
    directory = tmp_path / "cap"
    arm(directory, PLAYBACK)
    path = directory / ARM_NAME
    assert path.read_bytes() == arm_body(PLAYBACK)
    assert _mode(path) == 0o600 and _mode(directory) == 0o700
    with pytest.raises(ClientError, match="already present"):
        arm(directory, PLAYBACK)
    os.utime(path, (1, 1))
    arm(directory, PLAYBACK)
    disarm(directory)
    assert not path.exists()
