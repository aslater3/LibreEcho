#!/usr/bin/env python3
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).parents[1]
W=(ROOT/'.github/workflows/build-release.yml').read_text()
class Tests(unittest.TestCase):
 def test_hosted_only(self):
  self.assertNotIn('self-hosted',W); self.assertNotIn('Vaultwarden',W)
  self.assertIn('ports.ubuntu.com/ubuntu-ports', W)
  self.assertIn('apt-get download', W)
  self.assertIn('dpkg-deb -x', W)
  self.assertIn('actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809', W)
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
  self.assertIn("build/ci/build-public-neural-deps.sh', 'build/inputs/public-inputs.json'", W)
  self.assertNotIn('out/CURRENT', W)
  self.assertIn('runs-on: ubuntu-24.04',W)
 def test_triggers_and_jobs(self):
  self.assertIn('branches: [main]',W); self.assertIn('workflow_dispatch:',W); self.assertIn('version:',W)
  self.assertIn('prepare-public-inputs:',W); self.assertIn('publish-dev:',W); self.assertIn('publish-production:',W)
 def test_boundaries(self):
  self.assertIn('queue: max',W); self.assertIn('cancel-in-progress: false',W)
  self.assertIn('build-public-release.sh',W); self.assertIn('fetch-public-deps.py',W)
  for action in re.findall(r'uses:\s*([^\s]+)',W): self.assertRegex(action,r'@[0-9a-f]{40}$')
if __name__=='__main__': unittest.main()
