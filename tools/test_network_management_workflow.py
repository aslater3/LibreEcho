#!/usr/bin/env python3
"""Contracts for the 0.14 development network-management CI lane."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NetworkManagementWorkflowTests(unittest.TestCase):
    def test_workflow_exposes_exact_sources_and_open_dev_mode(self) -> None:
        source = (ROOT / ".github/workflows/build-release.yml").read_text()
        for token in ("platform_sha:", "linux_sha:", "ui_sha:", "network_adb:"):
            self.assertIn(token, source)
        self.assertIn("options: [disabled, open-dev]", source)
        self.assertIn("OPEN network ADB", source)
        self.assertIn("open-dev requires a manual dev dispatch", source)
        self.assertIn("explicit component SHAs must be full lowercase commits", source)
        self.assertIn('awk -v expected="$commit"', source)
        self.assertNotIn('grep -q "^${commit}[[:space:]]"', source)
        self.assertIn("network_adb: ${{ steps.resolve.outputs.network_adb }}", source)
        self.assertIn("LIBREECHO_NETWORK_ADB", source)

    def test_build_pipeline_passes_mode_to_builder_and_verifier(self) -> None:
        source = (ROOT / "build/build.sh").read_text()
        self.assertIn("LIBREECHO_NETWORK_ADB", source)
        self.assertIn('case "$NETWORK_ADB" in disabled|open-dev)', source)
        self.assertIn('--network-adb "$NETWORK_ADB"', source)
        self.assertIn('--expected-network-adb "$NETWORK_ADB"', source)
        self.assertIn("open network ADB is restricted to the dev channel", source)

    def test_public_entrypoint_preserves_network_adb_input(self) -> None:
        source = (ROOT / "build/ci/build-public-release.sh").read_text()
        self.assertIn("LIBREECHO_NETWORK_ADB", source)
        self.assertIn("disabled|open-dev", source)

    def test_ssh_uses_deferred_account_contract_without_embedded_credentials(self) -> None:
        build = (ROOT / "build/build.sh").read_text()
        workflow = (ROOT / ".github/workflows/build-release.yml").read_text()
        self.assertNotIn("LIBREECHO_SSH_ROOT_PASSWORD_HASH", build)
        self.assertIn('--scp "$DROPBEAR_OUTPUT/scp"', build)
        self.assertIn("--expected-scp-sha256", build)
        self.assertNotIn("LIBREECHO_SSH_ROOT_PASSWORD_HASH", workflow)
        self.assertNotIn("SSH_ROOT_PASSWORD_HASH", workflow)
        self.assertIn("libcrypt-dev:armhf libcrypt1:armhf", workflow)
        self.assertIn("DROPBEAR_SOURCE_ARCHIVE:", workflow)
        self.assertIn("DROPBEAR_LIBCRYPT_DEV_PACKAGE:", workflow)
        self.assertIn("DROPBEAR_LIBCRYPT_RUNTIME_PACKAGE:", workflow)
        self.assertIn("usr/share/libreecho-build-debs/dropbear", workflow)
        self.assertIn('ref="${ref//\\//-}"', workflow)


if __name__ == "__main__":
    unittest.main()
