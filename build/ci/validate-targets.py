#!/usr/bin/env python3
"""Validate Product targets and emit their canonical digests or shell values."""
import argparse
import json
from target_registry import load_target, parse_targets, descriptor_sha256, require_shared_kernel

p = argparse.ArgumentParser()
p.add_argument('--targets', default='radar_puffin,biscuit')
p.add_argument('--shell', action='store_true')
a = p.parse_args()
try:
    targets = parse_targets(a.targets)
    # One run compiles one kernel, so both output paths must vet the whole set.
    require_shared_kernel(targets)
    for target in targets:
        d = load_target(target)
        digest = descriptor_sha256(target)
        if a.shell:
            print('TARGET=' + target)
            print('TARGET_DESCRIPTOR_SHA256=' + digest)
            print('KERNEL_DEFCONFIG=' + d['kernel']['defconfig'])
            print('KERNEL_DTB_NAME=' + d['kernel']['dtb'])
            print('KERNEL_ARCH=' + {'arm32': 'arm', 'arm64': 'arm64'}[d['arch']])
            print('DTB_VERIFIER_ID=' + d['platform']['dtb_verifier'])
            print('TOOLS_PROFILE=' + d['platform']['tools_profile'])
        else:
            print(json.dumps({'board': target, 'target_descriptor_sha256': digest}, sort_keys=True))
except ValueError as exc:
    p.error(str(exc))
