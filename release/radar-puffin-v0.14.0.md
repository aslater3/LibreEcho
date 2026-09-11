# LibreEcho radar-puffin v0.14.0

**UNRELEASED — coordinated candidate; hardware acceptance and publication remain on hold.**

This release combines the 0.14 feature line with maintenance fixes carried by
0.13.15. The required upgrade acceptance baseline is 0.13.15. This document is
not evidence that an image has been installed, confirmed, or published.

## Candidate scope

The candidate retains 0.14's Local LLM endpoint/model controls, weather settings,
physical button and privacy integration, and USB role-switch support. It carries
forward the signed OTA v2 transaction and verified provenance implementation
rather than reverting those paths to the older boot-only updater.

### Maintenance fixes carried forward

- Factory IDME Wi-Fi identity, so a reboot does not generate a new fallback MAC
  (Linux #30, ported by #31).
- Atomic vendor revision matching, the additional approved firmware set, and
  explicit owner acceptance of a structurally compatible unknown set during
  setup (Platform #133/#134 and UI #227; ports #135/#229). Acceptance remains
  owner-local; malformed inputs are not implicitly trusted or redistributed.
- Required Wyoming artifact metadata for Home Assistant discovery (UI #230).
- AirPlay writable support mounts when the feature transaction has already
  mounted a payload (UI #231), including propagation of support-mount failures
  and recovery from partial support mounts.
- Home Assistant voice-ownership explanation in the effective integrations
  renderer (UI #236 and the subsequent #237 correction). The local controls
  remain available when local mode owns voice.
- OTA v2 as the manual release-dispatch default, with the candidate version
  derived from the release branch (Product #158). Explicit v1 bridges and
  non-dispatch/local v1 fallback remain available.

### 0.14 readiness corrections

Local spoken stop requests are handled deterministically before model calls.
Ringing timers retain priority; local radio, noise and queued speech can be
stopped without destroying the speech worker or opening another follow-up turn.
Phone-owned AirPlay/Bluetooth transport must still be stopped on the sender;
LibreEcho explains that boundary instead of claiming it has stopped the phone.

Wake-word diagnostics distinguish loaded models from live capture and inference.
Missing or stale telemetry is degraded, silence is not a capture failure, and
intentional disable/mute has a separate state. Playback-attributed wake peaks
may use one supporting frame while idle detection retains two; threshold, VAD
and lockout checks remain. Real interruption and false-activation acceptance is
mandatory before this policy is described as hardware validated. Wake builds
include the ONNX Runtime nsync static-link dependency.

Invalid persisted button actions return to their validated defaults. The full
UI source suite runs automatically for 0.14 PRs, in addition to browser and
signed-provenance integration checks. Product's same-owner coordinated PR lane
resolves the sibling candidates once and uses the identical source SHAs for
contract checks and image construction. Release publication never uses those
unmerged fix refs.

The Platform DTB verifier accepts peripheral-only legacy images and 0.14's
device-capable OTG mode, while still rejecting host-only mode and retaining
clock, audio, pin, key, USB-controller and interrupt checks. Image verification
also retains the boot-time device-role policy required for recovery ADB.

## Release gates

The owning changes are Product #156, Platform #135, UI #229 and Linux #31. Merge
approval is separate from implementation and host verification. Record the
final four source SHAs and artifact hashes from the successful coordinated
build; a green result on an earlier source set does not validate a later one.

Before stable publication, require:

1. The complete Product and UI suites, Platform packaging/DTB checks, ARM32
   kernel build and all feature payload builds on the exact coordinated set.
2. A normal signed 0.13.15-to-0.14.0 OTA update, candidate health confirmation,
   committed reboot, retained configuration/features and controlled fallback.
   Do not force confirmation or remove transaction evidence to pass the gate.
3. Fresh setup for both approved vendor sets and an explicitly accepted unknown
   compatible set; malformed/changed inputs must remain rejected.
4. AirPlay activation on pre-mounted and normal boots; real Home Assistant
   satellite addition and local/HA mode switching; local spoken stop, timer
   priority and continued speech after cancellation.
5. Warm/cold boot network identity, physical microphone privacy/LED state,
   Bluetooth/audio, USB recovery-device operation and deliberate role changes.
6. Normally booted wakeword payload testing during playback, including missed
   interruptions and false activations, with capture/reference flow verified.

Host fixtures do not establish any of these hardware outcomes. Rebuild the
assistant and wakeword feature payloads: replacing only boot-resident copies
would not deploy their daemon changes. No new device model support is claimed.
