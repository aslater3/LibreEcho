# Home Assistant ESPHome migration — LibreEcho 0.14.0

## Accepted scope

This implements the owner's corrected decision: ship the ESPHome native-API
satellite in **0.14.0**, with no phased 0.15/0.16 compatibility rollout. There
are no existing external HA users requiring a legacy satellite mode.

- Home Assistant uses `libreecho-esphomed` on TCP 6053, discovered as
  `_esphomelib._tcp`.
- Retire the **Wyoming satellite**: `libreecho-wyomingd`, its init/service
  definitions, `_wyoming._tcp` publication and port 10700 health checks.
- **Retain Local and Custom voice operation and the custom web UI.** The
  Wyoming STT/TTS clients connecting to remote Whisper/Piper are independent
  engines, not the retired HA satellite, and remain supported.
- Preserve saved Local/Custom engine, endpoint, credential and wake-word
  settings when entering HA mode. Leaving HA restores that configuration.
- Do not add a Wyoming/ESPHome selector, deprecation banner, delayed removal,
  or compatibility-only configuration machinery.
- Keep integration bit 1 as HA enabled. The satellite implementation is
  ESPHome; stale/missing legacy protocol settings must not select Wyoming.

The earlier September 30 plan's 0.15/0.16 rollout and backward-compatible
satellite-selector tasks are superseded by this scope. The low-level protocol,
bounded-resource design and validation ladder remain applicable.

## Ownership and branches

All owning repositories use purpose-named `feature/ha-esphome-014` branches
cut from verified `release/0.14.0` heads. Protected release refs and unrelated
worktrees are not edited. Implementation, local validation, remote publication,
merge, image deployment and physical acceptance are separate states.

| Owner | Responsibility |
| --- | --- |
| LibreEcho-UI | Native protocol/daemon, discovery, voice/audio/control adapters, mode switching, web UI, API/schema, tests |
| LibreEcho-Platform | UI bundle and image closure, startup/reconciliation, independent verifier, OTA health, runtime packaging tests |
| LibreEcho | This scope and coordinated release/acceptance documentation |

No kernel change is authorized by this migration. Existing capture and MAC
hardware prerequisites must be evaluated separately, not declared fixed by
new userspace tests.

## Implementation contracts

### Native protocol and daemon

Use the upstream ESPHome schema at a pinned revision and the Python client
version pinned by the selected Home Assistant test release. Verify message IDs,
field numbers, enum values and client handshake behavior from those bytes;
do not copy unverified values from the older plan.

The satellite remains C99 with a bounded `poll()` event loop, fixed buffers,
a 64 KiB maximum protobuf frame, at most two API clients, bounded network and
playback deadlines, and explicit connection/pipeline cleanup. Unknown protobuf
fields are skipped safely; malformed, overlong and oversized data are rejected.

Implement and exercise:

1. Hello, disconnect, ping, device information and entity/state enumeration.
2. Locally detected wake, post-AEC 16 kHz PCM with detection-relative pre-roll,
   HA voice request/response/events, overlap rejection, pipeline watchdog,
   cleanup and continued conversation.
3. WAV/MP3 TTS and announcements through the existing audiod voice bus, music
   ducking, bounded HTTP(S) fetching and completion/error reporting.
4. Media-player commands through the existing radiod/audiod control path,
   advertising only formats and operations actually supported.
5. HA-owned remote timers, without a second local expiry scheduler, plus
   ringing and stop behavior.
6. Wake-word configuration, persisted active selection and actual waked reload.
7. Soft mute reflecting actual state, without overriding the hardware latch.
8. ESPHome Noise transport and key provisioning/persistence, using the
   existing pinned mbedTLS crypto stack. Never claim encrypted support from a
   plaintext-only test. Once a key is configured, reject plaintext access.

Advertise only implemented and tested capabilities. A discovered device or
successful handshake is not a completed voice satellite.

### Discovery and runtime ownership

`mdnsd` accepts the live ESPHome satellite's authenticated lease, renders
bounded/escaped `_esphomelib._tcp` TXT data, withdraws records when the owner
exits and removes stale Wyoming records. AirPlay remains independently owned
and must neither enable nor disable HA discovery.

DeviceInfo and discovery must agree on a stable identity. Do not invent a
random MAC at each start or replace the reviewed factory/configured MAC path.

HA transitions are transactional: stop the competing voice owner, bring up
the selected graph, check readiness and restore the previous graph/config if
startup fails. An installed/listening satellite is not necessarily connected
to HA; expose that distinction honestly in the API and UI.

### Custom UI and configuration preservation

The existing control centre and Local/Custom choices remain. HA delegates
conversation to HA over ESPHome; Custom still uses the selected local or
remote STT, assistant and TTS engines, including Wyoming clients.

Noise keys are validated 32-byte base64 values, atomically stored with mode
0600, included in intentional authenticated backups, and excluded from normal
API responses, diagnostics, logs and public evidence. Any admin reveal or
rotation endpoint retains authentication, Origin and mutation-CSRF checks.

### Platform shipping and OTA safety

The daemon must close every image layer: build/link, bundle validation and
staging, init installation, image builder, independent verifier, startup graph,
watchdog and feature reconciliation. Removing the old satellite must not
remove Wyoming client binaries or their shared transport library.

With HA enabled, OTA health requires the **actual ESPHome daemon** and its
6053 listener. Wyoming's absence must not block health confirmation; an
unrelated process listening on the right port must not satisfy it. In Local
and Custom mode, their appropriate existing graph remains the health contract.

## Verification and completion gates

Use test-first development and isolate host tests from real device paths and
host service state. Concurrent workers have disjoint file ownership; shared
fixed-path suites run serially. Review the combined final bytes, not only
individual worker summaries.

| Gate | Required evidence |
| --- | --- |
| Codec | Golden vectors from the pinned client, unknown fields, malformed/truncated/oversized frames and bounds |
| Client interoperability | Real pinned aioesphomeapi connects, enumerates, subscribes and drives the supported protocol messages |
| Noise | Correct/wrong key, provisioning, reconnect/restart persistence and plaintext refusal after provisioning |
| Voice/playback | Scripted wake/turn, exact pre-roll/audio output, URL error/deadline cases, continuation and watchdog cleanup |
| Controls | Real adapter fixture calls and actual reported states for media, timer, wake selection and mute latch |
| Local/Custom preservation | HA enable/disable and failed-start rollback retain saved engines/endpoints/settings and restore one owner |
| Discovery | Service/TXT rendering, authenticated owner, owner death/withdrawal, stale record cleanup, independent AirPlay |
| Platform | Build/stage/verifier closure, ESPHome-only HA startup and OTA listener/pid ownership tests |
| Review | Spec compliance, then security/correctness review of the integrated changes; focused failures resolved |
| Integration | Applicable repository checks plus isolated HA-in-the-loop validation, with exact commands and source identity |
| Hardware | Separate signed-image acceptance; never inferred from host fixtures |

## Remaining physical acceptance

Before presenting 0.14 ESPHome as hardware-qualified, require the recorded
capture-after-TTS gate (Product #104) and stable MAC validation (Product #125).
Then check discovery/adoption, repeated wake/reply turns, timers and physical
stop, announcement ducking, media playback, continued conversation, hardware
mute behavior, HA/network recovery, Local/Custom ↔ HA switching without reboot,
ESPHome-mode OTA confirmation and a bounded-memory soak.

Local source implementation does not authorize remote pushes, PRs, merging,
release publication, image installation, reboot, slot confirmation or hardware
changes. Those retain explicit authorization and their own evidence gates.
