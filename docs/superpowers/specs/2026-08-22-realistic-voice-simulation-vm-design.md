# Realistic Voice Simulation VM Design

## Status

**Proposed.** A design under review; nothing here has been implemented.


## Purpose

LibreEcho needs a persistent, hardware-free environment that exercises realistic
voice and UI behavior without sharing state with other development VMs. The
environment must run the real LibreEcho web application and service daemons
wherever practical, replace only physical device boundaries, and make failures
repeatable and observable.

Reusable simulator and VM code belongs in the LibreEcho repositories. The
initial deployment target is a separately named persistent VM on the acceptance host.

## Evidence Boundary

A passing simulator run proves integration among the packaged LibreEcho UI,
HTTP API, configuration store, daemon sockets, button handling, wake-word
engine, voice orchestration, STT/TTS adapters, audio routing, and LED state
contracts used by the test profile.

It does not prove MT8163 electrical behavior, physical microphone arrays,
speaker acoustics, codec registers, FPGA capture, actual button GPIOs, or
real-room wake-word performance. Those remain hardware acceptance concerns.

## Relationship to the existing harnesses

`LibreEcho-UI` already contains two things that overlap this design, and a third
independent simulator would be a mistake. Neither is deprecated by this work.

**`tests/voice-e2e` is extended, not replaced.** It already drives the real
pipeline off-device from the correct boundary -- a fake `tinycap` producing
nine-channel `S24_3LE` into the real `micd`, then real `waked`, `agentd` and
`sttd`, with a mock audio adapter capturing what `ttsd` would have been asked to
say. That is precisely the capture-boundary injection this design needs, and the
VM reuses the mechanism rather than reimplementing it. What the VM adds is what
that harness deliberately does not have: buttons, LED and speaker observers, a
UI, fault injection, and long-running scenarios against a deployed image.

**`tools/virtual_echo.py` is kept for what it is good at.** It simulates the
web and service layer in isolation, with no audio path, which makes it far
faster than a VM for UI and API work. It stays the tool for that, and this
design must not absorb it or duplicate its scope.

**This design owns only the gap between them**: a deployed release image, in a
VM, with virtual hardware attached. If a scenario can be proved by
`tests/voice-e2e` or by `virtual_echo.py`, it belongs there instead -- both are
faster, and neither needs a VM to run.

## Repository Ownership

- `LibreEcho-UI` owns the simulator daemon, virtual-device protocols,
  simulation-only HTTP API and UI, Playwright fixtures, voice corpus metadata,
  and end-to-end assertions.
- `LibreEcho-platform` owns release-image extraction, VM/container assembly,
  persistent-VM lifecycle scripts, service startup, port allocation,
  state snapshots, and artifact export.
- `LibreEcho` records product-level design and implementation plans.
- Kernel repositories and `amonet-k32` require no changes for the first
  implementation.

Implementation must start from current upstream branches or isolated worktrees.
It must not modify the unrelated dirty kernel files in the current workspace or
reuse VM resources belonging to another agent.

## Architecture

The simulator uses a hybrid full-stack model. Real LibreEcho daemons and their
normal socket protocols remain in the path. A simulation-only daemon,
`libreecho-simd`, replaces physical inputs and observes physical outputs:

```text
Playwright or Simulation UI
             |
     authenticated simulation API
             |
        libreecho-simd
       /       |       \
button input  PCM I/O  state/fault journal
       \       |       /
 real LibreEcho daemon sockets
 buttond, micd, waked, sttd, agentd, ttsd, audiod, ledd, web
```

Simulated actions must enter at the narrowest existing hardware boundary. A
test may not report a successful voice turn by directly setting UI state,
posting a fabricated transcript to the web API, or bypassing wake detection
when the scenario claims to test wake detection.

## Runtime Profiles

The VM supports two explicit profiles:

### Deterministic

The real UI, API, configuration, daemon orchestration, socket framing, state
machines, and audio routing run normally. Fixed STT, assistant, and TTS engines
produce repeatable results. Tests use versioned PCM/WAV fixtures. This profile
is required for CI and for diagnosing control-flow regressions.

### Real Model

The Alexa-compatible wake model, selected STT model, configured assistant
endpoint, and selected TTS model run normally. Assertions allow explicitly
defined transcript and latency tolerances. This profile is an opt-in acceptance
suite on the acceptance host, not a required fast presubmit check.

Both profiles must identify themselves in test results and the UI. A result may
not silently substitute a deterministic engine for a requested real engine.

## Simulation Safety

Simulation capability is deny-by-default:

- `libreecho-web` registers simulation routes only when started with explicit
  simulation configuration and after connecting to `libreecho-simd`.
- Production mode returns `404` for every simulation route and omits the
  Simulation navigation item and assets.
- The simulation API requires normal authentication and CSRF protection.
- The VM binds the control interface to loopback by default. Remote access on
  the acceptance host occurs through an explicit SSH tunnel or equivalently restricted
  transport.
- Audio uploads accept only declared PCM/WAV formats, enforce duration and byte
  limits, and are stored beneath the current run directory.
