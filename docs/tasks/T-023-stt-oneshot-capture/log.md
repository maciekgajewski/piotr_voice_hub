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
- Sysadmin install of `at`/`atd` is blocked by a security gate. Nothing was installed.
  `at` remains absent. No host check and no retry from this task. Cleanup prerequisite
  is unsatisfied. No capture readiness.
- Replaced hub-owned retention (timer, startup sweep, shutdown delete, segment scores)
  with one host `at` job. The client reads `at -c` and the `atq` deadline and refuses
  `ARM` unless they match this directory. Teardown deletes the directory before `atrm`.
  A missing capture directory disarms the feature and leaves the hub up.
- `make check`: 985 passed, 8 skipped. `at` still absent. Not capture-ready. Not merged.
  Not deployed.
- Review of `afd73b6` (CHANGES NEEDED): the production compose bind made every
  `docker compose up` require the capture directory, and `rm -rf` of that directory
  removed the bind source so the next restart could not start. Carry-forwards, not
  done here: N2 (`for_playback` stays duck-typed), N3 (`close()` stays a log line).
  N6 is noted in the runbook (multi-segment `raw_text` can mismatch). N7 is a comment
  on the synchronous write. Duplicate `_check_ws` stays.
- Fix on this branch: the bind moved to `docker-compose.stt-oneshot.yml`, used only
  with an extra `-f`. Production `docker-compose.yml` has no capture volume. Cleanup
  and teardown are `find -P <directory> -mindepth 1 -delete`: contents only, so the
  bind source remains for a later start. `atrm` runs only after that command returns
  0. `_unlink` logs an `OSError` at ERROR and does not cancel the job. The play client
  refuses an existing `PLAY` or `ARM` before it connects. A session test opens the
  window with `play` of a URL and checks `for_playback` of that playback id. The
  runbook requires zero other answering clients and one absolute path for `$DIR`,
  `HUB_STT_CAPTURE`, and the `at` job.
- `make check`: 987 passed, 8 skipped. `at` still absent. Not capture-ready. The
  restart-after-cleanup behaviour is not verified on the host. Not merged. Not deployed.
