# T-023 runbook

**Not capture-ready. Do not deploy, arm, or play.** `at` is not installed (sysadmin
installation is blocked). This repository has not verified the host account. The manager
reports user `grok-developer`, uid/gid `1002`, `HOME=/home/grok-developer`. Confirm that
on the host before any future study. The path in the `at` job, the `--directory` argument,
and the bind source must be that same absolute directory.

`tools/speak_test.py` answers every `transcript_final`. Do not use it. Before starting,
the hub log must show no other API client answering transcripts (zero clients on the
WebSocket, or none of them send `speak` or `play`).

## Compose

A normal `docker compose up` does not mount a capture directory and does not need one.
The study adds the bind only for that run:

```sh
mkdir -m 0700 -p -- "$DIR"
docker compose -f docker-compose.yml -f docker-compose.stt-oneshot.yml up -d hub
```

`DIR` is the host directory. The override mounts it at `/stt-oneshot` with
`create_host_path: false`. `$DIR` and `HUB_STT_CAPTURE`, when set, must be the same path.
Dropping the second `-f` returns to the production compose.

Config inside the container:

```yaml
stt_capture: {enabled: true, satellite: voice-pe-03, directory: /stt-oneshot}
```

The hub does not create `/stt-oneshot`. If that path is missing or not mode `0700`,
capture stays off and the process stays up.

## Cleanup job, before any client

```sh
DIR=/home/grok-developer/.cache/piotr_voice_hub/stt-oneshot   # only if that is $HOME
printf '%s\n' "find -P $DIR -mindepth 1 -delete" | at now + 30 minutes
atq
at -c JOB
```

Use the confirmed absolute `$DIR`, not a different spelling of the same folder. `at -c JOB`
must contain exactly one `find` or `rm` line, and that line must be
`find -P $DIR -mindepth 1 -delete`. The directory itself stays, so a later restart can
still bind it. `atq` must show `JOB` at the deadline you pass the client, still in the
future. If either check is uncertain, do not continue.

## Clients

Capture client first:

```sh
python -m piotr_voice_hub.stt_oneshot_client \
  --arm --confirm-one-shot --satellite voice-pe-03 \
  --ws ws://127.0.0.1:8750/ws \
  --directory "$DIR" --at-job JOB --cleanup-deadline 'YYYY-MM-DDTHH:MM:SS'
```

Then one play. It refuses to send audio if `PLAY` or `ARM` is already in `$DIR`:

```sh
python -m piotr_voice_hub.stt_oneshot_play \
  --confirm-one-play --satellite voice-pe-03 \
  --ws ws://127.0.0.1:8750/ws \
  --directory "$DIR" --url 'https://example.invalid/study.wav'
```

Copy the `.pcm` and `.json` before the job runs. Empty `cleaned_text` means cleanup
removed the words: there is no `transcript_final`. A non-empty file must match the
released utterance's `raw_text`. A follow-up of several segments, or a first segment
that is only "Ryszardzie", can make `raw_text` differ from the first segment's
`cleaned_text`; the client then reports a mismatch and does not treat it as success.

## Teardown

Delete the contents first. `atrm` runs only after that command succeeds. If the delete
fails, the job stays queued.

```sh
python -m piotr_voice_hub.stt_oneshot_client --teardown --directory "$DIR" --at-job JOB
```

Then set `enabled: false` or remove the section, and stop using the override file.

## What deletion depends on

The hub never deletes these files. The job deletes the contents only while `atd` is
running. A reboot runs a due job only if `atd` starts. If the host or `atd` is down, the
files stay. There is no absolute wall-clock deletion.
