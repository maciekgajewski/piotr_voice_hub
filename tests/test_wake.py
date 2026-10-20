import unicodedata

import pytest

from piotr_voice_hub.hub.wake import (
    WakeMatch,
    is_wake_word_alone,
    match_wake_phrase,
    normalize_word,
    request_text,
)


def test_vocative_matches() -> None:
    assert match_wake_phrase("Ryszardzie, włącz światło") == WakeMatch(
        "Ryszardzie", "włącz światło"
    )


def test_remainder_keeps_original_casing_and_diacritics() -> None:
    result = match_wake_phrase("Ryszardzie, jaka będzie jutro pogoda?")
    assert result == WakeMatch("Ryszardzie", "jaka będzie jutro pogoda")


def test_phrase_case_is_ignored() -> None:
    assert match_wake_phrase("RYSZARDZIE zgaś Światło") == WakeMatch("RYSZARDZIE", "zgaś Światło")


@pytest.mark.parametrize("phrase", ["Ryszardzię", "Ryszárdzie", "RYSZARDZIĘ"])
def test_phrase_diacritic_artifacts_are_ignored(phrase: str) -> None:
    assert match_wake_phrase(f"{phrase}, stop") == WakeMatch(phrase, "stop")


def test_inner_punctuation_is_kept() -> None:
    result = match_wake_phrase("Ryszardzie: ustaw minutnik na 5 minut, proszę.")
    assert result == WakeMatch("Ryszardzie", "ustaw minutnik na 5 minut, proszę")


def test_symbols_at_the_end_are_kept() -> None:
    result = match_wake_phrase("Ryszardzie, ustaw jasność na 50%.")
    assert result == WakeMatch("Ryszardzie", "ustaw jasność na 50%")


def test_nfd_input_is_normalized_first() -> None:
    text = unicodedata.normalize("NFD", "ą ę Ryszardzie, włącz światło")
    assert match_wake_phrase(text) == WakeMatch("Ryszardzie", "włącz światło")


@pytest.mark.parametrize(
    ("text", "remainder"),
    [
        ("Hej, Ryszardzie, która godzina?", "która godzina"),
        ("No dobra Ryszardzie włącz radio", "włącz radio"),
    ],
)
def test_phrase_after_filler_words(text: str, remainder: str) -> None:
    result = match_wake_phrase(text)
    assert result is not None
    assert result.remainder == remainder


@pytest.mark.parametrize(
    "text",
    [
        "Powiedz mi proszę Ryszardzie, która godzina",  # fourth word
        "Wczoraj rozmawiałem z panem Ryszardzie",
    ],
)
def test_phrase_late_in_sentence_is_rejected(text: str) -> None:
    assert match_wake_phrase(text) is None


@pytest.mark.parametrize("text", ["Ryszardzie.", "Ryszardzie!", "  Ryszardzie?! ", "Ryszardzie..."])
def test_phrase_alone_gives_empty_remainder(text: str) -> None:
    assert match_wake_phrase(text) == WakeMatch("Ryszardzie", "")


@pytest.mark.parametrize(
    ("text", "remainder"),
    [
        ("Ryszardzie Ryszardzie", ""),
        ("Ryszardzie... Ryszardzie!", ""),
        ("Ryszardzie, Ryszardzie, włącz światło", "włącz światło"),
        ("Hej Ryszardzie, ryszardzie, RYSZARDZIE stop", "stop"),
    ],
)
def test_repeated_wake_word_is_skipped(text: str, remainder: str) -> None:
    result = match_wake_phrase(text)
    assert result is not None
    assert result.remainder == remainder


