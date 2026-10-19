# T-014 log (append-only)

## 2026-10-19: start; plan written, stopped for design questions

- Branch `t-014-playback` from origin/main (48b5f70), in the worktree
  `~/src/piotr_voice_hub-t014`. The deployed hub, its main checkout and the satellites were not
  touched: Maciej is running his acceptance test.
- Read design.md (§4, §4.1 step 6, §6, §7, §8, §9), the T-014 entry and the carry-forwards, the
  T-007 driver, `messages.py`/`protocol.py`, the old driver's playback path and the old
  firmware YAML (all read-only). This repo has no firmware yet.
- URL vs streaming is not ambiguous in the design: there is one shared HTTP server, and the
  satellite fetches a URL. The old repo does the same, with one server per playback.
- Stopped after plan.md: Q1 (the `supports_response` cue needs a firmware change), Q2 (drain by
  timing or by media player state) and Q3 (is `play` with a consumer URL in T-014) each change
  the implementation. Recommended: Q1 (b) driver side with a fallback, firmware in P2; Q2 (a)
  timing as in the old repo; Q3 no, P1.

## 2026-10-19: decisions; implemented

- Maciej decided (per the project rules: simplicity, narrow scope, reliability, port what
  worked in the old project):
  - **Q1:** driver side only. The firmware `supports_response` change is a P2 carry-forward.
  - **Q2:** the stream-timing end estimate, as in the old repo. A P1 hardware-test
    carry-forward compares it with the media player's announcing → idle state.
  - **Q3:** `play` with a consumer URL goes to P1 (a README note).

  All three are recorded in plan.md and in the docs/tasks/README.md carry-forwards.
- Implemented as in plan.md: `hub/playback_http.py`, the playback and cue-response parts of
  `satellites/esphome.py`, and the server wiring in `run_hub`. The old driver's choices are
  kept: endless WAV header, volume first with a 0.1 s pause, 5 s fetch wait, 30 s send
  margin, 0.2 s grace, UDP-connect local IP. The differences:
  - one shared server with a path per playback, instead of a server per playback;
  - `PlaybackEnd` returns at once and a task emits `PlaybackFinished`;
  - a device that never fetches makes the driver unavailable, where the old driver only logged
    a warning;
  - at most 300 s of audio per stream.
- New `tests/test_playback.py`: the server, driver playback and cue responses. The fake
  ESPHome client's media player now GETs the announced URL over loopback. The T-007 test
  "playback fails loudly until T-014" is gone. `make check`: 486 passed, 1 skipped.
- Unit tests only. The deployed hub, its checkout and the satellites were not touched
  (Maciej's acceptance test). There is no way to trigger a playback on hardware before the P1
  `speak` command, so the hardware check is in P1.
- docs/tasks/README.md: the T-014 table row is left as is, to avoid a conflict with PR #15,
  which edits the line after it. Its status follows when both are merged.

## 2026-10-19: rebased onto main after PR #15

- PR #15 was merged as 68dab8b. `t-014-playback` was rebased onto it without conflicts, and
  the T-014 row in docs/tasks/README.md now says "in review (PR #16)".

## 2026-10-19: PR #16 review 5569102704

- **B1:** a broken stream no longer ends in `PlaybackFinished`. If the GET fails or stalls
  mid-stream (the API still up), the drain makes the driver unavailable, with no
  `PlaybackFinished`, like a stream that was never fetched; the session recovers.
  - A stalled reader is cut off by the server's per-write timeout (`SEND_TIMEOUT_SECONDS`,
    10 s). A device that stops reading fills the socket buffers, and the next write then
    blocks. A playing device reads at least at the audio rate.
  - Tests: a reader that drops mid-stream (abort after header + 100 bytes) and one that stalls
    mid-stream (30 MB at 48 kHz stereo, past the loopback buffers; write timeout 0.3 s). Both
    end unavailable, with no `PlaybackFinished`.
- **B2:** the P2 firmware carry-forward was wrong. ESPHome answers a service call only from
  an explicit `api.respond`, so `supports_response` alone would make every cue wait 8 s and
  drop the connection. Rewritten in docs/tasks/README.md and plan.md: status mode, with
  `api.respond: {success: true}` after `script.wait` (YAML in the README). The driver already
  calls status mode with `return_response=False`. The 8 s wait is now commented: the answer
  comes after the about 1 s cue, below the session's 10 s. The Q1 lines in the entries above
  (`supports_response: optional`) are superseded by this.
- **Nits:**
  1. A P1 carry-forward, plus comments in `playback_http.py` and `esphome.py`: nothing bounds
     `PlaybackBegin` → `PlaybackEnd`, and the writer waits for chunks without a bound of its
     own. The producer must bound it.
  2. The URL is IPv4: `local_ip_for` is AF_INET only, and an IPv4-mapped `connected_address`
     is unwrapped. A pure IPv6 connection is a WARNING at connect and unavailable at
     `PlaybackBegin`.
  3. The configured-address fallback is gone (it was a blocking DNS lookup in the UDP
     connect). A missing `connected_address` is a `RuntimeError` at connect.
  4. Formats: 16-bit PCM, 1–2 channels, 8000–48000 Hz from a fixed list. Anything else is a
     `ValueError` before anything is sent.
  5. A stream serves only its satellite's IP; others get 403 and the stream is still
     fetchable.
  6. At most 8 HTTP connections at once (`MAX_CONNECTIONS`); more are closed at once.
  7. An unexpected drain error is logged and re-raised from `wait_for_event()`, and the driver
     is unavailable after it.
  8. `PlaybackBegin` waits for a pending stray-stream stop, like `StartListening`.
  9. Tests: drop and stall mid-stream, a transport stop during the drain, plus the drain error,
     the stray-stop wait, 403, the connection cap, formats and the IPv4/IPv6/missing address
     cases.
  10. PR body refreshed.

  `_fail()` now releases the playback (cancels the drain), so a transport stop or any failure
  ends it without `PlaybackFinished`.
