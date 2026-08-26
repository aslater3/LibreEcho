# LibreEcho initial-install tool

`run-one-shot.sh` is the recommended entry point because it downloads the
release checksum inventory and installer, verifies the installer before
executing Python, and then lets the Python installer download/verify the rest.

`libreecho-install.py` is the Product-side mirror of the verified LibreEcho
initial-install orchestrator. It is intentionally a single standard-library
Python file with a checksum sidecar:

```text
tools/libreecho-install.py
tools/libreecho-install.py.sha256
```

## What `one-shot` does

For a controlled fresh-install test on a supported Amazon Echo 2nd Gen /
`radar_puffin` / BISCUIT device, the command performs the complete flow:

```text
verify/download Product release
→ download and verify pinned Amonet + Git LFS inputs
→ run the Amonet BROM handoff
→ wait for unlocked BISCUIT fastboot
→ flash the verified boot image to logical boot_a and boot_b
→ erase only expdb
→ reboot and wait for ADB
→ verify boot_a_x and boot_b_x readback hashes
→ stage and verify all five feature payloads in userdata via the root runner
→ forward the Web UI over ADB
→ open the first-boot setup page
```

It does not flash Amonet wrapper partitions directly, rewrite GPT or RPMB,
format userdata, invent credentials, or confirm a boot slot. The Amonet exploit
owns the stock-to-Amonet conversion; this tool orchestrates the verified
handoff and LibreEcho installation after that point.

## User command

Download the release bootstrap from the exact Product release, then pass the
exact release tag. The bootstrap verifies the installer against the release
`SHA256SUMS` inventory **before executing Python**. The verified Python installer
then downloads the remaining release assets itself, including the boot image,
`initial-install.tar`, OTA key, five feature payloads/manifests, and checksums.
It also downloads and verifies the pinned Amonet commit and its Git LFS objects.

For a development build:

```bash
TAG=radar-puffin-build-<product-sha>-<source-set-id>-<artifact-set-id>
curl -fL -o "libreecho-${TAG}-run-one-shot.sh" \
  "https://github.com/aslater3/LibreEcho/releases/download/${TAG}/libreecho-${TAG}-run-one-shot.sh"
chmod +x "libreecho-${TAG}-run-one-shot.sh"

./"libreecho-${TAG}-run-one-shot.sh" "$TAG" \
  --fastboot-serial auto \
  --slots both \
  --execute-hardware
```

For a scheduled nightly, use its `radar-puffin-nightly-...` tag in exactly the
same form. Do not shorten, rename, or mix asset files from another release.

`install` is only the host-side preparation/checkpoint action and does not touch
hardware. Use `one-shot` for the actual BROM → fastboot → ADB installation.

## BROM operator sequence

USB remains connected throughout the entry sequence. When the installer shows
the boxed action prompt:

1. Ensure the Echo is connected to the host over USB.
2. Power the Echo off; USB can remain connected.
3. Hold the marked CLK-to-GND short.
4. Power the Echo on while holding the short.
5. Release the short when Amonet prompts you.
6. Press Enter when Amonet prompts you.

BROM entry may not work on the first attempt. If the installer continues
waiting, power-cycle the Echo and try the short sequence again. Keep the
terminal open and follow the live Amonet progress messages.

## Resuming

The installer stores private cached downloads and resumable state under the
user's home directory. If a hardware run stops after Amonet handoff or ADB
returns, use `continue-one-shot` with the same cache/state roots and release
identity. It will not reflash when the state is already at ADB or readback:

```bash
python3 "libreecho-${TAG}-installer.py" continue-one-shot \
  --release-tag "$TAG" \
  --fastboot-serial auto \
  --slots both \
  --execute-hardware
```

## Safety boundary

This is a controlled hardware-test tool, not a general public installer. A
successful checksum, build, or release publication does not establish hardware
acceptance. Preserve the release identity, Amonet log, fastboot/ADB output,
readback hashes, runtime checks, and UART evidence separately under the project
evidence directory.

## Echo Gen 2 pogo-pin carrier (v5)

[`libreecho-echo-gen2-pogo-plug-v5.zip`](./libreecho-echo-gen2-pogo-plug-v5.zip) contains the printable six-pin carrier used to make a repeatable development/service jig for the Echo 2nd Gen base contacts. It replaces the original rounded-rectangle rubber plug while positioning six spring probes over the 2 × 3 contact array.

The model is based on measured hardware dimensions: 3.0 mm pin pitch, approximately 0.66 mm pogo barrels, a 10.0 × 7.6 mm clearance lid, an 8.8 × 6.0 mm insert body, a 5.0 mm insert depth and a 2.0 mm lid. The gold contacts were estimated to sit roughly another 2 mm below the top of the pogo cage, so verify tip projection and compression against the physical device before wiring or powering the jig.

v5 is specifically designed for a 0.4 mm FDM nozzle. Instead of relying on marginal 0.68–0.75 mm printed holes, it uses flared through-channels and provides 0.80, 0.90, 1.00, 1.10 and 1.20 mm bore variants plus a calibration coupon. Print the coupon first; 1.00 mm is the recommended starting carrier for the measured 0.66 mm pins. If adhesive is required, use only a small amount at the wiring-side pocket and keep it clear of the moving plunger.

This is a hardware-development aid, not a required part of the LibreEcho software installation path.
