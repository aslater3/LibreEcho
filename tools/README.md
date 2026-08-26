# LibreEcho initial-install tool

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

Download only the installer from the exact Product release, then pass the exact
release tag. The installer downloads the release assets itself, including the
boot image, `initial-install.tar`, OTA key, five feature payloads/manifests,
and checksums. It also downloads and verifies the pinned Amonet commit and its
Git LFS objects.

For a development build:

```bash
TAG=radar-puffin-build-<product-sha>-<source-set-id>-<artifact-set-id>
curl -fL -o "libreecho-${TAG}-installer.py" \
  "https://github.com/aslater3/LibreEcho/releases/download/${TAG}/libreecho-${TAG}-installer.py"

python3 "libreecho-${TAG}-installer.py" one-shot \
  --release-tag "$TAG" \
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
