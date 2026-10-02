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
        # Plain-text parse: the CI contract job has no YAML library.
        text = (ROOT / '.github/workflows/publish-release.yml').read_text()
        dispatch = text.split('  workflow_dispatch:\n', 1)[1].split('\npermissions:', 1)[0]
        pointer = dispatch.split('      advance_pointer:\n', 1)[1].split('\n      release_tag:', 1)[0]
        self.assertIn('        type: boolean', pointer)
        self.assertIn('        default: false', pointer)
        jobs = {}
        body = text.split('\njobs:\n', 1)[1]
        for chunk in ('\n' + body).split('\n  ')[1:]:
            if chunk and not chunk.startswith(' ') and chunk.split('\n', 1)[0].endswith(':'):
                name = chunk.split(':', 1)[0]
                jobs[name] = chunk
            elif jobs:
                jobs[name] += '\n  ' + chunk
        self.assertIn('advance-v3-pointer', jobs)
        self.assertIn('publish-hosted-dev', jobs)
        for name, job in jobs.items():
            if 'publish_dev_pointer.py' in job:
                cond = [l for l in job.splitlines() if l.strip().startswith('if:')][0]
                self.assertIn("github.event_name == 'workflow_dispatch'", cond, name)
                self.assertIn('inputs.advance_pointer == true', cond, name)
        self.assertNotIn('publish_dev_pointer.py', jobs['publish-hosted-dev'])

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
