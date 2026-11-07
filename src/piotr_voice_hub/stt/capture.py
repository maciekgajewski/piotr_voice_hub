"""One follow-up final for one playback, written once (T-023).

Default off. The hub does not delete the files: a host `at` job, checked by the client
before `ARM` is written, removes the directory (partials included). That job is independent
of this process.
"""

import json
import logging
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from piotr_voice_hub.config import STUDY_SATELLITE_ID, SttCaptureConfig

SAMPLE_RATE = 16000
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1
MAX_PCM_BYTES = 20 * SAMPLE_RATE * SAMPLE_WIDTH_BYTES  # 640000
ARM_NAME = "ARM"
PLAY_NAME = "PLAY"
ARM_MAX_AGE_SECONDS = 120.0
DIR_MODE = 0o700
FILE_MODE = 0o600
_PREFIX = "stt-oneshot-"

Clock = Callable[[], float]


class CaptureError(Exception):
    """The capture directory cannot be used. The hub stays up and does not capture."""


def playback_id_ok(value: str) -> bool:
    if not value or len(value) > 80:
        return False
    return all(c.isalnum() or c in "-_" for c in value)


def arm_body(playback_id: str) -> bytes:
    """`voice-pe-03`, newline, the playback id, newline."""
    if not playback_id_ok(playback_id):
        raise ValueError(playback_id)
    return f"{STUDY_SATELLITE_ID}\n{playback_id}\n".encode()


def parse_arm(body: bytes) -> str | None:
    try:
        text = body.decode()
    except UnicodeError:
        return None
    satellite, sep, rest = text.partition("\n")
    playback_id, sep2, tail = rest.partition("\n")
    if sep != "\n" or sep2 != "\n" or tail != "" or satellite != STUDY_SATELLITE_ID:
        return None
    if not playback_id_ok(playback_id):
        return None
    return playback_id


def cleanup_argv(directory: Path) -> list[str]:
    """Delete the directory's contents, including partials, and leave the directory.

    The directory is the compose bind source. Removing it makes the next container start fail.
    """
    return ["find", "-P", str(directory), "-mindepth", "1", "-delete"]


def cleanup_command(directory: Path) -> str:
    return " ".join(cleanup_argv(directory))


class OneShotSttCapture:
    """Armed by `ARM`, then at most one pair for that playback id."""

    def __init__(self, config: SttCaptureConfig, *, clock: Clock = time.time) -> None:
        assert config.satellite == STUDY_SATELLITE_ID, config.satellite
        self._enabled = config.enabled
        self._directory = Path(config.directory)
        self._satellite = config.satellite
        self._clock = clock
        self._logger = logging.getLogger(f"{__name__}.SttCapture[{config.satellite}]")

    def bind(self, satellite: str) -> "BoundOneshot":
        return BoundOneshot(self, satellite)

    def prepare(self) -> None:
        """Require an existing mode-0700 directory. Do not create it (a missing parent must
        not be invented inside the container)."""
        assert self._enabled
        info = _existing_dir(self._directory)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CaptureError("stt_capture directory must be a real directory, not a symlink")
        if info.st_uid != os.getuid():
            raise CaptureError("stt_capture directory must be owned by the hub user")
        if (info.st_mode & 0o077) != 0 or (info.st_mode & 0o700) != 0o700:
            raise CaptureError("stt_capture directory must be mode 0700")
        self._logger.info("one-shot capture ready directory=%s disarmed=true", self._directory)

    def close(self) -> None:
        """The host `at` job deletes the contents. Shutdown does not."""
        self._logger.info("one-shot capture closed; host at job owns deletion")

    async def observe_final(
        self,
        satellite: str,
        playback_id: str,
        utterance_id: str,
        pcm: bytes,
        raw_joined: str,
        cleaned: str,
    ) -> None:
        """Write one final for this playback. A different playback leaves `ARM`."""
        if satellite != self._satellite or not playback_id_ok(playback_id):
            self._logger.debug("ignoring final for satellite=%s", satellite)
            return
        if not self._consume_arm(playback_id):
            return
        if len(pcm) > MAX_PCM_BYTES or len(pcm) % SAMPLE_WIDTH_BYTES != 0:
            self._logger.error(
                "one-shot capture disarmed: pcm bytes=%d over %d or odd",
                len(pcm),
                MAX_PCM_BYTES,
            )
            return
        stem = _stem(utterance_id)
        if stem is None:
            self._logger.error("one-shot capture disarmed: utterance id refused")
            return
        payload = _payload(satellite, playback_id, utterance_id, pcm, raw_joined, cleaned)
        try:
            _write_pair(self._directory, stem, pcm, payload)
        except OSError as error:
            _unlink_stem(self._directory, stem)
            self._logger.error("one-shot capture write failed: %s", error.strerror)
            return
        self._logger.info(
            "one-shot capture wrote bytes=%d file=%s.pcm playback_id=%s",
            len(pcm),
            stem,
            playback_id,
        )

    def _consume_arm(self, playback_id: str) -> bool:
        path = self._directory / ARM_NAME
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            self._logger.debug("no arm file")
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            _unlink(path)
            self._logger.error("one-shot capture disarmed: arm is not a regular file")
            return False
        if info.st_uid != os.getuid() or (info.st_mode & 0o077) != 0 or (info.st_mode & 0o400) == 0:
            _unlink(path)
            self._logger.error("one-shot capture disarmed: arm mode or owner refused")
            return False
        age = self._clock() - info.st_mtime
        if age < 0 or age > ARM_MAX_AGE_SECONDS:
            _unlink(path)
            self._logger.error("one-shot capture disarmed: arm age %.1f s", age)
            return False
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as error:
            _unlink(path)
            self._logger.error("one-shot capture disarmed: cannot read arm: %s", error.strerror)
            return False
        try:
            body = os.read(fd, 256)
        finally:
            os.close(fd)
        armed = parse_arm(body)
        if armed is None:
            _unlink(path)
            self._logger.error("one-shot capture disarmed: arm contents refused")
            return False
        if armed != playback_id:
            self._logger.debug("arm is for playback_id=%s, final is %s", armed, playback_id)
            return False
        try:
            os.unlink(path)
        except OSError as error:
            self._logger.error("one-shot capture disarmed: cannot remove arm: %s", error.strerror)
            return False
        self._logger.info("one-shot capture consumed playback_id=%s", playback_id)
        return True


