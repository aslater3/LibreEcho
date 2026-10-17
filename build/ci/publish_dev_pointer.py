"""Publish a small dev discovery pointer after immutable asset verification.

The pointer is transport metadata, never signing authority. The device still
verifies the OTA with its installed trust root and checks every signed asset.
Only the explicitly mutable channel pointer is replaced; immutable releases,
tags, and stable publication are never modified here.

The 0.13 line owns only ``release-pointer.txt``. The same channel release also
carries ``release-pointer-v3.txt``, which 0.14+ devices read; this publisher
never writes, replaces or deletes it and verifies it is byte-for-byte unchanged
(same asset id, size and digest) after the 0.13 pointer upload.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

CHANNEL = 'radar-puffin-dev-channel'
POINTER_ASSET = 'release-pointer.txt'
# Owned by the 0.14+ publisher. Preserved untouched; never written here.
FOREIGN_ASSETS = frozenset({'release-pointer-v3.txt'})
TAG = re.compile(r'radar-puffin-(?:build|nightly)-[a-f0-9]{7}-[a-f0-9]{16}-[a-f0-9]{16}')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pointer_bytes(root, tag, release):
    if not TAG.fullmatch(tag) or release.get('tag_name') != tag:
        raise ValueError('invalid immutable dev identity')
    if release.get('draft') is not False or release.get('prerelease') is not True:
        raise ValueError('dev source must be published prerelease')
    files = list(root.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError('invalid prepared asset')
    expected = {p.name: (p.stat().st_size, 'sha256:' + sha(p)) for p in files}
    assets = release.get('assets', [])
    actual = {a['name']: (a['size'], a.get('digest')) for a in assets}
    if len(actual) != len(assets) or actual != expected:
        raise ValueError('published inventory differs from verified preparation')
    ota = root / ('libreecho-' + tag + '.ota.tar')
    if ota.name not in expected:
        raise ValueError('missing canonical signed OTA')
    manifest = json.loads((root / ('libreecho-' + tag + '-build.json')).read_text())
    if manifest.get('channel') != 'dev' or manifest.get('signed') is not True:
        raise ValueError('not a signed dev package')
    # The 0.13 pointer must only ever name a single-target radar_puffin v1/v2
    # package that 0.13 devices can install; v3 (0.14+) belongs on the v3 pointer.
    if manifest.get('board') != 'radar_puffin' or manifest.get('ota_format', 'v1') not in ('v1', 'v2'):
        raise ValueError('0.13 dev pointer requires a radar_puffin v1/v2 package')
    return (tag + '\n' + sha(ota) + '\n').encode('ascii')


def frozen_channel_assets(assets):
    """Return the channel assets this publisher must leave untouched."""
    names = [a.get('name') for a in assets]
    if len(names) != len(set(names)):
        raise ValueError('duplicate channel assets; refusing mutation')
    if any(name != POINTER_ASSET and name not in FOREIGN_ASSETS for name in names):
        raise ValueError('unexpected channel assets; refusing mutation')
    return sorted(((a['name'], a.get('id'), a.get('size'), a.get('digest'))
                   for a in assets if a['name'] != POINTER_ASSET))


def verify_channel_readback(assets, frozen, data):
    """Fail unless only the 0.13 pointer changed and it holds exactly data."""
    if frozen_channel_assets(assets) != frozen:
        raise ValueError('frozen channel assets changed')
    pointers = [a for a in assets if a.get('name') == POINTER_ASSET]
    if (len(pointers) != 1 or pointers[0].get('size') != len(data)
            or pointers[0].get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest()):
        raise ValueError('published pointer readback mismatch')


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repository', required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--head', required=True)
    p.add_argument('--assets', required=True, type=Path)
    a = p.parse_args()
    if a.repository != 'aslater3/LibreEcho' or not re.fullmatch('[a-f0-9]{40}', a.head):
        raise SystemExit('invalid publication scope')
    api = 'repos/' + a.repository + '/releases/tags/'
    source = json.loads(gh('api', api + a.tag))
    data = pointer_bytes(a.assets, a.tag, source)
    lookup = subprocess.run(['gh', 'api', api + CHANNEL], capture_output=True, text=True)
    if lookup.returncode:
        if '(HTTP 404)' not in lookup.stderr:
            raise SystemExit('channel lookup failed; refusing mutation')
        gh('release', 'create', CHANNEL, '--repo', a.repository, '--target', a.head,
           '--prerelease', '--latest=false', '--title', 'LibreEcho dev update channel',
           '--notes', 'Mutable discovery pointer only. Source and signed assets remain in immutable development prereleases.')
    current = json.loads(gh('api', api + CHANNEL))
    if current.get('draft') is not False or current.get('prerelease') is not True or current.get('tag_name') != CHANNEL:
        raise SystemExit('invalid channel release')
    try:
        frozen = frozen_channel_assets(current.get('assets', []))
    except ValueError as exc:
        raise SystemExit(str(exc))
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / POINTER_ASSET
        file.write_bytes(data)
        # Upload exactly one file by its own name: --clobber can only replace
        # release-pointer.txt, never release-pointer-v3.txt.
        gh('release', 'upload', CHANNEL, str(file), '--repo', a.repository, '--clobber')
    current = json.loads(gh('api', api + CHANNEL))
    try:
        verify_channel_readback(current.get('assets', []), frozen, data)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print('dev_pointer_verified=' + a.tag + ' asset=' + POINTER_ASSET)


if __name__ == '__main__':
    main()
