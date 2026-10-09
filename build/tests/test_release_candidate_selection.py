"""Execute the shipped resolver branches without any GitHub/network operations."""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / '.github/workflows/build-release.yml').read_text()


def shell_block(start, end):
    return textwrap.dedent(WORKFLOW[WORKFLOW.index(start):WORKFLOW.index(end)])


class CandidateSelectionTests(unittest.TestCase):
    def run_shell(self, block, **overrides):
        env = dict(os.environ, GITHUB_EVENT_NAME='pull_request',
                   GITHUB_BASE_REF='release/0.14.0',
                   GITHUB_HEAD_REF='fix/forward-port-01314',
                   GITHUB_REF='refs/pull/156/merge', GITHUB_REF_NAME='156/merge',
                   GITHUB_REPOSITORY='aslater3/LibreEcho',
                   PR_HEAD_REPOSITORY='aslater3/LibreEcho',
                   OTA_FORMAT_INPUT='v2', OTA_RELEASE_INPUT='')
        env.update(overrides)
        return subprocess.run(['bash', '-eu', '-o', 'pipefail', '-c', block],
                              env=env, text=True, capture_output=True, timeout=5)

    def selection(self, **overrides):
        block = shell_block('          component_ref=main\n', '          for ref_spec in')
        r = self.run_shell(block + '\nprintf "%s\\n" "$platform_ref" "$linux_ref" "$ui_ref"', **overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_same_owner_coordinated_014_candidates(self):
        self.assertEqual(self.selection(), ['fix/forward-port-01314'] * 3)

    def test_fork_and_normal_pr_use_release_components(self):
        self.assertEqual(self.selection(PR_HEAD_REPOSITORY='fork/LibreEcho'), ['release/0.14.0'] * 3)
        self.assertEqual(self.selection(GITHUB_HEAD_REF='fix/ordinary'), ['release/0.14.0'] * 3)
        self.assertEqual(self.selection(GITHUB_BASE_REF='release/0.13.15'), ['release/0.13.15'] * 3)

    def test_existing_coordinated_feature_lane_preserved(self):
        self.assertEqual(self.selection(GITHUB_HEAD_REF='feature/example-product'),
                         ['feature/example-platform', 'release/0.14.0', 'feature/example-ui'])
        self.assertEqual(self.selection(GITHUB_HEAD_REF='feature/example-product',
                                        PR_HEAD_REPOSITORY='fork/LibreEcho'), ['release/0.14.0'] * 3)

    def test_release_dispatch_and_push_never_use_fix_refs(self):
        for event in ('push', 'workflow_dispatch'):
            self.assertEqual(self.selection(GITHUB_EVENT_NAME=event,
                GITHUB_REF='refs/heads/release/0.14.0', GITHUB_REF_NAME='release/0.14.0'),
                ['release/0.14.0'] * 3)
        self.assertEqual(self.selection(GITHUB_EVENT_NAME='push', GITHUB_REF='refs/heads/main',
                                        GITHUB_REF_NAME='main'), ['main'] * 3)

    def derivation(self, **overrides):
        import tempfile
        block = shell_block('          # Resolve the target version', '          case "$SSH_ENABLED_INPUT"')
        with tempfile.TemporaryDirectory() as directory:
            return self.run_shell(block + '\nprintf "%s\n" "$OTA_RELEASE_INPUT"', GITHUB_OUTPUT=str(Path(directory) / 'outputs'), OTA_FORMAT_INPUT='v3', **overrides)

    def test_v3_derives_branch_version_and_preserves_explicit_input(self):
        r = self.derivation(GITHUB_BASE_REF='release/0.15.0')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.15.0'))
        r = self.derivation(OTA_RELEASE_INPUT='0.14.0')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.14.0'))

    def test_v3_main_default_and_invalid_version_refusal(self):
        r = self.derivation(GITHUB_BASE_REF='', GITHUB_REF_NAME='main')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.14.0'))
        r = self.derivation(OTA_RELEASE_INPUT='invalid')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('invalid target version', r.stderr)

    def test_dispatch_default_and_resolved_contract_identity(self):
        inputs = WORKFLOW.split('      ota_format:', 1)[1].split('      ota_release:', 1)[0]
        self.assertIn('default: v3', inputs)
        self.assertIn('options: [v3]', inputs)
        self.assertIn("OTA_FORMAT_INPUT: ${{ inputs.ota_format || 'v3' }}", WORKFLOW)
        contracts = WORKFLOW.split('  contract-checks:', 1)[1].split('  prepare-public-inputs:', 1)[0]
        self.assertIn('needs: resolve-and-preflight', contracts)
        self.assertIn('ref: ${{ needs.resolve-and-preflight.outputs.platform_sha }}', contracts)


if __name__ == '__main__':
    unittest.main()
