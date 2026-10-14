# LibreEcho radar-puffin v0.14.0

LibreEcho 0.14.0 is the first release built on immutable, signed OTA v3
target-state generations, and the first release that supports two Echo models
from one release: the Amazon Echo (2nd Gen, `radar_puffin`) and the Amazon Echo
Dot (2nd Gen, `biscuit`).

## Upgrading from 0.13: reinstall required

**A device running 0.13.x cannot update itself to 0.14.0.** The 0.13 updater
only accepts OTA v1/v2 packages, and 0.14 ships only OTA v3. After 0.14.0 is
published, a 0.13 device's update check downloads the new package, rejects it
and reports `downloaded_package_invalid`. Nothing is installed and the device
keeps running 0.13 safely, but it will not move to 0.14 on its own.

To move to 0.14.0, reinstall with the installer in the
[installation guide](../docs/install/README.md). The installer formats
userdata, so settings, Wi-Fi credentials, accounts and pairings are not kept.
Run the setup wizard again after the first boot. Once installed, 0.14.0 takes
later OTA v3 releases through Settings.

## Highlights

### Updates: immutable signed generations (OTA v3)

- Each update installs a complete, signed target state (boot image plus every
  feature payload) as a new generation. The previous generation stays intact
  until the new one has booted and confirmed, so a failed update falls back
  without partial state.
- The updater records the resolved release tag and OTA hash with each check,
  clears the reboot-pending state once an update is confirmed, and reclaims its
  working set after a refused install.
- Updates are fetched from `dl.libreecho.org`, falling back to GitHub releases.
- Settings shows boot-slot confirmation state, and can restart the device into
  the installer (fastboot) without opening it.

### Two Echo models, one release

- Per-target images, recovery bundles, OTA packages and update channels for the
  Echo (2nd Gen) and the Echo Dot (2nd Gen).
- Per-target speaker profiles: the Echo keeps its stock-derived crossover,
  loudness and parametric EQ; the Dot gets a stock-like chain of its own.
- One-shot installation from stock fastboot. The installer reads the fastboot
  product to confirm the model, runs the Amonet step only when the bootloader is
  locked, never erases `expdb`, verifies and confirms the running boot slot, and
  can resume safely after an interrupted connection.

### Home Assistant via ESPHome

The Wyoming satellite is replaced by a native ESPHome API satellite. Home
Assistant adopts the device through the ESPHome integration, with encrypted API
transport, discovery, voice, media, announcements and timers. Local and Custom
voice modes remain; Custom can still use Wyoming STT/TTS servers. Discovery no
longer depends on AirPlay being installed: one shared mDNS runtime is built into
every image.

### Audio, media and the speaker

- Internet radio, including HLS/AAC streams such as BBC radio; Opus and Ogg
  playback; USB storage browsing.
- White, pink and brown noise and nursery sounds, shown in Now Playing.
- AirPlay announces the configured device name, follows the master volume,
  survives a busy or briefly lost controller, and keeps its LED bridge after the
  LED daemon restarts.
- Radar speaker protection with multiband dynamics, and a selectable codec
  profile.

### Voice and assistant

- A local LLM endpoint and model can be set on the Integrations page.
- Spoken stop requests are handled before any model call; ringing timers keep
  priority.
- Wake-word diagnostics separate loaded models from live capture. Owners can
  stage a wake model and keep a persistent microphone gain.
- Weather, including Met Office UKMO models and UK postcode lookup, and an
  optional 12-hour spoken clock.

### Device and web UI

- All four buttons are wired, with press feedback and settings. The mute lamp
  follows software mute without entering the hardware privacy latch.
- The idle ring defaults to a dim green front indicator. The visualiser no
  longer freezes under music.
- Baby Monitor streams on iPhone Safari.
- A Download kernel log button on the Logs page, and a diagnostic export that
  records the update channel and running boot slot.
- Ambient light from the board's light sensor, thermal zones chosen by rank,
  and supervised adapter daemons that are restarted when they stop answering.
- Factory reset stops every persistent-state writer first, and backups refuse
  symlinked state.
- Many HTTP, WebSocket, Wi-Fi scan and Bluetooth reliability fixes.

### Privacy

0.14 sends an anonymous weekly ping to `stats.libreecho.org` to count how many
devices run each version. It carries only the hardware model, version, channel,
development build hash, the week/month/year and a one-time install flag. It
carries no device ID, serial, MAC address or install date. Setup and the
Privacy page disclose it and show the exact body sent. Setup also asks for
health/usage and crash-report consent. 0.14 sends neither of those yet. See
[Privacy and telemetry](../docs/privacy.md).

### Safety

- Development and release images never write the `expdb` fastboot marker. The
  build contract and image verifier both enforce this.
- Optional services can no longer expire a fresh install's only boot slot.

## Validation

The release candidate (Product `3bd26c4`, Platform `122f090f`, UI `e9864654`,
Linux `ae49bb09`) was validated on an Echo (2nd Gen):

- a fresh install of both slots;
- setup, Wi-Fi and internet access;
- repeated reboots with the slot confirmed;
- a signed OTA v3 update to the inactive slot, with post-update confirmation;
- a Settings restart to fastboot and back;
- `misc` and `expdb` integrity before and after each step.

The Echo Dot (2nd Gen) was validated on an earlier 0.14 candidate (Product
`cc238ba`): a signed OTA v3 install, boot and slot confirmation. The boot-slot
changes made after that candidate have been validated on the Echo only.

The feature work above is covered by host and CI tests, and was exercised on
earlier 0.14 candidates. It was not re-run as a full hardware checklist on this
candidate.

## Known issues

- 0.13 devices report `downloaded_package_invalid` when they check for updates
  after 0.14.0 is published. Reinstall to move to 0.14 (see above).
- The diagnostic export reports `network` as unavailable on device builds. The
  Wi-Fi state is shown on the Network page.
- GPT-Live voice is not part of 0.14 and is hidden in the UI.
