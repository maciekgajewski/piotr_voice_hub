# T-009 log

## 2026-10-18

- Branch `t-009-wake-matcher` off `origin/main` @ `355c811` (T-006 merged).
- Placement: `hub/wake.py`, following design.md §5 ("hub/: session, wake, api, playback HTTP").
- Pattern and rule exactly as design.md §4.1 step 4: stem `\bryszard\w{0,3}\b` on normalized text,
  within the first three words. I apply it to each normalized word with `fullmatch`. That is the
  same match, and it gives the word's span in the original text for free.
- `wake_phrase` from config: the design hardcodes the stem, and §4.1 step 3 uses the configured
  "Ryszardzie" for STT `hotwords`/`initial_prompt`. So the matcher uses the fixed stem and does not
  derive a pattern from config (one wake phrase only, design.md §1; no generalization).
- Normalization: lowercase plus NFKD with combining marks removed. `ł`/`Ł` (and `ø`, `đ`) don't
  decompose under NFKD, so they are folded explicitly. Only the stem match uses normalized text.
- Remainder keeps the original casing and diacritics. The original text is tokenized, so the matched
  word's span maps straight back to it. Leading and trailing punctuation and whitespace are
  stripped, so "Ryszardzie, jaka będzie jutro pogoda?" gives "jaka będzie jutro pogoda", the
  design §6 example.
- Documented regex edges: `\w{0,3}` accepts Ryszardowi and Ryszardem (3 and 2 letters after the
  stem) and rejects Ryszardowski (5) and Ryszardowskiego. "Rysiu"/"Rysiek" don't match (not the
  stem). A filler word before the phrase is fine, since it is still within the first three words.
- `make check` passes. Status: pushed, PR to be opened.

## 2026-10-18: review fixes (PR #7, review 5564414056)

- Rebased onto `c3a8dd6` (T-005 merged).
- Blocker: a repeated wake word became the command ("Ryszardzie Ryszardzie" → "Ryszardzie",
  a false accept under the "non-empty final remainder" rule). The matcher now skips wake words
  that immediately follow the match. "Ryszardzie, Ryszardzie, włącz światło" → "włącz światło".
  A wake word later in the command is kept.
- **Decision (manager): match only the vocative "ryszardzie" after normalization.** No other
  case forms (Ryszard, Ryszarda, Ryszardem, Ryszardowi, Ryszardu, Ryszardze) and no bare
  "Ryszard". The stem accepted "Ryszard Kalisz powiedział..." or "Ryszarda nie ma w domu" from
  TV/radio as a wake plus a command. design.md §4.1 step 4 is updated. **Revisit at hardware
  acceptance with data**, if Whisper turns out to drop or mangle the vocative ending.
- Nits: NFC before splitting (NFD input split words at combining marks); the remainder's edges
  lose only whitespace, sentence punctuation, quotes and dashes, so "50%" keeps its "%"; the
  diacritics test now really tests diacritic artifacts ("Ryszardzię"), and the case test is
  named for case; the ø/đ folds are gone (ł/Ł kept). Naming nit skipped, as instructed.
