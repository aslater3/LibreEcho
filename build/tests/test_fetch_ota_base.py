import importlib.util
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'ci' / 'fetch-ota-base.py'

class BaselineTests(unittest.TestCase):
    def test_workflow_automatically_resolves_baseline(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/build-release.yml').read_text()
        inputs = workflow.split('permissions:', 1)[0]
        self.assertNotIn('      ota_base_catalog_url:', inputs)
        self.assertNotIn('      ota_base_catalog_sha256:', inputs)
        self.assertIn('python3 build/ci/fetch-ota-base.py --release', workflow)
        self.assertIn('LIBREECHO_OTA_BASE_CATALOG: ${{ steps.ota_base.outputs.catalog }}', workflow)
        self.assertIn('LIBREECHO_OTA_BASE_CATALOG_SHA256: ${{ steps.ota_base.outputs.sha256 }}', workflow)
        self.assertIn('build.tests.test_fetch_ota_base', workflow)
        spec = importlib.util.spec_from_file_location('inputs', root / 'build/ci/ota_v2_inputs.py')
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for release in ('0.13.11', '0.14.1'):
            result = mod.validate_inputs('v2', release, '', '', 'workflow_dispatch', 'refs/heads/release/' + release)
            self.assertEqual(result['ota_release'], release)
        with self.assertRaises(mod.InputError):
            mod.validate_inputs('v2', '0.14.1', '', '', 'workflow_dispatch', 'refs/heads/main')

    def test_release_assets_generate_local_catalog_and_reject_tampering(self):
        spec = importlib.util.spec_from_file_location('fetch_base', SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        releases = [{'tag_name': t, 'draft': False, 'prerelease': False} for t in
                    ['radar-puffin-v0.13.9', 'radar-puffin-v0.13.10', 'radar-puffin-v0.14.0']]
        self.assertEqual(mod.previous_release(releases, 'radar-puffin-v0.13.11'), 'radar-puffin-v0.13.10')
        self.assertEqual(mod.previous_release(releases, 'radar-puffin-v0.14.1'), 'radar-puffin-v0.14.0')
        releases[1]['prerelease'] = True
        self.assertEqual(mod.previous_release(releases, 'radar-puffin-v0.13.11'), 'radar-puffin-v0.13.9')
        with self.assertRaises(ValueError):
            mod.previous_release([], 'radar-puffin-v0.13.11')
        tag = 'radar-puffin-v0.13.10'
        prefix = 'libreecho-' + tag
        blobs = {}
        records = []
        for feature in mod.FEATURES:
            payload = feature.encode()
            manifest = json.dumps({'schema_version': 1, 'feature_id': feature,
                'format': 'squashfs-lz4', 'files': {}, 'payload': {
                    'filename': feature + '.squashfs', 'size': len(payload),
                    'sha256': hashlib.sha256(payload).hexdigest()}}).encode()
            for suffix, data in [('.squashfs', payload), ('.manifest.json', manifest)]:
                name = prefix + '-' + feature + suffix
                blobs[name] = data
                records.append({'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        blobs[prefix + '-build.json'] = json.dumps({'release': tag, 'channel': 'stable', 'board': 'radar_puffin', 'artifacts': records}).encode()
        blobs[prefix + '-SHA256SUMS'] = ''.join(hashlib.sha256(v).hexdigest() + '  ' + k + '\n' for k, v in blobs.items()).encode()
        assets = [{'name': k, 'size': len(v), 'digest': 'sha256:' + hashlib.sha256(v).hexdigest(), 'browser_download_url': mod.DOWNLOAD + tag + '/' + k} for k,v in blobs.items()]
        release = {'id': 1, 'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': assets}
        def fetch(url, dest, limit):
            data = json.dumps(release).encode() if '/releases/tags/' in url else blobs[url.rsplit('/',1)[1]]
            self.assertLessEqual(len(data), limit)
            dest.write_bytes(data)
        with tempfile.TemporaryDirectory() as td, patch.object(mod, 'download', side_effect=fetch):
            root = Path(td)
            catalog = mod.resolve(tag, root / 'good')
            data = json.loads(catalog.read_text())
            self.assertEqual(set(data['features']), set(mod.FEATURES))
            for feature, record in data['features'].items():
                self.assertEqual(Path(record['payload']['path']).name, feature + '.squashfs')
                self.assertTrue(Path(record['manifest']['path']).is_file())
            blobs[prefix + '-tts.squashfs'] = b'bad'
            with self.assertRaises(ValueError):
                mod.resolve(tag, root / 'bad')
            release['draft'] = True
            with self.assertRaises(ValueError):
                mod.resolve(tag, root / 'draft')
