# LibreEcho initial-install tool

`run-one-shot.sh` is the entry point. It downloads the release checksum
inventory and installer, verifies the installer before executing Python, and
then lets the Python installer download and verify the rest of the bundle.

`libreecho-install.py` is the Product-side mirror of the verified LibreEcho
initial-install orchestrator. It is a single standard-library Python file with a
checksum sidecar:

```text
tools/libreecho-install.py
tools/libreecho-install.py.sha256
```

## Supported targets

| Target | Product string | Amonet archive | Accepted LK builds |
|---|---|---|---|
| `radar_puffin` (Echo 2nd Gen) | `RADAR` | `amonet-radar-v1.0.0.zip` | `59779ca-20220524_183401`, `63cb91b-20221007_072309` |
| `biscuit` (Echo Dot 2nd Gen) | `BISCUIT` | `amonet-biscuit-v2.0.0.zip` | `63cb91b-20221007_072309` only |

The pinned archive and per-build payload digests are recorded in
`release/amonet-pins.json` and embedded in the installer as `AMONET_PINS`. A
test asserts the two never drift.

Biscuit accepts any LK build. The reviewed build `63cb91b-20221007_072309` uses
its own payload. Any other build uses the archive's default `fastbrick.img`,
as upstream `fastbrick.sh` does. An empty LK build string still fails closed
before any write.

## What `one-shot` does

The installer starts from **stock fastboot**. There is no BROM short and no
k32 launcher.

```text
verify/download the Product release
→ check the device's fastboot product against the release target
→ if the bootloader is locked:
     select the payload for the exact lk_build_desc
     extract and verify it from the pinned Amonet ZIP
     fastboot flash brick <payload>
→ wait for the unlocked fastboot device
→ validate the exact product and reviewed userdata geometry
→ format only userdata as ext4
→ flash the verified boot image to boot_a and boot_b
→ reboot and wait for ADB
→ collect read-only ADB bring-up diagnostics
→ verify boot_a_x and boot_b_x readback hashes
→ stage and verify all five feature payloads in userdata via the root runner
→ forward the Web UI over ADB
→ open the first-boot setup page
```

`expdb` is never erased. The Kaeru header there is the unlock proof, so the
installer leaves it intact.

### Unlock and the Amonet ZIP

- If `fastboot getvar unlock_status` reports `true`, no brick write is needed
  and `--amonet-zip` is not required.
- If the device is locked, pass the pinned archive:

  ```sh
  ./run-one-shot.sh "$TAG" --amonet-zip ~/Downloads/amonet-biscuit-v2.0.0.zip \
    --fastboot-serial auto --slots both --execute-hardware
  ```

- The archive's SHA-256 and size must match the pin for the selected target.
  The payload's size and SHA-256 must match the entry for the device's exact
  `lk_build_desc`. Any mismatch stops before the brick write.
- `eMMC-RO` and `Device mismatch` in the brick output are hard stops. The
  installer does not retry them.

The Amonet ZIPs are community artifacts. LibreEcho does not redistribute them;
you supply the exact file.

## Host requirements

```text
bash, adb, fastboot, executable mke2fs, executable dumpe2fs, staged tool probes
```

On Debian/Ubuntu:

```sh
sudo apt-get update
sudo apt-get install adb fastboot e2fsprogs
```

`--install-host-deps` installs only `e2fsprogs`; it does **not** install `adb`
or `fastboot`. Check the full tool set with:

```sh
command -v adb fastboot mke2fs dumpe2fs
```

The installer stages private copies of `fastboot`, `mke2fs`, and `dumpe2fs`
under the cache directory. It builds userdata with the reviewed ext4 feature
set, converts it to Android sparse format, validates the sparse header and the
exact expanded geometry, and flashes only `userdata`. If an image helper is
absent, the installer stops before device access with the repair command.

## User command

Use the public wrapper with an explicit **published stable** tag. It downloads
only the checksum file and installer bootstrap, verifies the bootstrap, and
hands control to the Python installer. The Python installer downloads and
verifies the complete release bundle, including `initial-install.tar` and the
five feature payloads and manifests.

