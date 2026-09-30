#!/usr/bin/env python3
"""Fail-closed release/base closure without changing signed OTA plan semantics.

Bases have a separate inventory: ota-assets/ remains exactly the changed-only
asset set enforced by Platform and Product. No manifest bytes are rewritten.

Every entry point is target-aware. A combined release stages, ships and gates
each selected target independently: the base namespace, the provenance
document, the published feature plan/inventory/OTA prefix and the recovery
bundle all carry the target's release slug, so a Radar artifact can never
satisfy a Biscuit plan (or the reverse).
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
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
from target_registry import DEFAULT, KNOWN_TARGETS, load_target

SCHEMA = 'libreecho-release-completeness-v1'
TARGETS = {target: load_target(target)['release_slug'] for target in KNOWN_TARGETS}
# Default-target alias kept for the historical single-target callers/tests.
PROVENANCE = f'libreecho-{TARGETS[DEFAULT]}-release-completeness.json'
API = 'https://api.github.com/repos/aslater3/LibreEcho'
DOWNLOAD = 'https://github.com/aslater3/LibreEcho/releases/download/'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,199}\Z')
TAG = re.compile(r'radar-puffin-(?:v[0-9]+\.[0-9]+\.[0-9]+|(?:build|nightly)-[a-f0-9]{7}-[a-f0-9]{16}-[a-f0-9]{16})\Z')


def provenance_name(target=DEFAULT):
    load_target(target)
    return f'libreecho-{TARGETS[target]}-release-completeness.json'


def product_tag(prefix, target=DEFAULT):
    """Recover the product-wide tag from a target-qualified asset prefix.

    ``asset_prefix(tag, DEFAULT)`` is ``libreecho-<tag>`` and for every other
    target ``libreecho-<slug>-<tag minus radar-puffin->``. Turning a slugged
    prefix such as ``libreecho-biscuit-build-...`` back into the one product
    tag ``radar-puffin-build-...`` keeps the provenance release identity
    product-wide instead of inventing a per-target tag.
    """
    load_target(target)
    stem = prefix.removeprefix('libreecho-')
    slug = TARGETS[target]
    if target != DEFAULT and stem.startswith(slug + '-'):
        stem = 'radar-puffin-' + stem[len(slug) + 1:]
    return stem


def safe_name(value):
    if not isinstance(value, str) or NAME.fullmatch(value) is None:
        fail('unsafe asset name')
    return value


def references(plan, target=DEFAULT):
    records = validate_plan(plan, plan.get('release', ''), plan.get('source_commit', ''), target)
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


def validate_completeness(plan, assets, provenance, bundle=None, *, target=DEFAULT):
    load_target(target)
    expected = references(plan, target)
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
        validate_bundle(plan, assets, checked, bundle, target=target)
    return checked


def install_features(plan, provenance, *, allow_runtime=False, target=DEFAULT):
    """Select fresh-install bytes; never change the signed OTA actions."""
    references(plan, target)
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


def validate_bundle(plan, assets, checked, bundle, *, target=DEFAULT):
    """Check every pinned line and exactly one correct staging pair per feature."""
    required = {r['name']: r for r in install_features(plan, {'references': list(checked.values())}, target=target)}
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
    slug = load_target(target)['release_slug']
    target_archives = sorted(assets.glob(f'libreecho-{slug}-*initial-install.tar')) + sorted(assets.glob(f'libreecho-{slug}-*.ota.tar'))
    for archive in target_archives:
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


def stage(run, catalog_path, *, target=DEFAULT):
    load_target(target)
    plan = load_json(run / 'feature-plan.json', 'feature plan')
    expected = references(plan, target)
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
    (run / provenance_name(target)).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    with tempfile.TemporaryDirectory(prefix='libreecho-base-check-') as directory:
        flat = Path(directory)
        for item in records:
            source = (dest if item['role'] == 'base' else run / 'ota-assets') / item['name']
            shutil.copyfile(source, flat / item['name'])
        validate_completeness(plan, flat, data, target=target)
        install_features(plan, data, target=target)
    return data


def ship(run, output, prefix, plan, *, target=DEFAULT):
    """Copy verified bases plus portable provenance into a prepared release."""
    load_target(target)
    data = load_json(run / provenance_name(target), 'release completeness provenance')
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
        elif prefix is not None:
            item['source_release'] = product_tag(prefix, target)
    validate_completeness(plan, output, data, target=target)
    path = output / provenance_name(target)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    paths.append(path)
    return data, paths


def _one(root, glob):
    paths = sorted(root.glob(glob))
    if len(paths) != 1:
        fail(f'expected exactly one {glob}, found {len(paths)}')
    return paths[0]


def _sums(assets, target=DEFAULT):
    """The target's own release checksum inventory, never a TWRPINSTALL section."""
    slug = TARGETS[target]
    paths = [p for p in sorted(assets.glob(f'libreecho-{slug}-*-SHA256SUMS'))
             if not p.name.endswith('-TWRPINSTALL-SHA256SUMS')]
    if len(paths) != 1:
        fail(f'expected exactly one {slug} release checksum inventory, found {len(paths)}')
    return paths[0]


