## 2026-11-22

- Maciej lifted the development pause. This task investigates the 24-bit JFK FLAC that
  voice-pe-03 failed to play during the 22 Nov hardware session (hub: `playback finished
  (url) after 0.9 s`, nothing heard). A 404 or refused port instead gives `reported no
  change within 5 s`.
- **Firmware evidence (read-only).** `docker logs piotr-voice-hub-hub-1` from the container
  created 16 Nov 2026 and last started 22 Nov 12:18 CET (Europe/Warsaw) has no
  `esphome_version`, `project_version`, `project_name` or `device_info` line. Connect logs
  only `connected to piotr-voice-pe-0{2,3}.local:6053` and `firmware lacks optional
  services ['play_follow_up_ready_cue']`. The driver (`satellites/esphome.py`) calls
  `connect(login=True)` and `list_entities_services()` but not aioesphomeapi
  `device_info()`. No API connection or playback was made to the satellites.
- **Decision.** The hub must not transcode or proxy media (design §4.1). On Voice PE ≤25.10
  the ESPHome `speaker` media player rejects non-16-bit FLAC
  ([audio_pipeline.cpp @ 2025.10.0](https://github.com/esphome/esphome/blob/9a29dec6d9c7ad8defc03caacb69e00314c86c16/esphome/components/speaker/media_player/audio_pipeline.cpp#L526-L530)).
  That matches the 0.9 s idle (header parsed, then stop). Firmware 26.5+ uses
  `speaker_source` + `audio_http` and should resample 24-bit; that path is unconfirmed
  here because the version was not logged. Narrowing §6 to WAV, MP3 and 16-bit FLAC is
  the reliability-preserving spec for raw URLs: it is what the device's own clips use,
  what ≤25.10 requires, and what failed vs worked on voice-pe-03. If a later connect log
  shows 26.5+, a remaining 24-bit failure needs device-side logs, and the limit can be
  revisited. Hub transcoding stays out of scope.
- T-025 was already taken (`t-025-docs-followup` worktree). This work is T-026 on branch
  `t-026-play-flac-16bit`. Production checkout `~/src/piotr_voice_hub` was not modified
  (still `c64ebcf`, detached).

- Local `make check` on this worktree: ruff and pyright clean; 992 tests passed, 8 skipped (GPU). No production or container changes.
