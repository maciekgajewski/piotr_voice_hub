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
- After that fix, `make check` again: 979 passed, 8 skipped. PR #34 opened against `main`.
  Not merged. Not deployed. Production remains `cd8c36a`.
- Review of `bb5c02d` held for a fixed head. Checked in code, not taken as established
  cause: `ARM` was not tied to a playback id and was written before connect; `close()`
  left the pair; a disabled restart did not delete it; `/home/maciek/probe/stt-oneshot`
  is not a compose mount (missing parent would `CaptureError` and restart-loop);
  `tools/speak_test.py` answers every `transcript_final`; an empty cleaned follow-up
  writes a pair and emits no `transcript_final`.
- Fix on this branch: `ARM` is the study satellite, a newline, the playback id, and a newline.
  Only that follow-up final is stored. The capture client connects first and arms only after `PLAY` plus
  `playback_finished` with `listening_for_follow_up`. It `release`s study transcripts and
  requires the file's utterance and `cleaned_text` to match; empty `cleaned_text` is
  success without a transcript. `stt_oneshot_play` sends one `play` and ignores
  transcripts. Shutdown and the next start (section present, enabled or not) delete
  pairs and partials, including before 1800 s. No host timer: a crash stays on disk
  until that start or until the operator deletes the directory. Container path is
  `/stt-oneshot`. Runbook: `docs/tasks/T-023-stt-oneshot-capture/runbook.md`.
- `make check` on this head: 983 passed, 8 skipped. Not merged. Not deployed.
