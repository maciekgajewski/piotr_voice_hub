"""Wake-phrase matcher (design.md §4.1 step 4). Pure functions, used on partial and final text.

The wake word is the vocative "Ryszardzie" only, compared after normalization (case and
diacritics ignored), and it must be one of the first three words. Other case forms
(Ryszard, Ryszarda, Ryszardem, ...) don't wake: they are what TV and radio say about a
Ryszard, while people address the hub in the vocative. Repeats right after the wake word
("Ryszardzie, Ryszardzie, ...") are skipped, so they never end up in the command.
The configured `wake_phrase` ("Ryszardzie") biases STT (hotwords); matching is fixed here.
"""

import re
import unicodedata
from dataclasses import dataclass

WAKE_WORD = "ryszardzie"  # normalized vocative
MAX_WAKE_WORD_INDEX = 2  # the wake word must be one of the first three words
_WORD = re.compile(r"\w+")
# Stripped from the remainder's edges, with any Unicode whitespace (str.strip): sentence
# punctuation, quotes, dashes. Symbols such as % stay ("ustaw jasność na 50%").
_EDGE_PUNCTUATION = ".,!?…;:-–—\"'„”“«»"


@dataclass(frozen=True)
class WakeMatch:
    phrase: str  # the matched word as transcribed, e.g. "Ryszardzie"
    remainder: str  # original text after the wake word(s), edges stripped; "" if no letter/digit


def normalize_word(word: str) -> str:
    """Lowercase and strip diacritics. NFKD does not decompose Polish ł, so fold it first."""
    folded = word.replace("ł", "l").replace("Ł", "L").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", folded) if not unicodedata.combining(c))


def match_wake_phrase(text: str) -> WakeMatch | None:
    """Return the wake word if it is among the first three words, and the text after it."""
    text = unicodedata.normalize("NFC", text)  # NFD input would split words at combining marks
    words = list(_WORD.finditer(text))
    for index, word in enumerate(words[: MAX_WAKE_WORD_INDEX + 1]):
        if normalize_word(word.group()) != WAKE_WORD:
            continue
        end = word.end()
        for repeat in words[index + 1 :]:
            if normalize_word(repeat.group()) != WAKE_WORD:
                break
            end = repeat.end()
        return WakeMatch(phrase=word.group(), remainder=_remainder(text[end:]))
    return None


def request_text(text: str) -> str:
    """The request in a segment that follows the wake word alone (T-017 fix): the whole text,
    edges stripped like a remainder; "" if it has no letter or digit."""
    return _remainder(unicodedata.normalize("NFC", text))


def _remainder(text: str) -> str:
    while (stripped := text.strip().strip(_EDGE_PUNCTUATION)) != text:
        text = stripped
    # No letter or digit ("♪", "*", "…") is not a command: Whisper emits ♪ for music.
    return text if any(c.isalnum() for c in text) else ""
