#!/usr/bin/env python3
"""Acquire, verify, and record the exact shared mDNS runtime provenance.

The committed package lock (``build/inputs/mdns-packages.lock.json``) is the
only package identity source.  Acquisition is pinned to the exact versions it
records, every downloaded archive is re-hashed against the lock before use, and
the resulting source map carries the exact archive SHA-256 plus
package/version/source/architecture for each package.

Corresponding-source and distribution-notice closure is a deliberately separate
gate.  It fails closed: it never records ``source_offer_verified: true`` without
a complete notice inventory in the verified runtime and a public source offer
for every distinct source package in the lock.  Binary provenance is not, by
itself, a corresponding-source offer.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

LOCK_SCHEMA = 'libreecho-mdns-packages/v1'
OFFERS_SCHEMA = 'libreecho-mdns-source-offers/v1'
RUNTIME_SCHEMA = 'libreecho-mdns-runtime/v1'
SOURCE_MAP_SCHEMA = 'libreecho-mdns-runtime-source-map/v1'
# The Platform runtime builder stages one distribution notice record per
# package under this immutable prefix; the closure gate requires the exact
# record for every locked package.
NOTICE_ROOT = 'usr/share/licenses/libreecho-mdns'
ARCHITECTURES = ('armhf', 'all')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
UNRESOLVED_OFFERS = {'NOASSERTION', 'NONE', 'TBD', 'TODO', 'UNKNOWN'}
PUBLIC_OFFER = re.compile(r'^https://[^\s]+$')


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_regular_file(path, description):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('%s is missing or unsafe' % description)
    return path


def load_lock(path):
    """Return the validated lock records; any deviation fails closed."""
    path = require_regular_file(path, 'package lock')
    document = json.loads(path.read_text())
    if document.get('schema') != LOCK_SCHEMA:
        raise ValueError('unsupported package lock schema')
    records = document.get('packages')
    if not isinstance(records, list) or not records:
        raise ValueError('package lock contains no packages')
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('malformed package lock record')
        for field in ('file', 'package', 'version', 'architecture', 'source', 'sha256'):
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError('package lock record is missing %s' % field)
        name = record['file']
        if Path(name).name != name or not name.endswith('.deb'):
            raise ValueError('invalid package archive path: %s' % name)
        if name in seen:
            raise ValueError('duplicate package archive: %s' % name)
        seen.add(name)
        if record['architecture'] not in ARCHITECTURES:
            raise ValueError('package %s is not ARMHF or architecture-independent'
                             % record['package'])
        if not HEX64.fullmatch(record['sha256']):
            raise ValueError('package %s archive hash is malformed' % record['package'])
    return records


def apt_spec(records):
    """Exact ``package=version`` acquisitions for the isolated public apt cache.

    Pinning the version is what makes the acquisition reproducible: the ambient
    archive resolution never selects the package bytes that are installed.
    """
    return ['%s=%s' % (record['package'], record['version'])
            for record in sorted(records, key=lambda item: (item['package'], item['version']))]


def verify_archives(archives, records):
    """Re-hash every locked archive; fail closed on any deviation."""
    archives = Path(archives)
    if archives.is_symlink() or not archives.is_dir():
        raise ValueError('package archive directory is missing or unsafe')
    expected = {record['file']: record for record in records}
    unexpected = sorted(
        entry.name for entry in archives.iterdir()
        if entry.name.endswith('.deb') and entry.name not in expected)
    if unexpected:
        raise ValueError('unrecorded package archives present: %s' % ', '.join(unexpected))
    verified = []
    for name, record in sorted(expected.items()):
        archive = require_regular_file(archives / name, 'package archive %s' % name)
        digest = sha256_file(archive)
        if digest != record['sha256']:
            raise ValueError('package archive hash mismatch: %s' % name)
        verified.append({
            'archive': record['file'],
            'sha256': digest,
            'package': record['package'],
            'version': record['version'],
            'source': record['source'],
            'architecture': record['architecture'],
        })
    return verified


def load_source_offers(path, records):
    """Return the reviewed corresponding-source offers validated against the lock."""
    path = require_regular_file(path, 'shared-runtime source offer index')
    document = json.loads(path.read_text())
    if document.get('schema') != OFFERS_SCHEMA:
        raise ValueError('unsupported source offer schema')
    offers = document.get('offers')
    if not isinstance(offers, list) or not offers:
        raise ValueError('source offer index is empty')
    versions = {}
    for record in records:
        versions.setdefault(record['source'], set()).add(record['version'])
    expected = {}
    for entry in offers:
        if not isinstance(entry, dict):
            raise ValueError('malformed source offer record')
        source = entry.get('source')
        offer = entry.get('source_offer')
        version = entry.get('version')
        if not isinstance(source, str) or not source:
            raise ValueError('source offer record is missing a source package')
        if source in expected:
            raise ValueError('duplicate source offer: %s' % source)
        if source not in versions:
            raise ValueError('source offer does not correspond to the lock: %s' % source)
        if not isinstance(offer, str) or offer.upper() in UNRESOLVED_OFFERS \
                or not PUBLIC_OFFER.fullmatch(offer):
            raise ValueError('source offer is not a public offer URL: %s' % source)
        if not isinstance(version, str) or versions[source] != {version}:
            raise ValueError('source offer version does not match the lock: %s' % source)
        expected[source] = {'source': source, 'version': version, 'source_offer': offer}
    missing = sorted(set(versions) - set(expected))
    if missing:
        raise ValueError('source offers missing for locked sources: %s' % ', '.join(missing))
    return [expected[source] for source in sorted(expected)]


def build_source_map(lock_path, records, verified):
    return {
        'schema': SOURCE_MAP_SCHEMA,
        'lock_sha256': sha256_file(lock_path),
        'packages': verified,
        # Acquisition records binary provenance only.  The corresponding-source
        # and notice closure is a separate gate and is never assumed here.
        'source_offer_verified': False,
    }


def check_closure(lock_path, runtime, source_offers, manifest_sha256):
    """Fail closed unless the runtime's notice and source-offer closure is complete."""
    records = load_lock(lock_path)
    lock_sha = sha256_file(lock_path)
    if not HEX64.fullmatch(manifest_sha256 or ''):
        raise ValueError('expected runtime manifest identity is malformed')
    runtime = Path(runtime)
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError('mDNS runtime root is missing or unsafe')
    manifest_path = require_regular_file(runtime / 'manifest.json', 'mDNS runtime manifest')
    if sha256_file(manifest_path) != manifest_sha256:
        raise ValueError('mDNS runtime manifest identity does not match the verified value')
    packages_path = require_regular_file(runtime / 'packages.json', 'mDNS runtime package inventory')
    if sha256_file(packages_path) != lock_sha:
        raise ValueError('mDNS runtime package inventory does not match the committed lock')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != RUNTIME_SCHEMA:
        raise ValueError('unsupported mDNS runtime manifest schema')
    if manifest.get('packages_sha256') != lock_sha:
        raise ValueError('mDNS runtime manifest is bound to a different package lock')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('mDNS runtime manifest has no file inventory')
    notices = []
    for record in records:
        member = '%s/%s/copyright' % (NOTICE_ROOT, record['package'])
        entry = files.get(member)
        if not isinstance(entry, dict) or not HEX64.fullmatch(str(entry.get('sha256', ''))):
            raise ValueError('mDNS runtime has no distribution notice record: %s'
                             % record['package'])
        notices.append({'package': record['package'], 'member': member,
                        'sha256': entry['sha256'], 'mode': entry.get('mode')})
    offers = load_source_offers(source_offers, records)
    return {
        'schema': SOURCE_MAP_SCHEMA,
        'lock_sha256': lock_sha,
        'runtime_manifest_sha256': manifest_sha256,
        'runtime_packages_sha256': manifest['packages_sha256'],
        'source_offer_index_sha256': sha256_file(source_offers),
        'runtime_files': len(files),
        'license_records': notices,
        'sources': offers,
        'packages': [{
            'archive': record['file'],
            'sha256': record['sha256'],
            'package': record['package'],
            'version': record['version'],
            'source': record['source'],
            'architecture': record['architecture'],
        } for record in records],
        # Reached only after the notice inventory and every corresponding-source
        # offer are present and consistent with the exact lock.
        'source_offer_verified': True,
    }


