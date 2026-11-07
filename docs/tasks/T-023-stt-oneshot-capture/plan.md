# T-023: one-shot STT capture on voice-pe-03

**Status:** PR #34, preparation only. `at` is not installed; cleanup is unsatisfied.
Not capture-ready, not for merge, not for deploy. Production stays `cd8c36a`.

## Goal

Answer whether a scratch client can read the PCM submitted to STT and the Whisper segments
from before transcript cleanup through the API that `cd8c36a` already serves. It cannot.
Prepare the smallest default-off way to keep **one** follow-up final on `voice-pe-03`, plus
a client that ends that accepted utterance with the existing `release` command.

## Verified (code at `cd8c36a` and `main`; those files do not differ)

- `FasterWhisperTranscriber.transcribe` (`src/piotr_voice_hub/stt/whisper.py`) calls
  faster-whisper with `language="pl"` and `beam_size` only, joins `segment.text.strip()`,
  and drops the segment objects and the info object.
- `FasterWhisperStreamingSttSession._transcribe` then runs `TranscriptPreprocessor`
  (bracket notes, whitespace, symbol-only to `""`, five spelling replacements) and returns
  only the cleaned string. The joined string is logged at DEBUG as `raw` and is not kept.
- The final PCM is the in-memory buffer `bytes(self._audio)` (16 kHz, 16-bit mono, including
  the driver's pre-roll). Nothing writes it.
- `transcript_final` (`hub/api.py`) carries `text`, `raw_text`, `follow_up`, `speaker`,
  `audio_ms`. `raw_text` is that cleaned string (session concatenates segments after
  cleanup). There is no PCM and no pre-cleanup segment list.
- Commands are `speak`, `play`, `stop`, `release` (`hub/commands.py`). `release` ends
  `processing` without TTS and without the reply-timeout cue.

The Nov 7 cause is not established. This task does not claim a hallucination or a fixed phrase.

## Approach

Optional config `stt_capture`, absent by default. `enabled: false` does not touch the disk.
`enabled: true` is only `voice-pe-03`, and only if `/stt-oneshot` already exists mode 0700.
A bad path disarms capture and leaves the hub up. `ARM` (mode 0600, age ≤ 120 s) names one
playback id. Only that follow-up final is written: PCM (≤ 640000 bytes), `raw_joined`,
`cleaned_text`, and ids. No scores. The hub does not delete the directory.

The host `at` job `rm -rf -- <directory>` is the deleter, including partials. The capture
client checks `at -c` and the `atq` deadline and does not write `ARM` if that check is
uncertain. `at` is not installed; that prerequisite is unsatisfied. Teardown deletes the
directory before `atrm`. The play client sends one `play` and ignores transcripts. The
capture client connects first, then arms, and `release`s an accepted transcript without
playing. Empty `cleaned_text` has no `transcript_final`.

## Out of scope

Public API fields, continuous recording, other satellites, partial-window audio, replaying a
URL, deploying, and reading `~/probe` evidence.

## Acceptance

- Tests: default-off, wrong satellite, overflow, arm age, repeated finals, cancel, client
  disconnect, no `speak`/`play`, `release` only.
- `make check`.
- Manual checklist in the PR, for a later run Maciej approves. No hardware in this task.
