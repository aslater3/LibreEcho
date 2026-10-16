#!/usr/bin/env python3
"""Emit one deterministic, release-owned whole target state (no baseline inputs)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys

from ota_v2_product import ContractError, DAEMONS, FEATURES, digest, fail, load_json, validate_file_records
from ota_v3_product import SCHEMA, INVENTORY_SCHEMA, asset_records, emit_manifest, transaction_id
from target_registry import DEFAULT, load_target


def plan(catalog_path, boot, *, target, release, version, update_channel, config_schema):
    catalog = load_json(catalog_path, 'candidate catalog')
    if not isinstance(catalog, dict) or catalog.get('board', DEFAULT) != target or set(catalog.get('features', {})) != set(FEATURES):
        fail('candidate catalog target/features mismatch')
    slug = load_target(target)['release_slug']
    records, sources = [], {}
    for fid in FEATURES:
        entry = catalog['features'][fid]
        if not isinstance(entry, dict) or set(entry) != {'payload', 'manifest'}:
            fail('candidate feature record malformed')
        checked = {}
        for kind in ('payload', 'manifest'):
            ref = entry[kind]
            if not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'size'}:
                fail('candidate asset record malformed')
            source = Path(ref['path'])
            sha, size = digest(source)
            if type(ref['size']) is not int or (sha, size) != (ref['sha256'], ref['size']):
                fail(f'candidate {fid} {kind} identity mismatch')
            checked[kind] = (source, sha, size)
        source, psha, psize = checked['payload']
        msource, msha, msize = checked['manifest']
        manifest = load_json(msource, 'candidate feature manifest')
        files = validate_file_records(manifest.get('files'), 'candidate feature manifest')
        if (manifest.get('schema_version') != 1 or manifest.get('feature_id') != fid
                or manifest.get('format') != 'squashfs-lz4'
                or manifest.get('payload') != {'filename': source.name, 'sha256': psha, 'size': psize}
                or DAEMONS[fid] not in files):
            fail(f'candidate {fid} feature identity mismatch')
        pname = f'libreecho-{slug}-base-{fid}-{psha}.payload.squashfs'
        mname = f'libreecho-{slug}-base-{fid}-{msha}.manifest.json'
        records.append({'feature_id': fid, 'asset': pname, 'size': psize, 'sha256': psha,
                        'manifest_asset': mname, 'manifest_size': msize, 'manifest_sha256': msha,
                        'daemon_path': DAEMONS[fid], 'daemon_sha256': files[DAEMONS[fid]]['sha256']})
        sources[pname], sources[mname] = source, msource
    boot_sha, boot_size = digest(boot)
    result = {'schema': SCHEMA, 'format': 'libreecho-ota-v3', 'manifest_version': 1,
              'board': target, 'soc': 'mt8163', 'architecture': 'armv7', 'image_profile': 'ota',
              'transaction_type': 'system', 'release': release, 'version': version,
              'update_channel': update_channel, 'service_profile': 'production',
              'commit_policy': 'after-slot-confirm', 'minimum_updater_schema': 3,
              'boot_filename': 'boot.img', 'boot_size': boot_size, 'boot_sha256': boot_sha,
              'features': records, 'config_schema': config_schema}
    result['transaction_id'] = transaction_id(result)
    emit_manifest(result)
    return result, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--target', default=DEFAULT)
    parser.add_argument('--candidate-catalog', type=Path, required=True)
    parser.add_argument('--boot-image', type=Path, required=True)
    parser.add_argument('--release', required=True, help='Immutable release tag, not SemVer')
    parser.add_argument('--version', required=True)
    parser.add_argument('--update-channel', choices=('dev', 'stable'), default='dev')
    parser.add_argument('--config-schema', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--asset-output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        result, sources = plan(args.candidate_catalog, args.boot_image, target=args.target,
                               release=args.release, version=args.version,
                               update_channel=args.update_channel, config_schema=args.config_schema)
        records = asset_records(result)
        if args.asset_output_dir.exists() or args.asset_output_dir.is_symlink():
            fail('target asset output already exists')
        args.asset_output_dir.mkdir(parents=True)
        for ref in records:
            shutil.copyfile(sources[ref['name']], args.asset_output_dir / ref['name'])
            if digest(args.asset_output_dir / ref['name']) != (ref['sha256'], ref['size']):
                fail('copied target asset identity mismatch')
        from release_completeness import SCHEMA as COMPLETENESS_SCHEMA, provenance_name
        refs = [{**ref, 'role': 'target', 'source_release': args.release, 'source_asset': ref['name']} for ref in records]
        data = {'schema': COMPLETENESS_SCHEMA, 'target': args.target, 'references': refs}
        for name, value in [('feature-plan.json', result), ('feature-assets.json', {'schema': INVENTORY_SCHEMA, 'board': args.target, 'release': args.release, 'assets': records}), (provenance_name(args.target), data)]:
            (args.output.parent / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        args.output.write_bytes(emit_manifest(result))
        print(f'target_manifest={args.output}')
        return 0
    except (ContractError, OSError, ValueError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
