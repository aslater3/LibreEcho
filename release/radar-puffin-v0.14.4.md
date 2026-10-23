# LibreEcho radar-puffin v0.14.4

LibreEcho 0.14.4 replaces 0.14.3 and includes everything in it (see
[the 0.14.3 note](radar-puffin-v0.14.3.md)). It is a fix release: better voice
pickup on the Echo 2nd Gen, a web server that no longer pegs a CPU core on
devices that have taken many updates, and smooth LED fades.

## Voice pickup (Echo 2nd Gen)

- **The microphone mix no longer comb-filters speech.** The wake-word and voice
  path used a fixed delay between two microphones that was only right for one
  direction, so speech from everywhere else lost parts of its spectrum. It is
  replaced by 12 fixed beams over the four microphones the stock firmware uses
  for speech. The beam follows the strongest talker; steady sounds such as
  fans or music do not take it (LibreEcho-UI #376).
- **Audio › Microphones** shows a live *Direction of arrival* view of where
  sound is coming from, also available as `GET /api/v1/microphones/beams`.

## Performance

- **The web server no longer uses a whole CPU core.** On a device that had
  taken many OTA updates, a full cache of hashes for retired builds made
  `libreecho-web` re-hash a running 34 MB feature program about once a second,
  keeping one core busy and the device warm. Retired entries are now dropped
  and running programs always keep cache space. On the affected Echo 2nd Gen,
  `libreecho-web` went from about 100% of one core to about 1%
  (LibreEcho-UI #385).

## Light ring

- **Smooth fades at low brightness.** The ring's driver chip has only 256 PWM
  steps, so dim colours stepped visibly: the sleep light's breathing moved in
  two jumps. The kernel can now also set each LED's current to ½, ⅓ or ¼
  (LibreEcho-Linux-6.1 #56), giving many more levels near black. The sleep
  light, Bluetooth pairing and Home Assistant/API pulses, the breathing mode
  and steady dim colours all use them (LibreEcho-UI #381, #386). Dim steady
  colours may look very slightly darker than in 0.14.3, because they now follow
  the brightness curve exactly instead of rounding up.
- **New boot animation.** While the device starts, a soft green segment rolls
  round the ring, replacing the one-pixel chase (LibreEcho-UI #386).

## Other fixes

- **Integrations › MQTT is hidden.** The switch saved a setting that nothing
  used, because the image has no MQTT client yet. An MQTT client is planned for
  0.15 (LibreEcho-UI #379).
- **Update fallback.** If `dl.libreecho.org` is unavailable, devices now fall
  back to the GitHub repository releases are published from. Previously the
  fallback pointed at a repository that does not exist yet, so a mirror outage
  failed every update check (LibreEcho-Platform #272).

## Upgrading

- **From 0.14.0 – 0.14.3:** update over the air. In the web UI open
  **System › System update**, choose **Check for updates**, then
  **Install update**, then **Reboot**.
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

Host tests for every change, plus a physical Echo 2nd Gen (`radar_puffin`)
running the 0.14.4 development build of these exact sources:

- **0.14.4 dev → 0.14.4 dev** through the device updater. It booted the new
  slot, reached startup-ready, confirmed it, and kept the same slot and
  transaction across a further reboot. The new boot
  animation showed during boot, and `libreecho-web` stayed at about 0–1% CPU.
- Earlier 0.14.4 candidate builds carrying the same code were checked on this
  unit for: the direction-of-arrival view running live, the MQTT card hidden,
  the hash cache pruning a full 32-entry cache of retired builds, and the light
  ring using the new current levels (read back from the driver).

Not yet tested on a physical device: the Echo Dot (`biscuit`), an update
straight from 0.14.3 stable, a fresh install or `--reinstall` of 0.14.4, and a
forced rollback from 0.14.4.
