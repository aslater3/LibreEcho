# LibreEcho radar-puffin v0.13.18

A focused patch release for installer reliability and owner-local firmware enrolment on the Echo Gen 2 / MT8163 Radar Puffin.

## Large feature uploads (Product #176)

- Give feature payload uploads a bounded minimum of 900 seconds, while preserving a larger operator-supplied ADB timeout.
- Reuse the existing elapsed-time heartbeat runner so an upload is not silently stopped by the ordinary 180-second command budget. Timeout diagnostics retain the partial stdout/stderr collected when the process is killed.
- Keep ordinary ADB command deadlines, payload hashes, staging paths, partition safeguards, readback verification and resume behaviour unchanged. This is not a staging redesign or an automatic destructive retry.

The report's immediate transfer-timeout hypothesis still requires the reporter's evidence bundle to distinguish a slow transfer from a stalled transport or resource problem.

## Owner-local firmware enrolment (Platform #172)

- Preserve the four-file owner-approved hash/size contract under `/data/libreecho/config/vendor-assets.tsv`; no vendor firmware bytes are persisted in userdata or distributed.
- Harden contract-copy/readback verification and durability around the atomic enrolment commit, retaining the existing strict validation and `owner-local-enrolled` state on subsequent boots.
- Exercise a cold second boot with fresh runtime directories and no force marker, plus rejection of a damaged enrolment copy.

The inspected 0.13.17 hosted boot image already contains the enrolment/reload implementation. This patch hardens and regression-tests that implementation; it does not establish why the reporter observed an older one-shot-only path. Keep Platform #172 open until the exact running importer and reboot evidence are reconciled.

## Coordinated source set

- Product version: `0.13.18`
- Product release ID: `radar-puffin-v0.13.18`
- Release impact: `patch`
- Baseline: exact coordinated source commits recorded by the hosted 0.13.17 build, not the 0.14 feature line.
- Product, Platform, Linux and UI use `release/0.13.18`; UI `VERSION` must equal `0.13.18`. Linux has no runtime changes for these two issues.

## Release status and validation

These notes describe source preparation, not publication or physical hardware acceptance. Before publishing, merge the linked component fixes, run the canonical hosted build with all four `release/0.13.18` refs, verify the signed complete asset inventory and independent image/provenance checks, and record live installation and two-boot firmware-enrolment acceptance. Do not reuse or replace the published 0.13.17 assets.
