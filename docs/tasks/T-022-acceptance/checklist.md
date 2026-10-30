# T-022: acceptance checklist (Maciej, on hardware)

The P1 acceptance run (plan.md): voice-pe-02 (living_room) and voice-pe-03 (study), every
merged P1 feature end to end, the recovery cases, and the baseline numbers. Write the results
into this task's `log.md` (or the PR) with the step numbers.

**How to read the steps.** Run every command on piotr, in a terminal of its own unless a step
says otherwise. `<sha>` is the commit the hub runs (step 0); `<laptop-ip>` is the laptop's
192.168.0.x address. The tool prints every hub message (`satellite`, `wake`,
`transcript_final`, `ack`, `playback_finished`, …) and its own commands prefixed `>>> `; it
answers every `transcript_final` with `speak` "Usłyszałem: <text>" unless the step adds an
option. Ctrl-C quits it. Log lines quoted below are the hub's own wording; `docker logs -t`
prefixes each with a UTC timestamp. The wake phrase is "Ryszardzie".

**Pass/fail** (plan interpretation 3): every step behaves as expected, VRAM stays within the
design budget (step 0.3), and no container restarts on its own (step 7.3). The counts and
latencies are recorded as the baseline for the P2 soak; anything that looks wrong is written
up.

## 0. Deploy and baseline

0.1. **Deploy main** (the hub, Piper and speaker recognition; the environment is read from the
running containers, so nothing about the config changes). In `~/src/piotr_voice_hub`:

```
cd ~/src/piotr_voice_hub && git pull --ff-only && git rev-parse HEAD
export HUB_CONFIG="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/config/hub.yaml"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-hub-1)"
export HUB_USER="$(docker inspect -f '{{.Config.User}}' piotr-voice-hub-hub-1)"
export SPEAKER_PROFILES="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/profiles"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-speaker-recognition-1)"
docker compose up -d --build && docker compose ps
```

Expect: `git rev-parse HEAD` is the merged main; all three services `healthy` within about
3 minutes (a new Piper container compiles for about 50 s, design §4.3). Record: the SHA (that
is `<sha>` below) and `date -u +%FT%TZ` as **T0**.

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
python3 -m http.server 8000
```

Leave it running (2.1 s Polish speech, 11 s English speech).

## 1. P1 exit criteria (design §11), both satellites

1.1. **voice-pe-02.** Run, then say "Ryszardzie, jaka będzie jutro pogoda?", wait for the reply,
then within 8 s say "a pojutrze?" without the phrase, then stay silent:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --listen-after`
Expect: the accepted cue; `transcript_final` with `text` like "jaka będzie jutro pogoda",
`follow_up: false` and `speaker` `{"name": "Maciek", …}` (or `null` if no profile is
configured, step 2.3); the reply "Usłyszałem: …" right after the cue; `playback_finished` with
`listening_for_follow_up: true` and the `LISTENING` light; the follow-up gives
`transcript_final` with `follow_up: true` and is answered; after it, 8 s of silence gives
`follow_up_timeout`, the `IDLE` light and status `idle`, with no cue. Ctrl-C.
Record: pass/fail, the speaker field of both finals.

1.2. **voice-pe-03:** the same command and the same two sentences.
Record: as 1.1.

## 2. Wake, STT and speaker hint (wake miss rate, README note "P1 → T-022")

2.1. **voice-pe-02, 25 utterances.** Note `date -u +%FT%TZ` as **S2**, run the tool, and say
each line below from where you usually talk to it, waiting for the reply (or for nothing, on
N1–N5) before the next:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)"`

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
"Nothing": no accepted cue and no `transcript_final`. Ctrl-C at the end.
Record per line: answered or missed (1–20), woke or not (N1–N5), and whether the transcript
is wrong in a way that changes the meaning. Then the counts (paste S2):
`docker logs --since <S2> piotr-voice-hub-hub-1 2>&1 | grep "session\[voice-pe-02\]: segment" | grep -c "wake=True"`
Expect 20 (each accepted utterance has one `wake=True` segment, line 19 included; more than 20
means a negative line was accepted, fewer means misses). Misses = 20 minus the answered lines.

2.2. **voice-pe-03:** the same list (note **S3**), the same command, then
`docker logs --since <S3> piotr-voice-hub-hub-1 2>&1 | grep "session\[voice-pe-03\]: segment" | grep -c "wake=True"`
Record: as 2.1.

2.3. **Speaker hint** over 2.1 and 2.2:
`docker logs --since <S2> piotr-voice-hub-hub-1 2>&1 | grep -E "speaker utterance-|speaker hint" | cut -c1-160`
Expect: one `speaker <utterance_id>: <name> confidence <c> (…)` or `…: null (…)` INFO line per accepted utterance (a
`speaker hint off: no speaker_recognition.profiles configured` WARNING at startup means no
profile is set: then every `speaker` is `null`, which is expected, not a failure). Record:
how many of the 40 named you, and the confidence range; one line per other speaker if
someone else said a few lines (expected `null`).

