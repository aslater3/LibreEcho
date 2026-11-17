#!/usr/bin/env python3
"""Promote a hardware-validated dev build to stable, and prove it.

A stable (prd) build re-runs the pipeline on the sources a dev build was
hardware tested with. The rebuild must not change what runs on the device.

  fetch     before the build: download and verify the validated dev release
            (GitHub digest, SHA256SUMS, build.json inventory), require that it
            was built from the same Platform, Linux and UI commits and from a
            Product commit that differs only in release notes or docs, and
            emit build.sh overrides so the stable build adopts the dev
            feature payloads byte for byte.
  check     after the build, before upload: every stable run's feature plan
            names the dev payloads, and its boot.img has the dev kernel,
            second stage and header, with a ramdisk that differs only in the
            files that carry the update channel by design.

Usage:
  promotion_parity.py fetch --tag DEV_TAG --targets radar_puffin,biscuit \\
      --output DIR --product SHA --platform SHA --linux SHA --ui SHA \\
      [--github-env FILE]
  promotion_parity.py check --dev DIR RUN [RUN ...]
  promotion_parity.py boot DEV_BOOT_IMG STABLE_BOOT_IMG
"""
import argparse
import fnmatch
import hashlib
import importlib.util
import json
import lzma
import os
import re
import struct
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = 'https://api.github.com/repos/aslater3/LibreEcho'
FEATURES = ('airplay2', 'tts', 'wakeword', 'stt', 'assistant')
OVERRIDE_ENV = {
    'airplay2': 'AIRPLAY', 'tts': 'TTS', 'wakeword': 'WAKEWORD',
    'stt': 'STT', 'assistant': 'ASSISTANT',
}
# Files the update channel changes on purpose (Platform build_recovery_image.py
# copy_adbd and add_ota_tools). Any other ramdisk difference fails the check.
CHANNEL_PATHS = frozenset({
    'sbin/adbd',
    'etc/libreecho/adb-transport',
    'etc/libreecho/update-channel',
    'etc/libreecho/ota-source.conf',
})
# Product changes allowed between the validated dev build and the stable
# build: they do not reach the image.
PRODUCT_DOC_PATHS = ('release/*.md', 'CHANGELOG.md', 'docs/*', 'README.md',
                     'SECURITY-ADVISORIES.md')
FEATURE_FIELDS = ('asset', 'sha256', 'size', 'manifest_asset', 'manifest_sha256',
                  'manifest_size', 'daemon_path', 'daemon_sha256')
# Android boot header v0: the sizes follow from the compared payloads and the
# 32-byte id at 576 is a digest over them, so neither is compared directly.
BOOT_ID = slice(576, 608)


class ParityError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def boot_parts(data):
    if data[:8] != b'ANDROID!':
        raise ParityError('not an Android boot image')
    kernel_size, _, ramdisk_size, _, second_size, _, _, page = struct.unpack('<8I', data[8:40])
    if page not in (2048, 4096, 16384) or not kernel_size or not ramdisk_size:
        raise ParityError('implausible boot image header')

    def pad(n):
        return (n + page - 1) // page * page

    koff = page
    roff = koff + pad(kernel_size)
    soff = roff + pad(ramdisk_size)
    if soff + second_size > len(data):
        raise ParityError('boot image is truncated')
    header = bytearray(data[:page])
    header[8:40] = data[12:16] + bytes(4) + data[20:24] + bytes(4) + data[28:40] + bytes(8)
    header[BOOT_ID] = bytes(BOOT_ID.stop - BOOT_ID.start)
    return {
        'header': bytes(header),
        'kernel': data[koff:koff + kernel_size],
        'ramdisk': data[roff:roff + ramdisk_size],
        'second': data[soff:soff + second_size],
    }


