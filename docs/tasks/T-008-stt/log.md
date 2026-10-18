# T-008 log

## 2026-10-18

- Branch `t-008-stt` off `origin/main` @ `c3a8dd6` (T-005 merged).
- Copied the old `faster_whisper.py` and trimmed it to the streaming session. Dropped: the
  non-streaming `SttSession`, `language` (fixed `pl`), `local_files_only` and
  `log_transcripts` config (not in design.md §7: the model downloads into the HF cache on first
  start, and transcript text is logged only at DEBUG), the temp WAV per call (the audio goes to
  the model as a float32 array, so no ffmpeg/av decode per partial), and the extra partial after
  audio end (the final replaces it; `transcribe_final()` no longer waits up to one interval).
- Bug found and fixed while testing: `close()` before the partial worker ever ran left
  `receive_text()` waiting forever (the cancelled task never reached its `finally`). `TextEnd` is
  now sent exactly once, by the worker or by `close()`.
- `make check` (no GPU): 268 passed, 2 skipped (the GPU test is opt-in). Gating on a CUDA device
  alone was not enough: piotr's host has the driver but no cuBLAS, so the test ran, downloaded
  the model and failed with `libcublas.so.12`. It now also needs `PIOTR_VOICE_HUB_GPU_TESTS=1`,
  which `tools/stt-gpu-test.sh` sets.
- Test clip `tests/data/wake_pl_16k.wav` (2.6 s): "Ryszardzie, jaka będzie jutro pogoda?",
  synthesized by the running `piotr-wyoming-piper` (pl_PL-bass-high), resampled to 16 kHz.

### GPU results on piotr (RTX 5060 Ti, nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04)

Clips streamed at real-time pace in 32 ms chunks; design §7 STT config (float16, beam 5 final,
beam 1 partials every 0.75 s over 4 s).

- Load + warm-up: 9.7 s cold (first process run), 1.6 s with the model already cached in the
  same process. Device-wide peak VRAM during the test: 2302 MiB (12 MiB idle baseline), which
  matches design.md §4.3 (2.3 GB peak).
- Final latency after segment end: 0.16–0.26 s.
- **`hotwords="Ryszardzie"` (as design.md §4.1 step 3 says):** the wake clip is recognized from
  the first partial ("Ryszardzie"). But the model echoes "Ryszardzie" as the whole transcript
  for 3 s of silence and of noise (partials and final), and for the first ~0.75 s of ordinary
  speech (seen once on "O jest test pierwszy"). The echo is always the bare word, so a final
  has an empty remainder and would be cancelled. The partial would still emit a false `wake`
  and set the LISTENING visual.
