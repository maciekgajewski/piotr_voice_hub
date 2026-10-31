# Microphone Protocol

## Status and scope

- **Authority:** Normative
- **Audience:** Agents changing the satellite session, drivers, capture, playback, cues, visual output, or satellite firmware
- **Read when:** Working in `src/piotr_voice_hub/satellites/`, satellite configuration, satellite tests, or satellite firmware
- **Source:** copied from `piotr_the_ai_server` `docs/microphone-protocol.md` @ `a299643` and trimmed to the hub's single continuous (open-mic) listening mode (T-005)

This document defines the abstract protocol between the hub's per-satellite session (the "manager" below; `SatelliteSession`, design.md §4) and a satellite driver. It governs listening, captured speech segments, assistant playback, cues, visual state, correlation, failure, and recovery.

It does not govern the consumer WebSocket API (design.md §6), STT internals, speaker recognition internals, or device-specific ESPHome services.

Requirement identifiers use the `MP-` prefix.

## Ownership boundaries

- The manager MUST own arming, playback sequencing, cue sequencing, visual state while connected, retries, and recovery.
- A driver MUST implement commands only through the abstract `Microphone` interface.
- A driver MUST own device-specific connections, callbacks, audio transport, service names, bitmaps, and LED rendering.
- A driver MUST NOT emit consumer API events, perform STT, decide whether open-mic text is accepted, or re-arm itself implicitly. (`MP-OWNER-001`)
- Other server components MUST NOT branch on a concrete driver type or invoke concrete firmware services.
- Firmware MAY implement connection-loss fail-safe behavior that cannot be commanded by a disconnected server.

## Terminology

- **Listening generation:** One explicitly armed interval identified by `listen_id`.
- **Speech segment:** One bounded sequence of captured speech identified by `utterance_id`.
- **Playback stream:** One assistant audio stream identified by `playback_id`.
- **Cue:** One short semantic sound identified by `cue_id`.
- **Main visual state:** The device-wide state controlled by this protocol while connected.
- **Orthogonal indicator:** A hardware-local indication, such as mute or volume, that does not replace the main visual state.
- **Stale event:** An event whose correlation identifier is not active.

Names in event tables are protocol events. Uppercase names in state tables are protocol states or enum values as explicitly stated.

## Correlation identifiers

- Every `StartListening` command MUST contain a newly generated non-empty `listen_id`. (`MP-ID-001`)
- Every `SpeechStarted` MUST contain a newly generated non-empty `utterance_id` associated with the active `listen_id`. (`MP-ID-002`)
- Every playback stream MUST use a newly generated non-empty `playback_id`.
- Every cue MUST use a newly generated non-empty `cue_id`.
- Driver events MUST echo all applicable identifiers.
- An identifier MUST NOT be reused during the process lifetime. Identifier factories guarantee this (monotonic ids); the protocol validator checks reuse against the 1024 most recent identifiers only, so its memory stays bounded under continuous listening.
- A stale event MUST NOT mutate current state. A trusted driver emitting a stale event has violated the protocol. (`MP-ID-003`)

Identifiers make delayed callbacks observable and prevent an old ESPHome run from changing a newer listening generation.

## Enum values

### Listening

There is one listening mode, continuous open-mic: the driver detects zero or more speech segments (Silero VAD, design.md §4.1) and the manager performs server-side wake-phrase acceptance. `StartListening` and `ListeningStarted` therefore carry no mode. The hub's follow-up (design.md §4.1 step 7) is manager policy on top of this mode, not a driver mode.

### Main visual state

| Value | User meaning | Voice Preview | Box3 |
|---|---|---|---|
| `ERROR` | Device is not connected to the controlling server | Low-light red LEDs | Error bitmap |
| `IDLE` | Connected; no acknowledged user request is active | LEDs off | Idle bitmap |
| `LISTENING` | The system is actively accepting acknowledged user input | Pulsing blue LEDs | Listening bitmap |
| `PROCESSING` | An accepted request is being processed or played back | Pulsing white LEDs | Processing bitmap |

