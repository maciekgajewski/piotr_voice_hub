# T-013: live wake-test runner

## Goal

Let Maciej test "Ryszardzie" live on piotr before the session (T-010) and main (T-012) exist:
one command connects to the configured satellites and prints, per satellite and timestamped,
what the hub would see: wake candidates from partials, the final transcript, whether it wakes,
the remainder, and latencies.

Throwaway by design: no session state machine, no agent, no playback, no API. It reuses the
real pieces (ESPHome driver, Silero VAD, faster-whisper STT, wake matcher) unchanged.

## Scope

- `src/piotr_voice_hub/wake_test.py`, script `piotr-voice-hub-wake-test`:
  - `--config PATH` (the hub config, design.md §7; `config/hub.example.yaml` format), optional
    `--satellite ID` (repeatable) to test a subset, `--log-level`.
  - Loads the shared Silero session and the STT model once (`stt.start()`), then runs one loop
    per satellite in a `TaskGroup`.
  - Per satellite (`run_satellite`): `SetVisualState(IDLE)`, `StartListening(new_id("listen"))`,
    wait for `ListeningStarted`, then read events forever:
    - `SpeechStarted`: new STT session (`max_segment_seconds` + 0.1 s), a task reading partials.
    - `AudioChunk`: `send_audio`.
    - first partial matching the wake word: print `wake partial`, `SetVisualState(LISTENING)`.
    - `SpeechEnded`: `transcribe_final()`, print the final, `WAKE`/`no wake`, the remainder and
      latencies, then return the visuals to idle (and `ResetWakeCandidate` if a partial woke
      but the final rejected it, design.md §4.1).
  - Latencies: segment end → final (`SpeechEnded` to `transcribe_final()` returning) and speech
    start → first wake partial (`SpeechStarted` to the first matching partial). `SpeechStarted`
    itself fires about 256 ms after speech onset (VAD start window), so the second number does
    not include that.
  - Commands to one satellite are serialized with a per-satellite lock (the partial reader and
    the event loop both send visuals).
  - `MicrophoneUnavailable`: print it, `close()` the driver, wait with backoff (1 s doubling to
    30 s, reset after a successful arm) and re-arm. The real backoff lives in T-010; this is the
    same rule, kept small.
  - Output goes to stdout as `HH:MM:SS.mmm [satellite] message`, local time. Transcripts are
    printed because this is an explicit operator diagnostic; logs stay metadata-only
    (MP-OBS-003).
  - Exit: Ctrl-C stops all loops and closes the drivers. STT fatal errors keep exit 70.
- `tools/wake-test.sh CONFIG [ARGS...]`: runs the script with `docker run` in
  `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04` (piotr has no host cuBLAS, so a host
  `uv run` does not work on the GPU), same caches as `tools/stt-gpu-test.sh`, `--network host`
  so mDNS `.local` names and the satellites on the LAN resolve, the config mounted read-only.
  A plain `docker run` script is used rather than a compose file: it is one command, has no
  long-running service, and matches the existing GPU test script.
- Not the T-012 main: T-012 wires the session state machine and API. The per-satellite loop
  here is small enough to copy or drop; making it the seed would need the T-010 interfaces.
- `docs/tasks/README.md`: status updates and the carry-forwards from the PR #9 review:
  - T-010: commands to one satellite are serialized by the session (one owner per driver).
  - driver follow-up: on disconnect, pending start/stop waits fail fast with the right reason.
  - driver follow-up: a half-open connection is cleaned up when the reader is cancelled
    mid-connect.
  - P2 soak: an audio-liveness check before the soak.

## Satellites (found 2026-10-18)

From the old firmware (`firmware/esphome/*.yaml` `substitutions.name`) and mDNS on piotr:

| id in hub.example.yaml | ESPHome name | address | state on 2026-10-18 |
| --- | --- | --- | --- |
| voice-pe-02 | piotr-voice-pe-02 | piotr-voice-pe-02.local (192.168.0.108) | online, API 6053 open |
| voice-pe-03 | piotr-voice-pe-03 | piotr-voice-pe-03.local (192.168.0.115) | online, API 6053 open |
| voice-pe-bedroom | piotr-voice-pe-bedroom-01 | piotr-voice-pe-bedroom-01.local | not on mDNS |
| box3-office | piotr-box3-01 | piotr-box3-01.local | not on mDNS |

`config/hub.example.yaml` used `voice-pe-02.local` / `voice-pe-03.local`; the real names have the
`piotr-` prefix. Fixed in this task (and in the design.md §7 example).

All four use API encryption (mDNS `api_encryption=Noise_NNpsk0_25519_ChaChaPoly_SHA256`) with a
per-device key `!secret <device>_api_key` from the gitignored `firmware/esphome/secrets.yaml`;
no API password. The old server took the same key per device in its config file
(`microphones.devices[].api_key`), mounted from `/home/maciek/ai_server_config.yaml`. Neither
file is readable by the agent account, so the live test needs Maciej to supply the keys.

## Tests

`tests/test_wake_test.py` with a fake microphone (scripted events, records commands) and a
fake STT (scripted partials and final), a fake clock and a captured printer:

- wake on a partial, then an accepted final: lines, latencies, remainder, visuals.
- partial wake rejected by the final: `ResetWakeCandidate` and idle.
- no wake at all: final printed as `no wake`, no visual change.
- `MicrophoneUnavailable` on arm and while reading: close, backoff 1, 2, then reset after a
  successful arm, new listen id each time.
- `max_length` segment end is reported.
- commands to one satellite never overlap.
- CLI: unknown `--satellite` id is a config error (exit 2).

## Manual test (piotr, Maciej)

See the PR checklist.
