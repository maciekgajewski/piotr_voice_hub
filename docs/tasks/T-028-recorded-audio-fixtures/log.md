# T-028 log

## 2026-11-22

- Numbered in the P2 breakdown. Starts after T-027's code PR, or in parallel if capacity
  allows. No household recording in this task unless Maciej consents to specific clips.

- Implementation started in parallel with T-027's review (manager go). Branch
  `t-028-recorded-audio-fixtures` from `origin/main` (3b46f75) in a worktree; production
  checkout left at c64ebcf. Fixtures are synthesized or copied from existing repo test data
  (`wake_pl_16k.wav`). No live recording, no satellite capture, no client to the production
  hub. Per-device recapture is written up as a question for Maciej in plan.md; not done.

- Fixtures generated on piotr with `tools/generate_audio_fixtures.sh`: throwaway
  `piotr-voice-hub-piper:t028` (re-tagged from `piotr-voice-hub-piper:rollback-4c96b16`)
  on `127.0.0.1:10228`. Production piper on 10201 was not used. The throwaway container
  and `:t028` tag were removed afterwards; no prune. Ten clips, 1,002,810 bytes.
- `make check` is green on the worktree (CPU fixture tests included; GPU tests skipped).
- GPU pass, `tools/fixtures-gpu-test.sh` (image `piotr-voice-hub-fixtures-test:t028`,
  `large-v3-turbo` / `cuda` / `float16` / `beam_size=5`): 1 passed in 62 s.
  Counts: expected accepts=2, rejects=8, false_accepts=0, false_rejects=0.
  Device VRAM: idle 4473 MiB, peak 6826 MiB (+2353 MiB during the test).
  Transcripts of note: near-miss came back as "Rysiek, włącz światłu." (matcher still
  rejected); two_speakers STT heard only speaker A; tv_music was messy and did not wake.
  The GPU word check for non-wake clips requires only the first expected word, because
  STT may inflect a later word.
