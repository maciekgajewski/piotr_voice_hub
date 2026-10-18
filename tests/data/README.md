# Test data

- `speech_pl_16k.wav`: 2.1 s of Polish speech, 16 kHz 16-bit mono. Converted with ffmpeg from
  `piotr_the_ai_server` `audio/monitor-test/box3-tts-20260526T110342Z.wav` @ `a299643`, which is
  Piper TTS output with the `pl_PL-bass-high` voice (Apache 2.0). It says "...jest test pierwszy".
- `wake_pl_16k.wav`: 2.6 s, 16 kHz 16-bit mono. "Ryszardzie, jaka będzie jutro pogoda?"
  synthesized on piotr by the `piotr-wyoming-piper` container (Wyoming, voice `pl_PL-bass-high`,
  Apache 2.0) at 22.05 kHz, then `ffmpeg -ar 16000 -ac 1 -sample_fmt s16`.
- `alarm_tone_16k.wav`: 2.7 s loud non-speech (a timer alarm tone, peak 32758 of 32767), 16 kHz
  16-bit mono. Used to check that Silero opens no segment on loud non-speech.
  **Attribution (CC BY 4.0):** "timer_finished" from Home Assistant Voice Preview Edition
  Sounds © 2024 by Clayton Charles Tapp, licensed under CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/). Source:
  https://github.com/esphome/home-assistant-voice-pe/blob/26.4.0/sounds/timer_finished.flac
  (licence: `sounds/LICENSE.md` in that repo). **Changed:** converted from FLAC to 16 kHz 16-bit
  mono WAV with `ffmpeg -ar 16000 -ac 1 -sample_fmt s16`. We got it via `piotr_the_ai_server`
  `audio/playback/timer_finished.flac` @ `a299643`, which is byte-identical to the upstream
  file at tag 26.4.0 (sha256 `e9ddba38c4a993fd5a218ec7d04d7a2824b995a9dbb6cb0d8599d7a0ac6a56ee`).
