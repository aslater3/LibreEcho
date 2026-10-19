"""Classify a verified build request before selecting a publisher."""
import argparse
import json
import re
from pathlib import Path
if __package__:
    from .target_registry import parse_targets
else:
    from target_registry import parse_targets


def route(request, branch, event):
    if not isinstance(request, dict) or request.get('schema') != 'libreecho-release-request-v1':
        raise ValueError('invalid release request')
    purpose = request.get('purpose')
    if purpose not in ('sandbox', 'dev', 'prd'):
        raise ValueError('missing or invalid build purpose')
    if purpose == 'sandbox':
        return 'none'
    channel = request.get('channel')
    if channel != ('stable' if purpose == 'prd' else 'dev'):
        raise ValueError('purpose/channel mismatch')
    targets = parse_targets(request.get('targets', ['radar_puffin']))
    if 'board' in request and request['board'] not in targets:
        raise ValueError('release request target mismatch')
    if event not in ('push', 'schedule', 'workflow_dispatch'):
        raise ValueError('unsupported publication event')
    release = re.fullmatch(r'release/(\d+\.\d+\.\d+)', branch)
    if branch != 'main' and not release:
        raise ValueError('unsupported publication branch')
    if purpose == 'dev':
        if any(request.get(key) for key in ('version', 'release_tag', 'release_notes')):
            raise ValueError('dev request contains stable metadata')
        if event != 'workflow_dispatch':
            raise ValueError('dev publication requires manual dispatch')
        return 'dev'
    if purpose == 'prd':
        if not release or event != 'workflow_dispatch':
            raise ValueError('stable publication requires manual release branch')
        version = release.group(1)
        if request.get('version') != version or request.get('release_tag') != 'radar-puffin-v' + version:
            raise ValueError('stable identity mismatch')
        for key in ('release_notes', 'ssh_enabled'):
            if not request.get(key):
                raise ValueError('missing stable field: ' + key)
        return 'stable'
    raise ValueError('invalid release channel')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--event', required=True)
    args = parser.parse_args()
    paths = list(args.artifact_root.rglob('release-request.json'))
    if len(paths) != 1 or paths[0].is_symlink() or paths[0].stat().st_size > 8192:
        raise SystemExit('expected one bounded release request')
    print('channel=' + route(json.loads(paths[0].read_text()), args.branch, args.event))
