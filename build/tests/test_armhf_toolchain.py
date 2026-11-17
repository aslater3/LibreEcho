#!/usr/bin/env python3
"""TDD tests for the ARMHF cross-toolchain lock, materializer, and verifier.

Task 2 (Product half): the Sendspin ARMHF cross-build's *compile-input*
closure must be an authenticated, hash-pinned lock that a stdlib materializer
can stage offline into a private prefix and that a verifier can re-derive from
the archives -- not from a receipt's self-attestation, a version string, or a
prior build's word.

Two evidence classes are kept deliberately separate:

* real shipped behavior exercised through hermetic ``.deb`` fixtures built with
  the standard library (explicitly **fixtures**, not provenance proof); and
* an opt-in end-to-end run over the real archived closure, gated on
  ``ARMHF_TOOLCHAIN_ARCHIVE_DIR`` so it never runs implicitly.

Run with:

    python3 -m unittest build/tests/test_armhf_toolchain.py -v
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import warnings
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "build/ci/armhf_toolchain.py"
LOCK_PATH = ROOT / "build/inputs/armhf-cross-toolchain.lock.json"
REAL_ARCHIVES = os.environ.get("ARMHF_TOOLCHAIN_ARCHIVE_DIR")

spec = spec_from_file_location("armhf_toolchain", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules["armhf_toolchain"] = module
spec.loader.exec_module(module)


# --------------------------------------------------------------------------
# Fixture builders: genuinely structured .deb files written by the stdlib.
# --------------------------------------------------------------------------

def _tar_bytes(members) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        for member in members:
            handle.addfile(member[0], io.BytesIO(member[1]) if member[1] is not None else None)
    return buffer.getvalue()


def _file(name, body, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = mode
    info.type = tarfile.REGTYPE
    return (info, body)


def _dir(name, mode=0o755):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.size = 0
    return (info, None)


def _symlink(name, target, mode=0o777):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.mode = mode
    info.size = 0
    return (info, None)


def _hardlink(name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    info.size = 0
    return (info, None)


def _fifo(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.FIFOTYPE
    info.size = 0
    return (info, None)


def _char_device(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.CHRTYPE
    info.devmajor = 1
    info.devminor = 3
    info.size = 0
    return (info, None)


def _ar(members) -> bytes:
    """Write a POSIX ar archive (the outer .deb container)."""
    out = bytearray(b"!<arch>\n")
    for name, body in members:
        encoded = name.encode() + b" " * (16 - len(name))
        header = (
            encoded
            + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6)
            + b"100644".ljust(8) + str(len(body)).encode().ljust(10) + b"`\n"
        )
        assert len(header) == 60, len(header)
        out += header + body
        if len(body) % 2:
            out += b"\n"
    return bytes(out)


def make_deb(path: Path, package, version, architecture, members, *,
             control=None) -> Path:
    """Build a real .deb: ar(debian-binary, control.tar.gz, data.tar.gz)."""
    control_body = control if control is not None else (
        f"Package: {package}\nVersion: {version}\nArchitecture: {architecture}\n"
    ).encode()
    control_tar = _tar_bytes([_file("./control", control_body)])
    data_tar = _tar_bytes(members)
    path.write_bytes(_ar([
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control_tar),
        ("data.tar.gz", data_tar),
    ]))
    return path


def simple_deb(path, package="demo", version="1.0-1", architecture="amd64", *,
               body=b"payload", rel="usr/share/demo/data") -> Path:
    return make_deb(path, package, version, architecture, [
        _dir("."), _dir("usr"), _dir("usr/share"), _dir("usr/share/demo"),
        _file(rel, body),
    ])


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lock_record(path: Path, name, version, architecture):
    return {
        "name": name,
        "version": version,
        "architecture": architecture,
        "filename": path.name,
        "pool_path": "pool/main/x/" + path.name,
        "url": "https://example.invalid/ubuntu/pool/main/x/" + path.name,
        "size": path.stat().st_size,
        "sha256": digest(path),
        "authentication": {
            "source": "live",
            "suite": "noble",
            "index": "main/binary-amd64/Packages.xz",
            "index_sha256": "a" * 64,
            "signing_fingerprint": "F6ECB3762474EDA9D21B7022871920D1991BC93C",
        },
    }


def make_lock(path: Path, records, *, schema=None) -> Path:
    document = {
        "schema": schema if schema is not None else module.LOCK_SCHEMA,
        "target": "arm-linux-gnueabihf-glibc-dynamic",
        "archives": records,
        "host_requirements": {"target": "arm-linux-gnueabihf"},
        "provenance": {"compile_sysroot_pinned": False},
    }
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def write_lock_out(path: Path, document) -> Path:
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


_MISSING = object()


def zstd_payload(raw: bytes):
    """Compress ``raw`` with whichever local zstd encoder is available.

    Fixture-only helper: returns ``None`` when the host has no zstd encoder so a
    caller can skip instead of pretending the backend was exercised.
    """
    try:
        from compression.zstd import ZstdFile
    except ImportError:
        pass
    else:
        buffer = io.BytesIO()
        with ZstdFile(buffer, "wb") as handle:
            handle.write(raw)
        return buffer.getvalue()
    tool = shutil.which("zstd")
    if tool:
        done = subprocess.run([tool, "-q", "-c"], input=raw, capture_output=True)
        if done.returncode == 0:
            return done.stdout
    return None


class ZstdBackends:
    """Deterministically choose which zstd decoders the module may find.

    A blocked backend is put into ``sys.modules`` as ``None`` (its import then
    raises ``ImportError``) and the ``zstd`` CLI is hidden from ``shutil.which``.
    The exact prior module/``which`` state is restored on exit; deterministic, no
    sleeps, no host mutation.
    """

    def __init__(self, *, stdlib: bool, py_module: bool, cli: bool = True):
        self.stdlib = stdlib
        self.py_module = py_module
        self.cli = cli

    def __enter__(self):
        self._saved_modules = {}
        for name in ("compression.zstd", "zstandard"):
            self._saved_modules[name] = sys.modules.get(name, _MISSING)
        if not self.stdlib:
            sys.modules["compression.zstd"] = None
        if not self.py_module:
            sys.modules["zstandard"] = None
        self._real_which = module.shutil.which
        if not self.cli:
            module.shutil.which = (
                lambda name: None if name == "zstd" else self._real_which(name))
        return self

    def __exit__(self, *exc):
        for name, saved in self._saved_modules.items():
            if saved is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved
        module.shutil.which = self._real_which
        return False


class Fixture:
    """A tmp dir with an archive cache, a lock, and a staged prefix."""

    def __init__(self, root: Path):
        self.root = root
        self.cache = root / "cache"
        self.cache.mkdir()
        self.debs = []

    def add(self, package, version="1.0-1", architecture="amd64", *, body=b"payload",
            rel="usr/share/demo/data", members=None):
        path = self.cache / f"{package}_{version}_{architecture}.deb"
        if members is None:
            simple_deb(path, package, version, architecture, body=body, rel=rel)
        else:
            make_deb(path, package, version, architecture, members)
        self.debs.append(path)
        return path

    def lock(self, records=None):
        self.lock_path = self.root / "lock.json"
        records = records if records is not None else [
            lock_record(deb, deb.name.split("_")[0], deb.name.split("_")[1],
                        deb.name.split("_")[2][:-4])
            for deb in self.debs
        ]
        make_lock(self.lock_path, records)
        return self.lock_path

    def stage(self, prefix=None):
        if not hasattr(self, "lock_path"):
            self.lock()
        prefix = prefix or (self.root / "prefix")
        return module.stage(self.lock_path, self.cache, prefix)


# --------------------------------------------------------------------------
# 1. Lock schema and authenticated identity
# --------------------------------------------------------------------------

class CommittedLockTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads(LOCK_PATH.read_text())

    def test_schema_and_target(self):
        self.assertEqual(self.lock["schema"], module.LOCK_SCHEMA)
        self.assertEqual(self.lock["target"], "arm-linux-gnueabihf-glibc-dynamic")

    def test_thirty_archives_with_required_fields(self):
        archives = self.lock["archives"]
        self.assertEqual(len(archives), 30)
        for record in archives:
            with self.subTest(package=record["name"]):
                for field in ("name", "version", "architecture", "url", "size",
                              "sha256", "filename"):
                    self.assertTrue(record.get(field), field)
                self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
                self.assertIsInstance(record["size"], int)
                self.assertTrue(record["size"] > 0)
                self.assertTrue(record["url"].startswith("https://"))
                self.assertEqual(record["url"].rsplit("/", 1)[-1], record["filename"])

    def test_authenticated_evidence_fields_present_per_archive(self):
        anchor = self.lock["trust_anchor"]["fingerprint"]
        self.assertEqual(anchor, "F6ECB3762474EDA9D21B7022871920D1991BC93C")
        pockets = self.lock["pockets"]
        for record in self.lock["archives"]:
            with self.subTest(package=record["name"]):
                auth = record["authentication"]
                self.assertRegex(auth["index_sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(auth["signing_fingerprint"], anchor)
                self.assertIn(auth["suite"], pockets)
                self.assertRegex(pockets[auth["suite"]]["inrelease_sha256"], r"^[0-9a-f]{64}$")

    def test_epochs_are_recorded_exactly(self):
        by_name = {record["name"]: record for record in self.lock["archives"]}
        self.assertEqual(by_name["libgmp10"]["version"], "2:6.3.0+dfsg-2ubuntu6.1")
        self.assertEqual(by_name["gcc-arm-linux-gnueabihf"]["version"], "4:13.2.0-7ubuntu1")

    def test_zlib1g_uses_the_historical_snapshot_url(self):
        record = next(r for r in self.lock["archives"] if r["name"] == "zlib1g")
        self.assertEqual(record["version"], "1:1.3.dfsg-3.1ubuntu2.1")
        self.assertTrue(record["url"].startswith(
            "https://snapshot.ubuntu.com/ubuntu/20241115T000000Z/"), record["url"])
        self.assertNotIn("archive.ubuntu.com", record["url"])
        self.assertEqual(record["authentication"]["source"], "snapshot")
        self.assertEqual(record["authentication"]["suite"],
                         "snapshot:20241115T000000Z:noble-updates")

    def test_lock_is_portable_and_does_not_claim_pinning(self):
        text = LOCK_PATH.read_text()
        self.assertNotIn("/home/", text)
        self.assertNotIn("/mnt/", text)
        provenance = self.lock["provenance"]
        self.assertIs(provenance["compile_sysroot_pinned"], False)
        self.assertIn("not performed", provenance["qemu_execution"])

    def test_host_requirements_are_separate_and_do_not_claim_independence(self):
        host = self.lock["host_requirements"]
        self.assertIn("not pinned", host["amd64_glibc"]["status"])
        self.assertIn("not claimed", host["amd64_glibc"]["status"].lower())
        self.assertIn("dpkg-deb", host["host_tools"])
        names = {record["name"] for record in self.lock["archives"]}
        self.assertNotIn("libc6", names)   # unresolved host libc is never a pinned archive

    def test_host_requirements_declare_the_zstd_backend(self):
        # All 30 locked archives carry a data.tar.zst member, so the materializer
        # cannot run without a zstd decoder.  The declared host floor must name it,
        # but only as ONE of the alternatives the module actually implements.
        host = self.lock["host_requirements"]
        declaration = host.get("zstd_backend")
        self.assertIsInstance(declaration, dict,
                              "host requirements must declare the zstd backend")
        alternatives = declaration.get("alternatives")
        self.assertIsInstance(alternatives, list)
        self.assertEqual(len(alternatives), 3)
        joined = " ".join(alternatives).lower()
        for token in ("compression.zstd", "zstandard", "zstd", "command-line", "3.14"):
            self.assertIn(token, joined,
                          "declared zstd alternatives must match the code paths")
        # It is a declared host dependency, not a fourth pinned archive.
        self.assertNotIn("host_tools_mandatory", declaration)

    def test_architecture_doc_declares_the_zstd_backend(self):
        doc = (ROOT / "docs/architecture/sendspin.md").read_text().lower()
        self.assertIn("zstd", doc,
                      "the architecture contract must declare the zstd dependency")
        for token in ("compression.zstd", "zstandard", "command-line", "3.14"):
            self.assertIn(token, doc)

    def test_committed_lock_loads_and_is_ordered(self):
        loaded = module.load_lock(LOCK_PATH)
        records = loaded["archives"]
        order = [(r["name"], r["architecture"], r["version"]) for r in records]
        self.assertEqual(order, sorted(order))


class LockValidationTests(unittest.TestCase):
    def _mutate(self, record_edit):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deb = simple_deb(root / "demo_1.0-1_amd64.deb")
            record = lock_record(deb, "demo", "1.0-1", "amd64")
            record_edit(record)
            path = write_lock_out(root / "lock.json", {
                "schema": module.LOCK_SCHEMA, "archives": [record],
            })
            return root, path

    def test_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lock_out(Path(tmp) / "lock.json",
                                  {"schema": "nope", "archives": []})
            with self.assertRaises(module.ToolchainError):
                module.load_lock(path)

    def test_rejects_empty_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lock_out(Path(tmp) / "lock.json",
                                  {"schema": module.LOCK_SCHEMA, "archives": []})
            with self.assertRaises(module.ToolchainError):
                module.load_lock(path)

    def test_rejects_duplicate_package(self):
        def edit(record):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deb = simple_deb(root / "demo_1.0-1_amd64.deb")
            record = lock_record(deb, "demo", "1.0-1", "amd64")
            path = write_lock_out(root / "lock.json", {
                "schema": module.LOCK_SCHEMA, "archives": [dict(record), dict(record)],
            })
            with self.assertRaises(module.ToolchainError):
                module.load_lock(path)

    def test_rejects_malformed_digest(self):
        _, path = self._mutate(lambda r: r.__setitem__("sha256", "zz"))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_rejects_malformed_size(self):
        _, path = self._mutate(lambda r: r.__setitem__("size", "big"))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_rejects_missing_version(self):
        _, path = self._mutate(lambda r: r.__setitem__("version", ""))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_rejects_unsafe_archive_filename(self):
        _, path = self._mutate(lambda r: r.__setitem__("filename", "../../evil.deb"))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_rejects_non_https_url(self):
        _, path = self._mutate(lambda r: r.__setitem__("url", "http://example.invalid/x.deb"))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_rejects_unknown_architecture(self):
        _, path = self._mutate(lambda r: r.__setitem__("architecture", "../evil"))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_rejects_record_fingerprint_that_differs_from_the_trust_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deb = simple_deb(root / "demo_1.0-1_amd64.deb")
            record = lock_record(deb, "demo", "1.0-1", "amd64")
            record["authentication"]["signing_fingerprint"] = "DEADBEEF" * 5
            path = write_lock_out(root / "lock.json", {
                "schema": module.LOCK_SCHEMA, "archives": [record],
                "trust_anchor": {"fingerprint": "F6ECB3762474EDA9D21B7022871920D1991BC93C"},
            })
            with self.assertRaises(module.ToolchainError):
                module.load_lock(path)

    def test_rejects_url_whose_basename_is_not_the_filename(self):
        _, path = self._mutate(lambda r: r.__setitem__(
            "url", "https://example.invalid/ubuntu/pool/main/x/other-file.deb"))
        with self.assertRaises(module.ToolchainError):
            module.load_lock(path)

    def test_accepts_a_record_matching_the_trust_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deb = simple_deb(root / "demo_1.0-1_amd64.deb")
            record = lock_record(deb, "demo", "1.0-1", "amd64")
            path = write_lock_out(root / "lock.json", {
                "schema": module.LOCK_SCHEMA, "archives": [record],
                "trust_anchor": {"fingerprint": "F6ECB3762474EDA9D21B7022871920D1991BC93C"},
            })
            loaded = module.load_lock(path)
            self.assertEqual(len(loaded["archives"]), 1)


# --------------------------------------------------------------------------
# 2. Archive pre-validation (size + SHA-256 + internal package identity)
# --------------------------------------------------------------------------

class ArchivePrevalidationTests(unittest.TestCase):
    def test_verifies_matching_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            lock = module.load_lock(fixture.lock())
            identities = module.verify_archives(lock, fixture.cache)
            self.assertEqual([i["name"] for i in identities], ["alpha"])
            self.assertEqual(identities[0]["sha256"], digest(fixture.debs[0]))

    def test_rejects_missing_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            lock_path = fixture.lock()
            fixture.debs[0].unlink()
            with self.assertRaises(module.ToolchainError):
                module.verify_archives(module.load_lock(lock_path), fixture.cache)

    def test_rejects_unrecorded_extra_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            module.load_lock(fixture.lock())
            simple_deb(fixture.cache / "sneaky_9_amd64.deb", "sneaky")
            with self.assertRaises(module.ToolchainError):
                module.verify_archives(module.load_lock(fixture.lock_path), fixture.cache)

    def test_rejects_size_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            lock_path = fixture.lock()
            document = json.loads(lock_path.read_text())
            document["archives"][0]["size"] += 1
            write_lock_out(lock_path, document)
            with self.assertRaises(module.ToolchainError):
                module.verify_archives(module.load_lock(lock_path), fixture.cache)

    def test_rejects_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            lock_path = fixture.lock()
            document = json.loads(lock_path.read_text())
            document["archives"][0]["sha256"] = "0" * 64
            write_lock_out(lock_path, document)
            with self.assertRaises(module.ToolchainError):
                module.verify_archives(module.load_lock(lock_path), fixture.cache)

    def test_rejects_internal_package_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            deb = fixture.add("alpha")
            # Lock claims a different version than the control file records.
            record = lock_record(deb, "alpha", "9.9-9", "amd64")
            lock_path = fixture.lock(records=[record])
            with self.assertRaises(module.ToolchainError):
                module.verify_archives(module.load_lock(lock_path), fixture.cache)

    def test_stage_checks_identity_before_extracting(self):
        # A broken data.tar plus a wrong control identity must report the
        # identity failure, proving archives are validated before extraction.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            deb = cache / "alpha_1.0-1_amd64.deb"
            deb.write_bytes(_ar([
                ("debian-binary", b"2.0\n"),
                ("control.tar.gz", _tar_bytes([_file(
                    "./control", b"Package: alpha\nVersion: 2.0-2\nArchitecture: amd64\n")])),
                ("data.tar.gz", b"not a real tarball"),
            ]))
            record = lock_record(deb, "alpha", "1.0-1", "amd64")
            lock_path = make_lock(root / "lock.json", [record])
            with self.assertRaises(module.ToolchainError) as ctx:
                module.stage(lock_path, cache, root / "prefix")
            self.assertIn("identit", str(ctx.exception).lower())
            self.assertFalse((root / "prefix").exists())


# --------------------------------------------------------------------------
# 3. Safe extraction: never trust an archive's member metadata
# --------------------------------------------------------------------------

UNSAFE_MEMBERS = {
    "absolute": [_file("/etc/evil", b"x")],
    "traversal": [_file("../escape", b"x")],
    "nested_traversal": [_file("usr/../../escape", b"x")],
    "absolute_symlink": [_symlink("usr/evil", "/etc/passwd")],
    "escaping_symlink": [_symlink("usr/evil", "../../../../etc/passwd")],
    "fifo": [_fifo("usr/fifo")],
    "device": [_char_device("usr/null")],
    "hardlink": [_file("usr/real", b"x"), _hardlink("usr/hard", "usr/real")],
    "empty_name": [_file("", b"x")],
}


class SafeExtractionTests(unittest.TestCase):
    def _stage_with_members(self, members):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            deb = cache / "evil_1.0-1_amd64.deb"
            make_deb(deb, "evil", "1.0-1", "amd64", members)
            record = lock_record(deb, "evil", "1.0-1", "amd64")
            lock_path = make_lock(root / "lock.json", [record])
            prefix = root / "prefix"
            with self.assertRaises(module.ToolchainError):
                module.stage(lock_path, cache, prefix)
            self.assertFalse(prefix.exists())
            leftovers = sorted(p.name for p in root.iterdir() if p.name != "cache")
            self.assertNotIn("prefix", leftovers)

    def test_rejects_every_unsafe_member_kind(self):
        for label, members in UNSAFE_MEMBERS.items():
            with self.subTest(kind=label):
                self._stage_with_members(members)

    def test_accepts_a_legitimate_relative_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            deb = cache / "lib_1.0-1_amd64.deb"
            make_deb(deb, "lib", "1.0-1", "amd64", [
                _dir("."), _dir("usr"), _dir("usr/lib"), _dir("usr/lib/demo"),
                _file("usr/lib/demo/libfoo.so.1.2.3", b"so"),
                _symlink("usr/lib/demo/libfoo.so.1", "libfoo.so.1.2.3"),
            ])
            record = lock_record(deb, "lib", "1.0-1", "amd64")
            lock_path = make_lock(root / "lock.json", [record])
            out = module.stage(lock_path, cache, root / "prefix")
            link = root / "prefix/usr/lib/demo/libfoo.so.1"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "libfoo.so.1.2.3")
            self.assertTrue(out["receipt"]["tree"]["symlinks"] >= 1)

    def test_accepts_parent_relative_symlink_that_stays_inside_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            deb = cache / "lib_1.0-1_amd64.deb"
            make_deb(deb, "lib", "1.0-1", "amd64", [
                _dir("."), _dir("usr"), _dir("usr/lib"), _dir("usr/lib/x86_64-linux-gnu"),
                _dir("usr/lib/gcc"), _dir("usr/lib/gcc/x"), _dir("usr/lib/gcc/x/p"),
                _file("usr/lib/x86_64-linux-gnu/libcc1.so.0", b"lib"),
                _symlink("usr/lib/gcc/x/p/libcc1.so", "../../../x86_64-linux-gnu/libcc1.so.0"),
            ])
            record = lock_record(deb, "lib", "1.0-1", "amd64")
            lock_path = make_lock(root / "lock.json", [record])
            module.stage(lock_path, cache, root / "prefix")
            self.assertTrue((root / "prefix/usr/lib/gcc/x/p/libcc1.so").is_symlink())

    def test_directory_overlap_between_archives_is_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            a = cache / "a_1_amd64.deb"
            b = cache / "b_1_amd64.deb"
            make_deb(a, "a", "1", "amd64", [_dir("."), _dir("usr"), _dir("usr/include"),
                                            _file("usr/include/a.h", b"a")])
            make_deb(b, "b", "1", "amd64", [_dir("."), _dir("usr"), _dir("usr/include"),
                                            _file("usr/include/b.h", b"b")])
            lock_path = make_lock(root / "lock.json", [
                lock_record(a, "a", "1", "amd64"),
                lock_record(b, "b", "1", "amd64"),
            ])
            module.stage(lock_path, cache, root / "prefix")
            self.assertEqual((root / "prefix/usr/include/a.h").read_bytes(), b"a")
            self.assertEqual((root / "prefix/usr/include/b.h").read_bytes(), b"b")

    def test_conflicting_file_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            a = cache / "a_1_amd64.deb"
            b = cache / "b_1_amd64.deb"
            make_deb(a, "a", "1", "amd64", [_dir("."), _dir("etc"), _file("etc/x", b"one")])
            make_deb(b, "b", "1", "amd64", [_dir("."), _dir("etc"), _file("etc/x", b"two")])
            lock_path = make_lock(root / "lock.json", [
                lock_record(a, "a", "1", "amd64"),
                lock_record(b, "b", "1", "amd64"),
            ])
            with self.assertRaises(module.ToolchainError):
                module.stage(lock_path, cache, root / "prefix")
            self.assertFalse((root / "prefix").exists())

    def test_type_conflict_between_archives_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            a = cache / "a_1_amd64.deb"
            b = cache / "b_1_amd64.deb"
            make_deb(a, "a", "1", "amd64", [_dir("."), _file("usr", b"file")])
            make_deb(b, "b", "1", "amd64", [_dir("."), _dir("usr"), _file("usr/y", b"y")])
            lock_path = make_lock(root / "lock.json", [
                lock_record(a, "a", "1", "amd64"),
                lock_record(b, "b", "1", "amd64"),
            ])
            with self.assertRaises(module.ToolchainError):
                module.stage(lock_path, cache, root / "prefix")

    def test_control_scripts_are_never_run(self):
        # A control archive that ships a maintainer script must never execute:
        # only data.tar is ever extracted.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            marker = root / "PWNED"
            postinst = f"#!/bin/sh\ntouch {marker}\n".encode()
            deb = cache / "p_1_amd64.deb"
            deb.write_bytes(_ar([
                ("debian-binary", b"2.0\n"),
                ("control.tar.gz", _tar_bytes([
                    _file("./control", b"Package: p\nVersion: 1\nArchitecture: amd64\n"),
                    _file("./postinst", postinst, mode=0o755),
                ])),
                ("data.tar.gz", _tar_bytes([_dir("."), _file("usr/share/p/data", b"d")])),
            ]))
            lock_path = make_lock(root / "lock.json", [lock_record(deb, "p", "1", "amd64")])
            module.stage(lock_path, cache, root / "prefix")
            self.assertFalse(marker.exists())


# --------------------------------------------------------------------------
# 4. Stage: offline materialization and no-replace publication
# --------------------------------------------------------------------------

class StagePublicationTests(unittest.TestCase):
    def test_stage_materializes_prefix_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha", body=b"alpha-bytes")
            result = fixture.stage()
            prefix = fixture.root / "prefix"
            self.assertTrue((prefix / "usr/share/demo/data").is_file())
            receipt = json.loads((prefix / module.RECEIPT_NAME).read_text())
            self.assertEqual(receipt["schema"], module.RECEIPT_SCHEMA)
            self.assertEqual(receipt["lock_sha256"], digest(fixture.lock_path))
            self.assertEqual([a["name"] for a in receipt["archives"]], ["alpha"])
            self.assertEqual(receipt["archives"][0]["sha256"], digest(fixture.debs[0]))
            self.assertRegex(receipt["tree"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(receipt["tree"]["files"], 1)

    def test_stage_offline_never_touches_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            fixture.lock()
            module.stage(fixture.lock_path, fixture.cache, fixture.root / "prefix")

    def test_receipt_and_output_contain_no_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            fixture.stage()
            text = (fixture.root / "prefix" / module.RECEIPT_NAME).read_text()
            self.assertNotIn(tmp, text)
            self.assertNotIn('"/', text)

    def test_refuses_existing_destination_and_leaves_it_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            fixture.lock()
            prefix = fixture.root / "prefix"
            prefix.mkdir()
            (prefix / "incumbent").write_bytes(b"keep")
            with self.assertRaises(module.ToolchainError):
                module.stage(fixture.lock_path, fixture.cache, prefix)
            self.assertEqual((prefix / "incumbent").read_bytes(), b"keep")
            self.assertEqual(sorted(p.name for p in prefix.iterdir()), ["incumbent"])

    def test_repeated_attempt_refuses_untouched_incumbent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            fixture.stage()
            first = sorted(p.name for p in (fixture.root / "prefix").iterdir())
            with self.assertRaises(module.ToolchainError):
                fixture.stage()
            self.assertEqual(sorted(p.name for p in (fixture.root / "prefix").iterdir()), first)

    def test_refuses_symlinked_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha")
            fixture.lock()
            real = root / "real"
            real.mkdir()
            link = root / "prefix"
            link.symlink_to(real)
            with self.assertRaises(module.ToolchainError):
                module.stage(fixture.lock_path, fixture.cache, link)
            self.assertEqual(sorted(real.iterdir()), [])

    def test_failure_leaves_no_destination_or_scratch_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha")
            fixture.add("beta")
            lock_path = fixture.lock()
            document = json.loads(lock_path.read_text())
            # Break the second archive's digest after the lock was written.
            target = next(r for r in document["archives"] if r["name"] == "beta")
            target["sha256"] = "0" * 64
            write_lock_out(lock_path, document)
            with self.assertRaises(module.ToolchainError):
                module.stage(lock_path, fixture.cache, root / "prefix")
            self.assertFalse((root / "prefix").exists())
            residue = [p.name for p in root.iterdir()
                       if p.name not in {"cache", "lock.json"}]
            self.assertEqual(residue, [])

    def test_publication_is_no_replace_and_removes_the_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            fixture.stage()
            stages = [p for p in fixture.root.iterdir() if p.name.startswith(".armhf-")]
            self.assertEqual(stages, [])

    def test_refuses_an_existing_empty_destination(self):
        # A plain rename would silently replace an existing *empty* directory;
        # publication must be no-replace for an empty incumbent too.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            fixture.lock()
            prefix = fixture.root / "prefix"
            prefix.mkdir()
            with self.assertRaises(module.ToolchainError):
                module.stage(fixture.lock_path, fixture.cache, prefix)
            self.assertTrue(prefix.is_dir())
            self.assertEqual(sorted(prefix.iterdir()), [])

    def test_refuses_destination_under_a_symlinked_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha")
            fixture.lock()
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real)
            with self.assertRaises(module.ToolchainError):
                module.stage(fixture.lock_path, fixture.cache, linked / "prefix")
            self.assertEqual(sorted(real.iterdir()), [])

    def test_prefix_env_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            env = module.prefix_env(prefix)
            self.assertEqual(env["SYSROOT"], str(prefix))
            self.assertEqual(env["CROSS_PREFIX"],
                             str(prefix) + "/usr/bin/arm-linux-gnueabihf-")
            self.assertEqual(env["LD_LIBRARY_PATH"],
                             str(prefix) + "/usr/lib/x86_64-linux-gnu")
            for value in env.values():
                self.assertNotIn("/mnt", value)


# --------------------------------------------------------------------------
# 4b. Publication confinement: the destination parent must be a *held*
#     directory identity, never re-resolved from an attacker-swappable path.
#     Regression for the SPEC blocking gap (path-based RENAME_NOREPLACE with
#     no held descriptor: a parent swapped for an external symlink published
#     the whole prefix + receipt outside the intended tree).
# --------------------------------------------------------------------------

def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


class _SwapMissingParentForSymlink:
    """At the instant the materializer creates the destination parent, create a
    symlink to ``external`` instead of a directory.

    Installed on ``os.mkdir`` so the identical deterministic probe drives the
    pre-fix path-based call site (``Path.mkdir`` -> ``os.mkdir(self, mode)``)
    and the fixed descriptor-relative call site
    (``os.mkdir(name, mode, dir_fd=fd)``).  No sleeps: the swap happens exactly
    inside the create, after the ancestor check and before any bytes are written.
    """

    def __init__(self, target_parent: Path, external: Path):
        self.target_parent = os.path.abspath(os.fspath(target_parent))
        self.target_name = Path(target_parent).name
        self.external = os.fspath(external)
        self.fired = False

    def __enter__(self):
        self._real = os.mkdir

        def hook(path, mode=0o777, *, dir_fd=None):
            if not self.fired:
                if dir_fd is None and os.path.abspath(os.fspath(path)) == self.target_parent:
                    self.fired = True
                    os.symlink(self.external, os.fspath(path))
                    return None
                if dir_fd is not None and os.fspath(path) == self.target_name:
                    self.fired = True
                    os.symlink(self.external, path, dir_fd=dir_fd)
                    return None
            return self._real(path, mode, dir_fd=dir_fd)

        os.mkdir = hook
        return self

    def __exit__(self, *exc):
        os.mkdir = self._real
        return False


class PublicationConfinementTests(unittest.TestCase):
    def _fixture_with_external(self, root: Path):
        fixture = Fixture(root)
        fixture.add("alpha", body=b"secret-bytes")
        fixture.lock()
        external = root / "external"
        external.mkdir()
        (external / "marker").write_bytes(b"untouched")
        return fixture, external

    def _assert_confined(self, external: Path):
        self.assertEqual((external / "marker").read_bytes(), b"untouched",
                         "external marker was modified")
        self.assertEqual(sorted(p.name for p in external.iterdir()), ["marker"],
                         "materializer wrote into the attacker-controlled directory")
        self.assertFalse((external / "prefix").exists())
        self.assertFalse((external / "prefix" / module.RECEIPT_NAME).exists())

    def _resolved(self, outcome):
        """A confined outcome is a refusal or a *reported detached* success."""
        if not isinstance(outcome, module.ToolchainError):
            self.assertTrue(outcome.get("detached"),
                            "publication into a detached parent must be reported")
        return outcome

    def test_missing_parent_swapped_for_external_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture_with_external(root)
            dest = root / "missing" / "prefix"   # parent does not exist yet
            with _SwapMissingParentForSymlink(dest.parent, external) as probe:
                try:
                    outcome = module.stage(fixture.lock_path, fixture.cache, dest)
                except module.ToolchainError as exc:
                    outcome = exc
            self.assertTrue(probe.fired, "the swap probe never ran")
            self._assert_confined(external)
            self._resolved(outcome)

    def test_parent_swapped_after_descriptor_acquired_stays_confined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture_with_external(root)
            dest = root / "missing" / "prefix"
            real_extract = getattr(module, "extract_deb_data")
            state = {"fired": False}

            def swap_then_extract(deb, stage_root, **kw):
                if not state["fired"]:
                    state["fired"] = True
                    os.rename(os.fspath(dest.parent), os.fspath(root / "detached-parent"))
                    os.symlink(os.fspath(external), os.fspath(dest.parent))
                return real_extract(deb, stage_root, **kw)

            setattr(module, "extract_deb_data", swap_then_extract)
            try:
                try:
                    outcome = module.stage(fixture.lock_path, fixture.cache, dest)
                except module.ToolchainError as exc:
                    outcome = exc
            finally:
                setattr(module, "extract_deb_data", real_extract)
            self.assertTrue(state["fired"])
            self._assert_confined(external)
            self._resolved(outcome)
            if not isinstance(outcome, module.ToolchainError):
                # The success must have landed in the *detached* held directory.
                self.assertTrue(
                    (root / "detached-parent" / "prefix" / module.RECEIPT_NAME).is_file())

    def test_existing_symlinked_ancestor_is_refused_and_target_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture_with_external(root)
            os.symlink(os.fspath(external), os.fspath(root / "missing"))
            with self.assertRaises(module.ToolchainError):
                module.stage(fixture.lock_path, fixture.cache, root / "missing" / "prefix")
            self._assert_confined(external)

    def test_stage_entry_swapped_before_publish_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture_with_external(root)
            dest = root / "prefix"
            real_rename = getattr(module, "_rename_noreplace")
            state = {"fired": False}

            def swap_then_rename(*args):
                if not state["fired"]:
                    state["fired"] = True
                    if len(args) == 4 and isinstance(args[1], str):
                        sfd, sname, dfd, dname = args
                        os.rename(sname, sname + ".swapped", src_dir_fd=sfd, dst_dir_fd=sfd)
                        os.symlink(os.fspath(external), sname, dir_fd=sfd)
                    else:
                        src = Path(args[0])
                        os.rename(src, src.parent / (src.name + ".swapped"))
                        os.symlink(os.fspath(external), os.fspath(src))
                return real_rename(*args)

            setattr(module, "_rename_noreplace", swap_then_rename)
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    try:
                        outcome = module.stage(fixture.lock_path, fixture.cache, dest)
                    except module.ToolchainError as exc:
                        outcome = exc
            finally:
                setattr(module, "_rename_noreplace", real_rename)
            self.assertTrue(state["fired"])
            self.assertIsInstance(outcome, module.ToolchainError,
                                  "a swapped stage entry must never be published")
            self.assertEqual(sorted(p.name for p in external.iterdir()), ["marker"],
                             "the external symlink target was modified")
            # The foreign entry the attacker moved into the destination is left
            # in place as truthfully-reported residue -- cleanup must not delete
            # a foreign object to hide it -- and it is never this invocation's
            # stage (no receipt = no false success).
            self.assertTrue(dest.is_symlink(),
                            "foreign entry at the destination must be left in place")
            self.assertEqual(os.readlink(dest), os.fspath(external))
            self.assertFalse((external / module.RECEIPT_NAME).exists())
            self.assertTrue(any("residue" in str(w.message) for w in caught),
                            "cleanup must report the residual entries")

    def test_competitor_destination_before_rename_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture_with_external(root)
            dest = root / "prefix"
            real_rename = getattr(module, "_rename_noreplace")
            state = {"fired": False}

            def create_then_rename(*args):
                if not state["fired"]:
                    state["fired"] = True
                    if len(args) == 4 and isinstance(args[1], str):
                        dfd, dname = args[2], args[3]
                        os.mkdir(dname, dir_fd=dfd)
                        fd = os.open(dname + "/incumbent",
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=dfd)
                        os.write(fd, b"keep")
                        os.close(fd)
                    else:
                        Path(args[1]).mkdir()
                        (Path(args[1]) / "incumbent").write_bytes(b"keep")
                return real_rename(*args)

            setattr(module, "_rename_noreplace", create_then_rename)
            try:
                with self.assertRaises(module.ToolchainError):
                    module.stage(fixture.lock_path, fixture.cache, dest)
            finally:
                setattr(module, "_rename_noreplace", real_rename)
            self.assertTrue(state["fired"])
            self.assertEqual((dest / "incumbent").read_bytes(), b"keep")
            self.assertEqual(sorted(p.name for p in dest.iterdir()), ["incumbent"])
            self.assertEqual(sorted(p.name for p in external.iterdir()), ["marker"])

    def test_nested_destination_parents_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha", body=b"nested")
            fixture.lock()
            dest = root / "new1" / "new2" / "prefix"
            result = module.stage(fixture.lock_path, fixture.cache, dest)
            self.assertTrue((dest / "usr/share/demo/data").is_file())
            self.assertTrue((dest / module.RECEIPT_NAME).is_file())
            self.assertFalse(result.get("detached"))

    def test_failure_removes_only_owned_created_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            # An unsafe member makes extraction fail after the parents exist.
            fixture.add("evil", members=[_file("usr/../../escape", b"x")])
            fixture.lock()
            keep = root / "keep"
            keep.mkdir()
            (keep / "marker").write_bytes(b"kept")
            with self.assertRaises(module.ToolchainError):
                module.stage(fixture.lock_path, fixture.cache, root / "new1" / "new2" / "prefix")
            self.assertFalse((root / "new1").exists(), "owned created parents leaked")
            self.assertEqual((keep / "marker").read_bytes(), b"kept")
            residue = [p.name for p in root.iterdir()
                       if p.name not in {"cache", "lock.json", "keep"}]
            self.assertEqual(residue, [])

    def test_repeated_rejected_stage_does_not_leak_descriptors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha")
            fixture.lock()
            prefix = root / "prefix"
            prefix.mkdir()

            def one():
                with self.assertRaises(module.ToolchainError):
                    module.stage(fixture.lock_path, fixture.cache, prefix)

            one()  # warm caches / one-time descriptors
            before = _fd_count()
            for _ in range(30):
                one()
            self.assertLessEqual(_fd_count(), before,
                                 "file descriptors leaked across rejected stages")

    def test_repeated_successful_stage_verify_does_not_leak_descriptors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha", body=b"ok")
            fixture.lock()

            def one(index):
                dest = root / ("p%02d" % index)
                module.stage(fixture.lock_path, fixture.cache, dest)
                module.verify(fixture.lock_path, fixture.cache, dest)
                shutil.rmtree(dest)

            one(0)  # warm
            before = _fd_count()
            for index in range(1, 10):
                one(index)
            self.assertLessEqual(_fd_count(), before,
                                 "file descriptors leaked across successful stages")


# --------------------------------------------------------------------------
# 5. Verify: re-derive from archives, not the receipt
# --------------------------------------------------------------------------

class VerifyTests(unittest.TestCase):
    def _staged(self, root: Path):
        fixture = Fixture(root)
        fixture.add("alpha", body=b"alpha")
        fixture.add("beta", version="2.0-1", body=b"beta", rel="usr/share/beta/data")
        fixture.stage()
        return fixture

    def test_verify_accepts_a_faithful_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            result = module.verify(fixture.lock_path, fixture.cache, fixture.root / "prefix")
            self.assertEqual(result["archives"], 2)
            self.assertRegex(result["tree_sha256"], r"^[0-9a-f]{64}$")

    def test_verify_rejects_tampered_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            (fixture.root / "prefix/usr/share/demo/data").write_bytes(b"tampered")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, fixture.root / "prefix")

    def test_verify_rejects_consistent_forged_receipt_plus_tampered_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            prefix = fixture.root / "prefix"
            (prefix / "usr/share/demo/data").write_bytes(b"tampered")
            # The attacker recomputes the receipt to match the tampered prefix.
            receipt_path = prefix / module.RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text())
            forged = module.tree_manifest(prefix, exclude={module.RECEIPT_NAME})
            receipt["tree"]["sha256"] = forged["sha256"]
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, prefix)

    def test_verify_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            (fixture.root / "prefix/usr/share/demo/data").unlink()
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, fixture.root / "prefix")

    def test_verify_rejects_extra_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            (fixture.root / "prefix/usr/share/demo/injected").write_bytes(b"x")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, fixture.root / "prefix")

    def test_verify_rejects_mode_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            target = fixture.root / "prefix/usr/share/demo/data"
            os.chmod(target, 0o600)
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, fixture.root / "prefix")

    def test_verify_rejects_missing_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            (fixture.root / "prefix" / module.RECEIPT_NAME).unlink()
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, fixture.root / "prefix")

    def test_verify_rejects_receipt_bound_to_a_different_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            prefix = fixture.root / "prefix"
            receipt_path = prefix / module.RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text())
            receipt["lock_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, prefix)

    def test_verify_rejects_receipt_with_wrong_archive_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            prefix = fixture.root / "prefix"
            receipt_path = prefix / module.RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text())
            receipt["archives"][0]["sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, prefix)

    def test_verify_rejects_absolute_path_in_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            prefix = fixture.root / "prefix"
            receipt_path = prefix / module.RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text())
            receipt["source"] = "/home/test-user/checkout"
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, prefix)

    def test_verify_rejects_escaping_symlink_in_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            prefix = fixture.root / "prefix"
            (prefix / "usr/escape").symlink_to("../../../../etc/passwd")
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.cache, prefix)

    def test_verify_requires_the_archive_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._staged(Path(tmp))
            with self.assertRaises(module.ToolchainError):
                module.verify(fixture.lock_path, fixture.root / "does-not-exist",
                              fixture.root / "prefix")


# --------------------------------------------------------------------------
# 6. Real CLI over subprocess (bounded) + opt-in real closure
# --------------------------------------------------------------------------

class CliSubprocessTests(unittest.TestCase):
    def _run(self, args, timeout=120):
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), *args],
            capture_output=True, text=True, timeout=timeout,
        )

    def test_cli_stage_and_verify_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha", body=b"abc")
            fixture.lock()
            prefix = fixture.root / "prefix"
            staged = self._run(["stage", "--lock", str(fixture.lock_path),
                                "--archives", str(fixture.cache), "--prefix", str(prefix)])
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertIn("armhf_toolchain_stage=PASS", staged.stdout)
            self.assertTrue((prefix / module.RECEIPT_NAME).is_file())
            verified = self._run(["verify", "--lock", str(fixture.lock_path),
                                  "--archives", str(fixture.cache), "--prefix", str(prefix)])
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("armhf_toolchain_verify=PASS", verified.stdout)

    def test_cli_stage_fails_closed_and_reports_nothing_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp))
            fixture.add("alpha")
            lock_path = fixture.lock()
            (fixture.cache / fixture.debs[0].name).unlink()
            result = self._run(["stage", "--lock", str(lock_path),
                                "--archives", str(fixture.cache),
                                "--prefix", str(fixture.root / "prefix")])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((fixture.root / "prefix").exists())

    def test_cli_env_prints_the_prefix_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            result = self._run(["env", "--prefix", str(prefix)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"SYSROOT={prefix}", result.stdout)
            self.assertIn("CROSS_PREFIX=%s/usr/bin/arm-linux-gnueabihf-" % prefix, result.stdout)
            self.assertIn("LD_LIBRARY_PATH=%s/usr/lib/x86_64-linux-gnu" % prefix, result.stdout)


class ZstdBackendTests(unittest.TestCase):
    """Host zstd dependency: the declared alternatives and their failure mode."""

    RAW = b"libreecho-sendspin-zstd-backend-probe" * 8

    def setUp(self):
        self.payload = zstd_payload(self.RAW)

    def test_absent_zstd_backend_error_names_every_supported_alternative(self):
        if self.payload is None:
            self.skipTest("no local zstd encoder to build a fixture")
        with ZstdBackends(stdlib=False, py_module=False, cli=False):
            with self.assertRaises(module.ToolchainError) as ctx:
                module._zstd_stream(self.payload)
        message = str(ctx.exception).lower()
        self.assertIn("no zstd backend", message)
        # The failure must tell the operator exactly how to make the host usable,
        # naming every alternative the module actually implements.
        for token in ("compression.zstd", "zstandard", "zstd", "3.14"):
            self.assertIn(token, message,
                          "no-backend error must name a supported alternative")

    def test_stdlib_zstd_backend_round_trips(self):
        if self.payload is None:
            self.skipTest("no local zstd encoder to build a fixture")
        try:
            import compression.zstd  # noqa: F401
        except ImportError:
            self.skipTest("host Python lacks the compression.zstd stdlib backend")
        with ZstdBackends(stdlib=True, py_module=False, cli=False):
            self.assertEqual(module._zstd_stream(self.payload).read(), self.RAW)

    def test_cli_zstd_backend_round_trips(self):
        if self.payload is None:
            self.skipTest("no local zstd encoder to build a fixture")
        if not shutil.which("zstd"):
            self.skipTest("host has no zstd CLI")
        with ZstdBackends(stdlib=False, py_module=False, cli=True):
            self.assertEqual(module._zstd_stream(self.payload).read(), self.RAW)

    def test_cli_zstd_backend_timeout_is_a_bounded_toolchain_error(self):
        # A hung zstd CLI must yield the bounded exit-2 ToolchainError, not an
        # uncaught subprocess.TimeoutExpired traceback.
        if self.payload is None:
            self.skipTest("no local zstd encoder to build a fixture")
        if not shutil.which("sh"):
            self.skipTest("no shell to build the stand-in zstd tool")
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "zstd"
            tool.write_text("#!/bin/sh\nsleep 30\n")
            tool.chmod(0o755)
            real_which = module.shutil.which
            saved_timeout = module.ZSTD_TIMEOUT
            try:
                module.ZSTD_TIMEOUT = 0.2
                with ZstdBackends(stdlib=False, py_module=False, cli=True):
                    module.shutil.which = (
                        lambda name: str(tool) if name == "zstd" else real_which(name))
                    with self.assertRaises(module.ToolchainError) as ctx:
                        module._zstd_stream(self.payload)
            finally:
                module.ZSTD_TIMEOUT = saved_timeout
                module.shutil.which = real_which
        self.assertIn("timed out", str(ctx.exception).lower())


class CleanupOwnershipTests(unittest.TestCase):
    """Failed-run cleanup must never delete a directory it does not own.

    Regression for QUALITY I2 (probe P3): a foreign real directory swapped into
    the stage name before cleanup was recursively deleted.
    """

    def _fixture(self, root: Path):
        fixture = Fixture(root)
        fixture.add("alpha", body=b"secret-bytes")
        fixture.lock()
        external = root / "external"
        external.mkdir()
        (external / "marker").write_bytes(b"untouched")
        return fixture, external

    def _assert_external_confined(self, external: Path):
        self.assertEqual((external / "marker").read_bytes(), b"untouched",
                         "the external symlink target was modified")
        self.assertEqual(sorted(p.name for p in external.iterdir()), ["marker"])

    def _stage_name(self, root: Path) -> str:
        return next(p.name for p in root.iterdir()
                    if p.name.startswith(".armhf-toolchain-stage-"))

    def test_normal_failed_run_cleanup_reports_no_residue(self):
        # A failing run whose untouched stage can be removed cleanly must be
        # removed with NO residue diagnostic; the recursion must not report the
        # directory it already removed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Fixture(root)
            fixture.add("alpha", body=b"first")
            fixture.add("beta", version="2.0-1", body=b"second",
                        rel="usr/share/demo/data")   # different bytes -> conflict
            fixture.lock()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with self.assertRaises(module.ToolchainError):
                    module.stage(fixture.lock_path, fixture.cache, root / "prefix")
            self.assertEqual([p.name for p in root.iterdir()
                              if p.name.startswith(".armhf-")], [],
                             "a cleanly-removable stage must leave no residue")
            self.assertEqual([str(w.message) for w in caught], [],
                             "clean cleanup must not warn about residue")

    def test_foreign_real_dir_swapped_at_stage_name_before_cleanup_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture(root)
            dest = root / "prefix"
            real_manifest = module.tree_manifest
            state = {"fired": False, "stage": None, "swapped": None}

            def swap_then_manifest(root_arg, *a, **kw):
                if not state["fired"]:
                    state["fired"] = True
                    name = self._stage_name(root)
                    state["stage"] = name
                    state["swapped"] = name + ".swapped"
                    os.rename(root / name, root / state["swapped"])
                    os.mkdir(root / name)
                    (root / name / "FOREIGN_SENTINEL").write_bytes(
                        b"valuable-foreign-data")
                return real_manifest(root_arg, *a, **kw)

            setattr(module, "tree_manifest", swap_then_manifest)
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    try:
                        outcome = module.stage(fixture.lock_path, fixture.cache, dest)
                    except module.ToolchainError as exc:
                        outcome = exc
            finally:
                setattr(module, "tree_manifest", real_manifest)

            self.assertTrue(state["fired"], "the swap probe never ran")
            self.assertIsInstance(outcome, module.ToolchainError,
                                  "a replaced stage entry must fail closed")
            self.assertFalse(dest.exists())
            self.assertTrue(
                (root / state["stage"] / "FOREIGN_SENTINEL").is_file(),
                "cleanup deleted a foreign directory it did not create")
            self.assertEqual(
                (root / state["stage"] / "FOREIGN_SENTINEL").read_bytes(),
                b"valuable-foreign-data")
            # This invocation's own stage may remain as residue, honestly reported.
            self.assertTrue((root / state["swapped"]).is_dir())
            self.assertTrue(any("residue" in str(w.message) for w in caught),
                            "cleanup must report the residual entries")
            self._assert_external_confined(external)

    def test_foreign_dir_swapped_between_cleanup_check_and_open_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture, external = self._fixture(root)
            dest = root / "prefix"
            real_manifest = module.tree_manifest
            real_open = os.open
            state = {"armed": False, "fired": False, "stage": None}

            def failing_manifest(*a, **kw):
                state["armed"] = True
                state["stage"] = self._stage_name(root)
                raise module.ToolchainError("injected manifest failure")

            def hooked_open(path, flags, *a, **kw):
                if (state["armed"] and not state["fired"]
                        and kw.get("dir_fd") is not None
                        and os.fspath(path) == state["stage"]):
                    state["fired"] = True
                    os.rename(root / state["stage"],
                              root / (state["stage"] + ".swapped"))
                    os.mkdir(root / state["stage"])
                    (root / state["stage"] / "FOREIGN_SENTINEL").write_bytes(b"foreign")
                return real_open(path, flags, *a, **kw)

            setattr(module, "tree_manifest", failing_manifest)
            setattr(module.os, "open", hooked_open)
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    try:
                        outcome = module.stage(fixture.lock_path, fixture.cache, dest)
                    except module.ToolchainError as exc:
                        outcome = exc
            finally:
                setattr(module, "tree_manifest", real_manifest)
                setattr(module.os, "open", real_open)

            self.assertTrue(state["armed"] and state["fired"],
                            "the check/open swap probe never ran")
            self.assertIsInstance(outcome, module.ToolchainError)
            self.assertTrue(
                (root / state["stage"] / "FOREIGN_SENTINEL").is_file(),
                "cleanup recursed into a directory swapped in at the check/open "
                "boundary")
            self.assertTrue(any("residue" in str(w.message) for w in caught))
            self._assert_external_confined(external)


@unittest.skipUnless(REAL_ARCHIVES, "set ARMHF_TOOLCHAIN_ARCHIVE_DIR to run the real closure")
class RealClosureTests(unittest.TestCase):
    """Opt-in end-to-end over the actual 30 authenticated archives."""

    def test_real_closure_stage_and_verify(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            result = module.stage(LOCK_PATH, Path(REAL_ARCHIVES), prefix)
            self.assertEqual(result["archives"], 30)
            verified = module.verify(LOCK_PATH, Path(REAL_ARCHIVES), prefix)
            self.assertEqual(verified["archives"], 30)
            # The prefix is consumer-readable and carries the expected tools.
            self.assertTrue((prefix / "usr/bin/arm-linux-gnueabihf-gcc").exists())
            self.assertTrue((prefix / "usr/arm-linux-gnueabihf/lib/libc.so.6").exists())
            self.assertTrue((prefix / "usr/arm-linux-gnueabihf/include/stdio.h").exists())
            self.assertTrue((prefix / "usr/include/tbb/tbb.h").exists())
            shutil.rmtree(prefix)


if __name__ == "__main__":
    unittest.main()
