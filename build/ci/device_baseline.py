"""Explicit maintainer-supplied dev migration identities, not release provenance.

The target must still verify every preserved byte; this input never authorizes
runtime capsules, stable signing, or publishing a release from device data.
Wakeword remains preserved unless the hash-bound JSON explicitly includes
``"replace_wakeword": true`` to adopt the validated Product candidate payload.
Omit the field to retain the previous behavior; strings and false are rejected.
"""
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA = 'libreecho-dev-device-baseline-v1'
FEATURES = ('airplay2', 'tts', 'wakeword', 'stt', 'assistant')
FIELDS = {'payload_sha256', 'manifest_sha256', 'daemon_sha256'}
MAX_BYTES = 8192


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate baseline key')
        result[key] = value
    return result


def parse(text, channel):
    if channel != 'dev':
        raise ValueError('device baseline requires dev channel')
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('device baseline exceeds limit')
    data = json.loads(text, object_pairs_hook=pairs)
    allowed_keys = ({'schema', 'features'}, {'schema', 'features', 'replace_wakeword'})
    if not isinstance(data, dict) or set(data) not in allowed_keys or data['schema'] != SCHEMA:
        raise ValueError('device baseline schema mismatch')
    if 'replace_wakeword' in data and data['replace_wakeword'] is not True:
        raise ValueError('replace_wakeword must be the explicit boolean true')
    features = data['features']
    if not isinstance(features, dict) or set(features) != set(FEATURES):
        raise ValueError('device baseline requires all five features')
    for record in features.values():
        if not isinstance(record, dict) or set(record) != FIELDS:
            raise ValueError('device baseline record mismatch')
        for value in record.values():
            if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
                raise ValueError('device baseline hash malformed')
    return data


def validate_plan(data, plan):
    records = plan['features']
    if len(records) != len(FEATURES) or {r['feature_id'] for r in records} != set(FEATURES):
        raise ValueError('device migration feature set mismatch')
    for record in records:
        base = data['features'][record['feature_id']]
        if (record['base_payload_sha256'] != base['payload_sha256'] or
                record['base_manifest_sha256'] != base['manifest_sha256']):
            raise ValueError('device migration baseline binding mismatch')
        if record['action'] not in {'preserve', 'replace'}:
            raise ValueError('device migration forbids runtime capsules')
        if record['action'] == 'preserve' and record['daemon_sha256'] != base['daemon_sha256']:
            raise ValueError('device migration preserved daemon mismatch')
        if (record['feature_id'] == 'wakeword' and record['action'] != 'preserve'
                and data.get('replace_wakeword') is not True):
            raise ValueError('device migration must preserve wakeword without explicit opt-in')


def main():
    text = os.environ['DEVICE_BASELINE_JSON']
    if os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or os.environ.get('OTA_FORMAT') != 'v2':
        raise ValueError('device baseline requires manual v2 dispatch')
    data = parse(text, os.environ['UPDATE_CHANNEL'])
    path = Path(os.environ['RUNNER_TEMP']) / 'device-baseline.json'
    encoded = (json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n').encode()
    with path.open('xb') as stream:
        stream.write(encoded)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write(f'catalog={path}\nsha256={hashlib.sha256(encoded).hexdigest()}\n')


if __name__ == '__main__':
    main()