def _bundle_manifest(assets, target=DEFAULT):
    slug = TARGETS[target]
    candidate = assets / f'libreecho-{slug}-bundle.manifest'
    if candidate.is_file():
        return candidate
    legacy = assets / 'bundle.manifest'
    if target == DEFAULT and legacy.is_file():
        return legacy
    return None


def check_assets(assets, bundle=None, *, target=DEFAULT):
    load_target(target)
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
        expected_key_sha256=os.environ.get('LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256'),
        expected_target=target)
    data = load_json(assets / provenance_name(target), 'published completeness provenance')
    if any(p['source_release'] == 'current-build' for p in data['references']):
        fail('published provenance still references current-build')
    validate_completeness(plan, assets, data, bundle, target=target)
    return plan, data


def check_run(run, builder, *, target=DEFAULT):
    """Exercise the real Platform bundle producer before uploading an artifact."""
    load_target(target)
    from ota_v2_product import load_feature_contract
    candidate = dict(line.split('=', 1) for line in (run / 'CURRENT.candidate').read_text().splitlines() if '=' in line)
    if candidate.get('board', DEFAULT) != target:
        fail('fresh-install completeness run target mismatch')
    if candidate.get('ota_format', 'v1') != 'v2':
        fail('fresh-install completeness requires an OTA v2 feature plan; v1 has no recovery feature contract')
    plan, inventory, asset_dir, _ = load_feature_contract(run, candidate)
    data = load_json(run / provenance_name(target), 'release completeness provenance')
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
            expected_key_sha256=os.environ.get('LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256'),
            expected_target=target)
        for source in (run / 'boot.img', run / 'ota-public-key.hex', ota):
            shutil.copyfile(source, flat / source.name)
        def record(path):
            sha, size = digest(path)
            return {'name': path.name, 'sha256': sha, 'size': size}
        manifest = dict(schema='libreecho-initial-install-v1', release='build-completeness-check',
            board=target, soc='mt8163', image_profile='ota', service_profile='production',
            boot=record(flat / 'boot.img'), ota_public_key=record(flat / 'ota-public-key.hex'),
            features=install_features(plan, data, target=target), amonet={})
        (flat / 'manifest.json').write_text(json.dumps(manifest) + '\n')
        spec = importlib.util.spec_from_file_location('recovery_builder', builder / 'build_install_bundle.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = Path(directory) / 'bundle'
        # Platform #211 assembles per target; an older builder must never be
        # asked to produce a new target's bundle.
        if 'target' in inspect.signature(module.assemble).parameters:
            module.assemble(flat, output, builder / 'src', '', 2153472, target)
        else:
            if target != DEFAULT:
                fail('Platform recovery builder lacks target support')
            module.assemble(flat, output, builder / 'src', '', 2153472)
        bundle = output / f'libreecho-{TARGETS[target]}-bundle.manifest'
        if not bundle.is_file():
            # An older Platform builder emits only the unqualified legacy name.
            bundle = output / 'bundle.manifest'
        validate_completeness(plan, flat, data, bundle, target=target)


def record_recovery(assets, *, target=DEFAULT):
    """Include stable recovery outputs in both strict publisher inventories.

    This is the legacy single-target layout: the recovery zip/manifest become
    members of the target's own release checksum inventory and build manifest.
    The combined/multi-target layout instead tracks recovery output in the
    separate ``*-TWRPINSTALL-SHA256SUMS`` sections that ``combined_publisher``
    verifies, so it must not call this.
    """
    load_target(target)
    slug = TARGETS[target]
    check_assets(assets, _bundle_manifest(assets, target), target=target)
    build_path = _one(assets, f'libreecho-{slug}-*-build.json')
    data = load_json(build_path, 'stable build manifest')
    if data.get('schema') != 'libreecho-stable-release-v1':
        fail('record-recovery is only for stable release assembly')
    names = {name for name in (f'libreecho-{slug}-install.zip', f'libreecho-{slug}-bundle.manifest')
             if (assets / name).is_file()}
    if target == DEFAULT:
        names |= {name for name in ('libreecho-install.zip', 'bundle.manifest') if (assets / name).is_file()}
    if not names:
        fail('no recovery outputs to record')
    if any(r['name'] in names for r in data['artifacts']):
        fail('recovery outputs already recorded')
    for name in sorted(names):
        sha, size = digest(assets / name)
        data['artifacts'].append(dict(name=name, sha256=sha, size=size))
    data['artifacts'].sort(key=lambda r: r['name'])
    build_path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    sums = _sums(assets, target)
    members = [line.split('  ', 1)[1] for line in sums.read_text().splitlines() if '  ' in line]
    members = sorted(set(members) | names)
    sums.write_text(''.join(f'{digest(assets / name)[0]}  {name}\n' for name in members), encoding='ascii')


def _candidate_target(run):
    path = run / 'CURRENT.candidate'
    marker = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line).get('board', DEFAULT)
    load_target(marker)
    return marker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    staging = sub.add_parser('stage')
    staging.add_argument('--run', type=Path, required=True)
    staging.add_argument('--base-catalog', type=Path, required=True)
    staging.add_argument('--target', choices=list(KNOWN_TARGETS))
    recovery = sub.add_parser('record-recovery')
    recovery.add_argument('--assets', type=Path, required=True)
    recovery.add_argument('--target', choices=list(KNOWN_TARGETS))
    check = sub.add_parser('check')
    check.add_argument('--assets', type=Path)
    check.add_argument('--run', type=Path)
    check.add_argument('--builder', type=Path)
    check.add_argument('--bundle', type=Path)
    check.add_argument('--target', choices=list(KNOWN_TARGETS))
    args = parser.parse_args()
    try:
        if args.command == 'stage':
            # Derive the target from the run's own candidate board; a combined
            # build stages one run directory per selected target.
            target = args.target or _candidate_target(args.run)
            stage(args.run, args.base_catalog, target=target)
            print(f'release_completeness=PASS target={target}')
            return 0
        targets = [args.target] if args.target else list(KNOWN_TARGETS)
        # One combined release: iterate supported targets, not one release per
        # target. Product-wide tags are untouched and no pointer changes here.
        for target in targets:
            if args.command == 'record-recovery':
                record_recovery(args.assets, target=target)
            elif args.run and args.builder and not args.assets:
                check_run(args.run, args.builder, target=target)
            elif args.assets and not args.run:
                bundle = args.bundle if (args.bundle and len(targets) == 1) else _bundle_manifest(args.assets, target)
                check_assets(args.assets, bundle, target=target)
            else:
                fail('check requires --assets or --run with --builder')
            print(f'release_completeness=PASS target={target}')
        return 0
    except (ContractError, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
