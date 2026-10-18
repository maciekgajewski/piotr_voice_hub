# T-009: wake-phrase matcher

## Goal

The pure wake-phrase matcher from design.md §4.1 step 4, used by the session (T-010) on both
partial and final transcripts.

## Scope

- `src/piotr_voice_hub/hub/wake.py` (design.md §5 `hub/`): `match_wake_phrase(text)` returns
  `WakeMatch(phrase, remainder)` or `None`.
  - Words are `\w+` runs. Each word is normalized (lowercase, NFKD diacritics stripped, plus explicit
    folds for letters NFKD keeps whole, notably Polish `ł`).
  - Match the design stem `ryszard\w{0,3}` on a whole normalized word, within the first three words.
    Per word, this equals the design's `\bryszard\w{0,3}\b` on the normalized text.
  - The remainder is the original text after the matched word (casing and diacritics kept), with
    leading and trailing punctuation and whitespace stripped. Inner punctuation is kept.
    It may be empty; the session decides (a final needs a non-empty remainder; a partial may
    emit `wake` with an empty one).
- Tests for inflections, punctuation, filler words, late phrase, empty remainder, Whisper oddities,
  growing partials, stem edge cases.

## Out of scope

- Session behaviour (wake/wake_cancelled, visuals), STT hotwords (T-008), config changes.

## Acceptance

- `make check` passes; tests cover all cases above.
