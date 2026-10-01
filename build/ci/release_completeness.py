#!/usr/bin/env python3
"""Fail-closed immutable OTA v3 and recovery release closure.

Every target owns and publishes its complete content-addressed feature set.
OTA and recovery must select the same bytes; neither prior releases nor device
baselines participate in staging, shipping or validation.
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
    from ota_v3_product import validate_plan as validate_target
    return {(r['feature_id'], 'target', kind):
            (r['sha256' if kind == 'payload' else 'manifest_sha256'],
             r['asset' if kind == 'payload' else 'manifest_asset'],
             r['size' if kind == 'payload' else 'manifest_size'])
            for r in validate_target(plan, target) for kind in ('payload', 'manifest')}


def validate_completeness(plan, assets, provenance, bundle=None, *, target=DEFAULT):
    from ota_v3_product import validate_assets
    load_target(target)
    expected = references(plan, target)
    if (not isinstance(provenance, dict) or set(provenance) != {'schema', 'target', 'references'}
            or provenance['schema'] != SCHEMA or provenance['target'] != target
            or not isinstance(provenance['references'], list)):
        fail('release completeness provenance schema mismatch')
    checked = {}
    for item in provenance['references']:
        if not isinstance(item, dict) or set(item) != {'feature_id', 'role', 'kind', 'name', 'sha256', 'size', 'source_release', 'source_asset'}:
            fail('malformed release completeness reference')
        if item['source_release'] != plan['release']:
            fail('completeness source release differs from target release')
        key = (item['feature_id'], item['role'], item['kind'])
        if key not in expected or key in checked:
            fail('unexpected or duplicate completeness reference')
        safe_name(item['name'])
        if (item['source_asset'] != item['name'] or type(item['size']) is not int
                or (item['sha256'], item['name'], item['size']) != expected[key]):
            fail('completeness reference disagrees with target')
        checked[key] = item
    if set(checked) != set(expected):
        fail('missing completeness references')
    validate_assets(plan, assets)
    if bundle is not None:
        validate_bundle(plan, assets, checked, bundle, target=target)
    return checked


def install_features(plan, provenance, *, allow_runtime=False, target=DEFAULT):
    """Recovery always selects the exact same published target as OTA."""
    references(plan, target)
    indexed = {(p['feature_id'], p['role'], p['kind']): p for p in provenance['references']}
    result = []
    for record in plan['features']:
        fid = record['feature_id']
        item = {'name': fid}
        for kind in ('payload', 'manifest'):
            ref = indexed.get((fid, 'target', kind))
            if ref is None or ref['source_release'] != plan['release']:
                fail(f'missing release-owned install-time {kind}: {fid}')
            item[kind] = {key: ref[key] for key in ('name', 'sha256', 'size')}
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


def stage(run, *, target=DEFAULT):
    """Revalidate this run's complete target, never resolve another release."""
    plan = load_json(run / 'feature-plan.json', 'target plan')
    data = load_json(run / provenance_name(target), 'target completeness')
    validate_completeness(plan, run / 'ota-assets', data, target=target)
    return data


def ship(run, output, prefix, plan, *, target=DEFAULT):
    """Ship only release-owned provenance; assets were copied from ota-assets."""
    data = load_json(run / provenance_name(target), 'release completeness provenance')
    if prefix is not None and product_tag(prefix, target) != plan['release']:
        fail('publication tag differs from signed target release')
    validate_completeness(plan, output, data, target=target)
    path = output / provenance_name(target)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return data, [path]


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
    references(plan, target)
    if product_tag(prefix, target) != plan['release']:
        fail('published tag differs from signed target release')
    validate_control_tar(ota, assets / f'{prefix}-ota-public-key.hex', 'v3', plan['version'],
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
    if candidate.get('ota_format') != 'v3':
        fail('fresh-install completeness requires an immutable OTA v3 target')
    plan, inventory, asset_dir, _ = load_feature_contract(run, candidate)
    data = load_json(run / provenance_name(target), 'release completeness provenance')
    with tempfile.TemporaryDirectory(prefix='libreecho-recovery-gate-') as directory:
        flat = Path(directory) / 'assets'
        flat.mkdir()
        for item in data['references']:
            source = asset_dir / safe_name(item['name'])
            shutil.copyfile(source, flat / item['name'])
        validate_completeness(plan, flat, data, target=target)
        ota = _one(run, '*.ota.tar')
        with tarfile.open(ota, 'r:') as tar:
            for name in ('manifest', 'manifest.sig'):
                (flat / name).write_bytes(tar.extractfile(name).read())
        validate_control_tar(ota, run / 'ota-public-key.hex', candidate['ota_format'], plan.get('version', plan['release']),
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
            stage(args.run, target=target)
            print(f'release_completeness=PASS target={target}')
            return 0
        if args.target:
            targets = [args.target]
        elif args.command == 'check' and args.run and not args.assets:
            # A build run directory holds exactly one target's candidate (the
            # workflow gates each run in turn). Derive it like `stage` does;
            # iterating every known target would demand a biscuit candidate
            # inside a radar_puffin run and fail every single-target build.
            targets = [_candidate_target(args.run)]
        else:
            # One combined release: iterate supported targets, not one release
            # per target. Product-wide tags are untouched; no pointer changes.
            targets = list(KNOWN_TARGETS)
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