```bash
TAG=radar-puffin-vX.Y.Z  # replace with the published stable tag you selected
curl -fL -o run-one-shot.sh "https://github.com/aslater3/LibreEcho/releases/download/${TAG}/libreecho-${TAG}-run-one-shot.sh"
chmod +x run-one-shot.sh
./run-one-shot.sh "$TAG" --fastboot-serial auto --slots both --execute-hardware
```

Development and nightly tags are for maintainer-controlled test hardware only.
Do not shorten, rename, or mix asset files from another release.

`install` is only the host-side preparation and checkpoint action and does not
touch hardware. Use `one-shot` for the installation itself.

## Resuming

With the same immutable release tag (never `latest`), the original device
serial, slot choice, cache/state roots, and install ID:

```bash
./run-one-shot.sh "$TAG" --continue --fastboot-serial "$SERIAL" \
  --slots both --local-port 18081 --execute-hardware
```

`--continue` must immediately follow the tag. `FEATURES_STAGED` and
`WEBUI_FORWARDED` continuation revalidates the cached release, both selected
boot slots, and every installed payload/manifest pair, then restores the setup
forward. It does not stage files again, reboot, reformat, or reflash. Choose
another local port when the original is occupied. A changed device, slot
selection, bundle, installed hash, or outstanding staging marker fails closed.

`BOOT_WRITTEN` can continue once the device is online in ADB, without
reflashing. `FASTBOOT_READY` can complete the boot writes without formatting
userdata again when the original successful format is recorded. Do not guess a
phase or edit state to force it. Concurrent one-shot and continuation runs share
the same cache lock.

If an older run stopped after ADB readback but before the userdata-format fix,
`continue-one-shot` refuses to guess. Pass `--repair-userdata` to reboot the
exact ADB device into fastboot, validate it, format only userdata, reboot,
recollect diagnostics, verify boot readback, and continue feature staging:

```bash
python3 "libreecho-${TAG}-installer.py" continue-one-shot \
  --release-tag "$TAG" \
  --fastboot-serial "$SERIAL" \
  --slots both \
  --repair-userdata \
  --execute-hardware
```

Every run leaves its log in `./libreecho-installer.log` unless `--log-file PATH`
is supplied. Do not rerun `one-shot` after the boot slots have been written
unless a fresh install is explicitly intended.

If any installer operation fails, it performs a best-effort evidence pass before
reporting the original error. It records host identity and USB/serial state,
fastboot device inventory and `getvar all`, ADB device inventory and read-only
device state, and the brick log. The installer packages the evidence and the
final log into:

```text
./libreecho-installer-evidence.tar.gz
```

The archive is mode `0600`. Missing or unresponsive transports are recorded as
collection failures inside the archive; they do not hide or replace the original
installation error.

## Safety boundary

This is a controlled hardware-test tool. A successful checksum, build, or
release publication does not establish hardware acceptance. Preserve the release
identity, the brick log, fastboot and ADB output, readback hashes, runtime
checks, and UART evidence separately under the project evidence directory.

## Userdata allocation contract (0.14)

The formatter fixes the ext4 block size at 4096 bytes and runs the staged
`dumpe2fs` in the C locale. It checks complete per-group free-block lists against
the filesystem and group free counts before emitting Android sparse data.
Filesystem-wide `Free blocks:` is a count, never a block address.

Android `DONT_CARE` chunks cover only blocks ext4 marks free. Every allocated
block, including zeroed inode tables, bitmaps and journal blocks, is emitted as
`RAW`. No host filesystem hole support is required. The 64 MiB programmed-data
limit, product identity checks, and exact two-layout allowlist are retained.
This initialisation is not a secure erase of unallocated old data.

The corresponding Platform image must accept 2,137,088 and 2,153,472 sectors for
userdata during init, feature staging, updater validation and boot control.
Publish a fresh immutable installer and rebuilt Platform/boot image together;
changing only the installer cannot fix an older image's runtime guards.

## Echo Gen 2 pogo-pin carrier (v5)

[`libreecho-echo-gen2-pogo-plug-v5.zip`](./libreecho-echo-gen2-pogo-plug-v5.zip)
contains the printable six-pin carrier used to make a repeatable development or
service jig for the Echo 2nd Gen base contacts. It is a hardware-development aid,
not a required part of the software installation path.
