#!/usr/bin/env python3
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).parents[1]
W=(ROOT/'.github/workflows/build-release.yml').read_text()
class Tests(unittest.TestCase):
 def test_hosted_only(self):
  self.assertNotIn('self-hosted',W); self.assertNotIn('Vaultwarden',W)
  self.assertIn('sudo apt-get install', W)
  self.assertIn('runs-on: ubuntu-24.04',W)
 def test_triggers_and_jobs(self):
  self.assertIn('branches: [main]',W); self.assertIn('workflow_dispatch:',W); self.assertIn('version:',W)
  self.assertIn('prepare-public-inputs:',W); self.assertIn('publish-dev:',W); self.assertIn('publish-production:',W)
 def test_boundaries(self):
  self.assertIn('queue: max',W); self.assertIn('cancel-in-progress: false',W)
  self.assertIn('build-public-release.sh',W); self.assertIn('fetch-public-deps.py',W)
  for action in re.findall(r'uses:\s*([^\s]+)',W): self.assertRegex(action,r'@[0-9a-f]{40}$')
if __name__=='__main__': unittest.main()