- The page displays a persistent `SIMULATION` banner and the active profile.
- Simulation state and production configuration are stored separately.

## Simulator Components

### Control and Event Journal

Every run receives an opaque run identifier. `libreecho-simd` serializes
commands, assigns monotonic sequence numbers, records monotonic and wall-clock
timestamps, and publishes structured events. Tests wait for events rather than
fixed sleeps.

The minimum event vocabulary is:

- `run.started`, `run.reset`, and `run.finished`;
- `button.pressed` and `button.released`;
- `microphone.started`, `microphone.chunk`, and `microphone.finished`;
- `wake.detected`, `listening.started`, and `listening.finished`;
- `stt.transcript`, `assistant.response`, and `tts.started`/`tts.finished`;
- `speaker.started`, `speaker.chunk`, `speaker.silent`, and `speaker.stopped`;
- `led.changed`;
- `service.started`, `service.stopped`, and `service.failed`;
- `fault.enabled` and `fault.cleared`.

Events contain the originating component, run identifier, sequence number, and
scenario correlation identifier. Secrets, bearer tokens, and assistant API
keys must never appear in events or artifacts.

### Virtual Buttons

The initial button set is Action, Volume Up, Volume Down, Microphone Mute, and
any playback button represented by the deployed hardware/service contract.
Press duration is controllable. The simulator supports overlapping presses and
rapid repeats so debounce behavior can be tested.

**Injection is via `uinput`.** `buttond` has no input socket and takes no
injected events; it opens evdev devices, filters them by advertised key bits,
and reads `EV_KEY`. The only way for a simulated press to traverse the real
daemon path is therefore to create a `uinput` device advertising the relevant
key bits and emit `EV_KEY` on it, so `buttond` discovers and reads it exactly as
it does the hardware. A simulator that instead calls the HTTP API, or writes to
a socket added for testing, is not exercising the button path and must not
claim to.

The Action button is `KEY_HELP` (`0x8a`), which is what the vendor keypad map
calls it. `buttond` gained that contract in LibreEcho-UI #143; before that it
handled only volume and mute, and this document's earlier claim that Action
"enters the same button-daemon path used by physical input" was not true when
it was written. The `uinput` device must advertise `KEY_HELP` for the Action
button to be discovered at all, because `buttond` filters candidate devices on
their key bits.

### Virtual Microphone

The production microphone boundary is **nine-channel `S24_3LE` at 16 kHz**,
which `micd` then unpacks, calibrates, beamforms, high-pass filters at 80 Hz and
applies digital gain to. Mono 16-bit PCM injected downstream of that skips every
one of those stages, so a scenario built on it cannot make any claim about the
audio front end -- including the wake word, whose input is the beamformer's
output.

The simulator therefore has **two injection points, and they are not
interchangeable**:

1. **Capture boundary (default).** Inject nine-channel `S24_3LE` through the
   existing `micd --capture-bin` seam, the same mechanism
   `LibreEcho-UI/tests/voice-e2e` already uses with its fake `tinycap`. This is
   the real path: unpack, calibration, beamforming, HPF and gain all run.
   Scenarios that assert anything about wake detection, endpointing or audio
   quality must use it.
2. **Mono orchestration shim.** Mono 16 kHz S16 PCM injected above the front
   end, for scenarios about sequencing, timeouts, LED ownership and UI state
   where the audio content is irrelevant. Results from this path must be
   labelled *orchestration only* in the journal and in any report, and must
   never be presented as front-end evidence.

WAV input is validated and converted before streaming. Injection uses timed
chunks and configurable gain rather than writing the entire utterance at once.
Scenarios may insert silence, split the wake word and command across chunks, add
versioned background-noise fixtures, or mix competing speech.

The first milestone uses deterministic audio files and generated phrases.
Live browser microphone streaming is a later feature because browser
permissions, host devices, resampling, and clock drift would weaken initial
repeatability.

### Speaker and LED Observers

Speaker output is captured as timestamped PCM and summarized with duration,
peak, RMS, first-audio latency, and transition-to-silence time. Tests do not
claim semantic TTS correctness solely from non-empty audio; deterministic mode
also checks the expected synthesis request.

LED observations record timestamped logical pattern, ownership, brightness,
and color state. The simulator does not pretend to validate physical LEDs.

### Fault Controls

Initial faults cover unavailable daemon sockets, delayed responses, malformed
audio, truncated audio, STT timeout, assistant timeout, TTS failure, and service
restart. Every fault is scoped to a run and cleared by reset.

## Simulation UI

An emulation-only Simulation page provides:

- virtual button press/release controls and press duration;
- selection and upload of voice fixtures;
- phrase generation, gain, pause, and noise controls;
- stream start, stop, and reset;
- active pipeline stage and latency display;
- speaker, LED, button, and service-state observations;
- fault toggles;
- event timeline;
- artifact download.

All controls expose stable semantic identifiers for Playwright. The UI calls
the same simulation API available to tests and does not implement a second
control path.

## Voice Corpus

The first acceptance corpus covers these twenty product-priority intents:

