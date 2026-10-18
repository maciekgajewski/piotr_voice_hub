"""Fixed transcript cleanup.

The replacement rules are copied verbatim from piotr_the_ai_server (hardware-tuned). New in the
hub: bracketed Whisper notes such as "(muzyka)" or "[MUZYKA]" are dropped first, so they never
become a command after the wake word.
"""

import logging
import re
from dataclasses import dataclass
from re import Pattern


@dataclass(frozen=True)
class TranscriptReplacementRule:
    pattern: Pattern[str]
    replacement: str


TRANSCRIPT_REPLACEMENTS = (
    TranscriptReplacementRule(
        pattern=re.compile(r"\bventilacji\b"),
        replacement="wentylacji",
    ),
    TranscriptReplacementRule(
        pattern=re.compile(r"\bklimatysację\b"),
        replacement="klimatyzację",
    ),
    TranscriptReplacementRule(
        pattern=re.compile(r"\bwędylacji\b"),
        replacement="wentylacji",
    ),
    TranscriptReplacementRule(
        pattern=re.compile(r"\bprzekłaśnij\b"),
        replacement="przygłośnij",
    ),
    TranscriptReplacementRule(
        pattern=re.compile(r"\bpo kłodach\b"),
        replacement="pogoda",
    ),
)
# One non-nested (...) or [...] group, with the whitespace before it.
BRACKETED_NOTE = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])")
_SPACES = re.compile(r"\s{2,}")


class TranscriptPreprocessor:
    def __init__(self, instance_id: str) -> None:
        self._logger = logging.getLogger(f"{__name__}.TranscriptPreprocessor[{instance_id}]")

    def preprocess(self, text: str) -> str:
        processed, note_count = BRACKETED_NOTE.subn("", text)
        if note_count:
            processed = _SPACES.sub(" ", processed).strip()
            self._logger.info("dropped bracketed transcript notes count=%s", note_count)
        for rule in TRANSCRIPT_REPLACEMENTS:
            processed, replacement_count = rule.pattern.subn(rule.replacement, processed)
            if replacement_count:
                self._logger.info(
                    "applied transcript replacement pattern=%r replacement=%r count=%s",
                    rule.pattern.pattern,
                    rule.replacement,
                    replacement_count,
                )
        return processed
