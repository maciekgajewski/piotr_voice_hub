# T-022: acceptance: log

## 2026-10-31

- Branch `t-022-acceptance` from `origin/main` cd8c36a (T-021 merged as PR #30). Production
  (`~/src/piotr_voice_hub`) stays on e60e03a; nothing is deployed by this task.
- Read design.md §4, §6, §9–§11 and the tasks README (T-022 row, the "P1 → T-022" wake miss
  rate note, the T-017 playback-end re-check, the P2 soak). Wrote plan.md (10
  interpretations) and checklist.md.
- No helper script: every count is one grep of the hub log (plan interpretation 6).
- PR #30 nits: N3, design.md and microphone-protocol.md now say the connect check is bounded
  at about 6 s (3 s for the first player state, then 3 s for idle after the STOP). N2,
  `test_close_during_a_stream_sends_no_stop`; dropping `playback.stream is None` from the
  STOP condition in `close()` fails it (applied and reverted from a /tmp backup).
- Tasks README: T-021 merged (PR #30), T-022 in review, its row narrowed to voice-pe-02 and
  voice-pe-03; T-021 plan status done.

## 2026-10-31 (thresholds decided)

- The manager settled the open thresholds question: no numeric threshold for the wake miss
  rate, false wakes or reply latency. They are recorded as the P2 baseline; Maciej's
  judgement at acceptance decides. plan.md (interpretation 3, 4, acceptance criteria) and
  checklist.md (pass/fail, 2.1, 2.4) now say so.
- Every false wake is logged with its time and transcript, though false wakes alone do not fail
  the run: 2.1 records the `ts` and `raw_text` of a negative line that woke; 7.1 and 7.2 save
  the tool's output with `tee` and grep its `wake`, `transcript_partial`, `transcript_final`
  and `wake_cancelled` lines (each with `ts` and `satellite`; the hub log has no transcript
  text), with one table row per false wake.
