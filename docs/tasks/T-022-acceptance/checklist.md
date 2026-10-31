# T-022: acceptance checklist (Maciej, on hardware)

The P1 acceptance run (plan.md): voice-pe-02 (living_room) and voice-pe-03 (study), every
merged P1 feature end to end, the recovery cases, and the baseline numbers. Write the results
into this task's `log.md` (or the PR) with the step numbers.

**How to read the steps.** Run every command on piotr. Everything works over ssh without a
TTY (a manager's non-interactive `ssh piotr '…'`, or a script fed with
`ssh -T piotr bash -s <<'EOF'`): no command prompts, pages or needs Ctrl-C, and `docker exec`
uses neither `-i` nor `-t` and gets `</dev/null`, so it never reads (and swallows) the rest of
a script on stdin. `<laptop-ip>` is the laptop's 192.168.0.x address;
0.5 runs on the laptop.

**`$SHA`, in every shell** (after 0.1's `git pull`; the production checkout is then at the
commit the hub runs). Each non-interactive `ssh piotr '…'` is a new shell, so start each such
call with this line, and put the step's command in braces:

```
<the SHA line> && { <the step's command>; }
```

The braces matter for steps with `&` or `;` (4.2, 6.4, 7.1, 7.2): without them, `&` would send
the whole `export … && …` chain to the background and the rest would run without `$SHA`.
Every tool command fetches `speak_test.py` at `$SHA`; if it is unset, bash prints
`SHA: set SHA, see the top` and the tool does nothing; the `curl` checks the SHA exists on
GitHub (a wrong one would otherwise make the tool silently do nothing):

```
export SHA="$(git -C ~/src/piotr_voice_hub rev-parse HEAD)" && curl -fsSI "https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/$SHA/tools/speak_test.py" >/dev/null && echo "SHA ok: $SHA"
```

Expect `SHA ok: <the SHA recorded in 0.1>`. The tool prints every hub message (`satellite`, `wake`,
`transcript_final`, `ack`, `playback_finished`, …) and its own commands prefixed `>>> `; it
answers every `transcript_final` with `speak` "Usłyszałem: <text>" unless the step adds an
option.

**Stopping the tool.** Without a TTY, Ctrl-C would stop only the local `docker exec`, and the
tool would stay connected to the hub as a consumer (it would answer later finals). So every
command that keeps listening runs under `timeout -s INT --preserve-status <s>` in the
container: after that many seconds it gets SIGINT, quits cleanly and exits 0. Each step's
window is long enough for what you say in it. To end one sooner, send the same SIGINT
(the stop line). **It ends every `timeout` in the hub container**, including a running 7.1 or
7.2 session: do not use it while `t022-tv` or `t022-night` runs (`tmux ls`) unless you mean
to end that period too.

```
docker exec piotr-voice-hub-hub-1 sh -c 'for p in /proc/[0-9]*; do case "$(tr "\0" " " < $p/cmdline 2>/dev/null)" in "timeout -s INT "*) kill -INT "${p#/proc/}";; esac; done' </dev/null
```

The one-shot commands (`--announce`, `--stop`) exit by themselves. Before 1.1 and before 3.3,
0.6 checks that no tool is still connected. Log lines quoted below are the hub's own
wording; `docker logs -t` prefixes each with a UTC timestamp. The wake phrase is "Ryszardzie".

**Pass/fail** (plan interpretation 3): every step behaves as expected, VRAM stays within the
design budget (step 0.3), and no container restarts on its own (step 7.3). There is **no
numeric threshold** for the wake miss rate, false wakes or reply latency: they are recorded
as the baseline for P2, and your judgement at acceptance decides. **Every false wake is
logged** with its time and transcript (2.1, 7.1, 7.2), though false wakes alone do not fail
the run.

## 0. Deploy and baseline

0.1. **Deploy main** (the hub, Piper and speaker recognition; the environment is read from the
running containers, so nothing about the config changes). In `~/src/piotr_voice_hub`:

