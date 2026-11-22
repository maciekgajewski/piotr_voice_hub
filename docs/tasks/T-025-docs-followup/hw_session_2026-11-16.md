# Hardware session 2026-11-16: remaining T-020/T-021 acceptance on voice-pe-03 (study) only

Production runs **c64ebcf50cc9851d230d36e45933aaf3e8c042ce** (deployed 2026-11-16 11:56 CET:
4c96b164 + T-024 CDI). Everything below is pinned to it. Only voice-pe-03 (study) is used; all
voice-pe-02 parts stay **deferred** (as on Nov 1/Nov 7).

Already done on Nov 7 (`hw_session_2026-11-07-results.md`): **A4, A5, B, C passed**, not
repeated. **D** (play + listen_after) failed on Nov 7 and is replaced by the separate T-023
one-shot diagnostic capture, which is **not part of this file**.

| Part | What | About |
| --- | --- | --- |
| 0 | Before | 2 min |
| A | T-020 reply session: A1, A2, A3 (all required) | 6 min |
| E | T-021 one-shots: E1–E7 (E5 has a pinned clip from T-025; not run on 16/22 Nov) | 6 min |
| F | T-021 hub restarts: F1, F2 | 4 min |
| Z | After checks | 2 min |
|   | **Total** | **about 20 min** |

**No listener anywhere.** No laptop `http.server`, no LAN listener on piotr, no 0.0.0.0
server, no firewall change. Every URL is public HTTPS pinned to a commit (or the public radio
stream). Part L of Nov 1 is dropped. The satellite fetching raw.githubusercontent.com over
HTTPS is proven: Nov 7 C played `speech_pl_16k.wav` from there (driver INFO `(url) after 2.7 s`).

**Volume.** No step changes the media player volume (`speak_test.py` sends no `volume`; the hub
refuses `volume` on `play`). Nothing to read first or restore.

**Deferred LAN coverage** (needs a LAN HTTP server, not allowed): satellite fetch over plain
LAN HTTP (E1/E2), a LAN 404 (E3, now a GitHub 404 over HTTPS), a refused LAN port (E4, now a
refused public port). E5 has a pinned public clip from T-025 (see E5); it was not run on 16 or 22 Nov.

## URLs (checked from piotr with `curl -sI` on 2026-11-16 ~11:59 CET)

| Name | URL | Check |
| --- | --- | --- |
| tool | `https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/c64ebcf50cc9851d230d36e45933aaf3e8c042ce/tools/speak_test.py` | 200 text/plain 11188 |
| CLIP11 (11.0 s, flac) | `https://raw.githubusercontent.com/openai/whisper/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/tests/jfk.flac` | 200 audio/flac 1152693 |
| SPEECH (2.1 s, wav) | `https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/c64ebcf50cc9851d230d36e45933aaf3e8c042ce/tests/data/speech_pl_16k.wav` | 200 audio/wav 67290 |
| BEEP (0.3 s, wav) | `https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$BEEP_SHA/tests/data/beep_16k.wav` | added in T-025. `$BEEP_SHA` is the PR #36 merge commit (`beep_16k.wav` is not in c64ebcf); the literal value is filled in by the next PR. |
| MISSING (404) | `https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/c64ebcf50cc9851d230d36e45933aaf3e8c042ce/tests/data/missing.wav` | 404 text/plain 14 |
| REFUSED | `http://stream.radioparadise.com:8001/x.wav` | TCP refused (RST) in 24–106 ms, 3 of 3 |
| RADIO | `http://stream.radioparadise.com/mp3-128` | 200 audio/mpeg, icy-name Radio Paradise |
| RADIO_HTTPS (fallback) | `https://stream.radioparadise.com/mp3-128` | 200 audio/mpeg |

## How to run (manager, on piotr, no TTY)

Each `ssh piotr '…'` is a new shell: start each call with the setup line, and put the step in
braces: `<setup line> && { <step>; }`.

