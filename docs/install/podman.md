# Linux USB flashing with rootless Podman

This optional workflow supplies the host tools in a disposable container. It
runs the [same one-shot installer](README.md), not a firmware build or a second
installer. Read that guide's safety, wiring and target requirements first.
Building the image or opening its shell does not flash anything.

## 1. Prepare the Linux host

Use native Linux with a direct USB connection to the Echo. On Debian/Ubuntu,
install these prerequisites once (administrator access is needed for host
packages and, if necessary, udev rules):

```sh
sudo apt-get update
sudo apt-get install podman crun uidmap usbutils git
```

Run Podman and flashing as your normal user, **never with sudo**:

```sh
podman info --format '{{.Host.Security.Rootless}}'
id -nG
```

Require `true` from the first command. Resolve rootless UID/GID mapping problems
using your distribution's instructions rather than switching to a rootful run.

### Host USB permissions

Rootless `--privileged` cannot grant access the host user does not have. Existing
Android udev rules may already work. Check the Echo with `lsusb` and check
read/write access to its corresponding `/dev/bus/usb/BUS/DEVICE` node. There is
no universally required `usb` group; `dialout` alone does not grant raw USB access.

Known IDs are `0bb4:0c01` for the Echo's fastboot transport and `18d1:d001` for
LibreEcho ADB. Confirm them on your unit. If missing permissions prevent access,
an administrator can install these narrow fallback rules in
`/etc/udev/rules.d/70-libreecho.rules`:

```sh
sudo tee /etc/udev/rules.d/70-libreecho.rules >/dev/null <<'RULES'
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0bb4", ATTR{idProduct}=="0c01", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="18d1", ATTR{idProduct}=="d001", GROUP="plugdev", MODE="0660"
RULES
```

Ensure the group exists, add the flashing user, and reload rules:

```sh
getent group plugdev || sudo groupadd --system plugdev
sudo usermod -aG plugdev "$USER"
sudo udevadm control --reload-rules
```

Log out and back in after a group change, then reconnect the device following
the wiring safety instructions. These permissions work over SSH too. They are
not serial-number isolation: other hardware can share the same IDs. Recovery
ADB during unlocking may use another ID; complete recovery-ID coverage has not
been established. If it differs, inspect it before adding a specific rule. Do
not permit all USB devices or make nodes world-writable to work around a failure.

## 2. Build the tools and open a shell

From a LibreEcho source checkout, build with the small dedicated context:

```sh
git clone https://github.com/aslater3/LibreEcho.git
cd LibreEcho
podman build -t localhost/libreecho-flasher tools/flash-container
```

Keep runtime files outside the source checkout. The directory below will contain
private ADB keys, downloads, logs and recovery state; do not commit or publish it.
Copy the checkout's archive pins there for a preliminary verification check:

```sh
install -d -m 0700 "$HOME/libreecho-flash-work" \
  "$HOME/libreecho-flash-work/.installer-home"
install -m 0644 release/amonet-pins.json "$HOME/libreecho-flash-work/amonet-pins.json"
podman run --rm -it \
  --runtime crun --group-add keep-groups --privileged \
  --network host --env ANDROID_ADB_SERVER_PORT=5038 \
  --volume /dev/bus/usb:/dev/bus/usb \
  --volume "$HOME/libreecho-flash-work:/work" \
  localhost/libreecho-flasher
```

The container defaults to `/work`, with `HOME=/work/.installer-home`. Container
UID 0 maps to your host user, not host root. Mounting the **USB directory**, not
one node, allows fastboot-to-ADB re-enumeration. `crun` and `keep-groups` preserve
supplementary-group device access. Host networking keeps the installer's
loopback-only setup forward reachable on the USB host. ADB port 5038 avoids the
usual host ADB server on 5037; it must itself be available.

**This privileged rootless container is not a sandbox for untrusted code.** It
has your user's USB access, host networking and write access to `/work`.
Privileged Podman may also expose other host devices accessible to your user.
Use trusted source and disconnect unrelated flashable devices. Internet access is
required for package installation and release downloads. No payload, archive,
installer or working-directory data is copied into the image.

## 3. Supply the archive and run the existing guide

