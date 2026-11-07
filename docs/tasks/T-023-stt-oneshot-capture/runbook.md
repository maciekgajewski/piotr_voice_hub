# T-023 runbook (not authorized to run from this task)

Do not deploy, arm, or play until Maciej accepts the design and the review of this head.
Production stays `cd8c36a` until then. `tools/speak_test.py` answers every `transcript_final`
with another `speak` or `play`. Do not use it for this study.

## Storage the container can see

The hub process runs in the `hub` container as `HUB_USER` (`docker-compose.yml` `user:`).
Its capture directory is the container path `/stt-oneshot`, the bind:

- host: `$HOME/.cache/piotr_voice_hub/stt-oneshot` (override with `HUB_STT_CAPTURE`)
- container: `/stt-oneshot`

`/home/maciek/probe/stt-oneshot` is not mounted. Inside the container that parent does not
exist, so `enabled: true` raises and `restart: unless-stopped` loops.

On the host, as the same uid/gid as `HUB_USER` (the owner of `HUB_CONFIG`), before compose:

```sh
mkdir -m 0700 -p ~/.cache/piotr_voice_hub/stt-oneshot
```

`create_host_path` is false so Docker does not create the directory as root. A missing
directory fails `docker compose up` immediately. Files are mode `0600`, owned by `HUB_USER`.
Read them on the host path above. Do not assume a path that exists only on the host is
visible in the container.

Config inside the container (the file mounted at `/config/hub.yaml`):

```yaml
stt_capture: {enabled: true, satellite: voice-pe-03, directory: /stt-oneshot}
```

`enabled: false`, or leaving the section in place, still deletes leftovers in that directory
on the next hub start. Omitting the section does not. After a crash, either start the hub
once with the section still present, or delete the host directory yourself. There is no cron
and no extra service. Shutdown also deletes the pair, so copy it first.

## Order

Both clients run on the host, against the hub WebSocket, using the **host** directory (the
same files the container sees at `/stt-oneshot`). The capture client connects first and does
not write `ARM` until `PLAY` names a playback and that playback's `playback_finished` has
`listening_for_follow_up: true`.

```sh
python -m piotr_voice_hub.stt_oneshot_client \
  --arm --confirm-one-shot --satellite voice-pe-03 \
  --ws ws://127.0.0.1:8750/ws \
  --directory ~/.cache/piotr_voice_hub/stt-oneshot
```

Then, and only then, one play. The URL must be reachable from the satellite. This client
ignores transcripts:

```sh
python -m piotr_voice_hub.stt_oneshot_play \
  --confirm-one-play --satellite voice-pe-03 \
  --ws ws://127.0.0.1:8750/ws \
  --directory ~/.cache/piotr_voice_hub/stt-oneshot \
  --url https://example.invalid/study.wav
```

The capture client releases each `transcript_final` on `voice-pe-03` and checks that the
json `utterance_id` and `cleaned_text` are that transcript's. An empty `cleaned_text` is the
segment after cleanup removed it: there is no `transcript_final`, and the client does not
treat that as a missed release. It does not send `play` or `speak`.

Copy `stt-oneshot-<utterance_id>.pcm` and `.json` from the host directory before stopping
the hub. Then set `enabled: false` (keep the section for one restart so leftovers are
deleted) or delete the directory.
