# LibreEcho installation guide

> For Amazon Echo 2nd generation (`radar_puffin`, MT8163) and Echo Dot 2nd
> generation (`biscuit`). The procedure uses USB fastboot on the stock device.
> It does not require opening the enclosure or shorting any pad.

## Safety first

- Unplug the Echo before opening it, and never probe a powered board.
- Keep the USB connection on the approved data cable. Do not connect a TTL UART
  adapter to the USB pads.
- Use a data-capable USB cable and a direct port without a hub.
- Stop and keep the installer log if the device behaves unexpectedly.

## Supported targets

| Device | Target | Amonet archive | Accepted LK build |
|---|---|---|---|
| Echo 2nd Gen | `radar_puffin` | `amonet-radar-v1.0.0.zip` | `59779ca-20220524_183401`, `63cb91b-20221007_072309`, `63cb91b-20221007_073612` |
| Echo Dot 2nd Gen | `biscuit` | `amonet-biscuit-v2.0.0.zip` | `63cb91b-20221007_072309` only |

Biscuit accepts any LK build. An unknown or empty LK build stops before any write.

## Equipment

### Required

- Data-capable USB cable
- `adb` and `fastboot` (Debian/Ubuntu packages: `adb` and `fastboot`)
- `bash`, `curl`, and Python 3
- `mke2fs` and `dumpe2fs` (both from `e2fsprogs`)
- The published **initial-install bundle** for the target and its matching
  SHA-256 file
- **Only if the device is locked:** the pinned Amonet ZIP for your target (see
  step 3). LibreEcho does not redistribute it.

On Debian/Ubuntu, install the host tools before starting the installer:

```sh
sudo apt-get update
sudo apt-get install adb fastboot e2fsprogs
```

`--install-host-deps` can install only the filesystem-image helpers; it does
**not** install `adb` or `fastboot`. Confirm they are available with
`command -v adb fastboot mke2fs dumpe2fs`.

## 1. Download and verify the release

Copy and paste these commands. `latest` resolves to the current published stable
release, and the wrapper verifies and runs the immutable installer:

```sh
TAG=latest
curl -fL -o run-one-shot.sh "https://raw.githubusercontent.com/aslater3/LibreEcho/main/tools/run-one-shot.sh"
chmod +x run-one-shot.sh
```

To deliberately install a development or nightly build, replace `latest` with
that build's complete immutable `radar-puffin-build-*` or
`radar-puffin-nightly-*` tag. Use these only on maintainer-controlled test
hardware.

The wrapper resolves `latest` through GitHub's public release API, rejects draft
and prerelease results, and passes the resolved immutable tag to the installer.
It verifies the installer checksum using public GitHub download URLs and does
not require a GitHub account or token. Do not rename or mix asset files from
another release.

## 2. Connect the device

1. Power the Echo or Dot off and connect it to the computer with the USB cable.
2. Check that the host can see it:

   ```sh
   adb devices
   ```

   The device is normally in ADB or fastboot mode. Both are acceptable as the
   starting point. The installer reads the device's fastboot product to
   confirm the target.

3. **Only if the device is locked**, download the matching Amonet ZIP and keep
   it unmodified. The installer checks its size and SHA-256, and extracts only
   the payload for the device's LK build.

## 3. Run the installer

```sh
./run-one-shot.sh "$TAG" --fastboot-serial auto --slots both --execute-hardware
```

If the device is locked, add the Amonet ZIP:

```sh
./run-one-shot.sh "$TAG" --amonet-zip ~/Downloads/amonet-biscuit-v2.0.0.zip \
  --fastboot-serial auto --slots both --execute-hardware
```

The installer:

1. Verifies the release and the installer checksum.
2. Reads the fastboot product and checks it against the release target.
3. If the bootloader is unlocked, skips the unlock step. If it is locked,
   selects the payload for the exact `lk_build_desc`, verifies it, and sends it
   with `fastboot flash brick`.
4. Formats userdata, flashes both verified boot slots, reboots, and waits for
   ADB.
