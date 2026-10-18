"""Fixed transcript cleanup for partials and finals.

The replacement rules come from piotr_the_ai_server (hardware-tuned, unchanged). New in the hub:
bracketed Whisper notes such as "(muzyka)" or "[MUZYKA]" are dropped, and a transcript with no
letter or digit ("♪", "...") becomes "". Such text must never reach the wake matcher or the
follow-up window as a command.
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
        processed = _SPACES.sub(" ", processed).strip()
        if note_count:
            self._logger.debug("dropped bracketed transcript notes count=%s", note_count)
        if not any(c.isalnum() for c in processed):
            if processed:
                self._logger.debug("dropped symbol-only transcript text=%r", processed)
            return ""
        for rule in TRANSCRIPT_REPLACEMENTS:
            processed, replacement_count = rule.pattern.subn(rule.replacement, processed)
            if replacement_count:
                self._logger.debug(
                    "applied transcript replacement pattern=%r replacement=%r count=%s",
                    rule.pattern.pattern,
                    rule.replacement,
                    replacement_count,
                )
        return processed
