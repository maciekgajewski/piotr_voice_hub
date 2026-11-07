"""One follow-up final on the study satellite, written once, then disarmed (T-023, design §7.1).

Default off: the hub constructs this only when `stt_capture.enabled` is true. Nothing here is a
WebSocket event. The arm file is the trigger; the next follow-up final either writes one pair
or disarms with no audio left behind.
"""

import asyncio
import json
import logging
import math
import os
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from piotr_voice_hub.config import STUDY_SATELLITE_ID, SttCaptureConfig

# Same PCM the satellites submit and `whisper.pcm_to_float` decodes. Kept here so this module
# does not import faster-whisper; tests assert the numbers match `whisper.py`.
SAMPLE_RATE = 16000
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1
# 20 s at 16 kHz 16-bit mono. A segment is at most max_segment_seconds (15) plus 1 s pre-roll.
MAX_PCM_BYTES = 20 * SAMPLE_RATE * SAMPLE_WIDTH_BYTES
ARM_NAME = "ARM"
ARM_BODY = b"voice-pe-03\n"
ARM_MAX_AGE_SECONDS = 120.0
RETAIN_SECONDS = 1800.0
WRITE_TIMEOUT_SECONDS = 2.0
DIR_MODE = 0o700
FILE_MODE = 0o600
_PREFIX = "stt-oneshot-"

Clock = Callable[[], float]


class CaptureError(Exception):
    """The capture directory is unusable. The hub exits before arming satellites."""


@dataclass(frozen=True)
class SegmentRecord:
    """One Whisper segment as decoded, before `TranscriptPreprocessor`."""

    text: str
    start: float | None = None
    end: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None

    def as_json(self) -> dict[str, str | float]:
        body: dict[str, str | float] = {"text": self.text}
        for key in ("start", "end", "avg_logprob", "no_speech_prob", "compression_ratio"):
            value = getattr(self, key)
            if value is not None:
                body[key] = value
        return body


@dataclass
class DecodeRecords:
    """Filled only for a final whose one-shot observer is attached. Partials pass none."""

    segments: list[SegmentRecord]
    info: dict[str, str | int | float]

    def __init__(self) -> None:
        self.segments = []
        self.info = {}


def segment_record(segment: object) -> SegmentRecord | None:
    text = getattr(segment, "text", None)
    if not isinstance(text, str):
        return None
    return SegmentRecord(
        text=text,
        start=_finite(getattr(segment, "start", None)),
        end=_finite(getattr(segment, "end", None)),
        avg_logprob=_finite(getattr(segment, "avg_logprob", None)),
        no_speech_prob=_finite(getattr(segment, "no_speech_prob", None)),
        compression_ratio=_finite(getattr(segment, "compression_ratio", None)),
    )


def info_fields(info: object) -> dict[str, str | int | float]:
    """Whitelisted scalars. `transcription_options` and language lists are not copied."""
    fields: dict[str, str | int | float] = {}
    language = getattr(info, "language", None)
    if isinstance(language, str) and language:
        fields["language"] = language
    for key in ("language_probability", "duration", "duration_after_vad"):
        value = _finite(getattr(info, key, None))
        if value is not None:
            fields[key] = value
    return fields


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


class _InFlight:
    """One writer. `abort` is never cleared, so a later final cannot revive it."""

    def __init__(self) -> None:
        self.abort = threading.Event()
        self.done = threading.Event()


