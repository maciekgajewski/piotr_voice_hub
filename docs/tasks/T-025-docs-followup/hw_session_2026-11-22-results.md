# Hardware session 2026-11-22 results (voice-pe-03, study)

Production c64ebcf50cc9851d230d36e45933aaf3e8c042ce. Checklist: hw_session_2026-11-16.md.
Session 11:58–12:22 CET. Maciej at the mic; steps relayed by the manager. Capture OFF (no
/stt-oneshot mount), volume never touched, nothing recorded, no firewall change, no listener.
"Heard" = Maciej via the manager; the rest is from the hub log and the tool logs in ~/probe.

## 0. Before — PASS (11:58)
SHA c64ebcf, clean checkout. Containers unchanged since 16 Nov (hub 97a3a16b28b0, piper
ada7acfb7d96, speaker db88c1fd0937), all healthy, restarts=0. Both satellites connected and
idle, 0 clients, VRAM 4473 MiB. check_gpu_cdi.sh (with container args) passed. URLs: tool,
CLIP11, SPEECH 200; MISSING 404; RADIO 200 (http and https); REFUSED exit 7.

## A. T-020 reply session — log ~/probe/t020-reply-20261122.log
- A1 PASS (heard 12:02). 12:01:33 wake and question; window opened; "Tak, poproszę" accepted as
  a follow-up (follow_up=true, no wake word); reply, second window, follow_up_timeout after 8.1 s.
  (An earlier try at 12:01:05 just timed out with no answer, which is fine.)
- A2 PASS (heard 12:03). The window timed out after 8.2 s; "Która godzina" right after was handled
  normally (follow_up=false); "Halo, słyszysz mnie?" gave no event.
- A3 PASS (heard 12:08). 12:07:51.8 window; clap and cough produced no segment; follow_up_timeout
  after 8.2 s; no transcript.

## E. T-021 one-shots
- E1 FAIL as written (test data). The JFK FLAC (CLIP11) ended "after 0.9 s" (the device changed
  state, then went idle), so the satellite was idle and the second (Polish) play was accepted
  and played (2.6 s). Heard: only the Polish clip "to jest test pierwszy". The hub behaved as
  designed. OPEN FINDING F-1 below.
- E1b PASS (replaces E1): radio with stop after 15 s; the Polish clip at +4 s got busy (exit 1);
  stop acked, one playback_finished at 12:11:45.7. Heard: music uninterrupted, no Polish.
- E2 PASS (radio instead of CLIP11): stop after 3 s; "playback stopped"; one playback_finished at
  12:12:44.9. Heard: cut off after about 3 s.
- E3 PASS (404, 12:12:54): the 5 s path (WARNING no change); one playback_finished; exit 0; no reconnect.
- E4 PASS (refused port, 12:13:01): the same 5 s path; one playback_finished; exit 0; no reconnect.
- E5 DEFERRED: needs a pinned ~0.3 s clip (follow-up docs PR).
- E6 DEFERRED: no playable pinned 11 s HTTPS clip (CLIP11 does not play; see F-1).
- E7 PASS: radio 12:14:00.2, stop 12:14:21.0, one playback_finished. Heard: about 20 s, then stopped.

## F. T-021 hub restarts (16 Nov OK from Maciej)
- F1 PASS (restart). Radio 12:15:50.9; docker restart 12:16:00.8; shutdown close() stopped
  the radio at once ("close: stopped playback (url)" 12:16:00.834). STT ready 12:16:04.8
  (2.9 s, cuda); connected 12:16:06.2. Heard: music about 10 s, stopped at the restart.
  Post-restart wake: no client connected, so the timeout was expected. 12:16:21.4 wake accepted
  (speaker Maciek 0.66), accepted cue logged as played 12:16:21.885, blinking = processing
  (waiting for an API client's reply), "reply timeout after 30 s" 12:16:51.9, timeout cue,
  listening again 12:16:52.7. No errors. The accepted cue was not heard on this one wake (F-2, later closed: not reproduced; heard clearly at 12:20:52).
- F2 PASS. Radio 12:18:15.6; kill 12:18:20.5; docker start returned 12:18:21.2; hub running
  23.8; connected 24.3; WARNING "media player is PLAYING at connect … stopping it" 24.37;
  "stopped the media player at connect" 12:18:25.99, about 4.8 s after start. No close line
  (kill skipped shutdown, as intended). Heard: music at 12:18, then it stopped.
- Wake check after F2 (2-minute listener, ~/probe/t020-wake-20261122.log) PASS: 12:20:52 wake
  accepted, accepted cue logged 12:20:52.636, reply "Jest dwunasta." 1.3 s, idle. Heard: accepted
  sound clearly and the answer ("all fine").

## Z. After — PASS (12:22)
All three containers healthy (hub restarted twice by F1/F2, restarts=0, Whisper on cuda each
time). Both satellites connected and idle, 0 clients, no tmux sessions, VRAM 4471 MiB, 0
ERROR/Traceback.

## Deferred
E5, E6, the LAN variants of E1–E4, voice-pe-02.

## Open findings
- F-1: the Voice PE does not play the pinned whisper jfk.flac (1.15 MB, 11 s). It fails fast
  (state change, 0.9 s), unlike a 404 or refused URL (5 s, no change). Cause under
  investigation in its own task (likely 24-bit FLAC vs the firmware's 16-bit-only player).

## Closed findings
- F-2 (closed, not reproduced): the accepted cue was logged as played after the F1 restart
  (12:16:21.885) but was not heard that once. On the 12:20:52 wake check Maciej heard the
  accepted cue clearly and the answer. It was not reproduced.

## Process lesson
F2 was started before Maciej confirmed F1 and while the manager's HOLD was still on its way
(messages arrive late). From now on, the next step starts only after an explicit go
following the previous step's confirmation.
