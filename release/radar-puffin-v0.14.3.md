# LibreEcho radar-puffin v0.14.3

LibreEcho 0.14.3 replaces 0.14.2 and includes everything in it (see
[the 0.14.2 note](radar-puffin-v0.14.2.md)). It is an audio-quality release:
Bluetooth playback no longer crackles, the phone's volume controls the speaker,
and the speaker path keeps 24-bit precision all the way to the DAC.

## Bluetooth audio

- **No more crackle on Bluetooth.** Phones send 44.1 kHz audio but the speaker
  runs at 48 kHz. 0.14.2 resampled each Bluetooth packet separately, which left
  a small discontinuity at every packet boundary. Bluetooth audio now goes
  through the same continuous resampler as the other sources
  (LibreEcho-UI #358).
- **Phone volume controls the speaker (AVRCP absolute volume).** The phone's
  volume buttons and slider set the speaker's own master volume. Volume changes
  made on the device or in the web UI are reported back to the phone, so both
  stay in step. Bluetooth and AirPlay now share one volume and one route into
  the speaker (LibreEcho-UI #359).

## Speaker output

- **24-bit output.** The audio engine keeps the signal in floating point from
  the master volume through the EQ, the multiband limiter and the crossover,
  and sends 24-bit samples (S32_LE) to the DAC. 0.14.2 rounded to 16 bits at
  each stage, so at normal listening levels only 8–10 significant bits reached
  the speaker, which could sound grainy on quiet passages and vocals. In host
  measurements, distortion plus noise of a 1 kHz tone at volume 50 drops from
  −42 dB to −80 dB on the Echo 2nd Gen chain (LibreEcho-Linux-6.1 #52,
  LibreEcho-Platform #267). If the kernel does not accept 24-bit output, the
  engine falls back to the 16-bit path, which renders exactly as before.
- The speaker EQ keeps the stock-matched loudness shelves. A change in a
  0.14.3 candidate that flattened them removed the stock presence lift around
  3 kHz and was reverted before release (LibreEcho-Platform #266, #268).

## Volume leveling (experimental, off by default)

**Audio › Output** has a new **Volume leveling (experimental)** switch. It
evens out loudness between and within tracks, modelled on the stock firmware's
music leveler (LibreEcho-Platform #269, LibreEcho-UI #365, #367, #368).

It is **off by default** because the current version can audibly pump: on
some tracks the level rises and falls in the middle of a song. A rework that
follows the stock design more closely is planned for 0.15
(LibreEcho-UI #366). The setting is kept across reboots.

## Upgrading

- **From 0.14.0, 0.14.1 or 0.14.2:** update over the air. In the web UI open
  **System › System update**, choose **Check for updates**, then
  **Install update**, then **Reboot**.
- **Bluetooth:** if your phone no longer lists the speaker after the update,
  forget the device on the phone and pair it again.
- **From 0.13.x:** reinstall. The 0.13 updater cannot install OTA v3 packages,
  so a 0.13 device reports `downloaded_package_invalid` and stays on 0.13. Use
  the installer in the
  [installation guide](https://github.com/aslater3/LibreEcho/blob/main/docs/install/README.md).
  Reinstalling formats userdata: settings, Wi-Fi credentials, accounts and
  pairings are not kept.
- **Echo 2nd Gen (`radar_puffin`) on the old boot chain:** if
  `fastboot getvar product` reports `BISCUIT`, or the installer fails with
  `boot_a_x readback hash mismatch`, run the
  [Kaeru migration kit](https://github.com/aslater3/LibreEcho/tree/main/tools/radar-kaeru-migration)
  before reinstalling (`--dry-run` first; `--gpt-fix` if an older copy of the
  kit already converted the chain).

## Validation

Host tests for every change, plus two physical devices running the 0.14.3
development build of these exact sources:

- **Echo 2nd Gen (`radar_puffin`), 0.14.2 → 0.14.3** through the device
  updater. It booted the new slot and kept the same slot across two further
  reboots. The volume-leveling switch worked from the web UI and its setting
  persisted across those reboots. Earlier 0.14.3 candidate builds carrying the
  same audio code were checked on this unit for Bluetooth playback without
  crackle, phone volume driving the speaker in both directions, and 24-bit
  (S32_LE) output during AirPlay playback.
- **Echo Dot (`biscuit`), 0.14.2 → 0.14.3** through its own web UI (Check for
  updates → Install update → Reboot) on the development channel. It booted and
  reports 0.14.3; the owner confirmed it working after the update.

Not yet tested on a physical device: a fresh install or `--reinstall` of
0.14.3, an update from 0.14.0 or 0.14.1, a forced rollback from 0.14.3, and
24-bit output on the Echo Dot.
