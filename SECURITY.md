# Security policy

LibreEcho is an independent, volunteer-maintained project for recoverable
Amazon Echo Gen 2 experimentation. Please do not use public GitHub issues for
security vulnerabilities.

## Supported scope

The currently supported public scope is the `radar-puffin v0.1.0` development
release and the Linux 6.1 development line for the documented Echo Gen 2 target.
The Developer Preview and Open Beta gates are separate release decisions; Open
Beta has not launched. A fix may be developed on a later review branch before it
is backported to a public release.

Security reports are especially important for:

- authentication, session, CSRF or access-control bypasses;
- OTA signature, rollback, update or release-identity verification;
- remote control-plane or network-service exposure;
- boot, recovery, privilege or arbitrary-write paths;
- credentials, tokens, owner-local firmware or private diagnostic disclosure;
- supply-chain, release workflow or third-party provenance issues.

Direct public-Internet exposure of the device control plane is unsupported. That
boundary does not make an access-control or data-disclosure report irrelevant;
report it privately when it could affect a trusted-LAN deployment or release
artifact.

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

## Coordination expectations

This is a volunteer project. Maintainers will acknowledge a private report when
practical, validate the impact, coordinate a fix or mitigation, and agree on a
public disclosure date with the reporter when a report is confirmed. Please do
not publish exploit details, credentials or a vulnerable release's private
artifacts before coordination is complete.

Third-party vulnerabilities should also be reported to the relevant upstream
project when LibreEcho is not the owner. Tell LibreEcho privately if the issue
also affects a LibreEcho release or packaging decision.

## Release withdrawal and rollback

A confirmed release-blocking vulnerability may require pausing downloads,
marking a release superseded, publishing mitigation guidance, or directing users
to the confirmed A/B rollback slot. The public release record and
<https://libreecho.org/> are the locations for sanitized withdrawal and rollback
instructions; private report details remain private.

## Ordinary bugs and questions

Use the public issue tracker for reproducible non-sensitive bugs, with all
secrets and identifying data removed. Use Discussions for design questions when
it is enabled. The website's security and support page explains the routing and
redaction checklist: <https://libreecho.org/#security>.
