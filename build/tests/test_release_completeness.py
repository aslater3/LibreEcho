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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan, self.provenance, self.bundle = fixture(self.root)

    def check(self, bundle=True):
        return gate.validate_completeness(self.plan, self.root, self.provenance,
                                          self.bundle if bundle else None)

    def test_complete_release_and_recovery_staging(self):
        self.check()

    def test_missing_each_reference_class(self):
        for role, kind in (('base', 'payload'), ('base', 'manifest'),
                           ('target', 'payload'), ('target', 'manifest')):
            with self.subTest(role=role, kind=kind):
                item = next(p for p in self.provenance['references'] if p['role'] == role and p['kind'] == kind)
                path = self.root / item['name']
                blob = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ContractError, 'unavailable|missing|regular'):
                    self.check()
                path.write_bytes(blob)

    def test_bb79646_preserved_wakeword_missing_then_bundled(self):
        # Same action topology as bb79646: replace airplay2/assistant, preserve
        # wakeword/tts/stt; the new wakeword candidate is NOT the signed base.
        item = next(p for p in self.provenance['references'] if p['feature_id'] == 'wakeword' and p['kind'] == 'payload')
        path = self.root / item['name']
        original = path.read_bytes()
        path.unlink()
        (self.root / 'new-wakeword.squashfs').write_bytes(b'new candidate, not the preserved base')
        with self.assertRaises(ContractError):
            self.check()
        path.write_bytes(original)
        self.check()

    def test_replace_bases_are_required_even_if_not_staged(self):
        item = next(p for p in self.provenance['references'] if p['feature_id'] == 'airplay2' and p['role'] == 'base')
        (self.root / item['name']).unlink()
        with self.assertRaises(ContractError):
            self.check()

    def test_corrupt_bytes_fail(self):
        (self.root / self.provenance['references'][0]['name']).write_bytes(b'corrupt')
        with self.assertRaises(ContractError):
            self.check()

    def test_missing_or_duplicate_provenance_fails(self):
        for records in (self.provenance['references'][1:],
                        self.provenance['references'] + [self.provenance['references'][0]]):
            with self.subTest(records=len(records)), self.assertRaises(ContractError):
                gate.validate_completeness(self.plan, self.root, dict(schema=gate.SCHEMA, target='radar_puffin', references=records))

    def test_unsafe_provenance_name_fails(self):
        self.provenance['references'][0]['name'] = '../outside'
        with self.assertRaises(ContractError):
            self.check()

    def test_symlink_asset_fails(self):
        path = self.root / self.provenance['references'][0]['name']
        path.rename(self.root / 'outside')
        path.symlink_to(self.root / 'outside')
        with self.assertRaises(ContractError):
            self.check()

    def test_staging_missing_wrong_or_duplicate_feature_fails(self):
        original = self.bundle.read_text()
        wake = next(line for line in original.splitlines() if line.startswith('staging=wakeword:'))
        for text in (original.replace(wake + '\n', ''),
                     original.replace(wake, wake.replace(self.plan['features'][2]['base_payload_sha256'], '0' * 64)),
                     original + wake + '\n'):
            with self.subTest(text=text):
                self.bundle.write_text(text)
                with self.assertRaises(ContractError):
                    self.check()

    def test_install_manifest_selects_preserve_base_not_candidate(self):
        result = gate.install_features(self.plan, self.provenance)
        wake = next(r for r in result if r['name'] == 'wakeword')
        self.assertEqual(wake['payload']['sha256'], self.plan['features'][2]['base_payload_sha256'])
        self.assertIn('-base-', wake['payload']['name'])

    def test_missing_source_release_fails(self):
        self.provenance['references'][0]['source_release'] = ''
        with self.assertRaises(ContractError):
            self.check()

    def test_runtime_fails_before_recovery_write(self):
        self.plan['features'][0]['action'] = 'runtime'
        self.plan['features'][0]['asset'] = self.plan['features'][0]['asset'].replace('.payload.', '.runtime.')
        self.plan['features'][0]['manifest_asset'] = self.plan['features'][0]['manifest_asset'].replace('.manifest.', '.runtime-manifest.')
        with self.assertRaisesRegex(ContractError, 'runtime'):
            gate.install_features(self.plan, self.provenance)


