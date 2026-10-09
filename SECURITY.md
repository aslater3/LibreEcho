# Security policy

LibreEcho is an independent, volunteer-maintained project for recoverable
Amazon Echo Gen 2 experimentation. Please do not use public GitHub issues for
security vulnerabilities.

## Supported versions

Security fixes are made for the newest published stable release line. Only
signed stable releases published in this repository's
[GitHub Releases](https://github.com/aslater3/LibreEcho/releases) are in scope
for a security fix.

| Version | Release ID | Security fixes |
| --- | --- | --- |
| 0.14.x | `radar-puffin-v0.14.*` | Yes, from first stable publication |
| 0.13.19 | `radar-puffin-v0.13.19` | Yes, until 0.14.0 is published as stable |
| 0.13.18 and earlier | `radar-puffin-v0.13.18` … `radar-puffin-v0.1.0` | No. Upgrade to the latest stable release |
| Development and sandbox prereleases | `radar-puffin-build-*` | No. Report issues found there if they also affect a stable release or the release branch |

When 0.14.0 is published as stable, 0.13.19 stops receiving security fixes
and users should upgrade through the signed OTA path. A fix may be developed
on the active `release/X.Y.Z` branch before it is published.

Verify every release with its `libreecho-radar-puffin-vX.Y.Z-SHA256SUMS` file
and published OTA public key before use. Do not mix assets from different
release tags.

## Scope

Security reports are especially important for:

- authentication, session, CSRF, Origin or access-control bypasses in the web
  control centre and HTTP API;
- OTA signature, rollback, update or release-identity verification;
- remote control-plane or network-service exposure, including Home Assistant
  (Wyoming or ESPHome), AirPlay, mDNS, SSH and recovery access-point services;
- boot, recovery, privilege or arbitrary-write paths;
- credentials, tokens, owner-local firmware or private diagnostic disclosure;
- supply-chain, release workflow, signing or third-party provenance issues;
- the one-shot and browser installers.

All public LibreEcho repositories are covered by this policy:
[LibreEcho](https://github.com/aslater3/LibreEcho),
[LibreEcho-UI](https://github.com/aslater3/LibreEcho-UI),
[LibreEcho-Platform](https://github.com/aslater3/LibreEcho-Platform),
[LibreEcho-Linux-6.1](https://github.com/aslater3/LibreEcho-Linux-6.1),
[LibreEcho-Installer-Web](https://github.com/aslater3/LibreEcho-Installer-Web) and
[LibreEcho-Docs](https://github.com/aslater3/LibreEcho-Docs). Report them all
through the single private route below.

### Documented deployment boundaries

The following are intentional, documented properties rather than
vulnerabilities. Reports showing that one is broader than documented are in
scope.

- The device control plane is designed for a trusted LAN. Direct exposure to
  the public Internet is unsupported. That does not make access-control or
  data-disclosure reports irrelevant; report them when they could affect a
  trusted-LAN deployment or a release artifact.
- From the 0.14 line, development-channel builds also listen for ADB on TCP
  port 5555 for maintainer testing. That listener gives unauthenticated root
  access to anyone on the same network. Stable builds expose ADB only over
  USB. Never run a development build on an untrusted network.
- Stable builds ship with SSH disabled. Builds that enable SSH authenticate it
  against the web control centre's accounts; public-key and direct root login
  are not offered.
- Physical access to the device, including USB and BROM access, is equivalent
  to full control. The project does not claim to defend against an attacker
  with the device in hand.

## Private reporting

Use GitHub's private vulnerability reporting form:

<https://github.com/aslater3/LibreEcho/security/advisories/new>

Do not open a public issue, pull request or discussion with exploit details. If
GitHub does not offer the private form, do not publish the vulnerability while
waiting for the maintainer to enable or announce an alternative private route.
The project does not treat a public issue as a substitute for confidential
coordination.

Include, where safe:

- affected public release, tag or commit;
- affected component and deployment context;
- smallest reliable reproduction;
- impact and realistic attack prerequisites;
- redacted logs, traces or proof of concept;
- whether the issue survives reboot, rollback or recovery;
- a suggested mitigation, if known.

Never include passwords, API tokens, private keys, Wi-Fi credentials, serials,
MAC addresses, SSIDs, private IPs, owner-local firmware or unredacted device
identities. Use placeholders and describe how maintainers can reproduce the
condition safely.

## Coordination and response

This is a volunteer project, so these are best-effort targets rather than
guarantees:

| Stage | Target |
| --- | --- |
| Acknowledge a private report | 7 days |
| Initial assessment and severity | 14 days |
| Fix or documented mitigation for a confirmed issue | 90 days from the report |

Maintainers validate the impact, develop the fix in a private advisory fork or
on the active release branch, and agree on a public disclosure date with the
reporter. Please do not publish exploit details, credentials or a vulnerable
release's private artifacts before coordination is complete. If the 90-day
target cannot be met, maintainers will tell the reporter why and agree on a
new date.

Third-party vulnerabilities should also be reported to the relevant upstream
project when LibreEcho is not the owner. Tell LibreEcho privately if the issue
also affects a LibreEcho release or packaging decision.

## Advisories

Confirmed vulnerabilities in a supported release are published as GitHub
Security Advisories on this repository, with a CVE requested through GitHub
when the issue meets CVE criteria. Each advisory names the affected and fixed
releases and credits the reporter unless they ask otherwise.

- Advisory list: <https://github.com/aslater3/LibreEcho/security/advisories>
- Advisory archive, including releases with no advisories:
  [SECURITY-ADVISORIES.md](SECURITY-ADVISORIES.md)
- Website summary: <https://libreecho.org/#advisories>

The release notes for a fixed release reference the advisory once it is public.

## Release withdrawal and rollback

A confirmed release-blocking vulnerability may require pausing downloads,
marking a release superseded, publishing mitigation guidance, or directing users
to the confirmed A/B rollback slot. The public release record, the advisory and
<https://libreecho.org/> are the locations for sanitized withdrawal and rollback
instructions; private report details remain private.

## Ordinary bugs and questions

Use the public issue tracker for reproducible non-sensitive bugs, with all
secrets and identifying data removed. Use Discussions for design questions when
it is enabled. The website's security and support page explains the routing and
redaction checklist: <https://libreecho.org/#security>.
