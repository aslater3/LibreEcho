#!/usr/bin/env python3
"""Stage a checksum-verified prior GitHub release for the OTA feature planner.

GitHub HTTPS release metadata is the baseline trust source, not an OTA signature.
Device signature verification and protected release signing remain independent.
"""
import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

API = 'https://api.github.com/repos/aslater3/LibreEcho'
DOWNLOAD = 'https://github.com/aslater3/LibreEcho/releases/download/'
FEATURES = ('airplay2', 'tts', 'wakeword', 'stt', 'assistant')
STABLE_TAG = re.compile(r'radar-puffin-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
DEV_TAG = re.compile(r'radar-puffin-(?:build|nightly)-[a-f0-9]{7}-[a-f0-9]{16}-[a-f0-9]{16}\Z')
DEV_CHANNEL = 'radar-puffin-dev-channel'
DEV_POINTER = 'release-pointer.txt'

def version(tag):
    match = STABLE_TAG.fullmatch(tag)
    if not match:
        raise ValueError('invalid stable release tag')
    return tuple(map(int, match.groups()))

def download(url, dest, limit):
    headers = {'User-Agent': 'LibreEcho-OTA-baseline', 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GH_TOKEN')
    if token and url.startswith(API + '/'):
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response, dest.open('xb') as output:
        if not response.url.startswith('https://'):
            raise ValueError('non-HTTPS redirect')
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError('asset exceeds bound')
            output.write(chunk)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def previous_release(releases, target):
    eligible = [r['tag_name'] for r in releases if not r.get('draft') and not r.get('prerelease')
                and STABLE_TAG.fullmatch(r.get('tag_name', '')) and version(r['tag_name']) < version(target)]
    if not eligible:
        raise ValueError('no preceding stable release')
    return max(eligible, key=version)

def _valid_dev_pointer(releases, pointer_text):
    if not pointer_text:
        return None
    lines = pointer_text.splitlines()
    if len(lines) != 2 or pointer_text != '\n'.join(lines) + '\n':
        return None
    tag, digest = lines
    if not DEV_TAG.fullmatch(tag) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return None
    matches = [r for r in releases if r.get('tag_name') == tag and not r.get('draft') and r.get('prerelease')]
    if len(matches) != 1:
        return None
    assets = [a for a in matches[0].get('assets', []) if a.get('name') == 'libreecho-' + tag + '.ota.tar']
    if len(assets) != 1 or assets[0].get('digest') != 'sha256:' + digest:
        return None
    return tag

def baseline_release(releases, target, channel, pointer_text=None):
    if channel == 'dev':
        tag = _valid_dev_pointer(releases, pointer_text)
        if tag is not None:
            return tag
    return previous_release(releases, target)

def fetch_dev_pointer(releases):
    channels = [r for r in releases if r.get('tag_name') == DEV_CHANNEL
                and not r.get('draft') and r.get('prerelease')]
    if len(channels) != 1:
        return None
    assets = [a for a in channels[0].get('assets', []) if a.get('name') == DEV_POINTER]
    if len(assets) != 1:
        return None
    asset = assets[0]
    url = asset.get('browser_download_url')
    size = asset.get('size')
    if url != DOWNLOAD + DEV_CHANNEL + '/' + DEV_POINTER or type(size) is not int or not 0 < size <= 256:
        return None
    with tempfile.TemporaryDirectory(prefix='libreecho-dev-pointer-') as directory:
        path = Path(directory) / DEV_POINTER
        try:
            download(url, path, 256)
            if path.stat().st_size != size:
                return None
            return path.read_text()
        except (OSError, UnicodeError, ValueError):
            return None

def resolve(tag, root):
    if STABLE_TAG.fullmatch(tag):
        channel = 'stable'
        prerelease = False
    elif DEV_TAG.fullmatch(tag):
        channel = 'dev'
        prerelease = True
    else:
        raise ValueError('invalid release tag')
    root.mkdir(parents=True, exist_ok=False)
    metadata = root / 'release.json'
    download(API + '/releases/tags/' + tag, metadata, 4 * 1024 * 1024)
    release = json.loads(metadata.read_text())
    if release['tag_name'] != tag or release['draft'] or release['prerelease'] != prerelease:
        raise ValueError('baseline is not the requested published release')
    assets = {}
    for asset in release['assets']:
        if asset['name'] in assets:
            raise ValueError('duplicate release asset')
        assets[asset['name']] = asset
    prefix = 'libreecho-' + tag
    def fetch(name, local, cap):
        asset = assets[name]
        if asset['browser_download_url'] != DOWNLOAD + tag + '/' + name:
            raise ValueError('asset URL differs from pinned release')
        size = asset['size']
        if type(size) is not int or not 0 < size <= cap:
            raise ValueError('invalid asset size')
        digest = asset.get('digest', '')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('GitHub asset digest missing or invalid')
        path = root / local
        download(asset['browser_download_url'], path, size)
        if path.stat().st_size != size or 'sha256:' + sha(path) != digest:
            raise ValueError('release asset digest/size mismatch')
        return path
    sums = fetch(prefix + '-SHA256SUMS', 'SHA256SUMS', 1024 * 1024)
    checksums = {}
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        if not match or match[2] in checksums:
            raise ValueError('invalid checksum inventory')
        checksums[match[2]] = match[1]
    build_name = prefix + '-build.json'
    build_path = fetch(build_name, 'build.json', 4 * 1024 * 1024)
    if checksums.get(build_name) != sha(build_path):
        raise ValueError('build metadata checksum mismatch')
    build = json.loads(build_path.read_text())
    build_release = build.get('release', build.get('ota_release'))
    if not isinstance(build_release, str):
        raise ValueError('baseline build release identity missing or invalid')
    if STABLE_TAG.fullmatch(build_release):
        expected_build_release = tag
    elif STABLE_TAG.fullmatch('radar-puffin-v' + build_release):
        expected_build_release = build_release
    else:
        raise ValueError('baseline build release identity missing or invalid')
    if channel == 'stable' and expected_build_release != tag:
        raise ValueError('baseline build identity mismatch')
    if (build['channel'], build['board']) != (channel, 'radar_puffin'):
        raise ValueError('baseline build identity mismatch')
    inventory = {r['name']: r for r in build['artifacts']}
    if len(inventory) != len(build['artifacts']):
        raise ValueError('duplicate build artifact')
    catalog = {'features': {}, 'sources': {}}
    for feature in FEATURES:
        catalog['sources'][feature] = {}
        record = {}
        for kind, suffix in [('payload', '.squashfs'), ('manifest', '.manifest.json')]:
            name = prefix + '-' + feature + suffix
            path = fetch(name, feature + suffix, 512 * 1024 * 1024 if kind == 'payload' else 4 * 1024 * 1024)
            digest, size = sha(path), path.stat().st_size
            if checksums.get(name) != digest or inventory[name]['sha256'] != digest or inventory[name]['size'] != size:
                raise ValueError('baseline inventory disagreement')
            record[kind] = {'path': str(path.resolve()), 'sha256': digest, 'size': size}
            catalog['sources'][feature][kind] = {'release': tag, 'asset': name}
        manifest = json.loads(Path(record['manifest']['path']).read_text())
        expected = {'filename': feature + '.squashfs', 'sha256': record['payload']['sha256'], 'size': record['payload']['size']}
        if (manifest.get('schema_version') != 1 or manifest.get('feature_id') != feature
                or manifest.get('format') != 'squashfs-lz4' or manifest.get('payload') != expected):
            raise ValueError('feature manifest disagrees with original payload')
        catalog['features'][feature] = record
    output = root / 'catalog.json'
    output.write_text(json.dumps(catalog, sort_keys=True, separators=(',', ':')) + '\n')
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, help='Candidate numeric version')
    parser.add_argument('--channel', choices=('dev', 'stable'), default='stable')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    target = 'radar-puffin-v' + args.release
    version(target)
    listing = args.output_dir.with_suffix('.releases.json')
    listing.parent.mkdir(parents=True, exist_ok=True)
    releases = []
    # Bounded pagination; fail rather than silently choose from an incomplete list.
    for page in range(1, 101):
        pagefile = listing.with_name(listing.name + f'.{page}')
        download(API + f'/releases?per_page=100&page={page}', pagefile, 16 * 1024 * 1024)
        batch = json.loads(pagefile.read_text())
        releases.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError('release listing exceeds pagination bound')
    pointer = fetch_dev_pointer(releases) if args.channel == 'dev' else None
    tag = baseline_release(releases, target, args.channel, pointer)
    catalog = resolve(tag, args.output_dir)
    values = f'catalog={catalog.resolve()}\nsha256={sha(catalog)}\nbase_release={tag}\n'
    print(values, end='')
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write(values)

if __name__ == '__main__':
    main()
