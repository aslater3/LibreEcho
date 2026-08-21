# Hosted public build boundary

The public workflow is GitHub-hosted only. It does not use `LibreEcho-Build`, a
self-hosted runner, Vaultwarden, sudo, private dependency roots, local caches,
owner-local firmware, or private models.

`build/inputs/public-inputs.json` is the closed dependency inventory. Entries
must have public HTTPS URLs, exact SHA-256 digests, licenses, and cleared
redistribution status before the hosted dependency job may fetch them. Current
unresolved toolchain, AOSP, host-tool, model, and owner-local firmware entries
are deliberately blocked; this branch must not fall back to local copies.

The hosted build entrypoint is not enabled until `build/ci/build-public-release.sh`
implements the mature builder boundary against that inventory. The workflow
fails closed when it is absent. Phase 1 still publishes all five feature
payloads but leaves OTA v1 boot-only; feature-aware OTA is Phase 2.