```
cd ~/src/piotr_voice_hub && git pull --ff-only && git rev-parse HEAD
export HUB_CONFIG="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/config/hub.yaml"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-hub-1)"
export HUB_USER="$(docker inspect -f '{{.Config.User}}' piotr-voice-hub-hub-1)"
export HUB_CACHE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hf"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-hub-1)"
export SPEAKER_PROFILES="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/profiles"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-speaker-recognition-1)"
docker compose up -d --build && docker compose ps
```

Expect: `git rev-parse HEAD` is the merged main; all three services `healthy` within about
3 minutes (a new Piper container compiles for about 50 s, design §4.3). Record: the SHA (then set
`$SHA` as at the top, in every shell) and `date -u +%FT%TZ` as **T0**.

0.2. **Restarts at the start:**
`docker inspect -f '{{.Name}} restarts={{.RestartCount}} started={{.State.StartedAt}}' piotr-voice-hub-hub-1 piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1`
Record the three lines.

0.3. **VRAM** (design §4.3 budget: hub about 2.2 GB, Piper about 2.2 GB, together about 4.4 GB,
up to about 4.9 GB with concurrent partials):
`nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | while IFS=', ' read -r pid mem unit; do for c in hub piper; do docker top piotr-voice-hub-$c-1 -eo pid | grep -qw "$pid" && echo "$c $mem $unit"; done; done`
Expect: one `hub` and one `piper` line, together at most about 4.9 GB. Record both.

0.4. **Memory:** `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep voice-hub`
Record the three lines (design §9: speaker recognition about 0.3–0.45 GB).

0.5. **Laptop audio server** (for the `play` steps; on the laptop, as in T-021):

```
mkdir -p ~/t022-audio && cd ~/t022-audio
curl -fsSLo speech.wav https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/fa4316cd17990b1cc0e7a645a20366acabc60e60/tests/data/speech_pl_16k.wav
curl -fsSLo jfk.flac https://raw.githubusercontent.com/openai/whisper/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/tests/jfk.flac
python3 -c "import wave; r=wave.open('speech.wav'); p=r.getparams(); f=r.readframes(r.getnframes()); w=wave.open('long.wav','wb'); w.setparams(p); w.writeframes(f*60); w.close()"
python3 -m http.server 8000
```

Leave it running (2.1 s Polish speech, 11 s English speech, and `long.wav`: `speech.wav`
60 times, about 2 minutes, for 6.4).

0.6. **No other API consumer** (a second one would answer finals too: `busy` replies, and it
would spoil 3.3):
`docker logs piotr-voice-hub-hub-1 2>&1 | grep -E "API .* (dis)?connected \(" | tail -1`
Expect: no line, or a last line ending `(0 clients)`. Otherwise find and stop that consumer
before step 1, and repeat this check before 3.3.

## 1. P1 exit criteria (design §11), both satellites

