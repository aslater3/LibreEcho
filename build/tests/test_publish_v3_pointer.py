"""Publisher channel mutation must be v3-only and explicitly authorized."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from build.ci import publish_dev_pointer as publisher

ROOT = Path(__file__).resolve().parents[2]


class PointerV3Tests(unittest.TestCase):
    def test_publisher_has_no_legacy_pointer_write_path(self):
        for path in (ROOT / 'build/ci/publish_dev_pointer.py', ROOT / '.github/workflows/publish-release.yml'):
            self.assertNotIn('release-pointer.txt', path.read_text())
        self.assertEqual(publisher.POINTER_ASSET, 'release-pointer-v3.txt')

    def test_workflow_requires_manual_hardware_validation_authorization(self):
        import yaml
        text = (ROOT / '.github/workflows/publish-release.yml').read_text()
        data = yaml.safe_load(text)
        dispatch = data.get('on', data.get(True))['workflow_dispatch']
        self.assertFalse(dispatch['inputs']['advance_pointer']['default'])
        self.assertEqual(dispatch['inputs']['advance_pointer']['type'], 'boolean')
        jobs = data['jobs']
        for name, job in jobs.items():
            if 'publish_dev_pointer.py' in json.dumps(job):
                self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
                self.assertIn('inputs.advance_pointer == true', job['if'])
        self.assertNotIn('publish_dev_pointer.py', json.dumps(jobs['publish-hosted-dev']))

    def test_cli_defaults_to_no_side_effect(self):
        with patch.object(sys, 'argv', ['publish_dev_pointer.py', '--repository', 'aslater3/LibreEcho', '--tag', 'unused', '--head', 'a'*40, '--assets', '/missing']), patch.object(publisher, 'gh') as gh, patch.object(publisher.subprocess, 'run') as run:
            publisher.main()
        gh.assert_not_called()
        run.assert_not_called()

    def test_cli_rejects_old_format_before_channel_mutation(self):
        from build.tests.test_publish_dev_pointer import PointerTests
        fixture = PointerTests()
        fixture.setUp()
        try:
            with patch.object(sys, 'argv', ['publish_dev_pointer.py', '--advance-pointer', 'true', '--repository', 'aslater3/LibreEcho', '--tag', fixture.tag, '--head', 'a'*40, '--assets', str(fixture.root)]), patch.object(publisher, 'gh', return_value=json.dumps(fixture.release)) as gh, patch.object(publisher.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'v3'):
                    publisher.main()
            run.assert_not_called()
            self.assertEqual(gh.call_count, 1)
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