2.4. **Reply latency** (plan interpretation 5), from the 2.1 and 2.2 runs:
`docker logs -t --since <S2> piotr-voice-hub-hub-1 2>&1 | grep -E "segment utterance_id=.*wake=True|playback .* requested by" | head -24`
The lines come in pairs: a `segment … wake=True` line, then the `playback … requested by`
line of its reply. Record the difference for the first five pairs of each satellite (in
seconds) and, by ear, whether the reply came promptly after the cue.

## 3. Bare wake, release and the reply timeout

3.1. **Bare wake with no request, voice-pe-03.** Run, say "Ryszardzie." and stay silent:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)"`
Expect: the `LISTENING` light; about 3 s after you stop, `wake_cancelled`, the timeout cue
and `idle`; no `transcript_final`. Ctrl-C. Record: pass/fail and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "wake word alone|no request within"`
(expect one line each).

3.2. **Release, voice-pe-02.** Run, say "Ryszardzie, nic nie mów.":
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --release`
Expect: the accepted cue, `transcript_final`, `>>> release`, its `ack`, then `idle` with no
sound. Ctrl-C. Record: pass/fail.

3.3. **Reply timeout, voice-pe-03.** No tool runs for this step (nobody answers). Say
"Ryszardzie, która godzina?" and wait 30 s. Then:
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep "reply timeout"`
Expect: the accepted cue and the `PROCESSING` light, about 30 s later the timeout cue and the
`IDLE` light; one `reply timeout after 30 s` line. Record: pass/fail.

## 4. Announcements, busy and stop

4.1. **Announcement, voice-pe-02** (no wake phrase):
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --announce voice-pe-02`
Expect: no cue; "To jest test ogłoszenia z huba."; the light stays idle; one
`playback_finished`; exit 0; "Ryszardzie" works right after. Record: pass/fail.

4.2. **Busy, voice-pe-03.** Start a 20 s announcement:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --announce voice-pe-03 --long`
and while it plays, in a second terminal:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --announce voice-pe-03`
Expect: the second one gets an immediate `busy` error and exits 1; the first plays on
unaffected and exits 0. Record: pass/fail.

4.3. **Stop with nothing playing is busy, voice-pe-02:**
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --stop voice-pe-02`
Expect: a `busy` error, exit 1, nothing heard. Record: pass/fail.

4.4. **Stop a reply mid-speech, voice-pe-02.** Run, say "Ryszardzie, opowiedz coś długiego.":
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --long --stop-after 4`
Expect: the reply cuts off about 4 s in; `stop` gets an `ack`; one `playback_finished` with
`listening_for_follow_up: false`; `idle`. Ctrl-C. Record: pass/fail.

4.5. **Stop an announcement mid-speech, voice-pe-03:**
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --announce voice-pe-03 --long --stop-after 4`
Expect: as 4.4, exit 0. Record: pass/fail.

4.6. **The end of a long reply (T-017 playback-end check), voice-pe-03.** Run, say
"Ryszardzie, opowiedz coś długiego.", listen to the end, then right after it say
"Ryszardzie, która godzina?":
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --long`
Expect: the 20 s reply is heard to its last words ("Koniec długiego testu.") with nothing cut
off; the second request is answered (the satellite re-armed in time); no `segment` line in the
hub log between `played playback_id=…` and your second request (the satellite did not hear
its own reply). Ctrl-C. Record: pass/fail and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "played playback_id|segment utterance_id" | tail -4`

## 5. Play a URL (T-021)

5.1. **Reply play, voice-pe-02.** Run, say "Ryszardzie, puść test.":
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --play http://<laptop-ip>:8000/speech.wav`
Expect: the accepted cue, then right after it the 2 s clip (the laptop server logs the GET);
one `playback_finished`; `idle`. Ctrl-C. Record: pass/fail and the INFO line from
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep "playback finished"` (`… (url) after N s`).

5.2. **Play with a follow-up, voice-pe-03.** Run, say "Ryszardzie, puść test.", after the clip say
"jeszcze raz" without the phrase, then stay silent:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --play http://<laptop-ip>:8000/speech.wav --listen-after`
Expect: the clip, `playback_finished` with `listening_for_follow_up: true` and the `LISTENING`
light; the follow-up is accepted (`follow_up: true`) and answered with the clip again; then
`follow_up_timeout` with no cue. Ctrl-C. Record: pass/fail.

5.3. **Announcement play, stopped, voice-pe-02:**
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --announce voice-pe-02 --play http://<laptop-ip>:8000/jfk.flac --stop-after 4`
Expect: no cue, the clip cuts off about 4 s in, one `playback_finished`, exit 0. Record:
pass/fail.

## 6. Restart and recovery

