"""Wake-phrase matcher (design.md §4.1 step 4). Pure functions, used on partial and final text.

The phrase is the fixed stem `ryszard` plus up to three letters (Ryszardzie, Ryszardze, Ryszard,
Ryszarda, Ryszardu, Ryszardowi...), matched on normalized words within the first three words.
The configured `wake_phrase` ("Ryszardzie") biases STT (hotwords); matching uses the stem.
"""

import re
import unicodedata
from dataclasses import dataclass

WAKE_STEM = re.compile(r"ryszard\w{0,3}")
MAX_WAKE_WORD_INDEX = 2  # the phrase must be one of the first three words
_WORD = re.compile(r"\w+")
_EDGE_NON_WORD = re.compile(r"^\W+|\W+$")
# Letters NFKD does not decompose into base letter + combining mark.
_EXTRA_FOLDS = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D"})


@dataclass(frozen=True)
class WakeMatch:
    phrase: str  # the matched word as transcribed, e.g. "Ryszardzie"
    remainder: str  # original text after the phrase, edge punctuation stripped; may be empty


def normalize_word(word: str) -> str:
    """Lowercase and strip diacritics (including Polish ł)."""
    decomposed = unicodedata.normalize("NFKD", word.translate(_EXTRA_FOLDS).lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def match_wake_phrase(text: str) -> WakeMatch | None:
    """Return the first wake word among the first three words and the text after it."""
    for index, word in enumerate(_WORD.finditer(text)):
        if index > MAX_WAKE_WORD_INDEX:
            return None
        if WAKE_STEM.fullmatch(normalize_word(word.group())):
            remainder = _EDGE_NON_WORD.sub("", text[word.end() :])
            return WakeMatch(phrase=word.group(), remainder=remainder)
    return None
