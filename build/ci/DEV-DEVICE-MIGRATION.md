# Development-device OTA v2 migration

The normal OTA v2 build derives its baseline from the preceding stable release.
A boot-only bridge does not change installed feature payloads. Development
payloads can therefore differ from the public baseline and correctly fail a
preserve check.

For explicitly authorized development migration only, manual Hosted build
supports `device_baseline_json` on `release/0.14.0`, with `update_channel=dev`
and `ota_format=v2`. Other combinations fail before build. Leaving the input
empty retains the release-derived baseline and v1 defaults unchanged.

Input is bounded to 8192 UTF-8 bytes and has exactly this schema:

- `schema`: `libreecho-dev-device-baseline-v1`
- `features`: exactly `airplay2`, `tts`, `wakeword`, `stt`, `assistant`
- Each feature: exactly `payload_sha256`, `manifest_sha256`, `daemon_sha256`,
  each a lowercase SHA-256 hex string.

Capture payload and manifest bytes read-only, verify hashes on the device before
and after transfer, and verify the daemon bytes inside each captured SquashFS
against the feature manifest. This is maintainer-supplied device identity, **not
proof of a published or trusted release**. Never put serials, paths, settings,
credentials or arbitrary file content in this input. Duplicate and extra keys
are rejected. Preserve a private local evidence record of capture verification.

The planner uses verified CI candidate bytes, replaces changed supported
features, and preserves the actual Wakeword generation because Wakeword is
excluded from this system-update policy. Runtime capsules are forbidden in
this migration mode. The protected signer checks the hash-bound baseline again
against every feature's base identity and all preserved daemon identities;
stable signing rejects this schema. The device's existing signed manifest,
preserve hash, staging, boot readback and rollback checks are not bypassed.

A successful host plan is not permission to install. Before deployment, verify
the signed package, exact boot image and all replacement assets, asset delivery
availability, target baseline stability, and storage headroom. Manual control-tar
upload alone does not transfer external feature assets. Keep the confirmed
rollback slot. Record reboot, exact image identity, service acceptance and
changed-feature acceptance separately. No wipe or ad-hoc updater replacement
is required or authorized by this input.
