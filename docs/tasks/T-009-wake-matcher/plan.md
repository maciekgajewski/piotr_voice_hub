# T-009: wake-phrase matcher

## Goal

The pure wake-phrase matcher from design.md §4.1 step 4, used by the session (T-010) on both
partial and final transcripts.

## Scope

- `src/piotr_voice_hub/hub/wake.py` (design.md §5 `hub/`): `match_wake_phrase(text)` returns
  `WakeMatch(phrase, remainder)` or `None`.
  - Text is NFC-normalized, then split into `\w+` words. Each word is normalized (lowercase,
    NFKD diacritics stripped, Polish `ł` folded explicitly).
  - The wake word is the vocative `ryszardzie` only (normalized, whole word), within the first
    three words. Other case forms and bare "Ryszard" don't match.
  - Repeated wake words right after the match are skipped.
  - The remainder is the original text after the wake word(s), with casing and diacritics kept,
    and only whitespace, sentence punctuation, quotes and dashes stripped from its edges (`%` and
    other symbols stay). It may be empty; the session decides (a final needs a non-empty
    remainder; a partial may emit `wake` with an empty one).
- Tests for the vocative, rejected case forms, punctuation, filler words, late phrase, repeats,
  empty remainder, Whisper oddities, growing partials, NFD input.

## Out of scope

- Session behaviour (wake/wake_cancelled, visuals), STT hotwords (T-008), config changes.

## Acceptance

- `make check` passes; tests cover all cases above.
