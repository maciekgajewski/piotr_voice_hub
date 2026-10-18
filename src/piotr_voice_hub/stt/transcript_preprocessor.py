"""Fixed transcript corrections, copied verbatim from piotr_the_ai_server (hardware-tuned)."""

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


class TranscriptPreprocessor:
    def __init__(self, instance_id: str) -> None:
        self._logger = logging.getLogger(f"{__name__}.TranscriptPreprocessor[{instance_id}]")

    def preprocess(self, text: str) -> str:
        processed = text
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
