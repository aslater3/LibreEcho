# Going back to stock Fire OS

> For Amazon Echo 2nd generation (`radar_puffin`) and Echo Dot 2nd generation
> (`biscuit`) units that run LibreEcho. This guide puts Amazon's Fire OS and
> Alexa back. The bootloader stays unlocked; see
> [What cannot be undone](#what-cannot-be-undone).

**Test status:** the LibreEcho maintainers have not yet run this guide end to
end on a LibreEcho unit ([#295](https://github.com/aslater3/LibreEcho/issues/295)).
It uses the TWRP recovery the unlock installs and the same TWRP commands that
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) runs on both
models to put Fire OS on freshly unlocked units. What it relies on about
LibreEcho itself (the partition list below, TWRP left in `recovery`) was read
from a running Echo Dot.

## What LibreEcho changed, and what this guide restores

| Part of the device | Changed by | After this guide |
|---|---|---|
| Boot slots `boot_a`, `boot_b` | LibreEcho installer and updates | Fire OS again |
| `system_a`, `system_b` | Not written by LibreEcho (it only reads vendor firmware from them) | Rewritten from Amazon's package |
| `userdata` | Formatted by LibreEcho | Wiped; Fire OS sets it up on first boot |
| `cache` | LibreEcho install records | Wiped |
| `recovery` (TWRP) | The unlock | Stays |
| Boot chain: preloader, LK, the Kaeru payload in `expdb`, the boot marker in `misc` | The unlock | Stays: the bootloader remains unlocked |
| Partition table | May have been changed by the unlock or, on some Echo 2nd Gen units, by the [Kaeru migration kit](../../tools/radar-kaeru-migration/README.md) | Stays |
| `persist` (calibration and device identity) | Never written | Unchanged |

LibreEcho does not keep a copy of the Fire OS boot images it replaced. The way
back is Amazon's own full update package, flashed from TWRP.

## What cannot be undone

- **The bootloader stays unlocked.** TWRP stays in `recovery`, and the Kaeru
  boot chain stays in place. Relocking means writing Amazon's preloader, LK and
  TEE back over the unlock, directly to the boot chain. The maintainers have
  done that on one unit of each model, with saved stock images, but a mistake
  there cannot be fixed over USB. This guide does not cover it.
- **Never write or erase `expdb`, and never flash the preloader, LK or TEE
  images from Amazon's package by hand.** `expdb` holds the Kaeru payload that
  the boot chain needs; without it the Echo no longer boots at all.
- **Amazon updates.** Once Fire OS is set up and online it updates itself. We
  have not tested what a later Amazon update does to an unlocked unit, and
  echo-dot-assist warns that an update can close the hole the unlock uses. If an
  update replaces the bootloader, the Echo behaves like a stock unit again: going
  back to LibreEcho then needs the Amonet ZIP, and the Echo 2nd Gen only accepts
  the LK builds listed in [Supported targets](README.md#supported-targets). If
  you might return to LibreEcho, keep the Echo offline until you decide.
- **LibreEcho data is erased:** settings, Wi-Fi profiles, accounts, pairings and
  installed features.

## Before you start

You need:

- the same USB connection you installed with: the USB pins on the bottom of an
  Echo 2nd Gen (see [Connect the device](README.md#2-connect-the-device)), or a
  data-capable micro-USB cable for an Echo Dot 2nd Gen;
- `adb` on the computer (Debian/Ubuntu package `adb`);
- Amazon's **full** update package for your model. These are the builds the
  unlock threads and echo-dot-assist pin:

| Device | Package | SHA-256 |
|---|---|---|
| Echo 2nd Gen | `update-kindle-radar_puffin-NS6572_user_6436_0012718777476.bin` (Fire OS 6572) | `976e2761c5bb9e5d3d9a2b1b9bc2f2296b89a5b37057ab1780743aa075f2df48` |
| Echo Dot 2nd Gen | `update-kindle-biscuit_puffin-NS65741_user_8146_0013222531716.bin` (Fire OS 6574.1) | `90832e86498c5e803974c30359aea71c1129757be0ddc59d831a94b27f81487f` |

LibreEcho does not redistribute Amazon's firmware. Download links for each
package are in the device pages of echo-dot-assist
([Echo 2nd Gen](https://github.com/Gamer92000/echo-dot-assist/blob/v2026.10.17.151309/devices/radar/README.md),
[Echo Dot 2nd Gen](https://github.com/Gamer92000/echo-dot-assist/blob/v2026.10.17.151309/devices/biscuit/README.md))
and in R0rt1z2's unlock threads on XDA
([Echo 2nd Gen](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-2nd-gen-2017-radar.4801290/),
[Echo Dot 2nd Gen](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/)).
Never flash a package for the other model. Check the file before you use it:

```sh
sha256sum update-kindle-*.bin
```

The result must match the table exactly.

## 1. Start TWRP

1. Unplug the Echo's power (on the Echo Dot, the micro-USB cable).
2. Echo 2nd Gen: seat the USB pins and connect them to the computer.
3. Hold **Volume Up (+)** only, and plug the power in while still holding it.
   See [Which button to hold](README.md#which-button-to-hold). Holding both
   volume buttons boots LibreEcho instead.
4. Check that the computer sees TWRP:

   ```sh
   adb devices -l
   adb shell getprop ro.twrp.version
   ```

   The Echo must be listed as `recovery`, and the second command prints a TWRP
   version (3.7 on the units the maintainers have checked). TWRP can report a
   different serial than fastboot or LibreEcho did. If more than one Android
   device is connected, add `-s <serial>` from `adb devices -l` to every `adb`
   command below.

If the Echo boots LibreEcho instead, unplug the power and repeat step 3.

## 2. Wipe LibreEcho and flash the first slot

```sh
adb shell 'twrp wipe cache && twrp wipe data'
adb push update-kindle-*.bin /sdcard/update.zip
adb shell twrp install /sdcard/update.zip
```

Wipe first, then copy: in TWRP, `/sdcard` is on `userdata`, so a package
copied before the wipe is deleted by it. The install takes a few minutes. Do not
unplug anything while it runs.

## 3. Flash the second slot

TWRP marks the slot it flashed as active, so the next install lands in the
other slot. Restart TWRP and install the same package again:

```sh
adb shell bcbtool get_active
adb reboot recovery
adb wait-for-recovery
adb shell twrp install /sdcard/update.zip
adb shell bcbtool get_active
```

The two `get_active` answers should differ (`a` and `b`). If they are the
same, the second install may not have reached the other slot, and repeating
it is unlikely to change that. Do not switch slots by hand with
`bcbtool set_active`. Go on to step 4: the active slot was just flashed with
Fire OS and boots it; the other slot may still hold the LibreEcho boot image.
Then open an issue (see
[If something goes wrong](#if-something-goes-wrong)) with both `get_active`
answers. If `bcbtool` is not available in your TWRP, run the commands without
the two `get_active` lines; you cannot check the slots then.

## 4. Boot Fire OS

```sh
adb shell rm /sdcard/update.zip
adb reboot
```

The first boot takes several minutes. Then set the Echo up with the Alexa app,
like a new device. Read [Amazon updates](#what-cannot-be-undone) before you do.

## Going back to LibreEcho later

Follow the [installation guide](README.md). The unit is still unlocked, so it
enters fastboot with **Volume Down (−)** and the installer skips the unlock; no
Amonet ZIP is needed. If an Amazon update has replaced the bootloader, follow
the guide as for a stock unit instead.

## If something goes wrong

- **Fire OS does not finish booting**: unplug the power, start TWRP again
  (step 1) and repeat steps 2 to 4 with a package whose SHA-256 matches.
- **The Echo reaches neither TWRP nor fastboot**: stop. Do not try other
  buttons, images or partitions. Open an issue in
  [aslater3/LibreEcho](https://github.com/aslater3/LibreEcho/issues) with the
  model, the step you reached and the exact output. Do not paste serial
  numbers.
