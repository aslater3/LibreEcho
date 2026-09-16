#!/usr/bin/env python3
"""Host-only sparse generation tests. No test communicates with a device."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import os
import shutil
import struct
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("userdata_test_installer", ROOT / "tools/libreecho-install.py")
assert spec and spec.loader
INSTALLER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(INSTALLER)


def report(blocks=16, free_ranges=((2, 15),), group_size=32768):
    """Synthetic dumpe2fs fixture with complete group counts and range lists."""
    free = bytearray(blocks)
    for start, end in free_ranges:
        free[start:end + 1] = b"\1" * (end - start + 1)
    lines = [f"Block count: {blocks}", "Block size: 4096", "First block: 0", f"Free blocks: {sum(free)}"]
    for number, start in enumerate(range(0, blocks, group_size)):
        end = min(blocks - 1, start + group_size - 1)
        ranges = []
        pos = start
        while pos <= end:
            if not free[pos]:
                pos += 1
                continue
            first = pos
            while pos <= end and free[pos]:
                pos += 1
            ranges.append(str(first) if first == pos - 1 else f"{first}-{pos - 1}")
        lines += [f"Group {number}: (Blocks {start}-{end})",
                  f"  {sum(free[start:end + 1])} free blocks, 1 free inodes, 0 directories",
                  "  Free blocks: " + ", ".join(ranges)]
    return "\n".join(lines) + "\n"


def apply_sparse(sparse: Path, target: Path):
    """Independent simple RAW/DONT_CARE decoder, preserving skipped target data."""
    with sparse.open("rb") as src, target.open("r+b") as dst:
        magic, major, minor, fh, ch, block, total, chunks, checksum = struct.unpack("<IHHHHIIII", src.read(28))
        assert (magic, major, minor, fh, ch, block, checksum) == (0xED26FF3A, 1, 0, 28, 12, 4096, 0)
        expanded = 0
        for _ in range(chunks):
            kind, reserved, count, length = struct.unpack("<HHII", src.read(12))
            assert not reserved and count > 0
            if kind == 0xCAC3:
                assert length == 12
                dst.seek(count * block, 1)
            elif kind == 0xCAC1:
                assert length == 12 + count * block
                left = count * block
                while left:
                    data = src.read(min(left, 1024 * 1024))
                    assert data
                    dst.write(data)
                    left -= len(data)
            else:
                raise AssertionError(f"unexpected writer chunk {kind:#x}")
            expanded += count
        assert expanded == total and src.read(1) == b""


class UserdataSparseTests(unittest.TestCase):
    def test_global_free_count_is_not_a_block_number(self):
        metadata = report(16, ((10, 12),))  # Header count=3; block 3 is allocated.
        free = INSTALLER._userdata_free_block_map(metadata, 16 * 4096)
        self.assertEqual(free[3], 0)
        self.assertEqual([i for i, value in enumerate(free) if value], [10, 11, 12])

    def test_group_lists_and_empty_group(self):
        free = INSTALLER._userdata_free_block_map(report(32, ((18, 31),), 16), 32 * 4096)
        self.assertEqual(sum(free[:16]), 0)
        self.assertEqual(sum(free), 14)

    def test_single_free_block_is_accepted(self):
        free = INSTALLER._userdata_free_block_map(report(16, ((5, 5),)), 16 * 4096)
        self.assertEqual(sum(free), 1)

    def test_malformed_reports_fail_closed(self):
        original = report(16, ((8, 15),))
        bad = [
            original.replace("Block size: 4096", "Block size: 1024"),
            original.replace("Block count: 16", "Block count: 17"),
            original.replace("First block: 0", "First block: 1"),
            original.replace("Free blocks: 8\n", "Free blocks: 9\n"),
            original.replace("8 free blocks,", "7 free blocks,"),
            original.replace("  Free blocks: 8-15", "  Free blocks: 8-16"),
            original.replace("  Free blocks: 8-15", "  Free blocks: 8-10, 10-15"),
            original.replace("  Free blocks: 8-15", "  Free blocks: 8-10, 12-11"),
            original.replace("  Free blocks: 8-15", "  Free blocks: rubbish"),
            original.replace("Group 0: (Blocks 0-15)", "Group 1: (Blocks 0-15)"),
            original.replace("Group 0: (Blocks 0-15)", "Group 0: (Blocks 1-15)"),
            original.replace("Group 0: (Blocks 0-15)", "Group 0: (Blocks 0-14)"),
            original.replace("  Free blocks: 8-15", ""),
            original + "  Free blocks: 8-15\n",
            original + "Free blocks: 8\n",
            original.split("Group")[0],
            report(16, ((0, 7),)),
            "",
        ]
        for number, metadata in enumerate(bad):
            with self.subTest(case=number), self.assertRaises(INSTALLER.InstallerError):
                INSTALLER._userdata_free_block_map(metadata, 16 * 4096)

    def test_dense_and_holey_hosts_have_identical_sparse_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dense, holey = root / "dense", root / "holey"
            dense.write_bytes(b"\0" * 16 * 4096)
            with holey.open("wb") as stream:
                stream.truncate(16 * 4096)
            for path in (dense, holey):
                with path.open("r+b") as stream:
                    stream.write(b"EXT4-FIXTURE")
            for path in (dense, holey):
                with mock.patch.object(INSTALLER.os, "lseek", side_effect=AssertionError("must not query host holes")):
                    INSTALLER._write_userdata_sparse(path, path.with_suffix(".sparse"), 16 * 4096, report())
            self.assertEqual(dense.with_suffix(".sparse").read_bytes(), holey.with_suffix(".sparse").read_bytes())

    def test_zero_allocated_blocks_overwrite_stale_target_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw, sparse, target = root / "raw", root / "sparse", root / "target"
            raw.write_bytes(b"\0" * 16 * 4096)
            with raw.open("r+b") as stream:
                stream.write(b"superblock fixture")
            target.write_bytes(b"\xa5" * 16 * 4096)
            count = INSTALLER._write_userdata_sparse(raw, sparse, 16 * 4096, report())
            self.assertEqual(count, 8192)
            self.assertEqual(INSTALLER._validate_android_sparse_image(sparse, 16 * 4096), count)
            apply_sparse(sparse, target)
            self.assertEqual(target.read_bytes()[:8192], raw.read_bytes()[:8192])
            self.assertEqual(target.read_bytes()[8192:], b"\xa5" * (14 * 4096))

    def test_both_reviewed_partition_sizes_expand_exactly(self):
        for size in (0x41380000, 0x41B80000):
            with self.subTest(size=hex(size)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, sparse = root / "raw", root / "sparse"
                with raw.open("wb") as stream:
                    stream.truncate(size)
                INSTALLER._validate_userdata_partition_size(size)
                count = INSTALLER._write_userdata_sparse(raw, sparse, size, report(size // 4096, ((2, size // 4096 - 1),)))
                self.assertEqual(INSTALLER._validate_android_sparse_image(sparse, size), count)
                self.assertEqual(count, 8192)

    def test_unknown_layout_rejected_before_generation(self):
        with mock.patch.object(INSTALLER, "verify_fastboot_product"), \
             mock.patch.object(INSTALLER, "_fastboot_partition_size", return_value=0x41B80000 + 512), \
             mock.patch.object(INSTALLER, "_run_command") as run, \
             mock.patch.object(INSTALLER, "_run_command_with_heartbeat") as flash:
            with self.assertRaises(INSTALLER.InstallerError), contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.format_userdata_in_fastboot("fastboot", "TEST", 120)
            run.assert_not_called()
            flash.assert_not_called()

    def test_budget_is_not_relaxed(self):
        blocks = 64 * 1024 * 1024 // 4096 + 2
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / "raw"
            sparse = Path(temporary) / "sparse"
            with raw.open("wb") as stream:
                stream.truncate(blocks * 4096)
            with self.assertRaisesRegex(INSTALLER.InstallerError, "write budget"):
                INSTALLER._write_userdata_sparse(raw, sparse, blocks * 4096, report(blocks, ((blocks - 1, blocks - 1),)))
            self.assertFalse(sparse.exists())

    def test_original_guard_reproduces_reported_dense_sparse_failure(self):
        for size in (0x41380000, 0x41B80000):
            with self.subTest(size=hex(size)), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "all-fill.sparse"
                path.write_bytes(struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, 4096, size // 4096, 1, 0)
                                 + struct.pack("<HHII", 0xCAC2, 0, size // 4096, 16) + b"\0" * 4)
                with self.assertRaisesRegex(INSTALLER.InstallerError, "sparse skip mode failed"):
                    INSTALLER._validate_android_sparse_image(path, size)

    def test_existing_output_and_wrong_raw_length_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            raw, sparse = Path(temporary) / "raw", Path(temporary) / "sparse"
            raw.write_bytes(b"\0" * 16 * 4096)
            sparse.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                INSTALLER._write_userdata_sparse(raw, sparse, 16 * 4096, report())
            with self.assertRaisesRegex(INSTALLER.InstallerError, "raw size"):
                INSTALLER._write_userdata_sparse(raw, sparse, 17 * 4096, report())
            self.assertEqual(sparse.read_bytes(), b"existing")

    def test_preflight_stages_dumpe2fs_without_img2simg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("fastboot", "mke2fs", "dumpe2fs"):
                (root / name).write_text("#!/bin/sh\nexit 0\n")
                (root / name).chmod(0o755)
            with mock.patch.object(INSTALLER, "_find_dumpe2fs", return_value=root / "dumpe2fs"), contextlib.redirect_stdout(io.StringIO()):
                staged = Path(INSTALLER.prepare_fastboot_tools(str(root / "fastboot"), root / "cache"))
            for name in ("fastboot", "mke2fs", "dumpe2fs"):
                self.assertTrue((staged.parent / name).is_file())
            self.assertFalse((staged.parent / "img2simg").exists())

    def test_missing_dumpe2fs_fails_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("fastboot", "mke2fs"):
                (root / name).write_text("#!/bin/sh\nexit 0\n")
                (root / name).chmod(0o755)
            with mock.patch.object(INSTALLER, "_find_dumpe2fs", return_value=None), \
                 mock.patch.object(INSTALLER, "_run_command") as run:
                with self.assertRaisesRegex(INSTALLER.InstallerError, "dumpe2fs"):
                    INSTALLER.prepare_fastboot_tools(str(root / "fastboot"), root / "cache")
                run.assert_not_called()

    def test_formatter_control_flow_only_authorises_validated_userdata_flash(self):
        for size in (0x41380000, 0x41B80000):
            with self.subTest(size=hex(size)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ("mke2fs", "dumpe2fs", "fastboot"):
                    (root / name).write_text("fixture")
                commands = []
                def run(argv, timeout, **kwargs):
                    commands.append(argv)
                    return subprocess.CompletedProcess(argv, 0, report(size // 4096, ((2, size // 4096 - 1),)) if argv[0] == "env" else "", "")
                def flash(argv, timeout, message):
                    self.assertEqual(argv[:5], [str(root / "fastboot"), "-s", "TEST", "flash", "userdata"])
                    self.assertEqual(INSTALLER._validate_android_sparse_image(Path(argv[5]), size), 8192)
                    self.assertGreaterEqual(timeout, 900)
                with mock.patch.object(INSTALLER, "verify_fastboot_product"), \
                     mock.patch.object(INSTALLER, "_fastboot_partition_size", return_value=size), \
                     mock.patch.object(INSTALLER, "_run_command", side_effect=run), \
                     mock.patch.object(INSTALLER, "_run_command_with_heartbeat", side_effect=flash) as write, \
                     contextlib.redirect_stdout(io.StringIO()):
                    INSTALLER.format_userdata_in_fastboot(str(root / "fastboot"), "TEST", 120)
                write.assert_called_once()
                self.assertIn("4096", commands[0])
                self.assertEqual(commands[1][:3], ["env", "LC_ALL=C", str(root / "dumpe2fs")])

    def test_invalid_allocation_report_never_authorises_flash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("mke2fs", "dumpe2fs", "fastboot"):
                (root / name).write_text("fixture")
            with mock.patch.object(INSTALLER, "verify_fastboot_product"), \
                 mock.patch.object(INSTALLER, "_fastboot_partition_size", return_value=0x41B80000), \
                 mock.patch.object(INSTALLER, "_run_command", return_value=subprocess.CompletedProcess([], 0, "bad report", "")), \
                 mock.patch.object(INSTALLER, "_run_command_with_heartbeat") as flash, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(INSTALLER.InstallerError):
                    INSTALLER.format_userdata_in_fastboot(str(root / "fastboot"), "TEST", 120)
                flash.assert_not_called()


def host_tool(name):
    return shutil.which(name) or next((str(p) for p in (Path('/usr/sbin') / name, Path('/sbin') / name) if p.is_file()), None)


@unittest.skipUnless(all(host_tool(n) for n in ('mke2fs', 'dumpe2fs', 'e2fsck')), 'real filesystem integration requires e2fsprogs')
class UserdataFilesystemIntegrationTests(unittest.TestCase):
    def check_geometry(self, size):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw, sparse, target = root / 'raw', root / 'sparse', root / 'target'
            with raw.open('wb') as stream:
                stream.truncate(size)
            subprocess.run([host_tool('mke2fs'), '-F', '-t', 'ext4', '-b', '4096', '-m', '0', '-O',
                            '^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file', '-E',
                            'lazy_itable_init=0,lazy_journal_init=0', str(raw)], check=True, capture_output=True, timeout=300)
            metadata = subprocess.run([host_tool('dumpe2fs'), str(raw)], check=True, capture_output=True,
                                      text=True, env={**os.environ, 'LC_ALL': 'C'}, timeout=300).stdout
            INSTALLER._write_userdata_sparse(raw, sparse, size, metadata)
            INSTALLER._validate_android_sparse_image(sparse, size)
            with target.open('wb') as stream:
                remaining = size
                stale = b'\xa5' * (1024 * 1024)
                while remaining:
                    chunk = stale[:min(remaining, len(stale))]
                    stream.write(chunk)
                    remaining -= len(chunk)
            apply_sparse(sparse, target)
            check = subprocess.run([host_tool('e2fsck'), '-fn', str(target)], capture_output=True, text=True, timeout=300)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_standard_layout_real_ext4_on_stale_target(self):
        self.check_geometry(0x41380000)

    def test_larger_layout_real_ext4_on_stale_target(self):
        self.check_geometry(0x41B80000)


if __name__ == '__main__':
    if '--require-filesystem-tools' in sys.argv:
        sys.argv.remove('--require-filesystem-tools')
        missing = [n for n in ('mke2fs', 'dumpe2fs', 'e2fsck') if not host_tool(n)]
        if missing:
            raise SystemExit('Filesystem integration tools missing: ' + ', '.join(missing))
    unittest.main()
