# piotr_voice_hub

A voice I/O hub for Piotr's ESPHome satellites (Home Assistant Voice PE, ESP32-S3-BOX-3).
It listens continuously, detects speech with a neural VAD, transcribes Polish with Whisper on
the GPU, accepts utterances addressed with the wake phrase "Ryszardzie" (with a speaker hint),
and speaks replies with Piper. Consumers talk to it over one WebSocket API.

- Design: [docs/design.md](docs/design.md)
- Working rules for contributors and agents: [AGENTS.md](AGENTS.md)
- Checks: `make check` (needs [uv](https://docs.astral.sh/uv/))

Licensed under MIT (see [LICENSE](LICENSE)).
