<div align="center">

<img src="assets/libreecho-mark.svg" alt="LibreEcho logo" width="88">

# LibreEcho

### A repairable, private voice assistant for hardware you already own.

[![Platform checks](https://github.com/aslater3/LibreEcho-Platform/actions/workflows/ota-release.yml/badge.svg?branch=main)](https://github.com/aslater3/LibreEcho-Platform/actions/workflows/ota-release.yml)
[![UI checks](https://github.com/aslater3/LibreEcho-UI/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/aslater3/LibreEcho-UI/actions/workflows/checks.yml)
[![Latest OTA](https://img.shields.io/github/v/release/aslater3/LibreEcho?display_name=tag&label=latest%20OTA&logo=github)](https://github.com/aslater3/LibreEcho/releases/latest)
[![Website](https://img.shields.io/badge/site-libreecho.org-16c7d9)](https://libreecho.org/)

[Visit libreecho.org](https://libreecho.org/) | [Join us on Discord](https://discord.gg/5zBcTWjU4H) | [Download the latest OTA](https://github.com/aslater3/LibreEcho/releases/latest) | [Support the project](https://buymeacoffee.com/libreecho)

</div>

The interface, API, and service daemons live in the separate
[LibreEcho-UI](https://github.com/aslater3/LibreEcho-UI) repository. That
repository is currently private pending a public-safety and licence review;
the public website does not present it as a logged-out source download.

LibreEcho is an open embedded voice-assistant operating system focused on
privacy, repairability, and local control. The current development line targets
the MT8163 ARM32 platform with a standalone Linux 6.1 kernel, separate product
tooling, a native web control centre, and a signed A/B update path.

The Linux 6.1 LTS-based line is the current development baseline. A complete
clean-source image has passed private hardware deployment and runtime
validation, but the project is not yet presented as a general-purpose upstream
Linux port or a stable public OTA release. Hardware fixes and service
integration continue on review branches.

## Start Here

- **Website:** [libreecho.org](https://libreecho.org/)
- **Latest release:** [signed OTA bundles](https://github.com/aslater3/LibreEcho/releases/latest)
- **Initial-install bootstrap:** [`tools/libreecho-install.py`](tools/libreecho-install.py) with its [SHA-256 sidecar](tools/libreecho-install.py.sha256). The mirror is release-gated against the canonical Build installer; hardware execution remains a separately qualified and explicitly authorized step.
- **Control centre:** [LibreEcho-UI](https://github.com/aslater3/LibreEcho-UI)
- **Hardware and OS:** [LibreEcho-Platform](https://github.com/aslater3/LibreEcho-Platform)
- **Issues:** [report a reproducible product problem](https://github.com/aslater3/LibreEcho/issues/new/choose)
- **Security:** [read the security policy](SECURITY.md) or [submit a private advisory](https://github.com/aslater3/LibreEcho/security/advisories/new)
- **Join the community:** [LibreEcho on Discord](https://discord.gg/5zBcTWjU4H)
- **Support:** [buy me a coffee](https://buymeacoffee.com/libreecho)

## Hardware Roadmap

LibreEcho currently runs on the Amazon Echo 2nd Gen (`radar` / Puffin) using
MediaTek MT8163V. The next most promising targets are the Echo Dot 2nd Gen
(`biscuit`), followed by MT8516-family devices such as the Echo Dot 3rd Gen
(`donut` / `crumpet`), Echo 3rd Gen (`pascal`), Echo Plus 2nd Gen (`lidar`) and
Echo Studio (`octave`).

Roadmap status distinguishes **LibreEcho working**, **access implemented**,
**potential on an already-supported SoC family**, **research candidates with
relevant boot-chain tooling**, and **new platforms**. Access to a device is not
the same as a completed LibreEcho port: each target still needs device-specific
kernel, recovery, packaging and runtime validation.

The MT8183 family is an active research area: Kaeru contains explicit MT8183
support and Amazon-specific LK groundwork for the Fire HD 10 (2019), but an
initial-access route still needs proving. MT8512 and MT8519 devices are also
research candidates for possible Fenrir <code>bl2_ext</code> applicability; this
does not mean Fenrir is currently proven on Echo hardware.

See the [full hardware compatibility and porting matrix](https://libreecho.org/#hardware-roadmap)
on the website, including relevant access repositories and the suggested porting
priority.

## What Is Included

- Local web administration for device, audio, wake word, networking, logs, and system settings.
- Signed A/B OTA updates with opt-in automatic installation and a manual update action.
- Linux kernel and initramfs bring-up for the MT8163 ARM32 platform.
- Clear component boundaries so UI work, hardware work, and product support can evolve independently.

## Repositories

| Repository | Owns |
| --- | --- |
| [`LibreEcho`](https://github.com/aslater3/LibreEcho) | Product site, documentation, support, roadmap, issues, release notes, and OTA distribution |
| [`LibreEcho-Platform`](https://github.com/aslater3/LibreEcho-Platform) | ARM32 product tooling, initramfs, feature packaging, OTA verification, and release workflow; the historical 3.18 tree remains here for compatibility |
| [`LibreEcho-Linux-6.1`](https://github.com/aslater3/LibreEcho-Linux-6.1) | Current standalone MT8163 Linux 6.1 kernel line and kernel-side platform changes |
| [`LibreEcho-UI`](https://github.com/aslater3/LibreEcho-UI) | Web control centre, HTTP API, service daemons, and UI tests |

See [the repository boundary guide](docs/repositories.md) for where a change belongs.

## Support

Use the issue tracker for reproducible bugs and hardware problems. Include the
device model, OS version, active slot, relevant logs, and the smallest reliable
reproduction. Do not include Wi-Fi passwords, API tokens, or private keys.

Security vulnerabilities must not be filed publicly. Read
[SECURITY.md](SECURITY.md) for the supported release scope, private reporting
route, redaction requirements and release-withdrawal guidance.

Once Discussions are enabled in the repository settings, use them for design
questions, setup help, and ideas that are not yet actionable bugs.

## License

LibreEcho is released under the MIT License. Component repositories may carry
their own copies of the license and additional notices.