```
export SHA=c64ebcf50cc9851d230d36e45933aaf3e8c042ce CLIP11=https://raw.githubusercontent.com/openai/whisper/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/tests/jfk.flac RADIO=http://stream.radioparadise.com/mp3-128 && export SPEECH=https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$SHA/tests/data/speech_pl_16k.wav MISSING=https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$SHA/tests/data/missing.wav REFUSED=http://stream.radioparadise.com:8001/x.wav && export ST="https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$SHA/tools/speak_test.py" && curl -fsSI "$ST" >/dev/null && echo "SHA ok: $SHA"
```

- `docker exec` is always plain (no `-i`/`-t`) with `</dev/null`.
- Listening tools run under `timeout -s INT --preserve-status <s>` in the hub container; exit 0
  at the end of the window or on the stop line.
- Sessions are detached `tmux` sessions carrying `$SHA` (`-e`), output `2>&1 | tee ~/probe/…`.
- One-shots (`--announce`) need no timeout: they exit after `playback_finished`, or after 120 s.
- Tool commands use `${SHA:?…}` etc., so an unset variable stops them.
- E5 only: do not reuse `$SHA` (c64ebcf) for the beep, or the BEEP URL 404s.
  `BEEP_SHA` = the PR #36 merge commit (`beep_16k.wav` is not in c64ebcf); the literal
  value is filled in by the next PR. Then:
  `export BEEP=https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${BEEP_SHA:?set BEEP_SHA, see the top}/tests/data/beep_16k.wav`

**Stop line** (ends **every** `timeout` in the hub container; use only to end the current session):

```
docker exec piotr-voice-hub-hub-1 sh -c 'for p in /proc/[0-9]*; do case "$(tr "\0" " " < $p/cmdline 2>/dev/null)" in "timeout -s INT "*) kill -INT "${p#/proc/}";; esac; done' </dev/null
```

**Free check** (before A, before E, before F):

`{ tmux ls 2>/dev/null | grep -E '^t02[01]-' || echo "no session"; docker logs piotr-voice-hub-hub-1 2>&1 | grep -E "API .*(connected|disconnected)" | tail -n 1; }`

Expect `no session` and no API line or one ending `(0 clients)`. If not: stop line, wait until
`tmux has-session -t <name>` fails, retry.