1.1. **voice-pe-02.** Run, then say "Ryszardzie, jaka będzie jutro pogoda?", wait for the reply,
then within 8 s say "a pojutrze?" without the phrase, then stay silent:
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 120 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --listen-after </dev/null`
Expect: the accepted cue; `transcript_final` with `text` like "jaka będzie jutro pogoda",
`follow_up: false` and `speaker` `{"name": "Maciek", …}` (or `null` if no profile is
configured, step 2.3); the reply "Usłyszałem: …" right after the cue; `playback_finished` with
`listening_for_follow_up: true` and the `LISTENING` light; the follow-up gives
`transcript_final` with `follow_up: true` and is answered; after it, 8 s of silence gives
`follow_up_timeout`, the `IDLE` light and status `idle`, with no cue. The tool ends at its timeout (or the stop line).
Record: pass/fail, the speaker field of both finals.

1.2. **voice-pe-03:** the same command and the same two sentences.
Record: as 1.1.

## 2. Wake, STT and speaker hint (wake miss rate, README note "P1 → T-022")

2.1. **voice-pe-02, 25 utterances.** Note `date -u +%FT%TZ` as **S2**, run the tool, and say
each line below from where you usually talk to it, waiting for the reply (or for nothing, on
N1–N5) before the next. The tool's output (the only place with the transcripts) is saved to
`~/probe/t022-2.1.log`:
`{ mkdir -p ~/probe; docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 900 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null 2>&1 | tee ~/probe/t022-2.1.log; }`

| # | Say | Expect |
| --- | --- | --- |
| 1 | Ryszardzie, która jest godzina? | answered |
| 2 | Ryszardzie, jaka będzie jutro pogoda? | answered |
| 3 | Ryszardzie, włącz światło w kuchni. | answered |
| 4 | Ryszardzie, ile to jest dwanaście razy osiem? | answered |
| 5 | Ryszardzie, przypomnij mi o spotkaniu o piętnastej. | answered |
| 6 | Hej Ryszardzie, co słychać? | answered |
| 7 | Ryszardzie, opowiedz mi dowcip. | answered |
| 8 | Ryszardzie, jak się nazywa stolica Australii? | answered |
| 9 | Ryszardzie, zgaś wszystkie światła. | answered |
| 10 | Ryszardzie, jaki dziś dzień tygodnia? | answered |
| 11 | Słuchaj, Ryszardzie, co mamy na obiad? | answered |
| 12 | Ryszardzie, ustaw minutnik na dziesięć minut. | answered |
| 13 | Ryszardzie, puść jakąś spokojną muzykę. | answered |
| 14 | Ryszardzie, ile stopni jest na zewnątrz? | answered |
| 15 | Ryszardzie, kiedy zachodzi słońce? | answered |
| 16 | Ryszardzie, dodaj mleko do listy zakupów. | answered |
| 17 | Ryszardzie, jak długo jedzie się do Krakowa? | answered |
| 18 | Ryszardzie, czy jutro będzie padać? | answered |
| 19 | "Ryszardzie." (a pause of about 1 s) "Która godzina?" | one utterance, answered |
| 20 | Ryszardzie, Ryszardzie, jesteś tam? | answered "jesteś tam" |
| N1 | Ryszard dzisiaj nie przyjdzie. | nothing |
| N2 | Widziałem wczoraj Ryszarda w sklepie. | nothing |
| N3 | Rozmawiałem z Ryszardem o pogodzie. | nothing |
| N4 | Dziękuję, Ryszardzie. | nothing (a `wake` then `wake_cancelled` is fine) |
| N5 | Mój kolega Ryszard lubi kawę. | nothing |

"Answered": the accepted cue, `transcript_final` with the text after the phrase, the reply.
"Nothing": no accepted cue and no `transcript_final`. Use the stop line when done.
Record per line: answered or missed (1–20), woke or not (N1–N5), and whether the transcript
is wrong in a way that changes the meaning. False wakes on N1–N5 (the rules of 7.1; plan
interpretation 4), from
`grep -E '"type":"(wake|transcript_partial|transcript_final|wake_cancelled)"' ~/probe/t022-2.1.log`:
- A false accept is a `transcript_final`: record its `ts` and `raw_text`. Any accept on N1–N5
  is a false wake.
- A cancelled candidate is `wake` … `wake_cancelled`: record the `wake`'s `ts` and the `text`
  of the last `transcript_partial` before the cancel, if there is one (a partial carries only
  the text after the phrase, not the whole transcript). On N1–N3 and N5 it is a false wake.
- **N4** ("Dziękuję, Ryszardzie."): a `wake` then `wake_cancelled` is the designed outcome,
  **not** a false wake. The matcher raises a candidate for the phrase in the first three words
  (design §4.1), and the final cancels it with no cue and no `transcript_final` to a consumer
  (the `wake` and `wake_cancelled` events do go out, and the tool prints them); there is no
  partial, because nothing follows the phrase. Record only whether the candidate appeared
  (baseline). An accept on N4 is a false wake. Then the counts (paste S2):
`docker logs --since <S2> piotr-voice-hub-hub-1 2>&1 | grep "session\[voice-pe-02\]: segment" | grep -c "wake=True"`
Expect 20 (each accepted utterance has one `wake=True` segment, line 19 included; more than 20
means a negative line was accepted, fewer means misses). Misses = 20 minus the answered lines
(no threshold: the baseline).

2.2. **voice-pe-03:** the same list (note **S3** before and **E3** after), the same command
with its own log:
`{ mkdir -p ~/probe; docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 900 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null 2>&1 | tee ~/probe/t022-2.2.log; }`
then the false wakes as in 2.1 (from `~/probe/t022-2.2.log`) and
`docker logs --since <S3> --until <E3> piotr-voice-hub-hub-1 2>&1 | grep "session\[voice-pe-03\]: segment" | grep -c "wake=True"`
Record: as 2.1.

2.3. **Speaker hint** over 2.1 and 2.2:
`docker logs --since <S2> --until <E3> piotr-voice-hub-hub-1 2>&1 | grep "speaker utterance-" | cut -c1-160`
and, for the startup line, `docker logs piotr-voice-hub-hub-1 2>&1 | grep "speaker hint off"`.
Expect: one `speaker <utterance_id>: <name> confidence <c> (…)` or `…: null (…)` INFO line
per accepted utterance. If the second command prints the startup WARNING `speaker hint off:
no speaker_recognition.profiles configured`, no profile is set: then there are no `speaker`
lines and every `speaker` is `null`, which is expected, not a failure. Record:
how many of the 40 named you, and the confidence range; one line per other speaker if
someone else said a few lines (expected `null`).

2.4. **Reply latency** (plan interpretation 5), from the 2.1 and 2.2 runs:
voice-pe-02 (only it is addressed between S2 and S3):
`docker logs -t --since <S2> --until <S3> piotr-voice-hub-hub-1 2>&1 | grep -E "segment utterance_id=.*wake=True|playback .* requested by" | head -10`
voice-pe-03:
`docker logs -t --since <S3> --until <E3> piotr-voice-hub-hub-1 2>&1 | grep -E "segment utterance_id=.*wake=True|playback .* requested by" | head -10`
The lines come in pairs: a `segment … wake=True` line, then the `playback … requested by`
line of its reply. Record the difference for the five pairs of each satellite (in
seconds) and, by ear, whether the reply came promptly after the cue (no threshold: the
baseline).

## 3. Bare wake, release and the reply timeout

3.1. **Bare wake with no request, voice-pe-03.** Run, say "Ryszardzie." and stay silent:
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 60 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null`
Expect: the `LISTENING` light; about 3 s after you stop, `wake_cancelled`, the timeout cue
and `idle`; no `transcript_final`. The stop line. Record: pass/fail and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "wake word alone|no request within"`
(expect one line each).

3.2. **Release, voice-pe-02.** Run, say "Ryszardzie, nic nie mów.":
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 60 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --release </dev/null`
Expect: the accepted cue, `transcript_final`, `>>> release`, its `ack`, then `idle` with no
sound. The stop line. Record: pass/fail.

