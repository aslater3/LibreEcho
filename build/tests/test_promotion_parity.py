import importlib.util
import json
import lzma
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'build' / 'ci' / 'promotion_parity.py'
spec = importlib.util.spec_from_file_location('promotion_parity', SCRIPT)
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

PAGE = 2048


def newc(files):
    out = bytearray()
    entries = [('.', 0o40755, b'')] + [(n, 0o100644, d) for n, d in files.items()]
    for ino, (name, mode, data) in enumerate(entries + [('TRAILER!!!', 0, b'')], 1):
        raw = name.encode() + b'\0'
        fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw), 0]
        out += b'070701' + b''.join(b'%08X' % f for f in fields) + raw
        out += bytes(-len(out) % 4)
        out += data
        out += bytes(-len(out) % 4)
    return bytes(out)


def boot(kernel=b'K' * 5000, files=None, cmdline=b'console=ttyS0', boot_id=b'\x11' * 20):
    files = {'init': b'#!/bin/sh\n', 'sbin/adbd': b'adbd-dev',
             'etc/libreecho/update-channel': b'dev\n', **(files or {})}
    ramdisk = lzma.compress(newc(files), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32)
    header = bytearray(PAGE)
    header[:8] = b'ANDROID!'
    header[8:40] = struct.pack('<8I', len(kernel), 0x40008000, len(ramdisk), 0x45000000,
                               0, 0, 0x44000100, PAGE)
    header[64:64 + len(cmdline)] = cmdline
    header[576:576 + len(boot_id)] = boot_id

    def pad(b):
        return b + bytes(-len(b) % PAGE)

    return bytes(header) + pad(kernel) + pad(ramdisk)


def feature(fid, sha='a' * 64, daemon='d' * 64):
    return {'feature_id': fid, 'asset': f'base-{fid}-{sha}.payload.squashfs', 'sha256': sha,
            'size': 10, 'manifest_asset': f'base-{fid}.manifest.json', 'manifest_sha256': 'b' * 64,
            'manifest_size': 5, 'daemon_path': f'usr/local/sbin/{fid}', 'daemon_sha256': daemon}


class BootParityTests(unittest.TestCase):
    def test_channel_files_are_the_only_allowed_ramdisk_difference(self):
        stable = boot(files={'sbin/adbd': b'adbd-stable',
                             'etc/libreecho/update-channel': b'stable\n'},
                      boot_id=b'\x22' * 20)
        problems, channel = pp.compare_boot(boot(), stable)
        self.assertEqual(problems, [])
        self.assertEqual(channel, ['etc/libreecho/update-channel', 'sbin/adbd'])

    def test_kernel_rebuild_fails(self):
        problems, _ = pp.compare_boot(boot(), boot(kernel=b'K' * 4999 + b'X'))
        self.assertIn('boot kernel differs from the validated dev build', problems)

    def test_command_line_change_fails(self):
        problems, _ = pp.compare_boot(boot(), boot(cmdline=b'console=ttyS1'))
        self.assertIn('boot header differs from the validated dev build', problems)

    def test_other_ramdisk_changes_fail(self):
        problems, _ = pp.compare_boot(boot(), boot(files={'init': b'#!/bin/sh\necho\n'}))
        self.assertEqual(problems, ['ramdisk changes init'])
        problems, _ = pp.compare_boot(boot(), boot(files={'usr/local/sbin/x': b'x'}))
        self.assertEqual(problems, ['ramdisk adds usr/local/sbin/x'])

    def test_channel_file_may_not_appear_or_vanish(self):
        problems, _ = pp.compare_boot(boot(), boot(files={'etc/libreecho/adb-transport': b'x'}))
        self.assertEqual(problems, ['ramdisk adds etc/libreecho/adb-transport'])

    def test_rejects_non_boot_image(self):
        with self.assertRaises(pp.ParityError):
            pp.compare_boot(b'\0' * 4096, boot())


