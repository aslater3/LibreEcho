"""Host-only shared-contract tests; no USB, release, or pointer mutations."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from nacl.signing import SigningKey

from build.ci.amonet_pins import amonet_record

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / 'build/ci'
sys.path.insert(0, str(CI))
from target_registry import (KNOWN_TARGETS, asset_prefix, contract_target, descriptor_sha256,
    load_target, parse_targets, platform_target_args, target_from_product, validate_descriptor)
from ota_v2_product import ContractError, validate_control_tar, validate_plan, validate_inventory, validate_stable_publisher
from release_route import route
from combined_release import assert_parity
from sign_ota_candidate import validate_handoff, create_handoff, invoke_platform_signer, HandoffError
from build.tests.test_prepare_dev_release import fixture as dev_fixture
from build.tests.test_release_packaging import fixture as stable_fixture
from build.tests.test_ota_v2_complete_gate import contract as v2_fixture, control_raw


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

INSTALLER = module('multi_target_installer', ROOT / 'tools/libreecho-install.py')
TWRP = module('multi_target_twrp', CI / 'prepare-twrp-installs.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_kv(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def write_kv(path, data):
    path.write_text(''.join(f'{key}={value}\n' for key, value in data.items()))


def bind_target(run, target, channel, key=None):
    for name in ('zImage', 'System.map', 'kernel.config', 'libreecho-radar-puffin.dtb'):
        (run / name).write_bytes(('host parity fixture: ' + name).encode())
    candidate = read_kv(run / 'CURRENT.candidate')
    candidate.update(board=target, target_descriptor_sha256=descriptor_sha256(target))
    image = json.loads((run / 'manifest.json').read_text())
    image['board'] = target
    (run / 'manifest.json').write_text(json.dumps(image))
    signing = key or SigningKey.generate()
    (run / 'ota-public-key.hex').write_text(signing.verify_key.encode().hex() + '\n')
    raw = ('format=libreecho-ota-v1\nmanifest_version=1\nboard=' + target +
           '\nsoc=mt8163\narchitecture=armv7\nimage_profile=ota\nversion=fixture-v1\n' +
           'service_profile=production\nfeature_policy=community-noncommercial\nupdate_channel=' + channel + '\n' +
           'boot_filename=boot.img\nboot_size=' + str((run / 'boot.img').stat().st_size) +
           '\nboot_sha256=' + sha(run / 'boot.img') + '\n').encode()
    ota = run / 'libreecho-run.ota.tar'
    with tarfile.open(ota, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in [('manifest', raw), ('manifest.sig', signing.sign(raw).signature.hex().encode() + b'\n'), ('boot.img', (run / 'boot.img').read_bytes())]:
            record = tarfile.TarInfo(name); record.size = len(data); record.mode = 0o644
            archive.addfile(record, io.BytesIO(data))
    candidate.update(ota_signing_mode='local', ota_bundle=str(ota), ota_bundle_sha256=sha(ota))
    write_kv(run / 'CURRENT.candidate', candidate)
    return signing


def prepare(script, artifact, output, product=None, extra=()):
    cmd = [sys.executable, str(script), '--artifact-root', str(artifact), '--product-commit', '1' * 40, '--output-dir', str(output)]
    if product:
        cmd += ['--product-root', str(product), '--release-version', '0.14.0', '--release-notes', 'release/radar-puffin-v0.14.0.md',
                ]
        # The base generator on origin/release/0.14.0 predates the pinned-archive
        # record and still requires these flags; the current one rejects none but
        # no longer declares them.
        if '--amonet-repository' in Path(script).read_text(encoding='utf-8'):
            cmd += ['--amonet-repository', 'https://github.com/aslater3/amonet-k32', '--amonet-tag', 'v1.0.0',
                    '--amonet-commit', 'dfefe52f0eed7296012707cfff1f753b0ea33257']
    return subprocess.run(cmd + list(extra), capture_output=True, text=True, timeout=180)


class DescriptorTests(unittest.TestCase):
    def test_valid_descriptors_and_canonical_digest(self):
        for target in KNOWN_TARGETS:
            data = load_target(target)
            self.assertEqual(data['target_id'], target)
            self.assertEqual(descriptor_sha256(target), hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        radar, biscuit = map(load_target, KNOWN_TARGETS)
        for field in ('kernel', 'soc', 'arch', 'features', 'layout_profiles'):
            self.assertEqual(radar[field], biscuit[field])
        # Operator decision (0.14.0): both targets are hardware-accepted.
        self.assertIs(radar['hardware_accepted'], True)
        self.assertIs(biscuit['hardware_accepted'], True)

    def test_unknown_or_duplicate_targets_refused(self):
        for value in ('unknown', '', 'radar_puffin,radar_puffin', 'radar_puffin, biscuit', 'radar_puffin,../biscuit'):
            with self.subTest(value=value), self.assertRaises(ValueError): parse_targets(value)

    def test_bad_descriptor_fields_fail_closed(self):
        radar = load_target()
        for mutate in (lambda d: d.pop('soc'), lambda d: d.update(extra=1), lambda d: d.update(arch='arm64'),
                       lambda d: d['kernel'].update(dtb='biscuit.dtb'), lambda d: d.update(fastboot_products=['BISCUIT']),
                       lambda d: d['platform'].update(hw_profile='biscuit@0'), lambda d: d.update(features=[])):
            changed = copy.deepcopy(radar); mutate(changed)
            with self.assertRaises(ValueError): validate_descriptor(changed)
        # The acceptance field must be a real boolean, never a truthy string.
        for target in KNOWN_TARGETS:
            for bad in ('true', 1, None):
                changed = load_target(target); changed['hardware_accepted'] = bad
                with self.subTest(target=target, value=bad), self.assertRaises(ValueError):
                    validate_descriptor(changed)

    def test_candidate_image_and_descriptor_are_bound(self):
        for target in KNOWN_TARGETS:
            candidate = {'board': target, 'target_descriptor_sha256': descriptor_sha256(target)}
            self.assertEqual(contract_target(candidate, {'board': target}), target)
            with self.assertRaises(ValueError): contract_target(candidate, {'board': next(t for t in KNOWN_TARGETS if t != target)})
            with self.assertRaises(ValueError): contract_target({**candidate, 'target_descriptor_sha256': '0' * 64}, {'board': target})

    def test_radar_names_and_tags_unchanged(self):
        for tag in ('radar-puffin-v0.14.0', 'radar-puffin-build-' + '1' * 7 + '-' + '2' * 16 + '-' + '3' * 16):
            self.assertEqual(asset_prefix(tag), 'libreecho-' + tag)
            self.assertEqual(asset_prefix(tag, 'biscuit'), 'libreecho-biscuit-' + tag.removeprefix('radar-puffin-'))

    def test_platform_feature_detection_and_radar_only_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / 'old.py'
            tool.write_text("print('usage: old.py --output PATH')\n")
            self.assertEqual(platform_target_args(tool, 'radar_puffin'), [])
            with self.assertRaisesRegex(ValueError, 'lacks --target'): platform_target_args(tool, 'biscuit')
            tool.write_text("print('usage: --target ID --target-descriptor-sha256 HASH')\n")
            for target in KNOWN_TARGETS:
                self.assertEqual(platform_target_args(tool, target, descriptor_sha256(target)), ['--target', target, '--target-descriptor-sha256', descriptor_sha256(target)])

    def test_installer_embedded_mapping_matches_descriptors(self):
        expected = {t: {k: load_target(t)[k] for k in ('release_slug', 'fastboot_products')} for t in KNOWN_TARGETS}
        self.assertEqual(INSTALLER.TARGET_INDEX, expected)
        for product, target in [('RADAR', 'radar_puffin'), ('BISCUIT', 'biscuit')]:
            self.assertEqual(target_from_product(product), target)
            with mock.patch.object(INSTALLER, '_run_command', return_value=subprocess.CompletedProcess([], 0, '', 'product: ' + product)):
                self.assertEqual(INSTALLER.verify_fastboot_product('mock', 'fixture'), target)
                self.assertEqual(INSTALLER.verify_fastboot_product('mock', 'fixture', 'radar_puffin', 'radar_puffin'), 'radar_puffin')
                if target == 'biscuit':
                    with self.assertRaises(INSTALLER.InstallerError): INSTALLER.verify_fastboot_product('mock', 'fixture', 'radar_puffin')

    def test_old_expdb_erase_guard_and_brom_path_are_gone(self):
        for name in ('LEGACY_EXPDB_ERASE_CLOSURES', 'require_legacy_expdb_erase', 'download_amonet',
                     'verify_amonet_root', 'run_amonet_with_progress', 'brom_permission_preflight', 'AMONET_COMMIT'):
            self.assertFalse(hasattr(INSTALLER, name), name)

    def test_embedded_pins_match_release_pin_file(self):
        from build.ci.amonet_pins import load_pins
        pins = load_pins()['targets']
        self.assertEqual(set(INSTALLER.AMONET_PINS), set(pins))
        for target in pins:
            self.assertEqual(INSTALLER.AMONET_PINS[target], pins[target], target)

    def test_biscuit_accepts_any_lk_build_and_uses_reviewed_payload_when_mapped(self):
        ok = INSTALLER.select_amonet_payload('biscuit', '63cb91b-20221007_072309')
        self.assertEqual(ok['payload'], 'fastbrick-20221007.img')
        for build in ('59779ca-20220524_183401', '63cb91b-20221007_072310', 'anything-else'):
            chosen = INSTALLER.select_amonet_payload('biscuit', build)
            self.assertEqual(chosen['payload'], 'fastbrick.img')
            self.assertEqual(chosen['build'], build)
        with self.assertRaisesRegex(INSTALLER.InstallerError, 'LK build is unknown'):
            INSTALLER.select_amonet_payload('biscuit', '')

    def test_radar_accepts_both_pinned_lk_builds(self):
        for build, name in (('59779ca-20220524_183401', 'fastbrick-20220524.img'), ('63cb91b-20221007_072309', 'fastbrick.img'), ('63cb91b-20221007_073612', 'fastbrick.img')):
            self.assertEqual(INSTALLER.select_amonet_payload('radar_puffin', build)['payload'], name)

    def test_brick_stops_on_emmc_ro_and_device_mismatch_without_retry(self):
        for text, pattern in (('eMMC-RO', 'read-only'), ('Device mismatch', 'Device mismatch')):
            with tempfile.TemporaryDirectory() as tmp:
                payload = Path(tmp) / 'fastbrick.img'; payload.write_bytes(b'x')
                result = subprocess.CompletedProcess([], 1, stdout=text, stderr='')
                with mock.patch.object(INSTALLER.subprocess, 'run', return_value=result) as run, \
                     mock.patch.object(INSTALLER, '_safe_regular'):
                    with self.assertRaisesRegex(INSTALLER.InstallerError, pattern):
                        INSTALLER.brick_fastboot_payload('fastboot', 'SER', payload, 60)
                self.assertEqual(run.call_count, 1)

    def test_one_shot_locked_device_without_zip_stops_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / 'cache' / 'radar-puffin-v0.14.0' / 'bundle.tar'
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b'fixture-bundle')
            boot = bundle.parent / 'b.img'
            boot.write_bytes(b'fixture-boot')
            manifest = {'board': 'radar_puffin', 'boot': {'name': 'b.img', 'sha256': hashlib.sha256(b'fixture-boot').hexdigest()}}
            with mock.patch.object(INSTALLER, '_prepare', return_value=(manifest, bundle)), \
                 mock.patch.object(INSTALLER, 'require_host_commands'), \
                 mock.patch.object(INSTALLER, 'prepare_fastboot_tools', return_value='fixture-fastboot'), \
                 mock.patch.object(INSTALLER, 'adb_forward_command', return_value=[]), \
                 mock.patch.object(INSTALLER, 'fastboot_devices', return_value=['SER']), \
                 mock.patch.object(INSTALLER, 'select_fastboot_serial', return_value='SER'), \
                 mock.patch.object(INSTALLER, 'verify_fastboot_product', return_value='radar_puffin'), \
                 mock.patch.object(INSTALLER, 'fastboot_getvar', return_value='false'), \
                 mock.patch.object(INSTALLER, 'validate_public_boot_image'), \
                 mock.patch.object(INSTALLER, '_run_command',
                                   return_value=subprocess.CompletedProcess([], 1, '', 'FAILunknown command\n')) as command:
                with self.assertRaisesRegex(INSTALLER.InstallerError, 'requires --amonet-zip'):
                    INSTALLER.one_shot(root, None, release_tag='radar-puffin-v0.14.0', cache_root=root / 'cache',
                        state_root=root / 'state', target='radar_puffin', execute_hardware=True)
                # Only the read-only Kaeru identity probe may run before the refusal.
                self.assertEqual([call.args[0][-2:] for call in command.call_args_list], [['oem', 'kaeru-version']])

    def test_route_accepts_targets_but_preserves_product_tag(self):
        request = {'schema': 'libreecho-release-request-v1', 'purpose': 'prd', 'publish': True, 'channel': 'stable', 'version': '0.14.0',
                   'release_tag': 'radar-puffin-v0.14.0', 'release_notes': 'release/radar-puffin-v0.14.0.md'}
        request.update(ssh_enabled='disabled')
        original = route(request, 'release/0.14.0', 'workflow_dispatch')
        for targets in (['radar_puffin'], ['biscuit'], list(KNOWN_TARGETS)):
            self.assertEqual(route({**request, 'targets': targets}, 'release/0.14.0', 'workflow_dispatch'), original)
        with self.assertRaises(ValueError): route({**request, 'targets': ['biscuit'], 'board': 'radar_puffin'}, 'release/0.14.0', 'workflow_dispatch')


class ReleaseTests(unittest.TestCase):
    def test_prepare_both_targets_development(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / 'combined'; artifact.mkdir()
            for target in KNOWN_TARGETS:
                run, _ = dev_fixture(root / target)
                bind_target(run, target, 'dev')
                shutil.move(str(run), artifact / target)
            (artifact / 'release-request.json').write_text(json.dumps({'targets': list(KNOWN_TARGETS)}))
            result = prepare(CI / 'prepare-dev-release.py', artifact, root / 'out')
            self.assertEqual(result.returncode, 0, result.stderr)
            tag = dict(l.split('=', 1) for l in result.stdout.splitlines() if '=' in l)['release_tag']
            self.assertTrue(tag.startswith('radar-puffin-build-'))
            for target in KNOWN_TARGETS:
                prefix = asset_prefix(tag, target)
                data = json.loads((root / 'out' / (prefix + '-build.json')).read_text())
                self.assertEqual((data['board'], data['target_descriptor_sha256']), (target, descriptor_sha256(target)))
                with tarfile.open(root / 'out' / (prefix + '-initial-install.tar')) as archive:
                    self.assertEqual(json.load(archive.extractfile('manifest.json'))['board'], target)
            self.assertEqual(len(list((root / 'out').iterdir())), int(dict(l.split('=', 1) for l in result.stdout.splitlines() if '=' in l)['asset_count']))

    def stable_combined(self, root):
        artifact = root / 'combined'; artifact.mkdir()
        signing = SigningKey.generate()
        for target in KNOWN_TARGETS:
            single, product = stable_fixture(root / target)
            run = single / 'run'
            (run / 'release-request.json').unlink()
            bind_target(run, target, 'stable', signing)
            shutil.move(str(run), artifact / target)
        (artifact / 'release-request.json').write_text(json.dumps({'schema': 'libreecho-release-request-v1', 'targets': list(KNOWN_TARGETS),
            'channel': 'stable', 'version': '0.14.0', 'release_tag': 'radar-puffin-v0.14.0', 'release_notes': 'release/radar-puffin-v0.14.0.md'}))
        result = prepare(CI / 'prepare-stable-release.py', artifact, root / 'out', product)
        self.assertEqual(result.returncode, 0, result.stderr)
        return artifact, root / 'out'

    def test_combined_stable_and_per_target_publisher_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, out = self.stable_combined(Path(tmp))
            self.assertEqual(validate_stable_publisher(out, 'radar-puffin-v0.14.0'), {p.name for p in out.iterdir()})
            for target in KNOWN_TARGETS:
                prefix = asset_prefix('radar-puffin-v0.14.0', target)
                self.assertEqual(json.loads((out / (prefix + '-build.json')).read_text())['board'], target)
                self.assertTrue((out / f'libreecho-{load_target(target)["release_slug"]}-stable.ota.tar').is_file())
            (out / 'unexpected').write_bytes(b'bad')
            with self.assertRaises(ValueError): validate_stable_publisher(out, 'radar-puffin-v0.14.0')

    def test_cross_target_candidate_refused_before_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, product = stable_fixture(root)
            bind_target(artifact / 'run', 'biscuit', 'stable')
            image = json.loads((artifact / 'run/manifest.json').read_text()); image['board'] = 'radar_puffin'
            (artifact / 'run/manifest.json').write_text(json.dumps(image))
            result = prepare(CI / 'prepare-stable-release.py', artifact, root / 'out', product, ['--target', 'biscuit'])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('target mismatch', result.stderr)
            self.assertFalse((root / 'out').exists())

    def test_parity_gate_refuses_different_shared_feature_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = {t: dev_fixture(root / t)[0] for t in KNOWN_TARGETS}
            for target, run in runs.items(): bind_target(run, target, 'dev')
            assert_parity(runs)
            (runs['biscuit'] / 'features/tts.squashfs').write_bytes(b'different')
            with self.assertRaisesRegex(ValueError, 'code parity'): assert_parity(runs)
            (runs['biscuit'] / 'features/tts.squashfs').write_bytes((runs['radar_puffin'] / 'features/tts.squashfs').read_bytes())
            (runs['biscuit'] / 'zImage').unlink()
            with self.assertRaisesRegex(ValueError, 'missing shared input'): assert_parity(runs)

    def test_legacy_radar_metadata_and_names_are_byte_identical_to_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, product = stable_fixture(root)
            old = root / 'prepare-stable-release.py'
            old.write_bytes(subprocess.check_output(['git', 'show', 'origin/release/0.14.0:build/ci/prepare-stable-release.py'], cwd=ROOT, timeout=30))
            with mock.patch.dict(os.environ, {'PYTHONPATH': str(CI)}):
                before = prepare(old, artifact, root / 'before', product)
                after = prepare(CI / 'prepare-stable-release.py', artifact, root / 'after', product)
            self.assertEqual(before.returncode, 0, before.stderr)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertEqual({p.name for p in (root / 'before').iterdir()}, {p.name for p in (root / 'after').iterdir()})
            # Intended changes versus the base generator: build.json's
            # hardware_accepted (the target descriptor now sets it) and the install
            # manifest's `amonet` record, which now identifies the pinned archive
            # instead of a repository/tag/commit. initial-install.tar carries that
            # manifest, so its hash, and the build.json and SHA256SUMS lines that
            # cite it, change with it. Everything else must stay byte-identical.
            build_name = next(p.name for p in (root / 'before').iterdir() if p.name.endswith('-build.json'))
            sums_name = next(p.name for p in (root / 'before').iterdir() if p.name.endswith('-SHA256SUMS'))
            tar_name = next(p.name for p in (root / 'before').iterdir() if p.name.endswith('-initial-install.tar'))
            prior = json.loads((root / 'before' / build_name).read_text())
            current = json.loads((root / 'after' / build_name).read_text())
            # The base generator may predate the descriptor (False) or already
            # copy it (True); the current generator must copy the descriptor.
            self.assertIsInstance(prior.pop('hardware_accepted'), bool)
            self.assertIs(current.pop('hardware_accepted'), True)
            for record in (prior, current):
                record['artifacts'] = [a for a in record['artifacts'] if a['name'] != tar_name]
            self.assertEqual(prior, current)
            def install_members(path):
                with tarfile.open(path) as archive:
                    return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
            old_members = install_members(root / 'before' / tar_name)
            new_members = install_members(root / 'after' / tar_name)
            self.assertEqual(set(old_members), set(new_members))
            old_manifest = json.loads(old_members.pop('manifest.json'))
            new_manifest = json.loads(new_members.pop('manifest.json'))
            self.assertEqual(old_manifest.pop('amonet')['repository'], 'https://github.com/aslater3/amonet-k32')
            self.assertEqual(new_manifest.pop('amonet'), amonet_record('radar_puffin'))
            self.assertEqual(old_manifest, new_manifest)
            self.assertEqual(old_members, new_members)
            prior_sums = (root / 'before' / sums_name).read_text().splitlines()
            current_sums = (root / 'after' / sums_name).read_text().splitlines()
            changed = (build_name, tar_name)
            self.assertEqual([l for l in prior_sums if not l.endswith(changed)],
                             [l for l in current_sums if not l.endswith(changed)])
            self.assertIn(hashlib.sha256((root / 'after' / build_name).read_bytes()).hexdigest() + '  ' + build_name, current_sums)
            self.assertIn(hashlib.sha256((root / 'after' / tar_name).read_bytes()).hexdigest() + '  ' + tar_name, current_sums)
            for path in (root / 'before').iterdir():
                if path.name in (build_name, sums_name, tar_name):
                    continue
                self.assertEqual(path.read_bytes(), (root / 'after' / path.name).read_bytes(), path.name)

    def test_twrp_calls_both_targets_and_keeps_radar_byte_copy_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, out = self.stable_combined(root)
            builder = artifact / 'twrp-builder/build_install_bundle.py'; builder.parent.mkdir()
            # Explicit host fixture, not a claim of Platform image execution.
            builder.write_text('')
            calls = []
            def run(command, **kwargs):
                target = command[command.index('--target') + 1]
                calls.append(target)
                dest = Path(command[command.index('--out') + 1]); dest.mkdir()
                slug = load_target(target)['release_slug']
                (dest / ('libreecho-' + slug + '-install.zip')).write_bytes(('test-zip-' + target).encode())
                (dest / ('libreecho-' + slug + '-bundle.manifest')).write_text('target=' + target + '\ndevice=' + target + '\nfastboot_products=' + ','.join(load_target(target)['fastboot_products']) + '\n')
            gate = mock.MagicMock()
            gate.TARGETS = {target: load_target(target)['release_slug'] for target in KNOWN_TARGETS}
            with mock.patch.object(TWRP, 'platform_target_args', side_effect=lambda tool, target: ['--target', target]), \
                 mock.patch.object(TWRP.subprocess, 'run', side_effect=run), \
                 mock.patch.object(TWRP.importlib.util, 'find_spec', return_value=object()), \
                 mock.patch.dict(sys.modules, {'release_completeness': gate}):
                count = TWRP.build_installs(out, artifact)
            self.assertEqual(gate.check_assets.call_count, 2)
            self.assertEqual({call.kwargs['target'] for call in gate.check_assets.call_args_list}, set(KNOWN_TARGETS))
            self.assertEqual(set(calls), set(KNOWN_TARGETS))
            self.assertEqual(count, len(list(out.iterdir())))
            for name, alias in [('libreecho-radar-puffin-install.zip', 'libreecho-install.zip'), ('libreecho-radar-puffin-bundle.manifest', 'bundle.manifest')]:
                self.assertEqual((out / name).read_bytes(), (out / alias).read_bytes())
            for target in KNOWN_TARGETS:
                self.assertTrue((out / f'libreecho-radar-puffin-v0.14.0-{load_target(target)["release_slug"]}-TWRPINSTALL-SHA256SUMS').is_file())
            validate_stable_publisher(out, 'radar-puffin-v0.14.0')


class SigningTests(unittest.TestCase):
    def test_plan_inventory_and_signed_control_bind_both_targets(self):
        for target in KNOWN_TARGETS:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                plan, inventory, assets, boot, key = v2_fixture(root)
                if target != 'radar_puffin':
                    for path in list(assets.iterdir()): path.rename(path.with_name(path.name.replace('radar-puffin', 'biscuit')))
                    plan = json.loads(json.dumps(plan).replace('radar-puffin', 'biscuit'))
                    inventory = json.loads(json.dumps(inventory).replace('radar-puffin', 'biscuit'))
                    plan['board'] = inventory['board'] = target
                records = validate_plan(plan, '0.14.0', plan['source_commit'], target)
                validate_inventory(inventory, records, '0.14.0', target)
                raw = control_raw(plan, boot).replace(b'board=radar_puffin', ('board=' + target).encode())
                # v2 canonical transaction ID is byte-bound to boot and records.
                from build.tests.test_ota_v2_complete_gate import write_control
                bundle = root / 'signed.ota.tar'; write_control(bundle, raw, key, boot)
                # control_raw fixture's txn ID is intentionally invalid; bind it here.
                material = '0.14.0' + sha(boot) + json.dumps(records, sort_keys=True, separators=(',', ':'))
                raw = raw.replace(b'txn-' + b'1' * 24, ('txn-' + hashlib.sha256(material.encode()).hexdigest()[:24]).encode())
                write_control(bundle, raw, key, boot)
                # Exercise Product's complete parser independently of an older
                # Platform checkout; Platform parser parity is cross-repo evidence.
                with mock.patch('ota_v2_product._canonical_parser', return_value=None):
                    validate_control_tar(bundle, root / 'public.hex', 'v2', '0.14.0', feature_plan=plan, feature_inventory=inventory,
                        feature_asset_dir=assets, boot_path=boot, expected_channel='stable', expected_target=target,
                        expected_key_sha256=sha(root / 'public.hex'))
                with self.assertRaises(ContractError): validate_control_tar(bundle, root / 'public.hex', 'v2', '0.14.0', expected_target=next(t for t in KNOWN_TARGETS if t != target))

    def test_real_handoff_creation_and_target_mismatch_refusal(self):
        platform = Path(os.environ.get('LIBREECHO_PLATFORM_SRC', str(ROOT.parent / 'platform')))
        for target in KNOWN_TARGETS:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp)
                plan, inventory, assets, boot, key = v2_fixture(run)
                if target != 'radar_puffin':
                    for path in list(assets.iterdir()): path.rename(path.with_name(path.name.replace('radar-puffin', 'biscuit')))
                    plan = json.loads(json.dumps(plan).replace('radar-puffin', 'biscuit'))
                    inventory = json.loads(json.dumps(inventory).replace('radar-puffin', 'biscuit'))
                    plan['board'] = inventory['board'] = target
                (run / 'feature-plan.json').write_text(json.dumps(plan))
                (run / 'feature-assets.json').write_text(json.dumps(inventory))
                (run / 'manifest.json').write_text(json.dumps({'board': target}))
                (run / 'ota-public-key.hex').write_text(key.verify_key.encode().hex() + '\n')
                (run / 'features').mkdir()
                for feature in load_target(target)['features']:
                    (run / 'features' / (feature + '.squashfs')).write_bytes((feature + ' fixture payload').encode())
                    (run / 'features' / (feature + '.manifest.json')).write_text(json.dumps({'feature_id': feature}))
                baseline = run / 'base-catalog.json'
                baseline.write_text(json.dumps({'board': target, 'features': {}}))
                handoff = run / 'handoff.json'
                with mock.patch.dict(os.environ, {'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': sha(run / 'ota-public-key.hex')}):
                    create_handoff(run, '0.14.0', plan['source_commit'], 'stable', baseline, sha(baseline), handoff,
                        platform, platform / 'tools/mt8163-arm32/ota/make_ota_bundle.py', target)
                data = validate_handoff(handoff)
                self.assertEqual(data['board'], target)
                self.assertEqual(data['target_descriptor_sha256'], descriptor_sha256(target))
                self.assertTrue(any(r['name'] == target + '.json' for r in data['product_runtime']['product_tools']))
                data['board'] = next(t for t in KNOWN_TARGETS if t != target)
                data['target_descriptor_sha256'] = descriptor_sha256(data['board'])
                handoff.write_text(json.dumps(data))
                with self.assertRaisesRegex(HandoffError, 'baseline target mismatch'): validate_handoff(handoff)

    def test_signer_argument_vector_contains_selected_target(self):
        with mock.patch('sign_ota_candidate.platform_target_args', side_effect=lambda tool, target: ['--target', target]), mock.patch('sign_ota_candidate.subprocess.run') as run:
            for target in KNOWN_TARGETS:
                command = invoke_platform_signer(Path('fixture.py'), boot_image=Path('boot'), build_manifest=Path('manifest'), signing_key=Path('key'),
                    public_key=Path('public'), output=Path('out'), plan=Path('plan'), release='0.14.0', update_channel='dev', target=target)
                self.assertEqual(command[-2:], ['--target', target])
                self.assertFalse(run.call_args.kwargs.get('shell', False))


if __name__ == '__main__': unittest.main()