`ERROR` is a normative visual state but is not a valid argument to `SetVisualState`; it is firmware-owned while disconnected. (`MP-VISUAL-001`)

### Cue type

| Value | Meaning |
|---|---|
| `UTTERANCE_ACCEPTED` | Full utterance was accepted and will be processed |
| `FOLLOW_UP_READY` | Device is ready for follow-up speech |
| `TIMEOUT` | The reply timeout expired, or no request followed the wake word alone. Not the follow-up window: it ends silently (T-020) |

## Manager-to-driver events

| Event | Fields | Valid use |
|---|---|---|
| `StartListening` | `listen_id` | Arm one new listening generation while disarmed |
| `StopListening` | `listen_id`, `reason` | Stop the matching generation |
| `SetVisualState` | `state` | Set `IDLE`, `LISTENING`, or `PROCESSING` while connected |
| `ResetWakeCandidate` | `listen_id`, `utterance_id` | Clear device candidate UI after final rejection without stopping open-mic listening |
| `PlayCue` | `cue_id`, `cue_type` | Play one semantic cue while disarmed |
| `PlaybackBegin` | `playback_id`, `rate`, `width`, `channels`, optional `volume` | Begin one assistant playback stream while disarmed |
| `PlaybackChunk` | `playback_id`, `data` | Append bytes to the matching playback stream |
| `PlaybackEnd` | `playback_id` | Declare that no more bytes will be sent |
| `PlaybackUrl` | `playback_id`, `url` | Begin one whole playback the device fetches from `url` itself while disarmed; no chunks or `PlaybackEnd` follow (T-021) |
| `PlaybackStop` | `playback_id` | Stop the playback now, before or after `PlaybackEnd`; it still ends with one `PlaybackFinished` (T-019) |
| `Close` | none | Permanently release the driver |

`SetVisualState` is independent of audio state and is valid in every connected, non-closed driver state. Duplicate commands are idempotent. (`MP-VISUAL-002`)

## Driver-to-manager events

| Event | Fields | Meaning |
|---|---|---|
| `ListeningStarted` | `listen_id` | Device has entered the requested listening generation |
| `ListeningStopped` | `listen_id`, `reason` | Matching generation is no longer active |
| `SpeechStarted` | `listen_id`, `utterance_id`, `rate`, `width`, `channels` | One captured segment has begun |
| `AudioChunk` | `listen_id`, `utterance_id`, `data` | Captured audio for the matching segment |
| `AudioProgress` | `listen_id`, `utterance_id`, `chunks`, `bytes` | Segment liveness without an audio payload |
| `SpeechEnded` | `listen_id`, `utterance_id`, `reason` | Matching captured segment is complete |
| `CueFinished` | `cue_id` | Matching cue has finished |
| `PlaybackFinished` | `playback_id` | Matching playback stream has drained and finished |
| `MicrophoneUnavailable` | operation correlation fields, `reason` | Current requested operation cannot continue temporarily |
| `DriverClosed` | none | Driver is permanently closed |

