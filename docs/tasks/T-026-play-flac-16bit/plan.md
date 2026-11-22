# T-026: 16-bit FLAC for `play`, and firmware logged at connect

## Goal

`design.md` §6 promised WAV/FLAC/MP3 for `play`. On 2026-11-22 voice-pe-03 fetched the
pinned 24-bit OpenAI Whisper `jfk.flac` and went idle in 0.9 s with nothing heard. The hub
sends raw URLs and must not grow a media proxy. This task records the evidence, narrows the
spec to formats the satellites can reliably play, gives the hardware checklist a 16-bit 11 s
FLAC, and logs the satellite firmware at connect so later failures can be judged against the
right ESPHome path.

## Scope

- `docs/design.md` §6: `play` is WAV, MP3 or 16-bit FLAC, with evidence links and a note on
  Voice PE 26.5+. Flagged for Maciej's design review.
- Log `esphome_version`, `project_name` and `project_version` once at connect (aioesphomeapi
  `device_info()`, already available after login). A failed `device_info` is a WARNING; the
  connection still comes up. Do not log the MAC address.
- `tests/data/jfk_44100_stereo_s16.flac`: a 16-bit re-encode of the Whisper JFK clip, with
  the ffmpeg command, source URL and MIT licence in `tests/data/README.md`.
- Hardware checklist E1/E6 in this task use that clip. An optional later confirmation step
  compares the original 24-bit URL (fails fast) with the 16-bit clip and the Voice PE
  wake-word FLAC. Nothing is played in this task.
- Tests for the connect log, including a `device_info` failure that must not drop the
  connection.

## Out of scope

- Hub transcoding or a media proxy.
- Deploy, container, firewall or image changes.
- Connecting to a satellite or playing audio.
- P2 firmware work.

## Steps

1. Read hub logs for existing `device_info` (none). Confirm the driver already logs in but
   does not call `device_info()`.
2. Narrow §6 and add connect logging with tests.
3. Add the 16-bit clip and update the checklist.
4. `make check`, push, open a PR. Do not merge.

## Acceptance

- `make check` is green.
- The PR body flags the design.md §6 change for Maciej's design review and includes a
  manual checklist. No merge from this task.