3.3. **Reply timeout, voice-pe-03.** No tool runs for this step (nobody answers; repeat the
0.6 check first). Say
"Ryszardzie, która godzina?" and wait 30 s. Then:
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep "reply timeout"`
Expect: the accepted cue and the `PROCESSING` light, about 30 s later the timeout cue and the
`IDLE` light; one `reply timeout after 30 s` line. Record: pass/fail.

## 4. Announcements, busy and stop

4.1. **Announcement, voice-pe-02** (no wake phrase):
`docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-02 </dev/null`
Expect: no cue; "To jest test ogłoszenia z huba."; the light stays idle; one
`playback_finished`; exit 0; "Ryszardzie" works right after. Record: pass/fail.

4.2. **Busy, voice-pe-03.** A 20 s announcement, and 3 s into it a second one (one command:
the first runs in the background):
`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --long </dev/null & sleep 3; docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 </dev/null; echo "second exit=$?"; wait $!; echo "first exit=$?"; }`
Expect: the second one gets an immediate `busy` error (`second exit=1`); the first plays on
unaffected (`first exit=0`). Record: pass/fail.

4.3. **Stop with nothing playing is busy, voice-pe-02:**
`docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --stop voice-pe-02 </dev/null`
Expect: a `busy` error, exit 1, nothing heard. Record: pass/fail.

4.4. **Stop a reply mid-speech, voice-pe-02.** Run, say "Ryszardzie, opowiedz coś długiego.":
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 90 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --long --stop-after 4 </dev/null`
Expect: the reply cuts off about 4 s in; `stop` gets an `ack`; one `playback_finished` with
`listening_for_follow_up: false`; `idle`. The stop line. Record: pass/fail.