def test_wake_word_later_in_command_is_kept() -> None:
    result = match_wake_phrase("Ryszardzie, powiedz Ryszardzie")
    assert result == WakeMatch("Ryszardzie", "powiedz Ryszardzie")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "...",
        "Ryszard Kalisz powiedział w wywiadzie",
        "Ryszarda nie ma w domu",
        "Z Ryszardem rozmawiałem",
        "Ryszardowi dziękuję",
        "Ryszardu, włącz światło",
        "Ryszardze, włącz światło",
        "Rysiu, włącz światło",
        "Rysiek włącz światło",
        "Ryszardowski przyjechał",
        "Richard, turn on the light",
        "Jaka będzie jutro pogoda?",
    ],
)
def test_no_match(text: str) -> None:
    assert match_wake_phrase(text) is None


def test_partial_transcripts_match_as_they_grow() -> None:
    assert match_wake_phrase("Ry") is None
    assert match_wake_phrase("Ryszard") is None
    assert match_wake_phrase("Ryszardzie") == WakeMatch("Ryszardzie", "")
    assert match_wake_phrase("Ryszardzie, włą") == WakeMatch("Ryszardzie", "włą")


def test_hyphen_and_quotes_around_phrase() -> None:
    assert match_wake_phrase('"Ryszardzie" - włącz radio') == WakeMatch("Ryszardzie", "włącz radio")
    assert match_wake_phrase("„Ryszardzie”, włącz radio") == WakeMatch("Ryszardzie", "włącz radio")


@pytest.mark.parametrize(
    ("word", "normalized"),
    [("Ryszardzie", "ryszardzie"), ("Łódź", "lodz"), ("ŻÓŁĆ", "zolc"), ("gęślą", "gesla")],
)
def test_normalize_word_folds_polish_letters(word: str, normalized: str) -> None:
    assert normalize_word(word) == normalized


@pytest.mark.parametrize(
    "text", ["Ryszardzie ♪", "Ryszardzie *", "Ryszardzie …", "Ryszardzie ♪ ♪ ♪", "Ryszardzie, ¿ #"]
)
def test_remainder_without_letters_or_digits_is_empty(text: str) -> None:
    assert match_wake_phrase(text) == WakeMatch("Ryszardzie", "")


def test_remainder_with_symbols_and_a_word_is_kept() -> None:
    assert match_wake_phrase("Ryszardzie ♪ włącz radio") == WakeMatch("Ryszardzie", "♪ włącz radio")


@pytest.mark.parametrize(
    "text",
    [
        "Ryszardzie,\u00a0włącz radio\u00a0",
        "Ryszardzie\u2003, \u00a0włącz radio.\u3000",
        "Ryszardzie\twłącz radio\n",
    ],
)
def test_unicode_whitespace_is_stripped(text: str) -> None:
    assert match_wake_phrase(text) == WakeMatch("Ryszardzie", "włącz radio")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Która godzina?", "Która godzina"),
        ("Ryszardzie, która godzina?", "która godzina"),  # a leading wake word is dropped
        ("Ryszardzie Ryszardzie, światło", "światło"),
        ("Powiedz, Ryszardzie, która godzina", "Powiedz, Ryszardzie, która godzina"),
        ("Ryszardzie?", ""),
        (" — włącz światło. ", "włącz światło"),
        ("ustaw jasność na 50%", "ustaw jasność na 50%"),
        ("", ""),
        ("♪", ""),
        ("...", ""),
    ],
)
def test_request_text_after_the_wake_word_alone(text: str, expected: str) -> None:
    assert request_text(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Ryszardzie", "Ryszardzie.", " ryszardzie?! ", "Ryszardzie, Ryszardzie!", "Ryszardzie ♪"],
)
def test_the_wake_word_alone(text: str) -> None:
    assert is_wake_word_alone(text)


@pytest.mark.parametrize(
    "text",
    [
        "Dziękuję, Ryszardzie.",  # a sentence ending in the vocative (PR #24 review B1)
        "Dobranoc, Ryszardzie!",
        "No, Ryszardzie",
        "Ryszardzie, światło",
        "Ryszard",
        "",
        "...",
    ],
)
def test_not_the_wake_word_alone(text: str) -> None:
    assert not is_wake_word_alone(text)
