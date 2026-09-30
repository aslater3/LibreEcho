#!/usr/bin/env python3
"""Resolve one authenticated prior feature catalog per selected target."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from target_registry import parse_targets

p = argparse.ArgumentParser()
p.add_argument('--targets', default='radar_puffin')
p.add_argument('--release', required=True)
p.add_argument('--channel', required=True)
p.add_argument('--output-dir', type=Path, required=True)
p.add_argument('--github-output', type=Path, required=True)
a = p.parse_args()
targets = parse_targets(a.targets)
a.output_dir.mkdir(parents=True, exist_ok=False)
records = {}
for target in targets:
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('fetch-ota-base.py')),
        '--target', target, '--release', a.release, '--channel', a.channel,
        '--output-dir', str(a.output_dir / target)], text=True, capture_output=True, timeout=540)
    if result.returncode:
        raise SystemExit(f'ERROR: {target} OTA v2 baseline unavailable; use explicit v1 initial-install scaffolding until a target-bound baseline exists. {result.stderr}')
    records[target] = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
index = a.output_dir / 'catalogs.json'
index.write_text(json.dumps(records, sort_keys=True) + '\n')
first = records[targets[0]]
with a.github_output.open('a') as output:
    output.write(f'catalog={first["catalog"]}\nsha256={first["sha256"]}\ncatalogs={index}\n')
