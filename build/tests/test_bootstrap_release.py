"""Fresh-install-only first release for a never-released, non-default target.

Host-only: real planner, real Platform OTA signer and recovery bundle builder,
real Product preparation and TWRP packaging. No network, release, or pointer
mutation. The GitHub lookup is simulated only at the subprocess boundary.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / 'build/ci'
sys.path.insert(0, str(CI))
from target_registry import descriptor_sha256  # noqa: E402
import release_completeness as gate  # noqa: E402
from ota_v2_product import ContractError, DAEMONS, FEATURES  # noqa: E402
from build.tests.test_prepare_dev_release import fixture as dev_fixture  # noqa: E402

PLATFORM = Path(os.environ.get('LIBREECHO_PLATFORM_SRC', str(ROOT.parent / 'platform')))
OTA_TOOL = PLATFORM / 'tools/mt8163-arm32/ota/make_ota_bundle.py'
PRIOR = 'radar-puffin-build-bb79646-4f32d15c721c8c85-b79745773b3f5ff7'
NO_BASE = 'Traceback (most recent call last):\n  ...\nValueError: no preceding stable release\n'


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BASES = module('fetch_ota_bases', CI / 'fetch-ota-bases.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kv(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def bootstrap_run(root: Path, target='biscuit', *, bootstrap=True, signing=None):
    """A signed target run whose base is its own real five-feature catalog.

    With bootstrap=False the same base is presented as a published prior
    release (catalog sources carry a real release tag), as for Radar.
    """
    run, commits = dev_fixture(root)
    features = run / 'features'
    catalog = {'features': {}, 'board': target}
    for fid in FEATURES:
        payload = features / f'{fid}.squashfs'
        payload.write_bytes(f'shared {fid} squashfs payload'.encode())
        daemon = DAEMONS[fid]
        manifest = {'schema_version': 1, 'feature_id': fid, 'format': 'squashfs-lz4',
                    'payload': {'filename': payload.name, 'sha256': sha(payload), 'size': payload.stat().st_size},
                    'files': {daemon: {'sha256': hashlib.sha256(fid.encode()).hexdigest(), 'size': 1, 'mode': '0755'}}}
        (features / f'{fid}.manifest.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
        catalog['features'][fid] = {kind: {'path': str(p), 'sha256': sha(p), 'size': p.stat().st_size}
                                    for kind, p in (('payload', payload), ('manifest', features / f'{fid}.manifest.json'))}
    candidate = kv(run / 'CURRENT.candidate')
    for fid in FEATURES:
        key = 'airplay' if fid == 'airplay2' else fid
        candidate[f'{key}_payload_sha256'] = sha(features / f'{fid}.squashfs')
        candidate[f'{key}_payload_size'] = str((features / f'{fid}.squashfs').stat().st_size)
        candidate[f'{key}_feature_manifest_sha256'] = sha(features / f'{fid}.manifest.json')
    # build.sh bootstrap: the candidate catalog is copied as the base catalog.
    candidate_catalog = run / 'feature-candidate-catalog.json'
    candidate_catalog.write_text(json.dumps(catalog, indent=2, sort_keys=True) + '\n')
    base = run / 'ota-base-catalog.json'
    if bootstrap:
        shutil.copyfile(candidate_catalog, base)
    else:
        published = json.loads(candidate_catalog.read_text())
        published['sources'] = {fid: {kind: {'release': PRIOR, 'asset': f'libreecho-{PRIOR}-{fid}{suffix}'}
                                      for kind, suffix in (('payload', '.squashfs'), ('manifest', '.manifest.json'))}
                                for fid in FEATURES}
        base.write_text(json.dumps(published, sort_keys=True) + '\n')
    plan, inventory = run / 'feature-plan.json', run / 'feature-assets.json'
    result = subprocess.run([sys.executable, str(CI / 'plan-feature-transaction.py'), '--target', target,
        '--update-channel', 'dev', '--base-catalog', str(base), '--candidate-catalog', str(candidate_catalog),
        '--release', '0.14.0', '--source-commit', commits['ui'], '--output', str(plan),
        '--inventory-output', str(inventory), '--asset-output-dir', str(run / 'ota-assets')],
        capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    image = json.loads((run / 'manifest.json').read_text())
    image.update(board=target, target_descriptor_sha256=descriptor_sha256(target))
    (run / 'manifest.json').write_text(json.dumps(image))
    signing = signing or SigningKey.generate()
    (run / 'fixture-signing-key.hex').write_text(signing.encode().hex() + '\n')
    (run / 'ota-public-key.hex').write_text(signing.verify_key.encode().hex() + '\n')
    ota = run / 'bootstrap.ota.tar'
    signed = subprocess.run([sys.executable, str(OTA_TOOL), '--format', 'v2', '--boot-image', str(run / 'boot.img'),
        '--build-manifest', str(run / 'manifest.json'), '--version', '0.14.0',
        '--signing-key', str(run / 'fixture-signing-key.hex'), '--public-key', str(run / 'ota-public-key.hex'),
        '--service-profile', 'production', '--feature-policy', 'community-noncommercial', '--update-channel', 'dev',
        '--target', target, '--target-descriptor-sha256', descriptor_sha256(target), '--feature-plan', str(plan), '--output', str(ota)], capture_output=True, text=True, timeout=120)
    if signed.returncode:
        raise AssertionError(signed.stderr)
    (run / 'fixture-signing-key.hex').unlink()
    candidate.update(board=target, target_descriptor_sha256=descriptor_sha256(target), ota_format='v2',
                     ota_release='0.14.0', feature_plan=str(plan), feature_asset_inventory=str(inventory),
                     feature_asset_dir=str(run / 'ota-assets'), ota_signing_mode='local', ota_bundle=str(ota),
                     ota_bundle_sha256=sha(ota), ota_base_catalog_sha256=sha(base), ota_base_bootstrap='1' if bootstrap else '0')
    (run / 'CURRENT.candidate').write_text(''.join(f'{k}={v}\n' for k, v in candidate.items()))
    return run, commits, sha(run / 'ota-public-key.hex')


def builder(run: Path):
    dest = run / 'twrp-builder'
    (dest / 'src').mkdir(parents=True)
    src = PLATFORM / 'tools/mt8163-arm32/recovery-install'
    shutil.copy(src / 'build_install_bundle.py', dest)
    for py in (PLATFORM / 'tools/mt8163-arm32').glob('*.py'):
        shutil.copy(py, dest)
    shutil.copytree(src / 'src', dest / 'src', dirs_exist_ok=True)
    return dest


class BaseSelectionTests(unittest.TestCase):
    def runner(self, table):
        def run(target, *_):
            rc, out, err = table[target]
            return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        return run

    def test_named_never_released_target_bootstraps(self):
        with tempfile.TemporaryDirectory() as tmp:
            records, _ = BASES.resolve('radar_puffin,biscuit', 'biscuit', '0.14.0', 'dev', Path(tmp) / 'b',
                                       self.runner({'radar_puffin': (0, 'catalog=/c\nsha256=' + 'a' * 64 + '\n', ''),
                                                    'biscuit': (1, '', NO_BASE)}))
            self.assertEqual(records['biscuit'], {'bootstrap': '1'})
            self.assertEqual(records['radar_puffin']['catalog'], '/c')

    def test_unnamed_target_without_baseline_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as caught:
            BASES.resolve('radar_puffin,biscuit', '', '0.14.0', 'dev', Path(tmp) / 'b',
                          self.runner({'radar_puffin': (0, 'catalog=/c\nsha256=' + 'a' * 64 + '\n', ''), 'biscuit': (1, '', NO_BASE)}))
        self.assertIn('biscuit OTA v2 baseline unavailable', str(caught.exception))

    def test_bootstrap_refusals(self):
        ok = (0, 'catalog=/c\nsha256=' + 'a' * 64 + '\n', '')
        cases = [
            ('radar_puffin', {'radar_puffin': ok}, 'radar_puffin', 'always has a published baseline'),
            ('radar_puffin', {'radar_puffin': ok}, 'biscuit', 'not selected'),
            ('radar_puffin,biscuit', {'radar_puffin': ok, 'biscuit': ok}, 'biscuit', 'already has a published baseline'),
            ('radar_puffin,biscuit', {'radar_puffin': ok, 'biscuit': (1, '', 'ValueError: release asset digest/size mismatch\n')},
             'biscuit', 'other than no prior release'),
            ('radar_puffin,biscuit', {'radar_puffin': ok, 'biscuit': (1, '', 'urllib.error.URLError: timed out\n')},
             'biscuit', 'other than no prior release'),
        ]
        for targets, table, boot, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as caught:
                BASES.resolve(targets, boot, '0.14.0', 'dev', Path(tmp) / 'b', self.runner(table))
            self.assertIn(message, str(caught.exception))


class BootstrapReleaseTests(unittest.TestCase):
    def test_bootstrap_stage_check_prepare_and_twrp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'verified-build'
            run, commits, key = bootstrap_run(root)
            env = {**os.environ, 'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': key,
                   'LIBREECHO_PLATFORM_SOURCE': str(PLATFORM)}
            plan = json.loads((run / 'feature-plan.json').read_text())
            self.assertEqual({r['action'] for r in plan['features']}, {'preserve'})
            self.assertEqual(json.loads((run / 'feature-assets.json').read_text())['assets'], [])
            dest = builder(run)
            # Exact workflow CLI: stage with the run's own catalog, then check.
            for args in (['stage', '--run', str(run), '--base-catalog', str(run / 'ota-base-catalog.json')],
                         ['check', '--run', str(run), '--builder', str(dest)]):
                result = subprocess.run([sys.executable, str(CI / 'release_completeness.py'), *args],
                                        capture_output=True, text=True, env=env, timeout=300)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'release_completeness=PASS target=biscuit')
            provenance = json.loads((run / 'libreecho-biscuit-release-completeness.json').read_text())
            self.assertIs(provenance['bootstrap'], True)
            self.assertEqual({r['source_release'] for r in provenance['references']}, {'current-build'})
            out = Path(tmp) / 'release-assets'
            prepared = subprocess.run([sys.executable, str(CI / 'prepare-dev-release.py'), '--artifact-root', str(root),
                '--product-commit', commits['product'], '--release-kind', 'development', '--output-dir', str(out),
                '--target', 'biscuit'], capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            values = dict(line.split('=', 1) for line in prepared.stdout.splitlines() if '=' in line)
            prefix = values['release_prefix']
            self.assertTrue(prefix.startswith('libreecho-biscuit-build-'))
            published = json.loads((out / 'libreecho-biscuit-release-completeness.json').read_text())
            self.assertEqual({r['source_release'] for r in published['references']}, {values['release_tag']})
            from unittest import mock
            anchor = mock.patch.dict(os.environ, {'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': key})
            anchor.start(); self.addCleanup(anchor.stop)
            gate.check_assets(out, target='biscuit')
            with tarfile.open(out / f'{prefix}-initial-install.tar') as archive:
                manifest = json.load(archive.extractfile('manifest.json'))
            self.assertEqual(manifest['board'], 'biscuit')
            for item in manifest['features']:
                fid = item['name']
                self.assertEqual(item['payload']['sha256'], sha(run / 'features' / f'{fid}.squashfs'))
            twrp = subprocess.run([sys.executable, str(CI / 'prepare-twrp-installs.py'), '--assets', str(out),
                                   '--artifact-root', str(root)], capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(twrp.returncode, 0, twrp.stderr)
            bundle = (out / 'libreecho-biscuit-bundle.manifest').read_text()
            self.assertIn('target=biscuit\n', bundle)
            self.assertIn('fastboot_products=BISCUIT\n', bundle)
            self.assertEqual(bundle.count('staging='), 5)
            gate.check_assets(out, out / 'libreecho-biscuit-bundle.manifest', target='biscuit')
            sums = subprocess.run('sha256sum -c *-SHA256SUMS', shell=True, cwd=out, capture_output=True, text=True)
            self.assertEqual(sums.returncode, 0, sums.stdout + sums.stderr)

    def test_bootstrap_cannot_smuggle_foreign_or_replaced_bases(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _, key = bootstrap_run(Path(tmp) / 'b')
            # A bootstrap run must never be Radar.
            candidate = kv(run / 'CURRENT.candidate')
            with self.assertRaises(ContractError):
                gate._bootstrap_run(run, 'radar_puffin')
            # Feature bytes changed after planning: refused.
            (run / 'features/tts.squashfs').write_bytes(b'tampered')
            with self.assertRaises(ContractError):
                gate.stage(run, run / 'ota-base-catalog.json', target='biscuit')
            del candidate

    def test_non_bootstrap_provenance_still_rejects_current_build_bases(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _, _ = bootstrap_run(Path(tmp) / 'b')
            gate.stage(run, run / 'ota-base-catalog.json', target='biscuit')
            data = json.loads((run / 'libreecho-biscuit-release-completeness.json').read_text())
            plan = json.loads((run / 'feature-plan.json').read_text())
            flat = Path(tmp) / 'flat'; flat.mkdir()
            for item in data['references']:
                shutil.copyfile(run / 'release-bases' / item['name'], flat / item['name'])
            gate.validate_completeness(plan, flat, data, target='biscuit')
            forged = {k: v for k, v in data.items() if k != 'bootstrap'}
            with self.assertRaises(ContractError):
                gate.validate_completeness(plan, flat, forged, target='biscuit')
            with self.assertRaises(ContractError):
                gate.validate_completeness(plan, flat, {**data, 'target': 'radar_puffin'}, target='radar_puffin')
            with self.assertRaises(ContractError):
                gate.validate_completeness(plan, flat, {**data, 'bootstrap': 'yes'}, target='biscuit')


    def test_combined_radar_plus_bootstrap_biscuit_publication(self):
        """The exact hosted combined sequence: gate loop, combined prepare, TWRP."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            art = tmp / 'verified-build'; art.mkdir()
            signing = SigningKey.generate()
            runs = {}
            for target, boot in (('radar_puffin', False), ('biscuit', True)):
                run, commits, key = bootstrap_run(tmp / target, target, bootstrap=boot, signing=signing)
                for name in ('zImage', 'System.map', 'kernel.config', 'libreecho-radar-puffin.dtb'):
                    (run / name).write_bytes(('shared parity fixture: ' + name).encode())
                runs[target] = art / target
                shutil.move(str(run), runs[target])
                # Paths recorded in the candidate/catalog moved with the run.
                for path in (runs[target] / 'CURRENT.candidate', runs[target] / 'ota-base-catalog.json',
                             runs[target] / 'feature-candidate-catalog.json'):
                    path.write_text(path.read_text().replace(str(run), str(runs[target])))
                c = kv(runs[target] / 'CURRENT.candidate')
                c['ota_base_catalog_sha256'] = sha(runs[target] / 'ota-base-catalog.json')
                (runs[target] / 'CURRENT.candidate').write_text(''.join(f'{k}={v}\n' for k, v in c.items()))
            (art / 'release-request.json').write_text(json.dumps({'targets': ['radar_puffin', 'biscuit']}))
            env = {**os.environ, 'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': key, 'LIBREECHO_PLATFORM_SOURCE': str(PLATFORM)}
            dest = builder(runs['radar_puffin'])
            # build-release.yml gate loop. Radar's published bases are faked at
            # the resolver boundary only: stage copies catalog bytes, and the
            # recorded source release is the real prior tag.
            for target, run in runs.items():
                catalog = run / 'ota-base-catalog.json'
                for args in (['stage', '--run', str(run), '--base-catalog', str(catalog)],
                             ['check', '--run', str(run), '--builder', str(dest)]):
                    r = subprocess.run([sys.executable, str(CI / 'release_completeness.py'), *args],
                                       capture_output=True, text=True, env=env, timeout=300)
                    self.assertEqual(r.returncode, 0, f'{target} {args[0]}: {r.stderr}')
                    self.assertEqual(r.stdout.strip(), f'release_completeness=PASS target={target}')
            radar_prov = json.loads((runs['radar_puffin'] / 'libreecho-radar-puffin-release-completeness.json').read_text())
            self.assertNotIn('bootstrap', radar_prov)
            self.assertEqual({r['source_release'] for r in radar_prov['references']}, {PRIOR})
            # publish-release.yml dev lane.
            out = tmp / 'release-assets'
            r = subprocess.run([sys.executable, str(CI / 'prepare-dev-release.py'), '--artifact-root', str(art),
                '--product-commit', commits['product'], '--release-kind', 'development', '--output-dir', str(out)],
                capture_output=True, text=True, env=env, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
            values = dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)
            self.assertEqual(values['targets'], 'radar_puffin,biscuit')
            self.assertTrue(values['release_tag'].startswith('radar-puffin-build-'))
            meta = subprocess.run([sys.executable, str(ROOT / 'tools/check-public-metadata.py'), str(out)],
                                  capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(meta.returncode, 0, meta.stdout + meta.stderr)
            twrp = subprocess.run([sys.executable, str(CI / 'prepare-twrp-installs.py'), '--assets', str(out),
                                   '--artifact-root', str(art)], capture_output=True, text=True, env=env, timeout=600)
            self.assertEqual(twrp.returncode, 0, twrp.stderr)
            sums = subprocess.run('sha256sum -c *-SHA256SUMS', shell=True, cwd=out, capture_output=True, text=True)
            self.assertEqual(sums.returncode, 0, sums.stdout + sums.stderr)
            names = {p.name for p in out.iterdir()}
            for target, slug, product in (('radar_puffin', 'radar-puffin', 'RADAR'), ('biscuit', 'biscuit', 'BISCUIT')):
                bundle = out / f'libreecho-{slug}-bundle.manifest'
                self.assertIn(f'libreecho-{slug}-install.zip', names)
                self.assertIn(f'target={target}\n', bundle.read_text())
                self.assertIn(f'fastboot_products={product}\n', bundle.read_text())
            with unittest.mock.patch.dict(os.environ, {'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': key}):
                for target, slug in (('radar_puffin', 'radar-puffin'), ('biscuit', 'biscuit')):
                    gate.check_assets(out, out / f'libreecho-{slug}-bundle.manifest', target=target)
            # Legacy Radar aliases are byte copies of the Radar-qualified outputs.
            self.assertEqual((out / 'libreecho-install.zip').read_bytes(), (out / 'libreecho-radar-puffin-install.zip').read_bytes())
            evidence = os.environ.get('LIBREECHO_BOOTSTRAP_EVIDENCE')
            if evidence:
                Path(evidence).write_text(json.dumps({'release_tag': values['release_tag'], 'assets': sorted(names),
                    'sha256': {n: sha(out / n) for n in sorted(names)}}, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
