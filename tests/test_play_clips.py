"""Playable `play` fixtures stay in the formats design.md §6 documents."""

from hashlib import sha256
from pathlib import Path

JFK_16BIT = Path(__file__).parent / "data" / "jfk_44100_stereo_s16.flac"
JFK_16BIT_SHA256 = "3745bf960c00b0579ccc30e8ec5abe74337b957872ed8ac3ed19d330560653d6"


def _flac_streaminfo(path: Path) -> tuple[int, int, int, int, int, int]:
    data = path.read_bytes()
    assert data[:4] == b"fLaC", f"{path} is not a native FLAC file"
    assert (data[4] & 0x7F) == 0 and int.from_bytes(data[5:8], "big") >= 34
    payload = data[8:42]
    min_block, max_block = int.from_bytes(payload[0:2], "big"), int.from_bytes(payload[2:4], "big")
    packed = int.from_bytes(payload[10:18], "big")
    sample_rate = (packed >> 44) & 0xFFFFF
    channels = ((packed >> 41) & 0x7) + 1
    bits_per_sample = ((packed >> 36) & 0x1F) + 1
    total_samples = packed & ((1 << 36) - 1)
    return sample_rate, channels, bits_per_sample, total_samples, min_block, max_block


def test_jfk_play_clip_is_sixteen_bit_flac() -> None:
    # A 24-bit file here would fail on Voice PE ≤25.10 the way the original
    # Whisper jfk.flac did on voice-pe-03 on 2026-11-22 (idle in 0.9 s).
    data = JFK_16BIT.read_bytes()
    sample_rate, channels, bits_per_sample, total_samples, min_block, max_block = _flac_streaminfo(
        JFK_16BIT
    )
    assert bits_per_sample == 16
    assert sample_rate == 44100
    assert channels == 2
    assert total_samples == 485100  # 11.000 s
    assert min_block == max_block == 4608
    assert len(data) == 488897
    assert sha256(data).hexdigest() == JFK_16BIT_SHA256
