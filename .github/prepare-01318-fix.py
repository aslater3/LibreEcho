#!/usr/bin/env python3
"""One-use, exact-preimage source patch; workflow files are not modified here."""
import hashlib
import subprocess
from pathlib import Path

path = Path('tools/libreecho-install.py')
data = path.read_bytes()
assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == '937e3273e963ab8dc025999d6fa4e854b054676a', 'installer preimage changed'
result = subprocess.run(['python3', '-B', '-m', 'unittest', 'tools.test_feature_upload_timeout.FeatureUploadTimeoutTests.test_bulk_upload_gets_900_seconds_without_extending_control_commands'], text=True, capture_output=True, timeout=60)
print(result.stdout, result.stderr)
assert result.returncode == 1 and "Expected '_run_command_with_heartbeat' to have been called once" in result.stderr, 'unexpected baseline result'
print('REGRESSION_BASELINE=FAIL_AS_EXPECTED')
source = data.decode()
old = 'USERDATA_FLASH_TIMEOUT = 900\n'
new = old + '# Bulk feature images need a separate budget from ordinary ADB commands.\nFEATURE_UPLOAD_TIMEOUT = 900\n'
assert source.count(old) == 1
source = source.replace(old, new)
old = '        _run_command([adb_bin, "-s", serial, "push", str(payload), remote_payload], timeout)\n'
new = '''        _run_command_with_heartbeat(
            [adb_bin, "-s", serial, "push", str(payload), remote_payload],
            max(timeout, FEATURE_UPLOAD_TIMEOUT),
            f"PAYLOAD STAGE: {name} upload still in progress; do not disconnect USB",
        )
'''
assert source.count(old) == 1
source = source.replace(old, new)
path.write_text(source)
digest = hashlib.sha256(path.read_bytes()).hexdigest()
Path('tools/libreecho-install.py.sha256').write_text(f'{digest}  libreecho-install.py\n')
print('INSTALLER_SHA256=' + digest)