class BaseResolutionTests(unittest.TestCase):
    def test_workflows_gate_before_upload_and_both_publications(self):
        root = CI.parents[1]
        build = (root / '.github/workflows/build-release.yml').read_text()
        publish = (root / '.github/workflows/publish-release.yml').read_text()
        adapter = (CI / 'prepare-twrp-installs.py').read_text()
        self.assertIn('build.tests.test_release_completeness', build)
        self.assertLess(build.index('release_completeness.py stage'), build.index('name: Upload verified dev build'))
        self.assertLess(build.index('release_completeness.py check'), build.index('name: Upload verified dev build'))
        # The target-qualified recovery adapter is the completeness gate at both
        # publication boundaries. It must delegate to #199's gate per target
        # rather than reimplement or weaken it.
        self.assertIn('release_completeness.check_assets(selected, bundle_manifest, target=target)', adapter)
        dev = publish.split('publish-stable:', 1)[0]
        stable = publish.split('publish-stable:', 1)[1]
        self.assertLess(dev.index('prepare-twrp-installs.py'), dev.index('name: Publish development or nightly prerelease'))
        self.assertLess(stable.index('prepare-twrp-installs.py'), stable.index('name: Publish stable Product release'))

    def test_resolve_by_published_digest_rehashes_bytes_and_records_source(self):
        blob = b'published payload'
        sha = hashlib.sha256(blob).hexdigest()
        tag = 'radar-puffin-v0.13.10'
        asset = dict(name='wakeword.squashfs', digest='sha256:' + sha, size=len(blob),
                     browser_download_url=gate.DOWNLOAD + tag + '/wakeword.squashfs')
        release = dict(tag_name=tag, draft=False, assets=[asset])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def fetch(url, dest, cap):
                self.assertEqual(url, asset['browser_download_url'])
                self.assertEqual(cap, len(blob))
                dest.write_bytes(blob)
            path, source_tag, source_asset = gate.resolve_digest(sha, [release], root, fetch)
            self.assertEqual((path.read_bytes(), source_tag, source_asset), (blob, tag, asset['name']))
            path.unlink()
            with self.assertRaisesRegex(ContractError, 'digest/size mismatch'):
                gate.resolve_digest(sha, [release], root, lambda u, p, c: p.write_bytes(b'tampered'))
            with self.assertRaisesRegex(ContractError, 'not found'):
                gate.resolve_digest('0' * 64, [release], root, fetch)
            asset['browser_download_url'] = 'https://other.example/payload'
            with self.assertRaisesRegex(ContractError, 'URL/size'):
                gate.resolve_digest(sha, [release], root, fetch)

    def test_release_listing_excludes_drafts_and_unknown_target_tags(self):
        data = [dict(tag_name='radar-puffin-v0.13.10', draft=False),
                dict(tag_name='radar-puffin-v0.13.11', draft=True),
                dict(tag_name='other-target-v0.13.10', draft=False)]
        with tempfile.TemporaryDirectory() as directory:
            selected = gate.published_releases(Path(directory), lambda u, p, c: p.write_text(json.dumps(data)))
        self.assertEqual([r['tag_name'] for r in selected], ['radar-puffin-v0.13.10'])

    def test_stage_catalog_copies_every_base_without_rewriting_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            plan, provenance, _ = fixture(source)
            run = root / 'run'
            run.mkdir()
            (run / 'ota-assets').mkdir()
            plan_bytes = json.dumps(plan).encode()
            (run / 'feature-plan.json').write_bytes(plan_bytes)
            catalog = {'features': {}, 'sources': {}}
            for item in provenance['references']:
                fid, kind = item['feature_id'], item['kind']
                if item['role'] == 'base':
                    catalog['features'].setdefault(fid, {})[kind] = {
                        'path': str(source / item['name']), 'sha256': item['sha256'], 'size': item['size']}
                    catalog['sources'].setdefault(fid, {})[kind] = {
                        'release': item['source_release'], 'asset': item['name']}
                else:
                    shutil.copyfile(source / item['name'], run / 'ota-assets' / item['name'])
            path = root / 'catalog.json'
            path.write_text(json.dumps(catalog))
            data = gate.stage(run, path)
            self.assertEqual(len(data['references']), 14)
            self.assertEqual(len(list((run / 'release-bases').iterdir())), 10)
            self.assertEqual((run / 'feature-plan.json').read_bytes(), plan_bytes)
            self.assertTrue(all(r['name'].startswith('libreecho-radar-puffin-base-')
                                for r in data['references'] if r['role'] == 'base'))

    def test_wrong_target_provenance_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, data, bundle = fixture(root)
            data['target'] = 'other_target'
            with self.assertRaisesRegex(ContractError, 'schema mismatch'):
                gate.validate_completeness(plan, root, data, bundle)