**Play grep** after each T-021 case (`--since 2m`; longer gap: use the step's printed time):

`docker logs -t --since 2m piotr-voice-hub-hub-1 2>&1 | grep -E "consumer url|playback finished|reported no change|playback stopped|at connect|of connecting|close: stopped"`

## 0. Before (2 min)

Confirm with the manager that nobody else is testing production now (in particular no
Sysadmin `daemon-reload` / T-024 check, and no T-023 capture armed: no `/stt-oneshot` mount).

`{ git -C ~/src/piotr_voice_hub rev-parse HEAD; docker inspect -f '{{.Name}} {{.State.Health.Status}} restarts={{.RestartCount}}' piotr-voice-hub-hub-1 piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1; docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' piotr-voice-hub-hub-1 | grep -c stt-oneshot; curl -s --max-time 3 http://127.0.0.1:8750/healthz; echo; ss -Htan state established '( sport = :8750 )' | wc -l; date -u +%FT%TZ; }`

Expect: `c64ebcf50cc9…`; all three `healthy restarts=0`; `0` (no stt-oneshot mount); healthz
with voice-pe-02 and voice-pe-03 `"connected":true,"state":"idle"`; `0` clients. Note the time
as **T0**.

URL re-check (seconds; external hosts can change):

`{ for u in "$CLIP11" "$SPEECH" "$MISSING" "$RADIO"; do printf '%s ' "$u"; curl -sI --max-time 10 "$u" | head -n 1; done; curl -sS -o /dev/null --connect-timeout 5 "$REFUSED"; echo "refused-check exit=$? (expect 7)"; }`

Expect 200, 200, 404, 200, then `exit=7` (refused). If REFUSED gives `28` (timeout), E4 becomes
a timeout case: run it anyway and record it as such. If RADIO fails, use
`RADIO=https://stream.radioparadise.com/mp3-128`; if both fail, skip E7 and F (**deferred**).

Then run the free check.

## A. T-020 reply session: A1, A2, A3 (6 min) — all required

Wake phrase "Ryszardzie". No ready cue on this firmware (expected `play_follow_up_ready_cue`
WARNING): a window is the **LISTENING light with no sound**, 8 s long.

Start (ends by itself after 15 min):

`{ mkdir -p ~/probe; date -u +%FT%TZ; tmux new -d -s t020-reply -e SHA="$SHA" 'docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 900 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --listen-after --text "Wziąć parasol?" </dev/null 2>&1 | tee ~/probe/t020-reply-20261116.log'; }`

Check: `{ tmux has-session -t t020-reply && echo running; tail -n 5 ~/probe/t020-reply-20261116.log; }`

Events: `grep -E '"type":"(wake|wake_cancelled|transcript_final|playback_finished|follow_up_timeout|error|status)"' ~/probe/t020-reply-20261116.log | tail -n 20`

**A1 (T-020 step 1), follow-up answered.** Maciej: „Ryszardzie, jaka będzie pogoda?”
- Expect: accepted cue, then „Wziąć parasol?”; LISTENING light, no sound;
  `playback_finished` with `"listening_for_follow_up":true`.
- Within 8 s, Maciej (no wake phrase): „Tak, poproszę”.
- Expect: accepted cue; `transcript_final` `"follow_up":true`, text „Tak, poproszę”; the reply
  again with a new window. Let it time out (`follow_up_timeout`, `idle`).
Record: pass/fail.

**A2 (step 2), window timeout.** After a reply („Ryszardzie, jaka będzie pogoda?”), Maciej
says nothing.
- About 8 s after the light comes on: `follow_up_timeout` and `idle`, no cue, light idle at
  once, no audible mic stop/start.
- Straight away, Maciej: „Ryszardzie, która godzina?” → normal: `transcript_final`
  `"follow_up":false`, then the reply and a window. Let it time out.
- Then speech without the phrase (e.g. „Halo, słyszysz mnie?”) gives nothing.
Record: pass/fail.

**A3 (step 3), noise ends the window.** Maciej: „Ryszardzie, jaka będzie pogoda?”. In the
window, he coughs or claps once (no words).
- Expect: `follow_up_timeout`, no cue, light idle at once, **no** `transcript_final`.
- If instead a `transcript_final` `"follow_up":true` appears with text nobody said (like Nov 7
  D's „Wszystkie prawa zastrzeżone”), record it verbatim with timestamps as a finding; it is
  T-023 material, do not retry.
Record: pass/fail.

**End A:** stop line, then `{ tmux has-session -t t020-reply 2>/dev/null && echo running || echo ended; }`. Expect `ended`.

## E. T-021 one-shots (6 min)

Free check first; no session runs during E. After each case run the play grep and record its
lines. Maciej only listens (and, where noted, wakes it once to confirm it is armed).

**E1 (T-021 steps 2 and 5), announcement play, then busy during it.**
CLIP11 is whisper `jfk.flac`. On 22 Nov it failed fast on the Voice PE (finding F-1:
the 24-bit file does not play; cause under investigation in its own task). E1b used
radio as the substitute (stop after 15 s; the Polish clip then got busy). See
`hw_session_2026-11-22-results.md`.

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${CLIP11:?see the top}" </dev/null & sleep 3; docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${SPEECH:?see the top}" </dev/null; echo "second exit=$?"; wait $!; echo "first exit=$?"; }`

- Heard: 11 s English speech (JFK „…ask not what your country can do for you…”), no cue, light
  idle; the Polish clip is **not** heard.
- Step 5: second command gets an immediate `busy` error, `second exit=1`.
- Step 2: one `playback_finished` within ~1 s of the clip's end, `first exit=0`.
- Afterwards Maciej: „Ryszardzie, która godzina?” → the accepted cue, as usual (no API
  client is connected during E, so record what follows; it is not part of the pass).
Record: INFO `… (url) after ~11–12 s`, nothing for the second command.

**E2 (step 3), stop mid-play.**

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${CLIP11:?see the top}" --stop-after 3 </dev/null; echo "exit=$?"; }`

- Heard: the clip cuts off ~3 s in. `stop` gets an `ack`; exactly one `playback_finished`,
  `exit=0`, `idle`.
Record: INFO `playback stopped playback_id=…`.

**E3 (step 6), 404 (GitHub over HTTPS; LAN 404 deferred).**

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${MISSING:?see the top}" </dev/null; echo "exit=$?"; }`

- Heard: nothing. One `playback_finished`, `exit=0`, no reconnect; the satellite still wakes.
- No server log to see the 404 (GitHub's); the URL's 404 is checked in part 0.
Record which path: fast fail INFO `… (url) after ~0.5 s`, or ~5 s later the WARNING
"reported no change within 5 s".

**E4 (step 7), refused port (no listener needed).** The satellite resolves
stream.radioparadise.com and sends a SYN to port 8001; that host answers with a TCP RST
(checked from piotr: refused in ~24 ms), so the satellite's HTTP client sees "connection
refused", as with the Nov 1 closed laptop port. Only the network path differs (internet host
vs LAN host).

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${REFUSED:?see the top}" </dev/null; echo "exit=$?"; }`

- Expect: as E3 (nothing heard, one `playback_finished`, `exit=0`, no reconnect).
Record which of the two lines it produced. A refused LAN port remains **deferred**.

**E5 (step 8), clip under 0.5 s.** A 0.3 s 880 Hz sine, 16 kHz 16-bit mono WAV
(`tests/data/beep_16k.wav`, T-025). Use `$BEEP_SHA` for the BEEP URL, not `$SHA`.
`BEEP_SHA` = the PR #36 merge commit (`beep_16k.wav` is not in c64ebcf); the literal
value is filled in by the next PR. Not run on 16 or 22 Nov.

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${BEEP:?see the top}" </dev/null; echo "exit=$?"; }`

- Heard: a short beep, about 0.3 s. One `playback_finished`, `exit=0`.
Record the INFO line (duration).

**E6 (step 9), https.** The same CLIP11 as E1 (E1/E2 already use HTTPS); kept as the Nov 1
step, run cold on an idle satellite. On 22 Nov this was deferred with F-1 (the 24-bit
`jfk.flac` does not play on Voice PE; own task). E1b used radio as the long-clip substitute.

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${CLIP11:?see the top}" </dev/null; echo "exit=$?"; }`

- Expect: as E1's first part (11 s heard, one `playback_finished`, `exit=0`).
Record the INFO line. If it is the 5 s WARNING while the clip still plays, note it (slow
https start).

**E7 (step 10), internet radio, stopped.** Skip if RADIO failed in part 0.

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${RADIO:?see the top}" --stop-after 20 </dev/null; echo "exit=$?"; }`

- Heard: about 20 s of music, then it stops. `stop` acked; one `playback_finished`, `exit=0`.
Record: INFO `playback stopped playback_id=…`.

## F. T-021 hub restarts: F1, F2 (4 min) — production hub restart/kill

Free check first; nothing else may run. Both steps restart the production hub (within this
approved session only). The background tool runs inside the hub container and dies with it
(no more output, non-zero status, likely 137 or 1, ignored by `wait`): expected. No
`long.wav` fallback exists now: if RADIO is down (both http and https), F is **deferred**.

**Wait-for-healthy**, after each restart:

`{ timeout 240 sh -c 'until [ "$(docker inspect -f "{{.State.Health.Status}}" piotr-voice-hub-hub-1)" = healthy ]; do sleep 2; done' && echo healthy; date -u +%FT%TZ; }`

**F1 (T-021 step 11), hub restart while the radio plays.**
Get a fresh OK from Maciej before rerunning.

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${RADIO:?see the top}" </dev/null & sleep 10; date -u +%FT%TZ; docker restart piotr-voice-hub-hub-1 && date -u +%FT%TZ; wait; }`

About 10 s of music, then the hub restarts (times printed before and after).
- Expect one of two paths:
  - shutdown stops the radio at once via `close()` (INFO `close: stopped playback … (url)`),
    no connect line;
  - or the radio stops once the hub reconnects: WARNING "the media player is PLAYING at connect
    (a play that outlived a lost connection or a hub restart?); stopping it", then INFO
    "stopped the media player at connect".
- Afterwards the satellite is idle, armed, quiet; Maciej: „Ryszardzie, która godzina?” wakes it.

Wait-for-healthy, then:

`docker logs -t --since 3m piotr-voice-hub-hub-1 2>&1 | grep -E "consumer url|hub running|voice-pe-03\]: connected to|at connect|of connecting|close: stopped"`

Record which path. Free check: no API line since the restart, or `(0 clients)`.

**F2 (T-021 step 11b), hub killed while the radio plays (connect check alone).**
Get a fresh OK from Maciej before rerunning.

`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --play "${RADIO:?see the top}" </dev/null & sleep 5; docker kill piotr-voice-hub-hub-1 && docker start piotr-voice-hub-hub-1 && date -u +%FT%TZ; wait; }`

SIGKILL skips `close()`, so the radio plays on while the hub restarts. Prints the time
`docker start` returned.
- Expect on reconnect: WARNING `the media player is PLAYING at connect (a play that outlived a
  lost connection or a hub restart?); stopping it` (state may read `ANNOUNCING`), then INFO
  `stopped the media player at connect`, and the radio stops.
- Afterwards idle and armed („Ryszardzie” wakes it).
- No `close: stopped playback` line for this play (one after the kill means the kill did not
  skip shutdown: note it; go by timestamps, F1 lines may show).

Maciej times the seconds from the printed `docker start` time until the radio goes silent.

Wait-for-healthy, then:

`docker logs -t --since 5m piotr-voice-hub-hub-1 2>&1 | grep -E "consumer url|hub running|voice-pe-03\]: connected to|at connect|of connecting|close: stopped"`

Reading: `consumer url` = play went out before the kill; `docker start` → `hub running` = hub
start (Piper check, Whisper warm-up); `connected to` → `stopped the media player at connect`
= the connect check, at most ~6 s (design §4.1 step 6).

Fail: no `at connect` WARNING while the radio still played; no `stopped … at connect` after it,
or "the media player is still busy 3 s after the stop at connect; going on" instead; radio still
playing after that line. Record (not fail): "no media player state within 3 s of connecting;
not checked for a stray play"; radio playing > 30 s after `docker start` only because
`hub running` came late (slow start).

Record: pass/fail, the lines, seconds to silence.

## Z. After (2 min)

`docker logs --since <T0> piotr-voice-hub-hub-1 2>&1 | grep -E "WARNING|ERROR|Traceback" | cut -c1-200`

Expect only: `firmware lacks optional services ['play_follow_up_ready_cue']`, two per hub start
(four after F1+F2); F1's `at connect` WARNING if
that path; F2's `at connect` WARNING; any recorded E3/E4/E6 `reported no change` WARNING. Any
ERROR or traceback is a finding.

GPU after the F restarts (fresh CUDA init in the restarted hub, relevant to T-024):

`docker logs --since <T0> piotr-voice-hub-hub-1 2>&1 | grep -iE "cuda" | cut -c1-200`

Expect the Whisper load on `cuda` after each start, and no "no CUDA-capable device" / CPU
fallback.

`docker logs --since <T0> piotr-voice-hub-piper-1 2>&1 | grep -ciE "error|traceback"` → expect `0`.

Final state (health, both satellites, 0 clients, no sessions):

`{ tmux ls 2>/dev/null | grep -E '^t02[01]-' || echo "no session"; docker inspect -f '{{.Name}} {{.State.Status}} {{.State.Health.Status}} restarts={{.RestartCount}}' piotr-voice-hub-hub-1 piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1; curl -s --max-time 3 http://127.0.0.1:8750/healthz; echo; ss -Htan state established '( sport = :8750 )' | wc -l; docker logs piotr-voice-hub-hub-1 2>&1 | grep -E "API .*(connected|disconnected)" | tail -n 1; date -u +%FT%TZ; }`

- Expect `no session`; all three `running healthy restarts=0` (`docker restart/kill/start` do
  not count; `restarts=1` on the hub right after F2 = restart-policy race: record, not a fail).
- healthz: voice-pe-02 and voice-pe-03 both `"connected":true,"state":"idle"`.
- `0` established clients; no API line or one ending `(0 clients)`.

Anything unhealthy: stop and report to the manager; no further restarts.

Logs kept for the record: `~/probe/t020-reply-20261116.log` and the terminal output of E/F.
