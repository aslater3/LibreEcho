import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from build.ci.publish_dev_pointer import pointer_bytes


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tag = 'radar-puffin-build-' + 'a'*7 + '-' + 'b'*16 + '-' + 'c'*16
        (self.root / ('libreecho-' + self.tag + '.ota.tar')).write_bytes(b'test-only-control')
        (self.root / ('libreecho-' + self.tag + '-build.json')).write_text(json.dumps({'channel':'dev','signed':True}))
        self.release = dict(tag_name=self.tag, prerelease=True, draft=False, assets=[dict(name=p.name,size=p.stat().st_size,digest='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.iterdir()])

    def test_verified_inventory_emits_bounded_pointer(self):
        data = pointer_bytes(self.root,self.tag,self.release)
        self.assertEqual(data.decode().splitlines(),[self.tag,hashlib.sha256(b'test-only-control').hexdigest()])
        self.assertLessEqual(len(data),256)

    def test_rejects_draft_stable_and_wrong_tag(self):
        for change in ({'draft':True},{'prerelease':False},{'tag_name':'radar-puffin-v0.13.11'}):
            with self.assertRaises(ValueError): pointer_bytes(self.root,self.tag,self.release | change)

    def test_rejects_missing_duplicate_or_changed_remote_asset(self):
        assets=self.release['assets']
        for changed in (assets[:-1],assets+assets[:1],[assets[0]|{'digest':'sha256:'+'0'*64}]+assets[1:]):
            with self.assertRaises(ValueError): pointer_bytes(self.root,self.tag,self.release | {'assets':changed})

    def test_rejects_untrusted_tag_shape(self):
        for tag in ('latest','radar-puffin-v0.13.11','../main',self.tag+'\n'):
            with self.assertRaises(ValueError): pointer_bytes(self.root,tag,self.release)

    def test_rejects_symlink(self):
        (self.root/'extra').symlink_to('missing')
        with self.assertRaises(ValueError): pointer_bytes(self.root,self.tag,self.release)
