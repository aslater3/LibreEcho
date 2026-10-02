import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from build.ci.publish_dev_pointer import pointer_bytes, validate_request, main


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tag = 'radar-puffin-build-' + 'a'*7 + '-' + 'b'*16 + '-' + 'c'*16
        (self.root / ('libreecho-' + self.tag + '.ota.tar')).write_bytes(b'test-only-control')
        (self.root / ('libreecho-' + self.tag + '-build.json')).write_text(json.dumps({'channel':'dev','signed':True}))
        self.release = dict(tag_name=self.tag, prerelease=True, draft=False, assets=[dict(name=p.name,size=p.stat().st_size,digest='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.iterdir()])

    def test_verified_inventory_emits_bounded_pointer(self):
        data = pointer_bytes(self.root,self.tag,self.release)
        self.assertEqual(data.decode().splitlines(),[self.tag,hashlib.sha256(b'test-only-control').hexdigest()])
        self.assertLessEqual(len(data),256)

    def test_rejects_draft_stable_and_wrong_tag(self):
        for change in ({'draft':True},{'prerelease':False},{'tag_name':'radar-puffin-v0.13.11'}):
            with self.assertRaises(ValueError): pointer_bytes(self.root,self.tag,self.release | change)

    def test_rejects_missing_duplicate_or_changed_remote_asset(self):
        assets=self.release['assets']
        for changed in (assets[:-1],assets+assets[:1],[assets[0]|{'digest':'sha256:'+'0'*64}]+assets[1:]):
            with self.assertRaises(ValueError): pointer_bytes(self.root,self.tag,self.release | {'assets':changed})

    def test_rejects_untrusted_tag_shape(self):
        for tag in ('latest','radar-puffin-v0.13.11','../main',self.tag+'\n'):
            with self.assertRaises(ValueError): pointer_bytes(self.root,tag,self.release)

    def test_request_guard_accepts_only_publishable_dev(self):
        request = dict(schema='libreecho-release-request-v1', purpose='dev', publish=True, channel='dev')
        path = self.root/'release-request.json'
        path.write_text(json.dumps(request))
        validate_request(path)
        for change in ({'purpose': 'sandbox'}, {'purpose': 'prd'}, {'purpose': None},
                       {'publish': False}, {'publish': 1}, {'channel': 'stable'}):
            path.write_text(json.dumps(request | change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_request(path)
        path.write_text(' ' * 8193)
        with self.assertRaises(ValueError):
            validate_request(path)
        path.unlink()
        with self.assertRaises(ValueError):
            validate_request(path)
        path.symlink_to(self.root/('libreecho-'+self.tag+'-build.json'))
        with self.assertRaises(ValueError):
            validate_request(path)

    def test_sandbox_cli_fails_before_any_github_call(self):
        from unittest.mock import patch
        request = self.root/'release-request.json'
        request.write_text(json.dumps(dict(schema='libreecho-release-request-v1',
                                          purpose='sandbox', publish=False, channel='dev')))
        argv = ['publish_dev_pointer.py', '--repository', 'aslater3/LibreEcho',
                '--tag', self.tag, '--head', 'a'*40, '--assets', str(self.root),
                '--request', str(request)]
        with patch('sys.argv', argv), patch('build.ci.publish_dev_pointer.gh') as gh, \
             patch('build.ci.publish_dev_pointer.subprocess.run') as run:
            with self.assertRaises(ValueError):
                main()
            gh.assert_not_called()
            run.assert_not_called()

    def test_sandbox_request_fails_before_github_even_when_advancing(self):
        from unittest.mock import patch
        request = self.root/'release-request.json'
        request.write_text(json.dumps(dict(schema='libreecho-release-request-v1',
                                          purpose='sandbox', publish=False, channel='dev')))
        argv = ['pointer', '--advance-pointer', 'true', '--repository', 'aslater3/LibreEcho',
                '--tag', self.tag, '--head', 'a'*40, '--assets', str(self.root),
                '--request', str(request)]
        with patch('sys.argv', argv), patch('build.ci.publish_dev_pointer.gh') as gh, \
             patch('build.ci.publish_dev_pointer.subprocess.run') as run:
            with self.assertRaises(ValueError):
                main()
            gh.assert_not_called()
            run.assert_not_called()

    def test_dev_request_cli_accepts_both_targets_and_nightly_policy(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        for target in ('radar_puffin', 'biscuit'):
            request = self.root/'release-request.json'
            request.write_text(json.dumps(dict(schema='libreecho-release-request-v1',
                                              purpose='dev', publish=True, channel='dev')))
            channel = ('radar-puffin' if target == 'radar_puffin' else target) + '-dev-channel'
            data = b'test-only-pointer\n'
            current = dict(tag_name=channel, draft=False, prerelease=True, assets=[])
            verified = current | {'assets': [dict(name='release-pointer-v3.txt', size=len(data),
                         digest='sha256:'+hashlib.sha256(data).hexdigest())]}
            from build.ci.target_registry import asset_prefix
            (self.root / (asset_prefix(self.tag, target) + '-build.json')).write_text(
                json.dumps({'channel': 'dev', 'signed': True, 'ota_format': 'v3', 'board': target}))
            argv = ['pointer', '--advance-pointer', 'true', '--repository', 'aslater3/LibreEcho', '--tag', self.tag,
                    '--head', 'a'*40, '--assets', str(self.root), '--target', target,
                    '--request', str(request)]
            with patch('sys.argv', argv), \
                 patch('build.ci.publish_dev_pointer.gh', side_effect=[json.dumps(self.release),
                       json.dumps(current), '', json.dumps(verified)]) as gh, \
                 patch('build.ci.publish_dev_pointer.pointer_bytes', return_value=data) as pointer, \
                 patch.dict('sys.modules', {'build.ci.release_completeness':
                            SimpleNamespace(check_assets=lambda *a, **k: None)}), \
                 patch('build.ci.publish_dev_pointer.subprocess.run', return_value=SimpleNamespace(returncode=0)):
                main()
                pointer.assert_called_once_with(self.root, self.tag, self.release, target)
            request.unlink()
        nightly = self.tag.replace('-build-', '-nightly-')
        for path in list(self.root.iterdir()):
            path.rename(path.with_name(path.name.replace(self.tag, nightly)))
        release = dict(tag_name=nightly, prerelease=True, draft=False,
                       assets=[dict(name=p.name,size=p.stat().st_size,
                                    digest='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest())
                               for p in self.root.iterdir()])
        self.assertTrue(pointer_bytes(self.root, nightly, release).startswith(nightly.encode()))

    def test_rejects_symlink(self):
        (self.root/'extra').symlink_to('missing')
        with self.assertRaises(ValueError): pointer_bytes(self.root,self.tag,self.release)
