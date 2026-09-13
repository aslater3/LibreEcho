"""Classify a verified build request before selecting a publisher."""
import argparse
import json
import re
from pathlib import Path


def route(request, branch, event):
    if not isinstance(request, dict) or request.get('schema') != 'libreecho-release-request-v1':
        raise ValueError('invalid release request')
    if event not in ('push', 'schedule', 'workflow_dispatch'):
        raise ValueError('unsupported publication event')
    release = re.fullmatch(r'release/(\d+\.\d+\.\d+)', branch)
    if branch != 'main' and not release:
        raise ValueError('unsupported publication branch')
    channel = request.get('channel')
    if channel == 'dev':
        if any(request.get(key) for key in ('version', 'release_tag', 'release_notes')):
            raise ValueError('dev request contains stable metadata')
        # Release-branch push validation is not publication authorization.
        return 'dev' if branch == 'main' or event == 'workflow_dispatch' else 'none'
    if channel == 'stable':
        if not release or event != 'workflow_dispatch':
            raise ValueError('stable publication requires manual release branch')
        version = release.group(1)
        if request.get('version') != version or request.get('release_tag') != 'radar-puffin-v' + version:
            raise ValueError('stable identity mismatch')
        for key in ('release_notes', 'amonet_repository', 'amonet_tag', 'amonet_commit', 'ssh_enabled'):
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
