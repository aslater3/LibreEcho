"""Publish a small dev discovery pointer after immutable asset verification.

The pointer is transport metadata, never signing authority. The device still
verifies the OTA with its installed trust root and checks every signed asset.
Only the explicitly mutable channel pointer is replaced; immutable releases,
tags, and stable publication are never modified here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

CHANNEL = 'radar-puffin-dev-channel'
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
    return (tag + '\n' + sha(ota) + '\n').encode('ascii')


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
    if any(x['name'] != 'release-pointer.txt' for x in current.get('assets', [])):
        raise SystemExit('unexpected channel assets; refusing mutation')
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / 'release-pointer.txt'
        file.write_bytes(data)
        gh('release', 'upload', CHANNEL, str(file), '--repo', a.repository, '--clobber')
    current = json.loads(gh('api', api + CHANNEL))
    assets = current.get('assets', [])
    if len(assets) != 1 or assets[0].get('name') != 'release-pointer.txt' or assets[0].get('size') != len(data) or assets[0].get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest():
        raise SystemExit('published pointer readback mismatch')
    print('dev_pointer_verified=' + a.tag)


if __name__ == '__main__':
    main()
