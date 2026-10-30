# LibreEcho radar-puffin v0.14.5

LibreEcho 0.14.5 replaces 0.14.4 and includes everything in it (see
[the 0.14.4 note](radar-puffin-v0.14.4.md)). It is a feature release: from
0.14.5 on, new features ship in the 0.14 line instead of waiting for 0.15.
It adds multiroom audio with Music Assistant, a news panel, local services
that survive updates, and several smaller settings, plus security updates and
update fixes.

## Multiroom audio with Music Assistant (Sendspin)

LibreEcho can now join [Music Assistant](https://music-assistant.io/) as a
Sendspin speaker, on its own or in a group of speakers that play in time with
each other. It is off by default: turn it on under **Integrations →
Sendspin (Music Assistant)**, then pair the speaker in Music Assistant with the
token shown on that card. See [the Sendspin guide](../docs/audio/sendspin.md).

- Discovered automatically on the local network (mDNS) and paired once with a
  token. The pairing survives restarts and updates. **Forget paired servers**
  issues a new token, and every server then has to pair again.
- FLAC (preferred) or PCM, 48 kHz stereo. Opus is not supported.
- Volume is the speaker volume, in both directions. A mute from Music
  Assistant silences only the music; alarms, timers and voice replies still
  play.
- Grouped speakers are aligned to their measured output delay, including each
  model's speaker processing.
- Playback starts about 1.5 seconds after pressing play while the amplifier
  powers up. The speaker keeps its output running for 5 seconds after music
  stops, so a seek resumes in about 0.5 s and a resume within a few seconds of
  pausing takes about 1 s.
- Known limits: one Music Assistant server at a time. Occasionally a speaker
  that starts or rejoins a group begins a few milliseconds off and settles
  within a few seconds.

## LibreEcho News

The dashboard can show a short **LibreEcho News** panel: new releases, progress
and security notes. It is off by default. When you turn it on, the device
fetches one small public file (`news-v1.json`) from `dl.libreecho.org`, or
from GitHub if that fails, only when the dashboard asks and at most every
12 hours. It sends nothing else. Items are plain text and links go only to
the project's own sites (LibreEcho-UI #349, Product #273).

## Local services that survive updates

Your own programs can live in `/data/libreecho/local/services`, which A/B
updates never touch. Turn **Local services** on under **System** (or create
`/data/libreecho/local/enabled` over USB ADB).
[The local services guide](https://github.com/aslater3/LibreEcho-Platform/blob/release/0.14.5/tools/mt8163-arm32/local/README.md)
explains the layout.

- Services start only after LibreEcho is up and, after an update, only once the
  update is confirmed healthy, so a broken service can neither block boot nor
  roll an update back.
- They run at low priority. A service that keeps crashing is stopped. If
  services were on for three boots in a row that did not stay up, they are
  skipped until you switch them off and on. Holding the Action button while
  the device starts also skips them for that boot.
- (LibreEcho-Platform #262, #303; LibreEcho-UI #348)

## Settings

- **Room.** Optionally tell the device which room it is in. The local assistant
  is told the room on each turn, and Home Assistant gets it as the suggested
  area when it adds the device (LibreEcho-UI #344).
- **Idle light auto-off.** The idle light can turn itself off after 5, 10, 30 or
  60 minutes without activity (LibreEcho-UI #345).
- **Setup colour scheme.** The control centre can use the setup wizard's
  darker colours. The choice is saved on the device (LibreEcho-UI #346, #432).
- **Recovery and reflash.** The **Device** page has a panel that restarts the
  device into recovery for a reflash, without opening the case. It is kept
  apart from the power controls and asks for confirmation (LibreEcho-UI #347).
- **SSH status.** The SSH switch now shows whether this image has SSH at all
  and what it is doing. Account names that SSH cannot use are reserved. SSH is
  on by default unless you switch it off (LibreEcho-UI #350, Platform #261).
  A fix makes SSH actually start: the server was launched with an option the
  shipped Dropbear does not accept (Platform #301).
- **Weekly active-device count.** **Privacy › Active-device count** has a switch
  to stop the anonymous weekly ping (LibreEcho-UI #415, Platform #287).

## Security updates

- **Shairport Sync 5.1 → 5.6.2** (AirPlay): fixes remotely reachable bugs,
  including an unauthenticated RTSP stack overflow, a crash from a short pairing
  message, and several out-of-bounds reads. **NQPTP 1.2.8 → 1.2.9** goes with
  it (LibreEcho-Platform #289, LibreEcho-UI #421).
- **Mbed TLS 3.6.4 → 3.6.7**: about 30 advisories on the 3.6 LTS line
  (LibreEcho-Platform #289, LibreEcho-UI #419).
- **FFmpeg 6.1.6** (Product #308).

## Updates

- **Updates no longer run out of space.** Assembling a new update needs about
  230 MB in `/data`. Leftovers were never released: pins of superseded
  candidates, files from a direct install, quarantined packages and stale
  partial downloads. On some devices that blocked every further update with
  `generation-space` (LibreEcho-Platform #291, #304).
- **A valid cached update is no longer quarantined** and downloaded again. This
  could also fill the quarantine and stop updates altogether
  (LibreEcho-Platform #278).
- **Update event log.** `/data/libreecho/update/ota.log` keeps a short history
  of update checks and installs, also in `logread` as `libreecho-ota`, so a
  failed update can be diagnosed afterwards (LibreEcho-Platform #279).

## Other changes

- **Echo Dot speaker unmute** is about 0.1 s quicker: the codec driver now
  checks every 5 ms whether the gain has been applied, instead of every 100 ms
  (LibreEcho-Linux #9).
- **Music visualizer** follows the beat more reliably (LibreEcho-Platform #265).
- **Installer and tools:**
  - the TWRP handoff and a harmless "restricted on locked hw" reply no longer
    stop a fresh install (Product #298);
  - `run-one-shot.sh --fastboot-serial auto` works with `fastboot` builds that
    print a trailing empty line (Product #323);
  - the Kaeru migration kit accepts the legacy Echo Dot userdata size
    (Product #306);
  - the install guide adds notes for USB connection order and WSL/usbipd
    (Product #307, #312).

## Upgrading

- **From 0.14.0 – 0.14.4:** update over the air. In the web UI open
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

## Known issues

- **AirPlay from Music Assistant:** with Music Assistant's AirPlay streaming
  mode on *Automatic* (the default, which picks *AirPlay 2 - PTP timing*) or
  *AirPlay 2 - NTP timing*, the speaker stays silent while Music Assistant
  shows it playing. Music Assistant documents Shairport Sync and AirPlay 2 as
  incompatible. Use **Sendspin** for Music Assistant, or set the speaker's
  AirPlay streaming mode to *AirPlay 2 - compatibility mode* or
  *AirPlay 1 (RAOP)*, which both play. See
  [the Sendspin guide](../docs/audio/sendspin.md#airplay-from-music-assistant).
  AirPlay from Apple devices is a different sender.

## Validation

Host tests for every change, plus a physical Echo 2nd Gen (`radar_puffin`) and
a physical Echo Dot (`biscuit`) running development builds of these sources:

- **0.14.4 stable → 0.14.5 candidate → 0.14.5 final candidate** on both units,
  through the device updater (`libreecho-generation` assemble and
  `libreecho-update install`, then a product reboot). Each update booted the
  new slot, reached startup-ready and confirmed the slot. On the final
  candidate both units kept the same slot and transaction across a further
  reboot. The final update needed no manual clean-up of `/data`.
- **Settings and features, on both units through the web API** (every setting
  restored afterwards): room, setup colour scheme, idle light auto-off, SSH
  status fields, the recovery panel present (not triggered), and the new UI
  panels served. Local services: a test service started, ran and stopped from
  the switch; with nothing set up the status reads *off*.
- **News:** the device fetched the published feed (6 items) from
  `dl.libreecho.org`.
- **Sendspin with Music Assistant 2.10.8**, both units grouped: token pairing,
  grouped playback, pause and resume (about 1 s after a short pause, 2.4 s after
  a long one), an announcement and a ringing timer during playback without a
  break, Forget paired servers, and disable. Output timing in server time
  differs by about 0.95 ms between the two units on clicks, the same for FLAC
  and PCM. That is the Echo Dot's speaker processing delay for very short
  clicks; on development builds the acoustic skew measured 0.03–0.04 ms.
- **AirPlay:** Music Assistant to the Echo 2nd Gen through Shairport Sync
  5.6.2: *AirPlay 1 (RAOP)* and *AirPlay 2 - compatibility mode* play;
  *AirPlay 2 - PTP timing* (the *Automatic* choice) and *AirPlay 2 - NTP
  timing* do not.
- **Echo Dot speaker unmute:** cold unmute wait 0.97 s, against 1.10 s on
  earlier builds.

Not yet tested on a physical device: AirPlay 2 from an Apple device, an
update started from the web UI (**Check for updates → Install update**), a
fresh install or `--reinstall` of 0.14.5, SSH starting with the Dropbear
fix, the weekly-ping switch, and a forced rollback from 0.14.5.
