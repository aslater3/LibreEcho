#!/usr/bin/env python3
"""Resolve one authenticated prior feature catalog per selected target.

A target with no published release of its own may be bootstrapped only when
the operator names it explicitly in ``--bootstrap-targets``. Bootstrap is
fail-closed: it is refused for the legacy default target, for targets not
selected in this build, when a prior target-bound baseline actually exists,
and for any baseline failure other than the exact "no preceding release"
result (network, digest or inventory errors never fall through to bootstrap).
A bootstrapped target's base is its own current build, bound later by
build.sh and release_completeness.py to the candidate's own feature hashes.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from target_registry import DEFAULT, parse_targets

NO_BASELINE = 'ValueError: no preceding stable release'


def fetch(target, release, channel, output):
    return subprocess.run([sys.executable, str(Path(__file__).with_name('fetch-ota-base.py')),
        '--target', target, '--release', release, '--channel', channel,
        '--output-dir', str(output)], text=True, capture_output=True, timeout=540)


def resolve(targets, bootstrap, release, channel, output_dir, runner=fetch):
    targets = parse_targets(targets)
    bootstrap = parse_targets(bootstrap) if bootstrap else []
    if DEFAULT in bootstrap:
        raise SystemExit(f'ERROR: {DEFAULT} always has a published baseline; bootstrap refused')
    if not set(bootstrap) <= set(targets):
        raise SystemExit('ERROR: bootstrap target is not selected in this build')
    output_dir.mkdir(parents=True, exist_ok=False)
    records = {}
    for target in targets:
        result = runner(target, release, channel, output_dir / target)
        if target in bootstrap:
            if result.returncode == 0:
                raise SystemExit(f'ERROR: {target} already has a published baseline; bootstrap refused, drop it from bootstrap_targets')
            if result.stderr.strip().splitlines()[-1:] != [NO_BASELINE]:
                raise SystemExit(f'ERROR: {target} baseline lookup failed for a reason other than no prior release; bootstrap refused. {result.stderr}')
            records[target] = {'bootstrap': '1'}
            continue
        if result.returncode:
            raise SystemExit(f'ERROR: {target} OTA v2 baseline unavailable; name it in bootstrap_targets only if it has never been released. {result.stderr}')
        records[target] = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    index = output_dir / 'catalogs.json'
    index.write_text(json.dumps(records, sort_keys=True) + '\n')
    return records, index


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--targets', default='radar_puffin')
    p.add_argument('--bootstrap-targets', default='')
    p.add_argument('--release', required=True)
    p.add_argument('--channel', required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--github-output', type=Path, required=True)
    a = p.parse_args()
    records, index = resolve(a.targets, a.bootstrap_targets, a.release, a.channel, a.output_dir)
    first = records[parse_targets(a.targets)[0]]
    if 'catalog' not in first:
        raise SystemExit('ERROR: the first selected target must have a published baseline')
    with a.github_output.open('a') as output:
        output.write(f'catalog={first["catalog"]}\nsha256={first["sha256"]}\ncatalogs={index}\n')


if __name__ == '__main__':
    main()
