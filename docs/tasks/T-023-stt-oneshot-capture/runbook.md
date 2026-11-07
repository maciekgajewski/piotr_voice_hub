# T-023 runbook

**Not capture-ready. Do not deploy, arm, or play.** `at` is not installed (sysadmin
installation is blocked). This repository has not verified the host account. The manager
reports user `grok-developer`, uid/gid `1002`, `HOME=/home/grok-developer`. Confirm that on
the host before any future study. Do not treat it as checked.

`tools/speak_test.py` answers every `transcript_final`. Do not use it.

## What would have to be true first

`atd` must already be running and `at` must work for the same uid as `HUB_USER`, with no
new unit from this repo. The manager is giving the operator the manual install command.
Nothing in this task installs or probes it. Until `at -c <job>` succeeds, the capture
client exits before `ARM`.

## Paths

| | Path |
|---|---|
| Container (config `stt_capture.directory`) | `/stt-oneshot` |
| Host bind (default) | `$HOME/.cache/piotr_voice_hub/stt-oneshot` |

The host directory must already exist, mode `0700`, owned by the user who will submit
`at`. Compose uses `create_host_path: false`, so a missing directory fails `docker compose
up` immediately. The hub does not create the directory and does not exit if the configured
path is missing, so a bad path does not restart-loop.

```yaml
stt_capture: {enabled: true, satellite: voice-pe-03, directory: /stt-oneshot}
```

## Cleanup job, before any client

As that same user, with the absolute host path (no `$HOME` in the job):

```sh
DIR=/home/grok-developer/.cache/piotr_voice_hub/stt-oneshot
mkdir -m 0700 -p -- "$DIR"
printf '%s\n' "rm -rf -- $DIR" | at now + 30 minutes
atq
at -c JOB
```

`at -c JOB` must contain exactly one `rm` line, and that line must be `rm -rf -- $DIR`
with the absolute path. `atq` must show `JOB` at the deadline you will pass to the client,
still in the future. If either check is uncertain, do not continue.

## Clients (host path, not the container path)

Capture client first. It connects only after the job check, and writes `ARM` only after
`PLAY` and `playback_finished` with `listening_for_follow_up: true`:

```sh
python -m piotr_voice_hub.stt_oneshot_client \
  --arm --confirm-one-shot --satellite voice-pe-03 \
  --ws ws://127.0.0.1:8750/ws \
  --directory "$DIR" --at-job JOB --cleanup-deadline 'YYYY-MM-DDTHH:MM:SS'
```

Then one play. It ignores transcripts:

```sh
python -m piotr_voice_hub.stt_oneshot_play \
  --confirm-one-play --satellite voice-pe-03 \
  --ws ws://127.0.0.1:8750/ws \
  --directory "$DIR" --url 'https://example.invalid/study.wav'
```

Copy the `.pcm` and `.json` before the job runs. Empty `cleaned_text` means cleanup removed
the words: there is no `transcript_final`, and the client does not treat that as a missed
`release`. A non-empty file must match the released utterance.

## Teardown

Remove the directory first. Cancel the job only after that:

```sh
python -m piotr_voice_hub.stt_oneshot_client --teardown --directory "$DIR" --at-job JOB
```

Then set `enabled: false` or remove the section.

## What deletion depends on

The hub never deletes the directory. The job deletes it, partials included, only while
`atd` is running. A reboot runs a due job only if `atd` starts. If the host or `atd` is
down, the files stay. There is no absolute wall-clock deletion.
