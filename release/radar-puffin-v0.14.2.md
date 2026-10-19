# LibreEcho radar-puffin v0.14.2

LibreEcho 0.14.2 replaces 0.14.1 and includes everything in it (larger
`userdata` partitions, `--reinstall`, the Nursery page, SSH following its saved
switch; see [the 0.14.1 note](radar-puffin-v0.14.1.md)). It adds one change in
philosophy: **the device is yours, and boot no longer judges what you put on it.**

## Boot no longer fails on user changes

0.14.1 could refuse to start any service, and then roll an OTA update back,
because of something a user had put in `/data`. A hidden directory
(`/data/.kaeru`, left by boot-chain migration tools) was enough.

- Unrecognised files and directories in `/data` are reported and kept; they
  never block boot. Symlinks in LibreEcho's own paths are still refused, because
  the services follow those paths.
- The result of the data cleanup helper is advisory. Boot bookkeeping, vendor
  import and the service graph depend only on `userdata` being identified and
  mounted.
- An OTA slot is confirmed on the recovery plane alone: userdata, ADB and the
  management web UI. Disabled or broken optional services, or a missing
  startup-ready marker, no longer roll an update back. The previous strict
  behaviour can be restored with `HEALTH_STRICT_SERVICES=1`.
- A boot image, feature payload or installed release folder you modified is
  reported (`boot-image-modified`, `generation-modified`) and booted anyway.
  Each feature mounts on its own, so one broken feature no longer takes the
  others down.
- If features cannot be activated at all, only the voice and AirPlay add-on
  services stop. Wi-Fi management, audio, LEDs, buttons, Bluetooth and the web
  UI keep running.
- A conflicting voice service that will not stop is logged instead of stopping
  every service.
- Stock Wi-Fi/Bluetooth firmware from an uncatalogued or updated stock ROM is
  imported automatically if it has the right structure, marked unverified, and
  remembered for later boots. The exact `system_a` size check is gone.
- Still enforced on purpose: downloaded updates must carry valid signatures and
  hashes before they install, and symlinks in LibreEcho's own paths are refused.
- Still enforced on purpose: `userdata` must be the expected partition
  (`mmcblk0p16`, name `userdata`, label `LIBREECHO_DATA`). LibreEcho will not
  guess where your data is or move it.

**If you are on 0.14.0 and the 0.14.1 update rolled back**, update to 0.14.2.

## Other fixes

- **Settings › System update › Build identity** shows the real source commit and
  digest instead of `unknown` (LibreEcho-Platform #257).
- The same panel shows the real transaction state and last result (for example
  `none` / `installed`) instead of `unknown` on 0.14 devices
  (LibreEcho-UI #355). The diagnostic export and `/api/v1/provenance` report
  the same corrected values. Updates were never affected.

## Validation

Host tests and ARM virtual-machine checks of the initramfs decision paths only.
Not yet tested on a physical device: the larger-partition install,
`--reinstall`, and the 0.14.0 to 0.14.2 over-the-air update.
