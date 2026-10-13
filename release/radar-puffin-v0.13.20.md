# LibreEcho radar-puffin v0.13.20

A focused patch release that fixes owners getting stuck in first-run setup after accepting their device's own Wi-Fi firmware, because the reboot that setup asks for silently disconnected the installer's USB link to the setup page.

## Setup survives the reboot after accepting device Wi-Fi firmware

- When LibreEcho finds the complete MT8163 Wi-Fi firmware set but this exact revision is not on the known-good list, setup offers **Accept device firmware**. The accepted state now has a **Reboot now** button (with confirmation, using the existing reboot API) instead of only telling the owner to reboot.
- The installer now stays attached after opening the setup page and watches setup until it is finished. When the Echo reboots, the host-side `adb forward` that carries `http://127.0.0.1:18080/setup.html` is dropped by adb; the installer waits for the same device to come back, restores the forward and reopens the setup page by itself. It never writes to the device while watching. Ctrl-C detaches safely, and `--no-wait-for-setup` keeps the previous behaviour.
- New `reconnect` installer command for owners who already closed the installer: it reattaches to the saved installation, restores the forward and reopens setup without flashing, formatting, rebooting or re-staging anything.
- The installation guide explains the reboot step, the `reconnect` command, and the manual fallback (`adb wait-for-device`, `adb forward tcp:18080 tcp:8080`, adding `-s <serial>` when several Android devices are connected).

## Experimental status and validation boundary

The setup reconnect has been exercised with host-side tests that simulate a device reboot, a dropped forward, a device that never returns and an operator interrupt. It has **not** been confirmed on hardware for this release: no device has run the 0.13.20 installer through an accept-firmware reboot yet. If the installer cannot reconnect within ten minutes it stops and prints the exact manual recovery command.

## Coordinated source set

- Product version: `0.13.20`
- Product release ID: `radar-puffin-v0.13.20`
- Release impact: `patch`
- Baseline: the exact coordinated source commits of `radar-puffin-v0.13.19`, not the 0.14 feature line.
- Product, Platform, Linux and UI use `release/0.13.20`; UI `VERSION` equals `0.13.20`. Platform and Linux have no changes for this release.

## Release status and validation

These notes describe source preparation, not physical hardware acceptance. Before publishing, merge the linked component fixes, run the canonical hosted build with all four `release/0.13.20` refs, and verify the signed complete asset inventory and independent image/provenance checks. Do not reuse or replace the published 0.13.19 assets.