def cpio_entries(ramdisk):
    if ramdisk[:6] != b'\xfd7zXZ\x00':
        raise ParityError('ramdisk is not xz-compressed')
    raw = lzma.decompress(ramdisk, format=lzma.FORMAT_XZ)
    entries, off = {}, 0
    while True:
        if raw[off:off + 6] != b'070701':
            raise ParityError(f'bad newc header at offset {off}')
        fields = [int(raw[off + 6 + i * 8:off + 14 + i * 8], 16) for i in range(13)]
        mode, uid, gid, nlink, filesize, namesize = (
            fields[1], fields[2], fields[3], fields[4], fields[6], fields[11])
        name_start = off + 110
        name = raw[name_start:name_start + namesize - 1].decode('utf-8', 'surrogateescape')
        data_start = (name_start + namesize + 3) & ~3
        body = raw[data_start:data_start + filesize]
        off = (data_start + filesize + 3) & ~3
        if name == 'TRAILER!!!':
            return entries
        name = re.sub(r'^\./', '', name) or '.'
        entries[name] = (mode, uid, gid, nlink, hashlib.sha256(body).hexdigest())


def compare_boot(dev, stable):
    a, b = boot_parts(dev), boot_parts(stable)
    problems = [f'boot {part} differs from the validated dev build'
                for part in ('header', 'kernel', 'second') if a[part] != b[part]]
    ea, eb = cpio_entries(a['ramdisk']), cpio_entries(b['ramdisk'])
    channel = []
    for name in sorted(set(ea) | set(eb)):
        if ea.get(name) == eb.get(name):
            continue
        if name in CHANNEL_PATHS and name in ea and name in eb:
            channel.append(name)
        elif name not in ea:
            problems.append(f'ramdisk adds {name}')
        elif name not in eb:
            problems.append(f'ramdisk drops {name}')
        else:
            problems.append(f'ramdisk changes {name}')
    return problems, channel


def compare_features(dev_plan, stable_plan):
    problems = []
    dev = {f['feature_id']: f for f in dev_plan.get('features', [])}
    stable = {f['feature_id']: f for f in stable_plan.get('features', [])}
    if set(dev) != set(stable):
        problems.append(f'feature set differs: dev {sorted(dev)} stable {sorted(stable)}')
    for fid in sorted(set(dev) & set(stable)):
        for field in FEATURE_FIELDS:
            if dev[fid].get(field) != stable[fid].get(field):
                problems.append(f'feature {fid} {field} differs from the validated dev build')
    return problems


def product_diff_is_docs(dev_sha, stable_sha, files):
    if dev_sha == stable_sha:
        return []
    return [name for name in files
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in PRODUCT_DOC_PATHS)]


