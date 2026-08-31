# LibreEcho radar-puffin v0.13.9 development release

This is a controlled **development-channel test release** for the Amazon Echo
2nd Gen / `radar_puffin` target. It is intended for the maintainer’s test
hardware and is not a stable or production-support commitment.

## Release identity

- Product release: `radar-puffin-v0.13.9`
- OTA channel: `dev`
- Release classification: GitHub prerelease
- Coordinated components: Product, Platform, Linux 6.1, and UI source heads
- Intended use: maintainer-controlled test flashing and validation
- Product source head: `617052293f40d92ff81ce114b7a5abdaaaca9ef7`
- Platform source head: `e05c01396399ced9c33509481d599ceb39c77dde`
- Linux 6.1 source head: `b5ecb69724322652fff51ac7c366b1618903630f`
- UI source head: `5ce64cf5374995dda0bfef5858fa7cc3e35a69a8`

The image candidate must record the exact Product, Platform, Linux, and UI
commits used to create it. Development assets remain separate from stable
publication and must not be marked `latest`.

## Included changes

This candidate carries the coordinated 0.13.8 baseline described in the
[0.13.8 release note](radar-puffin-v0.13.8.md), with the following changes in
the selected 0.13.9 source heads. Release-branch backports and merge commits
are deduplicated below.

### Product — release, installer, and build