class BoundOneshot:
    """`FinalObserver` for one satellite and, after `for_playback`, one playback id."""

    def __init__(
        self, capture: OneShotSttCapture, satellite: str, playback_id: str | None = None
    ) -> None:
        self._capture = capture
        self._satellite = satellite
        self._playback_id = playback_id

    def for_playback(self, playback_id: str) -> "BoundOneshot":
        return BoundOneshot(self._capture, self._satellite, playback_id)

    async def observe_final(
        self, utterance_id: str, pcm: bytes, raw_joined: str, cleaned: str
    ) -> None:
        if self._playback_id is None:
            return
        await self._capture.observe_final(
            self._satellite, self._playback_id, utterance_id, pcm, raw_joined, cleaned
        )


def _existing_dir(directory: Path) -> os.stat_result:
    parent = directory.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as error:
        raise CaptureError(
            f"stt_capture directory parent is not usable: {error.strerror}"
        ) from None
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise CaptureError(f"stt_capture directory parent is not a directory: {parent}")
    try:
        return os.lstat(directory)
    except OSError as error:
        raise CaptureError(f"stt_capture directory is not usable: {error.strerror}") from None


def _stem(utterance_id: str) -> str | None:
    if not utterance_id or len(utterance_id) > 80:
        return None
    if any(not (c.isalnum() or c in "-_") for c in utterance_id):
        return None
    return f"{_PREFIX}{utterance_id}"


def _payload(
    satellite: str,
    playback_id: str,
    utterance_id: str,
    pcm: bytes,
    raw_joined: str,
    cleaned: str,
) -> dict[str, Any]:
    return {
        "satellite": satellite,
        "playback_id": playback_id,
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
        "raw_joined": raw_joined,
        "cleaned_text": cleaned,
    }


def _write_pair(directory: Path, stem: str, pcm: bytes, payload: dict[str, Any]) -> None:
    """Synchronous: at most 640000 bytes on local disk, on the event loop (review N7)."""
    pcm_tmp = directory / f"{stem}.pcm.partial"
    json_tmp = directory / f"{stem}.json.partial"
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    _write_exclusive(pcm_tmp, pcm)
    try:
        _write_exclusive(json_tmp, encoded)
        os.replace(pcm_tmp, directory / f"{stem}.pcm")
        os.chmod(directory / f"{stem}.pcm", FILE_MODE)
        os.replace(json_tmp, directory / f"{stem}.json")
        os.chmod(directory / f"{stem}.json", FILE_MODE)
    except OSError:
        _unlink_stem(directory, stem)
        raise


def _write_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)
    os.chmod(path, FILE_MODE)


def _unlink_stem(directory: Path, stem: str) -> None:
    for name in (f"{stem}.pcm", f"{stem}.json", f"{stem}.pcm.partial", f"{stem}.json.partial"):
        _unlink(directory / name)


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as error:
        logging.getLogger(__name__).error(
            "one-shot capture could not remove %s: %s", path.name, error.strerror
        )