def github_compare(base, head):
    headers = {'User-Agent': 'LibreEcho-promotion', 'Accept': 'application/vnd.github+json'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    req = urllib.request.Request(f'{API}/compare/{base}...{head}?per_page=300', headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        body = json.loads(response.read(16 * 1024 * 1024))
    if body.get('status') not in ('ahead', 'identical') or body.get('behind_by') != 0:
        raise ParityError('stable Product commit does not descend from the validated dev build')
    files = body.get('files', [])
    if len(files) >= 300:
        raise ParityError('too many Product changes since the validated dev build')
    return [f['filename'] for f in files]


def load_fetcher():
    spec = importlib.util.spec_from_file_location('fetch_ota_base', HERE / 'fetch-ota-base.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch(args):
    fetcher = load_fetcher()
    if not fetcher.DEV_TAG.fullmatch(args.tag):
        raise ParityError('validated build must be a published dev build tag')
    expected = {'product': args.product, 'platform': args.platform,
                'linux': args.linux, 'ui': args.ui}
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    payloads = {}
    for target in args.targets.split(','):
        work = root / target
        catalog = json.loads(fetcher.resolve(args.tag, work, target).read_text())
        build = json.loads((work / 'build.json').read_text())
        if build.get('channel') != 'dev' or build.get('signed') is not True:
            raise ParityError(f'{target}: validated build is not a signed dev build')
        sources = build.get('sources', {})
        for name in ('platform', 'linux', 'ui'):
            if sources.get(name) != expected[name]:
                raise ParityError(f'{target}: {name} commit differs from the validated dev build')
        if sources.get('product') != expected['product']:
            changed = github_compare(sources.get('product'), expected['product'])
            outside = product_diff_is_docs(sources.get('product'), expected['product'], changed)
            if outside:
                raise ParityError(f'{target}: Product changed outside release notes/docs since '
                                  f'the validated dev build: {", ".join(sorted(outside)[:10])}')
        plan = build['feature_plan']
        if plan.get('board') != target:
            raise ParityError(f'{target}: validated build plan is for {plan.get("board")}')
        (work / 'dev-feature-plan.json').write_text(json.dumps(plan, sort_keys=True) + '\n')
        # The dev boot image, verified against the release checksum inventory
        # and the plan's boot digest.
        prefix = fetcher.asset_prefix(args.tag, target)
        sums = {}
        for line in (work / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ', 1)
            sums[name] = digest
        boot_name = prefix + '-boot.img'
        boot = work / 'boot.img'
        fetcher.download(f'https://github.com/aslater3/LibreEcho/releases/download/{args.tag}/{boot_name}',
                         boot, 64 * 1024 * 1024)
        if sha256(boot) != sums.get(boot_name) or sha256(boot) != plan.get('boot_sha256'):
            raise ParityError(f'{target}: dev boot image digest mismatch')
        for fid in FEATURES:
            record = catalog['features'][fid]
            key = (record['payload']['sha256'], record['manifest']['sha256'])
            if payloads.setdefault(fid, (key, record))[0] != key:
                raise ParityError(f'{fid}: validated payload differs between targets')
    lines = []
    for fid in FEATURES:
        record = payloads[fid][1]
        lines.append(f'LIBREECHO_{OVERRIDE_ENV[fid]}_PAYLOAD_OVERRIDE={record["payload"]["path"]}')
        lines.append(f'LIBREECHO_{OVERRIDE_ENV[fid]}_MANIFEST_OVERRIDE={record["manifest"]["path"]}')
    text = '\n'.join(lines) + '\n'
    print(text, end='')
    if args.github_env:
        with open(args.github_env, 'a', encoding='utf-8') as f:
            f.write(text)
    return []


def check(args):
    problems = []
    for run in map(Path, args.runs):
        plan = json.loads((run / 'feature-plan.json').read_text())
        board = plan.get('board')
        dev = Path(args.dev) / str(board)
        if not (dev / 'dev-feature-plan.json').is_file():
            problems.append(f'{run.name}: no validated dev build fetched for board {board}')
            continue
        dev_plan = json.loads((dev / 'dev-feature-plan.json').read_text())
        problems += [f'{board}: {p}' for p in compare_features(dev_plan, plan)]
        boot_problems, channel = compare_boot((dev / 'boot.img').read_bytes(),
                                              (run / 'boot.img').read_bytes())
        problems += [f'{board}: {p}' for p in boot_problems]
        for name in channel:
            print(f'{board}: expected channel difference: {name}')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('fetch')
    p.add_argument('--tag', required=True)
    p.add_argument('--targets', required=True)
    p.add_argument('--output', required=True)
    for name in ('product', 'platform', 'linux', 'ui'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--github-env')
    p = sub.add_parser('check')
    p.add_argument('--dev', required=True)
    p.add_argument('runs', nargs='+')
    p = sub.add_parser('boot')
    p.add_argument('dev_boot')
    p.add_argument('stable_boot')
    args = parser.parse_args(argv)
    try:
        if args.command == 'fetch':
            problems = fetch(args)
        elif args.command == 'check':
            problems = check(args)
        else:
            problems, channel = compare_boot(Path(args.dev_boot).read_bytes(),
                                             Path(args.stable_boot).read_bytes())
            for name in channel:
                print(f'expected channel difference: {name}')
    except (OSError, ValueError, KeyError, ParityError) as exc:
        print(f'ERROR: promotion parity: {exc}', file=sys.stderr)
        return 1
    for problem in problems:
        print(f'ERROR: promotion parity: {problem}', file=sys.stderr)
    if problems:
        return 1
    print(f'promotion parity ({args.command}): ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
