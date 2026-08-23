#!/usr/bin/env python3
from pathlib import Path
import unittest
from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).parents[2]
spec = spec_from_file_location("resolve_source_set", ROOT / "build/ci/resolve-source-set.py")
assert spec and spec.loader
r = module_from_spec(spec)
spec.loader.exec_module(r)
class Tests(unittest.TestCase):
 def test_all_four_shas_affect_id(self):
  x={k:'a'*40 for k in ('product','platform','linux','ui')}; y=dict(x); y['ui']='b'*40
  self.assertNotEqual(r.source_set_id(x),r.source_set_id(y))
 def test_bad_sha(self):
  with self.assertRaises(ValueError): r.validate_sha('not-a-sha')
if __name__=='__main__': unittest.main()