class RecoverySurfaceTests(unittest.TestCase):
    def test_stable_recovery_outputs_are_in_both_exact_inventories(self):
        import os
        import importlib.util
        import subprocess
        from build.tests.test_release_packaging import fixture as stable_fixture, add_v2_contract, WORKING_AMONET_COMMIT
        from ota_v2_product import digest, validate_stable_publisher
        platform = Path(os.environ.get('LIBREECHO_PLATFORM_SRC', str(CI.parents[1].parent / 'platform')))
        builder = platform / 'tools/mt8163-arm32/recovery-install'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts, product = stable_fixture(root)
            add_v2_contract(artifacts, action='replace')
            key_hash = digest(artifacts / 'run/ota-public-key.hex')[0]
            out = root / 'release'
            with patch.dict(os.environ, {'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': key_hash}):
                result = subprocess.run([sys.executable, str(CI / 'prepare-stable-release.py'),
                    '--artifact-root', str(artifacts), '--product-root', str(product), '--product-commit', '1' * 40,
                    '--release-version', '0.14.0', '--release-notes', 'release/radar-puffin-v0.14.0.md',
                    '--amonet-repository', 'https://github.com/aslater3/amonet-k32', '--amonet-tag', 'v1.0.0',
                    '--amonet-commit', WORKING_AMONET_COMMIT, '--output-dir', str(out)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                spec = importlib.util.spec_from_file_location('stable_builder', builder / 'build_install_bundle.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                recovery = root / 'recovery'
                module.assemble(out, recovery, builder / 'src', '', 2153472)
                for name in ('libreecho-install.zip', 'bundle.manifest'):
                    shutil.copyfile(recovery / name, out / name)
                gate.record_recovery(out)
                validate_stable_publisher(out, 'radar-puffin-v0.14.0', key_hash)
                inventory = json.loads(next(out.glob('*-build.json')).read_text())
                self.assertTrue({'libreecho-install.zip', 'bundle.manifest'}.issubset(
                    {r['name'] for r in inventory['artifacts']}))

    def test_real_signed_build_and_published_recovery_bundle(self):
        import os
        import importlib.util
        import tarfile
        from build.tests.test_prepare_dev_release import fixture as dev_fixture, add_v2_contract, make_signed_ota, digest
        platform = Path(os.environ.get('LIBREECHO_PLATFORM_SRC', str(CI.parents[1].parent / 'platform')))
        builder = platform / 'tools/mt8163-arm32/recovery-install'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, commits = dev_fixture(root)
            add_v2_contract(run, commits, action='replace')
            ota = run / 'development.ota.tar'
            make_signed_ota(run, ota, '0.14.0', 'v2', run / 'feature-plan.json')
            with patch.dict(os.environ, {'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': digest(run / 'ota-public-key.hex')}):
                gate.check_run(run, builder)
                spec = importlib.util.spec_from_file_location('prepare_dev', CI / 'prepare-dev-release.py')
                prepare = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(prepare)
                from ota_v2_product import load_feature_contract
                candidate = dict(line.split('=', 1) for line in (run / 'CURRENT.candidate').read_text().splitlines() if '=' in line)
                plan, _, asset_dir, assets = load_feature_contract(run, candidate)
                candidate['ota_bundle_sha256'] = digest(ota)
                out = root / 'release'
                out.mkdir()
                tag, count = prepare.prepare_complete_initial_install(run, out, candidate, commits, run / 'verify.log',
                    ota, '1' * 16, '2' * 16, 'development', plan, asset_dir, assets)
                self.assertEqual(count, len(list(out.iterdir())))
                with tarfile.open(next(out.glob('*initial-install.tar'))) as tar:
                    manifest = json.load(tar.extractfile('manifest.json'))
                wake = next(f for f in manifest['features'] if f['name'] == 'wakeword')
                self.assertEqual(wake['payload']['sha256'], plan['features'][2]['base_payload_sha256'])
                spec = importlib.util.spec_from_file_location('builder', builder / 'build_install_bundle.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                recovery = root / 'recovery'
                module.assemble(out, recovery, builder / 'src', '', 2153472)
                gate.check_assets(out, recovery / 'bundle.manifest')
                # A combined release carries another target's differently
                # signed control members. Those must not contaminate Radar's
                # gate; conflicting control members for Radar still fail.
                import io
                def conflicting_archive(path):
                    with tarfile.open(path, 'w') as archive:
                        entry = tarfile.TarInfo('manifest')
                        blob = b'different target control manifest'
                        entry.size = len(blob)
                        archive.addfile(entry, io.BytesIO(blob))
                other = out / 'libreecho-biscuit-v0.14.0.ota.tar'
                conflicting_archive(other)
                gate.check_assets(out, recovery / 'bundle.manifest')
                other.unlink()
                same = out / 'libreecho-radar-puffin-conflicting.ota.tar'
                conflicting_archive(same)
                with self.assertRaisesRegex(ContractError, 'conflicting bundled'):
                    gate.check_assets(out, recovery / 'bundle.manifest')
                same.unlink()
                # Exercise the shipped standalone host installer, no transport.
                spec = importlib.util.spec_from_file_location('installer', CI.parents[1] / 'tools/libreecho-install.py')
                installer = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(installer)
                installer._prepare(out, root / 'cache', tag)
                original = (recovery / 'bundle.manifest').read_text()
                wrong = next(line for line in original.splitlines() if line.startswith('staging=wakeword:'))
                (recovery / 'bundle.manifest').write_text(original.replace(wrong + '\n', ''))
                with self.assertRaisesRegex(ContractError, 'staging'):
                    gate.check_assets(out, recovery / 'bundle.manifest')


if __name__ == '__main__':
    unittest.main()
