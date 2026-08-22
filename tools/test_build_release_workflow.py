#!/usr/bin/env python3
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).parents[1]
W=(ROOT/'.github/workflows/build-release.yml').read_text()
TOOLCHAIN=(ROOT/'build/ci/build-public-toolchain.sh').read_text()
CACHE_PIN='0400d5f644dc74513175e3cd8d07132dd4860809'
class Tests(unittest.TestCase):
 def test_hosted_only(self):
  self.assertNotIn('self-hosted',W); self.assertNotIn('Vaultwarden',W)
  self.assertIn('ports.ubuntu.com/ubuntu-ports', W)
  self.assertIn('apt-get download', W)
  self.assertIn('dpkg-deb -x', W)
  self.assertIn(f'actions/cache/restore@{CACHE_PIN}', W)
  self.assertIn(f'actions/cache/save@{CACHE_PIN}', W)
  self.assertIn('LIBREECHO_COMPONENT_CACHE_ROOT', W)
  self.assertIn('LIBREECHO_REUSE_COMPONENT_CACHE: "1"', W)
  self.assertIn('restore-keys:', W)
  self.assertIn('include-hidden-files: true', W)
  self.assertIn('Build public neural dependencies', W)
  self.assertNotIn('public-neural-deps-', W)
  for variable in (
    'LIBREECHO_WAKE_ORT_SOURCE', 'LIBREECHO_ORT_BUILD',
    'LIBREECHO_ORT_PREFIX', 'LIBREECHO_WAKE_FLATBUFFERS_PYTHON',
    'LIBREECHO_SHERPA_SOURCE', 'LIBREECHO_SHERPA_PREFIX',
    'LIBREECHO_SPEEX_PREFIX',
  ):
   self.assertIn(variable, W)
  self.assertIn('build/ci/build-public-neural-deps.sh', W)
  self.assertIn('LD_LIBRARY_PATH: ${{ runner.temp }}/armhf-root/usr/lib/x86_64-linux-gnu', W)
  self.assertIn("build/ci/build-public-neural-deps.sh', 'build/inputs/public-inputs.json'", W)
  self.assertNotIn('out/CURRENT', W)
  self.assertIn('runs-on: ubuntu-24.04',W)
 def test_cache_layers(self):
  # Every expensive, input-stable stage is content-keyed so unchanged inputs
  # restore instead of rebuilding. Keys are input fingerprints: any input
  # change (inventory, builder script, compiler bytes, kernel SHA, defconfig)
  # changes the key and forces a rebuild under the new key.
  for key in (
    'libreecho-public-deps-v1-',
    'libreecho-musl-toolchain-v1-',
    'libreecho-neural-deps-v1-',
    'libreecho-components-v6-',
    'libreecho-kernel-out-v1-',
  ):
   self.assertIn(key, W)
  # The neural cache key binds the exact compiler bytes that produced it.
  self.assertIn('steps.armhf-id.outputs.armhf_cc_sha256', W)
  # The kernel cache key binds kernel SHA, compiler bytes, and defconfig.
  self.assertIn('needs.resolve-and-preflight.outputs.linux_sha', W)
  self.assertIn('mt8163_arm32_defconfig', W)
  # Component cache is saved even when the build fails (entries are stored
  # atomically per component); the kernel object tree is saved only after a
  # complete successful build.
  self.assertIn('if: always() && steps.restore-components.outputs.cache-hit', W)
  self.assertIn('if: success() && steps.restore-kernel.outputs.cache-hit', W)
  # Restored inputs are skipped; only cold paths rebuild and re-save.
  self.assertIn("if: steps.restore-deps.outputs.cache-hit != 'true'", W)
  self.assertIn("if: steps.restore-toolchain.outputs.cache-hit != 'true'", W)
  self.assertIn("if: steps.restore-neural.outputs.cache-hit != 'true'", W)
  # Cold-path saves are gated on success so a failed fetch/build never
  # stores partial output under the real key.
  self.assertIn("if: success() && steps.restore-deps.outputs.cache-hit", W)
  self.assertIn("if: success() && steps.restore-toolchain.outputs.cache-hit", W)
  self.assertIn("if: success() && steps.restore-neural.outputs.cache-hit", W)
 def test_triggers_and_jobs(self):
  self.assertIn('branches: [main]',W); self.assertIn('workflow_dispatch:',W); self.assertIn('version:',W)
  self.assertIn('prepare-public-inputs:',W); self.assertIn('publish-dev:',W); self.assertIn('publish-production:',W)
 def test_boundaries(self):
  # Concurrency is scoped per event and ref (independent PR lanes), and
  # running builds are never cancelled.
  self.assertIn('github.head_ref || github.ref',W); self.assertIn('cancel-in-progress: false',W)
  self.assertNotIn('queue: max',W)
  self.assertIn('build/ci/build-public-release.sh',W); self.assertIn('fetch-public-deps.py',W)
  self.assertIn('GNU_SITE = https://ftp.gnu.org/gnu', TOOLCHAIN)
  self.assertIn('MUSL_SITE = https://www.musl-libc.org/releases', TOOLCHAIN)
  self.assertIn('curl -4 -L --fail --retry 5 --retry-all-errors', TOOLCHAIN)
  self.assertIn('curl is required for public toolchain downloads', TOOLCHAIN)
  for package in ('gcc-13-arm-linux-gnueabihf-base', 'gcc-13-cross-base',
                  'cpp-13-arm-linux-gnueabihf', 'libgcc-13-dev-armhf-cross',
                  'libgcc-s1-armhf-cross', 'libstdc++6-armhf-cross',
                  'libstdc++-13-dev-armhf-cross'):
   self.assertIn(package, W)
  for action in re.findall(r'uses:\s*([^\s]+)',W): self.assertRegex(action,r'@[0-9a-f]{40}$')
if __name__=='__main__': unittest.main()