4.5. **Stop an announcement mid-speech, voice-pe-03:**
`docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-03 --long --stop-after 4 </dev/null`
Expect: as 4.4, exit 0. Record: pass/fail.

4.6. **The end of a long reply (T-017 playback-end check), voice-pe-03.** Run, say
"Ryszardzie, opowiedz coś długiego.", listen to the end, then right after it say
"Ryszardzie, która godzina?":
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 120 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --long </dev/null`
Expect: the 20 s reply is heard to its last words ("Koniec długiego testu.") with nothing cut
off; the second request is answered (the satellite re-armed in time), again with the same
20 s text (with `--long` the tool answers every request with it; that is expected); no `segment` line in the
hub log between `played playback_id=…` and your second request (the satellite did not hear
its own reply). The stop line. Record: pass/fail and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "played playback_id|segment utterance_id" | tail -4`

## 5. Play a URL (T-021)

5.1. **Reply play, voice-pe-02.** Run, say "Ryszardzie, puść test.":
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 60 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --play http://<laptop-ip>:8000/speech.wav </dev/null`
Expect: the accepted cue, then right after it the 2 s clip (the laptop server logs the GET);
one `playback_finished`; `idle`. The stop line. Record: pass/fail and the INFO line from
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep "playback finished"` (`… (url) after N s`).

5.2. **Play with a follow-up, voice-pe-03.** Run, say "Ryszardzie, puść test.", after the clip say
"jeszcze raz" without the phrase, then stay silent:
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 90 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --play http://<laptop-ip>:8000/speech.wav --listen-after </dev/null`
Expect: the clip, `playback_finished` with `listening_for_follow_up: true` and the `LISTENING`
light; the follow-up is accepted (`follow_up: true`) and answered with the clip again; then
`follow_up_timeout` with no cue. The stop line. Record: pass/fail.

5.3. **Announcement play, stopped, voice-pe-02:**
`docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-02 --play http://<laptop-ip>:8000/jfk.flac --stop-after 4 </dev/null`
Expect: no cue, the clip cuts off about 4 s in, one `playback_finished`, exit 0. Record:
pass/fail.

## 6. Restart and recovery

6.1. **Speaker service stopped, voice-pe-02.** `docker stop piotr-voice-hub-speaker-recognition-1`,
then run and say "Ryszardzie, kto mówi?":
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 90 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null`
Expect: answered as usual, `speaker: null`, and one WARNING
`speaker <id>: recognition failed: …; hint is null` (only if profiles are configured, see
2.3: with none, the hub never calls the service, so there is no WARNING and nothing to
check in this step but the answer). The stop line, then
`docker start piotr-voice-hub-speaker-recognition-1` and, once
`docker inspect -f '{{.State.Health.Status}}' piotr-voice-hub-speaker-recognition-1` says
`healthy`, the same command and sentence again: the speaker is back. Record: pass/fail and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep "speaker utterance-"`.

