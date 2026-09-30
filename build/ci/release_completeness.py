#!/usr/bin/env python3
"""Fail-closed release/base closure without changing signed OTA plan semantics.

Bases have a separate inventory: ota-assets/ remains exactly the changed-only
asset set enforced by Platform and Product. No manifest bytes are rewritten.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ota_v2_product import (ContractError, DAEMONS, FEATURES, digest, fail,
                            load_json, validate_control_tar, validate_plan)

SCHEMA = 'libreecho-release-completeness-v1'
TARGETS = {'radar_puffin': 'radar-puffin'}
PROVENANCE = 'libreecho-radar-puffin-release-completeness.json'
API = 'https://api.github.com/repos/aslater3/LibreEcho'
DOWNLOAD = 'https://github.com/aslater3/LibreEcho/releases/download/'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,199}\Z')
TAG = re.compile(r'radar-puffin-(?:v[0-9]+\.[0-9]+\.[0-9]+|(?:build|nightly)-[a-f0-9]{7}-[a-f0-9]{16}-[a-f0-9]{16})\Z')


def safe_name(value):
    if not isinstance(value, str) or NAME.fullmatch(value) is None:
        fail('unsafe asset name')
    return value


def references(plan):
    records = validate_plan(plan, plan.get('release', ''), plan.get('source_commit', ''))
    result = {}
    for record in records:
        fid = record['feature_id']
        for kind in ('payload', 'manifest'):
            result[fid, 'base', kind] = (record[f'base_{kind}_sha256'], None, None)
            if record['action'] != 'preserve':
                result[fid, 'target', kind] = (
                    record['sha256' if kind == 'payload' else 'manifest_sha256'],
                    record['asset' if kind == 'payload' else 'manifest_asset'],
                    record['size' if kind == 'payload' else 'manifest_size'])
    return result


def validate_completeness(plan, assets, provenance, bundle=None, *, target='radar_puffin'):
    expected = references(plan)
    if not isinstance(provenance, dict) or set(provenance) != {'schema', 'target', 'references'} or provenance['schema'] != SCHEMA or provenance['target'] != target:
        fail('release completeness provenance schema mismatch')
    records = provenance['references']
    if not isinstance(records, list):
        fail('release completeness references missing')
    checked = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != {'feature_id', 'role', 'kind', 'name', 'sha256', 'size', 'source_release', 'source_asset'}:
            fail('malformed release completeness reference')
        key = (item['feature_id'], item['role'], item['kind'])
        if key not in expected or key in checked:
            fail('unexpected or duplicate completeness reference')
        name = safe_name(item['name'])
        safe_name(item['source_asset'])
        tag = item['source_release']
        if not isinstance(tag, str) or (TAG.fullmatch(tag) is None and not (item['role'] == 'target' and tag == 'current-build')):
            fail('missing or invalid source release provenance')
        wanted, target_name, target_size = expected[key]
        if (item['sha256'] != wanted or type(item['size']) is not int or item['size'] < 1
                or (target_name is not None and (name != target_name or item['size'] != target_size))):
            fail('completeness reference disagrees with feature plan')
        if item['role'] == 'base':
            suffix = 'payload.squashfs' if item['kind'] == 'payload' else 'manifest.json'
            if name != f"libreecho-{TARGETS[target]}-base-{item['feature_id']}-{wanted}.{suffix}":
                fail('completeness base namespace mismatch')
        if digest(assets / name) != (wanted, item['size']):
            fail(f'completeness asset identity mismatch: {name}')
        checked[key] = item
    if set(checked) != set(expected):
        fail(f'missing completeness references: {sorted(set(expected) - set(checked))}')
    # Bind every full base manifest to its feature/payload/daemon; filenames are
    # intentionally NOT changed when an original manifest is shipped as -base-.
    for record in plan['features']:
        fid = record['feature_id']
        payload = checked[fid, 'base', 'payload']
        manifest = load_json(assets / checked[fid, 'base', 'manifest']['name'], 'base feature manifest')
        if (manifest.get('schema_version') != 1 or manifest.get('feature_id') != fid
                or manifest.get('format') != 'squashfs-lz4'
                or manifest.get('payload', {}).get('sha256') != payload['sha256']
                or manifest.get('payload', {}).get('size') != payload['size']):
            fail(f'base feature manifest/payload disagreement: {fid}')
        if record['action'] == 'preserve' and manifest.get('files', {}).get(DAEMONS[fid], {}).get('sha256') != record['daemon_sha256']:
            fail(f'preserved daemon identity mismatch: {fid}')
    if bundle is not None:
        validate_bundle(plan, assets, checked, bundle)
    return checked


def install_features(plan, provenance, *, allow_runtime=False):
    """Select fresh-install bytes; never change the signed OTA actions."""
    references(plan)
    indexed = {(p['feature_id'], p['role'], p['kind']): p for p in provenance['references']}
    result = []
    for record in plan['features']:
        fid = record['feature_id']
        if record['action'] == 'runtime' and not allow_runtime:
            fail(f'fresh-install recovery does not support runtime action: {fid}; Platform follow-up required')
        role = 'target' if record['action'] == 'replace' else 'base'
        item = {'name': fid}
        for kind in ('payload', 'manifest'):
            p = indexed.get((fid, role, kind))
            if p is None:
                fail(f'missing install-time {role} {kind}: {fid}')
            item[kind] = {key: p[key] for key in ('name', 'sha256', 'size')}
        result.append(item)
    return result


def validate_bundle(plan, assets, checked, bundle):
    """Check every pinned line and exactly one correct staging pair per feature."""
    required = {r['name']: r for r in install_features(plan, {'references': list(checked.values())})}
    if bundle.is_symlink() or not bundle.is_file():
        fail('bundle.manifest is unavailable or unsafe')
    staging = {}
    pinned = []
    for line in bundle.read_text(encoding='ascii').splitlines():
        key, sep, value = line.partition('=')
        if not sep or not key or not value:
            fail('malformed bundle.manifest line')
        parts = value.split(':')
        if key == 'staging':
            if len(parts) != 5 or parts[0] not in required or parts[0] in staging:
                fail('missing/duplicate/unknown recovery staging feature')
            fid, pname, psha, mname, msha = parts
            want = required[fid]
            if (pname, psha, mname, msha) != (want['payload']['name'], want['payload']['sha256'], want['manifest']['name'], want['manifest']['sha256']):
                fail(f'recovery staging does not cover install-time requirement: {fid}')
            staging[fid] = value
            pinned.extend(((pname, psha), (mname, msha)))
        elif key in {'payload', 'install_manifest', 'local_package'}:
            if len(parts) != 2:
                fail('malformed bundle pinned asset')
            pinned.append(tuple(parts))
    if set(staging) != set(FEATURES):
        fail('missing recovery staging feature')
    # At publication these three control members are inside their original tars,
    # not loose release assets. Resolve them without extracting arbitrary paths.
    internal = {}
    for archive in sorted(assets.glob('*initial-install.tar')) + sorted(assets.glob('*.ota.tar')):
        with tarfile.open(archive, 'r:') as tar:
            for member in tar.getmembers():
                if member.name not in {'manifest', 'manifest.sig', 'manifest.json'}:
                    continue
                if not member.isfile() or member.size > 1024 * 1024:
                    fail('unsafe bundled control member')
                import hashlib
                blob = tar.extractfile(member).read()
                sha = hashlib.sha256(blob).hexdigest()
                if member.name in internal and internal[member.name] != sha:
                    fail('conflicting bundled control members')
                internal[member.name] = sha
    for name, sha in pinned:
        safe_name(name)
        if (assets / name).exists():
            actual = digest(assets / name)[0]
        else:
            actual = internal.get(name)
        if actual != sha:
            fail(f'bundle pinned asset missing or digest mismatch: {name}')


def _fetch_module():
    spec = importlib.util.spec_from_file_location('fetch_base', Path(__file__).with_name('fetch-ota-base.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def published_releases(root, fetch):
    releases = []
    for page in range(1, 101):
        listing = root / f'releases-{page}.json'
        fetch(API + f'/releases?per_page=100&page={page}', listing, 16 * 1024 * 1024)
        batch = load_json(listing, 'GitHub release listing')
        if not isinstance(batch, list):
            fail('GitHub releases response is not a list')
        releases.extend(r for r in batch if not r.get('draft') and TAG.fullmatch(r.get('tag_name', '')))
        if len(batch) < 100:
            return releases
    fail('release listing exceeds pagination bound')


def resolve_digest(wanted, releases, root, fetch):
    for release in releases:
        tag = release['tag_name']
        for asset in release.get('assets', []):
            if asset.get('digest') != 'sha256:' + wanted:
                continue
            name = safe_name(asset['name'])
            url = DOWNLOAD + tag + '/' + name
            size = asset.get('size')
            if asset.get('browser_download_url') != url or type(size) is not int or not 0 < size <= 512 * 1024 * 1024:
                fail('invalid published base asset URL/size')
            path = root / wanted
            if not path.exists():
                fetch(url, path, size)
            if digest(path) != (wanted, size):
                fail('published base asset digest/size mismatch')
            return path, tag, name
    fail(f'referenced base not found in published releases: {wanted}')


def stage(run, catalog_path, *, target='radar_puffin'):
    plan = load_json(run / 'feature-plan.json', 'feature plan')
    expected = references(plan)
    catalog = load_json(catalog_path, 'base catalog')
    dest = run / 'release-bases'
    dest.mkdir(exist_ok=False)
    records = []
    fetch = _fetch_module().download
    with tempfile.TemporaryDirectory(prefix='libreecho-base-resolve-') as directory:
        scratch = Path(directory)
        releases = None
        for (fid, role, kind), (sha, target_name, size) in expected.items():
            if role == 'target':
                source = run / 'ota-assets' / target_name
                tag, original, name = 'current-build', target_name, target_name
            else:
                if catalog.get('schema') == 'libreecho-dev-device-baseline-v1':
                    if releases is None:
                        releases = published_releases(scratch, fetch)
                    source, tag, original = resolve_digest(sha, releases, scratch, fetch)
                else:
                    entry = catalog['features'][fid][kind]
                    source = Path(entry['path'])
                    provenance = catalog.get('sources', {}).get(fid, {}).get(kind, {})
                    tag, original = provenance.get('release'), provenance.get('asset')
                    if entry['sha256'] != sha or digest(source) != (sha, entry['size']):
                        fail('base catalog bytes disagree with plan')
                suffix = 'payload.squashfs' if kind == 'payload' else 'manifest.json'
                name = f'libreecho-{TARGETS[target]}-base-{fid}-{sha}.{suffix}'
                shutil.copyfile(source, dest / name)
                source = dest / name
            actual, actual_size = digest(source)
            if actual != sha or (size is not None and size != actual_size):
                fail('staged release reference digest/size mismatch')
            records.append(dict(feature_id=fid, role=role, kind=kind, name=name,
                                sha256=sha, size=actual_size, source_release=tag, source_asset=original))
    data = {'schema': SCHEMA, 'target': target, 'references': records}
    (run / PROVENANCE).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    with tempfile.TemporaryDirectory(prefix='libreecho-base-check-') as directory:
        flat = Path(directory)
        for item in records:
            source = (dest if item['role'] == 'base' else run / 'ota-assets') / item['name']
            shutil.copyfile(source, flat / item['name'])
        validate_completeness(plan, flat, data, target=target)
        install_features(plan, data)
    return data


def ship(run, output, prefix, plan):
    """Copy verified bases plus portable provenance into a prepared release."""
    data = load_json(run / PROVENANCE, 'release completeness provenance')
    data = json.loads(json.dumps(data))
    paths = []
    for item in data['references']:
        if item['role'] == 'base':
            name = safe_name(item['name'])
            source = run / 'release-bases' / name
            if digest(source) != (item['sha256'], item['size']):
                fail('release base identity changed before assembly')
            shutil.copyfile(source, output / name)
            paths.append(output / name)
        else:
            if prefix is not None:
                item['source_release'] = prefix.removeprefix('libreecho-')
    validate_completeness(plan, output, data)
    path = output / PROVENANCE
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    paths.append(path)
    return data, paths


def _one(root, glob):
    paths = sorted(root.glob(glob))
    if len(paths) != 1:
        fail(f'expected exactly one {glob}, found {len(paths)}')
    return paths[0]


def check_assets(assets, bundle=None, *, target='radar_puffin'):
    plan_path = _one(assets, f'libreecho-{TARGETS[target]}-*-feature-plan.json')
    plan = load_json(plan_path, 'published feature plan')
    prefix = plan_path.name.removesuffix('-feature-plan.json')
    inventory = load_json(assets / f'{prefix}-feature-assets.json', 'published feature inventory')
    ota = assets / f'{prefix}.ota.tar'
    with tarfile.open(ota, 'r:') as tar:
        member = tar.getmember('manifest')
        if not member.isfile() or not 0 < member.size <= 65536:
            fail('unsafe or oversized signed OTA manifest')
        raw = tar.extractfile(member).read().decode('ascii')
    fields = dict(line.split('=', 1) for line in raw.splitlines())
    if fields.get('board') != target:
        fail('signed manifest target does not match selected completeness target')
    validate_control_tar(ota, assets / f'{prefix}-ota-public-key.hex', 'v2', plan['release'],
        feature_plan=plan, feature_inventory=inventory, feature_asset_dir=assets,
        expected_channel=fields['update_channel'], boot_path=assets / f'{prefix}-boot.img',
        expected_key_sha256=os.environ.get('LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256'))
    data = load_json(assets / PROVENANCE, 'published completeness provenance')
    if any(p['source_release'] == 'current-build' for p in data['references']):
        fail('published provenance still references current-build')
    validate_completeness(plan, assets, data, bundle, target=target)
    return plan, data


def check_run(run, builder, *, target='radar_puffin'):
    """Exercise the real Platform bundle producer before uploading an artifact."""
    from ota_v2_product import load_feature_contract
    candidate = dict(line.split('=', 1) for line in (run / 'CURRENT.candidate').read_text().splitlines() if '=' in line)
    if candidate.get('ota_format', 'v1') != 'v2':
        fail('fresh-install completeness requires an OTA v2 feature plan; v1 has no recovery feature contract')
    plan, inventory, asset_dir, _ = load_feature_contract(run, candidate)
    data = load_json(run / PROVENANCE, 'release completeness provenance')
    with tempfile.TemporaryDirectory(prefix='libreecho-recovery-gate-') as directory:
        flat = Path(directory) / 'assets'
        flat.mkdir()
        for item in data['references']:
            source = (run / 'release-bases' if item['role'] == 'base' else asset_dir) / safe_name(item['name'])
            shutil.copyfile(source, flat / item['name'])
        validate_completeness(plan, flat, data, target=target)
        ota = _one(run, '*.ota.tar')
        with tarfile.open(ota, 'r:') as tar:
            for name in ('manifest', 'manifest.sig'):
                (flat / name).write_bytes(tar.extractfile(name).read())
        validate_control_tar(ota, run / 'ota-public-key.hex', 'v2', plan['release'],
            feature_plan=plan, feature_inventory=inventory, feature_asset_dir=asset_dir,
            expected_channel=candidate['update_channel'], boot_path=run / 'boot.img',
            expected_key_sha256=os.environ.get('LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256'))
        for source in (run / 'boot.img', run / 'ota-public-key.hex', ota):
            shutil.copyfile(source, flat / source.name)
        def record(path):
            sha, size = digest(path)
            return {'name': path.name, 'sha256': sha, 'size': size}
        manifest = dict(schema='libreecho-initial-install-v1', release='build-completeness-check',
            board='radar_puffin', soc='mt8163', image_profile='ota', service_profile='production',
            boot=record(flat / 'boot.img'), ota_public_key=record(flat / 'ota-public-key.hex'),
            features=install_features(plan, data), amonet={})
        (flat / 'manifest.json').write_text(json.dumps(manifest) + '\n')
        spec = importlib.util.spec_from_file_location('recovery_builder', builder / 'build_install_bundle.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = Path(directory) / 'bundle'
        module.assemble(flat, output, builder / 'src', '', 2153472)
        validate_completeness(plan, flat, data, output / 'bundle.manifest', target=target)


def record_recovery(assets):
    """Include stable recovery outputs in both strict publisher inventories."""
    check_assets(assets, assets / 'bundle.manifest')
    build_path = _one(assets, '*-build.json')
    data = load_json(build_path, 'stable build manifest')
    if data.get('schema') != 'libreecho-stable-release-v1':
        fail('record-recovery is only for stable release assembly')
    names = {'libreecho-install.zip', 'bundle.manifest'}
    if any(r['name'] in names for r in data['artifacts']):
        fail('recovery outputs already recorded')
    for name in sorted(names):
        sha, size = digest(assets / name)
        data['artifacts'].append(dict(name=name, sha256=sha, size=size))
    data['artifacts'].sort(key=lambda r: r['name'])
    build_path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    sums = _one(assets, '*-SHA256SUMS')
    sums.write_text(''.join(f'{digest(p)[0]}  {p.name}\n' for p in sorted(assets.iterdir())
                            if p.is_file() and p != sums), encoding='ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    staging = sub.add_parser('stage')
    staging.add_argument('--run', type=Path, required=True)
    staging.add_argument('--base-catalog', type=Path, required=True)
    recovery = sub.add_parser('record-recovery')
    recovery.add_argument('--assets', type=Path, required=True)
    check = sub.add_parser('check')
    check.add_argument('--assets', type=Path)
    check.add_argument('--run', type=Path)
    check.add_argument('--builder', type=Path)
    check.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    try:
        # One combined release: iterate supported targets, not one release per
        # target. Only radar_puffin is implemented today; no pointer changes.
        for target in TARGETS:
            if args.command == 'record-recovery':
                record_recovery(args.assets)
            elif args.command == 'stage':
                stage(args.run, args.base_catalog, target=target)
            elif args.run and args.builder and not args.assets:
                check_run(args.run, args.builder, target=target)
            elif args.assets and not args.run:
                check_assets(args.assets, args.bundle, target=target)
            else:
                fail('check requires --assets or --run with --builder')
            print(f'release_completeness=PASS target={target}')
        return 0
    except (ContractError, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
