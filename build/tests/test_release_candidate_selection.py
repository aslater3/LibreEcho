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
        block = shell_block('          # Release dispatches default to v2;',
                            '          python3 build/ci/ota_v2_inputs.py')
        return self.run_shell(block + '\nprintf "%s\\n" "$OTA_RELEASE_INPUT"', **overrides)

    def test_v2_derives_branch_version_and_preserves_explicit_input(self):
        r = self.derivation(GITHUB_REF='refs/heads/release/0.14.0',
                            GITHUB_REF_NAME='release/0.14.0')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.14.0'))
        r = self.derivation(OTA_RELEASE_INPUT='0.14.0')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.14.0'))

    def test_main_v2_without_candidate_is_rejected_and_v1_is_unchanged(self):
        r = self.derivation(GITHUB_REF='refs/heads/main', GITHUB_REF_NAME='main')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('select v1 for main builds', r.stderr)
        r = self.derivation(OTA_FORMAT_INPUT='v1')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ''))

    def test_dispatch_default_and_resolved_contract_identity(self):
        inputs = WORKFLOW.split('      ota_format:', 1)[1].split('      device_baseline_json:', 1)[0]
        self.assertIn('default: v2', inputs)
        self.assertIn("OTA_FORMAT_INPUT: ${{ inputs.ota_format || 'v1' }}", WORKFLOW)
        contracts = WORKFLOW.split('  contract-checks:', 1)[1].split('  public-inputs:', 1)[0]
        self.assertIn('needs: resolve-and-preflight', contracts)
        self.assertIn('ref: ${{ needs.resolve-and-preflight.outputs.platform_sha }}', contracts)


if __name__ == '__main__':
    unittest.main()