6.2. **Piper stopped, voice-pe-03.** `docker stop piotr-voice-hub-piper-1`, then run and say
"Ryszardzie, która godzina?":
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 90 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null`
Expect: the accepted cue, then the timeout cue (no audio, never a hang); one
`playback_finished`; `idle`; one ERROR `speak playback_id=…: TTS failed: …`; the hub keeps
running. The stop line, then `docker start piotr-voice-hub-piper-1`, wait for
`docker inspect -f '{{.State.Health.Status}}' piotr-voice-hub-piper-1` to say `healthy` (about
5 s for the same container), and repeat: answered. Record: pass/fail and the seconds to
healthy.

6.3. **Hub restart.** Note the time, then:
`docker restart piotr-voice-hub-hub-1`
then, once the API is up again (about 10–20 s; the tool says it cannot connect before that):
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 120 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null`
Expect: the tool shows both satellites `connected: true`, state `idle` (in its first messages
or soon after, as they reconnect);
"Ryszardzie, która godzina?" is answered on voice-pe-02 and on voice-pe-03. Record: the
seconds from the restart to both satellites `idle` (the `docker logs -t` timestamps of
`hub running` and the two `listening listen_id` lines), from
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "hub running|connected to|listening listen_id"`.

6.4. **Hub killed while a URL plays (the connect check), voice-pe-02.** Start the 2 minute
`long.wav` (0.5; longer than the hub's 10–20 s restart) in the background, and 5 s into it
(time for the tool to start, the `play` to go out and the satellite to fetch and start the
clip) kill and start the hub (one command; it prints the time of the `docker start`):
`{ docker exec piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --announce voice-pe-02 --play http://<laptop-ip>:8000/long.wav </dev/null & sleep 5; docker kill piotr-voice-hub-hub-1 && docker start piotr-voice-hub-hub-1 && date -u +%FT%TZ; wait; }`
(the background tool runs inside the hub container, so `docker kill` kills it too: it prints
nothing more, and its `docker exec` ends with a non-zero status, likely 137, which `wait`
ignores; that is expected here).
Expect: SIGKILL skips `close()`, so the clip plays on while the hub restarts; when the hub
reconnects, the WARNING `the media player is PLAYING at connect (a play that outlived a lost
connection or a hub restart?); stopping it` (the state may read `ANNOUNCING` instead), then
INFO `stopped the media player at connect`, and the audio stops; the satellite is `idle` and
a wake works. Before calling anything a fail, record the timestamped lines:
`docker logs -t --since 5m piotr-voice-hub-hub-1 2>&1 | grep -E "consumer url|hub running|voice-pe-02\]: connected to|at connect|of connecting|close: stopped"`
The `playback playback_id=… consumer url=…` line shows the `play` went out before the kill.
The others separate a slow start from a missing check: `docker start` → `hub running` is the hub's
start (Piper check, Whisper warm-up), `connected to` → `stopped the media player at connect`
is the connect check itself (at most about 6 s, design §4.1 step 6). Fail: no `at connect`
WARNING while the clip was still playing, no `stopped the media player at connect` after it
(or the `still busy … after the stop at connect` WARNING), or the audio still playing after
that line. Audio still playing more than 30 s after the `docker start` only because
`hub running` came late is a slow start, not a connect-check fail: record it. Record:
pass/fail, those lines, and the seconds from the `docker start` to silence.

6.5. **Satellite power cycle, voice-pe-03.** Run the tool:
`docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 240 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" </dev/null`
unplug voice-pe-03 for 30 s, plug it back in and wait for it to boot.
Expect: `satellite voice-pe-03` `connected: false`, state `error` soon after the unplug (at
the latest when the API connection times out); voice-pe-02 keeps working meanwhile (say
"Ryszardzie, która godzina?" to it); after the boot, `connected: true`, `idle` within the
re-arm backoff (at most 30 s), and a wake on voice-pe-03 works. The stop line. Record: the seconds
from plug-in to `idle`, and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "voice-pe-03\].*(unavailable|connected to|connect to)" | cut -c1-160`

## 7. False wakes and the end of the run

7.1. **TV period, 2 h** (an evening with the TV on in the living room, at your usual volume;
voice-pe-03 in the quiet study). The run must survive an ssh drop and needs no TTY, so it runs
in a detached `tmux` session (installed on piotr) that carries `$SHA`, ends by itself after
2 h, and saves the tool's output (stderr too, so a `cannot connect` is kept) to
`~/t022-tv.log`. Note `date -u +%FT%TZ` as **V0**, start
it, and do not address the hub:
`{ date -u +%FT%TZ; tmux new -d -s t022-tv -e SHA="$SHA" 'docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 7200 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --release </dev/null 2>&1 | tee ~/t022-tv.log'; }`
Check it is running (any time; also after an ssh drop):
`{ tmux has-session -t t022-tv && echo running; tail -n 3 ~/t022-tv.log; }`
Expect `running` and the tool's `satellite` lines. To watch from a real terminal,
`tmux attach -t t022-tv` (detach with Ctrl-b d). When it has ended (`tmux has-session` fails;
the stop line ends it sooner), note the end time **V1** (V0 + 2 h) and run:
`docker logs --since <V0> --until <V1> piotr-voice-hub-hub-1 2>&1 | grep -E "segment utterance_id|wake word alone" | grep -E "candidate=True|wake=True|wake word alone" | cut -c1-160`
Expect: ideally no lines (each line is a false wake candidate or accept). False wakes do not
fail the run on their own (no threshold), but every one is logged. The time and transcript
of each, from the saved tool output (the hub log has no transcript text):
`grep -E '"type":"(wake|transcript_partial|transcript_final|wake_cancelled)"' ~/t022-tv.log`
Each line has `ts` (UTC) and `satellite`. A false accept is a `transcript_final`: its
transcript is `raw_text`. A candidate that was cancelled is `wake` … `wake_cancelled`: its
transcript is the last `transcript_partial` `text` before it (none for a bare "Ryszardzie").
Record: the count per satellite, the total segments
`docker logs --since <V0> --until <V1> piotr-voice-hub-hub-1 2>&1 | grep -c "segment utterance_id"`
(how much speech the VAD let through), and one row per false wake:

| ts (UTC) | satellite | accepted or cancelled | transcript | what was on TV |
| --- | --- | --- | --- | --- |
| | | | | |

7.2. **Overnight quiet period, at least 8 h**, both satellites, nobody addressing them: the
same steps and counts with **Q0**/**Q1**, in its own detached session `t022-night` that
ends by itself after 8.5 h, the output saved to `~/t022-night.log`:
`{ date -u +%FT%TZ; tmux new -d -s t022-night -e SHA="$SHA" 'docker exec piotr-voice-hub-hub-1 timeout -s INT --preserve-status 30600 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/${SHA:?set SHA, see the top}/tools/speak_test.py)" --release </dev/null 2>&1 | tee ~/t022-night.log'; }`
check it with `{ tmux has-session -t t022-night && echo running; tail -n 3 ~/t022-night.log; }`, and
afterwards `grep -E '"type":"(wake|transcript_partial|transcript_final|wake_cancelled)"' ~/t022-night.log`.
Record: as 7.1, with "what was going on" (if known) instead of "what was on TV".

7.3. **End of the run:** repeat 0.2, 0.3 and 0.4, plus the reconnects since T0:
`docker logs --since <T0> piotr-voice-hub-hub-1 2>&1 | grep -E "esphome\[voice-pe-0[23]\]: connected to" | grep -oE "voice-pe-0[23]" | sort | uniq -c`
Expect: RestartCount unchanged (the restarts in 6.3 and 6.4 change StartedAt only); VRAM
within the budget; memory about as at the start (record the difference); connects per
satellite = the reconnects caused by steps 6.3–6.5 (2 for voice-pe-02, 3 for voice-pe-03; T0
is after the deploy's first connect; more means unplanned reconnects: record their times).