- [Product #73](https://github.com/aslater3/LibreEcho/pull/73): Added coordinated Product release lanes and stable OTA publishing workflow.
- [Product #76](https://github.com/aslater3/LibreEcho/pull/76): Signed `dev`-channel OTA prereleases can now be produced from main and scheduled nightly runs without replacing the latest/stable release.
- [Product #80](https://github.com/aslater3/LibreEcho/pull/80): Fixed one-shot feature staging by pushing the staging helper to the device and stopped the Amonet progress tailer from crashing.
- [Product #81](https://github.com/aslater3/LibreEcho/pull/81): Scheduled nightly releases now include the complete verified one-shot installer asset set for controlled fresh-install testing.
- [Product #82](https://github.com/aslater3/LibreEcho/pull/82): Release automation now defaults to the reviewed `aslater3/amonet-k32` Amonet repository.
- [Product #83](https://github.com/aslater3/LibreEcho/pull/83): Development builds now include the complete verified one-shot installer asset set for controlled fresh-install testing.
- [Product #85](https://github.com/aslater3/LibreEcho/pull/85): Added the Echo Gen 2 pogo-plug v5 carrier package and its operator documentation.
- [Product #90](https://github.com/aslater3/LibreEcho/pull/90): One-shot installs now report actionable BROM transport, permission, preloader, and `cdc_acm` failures instead of hanging silently.
- [Product #93](https://github.com/aslater3/LibreEcho/pull/93): One-shot installation now recreates wiped userdata before verified feature staging and leaves persistent diagnostics when staging fails.
- [Product #94](https://github.com/aslater3/LibreEcho/pull/94): One-shot installation now validates and self-stages its host `fastboot`/`mke2fs` tools before device access and suppresses misleading recovery warnings when BROM is healthy.
- [Product #96](https://github.com/aslater3/LibreEcho/pull/96): Release metadata accepts documented serial-device wildcards while continuing to reject concrete host/device paths.
- [Product #97](https://github.com/aslater3/LibreEcho/pull/97): One-shot installation now creates and validates a fastboot-compatible sparse ext4 userdata image instead of using the broken internal formatter.
- [Product #98](https://github.com/aslater3/LibreEcho/pull/98): Userdata initialization now skips empty blocks and reports elapsed time during otherwise-silent fastboot writes.
- [Product #100](https://github.com/aslater3/LibreEcho/pull/100): Restored the reviewed MT8163 helper needed to import owner-local Wi-Fi firmware during boot.
- [Product #103](https://github.com/aslater3/LibreEcho/pull/103): Hosted builds now supply the pinned libnl dependency needed for nl80211 Wi-Fi association.
- [Product #107](https://github.com/aslater3/LibreEcho/pull/107): Generated eSpeak phoneme data is now built before TTS packaging so the voice service can start with a complete runtime payload.
- [Product #109](https://github.com/aslater3/LibreEcho/pull/109): Added the supported 0.13.9 first-install guide and checksum-gated `latest` bootstrap.

### Platform — initramfs, connectivity, and image contracts

- [Platform #89](https://github.com/aslater3/LibreEcho-Platform/pull/89): Allowlisted timer and capture-mux state so valid files and crash leftovers do not block boot through the data-cleanup contract.
- [Platform #90](https://github.com/aslater3/LibreEcho-Platform/pull/90): Restored safe owner-local MT8163 firmware import across supported stock layouts and exposed bounded import status at runtime.
- [Platform #91](https://github.com/aslater3/LibreEcho-Platform/pull/91): Wi-Fi setup now prefers nl80211 while retaining WEXT fallback for compatibility.
- [Platform #92](https://github.com/aslater3/LibreEcho-Platform/pull/92): Device `reboot` requests now propagate the signals BusyBox sends to PID 1 instead of silently doing nothing.
- [Platform #93](https://github.com/aslater3/LibreEcho-Platform/pull/93): The TTS payload now contains the eSpeak data directory required for voice-service startup.
- [Platform #96](https://github.com/aslater3/LibreEcho-Platform/pull/96): The LibreEcho control centre remains reachable at `libreecho.local` after setup and factory reset.
- [Platform #99](https://github.com/aslater3/LibreEcho-Platform/pull/99): Corrected the Radar-Puffin DTB transformation so generated AFE pinctrl references remain bound to the correct hardware states.
- [Platform #103](https://github.com/aslater3/LibreEcho-Platform/pull/103): Kept the image startup contract aligned with conditional Bluetooth readiness and delayed voice-service readiness.

### Linux 6.1 — MT8163 kernel fixes

- [Linux #16](https://github.com/aslater3/LibreEcho-Linux-6.1/pull/16): Recovers MT8163 Wi-Fi from a bounded stale connected-state path after authentication timeout.
- [Linux #19](https://github.com/aslater3/LibreEcho-Linux-6.1/pull/19): Adds pstore/ramoops capture so kernel console, panic, ftrace, and userspace messages can survive a reboot for later diagnostics.
- [Linux #21](https://github.com/aslater3/LibreEcho-Linux-6.1/pull/21): Fixes MT8163 Wi-Fi reconnects after locally initiated disconnects by synchronizing cfg80211 state.

### UI — control centre and startup

- [UI #132](https://github.com/aslater3/LibreEcho-UI/pull/132): Restores the Home location and weather settings panel and its existing save path.
- [UI #134](https://github.com/aslater3/LibreEcho-UI/pull/134): Rejects incomplete, out-of-range, or stale coordinate updates so a place name cannot be saved against another location’s coordinates.
- [UI #135](https://github.com/aslater3/LibreEcho-UI/pull/135): Prevents repeated Bluetooth scans while discovery is active and reports the result when an automatic scan completes.
- [UI #195](https://github.com/aslater3/LibreEcho-UI/pull/195): Restores the sidebar link to the canonical LibreEcho Product repository.
- [UI #197](https://github.com/aslater3/LibreEcho-UI/pull/197): Allows clean startup with Bluetooth disabled and waits for slow voice-service dependencies before starting `agentd`.

The ledger intentionally omits test-only, CI-only, release-marker-only, and
unshipped documentation-only work. Open or main-only follow-ups and deferred
0.14 feature work are not part of this candidate.

## Validation boundary

The 0.13.9 test candidate must pass the deterministic ARM32 image build, DTB
hardware-contract verifier, source/provenance closure, signed A/B OTA checks,
and the relevant host-side component suites before it is flashed. A successful
host or image build is not hardware acceptance.

Physical flashing, boot, rollback, network, audio, voice, privacy, and runtime
acceptance are separate gates. This note does not claim that those checks have
been completed. No device is touched by the release-preparation workflow.

The community-noncommercial wakeword payload remains subject to
**CC-BY-NC-SA-4.0**, including its noncommercial, attribution, and ShareAlike
terms. Any other bundled third-party assets retain their individual licence and
redistribution terms.
