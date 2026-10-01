"""Self-contained immutable OTA v3 target-state emitter and validation.

No Platform parser or device state is consulted. A target contract describes
only the bytes published by one immutable release.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

from ota_v2_product import (ContractError, DAEMONS, FEATURES, HEX64, VERSION,
                            digest, fail, load_json, expected_public_key_sha256,
                            validate_file_records)
from target_registry import DEFAULT, load_target

SCHEMA = 'libreecho-product-target-plan-v3'
INVENTORY_SCHEMA = 'libreecho-product-target-assets-v3'
FIELDS = ('asset', 'size', 'sha256', 'manifest_asset', 'manifest_size',
          'manifest_sha256', 'daemon_path', 'daemon_sha256')
TOP = ('format', 'manifest_version', 'board', 'soc', 'architecture', 'image_profile',
       'transaction_type', 'transaction_id', 'release', 'version', 'update_channel',
       'service_profile', 'commit_policy', 'minimum_updater_schema', 'boot_filename',
       'boot_size', 'boot_sha256', 'feature_ids')
TAG = re.compile(r'radar-puffin-(?:v[0-9]+\.[0-9]+\.[0-9]+|(?:build|nightly)-[a-f0-9]{7}-[a-f0-9]{16}-[a-f0-9]{16})\Z')


def reject_forbidden(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if (key in {'action', 'activation', 'feature_policy'}
                    or key.startswith(('base_', 'runtime'))
                    or re.fullmatch(r'feature_.*_(?:action|activation)', key)
                    or (key.startswith('feature_') and ('_base_' in key or '_runtime' in key))):
                fail(f'forbidden target-state key: {key}')
            reject_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            reject_forbidden(child)


def transaction_id(plan):
    material = {key: value for key, value in plan.items() if key != 'transaction_id'}
    return 'txn-' + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]


def validate_plan(plan, target=DEFAULT):
    reject_forbidden(plan)
    load_target(target)
    expected = set(TOP) - {'feature_ids'} | {'schema', 'features', 'config_schema'}
    if not isinstance(plan, dict) or set(plan) != expected or plan.get('schema') != SCHEMA:
        fail('invalid v3 target plan schema')
    fixed = {'format': 'libreecho-ota-v3', 'manifest_version': 1, 'board': target,
             'soc': 'mt8163', 'architecture': 'armv7', 'image_profile': 'ota',
             'transaction_type': 'system', 'service_profile': 'production',
             'commit_policy': 'after-slot-confirm', 'minimum_updater_schema': 3,
             'boot_filename': 'boot.img', 'boot_size': 16777216}
    if any(type(plan.get(k)) is not type(v) or plan.get(k) != v for k, v in fixed.items()):
        fail('invalid v3 target identity or policy')
    if (not isinstance(plan['release'], str) or not TAG.fullmatch(plan['release'])
            or not isinstance(plan['version'], str) or not VERSION.fullmatch(plan['version'])
            or plan['update_channel'] not in ('dev', 'stable')
            or type(plan['config_schema']) is not int or not 1 <= plan['config_schema'] < 2**31
            or not isinstance(plan['boot_sha256'], str) or not HEX64.fullmatch(plan['boot_sha256'])):
        fail('invalid v3 release, version, config schema or boot hash')
    if plan['release'].startswith('radar-puffin-v') and plan['release'] != 'radar-puffin-v' + plan['version']:
        fail('stable release tag/version mismatch')
    records = plan['features']
    if not isinstance(records, list) or [r.get('feature_id') for r in records if isinstance(r, dict)] != list(FEATURES):
        fail('v3 target must contain exactly five ordered features')
    slug = load_target(target)['release_slug']
    for r in records:
        fid = r['feature_id']
        if set(r) != set(FIELDS) | {'feature_id'}:
            fail('invalid v3 feature fields')
        for key in ('sha256', 'manifest_sha256', 'daemon_sha256'):
            if not isinstance(r[key], str) or not HEX64.fullmatch(r[key]):
                fail('invalid v3 feature hash')
        for key in ('size', 'manifest_size'):
            if type(r[key]) is not int or not 0 < r[key] < 2**63:
                fail('invalid v3 feature size')
        if (r['asset'] != f'libreecho-{slug}-base-{fid}-{r["sha256"]}.payload.squashfs'
                or r['manifest_asset'] != f'libreecho-{slug}-base-{fid}-{r["manifest_sha256"]}.manifest.json'
                or r['daemon_path'] != DAEMONS[fid]):
            fail('v3 asset must be content-addressed and daemon path canonical')
    if plan['transaction_id'] != transaction_id(plan):
        fail('v3 transaction identity mismatch')
    return records


def emit_manifest(plan):
    records = validate_plan(plan, plan.get('board', DEFAULT))
    ordered = [(key, ','.join(FEATURES) if key == 'feature_ids' else plan[key]) for key in TOP]
    for r in records:
        ordered.extend((f'feature_{r["feature_id"]}_{key}', r[key]) for key in FIELDS)
    ordered.append(('config_schema', plan['config_schema']))
    return ''.join(f'{key}={value}\n' for key, value in ordered).encode('ascii')


def asset_records(plan):
    records = validate_plan(plan, plan['board'])
    return sorted([{'feature_id': r['feature_id'], 'kind': kind,
                    'name': r['asset' if kind == 'payload' else 'manifest_asset'],
                    'size': r['size' if kind == 'payload' else 'manifest_size'],
                    'sha256': r['sha256' if kind == 'payload' else 'manifest_sha256']}
                   for r in records for kind in ('payload', 'manifest')], key=lambda r: r['name'])


def validate_assets(plan, assets):
    for item in asset_records(plan):
        if digest(assets / item['name']) != (item['sha256'], item['size']):
            fail('v3 asset identity mismatch')
    for r in plan['features']:
        feature = load_json(assets / r['manifest_asset'], 'target feature manifest')
        files = validate_file_records(feature.get('files'), 'target feature manifest')
        if (feature.get('schema_version') != 1 or feature.get('feature_id') != r['feature_id']
                or feature.get('format') != 'squashfs-lz4'
                or feature.get('payload', {}).get('sha256') != r['sha256']
                or feature.get('payload', {}).get('size') != r['size']
                or files.get(r['daemon_path'], {}).get('sha256') != r['daemon_sha256']):
            fail('v3 feature manifest/payload/daemon identity mismatch')


def parse_manifest(raw):
    if not isinstance(raw, bytes) or len(raw) > 65536 or not raw.endswith(b'\n'):
        fail('v3 manifest framing invalid')
    try:
        fields = {}
        for line in raw.decode('ascii')[:-1].split('\n'):
            if line.count('=') != 1:
                fail('v3 manifest line invalid')
            key, value = line.split('=')
            if key in fields or not value:
                fail('v3 manifest duplicate or empty field')
            fields[key] = value
        reject_forbidden(fields)
        keys = list(TOP) + [f'feature_{fid}_{key}' for fid in FEATURES for key in FIELDS] + ['config_schema']
        if list(fields) != keys or fields['feature_ids'] != ','.join(FEATURES):
            fail('v3 manifest unknown, missing or reordered fields')
        def number(value):
            if not re.fullmatch(r'[1-9][0-9]*', value):
                fail('v3 manifest integer not canonical')
            return int(value)
        plan = {'schema': SCHEMA, **{key: fields[key] for key in TOP if key != 'feature_ids'}}
        for key in ('manifest_version', 'minimum_updater_schema', 'boot_size'):
            plan[key] = number(plan[key])
        plan['config_schema'] = number(fields['config_schema'])
        plan['features'] = []
        for fid in FEATURES:
            record = {'feature_id': fid, **{key: fields[f'feature_{fid}_{key}'] for key in FIELDS}}
            for key in ('size', 'manifest_size'):
                record[key] = number(record[key])
            plan['features'].append(record)
        validate_plan(plan, plan['board'])
        if emit_manifest(plan) != raw:
            fail('v3 manifest not canonical')
        return plan
    except (UnicodeDecodeError, KeyError, ValueError, TypeError) as exc:
        fail(f'v3 manifest rejected: {exc}')


def inventory_for(plan):
    return {'schema': INVENTORY_SCHEMA, 'board': plan['board'], 'release': plan['release'], 'assets': asset_records(plan)}


def load_contract(run, candidate):
    plan = load_json(run / 'feature-plan.json', 'v3 target plan')
    validate_plan(plan, candidate.get('board', DEFAULT))
    if (candidate.get('ota_format') != 'v3' or candidate.get('ota_release') != plan['version']
            or candidate.get('update_channel') != plan['update_channel']):
        fail('v3 candidate identity mismatch')
    if digest(run / 'boot.img') != (plan['boot_sha256'], plan['boot_size']):
        fail('v3 candidate boot identity mismatch')
    digest(run / 'target.manifest')
    if (run / 'target.manifest').read_bytes() != emit_manifest(plan):
        fail('v3 candidate target serialization mismatch')
    inventory = load_json(run / 'feature-assets.json', 'v3 target inventory')
    if inventory != inventory_for(plan):
        fail('v3 candidate inventory mismatch')
    assets = run / 'ota-assets'
    if assets.is_symlink() or not assets.is_dir() or {p.name for p in assets.iterdir()} != {r['name'] for r in inventory['assets']}:
        fail('v3 candidate asset set mismatch')
    validate_assets(plan, assets)
    return plan, inventory, assets, inventory['assets']


def validate_control(path, public_key, release, *, feature_plan, feature_inventory,
                     feature_asset_dir, expected_channel, boot_path,
                     expected_key_sha256, expected_target=DEFAULT):
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
    expected_public_key_sha256(public_key, expected_key_sha256)
    digest(path)
    try:
        with tarfile.open(path, 'r:') as archive:
            members = archive.getmembers()
            if ([m.name for m in members] != ['manifest', 'manifest.sig', 'boot.img']
                    or any(not m.isfile() for m in members)
                    or not 0 < members[0].size <= 65536 or members[1].size != 129
                    or members[2].size != 16777216):
                fail('v3 control member set or sizes invalid')
            raw, signature, boot = [archive.extractfile(m).read() for m in members]
        if not re.fullmatch(rb'[0-9a-f]{128}\n', signature):
            fail('v3 control signature framing invalid')
        key = public_key.read_text(encoding='ascii')
        if not re.fullmatch(r'[0-9a-f]{64}\n?', key):
            fail('v3 public key malformed')
        VerifyKey(bytes.fromhex(key.strip())).verify(raw, bytes.fromhex(signature[:-1].decode('ascii')))
        plan = parse_manifest(raw)
        if (plan != feature_plan or plan['board'] != expected_target or plan['version'] != release
                or plan['update_channel'] != expected_channel or feature_inventory != inventory_for(plan)):
            fail('v3 signed control differs from target contract')
        if (not boot.startswith(b'ANDROID!') or hashlib.sha256(boot).hexdigest() != plan['boot_sha256']
                or boot_path is None or digest(boot_path) != (plan['boot_sha256'], plan['boot_size'])):
            fail('v3 signed boot identity mismatch')
        validate_assets(plan, feature_asset_dir)
    except (OSError, ValueError, tarfile.TarError, BadSignatureError) as exc:
        fail(f'v3 control verification failed: {exc}')


def sign(run, signing_key, public_key, output, expected_key_sha256):
    from nacl.signing import SigningKey
    plan = load_json(run / 'feature-plan.json', 'v3 target plan')
    plan, inventory, assets, _ = load_contract(run, {'board': plan['board'], 'ota_format': 'v3',
        'ota_release': plan['version'], 'update_channel': plan['update_channel']})
    expected_public_key_sha256(public_key, expected_key_sha256)
    digest(signing_key)
    text = signing_key.read_text(encoding='ascii').strip()
    # Platform accepts either a seed or libsodium's seed+public-key secret.
    if not re.fullmatch(r'(?:[0-9a-f]{64}|[0-9a-f]{128})', text):
        fail('v3 signing key malformed')
    key = SigningKey(bytes.fromhex(text[:64]))
    derived = key.verify_key.encode().hex()
    if derived != public_key.read_text(encoding='ascii').strip() or (len(text) == 128 and text[64:] != derived):
        fail('v3 signing key identity mismatch')
    raw = emit_manifest(plan)
    boot = (run / 'boot.img').read_bytes()
    if not boot.startswith(b'ANDROID!'):
        fail('v3 boot must be Android image')
    with tarfile.open(output, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, blob in [('manifest', raw), ('manifest.sig', key.sign(raw).signature.hex().encode() + b'\n'), ('boot.img', boot)]:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(blob), 0o644
            archive.addfile(member, io.BytesIO(blob))
    validate_control(output, public_key, plan['version'], feature_plan=plan,
        feature_inventory=inventory, feature_asset_dir=assets, expected_channel=plan['update_channel'],
        boot_path=run / 'boot.img', expected_key_sha256=expected_key_sha256, expected_target=plan['board'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--signing-key', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--expected-key-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        sign(args.run, args.signing_key, args.public_key, args.output, args.expected_key_sha256)
        return 0
    except (ContractError, OSError, ValueError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
