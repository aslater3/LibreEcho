# Rootless Podman flashing

Tools for the [existing installer](README.md). Read its wiring/safety instructions
first; nothing flashes automatically.

## Host

Debian/Ubuntu prerequisites (one-time administrator setup):

```sh
sudo apt-get update
sudo apt-get install podman crun uidmap usbutils git
podman info --format '{{.Host.Security.Rootless}}'
```

Require `true`; run Podman **without sudo**. Container UID 0 maps to your user.

Rootless cannot bypass host USB permissions. Check `lsusb` and read/write access
to `/dev/bus/usb/BUS/DEVICE` after re-enumeration. Use existing Android rules, or:

```sh
getent group plugdev || sudo groupadd --system plugdev
sudo usermod -aG plugdev "$USER"
sudo tee /etc/udev/rules.d/70-libreecho.rules >/dev/null <<'RULES'
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0bb4", ATTR{idProduct}=="0c01", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="18d1", ATTR{idProduct}=="d001", GROUP="plugdev", MODE="0660"
RULES
sudo udevadm control --reload-rules
```

Log out/in; reconnect safely. IDs aren't serial-isolated; recovery may differ.
Inspect unfamiliar IDs; never grant all-USB/world-writable access.

## Container

```sh
git clone https://github.com/aslater3/LibreEcho.git
cd LibreEcho
podman build -t localhost/libreecho-flasher tools/flash-container
install -d -m 0700 "$HOME/libreecho-flash-work" \
  "$HOME/libreecho-flash-work/.installer-home"
podman run --rm -it \
  --runtime crun --group-add keep-groups --privileged \
  --network host --env ANDROID_ADB_SERVER_PORT=5038 \
  --volume /dev/bus/usb:/dev/bus/usb \
  --volume "$HOME/libreecho-flash-work:/work" \
  localhost/libreecho-flasher
```

Internet required. USB-directory mounting supports re-enumeration; `keep-groups`
preserves permissions. Host networking exposes setup on host loopback; ADB port
5038 must be free. **Privileged rootless is not a sandbox:** other user-accessible
devices and writable `/work` are exposed. Trust the installer; disconnect unrelated
flashable devices. No payload/state enters the image.

## Install and recover

Locked devices need the exact ZIP in `/work`: biscuit
`amonet-biscuit-v2.0.0.zip` ([author](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/),
[attachment](https://xdaforums.com/attachments/amonet-biscuit-v2-0-0-zip.6383906/)); radar
`amonet-radar-v1.0.0.zip`. Search fallback:
`"amonet-biscuit-v2.0.0.zip" "Rortiz2" site:xdaforums.com`.
Credit: R0rt1z2/k4y0z. No redistribution, version substitution, extraction or
separate FireOS procedure. Unlocked devices need no ZIP.

Match size/**SHA-256** against [pins](../../release/amonet-pins.json), not filenames;
the selected release's installer enforces them. Checksums aren't publisher
signatures. **Stop on mismatch.** Follow the [main guide](README.md) inside the
container: obtain the verified wrapper, check product, then explicitly invoke
`--execute-hardware`. **This erases userdata; never override a target mismatch.**

Require successful readback/feature verification and [first-boot checks](README.md#5-first-boot-and-setup).
Keep the installer open. Remote browser:

```sh
ssh -N -L 18080:127.0.0.1:18080 USER@USB_HOST
```

Open `http://127.0.0.1:18080/setup.html`; verify release/model.

Keep `/work`, including private HOME `.installer-home`, keys/logs/cache/state;
never publish it. Reopen with the same mount/HOME. After writes, [continue](README.md#recovering-from-a-forwarding-failure)
with the original immutable tag, serial and slots—don't restart flashing.
[CI](../../.github/workflows/flash-container.yml) checks tools, not hardware/USB coverage.