6.1. **Speaker service stopped, voice-pe-02.** `docker stop piotr-voice-hub-speaker-recognition-1`,
then run and say "Ryszardzie, kto mówi?":
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)"`
Expect: answered as usual, `speaker: null`, and one WARNING
`speaker <id>: recognition failed: …; hint is null`. Ctrl-C, then
`docker start piotr-voice-hub-speaker-recognition-1` and, once
`docker inspect -f '{{.State.Health.Status}}' piotr-voice-hub-speaker-recognition-1` says
`healthy`, the same command and sentence again: the speaker is back. Record: pass/fail and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep "speaker utterance-"`.

6.2. **Piper stopped, voice-pe-03.** `docker stop piotr-voice-hub-piper-1`, then run and say
"Ryszardzie, która godzina?":
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)"`
Expect: the accepted cue, then the timeout cue (no audio, never a hang); one
`playback_finished`; `idle`; one ERROR `speak playback_id=…: TTS failed: …`; the hub keeps
running. Ctrl-C, then `docker start piotr-voice-hub-piper-1`, wait for
`docker inspect -f '{{.State.Health.Status}}' piotr-voice-hub-piper-1` to say `healthy` (about
5 s for the same container), and repeat: answered. Record: pass/fail and the seconds to
healthy.

6.3. **Hub restart.** Note the time, then:
`docker restart piotr-voice-hub-hub-1`
then, once the API is up again (about 10–20 s; the tool says it cannot connect before that):
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)"`
Expect: the tool shows both satellites `connected: true`, state `idle` (in its first messages
or soon after, as they reconnect);
"Ryszardzie, która godzina?" is answered on voice-pe-02 and on voice-pe-03. Record: the
seconds from the restart to both satellites `idle` (the `docker logs -t` timestamps of
`hub running` and the two `listening listen_id` lines), from
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "hub running|connected to|listening listen_id"`.

6.4. **Hub killed while a URL plays (the connect check), voice-pe-02.** Start a long play:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --announce voice-pe-02 --play http://<laptop-ip>:8000/jfk.flac`
and within 3 s, in a second terminal:
`docker kill piotr-voice-hub-hub-1 && docker start piotr-voice-hub-hub-1`
Expect: SIGKILL skips `close()`, so the clip plays on until the hub reconnects; then, if it is
still playing, WARNING `the media player is PLAYING at connect … stopping it` and INFO
`stopped the media player at connect`, and it stops (an 11 s clip may end on its own first:
then no WARNING, which is fine); the satellite is `idle` and a wake works. Record which
happened and
`docker logs -t --since 5m piotr-voice-hub-hub-1 2>&1 | grep -E "at connect|of connecting|close: stopped"`

6.5. **Satellite power cycle, voice-pe-03.** Run the tool:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)"`
unplug voice-pe-03 for 30 s, plug it back in and wait for it to boot.
Expect: `satellite voice-pe-03` `connected: false`, state `error` soon after the unplug (at
the latest when the API connection times out); voice-pe-02 keeps working meanwhile (say
"Ryszardzie, która godzina?" to it); after the boot, `connected: true`, `idle` within the
re-arm backoff (at most 30 s), and a wake on voice-pe-03 works. Ctrl-C. Record: the seconds
from plug-in to `idle`, and
`docker logs -t --since 10m piotr-voice-hub-hub-1 2>&1 | grep -E "voice-pe-03\].*(unavailable|connected to|connect to)" | cut -c1-160`

## 7. False wakes and the end of the run

7.1. **TV period, 2 h** (an evening with the TV on in the living room, at your usual volume;
voice-pe-03 in the quiet study). Note `date -u +%FT%TZ` as **V0**, leave this running, and do
not address the hub:
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --release`
Afterwards (Ctrl-C), paste V0 and the end time V1:
`docker logs --since <V0> --until <V1> piotr-voice-hub-hub-1 2>&1 | grep -E "segment utterance_id|wake word alone" | grep -E "candidate=True|wake=True|wake word alone" | cut -c1-160`
Expect: no lines (each line is a false wake candidate or accept; every `transcript_final` the
tool printed is a false accept). Record: the count per satellite, the time and what was on
TV for each, and the total segments
`docker logs --since <V0> --until <V1> piotr-voice-hub-hub-1 2>&1 | grep -c "segment utterance_id"`
(how much speech the VAD let through).

7.2. **Overnight quiet period, at least 8 h**, both satellites, nobody addressing them: the
same command and counts with **Q0**/**Q1**.
`docker exec -it piotr-voice-hub-hub-1 python -c "$(curl -fsSL https://raw.githubusercontent.com/maciekgajewski/piotr_voice_hub/<sha>/tools/speak_test.py)" --release`
Record: as 7.1.

7.3. **End of the run:** repeat 0.2, 0.3 and 0.4, plus the reconnects since T0:
`docker logs --since <T0> piotr-voice-hub-hub-1 2>&1 | grep -E "esphome\[voice-pe-0[23]\]: connected to" | grep -oE "voice-pe-0[23]" | sort | uniq -c`
Expect: RestartCount unchanged (the restarts in 6.3 and 6.4 change StartedAt only); VRAM
within the budget; memory about as at the start (record the difference); connects per
satellite = the reconnects caused by steps 6.3–6.5 (2 for voice-pe-02, 3 for voice-pe-03; T0
is after the deploy's first connect; more means unplanned reconnects: record their times).
