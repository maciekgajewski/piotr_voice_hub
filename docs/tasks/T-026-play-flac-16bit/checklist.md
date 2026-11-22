# T-026 hardware checklist (later live session; nothing runs in this PR)

Use public HTTPS URLs pinned to a commit (or the public radio stream). No laptop server,
no firewall change, no recording. After this PR is merged, set `SHA` to that merge commit
so `$CLIP16` resolves. The production hub is not redeployed for this logging change until
Maciej says so; the new `esphome_version` line appears only after a later hub start or
satellite reconnect.

## URLs

| Name | URL | Expect |
| --- | --- | --- |
| CLIP16 (11.0 s, 16-bit FLAC) | `https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$SHA/tests/data/jfk_44100_stereo_s16.flac` | 200, audio/flac, 488897 bytes, 11 s heard |
| CLIP24 (11.0 s, 24-bit FLAC, original) | `https://raw.githubusercontent.com/openai/whisper/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/tests/jfk.flac` | 200, audio/flac, 1152693 bytes; on this device, idle in ~1 s, nothing heard |
| CONTROL (0.95 s, 16-bit 48 kHz mono FLAC) | `https://raw.githubusercontent.com/esphome/home-assistant-voice-pe/c16a4d010f39b06e6b14ef9aea85e2f5f280e20a/sounds/wake_word_triggered.flac` | 200, short wake chime |
| SPEECH | `https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$SHA/tests/data/speech_pl_16k.wav` | 200, ~2.1 s WAV (the Polish "to jest test pierwszy" clip) |

## E1 and E6 (replace the old 24-bit CLIP11)

**E1, announcement play, then busy during it.** Play CLIP16 as an announcement on
voice-pe-03. Expect 11 s of the JFK clip, one `playback_finished`, then (while it plays)
a second `play` of SPEECH is `busy`. After CLIP16 ends, the satellite is idle and a wake
works.

**E6, https.** Same CLIP16 as E1 (already HTTPS). Expect as E1's first part: 11 s heard,
one `playback_finished`, `exit=0`.

Do not use the 24-bit Whisper `jfk.flac` as CLIP11 for E1/E6.

## Optional confirmation (one later session, after a reconnect has logged firmware)

Read the hub log at the latest connect for both satellites:

`esphome[voice-pe-0x]: connected to … esphome_version=… project=… …`

Then, announcements only, voice-pe-03, idle between each:

1. CONTROL: the short wake chime is heard (FLAC-over-HTTPS works).
2. CLIP16: 11 s heard, one `playback_finished`.
3. CLIP24: nothing heard; `playback finished (url) after ~0.9 s` (or similar, under ~2 s).

If the connect log shows Voice PE **26.5+** (or ESPHome 2026.5+) and CLIP24 still fails,
do not treat the 16-bit `speaker` pipeline check as the cause; collect device-side logs
instead. If it shows **25.10 or earlier**, step 3 confirms that check.

No hub restart, container change or deploy is required for the plays themselves. The
firmware line needs one connect after this change is deployed.