class FeatureParityTests(unittest.TestCase):
    def test_identical_plans_pass(self):
        plan = {'features': [feature(f) for f in pp.FEATURES]}
        self.assertEqual(pp.compare_features(plan, json.loads(json.dumps(plan))), [])

    def test_rebuilt_daemon_fails(self):
        dev = {'features': [feature(f) for f in pp.FEATURES]}
        stable = {'features': [feature(f, daemon='e' * 64 if f == 'tts' else 'd' * 64)
                               for f in pp.FEATURES]}
        self.assertEqual(pp.compare_features(dev, stable),
                         ['feature tts daemon_sha256 differs from the validated dev build'])

    def test_missing_feature_fails(self):
        dev = {'features': [feature(f) for f in pp.FEATURES]}
        stable = {'features': [feature(f) for f in pp.FEATURES if f != 'stt']}
        self.assertTrue(any('feature set differs' in p for p in pp.compare_features(dev, stable)))


class ProductDiffTests(unittest.TestCase):
    def test_release_notes_and_docs_are_allowed(self):
        self.assertEqual(pp.product_diff_is_docs('a', 'b', [
            'release/radar-puffin-v0.14.3.md', 'CHANGELOG.md', 'docs/RELEASING.md']), [])

    def test_build_changes_are_not(self):
        self.assertEqual(pp.product_diff_is_docs('a', 'b', ['CHANGELOG.md', 'build/build.sh']),
                         ['build/build.sh'])
        self.assertEqual(pp.product_diff_is_docs('a', 'b', ['release/targets/radar.json']),
                         ['release/targets/radar.json'])


class CheckCommandTests(unittest.TestCase):
    def test_check_compares_each_run_with_its_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            plan = {'board': 'biscuit', 'features': [feature(f) for f in pp.FEATURES]}
            dev = tmp / 'dev' / 'biscuit'
            dev.mkdir(parents=True)
            (dev / 'dev-feature-plan.json').write_text(json.dumps(plan))
            (dev / 'boot.img').write_bytes(boot())
            run = tmp / 'run'
            run.mkdir()
            (run / 'feature-plan.json').write_text(json.dumps(plan))
            (run / 'boot.img').write_bytes(boot(files={'etc/libreecho/update-channel': b'stable\n'}))
            self.assertEqual(pp.main(['check', '--dev', str(tmp / 'dev'), str(run)]), 0)
            (run / 'boot.img').write_bytes(boot(kernel=b'Z' * 5000))
            self.assertEqual(pp.main(['check', '--dev', str(tmp / 'dev'), str(run)]), 1)
            other = dict(plan, board='radar_puffin')
            (run / 'feature-plan.json').write_text(json.dumps(other))
            self.assertEqual(pp.main(['check', '--dev', str(tmp / 'dev'), str(run)]), 1)


