# LibreEcho security advisory archive

This is the permanent public record of security advisories for LibreEcho
releases. It complements the machine-readable GitHub Security Advisory list at
<https://github.com/aslater3/LibreEcho/security/advisories>. See
[SECURITY.md](SECURITY.md) for supported versions and private reporting.

Each published advisory is added to the table below with its GHSA ID, CVE (when
assigned), severity, affected and fixed releases, and publication date. Entries
are never removed. A withdrawn advisory stays listed and is marked withdrawn.

## Published advisories

| Advisory | CVE | Severity | Component | Affected | Fixed in | Published |
| --- | --- | --- | --- | --- | --- | --- |
| None published to date | — | — | — | — | — | — |

## Release status

Status of each release line covered by the security policy.

| Release | Stable publication | Security fixes | Advisories |
| --- | --- | --- | --- |
| 0.14.0 (`radar-puffin-v0.14.0`) | Not yet published | Supported from publication | None |
| 0.13.19 (`radar-puffin-v0.13.19`) | 20 September 2026 | Supported until 0.14.0 is stable | None |
| 0.13.9 – 0.13.18 | 1 – 19 September 2026 | Not supported; upgrade | None |
| 0.13.8 and earlier, including 0.1.0 | Development releases | Not supported; upgrade | None |

## Security-relevant hardening by release

These changes reduced attack surface or strengthened verification. They were
not responses to reported vulnerabilities and have no advisory; they are listed
so reviewers can see when each protection arrived.

### 0.14.0 (unreleased)

- Optional HTTPS for the web control centre using a device-generated
  certificate. Persistent login sessions are only kept across restarts while
  HTTPS is enabled (LibreEcho-UI #137).
- SSH, when a build enables it, authenticates against the web control centre's
  accounts instead of a build-time root password, with no public-key or direct
  root login (LibreEcho-Platform #85, LibreEcho #188).
- Opt-in recovery access point with owner-prepared credentials and an
  authenticated reconnect flow (LibreEcho-UI #288, LibreEcho-Platform #216).
- Authenticated, fail-closed recovery from a stale OTA install lock
  (LibreEcho-Platform #206).
- Exact file-mode enforcement for the shared mDNS runtime
  (LibreEcho-Platform #149).

### 0.13.x

- Signed OTA v2 transactions with verified feature-payload provenance and
  signed authorizing identity (0.13.11 – 0.13.14).
- Diagnostic export is bounded and redacted (LibreEcho-UI #123).

## How to add an entry

1. Publish the GitHub Security Advisory with affected and patched versions
   written as release IDs (for example `radar-puffin-v0.13.19`).
2. Add a row to **Published advisories** in the same pull request that adds the
   fixed release note, linking the GHSA ID to its advisory page.
3. Update **Release status** for the affected release lines.
4. Reference the advisory in the fixed release's notes and on
   <https://libreecho.org/#advisories>.
