# LibreEcho radar-puffin v0.13.21

A bridge release that lets a 0.13 device move to 0.14 over the air, without reinstalling. It also offers an optional repair that moves Echos installed with 0.13's BROM method onto their board's community Kaeru boot chain, which brings TWRP.

## 0.13 devices can update to 0.14 over the air

- 0.13.21 accepts 0.14's OTA v3 update format. Earlier 0.13 releases rejected it only because their update parser predates v3: the signing key, the package container and the boot image size are the same. The v3 support is backported from the Platform commit pinned by the 0.14.0 release.
- The path is: update to 0.13.21 through the control centre (**System** > **System update**) as a normal update, then check for updates again and install 0.14. Dev-channel devices are offered 0.13.21 as soon as it is published; stable devices are offered it once it is the current stable release.
- OTA v1 and v2 packages are still accepted exactly as before, and the existing update status values are unchanged, so the 0.13 control centre keeps working. The update panel also recognises a 0.14 target.
- If 0.14 does not start properly, the device falls back to 0.13.21. It records the rolled-back release and package hash and does not offer that same 0.14 package again, so a failed update cannot loop.
- 0.14 carries over 0.13's versioned configuration, so the update is intended to keep settings, Wi-Fi and the account. See the validation section below for what has been confirmed on hardware. Downloading a configuration backup from the **System** page first is recommended.
- Reinstalling with the 0.14 installer remains the fallback. It formats userdata.

The step-by-step owner instructions are in [Upgrading from 0.13 to 0.14](https://github.com/aslater3/LibreEcho/blob/radar-puffin-v0.13.21/docs/install/README.md#upgrading-from-013-to-014).

## Optional: repair the bootloader and install TWRP

- Echos installed with 0.13's BROM method run an older boot chain. They report `BISCUIT` in fastboot whatever the board is, and have no TWRP. The new **Bootloader** panel on the **System** page shows the boot chain, the board, whether TWRP is present, and whether a repair is available.
- The repair moves the unit onto its board's community Kaeru chain: amonet-radar v1.0.0 for the Echo 2nd Gen, amonet-biscuit v2.0.0 for the Echo Dot 2nd Gen. It installs TWRP (hold **Volume Up** while plugging in power; **Volume Down** starts unlocked fastboot).
- It keeps the installed LibreEcho system and data, and reboots normally into LibreEcho when done.
- It is opt-in and only offered when the device can confirm its board from hardware evidence (not the board name the bootloader reports), the boot chain exactly matches a known 0.13 BROM install, and the eMMC can be read back. Otherwise it stays blocked. A wrong-board chain is the one mistake that cannot be undone.
- The pinned upstream packages are verified by size and SHA-256 before use. The repair follows the upstream installer, with these LibreEcho safeguards: it backs up every region it touches, writes TWRP first, reads back every write, writes the preloader last, never wipes all of `misc`, and keeps the running, confirmed slot active.
- Keep the device plugged in for the whole repair. If it is interrupted, the device starts in MediaTek USB download mode instead of booting, and is recovered over the USB connection with the LibreEcho installer. The BROM short is not needed.

The amonet tools and Kaeru chains are the work of k4y0z and R0rt1z2. See their [XDA thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-2nd-gen-2017-radar.4801290/).

## Stuck in fastboot after an earlier 0.14 install attempt

A 0.13 BROM-installed unit can start straight into fastboot after an aborted 0.14 install attempt, because a `FASTBOOT_PLEASE` marker was left in `expdb`. The installation guide documents the fix, `fastboot erase expdb` then `fastboot reboot`, together with the two checks that must pass first (`fastboot oem kaeru-version` is an unknown command, and `boot_a_x` and `boot_b_x` are both `0x1000000`). **Never erase `expdb` on a Kaeru chain**: there it holds the Kaeru bootloader patch, and erasing it bricks the device.

## Experimental status and validation boundary

The OTA v3 acceptance, boot-chain status and repair have been prepared in source with host-side tests.

- OTA 0.13.21 to 0.14 on hardware: TODO — validated on hardware: <pending>.
- Settings, Wi-Fi and account kept across the 0.14 update: TODO — validated on hardware: <pending>.
- Rollback from a failed 0.14 boot without re-offer: TODO — validated on hardware: <pending>.
- Bootloader repair, Echo 2nd Gen (amonet-radar v1.0.0): TODO — validated on hardware: <pending>.
- Bootloader repair, Echo Dot 2nd Gen (amonet-biscuit v2.0.0): TODO — validated on hardware: <pending>.
- USB download-mode recovery after an interrupted repair: TODO — validated on hardware: <pending>.
- `expdb` fastboot-marker fix on a legacy-chain unit: TODO — validated on hardware: <pending>.

Until these are filled in, treat each item as unconfirmed on hardware.

## Known issue: 0.13.21 as GitHub's Latest release

Stable 0.13 devices find updates through GitHub's "Latest" release, so 0.13.21 is marked Latest for a limited window. During that window the 0.14 `run-one-shot.sh` wrapper, which looks up the latest release, would find 0.13.21. Use the web installer, or pass the 0.14 tag explicitly. 0.14 devices are not affected: their update source stays pinned to 0.14. After the window, 0.14.x is made Latest again.

## Coordinated source set

- Product version: `0.13.21`
- Product release ID: `radar-puffin-v0.13.21`
- Release impact: `patch`
- Baseline: the exact coordinated source commits of `radar-puffin-v0.13.20`, not the 0.14 feature line.
- Product, Platform, Linux and UI use `release/0.13.21`; UI `VERSION` equals `0.13.21`. Linux has no changes for this release.

## Release status and validation

These notes describe source preparation, not physical hardware acceptance. Before publishing, merge the linked component changes, run the canonical hosted build with all four `release/0.13.21` refs, and verify the signed complete asset inventory and independent image/provenance checks. Do not reuse or replace the published 0.13.20 assets.
