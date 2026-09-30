#!/usr/bin/env python3
"""Build target-qualified TWRP assets, retaining Radar's byte-copy aliases."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from target_registry import DEFAULT, asset_prefix, load_target, platform_target_args


def build_installs(assets, artifact_root):
    builders = list(artifact_root.rglob('twrp-builder/build_install_bundle.py'))
    if len(builders) != 1:
        raise ValueError('expected one pinned Platform recovery builder')
    builder = builders[0]
    builds = [json.loads(p.read_text()) for p in assets.glob('*-build.json')]
    seen = set()
    for build in builds:
        target = build['board']
        descriptor = load_target(target)
        if target in seen:
            raise ValueError('duplicate target build metadata')
        seen.add(target)
        tag = build.get('release')
        if not tag:
            # Dev tags are encoded in the target-qualified build basename.
            paths = [p for p in assets.glob('*-build.json') if json.loads(p.read_text())['board'] == target]
            stem = paths[0].name.removeprefix('libreecho-').removesuffix('-build.json')
            tag = stem if target == DEFAULT else 'radar-puffin-' + stem.removeprefix('biscuit-')
        prefix = asset_prefix(tag, target)
        sums = assets / (prefix + '-SHA256SUMS')
        names = []
        for line in sums.read_text().splitlines():
            digest, name = line.split('  ', 1)
            path = assets / name
            if Path(name).name != name or path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('invalid target release checksum inventory')
            names.append(name)
        with tempfile.TemporaryDirectory(prefix='twrp-target-', dir=assets.parent) as tmp:
            stage = Path(tmp)
            selected = stage / 'assets'
            selected.mkdir()
            for name in names + [sums.name]:
                shutil.copyfile(assets / name, selected / name)
            args = platform_target_args(builder, target)
            output = stage / 'out'
            command = [sys.executable, str(builder), '--assets', str(selected), '--out', str(output), '--src', str(builder.parent / 'src'), *args]
            subprocess.run(command, check=True, timeout=180)
            slug = descriptor['release_slug']
            # #199 owns this gate. When present, invoke it on the isolated target
            # asset set; do not reinterpret its base/signature/staging semantics.
            if importlib.util.find_spec('release_completeness') is not None:
                import release_completeness
                if target not in release_completeness.TARGETS:
                    raise ValueError('release_completeness.py lacks ' + target + ' support; coordinated #199 target integration required')
                bundle_manifest = output / (f'libreecho-{slug}-bundle.manifest' if args else 'bundle.manifest')
                release_completeness.check_assets(selected, bundle_manifest, target=target)
            zip_name = f'libreecho-{slug}-install.zip'
            manifest_name = f'libreecho-{slug}-bundle.manifest'
            sources = [output / zip_name, output / manifest_name]
            if not args and target == DEFAULT:
                sources = [output / 'libreecho-install.zip', output / 'bundle.manifest']
            for source, name in zip(sources, [zip_name, manifest_name]):
                if source.is_symlink() or not source.is_file():
                    raise ValueError('Platform recovery builder did not emit target-qualified assets')
                shutil.copyfile(source, assets / name)
            manifest = dict(line.split('=', 1) for line in (assets / manifest_name).read_text().splitlines() if '=' in line)
            if args and (manifest.get('target') != target or manifest.get('device') != target or manifest.get('fastboot_products') != ','.join(descriptor['fastboot_products'])):
                raise ValueError('Platform recovery manifest target mismatch')
            members = [zip_name, manifest_name]
            if target == DEFAULT:
                for name, alias in [(zip_name, 'libreecho-install.zip'), (manifest_name, 'bundle.manifest')]:
                    shutil.copyfile(assets / name, assets / alias)
                members += ['libreecho-install.zip', 'bundle.manifest']
            data = ''.join(hashlib.sha256((assets / n).read_bytes()).hexdigest() + '  ' + n + '\n' for n in members)
            # Combined tag remains product-wide; target checksum sections coexist.
            (assets / f'libreecho-{tag}-{slug}-TWRPINSTALL-SHA256SUMS').write_text(data)
            if target == DEFAULT:
                legacy = ''.join(hashlib.sha256((assets / n).read_bytes()).hexdigest() + '  ' + n + '\n' for n in ['libreecho-install.zip', 'bundle.manifest'])
                (assets / f'{prefix}-TWRPINSTALL-SHA256SUMS').write_text(legacy)
    if not seen:
        raise ValueError('no prepared targets')
    return len(list(assets.iterdir()))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--assets', type=Path, required=True)
    p.add_argument('--artifact-root', type=Path, required=True)
    a = p.parse_args()
    print('asset_count=' + str(build_installs(a.assets, a.artifact_root)))
