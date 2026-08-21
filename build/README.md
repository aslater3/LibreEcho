# Hosted public build boundary

The public workflow is GitHub-hosted only. It does not use `LibreEcho-Build`, a
self-hosted runner, Vaultwarden, sudo, private dependency roots, local caches,
private models, or owner-local firmware bytes.

`build/inputs/public-inputs.json` is the closed dependency inventory. Entries
must have public HTTPS URLs, exact SHA-256 digests, licenses, and cleared
redistribution status before the hosted dependency job may fetch them. Current
unresolved toolchain entries are deliberately blocked; this branch must not fall
back to local copies. The device-local connectivity firmware is represented only
by the public importer contract: first boot verifies and copies it from stock
`system_a`; the bytes never enter CI, Git, or release assets.

`build/ci/build-public-release.sh` is present as a fail-closed boundary. It will
not invoke a copied private builder or use local fallback inputs while the
inventory remains blocked. Phase 1 still publishes all five feature payloads
but leaves OTA v1 boot-only; feature-aware OTA is Phase 2.