- **No bias:** no wake-word echo on silence or noise (Whisper's usual "Dziękuję." / "Dzięki za
  oglądanie!" hallucinations instead; VAD keeps those away). The wake clip's final is right, but
  the first two partials read "Przyszardzie…", so with the vocative-only matcher (T-009 review)
  the `wake` partial comes about 1.5 s later.
- `initial_prompt="Ryszardzie"` behaves like `hotwords` (same echo); measured in a one-off run.
- So `hotwords` is an optional argument; the caller (T-010 wiring) decides. **Open design
  question for Maciej**; design.md is unchanged.

## 2026-10-18: hotword decision (manager)

- **Decision: drop the hotword bias.** The `hotwords` option is removed from `stt/whisper.py`
  (nothing else used it), from the T-003 smoke script, and from design.md §4.1 step 3 and the
  §6 STT row. No `initial_prompt` either: the only prompt worth adding was the wake word, and it
  echoed the same way as `hotwords` (measured above).
- The GPU test now has one variant (no bias). It asserts that silence and noise never yield
  "ryszard", and that the wake clip's final and a partial start "Ryszardzie jaka".
- Re-run on piotr, unbiased: load + warm-up 10.0 s; finals "O jest test pierwszy." and
  "Ryszardzie, jaka będzie jutro pogoda?" (final latency 0.17-0.30 s); silence and noise gave
  "Dziękuję." / "Dzięki za oglądanie!" / "Dziękuje za uwagę.", no "Ryszardzie". Passed.
- `tools/stt-gpu-test.sh` now passes `--reinstall-package piotr-voice-hub`: the first re-run
  silently tested the cached wheel of the previous commit (same version, so uv reused it).
- `make check`: 268 passed, 1 skipped.
- Transcript cleanup (PR #7 re-review note, manager): the preprocessor drops bracketed Whisper
  notes, i.e. any non-nested "(...)" or "[...]" group ("(muzyka)", "[MUZYKA]", "(śmiech)") with
  the whitespace before it, then collapses spaces. "Ryszardzie (muzyka)" becomes "Ryszardzie",
  so the matcher sees an empty remainder. Runs on partials and finals, before the copied rules.

## 2026-10-18: review fixes (PR #8, review 5564592404) and old-project parity

- **What piotr_the_ai_server @ a299643 did** (read with `git show`/`git grep`, nothing checked
  out or changed):
  - `ai_server/speech_to_text/faster_whisper.py`: `model.transcribe(wav, language=..., beam_size=...)`
    and nothing else. So faster-whisper defaults: temperature fallback ladder,
    `condition_on_previous_text=True`, default no-speech/log-prob/compression gates, no
    `vad_filter`, **no `hotwords`, no `initial_prompt`**. Warm-up on all-zero audio.
  - `ai_server/speech_to_text/transcript_preprocessor.py`: the five replacement rules only
    (logged at INFO). No hallucination blocklist or bracket/♪ handling anywhere in
    `speech_to_text/` or `microphones/` (`git grep` for hotword, initial_prompt, no_speech,
    logprob, hallucin, amara, napisy, muzyka found nothing relevant). The only "dziękuję" list
    is `orchestrator/orchestrator.py:_cancels_pending_clarification`, agent logic, not STT.
  - Wake path, `ai_server/microphones/manager.py`: `_text_after_wake_phrase` is
    `(?iu)(?:^|\b)Ryszardzie\b[\s,.:;!?-]*(.*)` anywhere in the text. Partials only set the
    LISTENING visual (`_collect_open_mic_partials`); the final must have a non-empty remainder,
    otherwise `ResetWakeCandidate` + IDLE.
  - Silence and false wakes: the driver's peak gate (`drivers/box3_esphome.py`
    `_observe_audio_level`, `SPEECH_PEAK_THRESHOLD = 500`, `SPEECH_START_SECONDS = 0.2`,
    `end_silence_seconds` 0.9 in `config.example.yaml`) only opened a segment on loud audio, so
    Whisper rarely saw silence. With no bias there was no echo to suppress.
- **Hotwords: dropped (re-checked).** The old project never used a bias, so there was no
  gate+bias combination to reproduce; the design's `hotwords`/`initial_prompt` line was new in
  the hub. A gate can't fix it either: the echo also hit the first ~0.75 s of real speech
  (measured above), which any gate lets through. Removed in code, tests, plan, design §4.1
  step 3 and the §6 STT row (done in 9927fbc). Unbiased on piotr: silence and noise give no
  "Ryszardzie".
- **Decoding: back to the old project's call** (language + beam only). I had added
  `temperature=0`, `condition_on_previous_text=False`, a no_speech/avg_logprob filter and a
  hallucination blocklist; all removed before commit, as directed. Two deviations, both small:
  the audio goes to the model as an array (no temp WAV), and the warm-up uses low-level seeded
  noise instead of zeros (review nit: a realistic decode path for warm-up).
- **Added, beyond the old project:** the preprocessor drops bracketed notes ("(muzyka)",
  "[MUZYKA]") and returns "" for text with no letter or digit ("♪"). Reason: the hub accepts a
  wake plus any non-empty remainder, and the follow-up window (§4.1 step 7) accepts any
  non-empty text, so "Ryszardzie (muzyka)" or a bare "♪" must not count as a command. The old
  exact-phrase matcher had the same exposure; it is two lines here.
- Known and unchanged (as in the old project): Whisper's "Dziękuję." / "Dzięki za oglądanie!"
  on silence/noise. VAD (T-006) keeps non-speech away; T-013 measures what is left live.
- Reliability fixes:
  - Timeouts: model load 900 s (first start downloads 1.6 GB), each warm-up call 120 s, each
    transcription 30 s including the wait for the model lock, and the final's wait for the
    partial worker 30 s. On timeout or a model exception: `log.critical` and `os._exit(70)`
    (not `sys.exit`: a wedged executor thread would block shutdown); Docker restarts the hub.
    The handler is injectable for tests.
  - A partial failure is fatal as above; in tests (injected handler) it surfaces from
    `receive_text()` and `transcribe_final()`. `close()` retrieves the task's exception.
  - One `asyncio.Lock` per model: a partial skips its tick when the model is busy, a final
    waits. Partials run only when new audio arrived since the last partial.
  - The event queue is bounded (`ceil(max_audio / interval) + 2`), and `send_audio` asserts the
    segment cap (`max_audio_seconds`, i.e. max_segment_seconds plus pre-roll).
- Nits: `faster_whisper` imported at the top; preprocessor logs at DEBUG; docstring says VRAM is
  freed only at process exit; types not moved (skipped, as directed).
- `docs/tasks/README.md`: P0 task list with the new **T-013 wake-test runner** (after T-007,
  T-008 and T-009 merge, before T-010; seed of T-012's main).

### GPU results on piotr, 4 concurrent streams

`tools/stt-gpu-test.sh` (now also samples device VRAM every 200 ms):

- Load + warm-up 10.5 s. One stream: finals 0.18–0.31 s after segment end.
- Four concurrent streams (speech, wake, noise, silence clips started together, like four
  satellites finishing at once): finals 0.17–0.47 s, max 0.474 s. Partials were skipped while
  the model was busy (e.g. none for the silence stream), and every final ran.
- Device peak VRAM 2302 MiB (idle 12 MiB), the same as one stream: with the lock only one
  decode runs at a time, so the design §4.3 "+0.3–0.5 GB concurrent partials" estimate is not
  needed (design.md not changed here).
- Unbiased transcripts: "O jest test pierwszy.", "Ryszardzie, jaka będzie jutro pogoda?";
  noise/silence gave "Dziękuję." / "Dzięki za oglądanie!" / "Dziękuje za uwagę.", never
  "Ryszardzie". `make check`: 285 passed, 1 skipped.

## 2026-10-18: re-review fixes (PR #8, review 5564634105)

- Correction: the entry above that says "the §6 STT row" means design.md §5.
- Blocker: `close()` mid-partial released the model lock while the executor thread was still
  decoding, so the next final ran at the same time (2 concurrent calls, reproduced by the
  reviewer). Fix: `_run_model` acquires the lock, starts the thread, and releases the lock in
  the thread future's done callback; the awaiter awaits it through `asyncio.shield`, so
  cancelling the awaiter no longer releases the lock. The callback also retrieves the call's
  exception (logged at ERROR) when the awaiter is gone. Test: close-then-final keeps
  `max_active == 1`, and the final waits for the abandoned call.
- Blocker: the event bound assumed real-time audio; with slow audio and a lagging reader
  `put_nowait` raised `QueueFull` and `TextEnd` was lost. Fix: a partial that would take the
  last slot is dropped (best-effort, DEBUG log), so `TextEnd` always fits. Test with a 6-slot
  queue, 40 distinct partials and no reader.
- Tests added: the real `exit_process` in a subprocess with a wedged non-daemon thread (exit 70,
  CRITICAL logged), and a final waiting behind a wedged partial (fatal timeout, no deadlock).
- docs/tasks/README.md: T-010 without follow-up/busy (P1), T-011 events only (commands P1),
  T-014 ESPHome playback added.
