"""CPU tests for T-028 recorded-audio fixtures: format, VAD/segmenter, wake matcher."""

from __future__ import annotations

import hashlib

import pytest
from recorded_fixtures import (
    RecordedFixture,
    load_fixtures,
    load_manifest,
    segment_clip,
    vad_start_seconds,
)

from piotr_voice_hub.hub.wake import match_wake_phrase

FIXTURES = load_fixtures()
MANIFEST = load_manifest()


def test_manifest_format_is_16k_mono_s16() -> None:
    fmt = MANIFEST["format"]
    assert fmt == {
        "sample_rate": 16000,
        "sample_width": 2,
        "channels": 1,
        "encoding": "pcm_s16le",
    }
    assert MANIFEST["wake_phrase"] == "Ryszardzie"
    assert MANIFEST["runner_end_pad_seconds"] == 1.2


def test_fixture_set_covers_the_required_cases() -> None:
    ids = {item.id for item in FIXTURES}
    assert ids >= {
        "wake_question",
        "question_no_wake",
        "near_miss",
        "inflected",
        "silence",
        "noise_only",
        "speech_after_silence",
        "follow_up",
        "tv_music",
        "two_speakers",
    }


def test_fixtures_are_small() -> None:
    total = sum(item.bytes for item in FIXTURES)
    assert total <= 3_000_000  # a few MB at most


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item.id)
def test_file_matches_manifest(fixture: RecordedFixture) -> None:
    assert fixture.path.is_file(), fixture.path
    data = fixture.path.read_bytes()
    assert fixture.path.stat().st_size == fixture.bytes
    assert hashlib.sha256(data).hexdigest() == fixture.sha256
    # Touch the WAV header so a non-audio file with the same length cannot hide.
    fixture.pcm()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item.id)
def test_vad_segments_match_manifest(fixture: RecordedFixture) -> None:
    segments = segment_clip(fixture.pcm())
    if fixture.vad_mode == "exact":
        assert len(segments) == fixture.expected_vad_segments, (
            fixture.id,
            [seg.reason for seg in segments],
        )
    elif fixture.vad_mode == "at_least":
        assert fixture.expected_vad_segments is not None
        assert len(segments) >= fixture.expected_vad_segments, fixture.id
    elif fixture.vad_mode == "any":
        pass
    else:
        raise AssertionError(f"unknown vad_mode {fixture.vad_mode}")

    if fixture.min_speech_start_seconds is not None:
        start = vad_start_seconds(fixture.pcm())
        assert start is not None, fixture.id
        # Start hysteresis is 250 ms after onset, so allow a short window after the lead.
        earliest = fixture.min_speech_start_seconds
        latest = earliest + 0.8
        assert earliest <= start <= latest, (fixture.id, start)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item.id)
def test_matcher_on_expected_transcript(fixture: RecordedFixture) -> None:
    """The matcher contract, given a transcript that matches the clip's intended text."""
    result = match_wake_phrase(fixture.text)
    if fixture.expected_wake:
        assert result is not None, fixture.id
        assert result.remainder == fixture.expected_remainder
    else:
        assert result is None, (fixture.id, result)