class FetchCommandTests(unittest.TestCase):
    TAG = 'radar-puffin-build-e026c3a-a0bb0b3d58b0fba9-29ee26d6b839c047'
    SOURCES = {'product': 'p' * 40, 'platform': 'q' * 40, 'linux': 'r' * 40, 'ui': 's' * 40}

    def fake_fetcher(self, build_overrides=None, boot_bytes=None):
        real = pp.load_fetcher()
        image = boot_bytes or boot()

        def resolve(tag, root, target):
            root.mkdir(parents=True)
            features = {}
            for fid in pp.FEATURES:
                (root / f'{fid}.squashfs').write_bytes(fid.encode())
                (root / f'{fid}.manifest.json').write_text('{}')
                features[fid] = {
                    'payload': {'path': str(root / f'{fid}.squashfs'), 'sha256': fid * 2, 'size': 1},
                    'manifest': {'path': str(root / f'{fid}.manifest.json'), 'sha256': 'm', 'size': 2},
                }
            build = {'channel': 'dev', 'signed': True, 'sources': dict(self.SOURCES),
                     'feature_plan': {'board': target, 'boot_sha256': pp.hashlib.sha256(image).hexdigest(),
                                      'features': []}}
            build.update(build_overrides or {})
            (root / 'build.json').write_text(json.dumps(build))
            prefix = real.asset_prefix(tag, target)
            (root / 'SHA256SUMS').write_text(
                f'{pp.hashlib.sha256(image).hexdigest()}  {prefix}-boot.img\n')
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'features': features}))
            return catalog

        def download(url, dest, limit):
            dest.write_bytes(image)

        return SimpleNamespace(DEV_TAG=real.DEV_TAG, asset_prefix=real.asset_prefix,
                               resolve=resolve, download=download)

    def run_fetch(self, fetcher, **sources):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / 'env'
            args = ['fetch', '--tag', self.TAG, '--targets', 'radar_puffin,biscuit',
                    '--output', str(Path(tmp) / 'dev'), '--github-env', str(env)]
            for name, value in {**self.SOURCES, **sources}.items():
                args += ['--' + name, value]
            with patch.object(pp, 'load_fetcher', return_value=fetcher):
                rc = pp.main(args)
            return rc, env.read_text() if env.exists() else ''

    def test_emits_overrides_for_every_feature(self):
        rc, env = self.run_fetch(self.fake_fetcher())
        self.assertEqual(rc, 0)
        for name in ('AIRPLAY', 'TTS', 'WAKEWORD', 'STT', 'ASSISTANT'):
            self.assertIn(f'LIBREECHO_{name}_PAYLOAD_OVERRIDE=', env)
            self.assertIn(f'LIBREECHO_{name}_MANIFEST_OVERRIDE=', env)

    def test_rejects_different_platform_commit(self):
        rc, env = self.run_fetch(self.fake_fetcher(), platform='x' * 40)
        self.assertEqual((rc, env), (1, ''))

    def test_rejects_unsigned_or_stable_source(self):
        self.assertEqual(self.run_fetch(self.fake_fetcher({'signed': False}))[0], 1)
        self.assertEqual(self.run_fetch(self.fake_fetcher({'channel': 'stable'}))[0], 1)

    def test_product_may_move_only_by_docs(self):
        with patch.object(pp, 'github_compare', return_value=['release/radar-puffin-v0.14.3.md']):
            self.assertEqual(self.run_fetch(self.fake_fetcher(), product='t' * 40)[0], 0)
        with patch.object(pp, 'github_compare', return_value=['build/build.sh']):
            self.assertEqual(self.run_fetch(self.fake_fetcher(), product='t' * 40)[0], 1)

    def test_rejects_stable_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ['fetch', '--tag', 'radar-puffin-v0.14.3', '--targets', 'radar_puffin',
                    '--output', str(Path(tmp) / 'dev')]
            for name, value in self.SOURCES.items():
                args += ['--' + name, value]
            self.assertEqual(pp.main(args), 1)


class PipelineWiringTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/build-release.yml').read_text()
        self.build = (ROOT / 'build/build.sh').read_text()

    def test_stable_requires_a_validated_dev_build(self):
        self.assertIn('validated_dev_build:', self.workflow)
        self.assertIn('stable builds require validated_dev_build', self.workflow)

    def test_adopt_before_build_and_gate_before_upload(self):
        adopt = self.workflow.index('- name: Adopt validated dev build payloads')
        build = self.workflow.index('- name: Build image and features')
        gate = self.workflow.index('- name: Gate promotion parity with the validated dev build')
        upload = self.workflow.index('- name: Upload verified dev build')
        self.assertLess(adopt, build)
        self.assertLess(build, gate)
        self.assertLess(gate, upload)

    def test_build_adopts_every_feature_payload(self):
        for name in ('AIRPLAY', 'TTS', 'WAKEWORD', 'STT', 'ASSISTANT'):
            self.assertIn(f'LIBREECHO_{name}_PAYLOAD_OVERRIDE', self.build)
        for fid in ('airplay2', 'tts', 'wakeword', 'stt', 'assistant'):
            self.assertIn(f'adopt_feature_payload {fid} ', self.build)

    def test_kernel_build_identity_is_pinned(self):
        for var in ('KBUILD_BUILD_USER=', 'KBUILD_BUILD_HOST=', 'KBUILD_BUILD_VERSION=',
                    'KBUILD_BUILD_TIMESTAMP='):
            self.assertIn(var, self.build)


if __name__ == '__main__':
    unittest.main()
