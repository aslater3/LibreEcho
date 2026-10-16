#!/usr/bin/env python3
import argparse
from pathlib import Path
from target_registry import platform_target_args
p = argparse.ArgumentParser()
p.add_argument('--tool', required=True, type=Path)
p.add_argument('--target', required=True)
p.add_argument('--descriptor')
a = p.parse_args()
try:
    print('\n'.join(platform_target_args(a.tool, a.target, a.descriptor)), end='')
except ValueError as exc:
    p.error(str(exc))