**Locked devices only:** obtain the exact archive required by the selected
release. For Dot 2nd Gen (`biscuit`), the current pin is
`amonet-biscuit-v2.0.0.zip`, from Roger Ortiz / R0rt1z2's
[XDA thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/)
and its [2.0.0 attachment](https://xdaforums.com/attachments/amonet-biscuit-v2-0-0-zip.6383906/).
If the link moves, search for
`"amonet-biscuit-v2.0.0.zip" "Rortiz2" site:xdaforums.com` and find the author's
post. Do not substitute a newer version or follow the forum's separate FireOS
installation sequence. Credit for Amonet belongs to R0rt1z2 and k4y0z.

Place the unmodified ZIP in the host's `libreecho-flash-work` directory; it will
appear in `/work`. LibreEcho does not redistribute it. For Echo 2nd Gen
(`radar_puffin`), use its separate `amonet-radar-v1.0.0.zip` and target pins;
the biscuit archive is not interchangeable. Already-unlocked devices need no ZIP.

The [canonical archive pins](../../release/amonet-pins.json) specify the size and
**SHA-256**, not SHA-512. Inside the container, check the biscuit archive against
the snapshot copied above (change both arguments for radar):

```sh
python3 - biscuit amonet-biscuit-v2.0.0.zip <<'PY'
import hashlib
import json
from pathlib import Path
import sys

pin = json.loads(Path('amonet-pins.json').read_text())['targets'][sys.argv[1]]
archive = Path(sys.argv[2])
if archive.name != pin['archive'] or archive.stat().st_size != pin['archive_size']:
    raise SystemExit('STOP: archive name or size mismatch')
with archive.open('rb') as source:
    digest = hashlib.file_digest(source, 'sha256').hexdigest()
if digest != pin['archive_sha256']:
    raise SystemExit('STOP: archive SHA-256 mismatch')
print(archive.name + ': OK')
PY
```

Require a successful exit and `OK`; otherwise stop. This compares bytes, not an
independent publisher signature. The selected release's installer performs the
final authoritative archive/payload verification; the checkout snapshot is only
a precheck. Never bypass a mismatch or mix release inputs.

Inside the container, follow [Download and verify the release](README.md#1-download-and-verify-the-release)
to download the existing wrapper into `/work` and set `TAG`. Follow the guide's
connection checks (`lsusb`, `fastboot devices`, `fastboot getvar product`) before
its explicit destructive command. For example, a locked Dot uses:

```sh
./run-one-shot.sh "$TAG" --amonet-zip /work/amonet-biscuit-v2.0.0.zip \
  --fastboot-serial auto --slots both --execute-hardware
```

**This erases userdata.** Do not force a target mismatch. Keep the installer open
through first-boot setup. Opening a shell alone does not run this command.

## 4. Verify, set up and retain recovery state

Require the installer to complete successfully, including boot readback and
feature verification. Use the main guide's [first-boot checks](README.md#5-first-boot-and-setup)
inside the same container and ADB server. The setup page is forwarded on the USB
host's loopback port 18080. For a browser on another computer, tunnel from that
computer rather than publishing the setup port:

```sh
ssh -N -L 18080:127.0.0.1:18080 USER@USB_HOST
```

Then open `http://127.0.0.1:18080/setup.html` in that browser and verify the
expected release/model during setup. Replace the SSH placeholders yourself.

Exiting removes the container, **not** the work directory or its private state.
After failure, retain everything and reopen the shell with the same mount and
HOME. Follow [continuation](README.md#recovering-from-a-forwarding-failure) using
the original immutable tag, serial, state and slot selection. Do not restart a
fresh destructive installation after boot slots have been written.

## Checks without hardware

The image can be checked without USB, privileged mode or host networking:

```sh
podman run --rm localhost/libreecho-flasher bash -euc \
  'command -v adb fastboot curl python3 mke2fs dumpe2fs lsusb'
```

The scoped container CI performs that build/tool check; it does not flash.
A successful build does not establish host USB permissions, recovery-ID coverage
or hardware compatibility. This workflow makes no changes to the installer,
firmware, boot chain or release process, and publishes no container image.