1. Current weather.
2. Weather forecast.
3. Current time.
4. Current date or day.
5. Set a timer.
6. Cancel a timer.
7. Set an alarm.
8. Cancel an alarm.
9. Set a reminder.
10. Add an item to the shopping list.
11. Read the shopping list.
12. Play music.
13. Pause or resume playback.
14. Select the next or previous track.
15. Set volume.
16. Mute or unmute.
17. Turn a smart-home device on or off.
18. Adjust light brightness.
19. Ask a factual question.
20. Stop or cancel current output.

Each corpus entry declares expected wake behavior, acceptable transcripts,
expected intent/result, state transitions, timeout, and whether external
services are permitted.

The variation dimensions are:

- no deliberate wake-to-command pause;
- 250 ms, 500 ms, 1 second, 2 seconds, and configured listening-boundary
  pauses;
- wake and command in one audio chunk or separate chunks;
- fast, normal, and slow speech;
- quiet, nominal, and loud input levels;
- multiple versioned speakers or accents where licensed fixtures exist;
- clean room, background noise, music, and competing speech;
- filler and false starts;
- self-correction;
- repeated wake word;
- wake word followed by silence;
- command without a wake word;
- acoustically similar non-wake phrases;
- barge-in while output is active.

Fast CI runs one canonical recording for every intent and the complete pause
matrix for a critical subset. The persistent acceptance run may
execute the complete compatible speaker, pause, gain, and noise matrix.

## Global Stop Contract

“Alexa, stop” is a global, high-priority cancellation intent. It must stop
music or other media, interrupt TTS and announcements, stop generated sleep
noise, terminate an active assistant response stream, clear queued speech, and
return listening, LED, and UI state to idle. It must not change volume,
unrelated configuration, or persisted playback preferences.

The command must work during output playback through the real barge-in and echo
handling path. Tests cover no pause, multiple pauses, an already-open listening
window, repeated commands, quiet speech over loud output, every supported
output source, idle invocation, and negative wording such as “Alexa, don’t
stop.”

Acceptance requires captured speaker output to reach the defined silence
threshold within the configured stop-latency budget and remain silent for the
observation window. Clearing only UI state while audio continues is a failure.

## Initial Vertical Slice

The first implementation proves two scenarios before expanding the corpus:

1. Press virtual Action, observe the real button path, listening transition,
   LED ownership transition, timeout, and return to idle.
2. Start simulated playback, inject a versioned “Alexa, stop” fixture through
   the virtual microphone, observe real wake and cancellation paths, and prove
   speaker silence plus idle UI/LED state.

If real-model inputs are unavailable during initial development, scenario two
may first pass in deterministic profile, but completion requires an explicit
real-model run on the acceptance host.

## Persistent Acceptance VM

The Codex-owned instance is named `libreecho-codex-voice`. It uses a dedicated
state directory, disk or overlay, PID file, QEMU monitor socket, lock,
dynamically allocated forwarded ports, test-artifact directory, and service
identity. Lifecycle commands operate only after validating this ownership
metadata.

The launcher must never use broad process termination, generic fixed ports,
another VM's disk, or shared writable state. The VM persists between sessions,
while each test resets simulator state and restores the configured clean data
snapshot. Expensive base images and model assets may be cached separately from
mutable VM state.

## Artifacts and Results

Each scenario produces:

- scenario metadata and simulator profile;
- exact input fixture hashes and timing parameters;
- structured event timeline;
- service logs with secrets redacted;
- Playwright trace and failure screenshots;
- input and captured output PCM/WAV;
- transcript and assistant result;
- wake, listening, STT, response, first-audio, stop, and recovery latencies;
- final configuration and logical device state;
- pass, fail, or explicit skip reason for every assertion.

Artifacts are retained on failure and exportable from the acceptance host. Successful
high-volume matrix runs may apply a documented retention limit while preserving
the result summary and fixture hashes.

## Test Layers

1. Unit tests validate audio conversion, event ordering, state reset, payload
   limits, redaction, and ownership checks.
2. Service integration tests connect simulated devices to individual real
   daemons and validate socket contracts.
3. Deterministic Playwright scenarios drive the Simulation UI and assert the
   complete observable service flow.
4. Real-model `the acceptance host` acceptance scenarios exercise wake, STT, assistant, TTS,
   barge-in, and the voice corpus with declared tolerances.
5. Existing full-system OTA and initial-install VMs remain separate evidence
   tiers. The voice simulator does not replace their storage and boot coverage.

## Success Criteria

The initial feature is complete when:

- production mode has no reachable simulation page or API;
- the persistent isolated acceptance VM can be built and started from committed
  repository scripts;
- simulator reset makes repeated scenarios independent;
- the Action-button scenario passes through real daemon interfaces;
- “Alexa, stop” interrupts simulated active playback and produces captured
  silence within the declared budget;
- deterministic results are repeatable across three consecutive clean resets;
- at least one real-model wake-and-stop run passes on the acceptance host;
- failures retain sufficient artifacts to locate the failing stage without
  rerunning interactively;
- existing LibreEcho-UI unit, contract, and Playwright suites continue to pass.

Expansion to the complete twenty-intent variation matrix follows after this
vertical slice establishes the virtual-device and observation contracts.