def write_document(document, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('refusing to overwrite provenance output: %s' % output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, sort_keys=True, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    spec = commands.add_parser('apt-spec', help='print exact apt acquisition pins')
    spec.add_argument('--lock', type=Path, required=True)

    verify = commands.add_parser('verify', help='verify acquired archives against the lock')
    verify.add_argument('--lock', type=Path, required=True)
    verify.add_argument('--archives', type=Path, required=True)
    verify.add_argument('--source-map', type=Path)

    closure = commands.add_parser('closure', help='source and notice closure gate')
    closure.add_argument('--lock', type=Path, required=True)
    closure.add_argument('--runtime', type=Path, required=True)
    closure.add_argument('--source-offers', type=Path, required=True)
    closure.add_argument('--manifest-sha256', required=True)
    closure.add_argument('--output', type=Path)

    args = parser.parse_args()
    try:
        if args.command == 'apt-spec':
            for pin in apt_spec(load_lock(args.lock)):
                print(pin)
            return 0
        if args.command == 'verify':
            records = load_lock(args.lock)
            verified = verify_archives(args.archives, records)
            if args.source_map is not None:
                write_document(build_source_map(args.lock, records, verified), args.source_map)
            print('mdns_packages_verified=%d lock_sha256=%s'
                  % (len(verified), sha256_file(args.lock)))
            return 0
        document = check_closure(args.lock, args.runtime, args.source_offers,
                                 args.manifest_sha256)
        if args.output is not None:
            write_document(document, args.output)
        print('mdns_runtime_source_offer_verified=%s manifest_sha256=%s notices=%d'
              % (str(document['source_offer_verified']).lower(),
                 document['runtime_manifest_sha256'], len(document['license_records'])))
        return 0
    except (ValueError, json.JSONDecodeError) as error:
        # Fail closed: no partial or optimistic provenance record is written.
        print('ERROR: %s' % error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
