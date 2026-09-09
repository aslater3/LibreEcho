#!/usr/bin/env python3
"""Stage a checksum-verified prior GitHub release for the OTA feature planner.

GitHub HTTPS release metadata is the baseline trust source, not an OTA signature.
Device signature verification and protected release signing remain independent.
"""
import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

API = 'https://api.github.com/repos/aslater3/LibreEcho'
DOWNLOAD = 'https://github.com/aslater3/LibreEcho/releases/download/'
FEATURES = ('airplay2', 'tts', 'wakeword', 'stt', 'assistant')
TAG = re.compile(r'radar-puffin-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')

def version(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError('invalid stable release tag')
    return tuple(map(int, match.groups()))

def download(url, dest, limit):
    req = urllib.request.Request(url, headers={'User-Agent': 'LibreEcho-OTA-baseline', 'Accept': 'application/vnd.github+json'})
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
                and TAG.fullmatch(r.get('tag_name', '')) and version(r['tag_name']) < version(target)]
    if not eligible:
        raise ValueError('no preceding stable release')
    return max(eligible, key=version)

def resolve(tag, root):
    version(tag)
    root.mkdir(parents=True, exist_ok=False)
    metadata = root / 'release.json'
    download(API + '/releases/tags/' + tag, metadata, 4 * 1024 * 1024)
    release = json.loads(metadata.read_text())
    if release['tag_name'] != tag or release['draft'] or release['prerelease']:
        raise ValueError('baseline is not the requested published stable release')
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
    if (build['release'], build['channel'], build['board']) != (tag, 'stable', 'radar_puffin'):
        raise ValueError('baseline build identity mismatch')
    inventory = {r['name']: r for r in build['artifacts']}
    if len(inventory) != len(build['artifacts']):
        raise ValueError('duplicate build artifact')
    catalog = {'features': {}}
    for feature in FEATURES:
        record = {}
        for kind, suffix in [('payload', '.squashfs'), ('manifest', '.manifest.json')]:
            name = prefix + '-' + feature + suffix
            path = fetch(name, feature + suffix, 512 * 1024 * 1024 if kind == 'payload' else 4 * 1024 * 1024)
            digest, size = sha(path), path.stat().st_size
            if checksums.get(name) != digest or inventory[name]['sha256'] != digest or inventory[name]['size'] != size:
                raise ValueError('baseline inventory disagreement')
            record[kind] = {'path': str(path.resolve()), 'sha256': digest, 'size': size}
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
    tag = previous_release(releases, target)
    catalog = resolve(tag, args.output_dir)
    values = f'catalog={catalog.resolve()}\nsha256={sha(catalog)}\nbase_release={tag}\n'
    print(values, end='')
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write(values)

if __name__ == '__main__':
    main()
