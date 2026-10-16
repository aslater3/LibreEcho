"""Adapter around the existing per-target stable completeness gate."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from target_registry import DEFAULT, asset_prefix, descriptor_sha256, load_target


def checksum_members(output, sums):
    if sums.is_symlink() or not sums.is_file():
        raise ValueError('missing or unsafe checksum inventory')
    names = set()
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]{0,159})', line)
        if not match or match[2] in names:
            raise ValueError('malformed or duplicate checksum record')
        path = output / match[2]
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != match[1]:
            raise ValueError('publisher checksum mismatch: ' + match[2])
        names.add(match[2])
    return names


def validate_combined_publisher(output, tag, validator, key_digest):
    index_path = output / ('libreecho-' + tag + '-targets.json')
    twrp_sums = list(output.glob('*-TWRPINSTALL-SHA256SUMS'))
    if not index_path.exists() and not twrp_sums:
        return None
    builds = [json.loads(p.read_text()) for p in output.glob('*-build.json')]
    targets = [b['board'] for b in builds]
    if not targets or len(set(targets)) != len(targets):
        raise ValueError('duplicate or absent published targets')
    expected_index = {'schema': 'libreecho-combined-release-v1', 'release': tag, 'targets': [
        {'board': t, 'prefix': asset_prefix(tag, t), 'target_descriptor_sha256': descriptor_sha256(t)}
        for t in sorted(targets, key=lambda t: (t != DEFAULT, t))]}
    claimed = set()
    if index_path.exists():
        if index_path.is_symlink() or json.loads(index_path.read_text()) != expected_index:
            raise ValueError('combined target index mismatch')
        claimed.add(index_path.name)
    elif len(targets) != 1:
        raise ValueError('combined release lacks target index')
    for target in targets:
        prefix = asset_prefix(tag, target)
        sums = output / (prefix + '-SHA256SUMS')
        members = checksum_members(output, sums)
        if claimed & members:
            raise ValueError('cross-target checksum inventory overlap')
        with tempfile.TemporaryDirectory(prefix='publisher-target-', dir=output.parent) as tmp:
            single = Path(tmp)
            for name in members | {sums.name}:
                shutil.copyfile(output / name, single / name)
            validator(single, tag, key_digest, target=target, _single=True)
        claimed |= members | {sums.name}
        slug = load_target(target)['release_slug']
        twrp = {'libreecho-' + slug + '-install.zip', 'libreecho-' + slug + '-bundle.manifest'}
        target_sums = output / f'libreecho-{tag}-{slug}-TWRPINSTALL-SHA256SUMS'
        if twrp_sums:
            expected = twrp | ({'libreecho-install.zip', 'bundle.manifest'} if target == DEFAULT else set())
            if checksum_members(output, target_sums) != expected:
                raise ValueError('target TWRP inventory mismatch')
            values = dict(line.split('=', 1) for line in (output / ('libreecho-' + slug + '-bundle.manifest')).read_text().splitlines() if '=' in line)
            if values.get('target', target) != target or values.get('device') != target:
                raise ValueError('TWRP manifest target mismatch')
            claimed |= expected | {target_sums.name}
            if target == DEFAULT:
                for named, alias in [('libreecho-radar-puffin-install.zip', 'libreecho-install.zip'), ('libreecho-radar-puffin-bundle.manifest', 'bundle.manifest')]:
                    if (output / named).read_bytes() != (output / alias).read_bytes():
                        raise ValueError('Radar legacy alias differs')
                legacy_sums = output / (prefix + '-TWRPINSTALL-SHA256SUMS')
                if checksum_members(output, legacy_sums) != {'libreecho-install.zip', 'bundle.manifest'}:
                    raise ValueError('Radar legacy TWRP inventory mismatch')
                claimed.add(legacy_sums.name)
    actual = {p.name for p in output.iterdir()}
    if actual != claimed or any(p.is_symlink() or not p.is_file() for p in output.iterdir()):
        raise ValueError('combined release contains extra or missing assets')
    return actual
