"""The lock is generated from acquired archives, not guessed package versions."""
from pathlib import Path
import importlib.util
import hashlib
import json
import tempfile
import unittest
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'build/ci/mdns_packages.py'
RUNTIME_SCRIPT = Path(__file__).resolve().parents[1] / 'build/ci/mdns_runtime.py'
LOCK = Path(__file__).resolve().parents[1] / 'build/inputs/mdns-packages.lock.json'
OFFERS = Path(__file__).resolve().parents[1] / 'build/inputs/mdns-source-offers.json'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packages_module():
    return load('mdns_packages', SCRIPT)


def runtime_module():
    return load('mdns_runtime', RUNTIME_SCRIPT)


def write_archive_fixture(root, entries=(('avahi-daemon', '0.8-13ubuntu6.2', 'avahi', 'armhf'),
                                        ('adduser', '3.137ubuntu1', 'adduser', 'all'))):
    archives = root / 'archives'
    archives.mkdir()
    records = []
    for package, version, source, architecture in entries:
        name = '%s_%s_%s.deb' % (package, version, architecture)
        data = ('fixture-archive-%s' % package).encode()
        (archives / name).write_bytes(data)
        records.append({'file': name, 'sha256': hashlib.sha256(data).hexdigest(),
                        'package': package, 'version': version,
                        'architecture': architecture, 'source': source})
    lock = root / 'lock.json'
    lock.write_text(json.dumps({'schema': 'libreecho-mdns-packages/v1', 'packages': records},
                               sort_keys=True, indent=2) + '\n')
    return archives, lock, records


def write_offers(root, records):
    offers = {}
    for record in records:
        offers[record['source']] = record['version']
    document = {
        'schema': 'libreecho-mdns-source-offers/v1',
        'archive': 'https://ports.ubuntu.com/ubuntu-ports',
        'offers': [{'source': source, 'version': version,
                    'source_offer': 'https://launchpad.net/ubuntu/+source/%s' % source}
                   for source, version in sorted(offers.items())],
    }
    path = root / 'offers.json'
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + '\n')
    return path


