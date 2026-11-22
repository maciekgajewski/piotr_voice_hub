"""Generate T-028 recorded-audio fixtures (synthesized only; never records).

Silence, noise, tones and mixes are made here with a fixed seed. Speech is
synthesized by a throwaway Piper service (see tools/generate_audio_fixtures.sh)
on a loopback URI, never the production piper on 10201. Committed WAVs and
tests/data/fixtures/manifest.json are the CI source; this script is the
provenance.

Piper-derived speech is not bit-reproducible on regeneration: the same text
and voice can yield different PCM across model or runtime versions. Silence
(zeros), noise_only (seeded PRNG), the byte-identical copy of
tests/data/wake_pl_16k.wav, and concatenations of those are bit-reproducible.
The two_speakers and tv_music mixes are deterministic given the same input
PCM and seed, but their Piper layers are not.

Usage, from the repo root, after the throwaway Piper is listening:

    uv run python tools/generate_audio_fixtures.py --piper-uri tcp://127.0.0.1:10228
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np

from piotr_voice_hub.config import TtsConfig, parse_tcp_uri
from piotr_voice_hub.tts.wyoming_piper import WyomingPiperTts

RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
SEED = 28
# Trailing pad is added by the test runner, not stored in most clips.
NOISE_SIGMA = 400
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "data" / "fixtures"
EXISTING_WAKE = Path(__file__).resolve().parent.parent / "tests" / "data" / "wake_pl_16k.wav"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
VOICE = "pl_PL-bass-high"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PRODUCTION_PIPER_PORT = 10201
VOICE_LICENSE = (
    "Apache-2.0, rhasspy/piper-voices pl_PL-bass-high (c10ece1aade47bb51c153c893d14e5bf8e5b7117)"
)

PHRASES = {
    "question_no_wake": "Jaka będzie jutro pogoda?",
    "near_miss": "Rysiek, włącz światło",
    "inflected": "Ryszarda nie ma w domu",
    "follow_up": "Ustaw minutnik na pięć minut",
    "two_speakers_a": "Która jest godzina?",
    "two_speakers_b": "Włącz radio w kuchni",
    "tv_a": "Dzisiaj wieczorem mecz zaczyna się o dwudziestej",
    "tv_b": "Wiadomości z kraju i ze świata po przerwie",
    "tv_c": "Przepis na ciasto znajdziecie na naszej stronie",
}


def pcm_from_samples(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.rint(samples), -32767, 32767).astype("<i2")
    return clipped.tobytes()


def samples_from_pcm(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64)


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(RATE)
        wav.writeframes(pcm)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (
            RATE,
            SAMPLE_WIDTH,
            CHANNELS,
        ), f"{path} must be 16 kHz 16-bit mono"
        return wav.readframes(wav.getnframes())


def silence(seconds: float) -> bytes:
    return b"\0\0" * int(round(seconds * RATE))


def noise(seconds: float, sigma: float, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    samples = rng.normal(0, sigma, int(round(seconds * RATE)))
    return pcm_from_samples(samples)


def tones(seconds: float, freqs: tuple[float, ...], amp: float = 0.12) -> bytes:
    n = int(round(seconds * RATE))
    t = np.arange(n, dtype=np.float64) / RATE
    samples = np.zeros(n, dtype=np.float64)
    for freq in freqs:
        samples += amp * np.sin(2 * math.pi * freq * t)
    fade = int(0.02 * RATE)
    if fade > 0:
        ramp = np.linspace(0, 1, fade)
        samples[:fade] *= ramp
        samples[-fade:] *= ramp[::-1]
    return pcm_from_samples(samples * 32767)


def mix_at(
    parts: list[tuple[bytes, int, float]],
    length: int | None = None,
) -> bytes:
    """Overlay PCM parts as (pcm, offset_samples, gain)."""
    end = 0
    parsed: list[tuple[np.ndarray, int, float]] = []
    for pcm, offset, gain in parts:
        samples = samples_from_pcm(pcm)
        parsed.append((samples, offset, gain))
        end = max(end, offset + len(samples))
    n = length if length is not None else end
    out = np.zeros(n, dtype=np.float64)
    for samples, offset, gain in parsed:
        sl = samples * gain
        last = min(offset + len(sl), n)
        if offset < n:
            out[offset:last] += sl[: last - offset]
    return pcm_from_samples(out)


def pitch_shift(pcm: bytes, ratio: float) -> bytes:
    """Turntable-style shift: pitch and duration change together."""
    samples = samples_from_pcm(pcm)
    if len(samples) < 2:
        return pcm
    new_len = max(2, int(round(len(samples) / ratio)))
    x = np.arange(len(samples), dtype=np.float64)
    new_x = np.linspace(0, len(samples) - 1, new_len)
    return pcm_from_samples(np.interp(new_x, x, samples))


def resample_to_16k_mono_s16(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    """Resample with ffmpeg when the format is not already 16 kHz 16-bit mono."""
    if (rate, width, channels) == (RATE, SAMPLE_WIDTH, CHANNELS):
        return pcm
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.wav"
        dest = Path(tmp) / "out.wav"
        with wave.open(str(src), "w") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-ar",
                str(RATE),
                "-ac",
                str(CHANNELS),
                "-sample_fmt",
                "s16",
                str(dest),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg resample failed: {result.stderr}")
        return read_wav(dest)


async def synthesize(tts: WyomingPiperTts, text: str) -> bytes:
    async with tts.synthesize(text) as speech:
        chunks = bytearray()
        async for chunk in speech:
            chunks.extend(chunk)
        fmt = speech.format
    return resample_to_16k_mono_s16(bytes(chunks), fmt.rate, fmt.width, fmt.channels)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wav_info(path: Path) -> tuple[int, float]:
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        return path.stat().st_size, frames / rate


def entry(
    fixture_id: str,
    path: Path,
    *,
    text: str,
    expected_wake: bool,
    expected_remainder: str | None,
    expected_transcript_words: list[str] | None,
    expected_vad_segments: int | None,
    vad_mode: str,
    source: str,
    generator: dict[str, Any],
    notes: str = "",
    min_speech_start_seconds: float | None = None,
    soft_transcript: bool = False,
    license: str = VOICE_LICENSE,
) -> dict[str, Any]:
    size, duration = wav_info(path)
    rel = path.relative_to(FIXTURES_DIR).as_posix()
    item: dict[str, Any] = {
        "id": fixture_id,
        "path": rel,
        "text": text,
        "expected_wake": expected_wake,
        "expected_remainder": expected_remainder,
        "expected_transcript_words": expected_transcript_words,
        "expected_vad_segments": expected_vad_segments,
        "vad_mode": vad_mode,
        "soft_transcript": soft_transcript,
        "min_speech_start_seconds": min_speech_start_seconds,
        "source": source,
        "license": license,
        "generator": generator,
        "notes": notes,
        "sha256": sha256(path),
        "bytes": size,
        "duration_seconds": round(duration, 3),
    }
    return item


async def build(piper_uri: str) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    tts = WyomingPiperTts(TtsConfig(uri=piper_uri, voice=VOICE))
    await tts.start()
    spoken: dict[str, bytes] = {}
    for key, phrase in PHRASES.items():
        spoken[key] = await synthesize(tts, phrase)
        print(f"synthesized {key}: {len(spoken[key]) / (RATE * SAMPLE_WIDTH):.2f} s", flush=True)

    fixtures: list[dict[str, Any]] = []

    # Reuse the existing, Whisper-checked wake clip rather than a new take.
    wake_src = EXISTING_WAKE
    assert wake_src.is_file(), wake_src
    wake_link = FIXTURES_DIR / "wake_question.wav"
    if wake_link.exists() or wake_link.is_symlink():
        wake_link.unlink()
    # Store a real file so the fixture set is self-contained; bytes must match the original.
    wake_link.write_bytes(wake_src.read_bytes())
    assert sha256(wake_link) == sha256(wake_src)
    fixtures.append(
        entry(
            "wake_question",
            wake_link,
            text="Ryszardzie, jaka będzie jutro pogoda?",
            expected_wake=True,
            expected_remainder="jaka będzie jutro pogoda",
            expected_transcript_words=["ryszardzie", "jaka", "będzie", "jutro", "pogoda"],
            expected_vad_segments=1,
            vad_mode="exact",
            source=(
                "repo tests/data/wake_pl_16k.wav "
                "(Piper pl_PL-bass-high on piotr; see tests/data/README.md)"
            ),
            generator={
                "kind": "copied",
                "from": "tests/data/wake_pl_16k.wav",
                "command": "cp tests/data/wake_pl_16k.wav tests/data/fixtures/wake_question.wav",
                "seed": None,
            },
            notes=(
                "Phrase + command. The original clip is kept as "
                "tests/data/wake_pl_16k.wav; this file is a byte-identical copy."
            ),
        )
    )

    def write_spoken(fixture_id: str, key: str, **kwargs: Any) -> None:
        path = FIXTURES_DIR / f"{fixture_id}.wav"
        write_wav(path, spoken[key])
        fixtures.append(
            entry(
                fixture_id,
                path,
                text=PHRASES[key],
                source=(
                    "synthesized: throwaway Piper pl_PL-bass-high "
                    "(tools/generate_audio_fixtures.sh)"
                ),
                generator={
                    "kind": "piper",
                    "voice": VOICE,
                    "text": PHRASES[key],
                    "piper_uri_port": 10228,
                    "command": (
                        "tools/generate_audio_fixtures.sh "
                        "(image piotr-voice-hub-piper:t028, uri tcp://127.0.0.1:10228)"
                    ),
                    "seed": None,
                },
                **kwargs,
            )
        )

    write_spoken(
        "question_no_wake",
        "question_no_wake",
        expected_wake=False,
        expected_remainder=None,
        expected_transcript_words=["jaka", "będzie", "jutro", "pogoda"],
        expected_vad_segments=1,
        vad_mode="exact",
        notes="The same question as the wake clip, without the wake word.",
    )
    write_spoken(
        "near_miss",
        "near_miss",
        expected_wake=False,
        expected_remainder=None,
        expected_transcript_words=["rysiek", "włącz", "światło"],
        expected_vad_segments=1,
        vad_mode="exact",
        notes="A familiar diminutive, not the vocative 'Ryszardzie'.",
    )
    write_spoken(
        "inflected",
        "inflected",
        expected_wake=False,
        expected_remainder=None,
        expected_transcript_words=["ryszarda", "nie", "ma", "w", "domu"],
        expected_vad_segments=1,
        vad_mode="exact",
        notes="Genitive 'Ryszarda', not the vocative. Must not wake (design.md §4.1).",
    )
    write_spoken(
        "follow_up",
        "follow_up",
        expected_wake=False,
        expected_remainder=None,
        expected_transcript_words=["ustaw", "minutnik", "na", "pięć", "minut"],
        expected_vad_segments=1,
        vad_mode="exact",
        notes=(
            "A request without the wake word. The matcher must not accept it; the session "
            "follow-up window (already tested in test_session.py) would."
        ),
    )

    silence_path = FIXTURES_DIR / "silence.wav"
    write_wav(silence_path, silence(3.0))
    fixtures.append(
        entry(
            "silence",
            silence_path,
            text="",
            expected_wake=False,
            expected_remainder=None,
            expected_transcript_words=[],
            expected_vad_segments=0,
            vad_mode="exact",
            source="generated in-repo (zeros)",
            generator={
                "kind": "silence",
                "command": "tools/generate_audio_fixtures.py (3.0 s of zeros at 16 kHz)",
                "seed": None,
                "duration_seconds": 3.0,
            },
            notes="Quiet room. VAD must open no segment.",
            license="generated in-repo (zeros)",
        )
    )

    noise_path = FIXTURES_DIR / "noise_only.wav"
    write_wav(noise_path, noise(3.0, NOISE_SIGMA, SEED))
    fixtures.append(
        entry(
            "noise_only",
            noise_path,
            text="",
            expected_wake=False,
            expected_remainder=None,
            expected_transcript_words=[],
            expected_vad_segments=0,
            vad_mode="exact",
            source="generated in-repo (Gaussian noise)",
            generator={
                "kind": "noise",
                "command": "tools/generate_audio_fixtures.py (3.0 s Gaussian, sigma=400, seed=28)",
                "seed": SEED,
                "sigma": NOISE_SIGMA,
                "duration_seconds": 3.0,
            },
            notes="Noise only, similar level to tests/test_vad.py. VAD must open no segment.",
            license="generated in-repo (PRNG, seed 28)",
        )
    )

    after = FIXTURES_DIR / "speech_after_silence.wav"
    lead = 4.0
    write_wav(after, silence(lead) + read_wav(wake_link))
    fixtures.append(
        entry(
            "speech_after_silence",
            after,
            text="Ryszardzie, jaka będzie jutro pogoda?",
            expected_wake=True,
            expected_remainder="jaka będzie jutro pogoda",
            expected_transcript_words=["ryszardzie", "jaka", "będzie", "jutro", "pogoda"],
            expected_vad_segments=1,
            vad_mode="exact",
            min_speech_start_seconds=lead,
            source="4.0 s of zeros plus the wake_question clip",
            generator={
                "kind": "concat",
                "command": "silence(4.0) + tests/data/fixtures/wake_question.wav",
                "seed": None,
                "lead_silence_seconds": lead,
            },
            notes=(
                "A wake after a long quiet stretch. VAD start must be at or after "
                "4 s (plus the 250 ms start run)."
            ),
            license=VOICE_LICENSE + "; leading silence generated in-repo",
        )
    )

    two_path = FIXTURES_DIR / "two_speakers.wav"
    speaker_b = pitch_shift(spoken["two_speakers_b"], 1.18)
    # Start B 0.35 s after A so the phrases overlap, like two people talking.
    two_pcm = mix_at(
        [
            (spoken["two_speakers_a"], 0, 0.85),
            (speaker_b, int(0.35 * RATE), 0.85),
        ]
    )
    write_wav(two_path, two_pcm)
    fixtures.append(
        entry(
            "two_speakers",
            two_path,
            text="Która jest godzina? / Włącz radio w kuchni",
            expected_wake=False,
            expected_remainder=None,
            expected_transcript_words=None,
            expected_vad_segments=1,
            vad_mode="at_least",
            soft_transcript=True,
            source="synthesized two Piper phrases, one pitch-shifted, mixed",
            generator={
                "kind": "mix",
                "voice": VOICE,
                "texts": [PHRASES["two_speakers_a"], PHRASES["two_speakers_b"]],
                "pitch_shift_ratio_b": 1.18,
                "offset_seconds_b": 0.35,
                "command": "tools/generate_audio_fixtures.sh (two_speakers mix)",
                "seed": None,
            },
            notes=(
                "Two overlapping non-wake phrases. VAD may open one or two segments. "
                "The wake matcher must not accept (hard). The transcript may be messy (soft)."
            ),
            license=(
                "Piper pl_PL-bass-high (Apache-2.0); second speaker is a pitch-shifted overlay"
            ),
        )
    )

    tv_path = FIXTURES_DIR / "tv_music.wav"
    bed = tones(6.0, (220.0, 277.2, 329.6, 440.0), amp=0.08)
    tv_pcm = mix_at(
        [
            (bed, 0, 1.0),
            (noise(6.0, 180, SEED + 1), 0, 0.5),
            (spoken["tv_a"], int(0.3 * RATE), 0.55),
            (pitch_shift(spoken["tv_b"], 0.92), int(1.6 * RATE), 0.5),
            (pitch_shift(spoken["tv_c"], 1.12), int(3.1 * RATE), 0.5),
        ],
        length=int(6.0 * RATE),
    )
    write_wav(tv_path, tv_pcm)
    fixtures.append(
        entry(
            "tv_music",
            tv_path,
            text="",
            expected_wake=False,
            expected_remainder=None,
            expected_transcript_words=None,
            expected_vad_segments=0,
            vad_mode="any",
            soft_transcript=True,
            source=(
                "synthesized stand-in for TV/music: tones, noise, layered non-wake Polish phrases"
            ),
            generator={
                "kind": "mix",
                "voice": VOICE,
                "texts": [PHRASES["tv_a"], PHRASES["tv_b"], PHRASES["tv_c"]],
                "tone_hz": [220.0, 277.2, 329.6, 440.0],
                "noise_sigma": 180,
                "seed": SEED + 1,
                "command": "tools/generate_audio_fixtures.sh (tv_music mix)",
            },
            notes=(
                "Stand-in for TV/music until a consented device capture exists. VAD may open a "
                "segment (speech-like layers). Accepting a wake is a hard fail. Transcript is soft."
            ),
            license=(
                "generated in-repo: Piper phrases (Apache-2.0 pl_PL-bass-high) layered with tones"
            ),
        )
    )

    manifest = {
        "format": {
            "sample_rate": RATE,
            "sample_width": SAMPLE_WIDTH,
            "channels": CHANNELS,
            "encoding": "pcm_s16le",
        },
        "wake_phrase": "Ryszardzie",
        "seed": SEED,
        "runner_end_pad_seconds": 1.2,
        "notes": (
            "Replayable synthesized fixtures for VAD, STT and the wake matcher (T-028, "
            "design.md §10). No household recording. One acoustic set at the 16 kHz mono "
            "PCM the satellites send; per-device (Voice PE vs BOX-3) recapture needs "
            "Maciej's approval of the phrases and the output path first. "
            "Piper-derived speech is not bit-reproducible on regeneration; silence, "
            "seeded noise, the wake_pl_16k.wav copy and concatenations of those are. "
            "See tests/data/README.md for the pl_PL-bass-high lineage (test use only)."
        ),
        "fixtures": fixtures,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    total = sum(item["bytes"] for item in fixtures)
    print(f"wrote {len(fixtures)} fixtures, {total} bytes, manifest {MANIFEST_PATH}")


def check_piper_uri(uri: str) -> tuple[str, int]:
    """A loopback Wyoming URI, not the production piper on 10201."""
    host, port = parse_tcp_uri(uri)
    if host.casefold() not in LOOPBACK_HOSTS:
        raise ValueError(f"{uri!r}: piper must listen on localhost, 127.0.0.1 or ::1, not {host}")
    if port == PRODUCTION_PIPER_PORT:
        raise ValueError(f"{uri!r}: port {PRODUCTION_PIPER_PORT} is the production piper")
    return host, port


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--piper-uri",
        required=True,
        help=(
            "Wyoming URI of a throwaway Piper (e.g. tcp://127.0.0.1:10228). "
            "Loopback only; not port 10201."
        ),
    )
    args = parser.parse_args(argv)
    try:
        check_piper_uri(args.piper_uri)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    asyncio.run(build(args.piper_uri))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
