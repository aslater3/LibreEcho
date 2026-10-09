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

### Home Assistant: ESPHome satellite and retained Custom voice

The 0.14 feature migration replaces the Wyoming **satellite** with a native
ESPHome API satellite. Home Assistant adopts it through the ESPHome integration;
Local and Custom voice modes and the existing custom web control centre remain.
Custom may still select Wyoming STT/TTS clients for remote Whisper/Piper: those
clients are not the retired HA satellite.

The migration has no legacy satellite selector or phased 0.15/0.16 rollout.
It includes native discovery, voice/media/announcement controls, encrypted API
transport, and coordinated startup, packaging and OTA-health changes. See the
[accepted implementation scope](../docs/plans/ha-esphome-0.14.md) for the
contracts and verification gates. This describes the feature branch's scope,
not completed image or hardware qualification.

Before publication, independently verify repeated capture after TTS (Product
#104), stable identity (Product #125), actual ESPHome adoption and turns,
Local/Custom restoration, and ESPHome-mode OTA confirmation. Host protocol or
packaging checks alone do not establish these outcomes.

### Weekly active-device count and privacy choices

0.14 adds an anonymous weekly ping to `stats.libreecho.org`, purely to count
how many devices are running and on which version. It carries only hardware
model, version, channel, development build hash, and the week/month/year; no
device ID, serial, MAC or install date. It cannot be turned off and is
disclosed in setup and on the Privacy page, which shows the exact body sent.
Setup now offers health/usage and crash-report consent, ticked by default for
new setups; upgrades keep existing choices. 0.14 sends neither yet. See
[Privacy and telemetry](../docs/privacy.md).

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
mandatory before this policy is described as hardware validated. The wake link
recipe and reduced-dependency cache match the pinned ONNX Runtime archive set,
rather than requiring an nsync library that the pinned revision does not produce.

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

### Home Assistant discovery no longer requires an AirPlay installation

Home Assistant and Wyoming discovery no longer require an AirPlay payload to be
installed. The 0.14 candidate builds one boot-contained Avahi/D-Bus discovery
runtime independently of AirPlay feature selection: Product acquires the exact
ARMHF package versions named by the checked-in package lock from the public
Ubuntu ports archive, records each archive SHA-256 with its package, version,
source and architecture, builds the runtime outside the feature-enabled
conditions, and passes the verified runtime, its manifest identity and its
provenance record into the recovery-image contract. The corresponding-source and
distribution-notice closure is a fail-closed gate, so an unproven source offer
can never be recorded as verified. Avahi/D-Bus bytes retained inside
compatibility-preserving AirPlay payloads stay inert on a shared-runtime image;
they are deliberately retained for rollback and are not a second responder.

Discovery ownership, boot ordering and health for the shared runtime also depend
on the coordinated LibreEcho-UI and LibreEcho-Platform changes, and the shared
runtime has not yet been included in an integrated image build. Treat this as a
source and hosted-build correction: HA discovery on a no-AirPlay device, consumer
coexistence, and rollback compatibility still require the integrated image and
physical-device acceptance listed below.

### First installation and continuation

Initial setup enables AirPlay before checking feature activation. Asynchronous
setup completion carries that choice back to the parent HTTP process, so a
subsequent unrelated settings save does not disable it. An explicit later
AirPlay disable survives restart; other integration choices are preserved.

One-shot installation starts from stock fastboot. It reads the device's fastboot
product to confirm the target, and it runs the Amonet brick step only when the
bootloader is locked. The pinned Amonet ZIP is supplied by the operator with
`--amonet-zip`. Biscuit accepts any LK build; the default `fastbrick.img` covers builds without a reviewed mapping. `expdb`
is never erased. One-shot staging checks both payload and manifest integrity,
including device readback. Continuation is bound to the original release, bundle, device and
boot slots. Once features are staged, it revalidates them and restores forwarding
without repeating formatting, flashing, rebooting or payload writes. The retained
local-release cache includes SHA256SUMS and its covered assets. The wrapper
requires the original immutable tag for continuation, not mutable `latest`.

These are source corrections backed by host tests, not completed physical
installation acceptance. Do not describe a candidate as hardware validated from these tests.

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
3. Fresh one-shot installation and setup for both approved vendor sets and an
   explicitly accepted unknown compatible set; malformed/changed inputs must
   remain rejected. Verify first-boot feature activation and safe continuation
   after a forwarding interruption without repeating completed device writes.
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
