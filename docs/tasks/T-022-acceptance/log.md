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

## 2026-10-31 (review 5719990609)

- S1: 6.4 plays `long.wav` (`speech.wav` 60 times, about 2 min, made on the laptop in 0.5 with
  the review's `wave` recipe), so the clip outlasts the 10–20 s restart; the expected result is
  the WARNING `the media player is PLAYING at connect (…); stopping it` and INFO `stopped the
  media player at connect` (checked in `_stop_stray_player`), plus the seconds to silence.
- S2: 2.4 runs per satellite: voice-pe-02 `--since S2 --until S3`, voice-pe-03 `--since S3
  --until E3` (E3 is new: the end of 2.2), `head -10` each.
- S3: 2.1 uses the 7.1 rules (accept: `ts` and `raw_text`; cancelled candidate: the `wake`'s
  `ts` and its last partial). N4's `wake` then `wake_cancelled` is not a false wake (the
  designed outcome for the phrase in the first three words, design §4.1; no cue, nothing to
  a consumer, no partial); an accept on N4 is.
- S4: tmux and screen are both on piotr (`command -v`); 7.1 and 7.2 run in a named tmux
  session that carries `$SHA`, with `tmux ls` and `tmux attach`.
- N1: 2.3 uses `--until E3`, and greps the startup `speaker hint off` WARNING over the whole log
  (it was before S2, so the old grep could not show it). N2: 6.1's WARNING needs profiles.
  N3: one `$SHA`, set and checked (`curl -fsSI`) at the top; the 21 commands use
  `${SHA:?…}`. N4: new 0.6 (no other API consumer), repeated before 3.3. N5: 0.1 also takes
  HUB_CACHE from the `/hf` mount. N6: 4.6's second request is also answered with the 20 s
  text (`--long` answers every request with `LONG_TEXT`, which ends "Koniec długiego testu.").

## 2026-10-31 (review 5720030107 nits, branch `t-022-nits`)

- PR #31 merged as fed8b07 (review 5720030107: MERGE). Follow-up branch `t-022-nits` from it.
- N1 (correction of the "review 5719990609" entry above, S3): "nothing to a consumer" was
  wrong. The `wake` and `wake_cancelled` events do go out to API clients; what is not sent is a
  `transcript_final`. Fixed in checklist 2.1 and plan interpretation 4.
- N2: the checklist runs over ssh without a TTY. `docker exec -i` (never `-t`); every
  listening tool runs under `timeout -s INT --preserve-status <s>` in the container, so it
  quits cleanly by itself (without a TTY, Ctrl-C would stop only the local client and leave the
  tool connected); a "stop line" sends the same SIGINT sooner. 4.2 and 6.4 are one command
  each (the first part in the background). 7.1 and 7.2 start detached tmux sessions
  (`tmux new -d -s … -e SHA="$SHA" '… | tee …'`) that end by themselves, with a
  `tmux has-session` check. Each non-interactive ssh call is a new shell, so it starts with the
  `$SHA` line. No step pages or prompts (no `git log`/`git diff`; `git pull --ff-only` makes no
  merge commit).
- N3: 6.4 records the timestamped `hub running`, `connected to`, `at connect` and
  `stopped the media player at connect` lines before calling a fail, so a slow start is told
  apart from a missing connect check.
