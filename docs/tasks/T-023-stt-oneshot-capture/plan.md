# T-023: one-shot STT capture on voice-pe-03

**Status:** PR #34 open. Not for merge until Maciej accepts the design note and a later,
separate trigger. Production stays `cd8c36a`. This branch does not deploy, open a listener,
play audio, or read the piotr evidence files.

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
`enabled: true` is allowed only for satellite `voice-pe-03`. The hub stays disarmed until a
mode-0600 `ARM` file appears. The next follow-up **final** on that satellite only, within
120 s, writes one `.pcm` and one `.json` (mode 0600, at most 640000 PCM bytes) and removes
the arm. Overflow, a stale arm, a write error, cancellation, and hub shutdown disarm and
leave no partial audio. Files are removed 1800 s after a successful write.

The client is opt-in (`--arm` and `--confirm-one-shot`), refuses any other satellite, never
sends `speak` or `play`, and on one `transcript_final` with `follow_up: true` sends
`release`.

## Out of scope

Public API fields, continuous recording, other satellites, partial-window audio, replaying a
URL, deploying, and reading `~/probe` evidence.

## Acceptance

- Tests: default-off, wrong satellite, overflow, arm age, repeated finals, cancel, client
  disconnect, no `speak`/`play`, `release` only.
- `make check`.
- Manual checklist in the PR, for a later run Maciej approves. No hardware in this task.