5. Verifies boot readback, stages the feature payloads, and creates the
   temporary setup forward.

`--slots both` is intentional: it writes and verifies both boot slots. Do not
substitute a raw boot image, OTA archive, or manually selected partition.

The installer does not erase `expdb`. The Kaeru unlock proof there is left
intact.

Never run the installer on a device whose product does not match the release
target. Use the device's own fastboot product; do not pass `--target` to
override a mismatch.

## 4. Watch the output

Keep the terminal open. The installer prints each stage and writes a log to
`./libreecho-installer.log`. If a stage fails, do not retry with a different
image or partition until you understand the error. Preserve the log and the exact
error text.

## Recovering from a forwarding failure

Do not restart the fresh `one-shot` flow after boot slots have been written.
Set `TAG` to the exact resolved tag from the original log (not `latest`), and
`SERIAL` to that device's original serial. Keep the original cache and state
roots, install ID, and slot selection. For example, after all payloads were
staged but local port 18080 was unavailable:

```sh
./run-one-shot.sh "$TAG" --continue --fastboot-serial "$SERIAL" \
  --slots both --local-port 18081 --execute-hardware
```

Continuation from `FEATURES_STAGED` or `WEBUI_FORWARDED` verifies the cached
inputs, boot-slot readback, and all installed payloads and manifests before
forwarding. It performs no format, flash, reboot, or payload replacement in
those states. Open the URL printed by the successful continuation. A missing
device, changed hash, or incomplete staging marker stops the operation rather
than guessing. Keep the failure log; never edit phase or format markers to
bypass a refusal.

## 5. First boot and setup

1. The installer has rebooted the device and waited for ADB. Do not touch the
   device or reconnect cables while it is powered.
2. Verify the temporary ADB connection and forward:

   ```sh
   adb wait-for-device
   adb get-state
   adb forward --list | grep 'tcp:18080'
   ```

3. Before Wi-Fi setup, open the installer's forwarded setup page:

   ```text
   http://127.0.0.1:18080/setup.html
   ```

   If the browser did not open automatically, enter that URL manually while the
   USB forward is active.
4. Complete the account and setup wizard. After Wi-Fi is applied, disconnect
   power before reconnecting any cables. Reconnect only while unpowered, then
   power on again.
5. On the normal LAN, open the advertised control-centre address:

   ```text
   http://libreecho.local:8080/
   ```

   If mDNS is unavailable, use the IP address assigned by your router:
   `http://<device-ip>:8080/`.
6. Verify that `libreecho.local` resolves and that the control centre remains
   reachable after reconnecting to the normal LAN.
7. Test the features listed for the release.

The first-run setup creates the local administrator account and stores Wi-Fi
credentials on the device. The installer does not invent, print, or upload those
credentials. A factory reset later removes the account, setup marker, Wi-Fi
profiles, and other mutable configuration, then reboots to this first-run state.

## Troubleshooting

### The device is not detected

- Confirm the USB cable is data-capable and connected directly, not through a
  hub.
- Run `adb devices` and `fastboot devices`. If neither shows the device, try
  another cable or port.

### The product does not match the release

The installer refuses to write when the device's fastboot product does not match
the release target. Do not force it with `--target`. Confirm that you are using
the bundle for this device.

### The device is locked and no Amonet ZIP was provided

Pass the pinned archive with `--amonet-zip`. The installer requires it only when
`unlock_status` is not `true`.

### The Amonet ZIP is refused

The archive's SHA-256 or size does not match the pin, or the device's `lk_build_desc`
is not in the accepted list for its target. Use the exact archive named in the
table above. For Biscuit, any LK build is accepted; the default payload covers builds without a reviewed mapping.

### The brick step reports `eMMC-RO` or `Device mismatch`

These are hard stops. The installer does not retry them. Preserve the log and the
exact error text, and do not try another payload.

### Installer reports a write failure

Stop and note the error message. Do not try a different partition or image. Ask
for help with the release version, board revision, and error message.
