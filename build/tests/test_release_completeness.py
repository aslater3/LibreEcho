"""Fresh-install/base closure, independent of changed-only OTA inventory."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1] / 'ci'
sys.path.insert(0, str(CI))
import release_completeness as gate
from ota_v2_product import ContractError, DAEMONS, FEATURES


def fixture(root):
    records, provenance, staging = [], [], []
    for feature in FEATURES:
        record = dict(feature_id=feature, action='preserve', activation='reboot',
                      daemon_path=DAEMONS[feature], daemon_sha256='c' * 64,
                      release='0.14.0', source_commit='4' * 40)
        for role in ('base', 'target'):
            if role == 'target' and feature not in ('airplay2', 'assistant'):
                continue
            for kind, suffix in (('payload', 'payload.squashfs'), ('manifest', 'manifest.json')):
                name = (f'libreecho-base-{feature}.{suffix}' if role == 'base' else
                        f'libreecho-radar-puffin-0.14.0-{feature}.{suffix}')
                blob = f'{feature} {role} {kind}'.encode()
                if kind == 'manifest':
                    payload = root / name.replace('manifest.json', 'payload.squashfs')
                    blob = json.dumps(dict(schema_version=1, feature_id=feature,
                        format='squashfs-lz4', payload=dict(filename=payload.name,
                        sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
                        size=payload.stat().st_size), files={DAEMONS[feature]: {'sha256': 'c' * 64}})).encode()
                path = root / name
                path.write_bytes(blob)
                sha = hashlib.sha256(blob).hexdigest()
                provenance.append(dict(feature_id=feature, role=role, kind=kind, name=name,
                    sha256=sha, size=len(blob), source_release='radar-puffin-v0.13.10', source_asset=name))
                if role == 'base':
                    record[f'base_{kind}_sha256'] = sha
                else:
                    record['action'] = 'replace'
                    record.update({('asset' if kind == 'payload' else 'manifest_asset'): name,
                                   ('sha256' if kind == 'payload' else 'manifest_sha256'): sha,
                                   ('size' if kind == 'payload' else 'manifest_size'): len(blob)})
        records.append(record)
        items = [p for p in provenance if p['feature_id'] == feature and
                 p['role'] == ('base' if record['action'] == 'preserve' else 'target')]
        staging.append('staging=' + feature + ':' + ':'.join(
            x for p in sorted(items, key=lambda p: p['kind'], reverse=True) for x in (p['name'], p['sha256'])))
    # Renaming a source manifest externally must not rewrite its signed bytes.
    for p in provenance:
        if p['role'] == 'base':
            old = p['name']
            suffix = 'payload.squashfs' if p['kind'] == 'payload' else 'manifest.json'
            p['name'] = f"libreecho-radar-puffin-base-{p['feature_id']}-{p['sha256']}.{suffix}"
            (root / old).rename(root / p['name'])
            staging = [line.replace(old, p['name']) for line in staging]
    plan = dict(schema='libreecho-product-feature-plan-v1', transaction_type='system',
                activation='reboot', release='0.14.0', source_commit='4' * 40, features=records)
    provenance = dict(schema=gate.SCHEMA, target='radar_puffin', references=provenance)
    bundle = root / 'bundle.manifest'
    bundle.write_text('schema=1\n' + '\n'.join(staging) + '\n')
    return plan, provenance, bundle


def complete_v2_fixture(run):
    """Add real, hash-bound full bases to legacy OTA test fixtures."""
    plan_path = run / 'feature-plan.json'
    plan = json.loads(plan_path.read_text())
    base_dir = run / 'release-bases'
    base_dir.mkdir(exist_ok=True)
    refs = []
    for record in plan['features']:
        fid = record['feature_id']
        payload = (fid + ' published base').encode()
        psha = hashlib.sha256(payload).hexdigest()
        pname = f'libreecho-radar-puffin-base-{fid}-{psha}.payload.squashfs'
        manifest = json.dumps(dict(schema_version=1, feature_id=fid,
            format='squashfs-lz4', payload=dict(filename=pname, sha256=psha, size=len(payload)),
            files={DAEMONS[fid]: {'sha256': record['daemon_sha256']}})).encode()
        msha = hashlib.sha256(manifest).hexdigest()
        mname = f'libreecho-radar-puffin-base-{fid}-{msha}.manifest.json'
        for kind, name, blob, sha in (('payload', pname, payload, psha), ('manifest', mname, manifest, msha)):
            (base_dir / name).write_bytes(blob)
            record[f'base_{kind}_sha256'] = sha
            refs.append(dict(feature_id=fid, role='base', kind=kind, name=name, sha256=sha,
                             size=len(blob), source_release='radar-puffin-v0.13.10', source_asset=name))
        if record['action'] != 'preserve':
            for kind in ('payload', 'manifest'):
                name = record['asset' if kind == 'payload' else 'manifest_asset']
                path = run / 'ota-assets' / name
                refs.append(dict(feature_id=fid, role='target', kind=kind, name=name,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size,
                    source_release='current-build', source_asset=name))
    plan_path.write_text(json.dumps(plan) + '\n')
    (run / gate.PROVENANCE).write_text(json.dumps(dict(schema=gate.SCHEMA, target='radar_puffin', references=refs)) + '\n')


class CompletenessTests(unittest.TestCase):
    """All new releases are complete v3 targets, including the first of a board."""
    def setUp(self):
        import subprocess
        from build.tests import test_target_manifest as target_tests
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        catalog, boot = target_tests.fixture(self.root)
        self.run = self.root / 'run'
        helper = target_tests.TargetManifestTests()
        result = subprocess.run(helper.command(catalog, boot, self.run), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assets = self.run / 'ota-assets'
        self.plan = json.loads((self.run / 'feature-plan.json').read_text())
        self.provenance = json.loads((self.run / gate.PROVENANCE).read_text())
        self.bundle = self.run / 'bundle.manifest'
        refs = {(p['feature_id'], p['kind']): p for p in self.provenance['references']}
        self.bundle.write_text('schema=1\n' + ''.join('staging=' + ':'.join([fid, refs[fid, 'payload']['name'], refs[fid, 'payload']['sha256'], refs[fid, 'manifest']['name'], refs[fid, 'manifest']['sha256']]) + '\n' for fid in FEATURES))

    def check(self):
        return gate.validate_completeness(self.plan, self.assets, self.provenance, self.bundle)

    def test_complete_release_and_recovery_staging(self):
        self.assertEqual(len(self.check()), 10)

    def test_missing_each_reference_class(self):
        for item in self.provenance['references']:
            path = self.assets / item['name']
            blob = path.read_bytes()
            path.unlink()
            with self.subTest(feature=item['feature_id'], kind=item['kind']), self.assertRaises(ContractError):
                self.check()
            path.write_bytes(blob)

    def test_corrupt_bytes_fail(self):
        (self.assets / self.provenance['references'][0]['name']).write_bytes(b'corrupt')
        with self.assertRaises(ContractError): self.check()

    def test_missing_or_duplicate_provenance_fails(self):
        for records in (self.provenance['references'][1:], self.provenance['references'] + [self.provenance['references'][0]]):
            with self.assertRaises(ContractError):
                gate.validate_completeness(self.plan, self.assets, dict(schema=gate.SCHEMA, target='radar_puffin', references=records))

    def test_unsafe_provenance_name_fails(self):
        self.provenance['references'][0]['name'] = '../outside'
        with self.assertRaises(ContractError): self.check()

    def test_symlink_asset_fails(self):
        path = self.assets / self.provenance['references'][0]['name']
        path.rename(self.root / 'outside')
        path.symlink_to(self.root / 'outside')
        with self.assertRaises(ContractError): self.check()

    def test_staging_missing_wrong_or_duplicate_feature_fails(self):
        original = self.bundle.read_text()
        wake = next(line for line in original.splitlines() if line.startswith('staging=wakeword:'))
        for text in (original.replace(wake + '\n', ''), original.replace(wake, wake.replace(self.plan['features'][2]['sha256'], '0'*64)), original + wake + '\n'):
            self.bundle.write_text(text)
            with self.assertRaises(ContractError): self.check()

    def test_recovery_selects_all_target_bytes(self):
        result = gate.install_features(self.plan, self.provenance)
        for feature, record in zip(result, self.plan['features']):
            self.assertEqual(feature['payload']['sha256'], record['sha256'])
            self.assertEqual(feature['manifest']['sha256'], record['manifest_sha256'])

    def test_foreign_or_missing_source_release_fails(self):
        for source in ('', 'current-build', 'radar-puffin-v0.13.10'):
            self.provenance['references'][0]['source_release'] = source
            with self.assertRaisesRegex(ContractError, 'source release'): self.check()

    def test_foreign_source_asset_fails(self):
        self.provenance['references'][0]['source_asset'] = 'foreign.payload.squashfs'
        with self.assertRaises(ContractError): self.check()

    def test_daemon_manifest_mismatch_fails(self):
        self.plan['features'][0]['daemon_sha256'] = 'f'*64
        from ota_v3_product import transaction_id
        self.plan['transaction_id'] = transaction_id(self.plan)
        with self.assertRaisesRegex(ContractError, 'daemon'): self.check()

    def test_invalid_size_type_fails(self):
        self.provenance['references'][0]['size'] = True
        with self.assertRaises(ContractError): self.check()

    def test_wrong_target_provenance_fails(self):
        self.provenance['target'] = 'biscuit'
        with self.assertRaisesRegex(ContractError, 'schema mismatch'): self.check()

    def test_forbidden_policy_fails_before_recovery_write(self):
        self.plan['feature_policy'] = 'preserve'
        with self.assertRaisesRegex(ContractError, 'forbidden'):
            gate.install_features(self.plan, self.provenance)

    def test_stage_rehashes_every_owned_asset(self):
        gate.stage(self.run)
        (self.assets / self.provenance['references'][0]['name']).write_bytes(b'corrupt')
        with self.assertRaises(ContractError): gate.stage(self.run)

    def test_ship_does_not_rebind_foreign_provenance(self):
        self.provenance['references'][0]['source_release'] = 'radar-puffin-v0.13.10'
        (self.run / gate.PROVENANCE).write_text(json.dumps(self.provenance))
        with self.assertRaisesRegex(ContractError, 'source release'):
            gate.ship(self.run, self.assets, 'libreecho-' + self.plan['release'], self.plan)

    def test_ship_refuses_different_publication_tag(self):
        with self.assertRaisesRegex(ContractError, 'publication tag'):
            gate.ship(self.run, self.assets, 'libreecho-radar-puffin-v0.14.0', self.plan)

    def test_workflows_gate_before_upload_and_both_publications(self):
        root = CI.parents[1]
        build = (root / '.github/workflows/build-release.yml').read_text()
        publish = (root / '.github/workflows/publish-release.yml').read_text()
        adapter = (CI / 'prepare-twrp-installs.py').read_text()
        self.assertIn('build.tests.test_release_completeness', build)
        self.assertLess(build.index('release_completeness.py stage'), build.index('name: Upload verified dev build'))
        self.assertLess(build.index('release_completeness.py check'), build.index('name: Upload verified dev build'))
        self.assertIn('release_completeness.check_assets(selected, bundle_manifest, target=target)', adapter)
        dev, stable = publish.split('  publish-stable:', 1)
        self.assertLess(dev.index('prepare-twrp-installs.py'), dev.index('name: Publish development or nightly prerelease'))
        self.assertLess(stable.index('prepare-twrp-installs.py'), stable.index('name: Publish stable Product release'))


if __name__ == '__main__':
    unittest.main()
