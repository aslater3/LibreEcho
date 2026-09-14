#!/usr/bin/env python3
"""Record exact acquired binary archives; source offers remain a separate gate."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SCHEMA = 'libreecho-mdns-packages/v1'

def create_lock(directory):
    records = []
    identities = set()
    for archive in sorted(directory.glob('*.deb')):
        if archive.is_symlink() or not archive.is_file():
            raise ValueError('package archive must be a regular file')
        metadata = subprocess.run(
            ['dpkg-deb', '-f', str(archive), 'Package', 'Version', 'Architecture', 'Source'],
            capture_output=True, text=True, check=True, timeout=30).stdout
        fields = dict(line.split(': ', 1) for line in metadata.splitlines() if ': ' in line)
        if fields.get('Architecture') not in ('armhf', 'all'):
            raise ValueError('package is not ARMHF or architecture-independent')
        identity = fields['Package']
        if identity in identities:
            raise ValueError('duplicate binary package')
        identities.add(identity)
        records.append({'file': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                        'package': identity, 'version': fields['Version'],
                        'architecture': fields['Architecture'],
                        'source': fields.get('Source', identity)})
    if not records:
        raise ValueError('no binary packages acquired')
    return {'schema': SCHEMA, 'packages': records}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    document = create_lock(args.archives)
    # Exclusive creation prevents silently replacing an already-frozen lock.
    with args.output.open('x') as output:
        json.dump(document, output, sort_keys=True, indent=2)
        output.write('\n')

if __name__ == '__main__':
    main()
