# Test data

- `speech_pl_16k.wav`: 2.1 s of Polish speech, 16 kHz 16-bit mono. Converted with ffmpeg from
  `piotr_the_ai_server` `audio/monitor-test/box3-tts-20260526T110342Z.wav` @ `a299643`, which is
  Piper TTS output with the `pl_PL-bass-high` voice (Apache 2.0). It says "...jest test pierwszy".
- `wake_pl_16k.wav`: 2.6 s, 16 kHz 16-bit mono. "Ryszardzie, jaka będzie jutro pogoda?"
  synthesized on piotr by the `piotr-wyoming-piper` container (Wyoming, voice `pl_PL-bass-high`,
  Apache 2.0) at 22.05 kHz, then `ffmpeg -ar 16000 -ac 1 -sample_fmt s16`.
