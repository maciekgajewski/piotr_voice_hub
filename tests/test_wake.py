import pytest

from piotr_voice_hub.hub.wake import WakeMatch, match_wake_phrase, normalize_word


@pytest.mark.parametrize("word", ["Ryszardzie", "Ryszardze", "Ryszard", "Ryszarda", "Ryszardu"])
def test_inflections_match(word: str) -> None:
    assert match_wake_phrase(f"{word}, włącz światło") == WakeMatch(word, "włącz światło")


def test_remainder_keeps_original_casing_and_diacritics() -> None:
    result = match_wake_phrase("Ryszardzie, jaka będzie jutro pogoda?")
    assert result == WakeMatch("Ryszardzie", "jaka będzie jutro pogoda")


def test_case_and_diacritics_insensitive_phrase() -> None:
    assert match_wake_phrase("RYSZARDZIE zgaś Światło") == WakeMatch("RYSZARDZIE", "zgaś Światło")


def test_inner_punctuation_is_kept() -> None:
    result = match_wake_phrase("Ryszardzie: ustaw minutnik na 5 minut, proszę.")
    assert result == WakeMatch("Ryszardzie", "ustaw minutnik na 5 minut, proszę")


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
        "Wczoraj rozmawiałem z Ryszardem o pogodzie",
    ],
)
def test_phrase_late_in_sentence_is_rejected(text: str) -> None:
    assert match_wake_phrase(text) is None


@pytest.mark.parametrize("text", ["Ryszardzie.", "Ryszardzie!", "  Ryszardzie?! ", "Ryszardzie..."])
def test_phrase_alone_gives_empty_remainder(text: str) -> None:
    assert match_wake_phrase(text) == WakeMatch("Ryszardzie", "")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "...",
        "Rysiu, włącz światło",
        "Rysiek włącz światło",
        "Ryszardowski przyjechał",  # stem + 5 letters: the \w{0,3} limit rejects it
        "Ryszardowskiego nie ma",
        "Richard, turn on the light",
        "Jaka będzie jutro pogoda?",
    ],
)
def test_no_match(text: str) -> None:
    assert match_wake_phrase(text) is None


def test_documented_stem_edges() -> None:
    # \w{0,3} after the stem: these are accepted by the design's pattern.
    assert match_wake_phrase("Ryszardowi dziękuję") == WakeMatch("Ryszardowi", "dziękuję")
    assert match_wake_phrase("Ryszardem zostaw") == WakeMatch("Ryszardem", "zostaw")


def test_partial_transcripts_match_as_they_grow() -> None:
    assert match_wake_phrase("Ry") is None
    assert match_wake_phrase("Ryszar") is None
    assert match_wake_phrase("Ryszardzie") == WakeMatch("Ryszardzie", "")
    assert match_wake_phrase("Ryszardzie, włą") == WakeMatch("Ryszardzie", "włą")


def test_first_matching_word_wins() -> None:
    assert match_wake_phrase("Ryszard, Ryszardzie, stop") == WakeMatch(
        "Ryszard", "Ryszardzie, stop"
    )


def test_hyphen_and_quotes_around_phrase() -> None:
    assert match_wake_phrase('"Ryszardzie" - włącz radio') == WakeMatch("Ryszardzie", "włącz radio")


@pytest.mark.parametrize(
    ("word", "normalized"),
    [("Ryszardzie", "ryszardzie"), ("Łódź", "lodz"), ("ŻÓŁĆ", "zolc"), ("gęślą", "gesla")],
)
def test_normalize_word_folds_polish_letters(word: str, normalized: str) -> None:
    assert normalize_word(word) == normalized
