"""Immutable whole-state Product contract, independent of Platform parsers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CI = Path(__file__).resolve().parents[1] / 'ci'
sys.path.insert(0, str(CI))
from ota_v2_product import ContractError, DAEMONS, FEATURES
import release_completeness as gate

TAG = 'radar-puffin-build-' + 'a'*7 + '-' + 'b'*16 + '-' + 'c'*16
FIELDS = ('asset', 'size', 'sha256', 'manifest_asset', 'manifest_size', 'manifest_sha256', 'daemon_path', 'daemon_sha256')
TOP = ('format', 'manifest_version', 'board', 'soc', 'architecture', 'image_profile', 'transaction_type', 'transaction_id', 'release', 'version', 'update_channel', 'service_profile', 'commit_policy', 'minimum_updater_schema', 'boot_filename', 'boot_size', 'boot_sha256', 'feature_ids')


def fixture(root, target='radar_puffin'):
    source = root / 'source'
    source.mkdir()
    catalog = {'board': target, 'features': {}}
    for fid in FEATURES:
        payload = source / (fid + '.squashfs')
        payload.write_bytes((fid + ' release-owned payload').encode())
        psha = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest = source / (fid + '.manifest.json')
        manifest.write_text(json.dumps({'schema_version': 1, 'feature_id': fid, 'format': 'squashfs-lz4', 'payload': {'filename': payload.name, 'sha256': psha, 'size': payload.stat().st_size}, 'files': {DAEMONS[fid]: {'sha256': hashlib.sha256(fid.encode()).hexdigest()}}}))
        catalog['features'][fid] = {kind: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size} for kind, path in [('payload', payload), ('manifest', manifest)]}
    path = root / 'catalog.json'
    path.write_text(json.dumps(catalog))
    boot = root / 'boot.img'
    with boot.open('wb') as stream:
        stream.truncate(16777216)
    return path, boot


class TargetManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, catalog, boot, out, target='radar_puffin'):
        return [sys.executable, '-B', str(CI / 'plan-target-manifest.py'), '--target', target, '--candidate-catalog', str(catalog), '--boot-image', str(boot), '--release', TAG, '--version', '0.14.0', '--update-channel', 'dev', '--config-schema', '1', '--output', str(out / 'target.manifest'), '--asset-output-dir', str(out / 'ota-assets')]

    def run_planner(self, target='radar_puffin'):
        catalog, boot = fixture(self.root, target)
        out = self.root / 'run'
        result = subprocess.run(self.command(catalog, boot, out, target), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return out, catalog, boot

    def test_planner_exists(self):
        self.assertTrue((CI / 'plan-target-manifest.py').is_file(), 'whole-state planner missing')

    def test_rejects_all_baseline_arguments(self):
        catalog, boot = fixture(self.root)
        for flag in ('--base-catalog', '--device-baseline', '--runtime-dir'):
            result = subprocess.run(self.command(catalog, boot, self.root / 'out') + [flag, 'ignored'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('unrecognized arguments', result.stderr)

    def test_canonical_whole_target_determinism_and_cross_route_identity(self):
        for target in ('radar_puffin', 'biscuit'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                self.root = Path(directory)
                out, catalog, boot = self.run_planner(target)
                raw = (out / 'target.manifest').read_bytes()
                values = dict(line.split('=', 1) for line in raw.decode().splitlines())
                self.assertEqual(list(values), list(TOP) + [f'feature_{fid}_{field}' for fid in FEATURES for field in FIELDS] + ['config_schema'])
                self.assertEqual(values['format'], 'libreecho-ota-v3')
                self.assertEqual(values['release'], TAG)
                self.assertEqual(values['minimum_updater_schema'], '3')
                self.assertEqual(values['boot_size'], '16777216')
                self.assertEqual(values['boot_sha256'], hashlib.sha256(boot.read_bytes()).hexdigest())
                self.assertFalse(any(k.endswith('_action') or '_base_' in k or 'activation' in k or 'runtime' in k or k == 'feature_policy' for k in values))
                provenance = json.loads((out / gate.provenance_name(target)).read_text())
                refs = {(r['feature_id'], r['kind']): r for r in provenance['references']}
                plan = json.loads((out / 'feature-plan.json').read_text())
                staging = []
                for fid in FEATURES:
                    for kind, prefix in [('payload', ''), ('manifest', 'manifest_')]:
                        ref = refs[fid, kind]
                        self.assertEqual(ref['source_release'], TAG)
                        self.assertEqual((values[f'feature_{fid}_{prefix}asset'], values[f'feature_{fid}_{prefix}sha256'], int(values[f'feature_{fid}_{prefix}size'])), (ref['name'], ref['sha256'], ref['size']))
                        asset = out / 'ota-assets' / ref['name']
                        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), ref['sha256'])
                    staging.append('staging=' + ':'.join([fid, refs[fid, 'payload']['name'], refs[fid, 'payload']['sha256'], refs[fid, 'manifest']['name'], refs[fid, 'manifest']['sha256']]))
                bundle = out / 'bundle.manifest'
                bundle.write_text('schema=1\n' + '\n'.join(staging) + '\n')
                gate.validate_completeness(plan, out / 'ota-assets', provenance, bundle, target=target)
                second = self.root / 'again'
                result = subprocess.run(self.command(catalog, boot, second, target), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(raw, (second / 'target.manifest').read_bytes())

    def test_gate_rejects_foreign_release_and_forbidden_fields(self):
        out, _, _ = self.run_planner()
        plan = json.loads((out / 'feature-plan.json').read_text())
        provenance = json.loads((out / gate.PROVENANCE).read_text())
        provenance['references'][0]['source_release'] = 'radar-puffin-v0.13.10'
        with self.assertRaisesRegex(ContractError, 'source release'):
            gate.validate_completeness(plan, out / 'ota-assets', provenance)
        for key in ('action', 'base_payload_sha256', 'activation', 'runtime_asset', 'feature_policy'):
            bad = json.loads(json.dumps(plan))
            bad['features'][0][key] = 'forbidden'
            with self.assertRaises(ContractError):
                gate.references(bad)

    def test_rejects_corrupt_missing_and_misbound_input(self):
        catalog, boot = fixture(self.root)
        data = json.loads(catalog.read_text())
        Path(data['features']['wakeword']['payload']['path']).write_bytes(b'corrupt')
        result = subprocess.run(self.command(catalog, boot, self.root / 'run'), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity', result.stderr)

    def test_completeness_gate_rejects_legacy_action_plans(self):
        from build.tests.test_release_completeness import fixture as legacy_fixture
        plan, provenance, bundle = legacy_fixture(self.root)
        with self.assertRaisesRegex(ContractError, 'forbidden'):
            gate.validate_completeness(plan, self.root, provenance, bundle)

    def test_device_baseline_removed(self):
        self.assertFalse((CI / 'device_baseline.py').exists())
        self.assertFalse((CI.parent / 'tests/test_device_baseline.py').exists())

    def test_build_entrypoint_has_no_baseline_or_runtime_inputs(self):
        script = (CI.parent / 'build.sh').read_text()
        for token in ('OTA_BASE_CATALOG', 'OTA_BASE_BOOTSTRAP', 'DEVICE_BASELINE', 'OTA_RUNTIME_DIR', 'plan-feature-transaction.py'):
            self.assertNotIn(token, script)
        self.assertIn('plan-target-manifest.py', script)

    def test_build_workflow_only_selects_v3_and_no_prior_catalog(self):
        workflow = (CI.parents[1] / '.github/workflows/build-release.yml').read_text()
        self.assertIn('default: v3', workflow)
        self.assertIn('options: [v3]', workflow)
        for token in ('device_baseline', 'steps.ota_base', '--base-catalog', 'ota_v2_inputs.py'):
            self.assertNotIn(token, workflow)


if __name__ == '__main__':
    unittest.main()
