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
→ validate the exact BISCUIT product and reviewed userdata geometry
→ format only userdata as ext4 (Amonet has cleared its old filesystem header)
→ flash the verified boot image to logical boot_a and boot_b
→ erase only expdb
→ reboot and wait for ADB
→ collect read-only ADB bring-up diagnostics
→ verify boot_a_x and boot_b_x readback hashes
→ stage and verify all five feature payloads in userdata via the root runner
→ forward the Web UI over ADB
→ open the first-boot setup page
```

The installer also performs a host preflight before BROM. It stages private
copies of `fastboot`, `mke2fs`, and `img2simg` under the cache directory. To
avoid distro fastboot's incompatible internal ext4 generator, the installer
builds userdata with the reviewed ext4 feature set, converts it to Android
sparse format, validates the sparse header and exact expanded geometry, and
flashes only `userdata`. If either image helper is absent, the installer stops
before device access with the exact repair command. To let it install
`e2fsprogs` and `android-sdk-libsparse-utils` using `apt-get`/`sudo`, add
`--install-host-deps`.

Host requirements are validated before the BROM handoff:

```text
bash, adb, fastboot, executable mke2fs, executable img2simg, staged tool probes
```

On Debian/Ubuntu, install the required host tools before using `one-shot`:

```sh
sudo apt-get update
sudo apt-get install adb fastboot e2fsprogs android-sdk-libsparse-utils
```

`--install-host-deps` can install only `e2fsprogs` and
`android-sdk-libsparse-utils`; it does **not** install `adb` or `fastboot`.
Check the complete tool closure with:

```sh
command -v adb fastboot mke2fs img2simg
```

It does not flash Amonet wrapper partitions directly or invent credentials. The
Amonet exploit owns the stock-to-Amonet conversion; this tool validates the
handoff, recreates the reviewed userdata filesystem, and completes LibreEcho
installation after that point.

## User command

Use the public wrapper with an explicit **published stable** tag. It downloads
only the checksum file and installer bootstrap, verifies the bootstrap, and
then hands control to the Python installer. The Python installer downloads and
verifies the complete release bundle, including `initial-install.tar`, the five
feature payloads/manifests, and pinned Amonet inputs.

```bash
TAG=radar-puffin-vX.Y.Z  # replace with the published stable tag you selected
curl -fL -o run-one-shot.sh "https://github.com/aslater3/LibreEcho/releases/download/${TAG}/libreecho-${TAG}-run-one-shot.sh"
chmod +x run-one-shot.sh
./run-one-shot.sh "$TAG" --fastboot-serial auto --slots both --execute-hardware
```

Development and nightly tags are for maintainer-controlled test hardware only;
they are not a public installation recommendation. Do not shorten, rename, or
mix asset files from another release.

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

### BROM diagnostics

Transport diagnostics are shown only while the installer is actually waiting
for BROM, or when the handoff fails before any Amonet progress is recorded. If
`0e8d:0003` and a MediaTek `ttyACM` node are both present, the output says:

```text
BROM transport is healthy; no transport action is needed.
```

It does not print generic shorting, ModemManager, or recovery advice in that
healthy state. Those messages are reserved for an actual missing, ambiguous, or
incorrect transport condition.

## Resuming

The installer stores private cached downloads and resumable state under the
user's home directory. If a legacy run stopped after ADB/readback before the userdata-format fix,
`continue-one-shot` refuses to guess. Pass `--repair-userdata` to explicitly
reboot the exact ADB device into fastboot, validate it, format only userdata,
reboot, recollect diagnostics, verify boot readback, and continue feature
staging without repeating the BROM/Amonet conversion:

```bash
python3 "libreecho-${TAG}-installer.py" continue-one-shot \
  --release-tag "$TAG" \
  --fastboot-serial auto \
  --slots both \
  --repair-userdata \
  --execute-hardware
```

Every run leaves its shareable log in `./libreecho-installer.log` unless
`--log-file PATH` is supplied. Do not rerun `one-shot` after Amonet has already
completed unless a fresh conversion is explicitly intended.

If any installer operation fails, it performs a best-effort evidence pass before
reporting the original error. When available, this records host identity and
USB/serial state, fastboot device inventory and `getvar all`, ADB device
inventory and read-only device state, and the cached Amonet log. The installer
packages the evidence and the final installer log into:

```text
./libreecho-installer-evidence.tar.gz
```

The archive is mode `0600`. Missing or unresponsive transports are recorded as
collection failures inside the archive; they do not hide or replace the original
installation error. ADB collection is attempted if a device is visible even
when the failure occurred while waiting for or using fastboot.

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