class OneShotSttCapture:
    """Armed by `ARM`, then at most one successful pair. Every failure deletes the arm."""

    def __init__(
        self,
        config: SttCaptureConfig,
        *,
        clock: Clock = time.time,
        write_timeout: float = WRITE_TIMEOUT_SECONDS,
    ) -> None:
        assert config.enabled, "construct a capture only when it is enabled"
        assert config.satellite == STUDY_SATELLITE_ID, config.satellite
        self._directory = Path(config.directory)
        self._satellite = config.satellite
        self._clock = clock
        self._write_timeout = write_timeout
        self._lock = asyncio.Lock()
        self._publish = threading.Lock()
        self._inflight_guard = threading.Lock()
        self._inflight: list[_InFlight] = []
        self._timers: list[asyncio.TimerHandle] = []
        self._logger = logging.getLogger(f"{__name__}.SttCapture[{config.satellite}]")

    def bind(self, satellite: str) -> "BoundOneshot":
        """The object a session passes into STT. It pins the satellite id."""
        return BoundOneshot(self, satellite)

    def prepare(self) -> None:
        """Create or check the directory, then drop pairs that are already past retention."""
        self._ensure_directory()
        self._sweep()
        self._logger.info("one-shot capture ready directory=%s disarmed=true", self._directory)

    def close(self) -> None:
        """Shutdown: cancel retention timers and delete `ARM`. Finished pairs stay until
        retention (the next enabled start sweeps them)."""
        for handle in self._timers:
            handle.cancel()
        self._timers.clear()
        with self._inflight_guard:
            pending = tuple(self._inflight)
        for slot in pending:
            slot.abort.set()
        for slot in pending:
            slot.done.wait(self._write_timeout)
        self._unlink_quiet(self._directory / ARM_NAME)
        self._logger.info("one-shot capture closed")

    async def observe_final(
        self,
        satellite: str,
        utterance_id: str,
        pcm: bytes,
        raw_joined: str,
        segments: tuple[SegmentRecord, ...],
        cleaned: str,
        beam_size: int,
        info: dict[str, str | int | float],
    ) -> None:
        """Store one final, or disarm. A failure here must not fail the utterance."""
        async with self._lock:
            if satellite != self._satellite:
                self._logger.debug("ignoring final for satellite=%s", satellite)
                return
            if not self._consume_arm():
                return
            if len(pcm) > MAX_PCM_BYTES or len(pcm) % SAMPLE_WIDTH_BYTES != 0:
                self._logger.error(
                    "one-shot capture disarmed: pcm bytes=%d over %d or odd",
                    len(pcm),
                    MAX_PCM_BYTES,
                )
                return
            stem = self._stem(utterance_id)
            if stem is None:
                self._logger.error("one-shot capture disarmed: utterance id refused")
                return
            payload = _payload(
                satellite,
                utterance_id,
                pcm,
                raw_joined,
                segments,
                cleaned,
                beam_size,
                info,
                self._clock() + RETAIN_SECONDS,
            )
            slot = _InFlight()
            with self._inflight_guard:
                self._inflight.append(slot)

            def write() -> None:
                try:
                    self._write_files(stem, pcm, payload, slot.abort)
                finally:
                    with self._inflight_guard:
                        if slot in self._inflight:
                            self._inflight.remove(slot)
                    slot.done.set()

            task = asyncio.create_task(asyncio.to_thread(write), name=f"stt-oneshot-{utterance_id}")
            try:
                async with asyncio.timeout(self._write_timeout):
                    await task
            except TimeoutError:
                await self._stop_write(task, slot, stem, "write timed out")
                return
            except asyncio.CancelledError:
                await self._stop_write(task, slot, stem, "cancelled")
                raise
            except Exception as error:
                await self._stop_write(
                    task, slot, stem, f"write failed: {error.__class__.__name__}"
                )
                return
            self._schedule_removal(stem)
            self._logger.info("one-shot capture wrote bytes=%d file=%s.pcm", len(pcm), stem)

    def sweep_expired(self) -> None:
        """Drop pairs whose `remove_after_unix` has passed. Used at start and by tests."""
        self._sweep()

    async def _stop_write(
        self, task: asyncio.Task[None], slot: "_InFlight", stem: str, reason: str
    ) -> None:
        # The attempt's abort stays set. A later final must not clear it: cancelling the
        # asyncio wrapper does not stop the writer thread.
        slot.abort.set()
        await self._wait_until(slot.done)
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=self._write_timeout)
        self._unlink_stem(stem)
        self._logger.error("one-shot capture disarmed: %s", reason)

    async def _wait_until(self, done: threading.Event) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._write_timeout
        while not done.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.01, remaining))

    def _consume_arm(self) -> bool:
        """True only when a fresh, private, exact `ARM` was unlinked. Otherwise no audio."""
        path = self._directory / ARM_NAME
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            self._logger.debug("no arm file")
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            self._unlink_quiet(path)
            self._logger.error("one-shot capture disarmed: arm is not a regular file")
            return False
        if info.st_uid != os.getuid() or (info.st_mode & 0o077) != 0 or (info.st_mode & 0o400) == 0:
            self._unlink_quiet(path)
            self._logger.error("one-shot capture disarmed: arm mode or owner refused")
            return False
        age = self._clock() - info.st_mtime
        if age < 0 or age > ARM_MAX_AGE_SECONDS:
            self._unlink_quiet(path)
            self._logger.error("one-shot capture disarmed: arm age %.1f s", age)
            return False
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as error:
            self._unlink_quiet(path)
            self._logger.error("one-shot capture disarmed: cannot read arm: %s", error.strerror)
            return False
        try:
            body = os.read(fd, len(ARM_BODY) + 1)
        finally:
            os.close(fd)
        if body != ARM_BODY:
            self._unlink_quiet(path)
            self._logger.error("one-shot capture disarmed: arm contents refused")
            return False
        try:
            os.unlink(path)
        except OSError as error:
            self._logger.error("one-shot capture disarmed: cannot remove arm: %s", error.strerror)
            return False
        self._logger.info("one-shot capture armed window consumed")
        return True

    def _stem(self, utterance_id: str) -> str | None:
        if not utterance_id or len(utterance_id) > 80:
            return None
        if any(not (c.isalnum() or c in "-_") for c in utterance_id):
            return None
        return f"{_PREFIX}{utterance_id}"

    def _write_files(
        self, stem: str, pcm: bytes, payload: dict[str, Any], abort: threading.Event
    ) -> None:
        pcm_tmp = self._directory / f"{stem}.pcm.partial"
        json_tmp = self._directory / f"{stem}.json.partial"
        pcm_final = self._directory / f"{stem}.pcm"
        json_final = self._directory / f"{stem}.json"
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self._write_exclusive(pcm_tmp, pcm)
        self._write_exclusive(json_tmp, encoded)
        with self._publish:
            if abort.is_set():
                self._unlink_quiet(pcm_tmp, json_tmp)
                return
            os.replace(pcm_tmp, pcm_final)
            os.chmod(pcm_final, FILE_MODE)
            os.replace(json_tmp, json_final)
            os.chmod(json_final, FILE_MODE)
            if abort.is_set():
                self._unlink_quiet(pcm_final, json_final)

    def _write_exclusive(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)
        os.chmod(path, FILE_MODE)

    def _schedule_removal(self, stem: str) -> None:
        pcm, meta = self._pair(stem)
        handle = asyncio.get_running_loop().call_later(
            RETAIN_SECONDS, self._unlink_quiet, pcm, meta
        )
        self._timers.append(handle)

    def _sweep(self) -> None:
        if not self._directory.is_dir():
            return
        for entry in os.scandir(self._directory):
            name = entry.name
            if not name.startswith(_PREFIX):
                continue
            try:
                info = os.lstat(entry.path)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                self._unlink_quiet(Path(entry.path))
                self._logger.error("one-shot capture removed symlink %s", name)
                continue
            if name.endswith(".partial"):
                self._unlink_quiet(Path(entry.path))
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if name.endswith(".pcm"):
                stem = name[: -len(".pcm")]
                if not self._regular(self._directory / f"{stem}.json"):
                    self._unlink_stem(stem)
                continue
            if not name.endswith(".json"):
                continue
            stem = name[: -len(".json")]
            if not self._regular(self._directory / f"{stem}.pcm") or self._expired(
                Path(entry.path), info
            ):
                self._unlink_stem(stem)
                self._logger.info("one-shot capture removed expired %s", stem)

    def _expired(self, meta: Path, info: os.stat_result) -> bool:
        when: object = None
        try:
            fd = os.open(meta, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                raw = os.read(fd, 1024 * 1024)
            finally:
                os.close(fd)
            loaded = json.loads(raw.decode())
            if isinstance(loaded, dict):
                when = loaded.get("remove_after_unix")
        except (OSError, UnicodeError, json.JSONDecodeError):
            when = None
        if isinstance(when, int | float) and not isinstance(when, bool):
            return float(when) <= self._clock()
        return self._clock() - info.st_mtime >= RETAIN_SECONDS

    def _regular(self, path: Path) -> bool:
        try:
            info = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)

    def _pair(self, stem: str) -> tuple[Path, Path]:
        return self._directory / f"{stem}.pcm", self._directory / f"{stem}.json"

    def _unlink_stem(self, stem: str) -> None:
        pcm, meta = self._pair(stem)
        self._unlink_quiet(
            pcm,
            meta,
            self._directory / f"{stem}.pcm.partial",
            self._directory / f"{stem}.json.partial",
        )

    def _unlink_quiet(self, *paths: Path) -> None:
        for path in paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue
            except OSError as error:
                self._logger.error(
                    "one-shot capture could not remove %s: %s", path.name, error.strerror
                )

    def _ensure_directory(self) -> None:
        parent = self._directory.parent
        parent_info = _lstat_dir(parent, "stt_capture directory parent")
        if not stat.S_ISDIR(parent_info.st_mode):
            raise CaptureError(f"stt_capture directory parent is not a directory: {parent}")
        try:
            info = os.lstat(self._directory)
        except FileNotFoundError:
            os.mkdir(self._directory, DIR_MODE)
            os.chmod(self._directory, DIR_MODE)
            info = _lstat_dir(self._directory, "stt_capture directory")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CaptureError("stt_capture directory must be a real directory, not a symlink")
        if info.st_uid != os.getuid():
            raise CaptureError("stt_capture directory must be owned by the hub user")
        if (info.st_mode & 0o077) != 0 or (info.st_mode & 0o700) != 0o700:
            raise CaptureError("stt_capture directory must be mode 0700")


