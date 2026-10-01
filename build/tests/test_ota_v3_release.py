"""Exercise real v3 signing, staging and dev publication preparation locally."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from nacl.signing import SigningKey
from build.tests.test_target_manifest import CI, TAG, fixture, TargetManifestTests
import ota_v3_product as v3
import release_completeness as gate
from ota_v2_product import ContractError, digest, load_feature_contract, validate_control_tar


class V3ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        catalog, boot = fixture(self.root)
        with boot.open('r+b') as stream:
            stream.write(b'ANDROID!')
        self.run_dir = self.root / 'run'
        helper = TargetManifestTests()
        result = subprocess.run(helper.command(catalog, boot, self.run_dir), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.copyfile(boot, self.run_dir / 'boot.img')
        self.candidate = {'board': 'radar_puffin', 'ota_format': 'v3', 'ota_release': '0.14.0', 'update_channel': 'dev'}
        (self.run_dir / 'CURRENT.candidate').write_text(''.join(f'{k}={v}\n' for k, v in self.candidate.items()))
        key = SigningKey(bytes.fromhex('12'*32))
        self.secret = self.root / 'private.hex'
        self.secret.write_text(key.encode().hex() + '\n')
        self.public = self.run_dir / 'ota-public-key.hex'
        self.public.write_text(key.verify_key.encode().hex() + '\n')
        self.anchor = digest(self.public)[0]
        self.ota = self.run_dir / 'candidate.ota.tar'

    def sign(self):
        self.assertTrue(callable(getattr(v3, 'sign', None)), 'v3 signer missing')
        v3.sign(self.run_dir, self.secret, self.public, self.ota, self.anchor)

    def test_v3_signing_is_deterministic_and_existing_validator_dispatches(self):
        self.sign()
        original = self.ota.read_bytes()
        v3.sign(self.run_dir, self.secret, self.public, self.ota, self.anchor)
        self.assertEqual(original, self.ota.read_bytes())
        plan, inventory, assets, _ = load_feature_contract(self.run_dir, self.candidate)
        validate_control_tar(self.ota, self.public, 'v3', '0.14.0', feature_plan=plan,
                             feature_inventory=inventory, feature_asset_dir=assets,
                             expected_channel='dev', boot_path=self.run_dir / 'boot.img',
                             expected_key_sha256=self.anchor)
        with tarfile.open(self.ota) as archive:
            self.assertEqual(archive.extractfile('manifest').read(), (self.run_dir / 'target.manifest').read_bytes())
        with self.assertRaises(ContractError):
            v3.sign(self.run_dir, self.secret, self.public, self.ota, '0'*64)

    def test_parser_rejects_forbidden_unknown_missing_reordered_and_noncanonical(self):
        self.assertTrue(callable(getattr(v3, 'parse_manifest', None)), 'self-contained v3 parser missing')
        raw = (self.run_dir / 'target.manifest').read_bytes()
        plan = json.loads((self.run_dir / 'feature-plan.json').read_text())
        self.assertEqual(v3.parse_manifest(raw), plan)
        for bad in (raw + b'feature_wakeword_action=preserve\n', raw + b'unknown=value\n', raw.replace(b'config_schema=1\n', b''), raw.replace(b'config_schema=1\n', b'config_schema=01\n'), raw.replace(b'format=libreecho-ota-v3\nmanifest_version=1\n', b'manifest_version=1\nformat=libreecho-ota-v3\n'), raw.replace(b'minimum_updater_schema=3', b'minimum_updater_schema=2')):
            with self.subTest(bad=bad[-80:]), self.assertRaises(ContractError):
                v3.parse_manifest(bad)

    def test_stage_requires_no_catalog_and_rechecks_owned_bytes(self):
        self.sign()
        self.assertEqual(gate.stage(self.run_dir)['references'], json.loads((self.run_dir / gate.PROVENANCE).read_text())['references'])
        path = next((self.run_dir / 'ota-assets').glob('*.payload.squashfs'))
        path.write_bytes(b'changed after planning')
        with self.assertRaises(ContractError):
            gate.stage(self.run_dir)

    def test_dev_preparation_preserves_signed_release_identity_and_recovery_hashes(self):
        self.sign()
        self.assertTrue(callable(getattr(v3, 'parse_manifest', None)), 'v3 parser missing')
        spec = importlib.util.spec_from_file_location('v3_prepare_dev', CI / 'prepare-dev-release.py')
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        features = self.run_dir / 'features'
        features.mkdir()
        catalog = json.loads((self.root / 'catalog.json').read_text())
        candidate = dict(self.candidate, product_git_head='a'*40, boot_image_sha256=digest(self.run_dir / 'boot.img')[0], ota_bundle_sha256=digest(self.ota)[0])
        for fid, entry in catalog['features'].items():
            key = 'airplay' if fid == 'airplay2' else fid
            for kind, suffix in [('payload', '.squashfs'), ('manifest', '.manifest.json')]:
                shutil.copyfile(entry[kind]['path'], features / (fid + suffix))
            candidate[key + '_payload_sha256'] = entry['payload']['sha256']
            candidate[key + '_feature_manifest_sha256'] = entry['manifest']['sha256']
        verification = self.run_dir / 'verify.log'
        verification.write_text('PASS test fixture\n')
        out = self.root / 'release'
        out.mkdir()
        plan, _, assets, items = load_feature_contract(self.run_dir, self.candidate)
        with patch.dict(os.environ, {'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': self.anchor}):
            tag, count = prepare.prepare_complete_initial_install(self.run_dir, out, candidate, {}, verification, self.ota, '1'*16, '2'*16, 'development', plan, assets, items)
            self.assertEqual(tag, TAG)
            self.assertEqual(count, len(list(out.iterdir())))
            gate.check_assets(out)
        provenance = json.loads((out / gate.PROVENANCE).read_text())
        self.assertTrue(all(p['source_release'] == TAG for p in provenance['references']))
        with tarfile.open(next(out.glob('*initial-install.tar'))) as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
        self.assertEqual(manifest['features'], gate.install_features(plan, provenance))

    def test_build_entrypoint_uses_whole_state_planner_and_signer(self):
        source = (CI.parent / 'build.sh').read_text()
        self.assertIn('plan-target-manifest.py', source)
        self.assertIn('ota_v3_product.py', source)
        self.assertNotIn('plan-feature-transaction.py', source)


if __name__ == '__main__':
    unittest.main()
