#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, re

SHA = re.compile(r"^[0-9a-f]{40}$")

def validate_sha(value: str) -> str:
    if not SHA.fullmatch(value): raise ValueError("expected lowercase 40-character SHA")
    return value

def source_set_id(values: dict[str,str]) -> str:
    ordered = [values[k] for k in ("product","platform","linux","ui")]
    return hashlib.sha256(("\n".join(ordered)+"\n").encode()).hexdigest()[:16]

def production_tag(version: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version): raise ValueError("invalid X.Y.Z version")
    return "radar-puffin-v" + version

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--product',required=True); p.add_argument('--platform',required=True); p.add_argument('--linux',required=True); p.add_argument('--ui',required=True); p.add_argument('--version',default=''); args=p.parse_args()
    values={k:validate_sha(getattr(args,k)) for k in ('product','platform','linux','ui')}
    mode='production' if args.version else 'dev'; version=args.version if args.version else 'dev-'+source_set_id(values); tag=production_tag(args.version) if args.version else 'radar-puffin-dev'
    for k,v in values.items(): print(f'{k}_sha={v}')
    print(f'source_set_id={source_set_id(values)}'); print(f'mode={mode}'); print(f'version={version}'); print(f'tag={tag}'); print(f'channel={"stable" if mode=="production" else "dev"}')
    return 0
if __name__ == '__main__': raise SystemExit(main())
