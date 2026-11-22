# Test data

- `speech_pl_16k.wav`: 2.1 s of Polish speech, 16 kHz 16-bit mono. Converted with ffmpeg from
  `piotr_the_ai_server` `audio/monitor-test/box3-tts-20260526T110342Z.wav` @ `a299643`, which is
  Piper TTS output with the `pl_PL-bass-high` voice. The MODEL_CARD marks that voice's own
  Polish dataset as Apache 2.0; see the T-028 lineage note below (test use only). It says
  "...jest test pierwszy".
- `wake_pl_16k.wav`: 2.6 s, 16 kHz 16-bit mono. "Ryszardzie, jaka będzie jutro pogoda?"
  synthesized on piotr by the `piotr-wyoming-piper` container (Wyoming, voice `pl_PL-bass-high`)
  at 22.05 kHz, then `ffmpeg -ar 16000 -ac 1 -sample_fmt s16`. See the T-028 lineage note below
  (test use only).
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

- `beep_16k.wav`: 0.3 s 880 Hz sine tone, 16 kHz 16-bit mono, peak 0.5 of full scale, with a
  5 ms linear fade in and out (9644 bytes). Generated for the T-021 hardware checklist E5
  (clip under 0.5 s). Recreate from the repo root:

```sh
python3 - <<'PY'
import math, struct, wave
rate, dur, freq, amp, fade_s = 16000, 0.3, 880.0, 0.5, 0.005
n, fade = int(round(rate * dur)), int(rate * fade_s)
samples = []
for i in range(n):
    s = amp * math.sin(2 * math.pi * freq * i / rate)
    if i < fade:
        s *= i / fade
    elif i >= n - fade:
        s *= (n - 1 - i) / fade
    samples.append(int(round(s * 32767)))
with wave.open("tests/data/beep_16k.wav", "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", x) for x in samples))
PY
```