def write_runtime(root, lock, records, missing_notice=None, packages_bytes=None):
    runtime = root / 'runtime'
    runtime.mkdir(parents=True)
    (runtime / 'packages.json').write_bytes(packages_bytes if packages_bytes is not None
                                           else lock.read_bytes())
    files = {'usr/sbin/avahi-daemon': {'mode': 493, 'sha256': 'a' * 64}}
    for record in records:
        if record['package'] == missing_notice:
            continue
        files['usr/share/licenses/libreecho-mdns/%s/copyright' % record['package']] = {
            'mode': 420, 'sha256': hashlib.sha256(record['package'].encode()).hexdigest()}
    manifest = {'schema': 'libreecho-mdns-runtime/v1', 'files': files,
                'packages_sha256': hashlib.sha256((runtime / 'packages.json').read_bytes()).hexdigest(),
                'source_offer_verified': False}
    (runtime / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return runtime, hashlib.sha256((runtime / 'manifest.json').read_bytes()).hexdigest()


class PackageLock(unittest.TestCase):
    def test_exact_archive_identity_and_architecture(self):
        module = packages_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'package'
            (package / 'DEBIAN').mkdir(parents=True)
            archives = root / 'archives'
            archives.mkdir()
            control = package / 'DEBIAN/control'
            control.write_text('Package: mdns-fixture\nVersion: 1.2-3\nArchitecture: armhf\nSource: source-fixture (1.2-3)\nMaintainer: Test <test@example.invalid>\nDescription: package lock test fixture\n')
            archive = archives / 'fixture.deb'
            subprocess.run(['dpkg-deb', '--build', str(package), str(archive)],
                           check=True, capture_output=True, timeout=10)
            result = module.create_lock(archives)['packages'][0]
            self.assertEqual(result['package'], 'mdns-fixture')
            self.assertEqual(result['version'], '1.2-3')
            self.assertEqual(result['source'], 'source-fixture (1.2-3)')
            self.assertEqual(result['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
            control.write_text(control.read_text().replace('Architecture: armhf', 'Architecture: amd64'))
            subprocess.run(['dpkg-deb', '--build', str(package), str(archive)],
                           check=True, capture_output=True, timeout=10)
            with self.assertRaises(ValueError):
                module.create_lock(archives)

    def test_rejects_empty_archive_set(self):
        self.assertTrue(SCRIPT.is_file(), 'shared mDNS package lock implementation missing')
        module = packages_module()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                module.create_lock(Path(root))


class AcquiredPackageProvenance(unittest.TestCase):
    """Acquisition is pinned to the lock; nothing is taken on trust."""

    def test_committed_lock_is_armhf_and_publicly_pinned(self):
        module = runtime_module()
        records = module.load_lock(LOCK)
        self.assertGreaterEqual(len(records), 1)
        for record in records:
            self.assertIn(record['architecture'], ('armhf', 'all'))
            self.assertRegex(record['sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(record['file'].startswith(record['package'] + '_'))
        # Exact versions, including epoch-qualified ones, are what apt receives.
        pins = module.apt_spec(records)
        self.assertIn('avahi-daemon=0.8-13ubuntu6.2', pins)
        self.assertTrue(all('=' in pin for pin in pins), pins)
        self.assertEqual(len(set(pins)), len(records))
        self.assertEqual(module.apt_spec(records), pins)

    def test_live_lock_and_offers_share_one_source_set(self):
        module = runtime_module()
        records = module.load_lock(LOCK)
        offers = module.load_source_offers(OFFERS, records)
        locked = {record['source'] for record in records}
        self.assertEqual({offer['source'] for offer in offers}, locked)
        # The offers file is a corresponding-source index, not a notice claim.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / 'offers.json'
            broken.write_text(json.dumps({
                'schema': 'libreecho-mdns-source-offers/v1',
                'offers': [{'source': source, 'version': '0', 'source_offer': 'https://example.invalid'}
                           for source in sorted(locked)],
            }))
            with self.assertRaises(ValueError):
                module.load_source_offers(broken, records)

    def test_archive_verification_fails_closed(self):
        module = runtime_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archives, lock, records = write_archive_fixture(root)
            verified = module.verify_archives(archives, module.load_lock(lock))
            self.assertEqual([entry['package'] for entry in verified],
                             sorted(record['package'] for record in records))
            self.assertEqual({entry['source'] for entry in verified}, {'avahi', 'adduser'})
            # A mutated archive is rejected on its exact SHA-256.
            (archives / records[0]['file']).write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                module.verify_archives(archives, module.load_lock(lock))
            # Unrecorded archives are rejected rather than silently admitted.
            (archives / records[0]['file']).write_bytes(('fixture-archive-%s' % records[0]['package']).encode())
            (archives / 'extra_1.0_armhf.deb').write_bytes(b'extra')
            with self.assertRaises(ValueError):
                module.verify_archives(archives, module.load_lock(lock))

    def test_source_map_records_exact_archive_identity(self):
        module = runtime_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archives, lock, records = write_archive_fixture(root)
            document = module.build_source_map(
                lock, module.load_lock(lock), module.verify_archives(archives, module.load_lock(lock)))
            self.assertEqual(document['schema'], 'libreecho-mdns-runtime-source-map/v1')
            self.assertEqual(document['lock_sha256'], hashlib.sha256(lock.read_bytes()).hexdigest())
            recorded = {entry['archive']: entry for entry in document['packages']}
            self.assertEqual(len(recorded), len(records))
            for record in records:
                entry = recorded[record['file']]
                self.assertEqual(entry['sha256'], record['sha256'])
                self.assertEqual(entry['version'], record['version'])
                self.assertEqual(entry['source'], record['source'])
                self.assertEqual(entry['architecture'], record['architecture'])
            # Acquisition provenance alone never claims source closure.
            self.assertFalse(document['source_offer_verified'])

    def test_closure_gate_fails_closed_without_evidence(self):
        module = runtime_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, lock, records = write_archive_fixture(root)
            offers = write_offers(root, records)
            for index, kwargs in enumerate((
                # A locked package with no distribution notice record.
                {'missing_notice': records[0]['package']},
                # A runtime whose package inventory is not the committed lock.
                {'packages_bytes': b'{"schema": "libreecho-mdns-packages/v1", "packages": []}'},
            )):
                with self.subTest(index=index):
                    case = root / ('case-%d' % index)
                    case.mkdir()
                    runtime, manifest_sha = write_runtime(case, lock, records, **kwargs)
                    with self.assertRaises(ValueError):
                        module.check_closure(lock, runtime, offers, manifest_sha)
            runtime, manifest_sha = write_runtime(root / 'good', lock, records)
            # A wrong manifest identity is rejected before any closure claim.
            with self.assertRaises(ValueError):
                module.check_closure(lock, runtime, offers, 'b' * 64)
            # Missing corresponding-source offers are rejected.
            empty = root / 'empty-offers.json'
            empty.write_text(json.dumps({'schema': 'libreecho-mdns-source-offers/v1', 'offers': []}))
            with self.assertRaises(ValueError):
                module.check_closure(lock, runtime, empty, manifest_sha)

    def test_closure_gate_verifies_complete_evidence(self):
        module = runtime_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / 'case'
            case.mkdir()
            _, lock, records = write_archive_fixture(case)
            offers = write_offers(case, records)
            runtime, manifest_sha = write_runtime(case, lock, records)
            document = module.check_closure(lock, runtime, offers, manifest_sha)
            self.assertTrue(document['source_offer_verified'])
            self.assertEqual(document['lock_sha256'], hashlib.sha256(lock.read_bytes()).hexdigest())
            self.assertEqual(document['runtime_manifest_sha256'], manifest_sha)
            self.assertEqual(len(document['license_records']), len(records))
            self.assertEqual({entry['source'] for entry in document['sources']},
                             {record['source'] for record in records})
            self.assertEqual(document['sources'][0]['source_offer'],
                             'https://launchpad.net/ubuntu/+source/adduser')
            # The record is only written when the closure actually holds.
            output = root / 'out/provenance.json'
            module.write_document(document, output)
            self.assertTrue(json.loads(output.read_text())['source_offer_verified'])


if __name__ == '__main__':
    unittest.main()