class BoundOneshot:
    """`FinalObserver` for one satellite. The STT session does not know the satellite id."""

    def __init__(self, capture: OneShotSttCapture, satellite: str) -> None:
        self._capture = capture
        self._satellite = satellite

    async def observe_final(
        self,
        utterance_id: str,
        pcm: bytes,
        raw_joined: str,
        segments: tuple[SegmentRecord, ...],
        cleaned: str,
        beam_size: int,
        info: dict[str, str | int | float],
    ) -> None:
        await self._capture.observe_final(
            self._satellite,
            utterance_id,
            pcm,
            raw_joined,
            segments,
            cleaned,
            beam_size,
            info,
        )


def _lstat_dir(path: Path, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as error:
        raise CaptureError(f"{label} is not usable: {error.strerror}") from None


def _payload(
    satellite: str,
    utterance_id: str,
    pcm: bytes,
    raw_joined: str,
    segments: tuple[SegmentRecord, ...],
    cleaned: str,
    beam_size: int,
    info: dict[str, str | int | float],
    remove_after: float,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "satellite": satellite,
        "utterance_id": utterance_id,
        "kind": "final",
        "follow_up": True,
        "format": {
            "encoding": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "sample_width_bytes": SAMPLE_WIDTH_BYTES,
        },
        "pcm_bytes": len(pcm),
        "audio_ms": len(pcm) * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH_BYTES),
        "beam_size": beam_size,
        "raw_joined": raw_joined,
        "segments": [segment.as_json() for segment in segments],
        "cleaned_text": cleaned,
        "remove_after_unix": remove_after,
    }
    if info:
        body["info"] = info
    return body
