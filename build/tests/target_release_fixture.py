"""Migrate synthetic packaging fixtures to a complete release-owned v3 target."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

CI = Path(__file__).resolve().parents[1] / 'ci'
sys.path.insert(0, str(CI))
from ota_v2_product import DAEMONS, FEATURES, digest
from ota_v3_product import sign


def add_target_contract(run, *, channel='dev', tag=None, target='radar_puffin'):
    candidate = dict(line.split('=', 1) for line in (run / 'CURRENT.candidate').read_text().splitlines() if '=' in line)
    tag = tag or ('radar-puffin-v0.14.0' if channel == 'stable' else 'radar-puffin-build-' + candidate['product_git_head'][:7] + '-' + 'b'*16 + '-' + 'c'*16)
    catalog = {'board': target, 'features': {}}
    for fid in FEATURES:
        payload = run / 'features' / (fid + '.squashfs')
        manifest = run / 'features' / (fid + '.manifest.json')
        sha, size = digest(payload)
        manifest.write_text(json.dumps({'schema_version': 1, 'feature_id': fid, 'format': 'squashfs-lz4', 'payload': {'filename': payload.name, 'sha256': sha, 'size': size}, 'files': {DAEMONS[fid]: {'sha256': 'c'*64}}}, sort_keys=True) + '\n')
        catalog['features'][fid] = {kind: {'path': str(path), 'sha256': digest(path)[0], 'size': digest(path)[1]} for kind, path in [('payload', payload), ('manifest', manifest)]}
        prefix = 'airplay' if fid == 'airplay2' else fid
        candidate[prefix + '_feature_manifest_sha256'] = digest(manifest)[0]
    catalog_path = run / 'candidate-catalog.json'
    catalog_path.write_text(json.dumps(catalog))
    result = subprocess.run([sys.executable, '-B', str(CI / 'plan-target-manifest.py'), '--target', target, '--candidate-catalog', str(catalog_path), '--boot-image', str(run / 'boot.img'), '--release', tag, '--version', '0.14.0', '--update-channel', channel, '--output', str(run / 'target.manifest'), '--asset-output-dir', str(run / 'ota-assets')], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    candidate.update({'board': target, 'ota_format': 'v3', 'ota_release': '0.14.0', 'update_channel': channel,
        'feature_plan': str(run / 'feature-plan.json'), 'feature_asset_inventory': str(run / 'feature-assets.json'), 'feature_asset_dir': str(run / 'ota-assets')})
    from target_registry import descriptor_sha256
    candidate['target_descriptor_sha256'] = descriptor_sha256(target)
    image_path = run / 'manifest.json'
    image = json.loads(image_path.read_text())
    image.update(board=target, target_descriptor_sha256=descriptor_sha256(target))
    image_path.write_text(json.dumps(image))
    key = next((p for p in (run / 'fixture-signing-key.hex', run / 'private.hex') if p.is_file()), None)
    if key is None:
        from nacl.signing import SigningKey
        signing = SigningKey(bytes.fromhex('12'*32))
        key = run / 'fixture-signing-key.hex'
        key.write_text(signing.encode().hex() + '\n')
        (run / 'ota-public-key.hex').write_text(signing.verify_key.encode().hex() + '\n')
    candidate['ota_signing_mode'] = 'local'
    candidate['ota_bundle'] = candidate.get('ota_bundle') or str(run / 'development.ota.tar')
    ota = Path(candidate['ota_bundle'])
    sign(run, key, run / 'ota-public-key.hex', ota, digest(run / 'ota-public-key.hex')[0])
    candidate['ota_bundle_sha256'] = digest(ota)[0]
    (run / 'CURRENT.candidate').write_text(''.join(f'{k}={v}\n' for k, v in candidate.items()))
    return json.loads((run / 'feature-plan.json').read_text())