- `jfk_44100_stereo_s16.flac`: 11.000 s, 44.1 kHz stereo 16-bit native FLAC (485,100 samples,
  block size 4608, 488,897 bytes, sha256
  `3745bf960c00b0579ccc30e8ec5abe74337b957872ed8ac3ed19d330560653d6`). A 16-bit re-encode of
  OpenAI Whisper's test clip, used as the pinned 11 s FLAC for hardware play checks (E1/E6).
  **Source (MIT):**
  Copyright (c) 2022 OpenAI
  https://raw.githubusercontent.com/openai/whisper/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/tests/jfk.flac
  (OpenAI Whisper, MIT,
  https://github.com/openai/whisper/blob/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/LICENSE).
  The speech is the public-domain JFK inaugural-address excerpt that file carries. The
  original is 24-bit 44.1 kHz stereo; Voice PE firmware 25.10 and earlier rejects non-16-bit
  FLAC, and that original finished in 0.9 s with nothing heard on voice-pe-03 on 2026-11-22.
  **Command:**
  ```
  ffmpeg -i jfk.flac -c:a flac -sample_fmt s16 tests/data/jfk_44100_stereo_s16.flac
  ```

## Recorded-audio fixtures (T-028)

Replayable 16 kHz 16-bit mono WAVs for VAD, the ESPHome segmenter, STT and the wake matcher,
in `tests/data/fixtures/` with `manifest.json`. They are synthesized or copied from the
clips above. There is no household recording and no capture tool in this repo.

| id | file | intended text | wake | VAD |
| --- | --- | --- | --- | --- |
| wake_question | fixtures/wake_question.wav | Ryszardzie, jaka będzie jutro pogoda? | yes | 1 segment |
| question_no_wake | fixtures/question_no_wake.wav | Jaka będzie jutro pogoda? | no | 1 segment |
| near_miss | fixtures/near_miss.wav | Rysiek, włącz światło | no | 1 segment |
| inflected | fixtures/inflected.wav | Ryszarda nie ma w domu | no | 1 segment |
| silence | fixtures/silence.wav | (3 s of zeros) | no | none |
| noise_only | fixtures/noise_only.wav | (3 s Gaussian, seed 28) | no | none |
| speech_after_silence | fixtures/speech_after_silence.wav | 4 s of zeros, then the wake question | yes | 1 segment, start ≥ 4 s |
| follow_up | fixtures/follow_up.wav | Ustaw minutnik na pięć minut | no | 1 segment |
| tv_music | fixtures/tv_music.wav | tones + noise + layered non-wake phrases | no (hard) | any (soft) |
| two_speakers | fixtures/two_speakers.wav | two overlapping non-wake phrases | no (hard) | at least 1 (soft transcript) |

`wake_question.wav` is a byte-identical copy of `wake_pl_16k.wav` (Piper
`pl_PL-bass-high`; already checked by `tests/test_stt_gpu.py`). The other speech clips were
synthesized on piotr with a throwaway `piotr-voice-hub-piper:t028` container on
`127.0.0.1:10228`, never the production piper on 10201. Silence, noise, the 4 s lead, the
music bed and the two-speaker mix were generated by `tools/generate_audio_fixtures.py`
(seed 28 where a PRNG is used).

Regeneration is not bit-identical for Piper-derived speech: the same text and voice can
yield different PCM across model or runtime versions. The committed WAVs and the sha256
values in `manifest.json` are the CI source. Silence (zeros), `noise_only` (seeded PRNG,
seed 28), the byte-identical `wake_question.wav` copy, and the `speech_after_silence.wav`
concatenation of zeros plus that copy are bit-reproducible. The `two_speakers` and
`tv_music` mixes are deterministic given the same input PCM and seed, but their Piper
layers are not.

**Lineage (`pl_PL-bass-high`, test use only).** The MODEL_CARD at rhasspy/piper-voices
`c10ece1aade47bb51c153c893d14e5bf8e5b7117` (`pl/pl_PL/bass/high/MODEL_CARD`) marks that
voice's own Polish dataset as Apache 2.0, and says it was fine-tuned from
`en_US-lessac-high`. The `en_US-lessac-high` MODEL_CARD at the same revision names the
Lessac Blizzard 2013 corpus, whose licence is for research and exploration of speech
technologies and excludes commercial use of voice-synthesis products or services
(https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html).
Piper's licensing note is that a MODEL_CARD licence line describes that voice's own
dataset only, not the datasets of a voice it was fine-tuned from. These Piper-derived
clips are for test use in this repo only.

CPU tests: `tests/test_audio_fixtures.py` (format, sha256, VAD/segmenter, matcher on the
intended text). GPU / STT: `tests/test_audio_fixtures_gpu.py`, run with
`tools/fixtures-gpu-test.sh` (image `piotr-voice-hub-fixtures-test:t028`).

To regenerate (on piotr, from a worktree; needs the named piper image
`piotr-voice-hub-piper:rollback-4c96b16` or `SOURCE_TAG` set):

```sh
tools/generate_audio_fixtures.sh
```

The script tags `piotr-voice-hub-piper:t028`, publishes Wyoming on `127.0.0.1:10228`, writes
the WAVs and the manifest, then removes only that container and tag. It does not prune. The
generator refuses a Piper URI that is not loopback (`127.0.0.1`, `localhost`, or `::1`) and
refuses port 10201.

### Adding a consented clip

Do not record from a satellite or a room mic unless Maciej has approved the phrases and the
output path first. Then:

1. Write 16 kHz 16-bit mono WAV files into a path he approved (outside git).
2. Copy only the consented files into `tests/data/fixtures/`.
3. Add a manifest entry with the text, expected wake / transcript, source, and a sha256.
4. Run `tests/test_audio_fixtures.py` and, on piotr, `tools/fixtures-gpu-test.sh`.

This set is one acoustic family (synthesized). A Voice PE vs BOX-3 recapture is a question
for Maciej in `docs/tasks/T-028-recorded-audio-fixtures/plan.md`, not something to do
without his words.
