"""Dev-only identity override gates and actual planner regression."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ci'))
import device_baseline as device
from build.tests.test_plan_feature_transaction import write_catalog, run_plan, COMMIT


class DeviceBaselineTests(unittest.TestCase):
    def baseline(self):
        return {'schema': device.SCHEMA, 'features': {
            f: {k: 'a' * 64 for k in device.FIELDS} for f in device.FEATURES}}

    def test_strict_schema_and_dev_channel(self):
        data = self.baseline()
        self.assertEqual(device.parse(json.dumps(data), 'dev'), data)
        invalid = [dict(data, serial='private'), dict(data, schema='unknown')]
        missing = copy.deepcopy(data)
        del missing['features']['tts']
        invalid.append(missing)
        bad = copy.deepcopy(data)
        bad['features']['tts']['payload_sha256'] = 'A' * 64
        invalid.append(bad)
        for value in invalid:
            with self.assertRaises(ValueError):
                device.parse(json.dumps(value), 'dev')
        for text, channel in [(json.dumps(data), 'stable'), (' ' * 8193, 'dev'),
                              ('{"schema":1,"schema":2}', 'dev')]:
            with self.assertRaises(ValueError):
                device.parse(text, channel)

    def test_workflow_guard_rejects_non_dev_non_v2_and_nonmanual(self):
        import os
        import subprocess
        import textwrap
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/build-release.yml').read_text()
        block = workflow.split('      - id: resolve', 1)[1].split('        run: |', 1)[1]
        guard = textwrap.dedent(block.split('          if [[ "$GITHUB_EVENT_NAME" == pull_request ]]; then', 1)[0])
        env = dict(os.environ, DEVICE_BASELINE_JSON=json.dumps(self.baseline()),
                   GITHUB_EVENT_NAME='workflow_dispatch', RELEASE_CHANNEL='dev',
                   OTA_FORMAT_INPUT='v2', GITHUB_REF='refs/heads/release/0.13.14')
        self.assertEqual(subprocess.run(['bash', '-c', guard], env=env, cwd=root, capture_output=True, timeout=10).returncode, 0)
        for key, value in [('RELEASE_CHANNEL', 'stable'), ('OTA_FORMAT_INPUT', 'v1'),
                           ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_REF', 'refs/heads/main'),
                           ('DEVICE_BASELINE_JSON', '{"serial":"private"}')]:
            result = subprocess.run(['bash', '-c', guard], env=env | {key: value}, cwd=root, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0, key)
        self.assertIn('build.tests.test_device_baseline', workflow)

    def test_explicit_wakeword_replacement_is_strict_and_dev_only(self):
        data = dict(self.baseline(), replace_wakeword=True)
        self.assertEqual(device.parse(json.dumps(data), 'dev'), data)
        with self.assertRaises(ValueError):
            device.parse(json.dumps(data), 'stable')
        for value in (False, 0, 1, 'true', None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                device.parse(json.dumps(dict(data, replace_wakeword=value)), 'dev')

    def test_real_planner_stages_only_explicit_wakeword_replacement(self):
        from build.tests.test_plan_feature_transaction import DAEMONS, digest
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = write_catalog(root, 'candidate')
            catalog = json.loads(candidate.read_text())['features']
            baseline = self.baseline()
            for fid, entry in catalog.items():
                manifest = json.loads(Path(entry['manifest']['path']).read_text())
                baseline['features'][fid] = {
                    'payload_sha256': entry['payload']['sha256'],
                    'manifest_sha256': entry['manifest']['sha256'],
                    'daemon_sha256': manifest['files'][DAEMONS[fid]]['sha256'],
                }
            baseline['features']['wakeword'] = {k: 'a' * 64 for k in device.FIELDS}
            baseline['replace_wakeword'] = True
            base = root / 'device.json'
            base.write_text(json.dumps(baseline))
            output, assets = root / 'plan.json', root / 'ota-assets'
            args = ['--base-catalog', str(base), '--candidate-catalog', str(candidate),
                    '--release', '0.13.14', '--source-commit', COMMIT,
                    '--output', str(output), '--asset-output-dir', str(assets)]
            rejected = run_plan(*args, '--update-channel', 'stable')
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(assets.exists())
            accepted = run_plan(*args, '--update-channel', 'dev')
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            plan = json.loads(output.read_text())
            device.validate_plan(baseline, plan)
            self.assertEqual({r['feature_id']: r['action'] for r in plan['features']},
                             {f: 'replace' if f == 'wakeword' else 'preserve' for f in device.FEATURES})
            wake = next(r for r in plan['features'] if r['feature_id'] == 'wakeword')
            self.assertEqual({p.name for p in assets.iterdir()}, {wake['asset'], wake['manifest_asset']})
            for name, key in ((wake['asset'], 'sha256'), (wake['manifest_asset'], 'manifest_sha256')):
                self.assertEqual(digest(assets / name), wake[key])
            for flag in (False, True):
                altered = copy.deepcopy(plan)
                record = next(r for r in altered['features'] if r['feature_id'] == 'wakeword')
                if flag:
                    record['base_payload_sha256'] = 'b' * 64
                else:
                    record['action'] = 'runtime'
                with self.assertRaises(ValueError):
                    device.validate_plan(baseline, altered)
            without_opt_in = copy.deepcopy(baseline)
            del without_opt_in['replace_wakeword']
            with self.assertRaises(ValueError):
                device.validate_plan(without_opt_in, plan)

    def test_real_planner_preserves_excluded_wakeword(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = write_catalog(root, 'candidate')
            base = root / 'device.json'
            base.write_text(json.dumps(self.baseline()))
            output = root / 'plan.json'
            args = ['--base-catalog', str(base), '--candidate-catalog', str(candidate),
                    '--release', '0.13.14', '--source-commit', COMMIT, '--output', str(output)]
            rejected = run_plan(*args)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(output.exists())
            accepted = run_plan(*args, '--update-channel', 'dev')
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            plan = json.loads(output.read_text())
            device.validate_plan(self.baseline(), plan)
            self.assertEqual({r['feature_id']: r['action'] for r in plan['features']},
                             {f: 'preserve' if f == 'wakeword' else 'replace' for f in device.FEATURES})
            for mutate in ('base', 'daemon', 'runtime', 'wakeword'):
                altered = copy.deepcopy(plan)
                record = next(r for r in altered['features'] if r['feature_id'] == 'wakeword')
                if mutate == 'base': record['base_payload_sha256'] = 'b' * 64
                if mutate == 'daemon': record['daemon_sha256'] = 'b' * 64
                if mutate == 'runtime': record['action'] = 'runtime'
                if mutate == 'wakeword': record['action'] = 'replace'
                with self.assertRaises(ValueError): device.validate_plan(self.baseline(), altered)
            runtime = run_plan(*args, '--update-channel', 'dev', '--runtime-dir', str(root))
            self.assertNotEqual(runtime.returncode, 0)


if __name__ == '__main__':
    unittest.main()
