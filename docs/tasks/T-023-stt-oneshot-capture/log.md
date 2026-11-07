# T-023 log

## 2026-11-07

- Compared `cd8c36a` (production) with `main` (`b1bbb1a`). `stt/whisper.py`,
  `stt/transcript_preprocessor.py`, `hub/api.py`, `hub/events.py`, `hub/commands.py`, and
  `hub/session.py` are identical. Findings below apply to both.
- API exposure, from those files: a client can see cleaned `transcript_final`
  (`text`, `raw_text`, `follow_up`, `speaker`, `audio_ms`) and can send `release`. It cannot
  see the PCM buffer passed into the final `transcribe`, the Whisper segment objects, or the
  joined string from before `TranscriptPreprocessor`. `raw_text` is the cleaned text.
- Evidence files named in the incident (`~/probe/d-study-20261107.log` and the rest) are not
  in this workspace and were not read. The phrase and the timings stay an unproven report.
- Decision: do not add a public audio event. Add a default-off, one-shot file capture and a
  client that only arms that capture and sends the existing `release`. Design note is in
  `docs/design.md` for Maciej; nothing here is deployed.
- `make check`: 978 passed, 8 skipped (GPU tests). Independent review found one must-fix: a
  cancelled writer could publish after the next final cleared a shared abort flag. Each write
  now has its own abort flag that is never cleared; `_stop_write` and `close()` set it and
  wait, then delete. Re-review: clean.