`SpeechEnded.reason` is `completed` (the driver's VAD detected the end of speech) or `max_length` (the segment reached the configured maximum length; a new segment may start right after).

Capture events use `SpeechStarted` and `SpeechEnded`. Playback uses `PlaybackBegin`, `PlaybackChunk`, `PlaybackEnd`, and `PlaybackFinished`. The protocol MUST NOT overload `AudioStart` or `AudioEnd` across these boundaries. (`MP-EVENT-001`)

## Driver protocol states

### `DISARMED`

No listening, capture, cue, or playback operation is active.

### `ARMING`

The driver is executing `StartListening` and has not yet emitted `ListeningStarted`.

### `LISTENING`

The requested generation is active and no speech segment is currently open.

### `CAPTURING`

Exactly one speech segment is active.

### `STOPPING`

The driver is executing `StopListening` and has not yet emitted `ListeningStopped`.

### `PLAYING_CUE`

Exactly one cue is active.

### `PLAYING_AUDIO`

Exactly one assistant playback stream is active or draining.

### `CLOSED`

Terminal state.

## Driver transition table

The table covers audio-state events. `SetVisualState` is independently valid in every connected state except `CLOSED`.

| Current state | Command or driver result | Action | Next state |
|---|---|---|---|
| `DISARMED` | `StartListening` | Begin requested generation | `ARMING` |
| `DISARMED` | `PlayCue` | Begin semantic cue | `PLAYING_CUE` |
| `DISARMED` | `PlaybackBegin` | Open playback stream | `PLAYING_AUDIO` |
| `DISARMED` | `PlaybackUrl` | Start the device on the URL; input is already complete | `PLAYING_AUDIO` |
| `DISARMED` | `Close` | Release resources | `CLOSED` |
| `ARMING` | matching `ListeningStarted` | Record active generation | `LISTENING` |
| `ARMING` | matching `MicrophoneUnavailable` | Report failure | `DISARMED` |
| `ARMING` | matching `StopListening` | Cancel activation | `STOPPING` |
| `LISTENING` | `SpeechStarted` | Open one segment | `CAPTURING` |
| `LISTENING` | matching `StopListening` | Stop generation | `STOPPING` |
| `LISTENING` | matching `MicrophoneUnavailable` | Report failure | `DISARMED` |
| `CAPTURING` | matching `AudioChunk` | Forward audio | `CAPTURING` |
| `CAPTURING` | matching `AudioProgress` | Record liveness | `CAPTURING` |
| `CAPTURING` | matching `SpeechEnded` | Close segment | `LISTENING` |
| `CAPTURING` | matching `MicrophoneUnavailable` | Abort segment and generation | `DISARMED` |
| `STOPPING` | matching `ListeningStopped` | Clear generation | `DISARMED` |
| `STOPPING` | matching `MicrophoneUnavailable` | Clear failed generation | `DISARMED` |
| `PLAYING_CUE` | matching `CueFinished` | Complete cue | `DISARMED` |
| `PLAYING_CUE` | matching `MicrophoneUnavailable` | Abort cue | `DISARMED` |
| `PLAYING_AUDIO` | matching `PlaybackChunk` | Append audio | `PLAYING_AUDIO` |
| `PLAYING_AUDIO` | matching `PlaybackEnd` | Finish input and drain device | `PLAYING_AUDIO` |
| `PLAYING_AUDIO` | matching `PlaybackStop` | Stop device playback, release the stream | `PLAYING_AUDIO` |
| `PLAYING_AUDIO` | matching `PlaybackFinished` after `PlaybackEnd`, `PlaybackUrl` or `PlaybackStop` | Complete playback | `DISARMED` |
| `PLAYING_AUDIO` | matching `MicrophoneUnavailable` | Abort playback | `DISARMED` |
| any non-closed state | `Close` | Abort operation and release resources | `CLOSED` |
| any non-closed state | `DriverClosed` | Discard the active operation; the driver is permanently unavailable | `CLOSED` |
| `CLOSED` | any command or event | Inapplicable | `CLOSED` |

Any command or event not allowed by this table is an internal protocol violation. A nested `SpeechStarted`, mismatched identifier, audio outside `CAPTURING`, or implicit driver re-arm MUST fail an invariant. (`MP-STATE-001`)

For a device that streams capture through an independently running voice-assistant pipeline, handling `StopListening` MUST actively stop that device pipeline and wait for the device's stop notification before emitting `ListeningStopped`. A server-to-device run-end event alone MUST NOT be treated as a correlated stop acknowledgement. If the explicit stop notification does not arrive within a bounded implementation timeout, the driver MUST discard the stopped generation and recover the transport before completing stop. (`MP-STOP-001`)

After `StopListening`, the driver MUST drop any capture events (`SpeechStarted`, `AudioChunk`, `AudioProgress`, `SpeechEnded`) of the stopped generation that are still queued or arrive late, instead of delivering them; in `STOPPING` they would violate `MP-STATE-001`. Only `ListeningStopped` (or `MicrophoneUnavailable`) completes the stop. (`MP-STOP-002`)

## Listening requirements

- The driver MUST perform local speech gating but MUST NOT perform final wake-phrase acceptance.
- One listening generation MAY produce zero or more sequential speech segments.
- Ordinary silence while `LISTENING` is normal.
- Completing or rejecting one segment MUST NOT end the listening generation.
- The manager MUST explicitly stop the generation before cue or assistant playback.

## Main visual-state protocol

### Ownership and precedence

- When connected, the manager MUST explicitly command the main visual state. (`MP-VISUAL-003`)
- Drivers and firmware MUST NOT infer `IDLE`, `LISTENING`, or `PROCESSING` from listening, speech, cue, playback, or voice-assistant callbacks. (`MP-VISUAL-004`)
- Firmware MUST enter `ERROR` when its server-connection watchdog determines that the controlling server is unavailable.
- `ERROR` MUST override the last server-commanded state while disconnected.
- On reconnection, the device MUST retain `ERROR` until it receives the manager's first `SetVisualState`; the manager MUST send that command as part of connection initialization. (`MP-VISUAL-005`)
- Mute, setup, volume, and other hardware-local indicators MAY be shown orthogonally, but MUST NOT replace the main connected visual state. (`MP-VISUAL-006`)
- This protocol defines no `MUTED`, `SPEAKING`, or setup main visual state.

### Required transitions

| Situation | Required state |
|---|---|
| Connected and no accepted request active | `IDLE` |
| First server-side open-mic partial detects a wake candidate | `LISTENING` immediately |
| Open-mic candidate is rejected by final transcript | `IDLE` |
| Complete utterance is accepted | `PROCESSING` |
| Waiting for the consumer's reply (`speak`/`play`/`release`) | `PROCESSING` |
| Assistant audio is playing | `PROCESSING` |
| Playback finishes and no follow-up is requested | `IDLE` |
| Playback finishes and follow-up is requested, including while its ready cue plays (until the P2 firmware: no ready cue, the visual alone) | `LISTENING` |
| Reply timeout expires | `IDLE` after the timeout cue completes |
| Follow-up window expires, or ends on a segment with no request | `IDLE` at once, no cue, still listening |
| Server connection is lost | Firmware-owned `ERROR` |

`PROCESSING` MUST remain active throughout assistant playback. (`MP-VISUAL-007`)

## Open-mic acceptance protocol

Open-mic acceptance is manager-owned and has the following ordered internal milestones, which are not driver protocol events:

```text
SpeechStarted
-> private audio and partial STT
-> optional internal WakeCandidateDetected
-> SpeechEnded
-> internal FinalTranscriptProduced
-> final wake-phrase validation
-> Accepted or Rejected
```

### Partial candidate

- Partial transcript snapshots MUST remain private to the manager/STT boundary.
- Partial transcript text MAY be sent to consumers only as the post-phrase `transcript_partial` API event, after a wake candidate (design.md §6).
- Transcript content MUST NOT be logged at INFO; INFO logs remain metadata-only. Raw and preprocessed partial and final STT results MAY be logged at DEBUG. (`MP-OBS-003`)
- The first partial containing the configured wake phrase MUST mark the current `utterance_id` as a candidate and immediately send `SetVisualState(LISTENING)`. (`MP-OPENMIC-001`)
- Candidate visual feedback MUST occur while capture continues; it MUST NOT wait for `SpeechEnded` or final transcription.
- Duplicate positive partials for the same utterance MUST NOT repeat the transition or cue.
- Candidate detection MUST NOT play `UTTERANCE_ACCEPTED`.

### Final rejection

After `SpeechEnded`, the final transcript determines acceptance.

- If the final transcript lacks the wake phrase or usable non-empty text after it, the segment MUST be rejected.
- If a partial candidate had been displayed, rejection MUST send `ResetWakeCandidate` and `SetVisualState(IDLE)`. (`MP-OPENMIC-002`)
- Rejection MUST discard text and buffered speaker-recognition work.
- Rejection MUST NOT emit `transcript_final`, play an accepted cue, or stop the open-mic listening generation.

### Final acceptance

- Acceptance MUST occur only after final transcript production, wake-phrase validation, and usable suffix extraction. (`MP-OPENMIC-003`)
- Acceptance MUST send `SetVisualState(PROCESSING)`.
- The manager MUST stop the open-mic generation and receive `ListeningStopped` before playing `UTTERANCE_ACCEPTED` or assistant audio.
- Speaker recognition MAY use buffered audio but MUST be awaited only after acceptance.
- One accepted segment MUST be emitted exactly once as `transcript_final`.

## Cue and playback sequencing

- A half-duplex driver MUST be `DISARMED` before `PlayCue`, `PlaybackBegin` or `PlaybackUrl`. (`MP-AUDIO-001`)
- The manager MUST await `CueFinished` before starting a conflicting listening or playback operation.
- `PlaybackEnd` means no more bytes will be sent; `PlaybackFinished` means the device has drained the audio and playback is complete.
- `PlaybackUrl` is a whole playback: the device fetches the URL, and `PlaybackFinished` means its media player has played it and is no longer busy. The ESPHome driver sends it as an announcement once the player is not busy (as for a stream), takes the end from the player state (a change within 5 s, at least 0.5 s after the call, then not busy), and stops it past 10 minutes. A URL the device cannot fetch or play still ends in one `PlaybackFinished`, with no reason field: either it announces and goes idle at once (an INFO `playback finished … after 0.5 s`, like a very short sound) or it reports no change within 5 s (a WARNING; a slow https start can land here too). A failed command is `MicrophoneUnavailable` with no `PlaybackFinished`. (T-021)
- A URL does not end when the driver forgets it (a lost connection, a failure, `Close`): `Close` during a `PlaybackUrl` sends the media player `STOP`, and on every connect, before the first `StartListening`, a busy player is stopped and awaited, with a WARNING; bounded at about 6 s (up to 3 s for the player's first state, then up to 3 s for idle after the `STOP`; past either bound a WARNING and on). (T-021, PR #30 review B)
- The manager MUST NOT re-arm listening before `PlaybackFinished`.
- After `PlaybackStop` the manager sends no more `PlaybackChunk`, `PlaybackEnd` or `PlaybackStop` for that playback. If the drain had already finished, `PlaybackStop` does nothing and the queued `PlaybackFinished` is the one. Exactly one `PlaybackFinished` ends each playback either way.
- Visual `PROCESSING` remains active while playback for a reply drains.
- A driver MAY advertise full-duplex capability in a future interface revision, but no current behavior may assume it.

## Timeouts and cancellation

| Timeout | Starts | Successful completion | Failure meaning | Owner |
|---|---|---|---|---|
| Arm acknowledgement | `StartListening` | `ListeningStarted` | Device did not enter requested mode | Manager |
| Follow-up speech start | follow-up ready cue finished (none until the P2 firmware) and listening re-armed | `SpeechStarted` within `follow_up_seconds` | User did not begin follow-up | Manager |
| Reply | `transcript_final` emitted | consumer `speak`/`play`/`release` within `reply_timeout_seconds` | Consumer did not answer | Manager |
| Segment liveness | `SpeechStarted` or last segment event | next `AudioChunk`, `AudioProgress`, or `SpeechEnded` | Active capture stalled | Manager |
| Cue completion | `PlayCue` | `CueFinished` | Cue operation stalled | Manager |
| Playback completion | `PlaybackBegin` or last playback activity | `PlaybackFinished` | Playback stalled | Manager |
| URL playback start | `PlaybackUrl` sent to the device | a media player change within 5 s | Device did not start the URL: WARNING, `PlaybackFinished` | Driver |
| URL playback end | `PlaybackUrl` sent to the device | player not busy within 10 min | Endless or stuck: WARNING, stop, `PlaybackFinished` | Driver |
| URL playback (session) | `PlaybackUrl` | `PlaybackFinished` within 720 s | Driver stalled: treated as a satellite failure | Manager |
| Connection watchdog | last valid server connectivity evidence | server connectivity restored | Server unavailable | Firmware |

- Idle open-mic `LISTENING` has no speech-segment liveness timeout. (`MP-TIMEOUT-001`)
- A timeout MUST name the active operation and correlation identifier in logs.
- Timeout recovery MUST stop or discard the failed generation before creating another identifier.

## Failure and recovery

- A failed capture MUST NOT be converted into an empty accepted utterance.
- Recovery MUST NOT reuse a `listen_id`, `utterance_id`, `cue_id`, or `playback_id`.
- The manager MUST explicitly select the next visual state after a recoverable failure while connected.
- Open-mic rejection is a normal outcome, not device unavailability.
- Follow-up and reply timeouts are normal outcomes, not device unavailability.
- Connection loss activates firmware-owned `ERROR`; server reconnection begins with an explicit visual-state command.
- If an internal sequence violates state or correlation invariants, the manager MUST close and recreate the driver/session boundary rather than guessing recovery. (`MP-ERROR-001`)

## Normal sequences

### Open-mic rejection after an early candidate

```text
SetVisualState(IDLE)
StartListening(listen-2)
ListeningStarted(listen-2)
SpeechStarted(listen-2, utterance-2)
AudioChunk(...)*
partial STT detects wake candidate
SetVisualState(LISTENING)
AudioChunk(...)*
SpeechEnded(listen-2, utterance-2)
final transcript rejects candidate
ResetWakeCandidate(listen-2, utterance-2)
SetVisualState(IDLE)
```

### Accepted response and playback

```text
accepted utterance
SetVisualState(PROCESSING)
StopListening(active-listen-id, "utterance_accepted") if still armed
ListeningStopped(active-listen-id, "utterance_accepted")
PlayCue(UTTERANCE_ACCEPTED)
CueFinished
PlaybackBegin(playback-1, ...)
PlaybackChunk(playback-1, ...)*
PlaybackEnd(playback-1)
PlaybackFinished(playback-1)
SetVisualState(IDLE) or SetVisualState(LISTENING) for follow-up
```

## Invalid sequences

- `AudioChunk` before `SpeechStarted`;
- a second `SpeechStarted` while capturing;
- event identifiers that do not match the active operation;
- `PlaybackBegin` while listening is active on a half-duplex driver;
- implicit re-arm after capture or playback;
- treating open-mic idle silence as an active segment timeout;
- showing `LISTENING` for ordinary open-mic speech before a wake candidate exists;
- waiting until final transcription to show a detected candidate;
- accepting before final transcript validation;
- switching from `PROCESSING` to `IDLE` when playback begins;
- inferring the connected main visual from firmware voice-assistant callbacks;
- using `ERROR` to represent mute or a recoverable processing failure.

## Observability requirements

- Every command and driver event MUST be logged on DEBUG with microphone instance, protocol state, and all correlation identifiers. (`MP-OBS-001`)
- Listening, accepted utterance, cue, playback, unavailability, reconnection, and recovery are crucial events and MUST be logged briefly on INFO.
- Every main visual transition MUST be logged on DEBUG with old state, new state, and cause. (`MP-OBS-002`)
- Transcript text, partial, rejected or accepted, MUST be logged only at DEBUG (`MP-OBS-003`).
- Stale events and protocol violations MUST be logged with enough context to identify the old and active generations.

## Implementation and conformance references

Implementation:

- `src/piotr_voice_hub/satellites/messages.py`
- `src/piotr_voice_hub/satellites/interfaces.py`
- `src/piotr_voice_hub/satellites/types.py`
- `src/piotr_voice_hub/satellites/protocol.py` (`MicrophoneProtocolState`, the MP-* invariant asserts)
- the satellite session (P0, design.md §5) and the ESPHome driver (`satellites/esphome.py`, P0)
- satellite firmware (P2, design.md §8)

Conformance tests:

- `tests/test_satellite_protocol.py`
- `tests/microphone_driver_conformance.py` (reusable driver checks) and `tests/test_microphone_driver_conformance.py`

## Unresolved decisions

None. New listening modes, main visual states, full-duplex behavior, or device-inferred visual transitions require a design change before implementation.
