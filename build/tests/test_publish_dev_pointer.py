import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from build.ci.publish_dev_pointer import (
    POINTER_ASSET, frozen_channel_assets, pointer_bytes, verify_channel_readback,
)


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tag = 'radar-puffin-build-' + 'a'*7 + '-' + 'b'*16 + '-' + 'c'*16
        (self.root / ('libreecho-' + self.tag + '.ota.tar')).write_bytes(b'test-only-control')
        self.build = self.root / ('libreecho-' + self.tag + '-build.json')
        self.write_build({'channel':'dev','signed':True,'board':'radar_puffin','ota_format':'v2'})
        self.release = dict(tag_name=self.tag, prerelease=True, draft=False, assets=[dict(name=p.name,size=p.stat().st_size,digest='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.iterdir()])

    def write_build(self, data):
        self.build.write_text(json.dumps(data))
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

    def test_rejects_v3_and_non_radar_puffin_packages(self):
        for change in ({'ota_format': 'v3'}, {'board': 'biscuit'}, {'board': None}):
            data = {'channel': 'dev', 'signed': True, 'board': 'radar_puffin', 'ota_format': 'v2'}
            data.update(change)
            data = {k: v for k, v in data.items() if v is not None}
            self.write_build(data)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'radar_puffin v1/v2'):
                pointer_bytes(self.root, self.tag, self.release)

    def test_v1_package_without_format_key_is_accepted(self):
        self.write_build({'channel': 'dev', 'signed': True, 'board': 'radar_puffin'})
        self.assertTrue(pointer_bytes(self.root, self.tag, self.release).startswith(self.tag.encode()))


class ChannelAssetTests(unittest.TestCase):
    DATA = b'radar-puffin-build-aaaaaaa-bbbbbbbbbbbbbbbb-cccccccccccccccc\n' + b'0' * 64 + b'\n'
    V3 = dict(name='release-pointer-v3.txt', id=7, size=126, digest='sha256:' + 'e' * 64)

    def pointer(self, data=DATA, asset_id=9):
        return dict(name=POINTER_ASSET, id=asset_id, size=len(data),
                    digest='sha256:' + hashlib.sha256(data).hexdigest())

    def test_v3_pointer_is_frozen_and_0_13_pointer_is_the_only_writable_asset(self):
        frozen = frozen_channel_assets([self.pointer(b'old\n'), self.V3])
        self.assertEqual(frozen, [('release-pointer-v3.txt', 7, 126, 'sha256:' + 'e' * 64)])
        self.assertEqual(frozen_channel_assets([]), [])
        self.assertEqual(frozen_channel_assets([self.pointer()]), [])

    def test_unexpected_or_duplicate_channel_assets_refuse_mutation(self):
        for assets in ([self.V3, dict(self.V3, id=8)],
                       [self.pointer(), dict(name='release-pointer-v4.txt', id=1, size=1, digest='x')],
                       [self.pointer(), self.pointer()]):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                frozen_channel_assets(assets)

    def test_readback_requires_unchanged_v3_and_exact_0_13_pointer(self):
        frozen = frozen_channel_assets([self.pointer(b'old\n'), self.V3])
        verify_channel_readback([self.pointer(asset_id=10), self.V3], frozen, self.DATA)
        for assets in (
            [self.pointer(asset_id=10)],                                   # v3 deleted
            [self.pointer(asset_id=10), dict(self.V3, id=11)],             # v3 replaced
            [self.pointer(asset_id=10), dict(self.V3, digest='sha256:' + 'f' * 64)],
            [self.pointer(b'other\n'), self.V3],                           # wrong pointer bytes
            [self.V3],                                                     # 0.13 pointer missing
        ):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                verify_channel_readback(assets, frozen, self.DATA)
