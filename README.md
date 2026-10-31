<div align="center">

<img src="assets/libreecho-mark.svg" alt="LibreEcho logo" width="88">

# LibreEcho

### A repairable, private voice assistant for hardware you already own.

[![Platform checks](https://github.com/aslater3/LibreEcho-Platform/actions/workflows/ota-release.yml/badge.svg?branch=main)](https://github.com/aslater3/LibreEcho-Platform/actions/workflows/ota-release.yml)
[![UI checks](https://github.com/aslater3/LibreEcho-UI/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/aslater3/LibreEcho-UI/actions/workflows/checks.yml)
[![Latest OTA](https://img.shields.io/github/v/release/aslater3/LibreEcho?display_name=tag&label=latest%20OTA&logo=github)](https://github.com/aslater3/LibreEcho/releases/latest)
[![Website](https://img.shields.io/badge/site-libreecho.org-16c7d9)](https://libreecho.org/)

[Visit libreecho.org](https://libreecho.org/) | [Join us on Discord](https://discord.gg/5zBcTWjU4H) | [Download the latest release](https://github.com/aslater3/LibreEcho/releases/latest) | [Support the project](https://buymeacoffee.com/libreecho)

</div>

LibreEcho replaces the software on an Amazon Echo with an open, local-first
voice assistant. It runs a standalone Linux 6.1 kernel on the MediaTek MT8163,
is set up and managed from a web control centre on your own network, and
updates through signed releases that fall back safely if an update fails. Your
voice, settings and home stay on the device (see
[Privacy and telemetry](docs/privacy.md)).

## Supported devices

One release covers both models. The installer reads the device's fastboot
product and picks the matching build automatically.

| Device | Target | Fastboot product | How it connects | Fastboot entry (stock device) |
|---|---|---|---|---|
| **Amazon Echo (2nd Gen)** | `radar_puffin` | `RADAR` | USB pins on the bottom: pogo-pin adapter or a soldered cable | Hold **Action** while plugging in power |
| **Amazon Echo Dot (2nd Gen)** | `biscuit` | `BISCUIT` | Its own micro-USB port; no opening needed | Hold **Action** while plugging in the micro-USB cable |

Both models run the same kernel, system and features, with a speaker profile
tuned for each. Echo Dot support is newer and has had less field testing than
the Echo; please report Dot problems with a diagnostic bundle from
**Settings**.

The current stable release is
[`radar-puffin-v0.14.4`](https://github.com/aslater3/LibreEcho/releases/tag/radar-puffin-v0.14.4)
([release note](release/radar-puffin-v0.14.4.md)). See
[the 0.14.0 release note](release/radar-puffin-v0.14.0.md) for what is new in
the 0.14 line and the [changelog](CHANGELOG.md) for every release.

## Install

The complete procedure is the
[Echo 2nd Gen one-shot installation guide](docs/install/README.md), which also
covers the Echo Dot 2nd Gen. Read it before you start. The short version
follows.

**No shorting is needed.** Installation runs over USB fastboot from the stock
bootloader.

### 1. Get the host tools

You need a Linux computer with `bash`, `curl`, Python 3, `adb`, `fastboot` and
`e2fsprogs`. On Debian/Ubuntu:

```sh
sudo apt-get update
sudo apt-get install adb fastboot e2fsprogs
```

### 2. Connect the device in fastboot

- **Echo 2nd Gen:** remove only the outer cover on the base and seat a pogo-pin
  adapter on the six gold USB contacts. A printable carrier is in
  [`tools/`](tools/README.md#echo-gen-2-pogo-pin-carrier-v5), and a ready-made
  adapter will be on sale soon. Alternatively, solder a USB cable to the D+, D−
  and GND pads using the [disassembly guide](docs/disassembly/echo-gen-2/README.md).
  With the power adapter unplugged, connect USB to the computer, then hold
  **Action** and plug the power in.
- **Echo Dot 2nd Gen:** hold **Action** and plug a data-capable micro-USB cable
  from the computer into the Dot. The cable also powers it.

A device that has already run LibreEcho (unlocked with Kaeru) ignores
**Action**. Hold **Volume Down (−)** on its own instead. See
[Which button to hold](docs/install/README.md#which-button-to-hold).

Check that the computer sees it:

```sh
fastboot devices
fastboot getvar product   # RADAR = Echo 2nd Gen, BISCUIT = Echo Dot 2nd Gen
```

### 3. Run the installer

```sh
TAG=latest
curl -fL -o run-one-shot.sh "https://raw.githubusercontent.com/aslater3/LibreEcho/main/tools/run-one-shot.sh"
chmod +x run-one-shot.sh
./run-one-shot.sh "$TAG" --fastboot-serial auto --slots both --execute-hardware
```

`latest` resolves to the current stable release. The wrapper checks the
installer against the release checksums before running it, and the installer
verifies everything it downloads.

**Locked (stock) devices** also need the community Amonet unlock archive for
their model. LibreEcho does not redistribute it: supply the exact file and pass
it with `--amonet-zip`. The installer checks its size and SHA-256 and refuses
any other file.

| Device | Archive | SHA-256 |
|---|---|---|
| Echo 2nd Gen | `amonet-radar-v1.0.0.zip` | `ecdb07bc05a508532e5ffed77121592d492b1a91572839e0f17545421f398f1a` |
| Echo Dot 2nd Gen | `amonet-biscuit-v2.0.0.zip` | `98297293701082bc7272efe077f941c56fc7b6e1f27ef6f2e93b6e4c6fc7b62d` |

```sh
./run-one-shot.sh "$TAG" --amonet-zip ~/Downloads/amonet-biscuit-v2.0.0.zip \
  --fastboot-serial auto --slots both --execute-hardware
```

The installer unlocks the device only if it is locked, formats userdata, writes
and verifies both boot slots, installs the voice and media features, then opens
the first-boot setup page at `http://127.0.0.1:18080/setup.html` over USB. If
the connection is interrupted it can resume safely; read
[Recovering from a forwarding failure](docs/install/README.md#recovering-from-a-forwarding-failure)
before re-running anything.

### 4. Set it up

Create the administrator account and join Wi-Fi in the setup wizard. After
that, the control centre is at `http://libreecho.local:8080/` (or
`http://<device-ip>:8080/`). Later releases install from **Settings**.

### Upgrading from 0.13, or reinstalling

A device on 0.13.x cannot update itself to 0.14. Reinstall it with the steps
above. Installing formats userdata, so run the setup wizard again afterwards.

To reinstall a device that already has a 0.14 installation, for example a
0.14.0 install that stopped at *waiting for startup-ready*, use the
`--reinstall` option of the 0.14.1 or later wrapper. It does not repeat the unlock and moves a
running device into fastboot itself. It **erases userdata** and asks you to
type `ERASE` first:

```sh
TAG=radar-puffin-v0.14.4
curl -fL -o run-one-shot.sh "https://github.com/aslater3/LibreEcho/releases/download/${TAG}/libreecho-${TAG}-run-one-shot.sh"
chmod +x run-one-shot.sh
./run-one-shot.sh "$TAG" --reinstall --execute-hardware
```

### Early Echo 2nd Gen installs on the old boot chain

Some Echo 2nd Gen units installed before the Echo-specific bootloader existed
still report `BISCUIT` in fastboot. The
[Radar Kaeru migration kit](tools/radar-kaeru-migration/README.md) converts the
boot chain in place, with gated writes, backups and readback checks. Always run
its `--dry-run` first. Its full write path has not yet been run end to end on an
old-chain device, so treat early conversions as a test and keep the logs.

### Going back to stock Fire OS

To put Amazon's Fire OS and Alexa back on a LibreEcho unit, follow
[Going back to stock Fire OS](docs/install/restore-stock.md). It flashes
Amazon's own update package from TWRP into both slots. The bootloader stays
unlocked.

## What you get

- **A voice assistant on the device.** Wake word, speech-to-text and
  text-to-speech run locally, with Local and Custom voice modes and an optional
  local LLM endpoint. Timers, weather (including Met Office models and UK
  postcode lookup) and an optional spoken clock.
- **Home Assistant.** A native ESPHome API satellite: adopt the device through
  the ESPHome integration for voice, media, announcements and timers.
- **Music and audio.** AirPlay (from Music Assistant, use Sendspin or an AirPlay
  streaming mode that works: see [the Sendspin guide](docs/audio/sendspin.md)),
  multiroom audio with Music Assistant (Sendspin), internet radio (including HLS/AAC streams such
  as BBC radio), Opus and Ogg playback, USB storage browsing, and speaker
  tuning for each model.
- **Nursery.** A baby monitor that streams to phones, including iPhone Safari,
  plus white, pink and brown noise and sleep sounds with an optional heartbeat.
  The Action button can switch a sleep sound on and off.
- **A web control centre.** Device, audio, wake word, network, buttons, logs,
  diagnostic export and factory reset, all on your own network.
- **Safe updates.** Each update installs a complete, signed generation. The
  previous one stays until the new one has booted and confirmed, so a failed
  update falls back cleanly. Updates come from `dl.libreecho.org` with GitHub as
  a fallback, and can install automatically if you opt in.
- **Optional SSH**, off until you turn it on in the web interface, and signed in
  with the control centre's accounts.

## Privacy

LibreEcho sends one anonymous weekly ping to count active devices by hardware
model and version. It carries no device ID, serial number, MAC address or
account, setup and the **Privacy** page show the exact body sent, and from
0.14.5 it can be switched off on the **Privacy** page. Health, usage and crash
report choices start ticked on new setups and can be turned off; this release
only records the choice and sends no health, usage or crash data. Details:
[Privacy and telemetry](docs/privacy.md).

## Hardware roadmap

| Status | Devices |
|---|---|
| **LibreEcho working** (MT8163) | Echo 2nd Gen (`radar` / Puffin), Echo Dot 2nd Gen (`biscuit`) |
| **Next candidates** (MT8516 family) | Echo Dot 3rd Gen (`donut` / `crumpet`), Echo 3rd Gen (`pascal`), Echo Plus 2nd Gen (`lidar`), Echo Studio (`octave`) |

Access to a device is not the same as a completed port: each new target still
needs device-specific kernel, recovery, packaging and runtime validation.

The MT8183 family is an active research area: Kaeru contains explicit MT8183
support and Amazon-specific LK groundwork for the Fire HD 10 (2019), but an
initial-access route still needs proving. MT8512 and MT8519 devices are also
research candidates for possible Fenrir <code>bl2_ext</code> applicability; this
does not mean Fenrir is currently proven on Echo hardware.

See the [full hardware compatibility and porting matrix](https://libreecho.org/#hardware-roadmap)
on the website, including relevant access repositories and the suggested porting
priority.

To add a model yourself, follow the
[porting guide](docs/porting/README.md). It covers target profiles, the device
tree, audio, light and button mapping and the acceptance checklist, using the
Echo Dot 2nd Gen as the worked example.

## Repositories

| Repository | Owns |
| --- | --- |
| [`LibreEcho`](https://github.com/aslater3/LibreEcho) | Product site, hosted image CI, documentation, support, roadmap, issues, release notes, and OTA distribution |
| [`LibreEcho-Platform`](https://github.com/aslater3/LibreEcho-Platform) | ARM32 product tooling, initramfs, feature packaging, and OTA verification/build inputs; the historical 3.18 tree remains here for compatibility |
| [`LibreEcho-Linux`](https://github.com/aslater3/LibreEcho-Linux) | MT8163 Linux 6.1 kernel on upstream stable history, with kernel-side platform changes and the in-tree connectivity stack. Replaces the archived [`LibreEcho-Linux-6.1`](https://github.com/aslater3/LibreEcho-Linux-6.1), which holds the source for releases up to 0.14.4 |
| [`LibreEcho-UI`](https://github.com/aslater3/LibreEcho-UI) | Web control centre, HTTP API, service daemons, and UI tests |

See [the repository boundary guide](docs/repositories.md) for where a change belongs.

## Hardware modifications

Optional, irreversible changes to the device itself.

| Guide | What it enables |
|---|---|
| [Adding a USB tail](docs/usb-tail-mod.md) | ADB and external USB storage on the Echo 2nd Gen, by bringing a short cable out of the case |

## Support

Use the [issue tracker](https://github.com/aslater3/LibreEcho/issues/new/choose)
for reproducible bugs and hardware problems. Include the device model (Echo or
Echo Dot), LibreEcho version, active slot, relevant logs or a diagnostic
bundle, and the smallest reliable reproduction. Do not include Wi-Fi passwords,
API tokens, or private keys. For setup help, join us on
[Discord](https://discord.gg/5zBcTWjU4H).

Security vulnerabilities must not be filed publicly. Read
[SECURITY.md](SECURITY.md) for supported versions, the private reporting
route, response targets, redaction requirements and release-withdrawal
guidance. Published advisories are archived in
[SECURITY-ADVISORIES.md](SECURITY-ADVISORIES.md).

## License

LibreEcho is released under the MIT License. Component repositories may carry
their own copies of the license and additional notices. Release bundles also
contain third-party components under their own licences, including a
noncommercial wake-word model; read each release's notes before redistributing.
