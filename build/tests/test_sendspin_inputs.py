#!/usr/bin/env python3
"""Focused contract test: Sendspin pinned inputs and architecture identity.

Task 1 gate. The Sendspin integration must be declared from immutable,
hash-pinned upstream sources with one reconciled protocol/SDK/oracle identity,
and the Product architecture document must carry that same identity. The test
fails closed on: a missing digest, an unpinned transitive, an uncontrolled
runtime download, an escaping submodule path, a hash drift in the real staging
path, a stale staged tree, or an inconsistent protocol/SDK identity.

The loader/contract tests never touch the network. The staging tests exercise
the *real* downloader/verify/extract path (`stage_sendspin`, `download_to`,
`stage_archive_tree`) against hermetic `tarfile` fixtures and local archive
caches -- never a mocked network response.

Run with:

    python3 -m unittest discover -s build/tests -p test_sendspin_inputs.py -v
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
ARCH_DOC = ROOT / "docs/architecture/sendspin.md"
INVENTORY = ROOT / "build/inputs/public-inputs.json"
FETCHER = ROOT / "build/ci/fetch-public-deps.py"

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load("fetch_public_deps", FETCHER)


# --- Frozen identity (verified against Sendspin upstream) -------------------
SPEC_COMMIT = "671a34d408581fc25ea56b3528a4a3f13e3be901"
SPEC_SHA256 = "f0890562729069498cc5da57b63d1eea5f24cd661dc6dfc0c168614a216ffd3e"
SDK_COMMIT = "8cdd4b38d029f3ef754756d494e0b53c42b81d75"
SDK_SHA256 = "2749ee07e4974f3879297af12d651349df33f46125a100cc935bf6966ed8af45"
ORACLE_COMMIT = "83209af414e1950dbbd0ebf60a9c0567b2c5f0c8"
ORACLE_SHA256 = "805dc716dc6bb80115040c476610342f27b68b44462bbcc0dd2713c2e98fba15"

# The reviewed SDK patch set and the canonical verifier tree digest. The digest
# is the mode-aware canonical value produced by the Platform verifier (and the
# Product materializer): both extract with tarfile filter="data", which keeps
# the stored member permissions (it clears only setuid/setgid/sticky and
# group/other write bits) and applies them with an explicit chmod, so the value
# is umask-independent -- unlike a `tar` CLI extraction, whose member modes are
# masked by the process umask. The two patches are applied in declared
# order (0001 then 0002), so the second diffs the 0001-patched tree.
SDK_PATCH_FILE = "0001-stream-end-reason.patch"
SDK_PATCH_SHA256 = "d9273acc1e014ddc1550b5791a41d8ad360b9639b9f8059c3c6cb4f38ebe7090"
SDK_PATCH_FILE_2 = "0002-stream-clear-boundary.patch"
SDK_PATCH_SHA256_2 = "b01f3088e52e779070de4f1ef5191aad1c1e9501e320fb25746b1e244c59007b"
SDK_PATCHES = (
    (SDK_PATCH_FILE, SDK_PATCH_SHA256),
    (SDK_PATCH_FILE_2, SDK_PATCH_SHA256_2),
)
CANONICAL_SDK_PATCHED_TREE_SHA256 = (
    "b2aa25866dd445f6e2756e7e490986382a9639871936b7eafde6a9dc1a4acb80"
)

# name -> {commit, archive_sha256, submodules: {path: (commit, sha256)}}
EXPECTED = {
    "sendspin-protocol-spec": {"commit": SPEC_COMMIT, "sha": SPEC_SHA256},
    "sendspin-sdk": {"commit": SDK_COMMIT, "sha": SDK_SHA256},
    "sendspin-server-oracle": {"commit": ORACLE_COMMIT, "sha": ORACLE_SHA256},
    "sendspin-arduinojson": {
        "commit": "32520135092970120a5ac165cf45f48e658c421d",
        "sha": "9e48d9b1c690eed365e95a690c76f917af104eb74ffe3916568351dc78d45b2e",
    },
    "sendspin-ixwebsocket": {
        "commit": "c5a02f1066fb0fde48f80f51178429a27f689a39",
        "sha": "ef272693e67daef33275daa8d3685f48e8fe4dbe098338750f9dad3013016d96",
    },
    "sendspin-micro-flac": {
        "commit": "9f8bfe5c9ee46cea175084b49ae8ac95545705b5",
        "sha": "bab7a0adc5a5a016d32bcb7cb17f38477f2397d4ef2d6f6de2dfecb990388bb7",
        "submodules": {
            "lib/micro-ogg-demuxer": (
                "865ad9d831e7dc76bb9c142607bae33fc75648e7",
                "bb81cc1b64d1d888e5d5c93d12d07629887d1a197ab699e196fc37698e9f47bd",
            )
        },
    },
    "sendspin-noise-c": {
        "commit": "a1e08809a1b8f65cd91765ba7d68a6d00648ad61",
        "sha": "0bfe220508f412c3944a9fe1ed38b741940a5bbcb001cd751f79fb0ee6ec63a8",
    },
}

# Opus is patent-encumbered and PCM-only is the frozen target; these must not
# enter the staged closure.
FORBIDDEN = {"sendspin-micro-opus", "sendspin-opus", "sendspin-esp-websocket-client"}


def _records():
    data = module.load(INVENTORY)
    return {item["name"]: item for item in data["inputs"]}


class SendspinInputContractTests(unittest.TestCase):
    def test_inventory_loads_and_carries_the_sendspin_closure(self):
        records = _records()
        missing = sorted(set(EXPECTED) - set(records))
        self.assertEqual(missing, [], f"missing sendspin inventory records: {missing}")

    def test_sendspin_records_are_immutable_hash_pinned_git_sources(self):
        records = _records()
        for name, spec in EXPECTED.items():
            with self.subTest(name=name):
                rec = records[name]
                self.assertEqual(rec["kind"], "source-git")
                self.assertEqual(rec["redistribution"], "source-git-pinned")
                self.assertTrue(rec["url"].startswith("https://"), rec["url"])
                self.assertRegex(rec["commit"], HEX40)
                self.assertEqual(rec["commit"], spec["commit"])
                self.assertTrue(rec["archive_url"].startswith("https://"))
                self.assertRegex(rec["archive_sha256"], HEX64)
                self.assertEqual(rec["archive_sha256"], spec["sha"])
                # Identity: the committed archive URL must embed the pinned commit.
                self.assertTrue(
                    rec["archive_url"].endswith(rec["commit"] + ".tar.gz"),
                    f"{name}: archive_url does not embed the pinned commit",
                )
                self.assertTrue(rec["license"])

    def test_transitive_closure_pins_submodules_and_rejects_moving_tags(self):
        records = _records()
        for name, rec in records.items():
            if not name.startswith("sendspin-"):
                continue
            for key in ("url", "archive_url"):
                value = rec.get(key, "")
                self.assertNotIn("/refs/tags/", value, f"{name}.{key} is a moving ref")
            for sub in rec.get("submodules", []):
                with self.subTest(name=name, sub=sub.get("path")):
                    self.assertTrue(sub["url"].startswith("https://"))
                    self.assertRegex(sub["commit"], HEX40)
                    self.assertRegex(sub["archive_sha256"], HEX64)
                    self.assertTrue(sub["archive_url"].endswith(sub["commit"] + ".tar.gz"))
        flac = records["sendspin-micro-flac"]
        subs = {s["path"]: s for s in flac.get("submodules", [])}
        self.assertIn("lib/micro-ogg-demuxer", subs)
        self.assertEqual(
            subs["lib/micro-ogg-demuxer"]["commit"],
            EXPECTED["sendspin-micro-flac"]["submodules"]["lib/micro-ogg-demuxer"][0],
        )
        self.assertEqual(
            subs["lib/micro-ogg-demuxer"]["archive_sha256"],
            EXPECTED["sendspin-micro-flac"]["submodules"]["lib/micro-ogg-demuxer"][1],
        )

    def test_opus_and_unused_roles_are_not_staged(self):
        records = _records()
        for name in FORBIDDEN:
            self.assertNotIn(name, records, f"{name} must not be staged (PCM-only freeze)")
        self.assertNotIn("sendspin-micro-opus", records)
        doc = ARCH_DOC.read_text(encoding="utf-8")
        self.assertIn("SENDSPIN_ENABLE_OPUS=OFF", doc)

    def test_loader_rejects_sendspin_record_without_archive_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            path.write_text(json.dumps({"schema": module.SCHEMA, "inputs": [{
                "name": "sendspin-sdk",
                "url": "https://github.com/Sendspin/sendspin-cpp.git",
                "commit": SDK_COMMIT,
                "archive_url": f"https://github.com/Sendspin/sendspin-cpp/archive/{SDK_COMMIT}.tar.gz",
                "sha256": "",
                "kind": "source-git",
                "license": "Apache-2.0",
                "redistribution": "source-git-pinned",
            }]}))
            with self.assertRaises(ValueError):
                module.load(path)

    def test_loader_rejects_inconsistent_sendspin_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            path.write_text(json.dumps({"schema": module.SCHEMA, "inputs": [{
                "name": "sendspin-sdk",
                "url": "https://github.com/Sendspin/sendspin-cpp.git",
                "commit": SDK_COMMIT,
                # archive points at a different commit than the declared one
                "archive_url": "https://github.com/Sendspin/sendspin-cpp/archive/"
                               "0000000000000000000000000000000000000000.tar.gz",
                "archive_sha256": SDK_SHA256,
                "kind": "source-git",
                "license": "Apache-2.0",
                "redistribution": "source-git-pinned",
            }]}))
            with self.assertRaises(ValueError):
                module.load(path)

    def test_loader_rejects_unpinned_sendspin_submodule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            path.write_text(json.dumps({"schema": module.SCHEMA, "inputs": [{
                "name": "sendspin-micro-flac",
                "url": "https://github.com/esphome-libs/micro-flac.git",
                "commit": EXPECTED["sendspin-micro-flac"]["commit"],
                "archive_url": "https://github.com/esphome-libs/micro-flac/archive/"
                               + EXPECTED["sendspin-micro-flac"]["commit"] + ".tar.gz",
                "archive_sha256": EXPECTED["sendspin-micro-flac"]["sha"],
                "kind": "source-git",
                "license": "Apache-2.0",
                "redistribution": "source-git-pinned",
                "submodules": [{
                    "path": "lib/micro-ogg-demuxer",
                    "url": "https://github.com/esphome-libs/micro-ogg-demuxer.git",
                    "commit": "main",
                    "archive_url": "https://github.com/esphome-libs/micro-ogg-demuxer/archive/main.tar.gz",
                    "archive_sha256": "",
                }],
            }]}))
            with self.assertRaises(ValueError):
                module.load(path)

    def test_architecture_document_reconciles_the_frozen_identity(self):
        doc = ARCH_DOC.read_text(encoding="utf-8")
        for value in (SPEC_COMMIT, SDK_COMMIT, ORACLE_COMMIT):
            self.assertIn(value, doc, f"architecture doc omits frozen identity {value}")
        # The staged-closure ids must be described as offline, not tag-fetched.
        self.assertIn("FETCHCONTENT_SOURCE_DIR", doc)

    def test_architecture_document_defines_audio_sink_contract(self):
        doc = ARCH_DOC.read_text(encoding="utf-8")
        for token in (
            "/run/libreecho-audio/sendspin.sock",
            "SOCK_SEQPACKET",
            "LE_AUDIO_SINK/1",
            "2048",
            "little endian",
        ):
            self.assertIn(token, doc, f"architecture doc omits sink contract token {token!r}")

    def test_documentation_makes_no_production_ready_claim(self):
        doc = ARCH_DOC.read_text(encoding="utf-8").lower()
        self.assertIn("not production", doc)

    def test_fetcher_source_git_path_is_hash_verified_not_tag_following(self):
        source = FETCHER.read_text(encoding="utf-8")
        self.assertIn("archive_sha256", source)
        self.assertIn("archive_url", source)
        self.assertNotIn("GIT_TAG", source)

    def test_documentation_excludes_opus_by_policy_not_patent_claim(self):
        # The exclusion is a policy/freeze decision. Asserting Opus is
        # "patent-encumbered" (or that the freeze "avoids the patent surface")
        # is an uncited legal claim and must not be stated as fact.
        doc = ARCH_DOC.read_text(encoding="utf-8").lower()
        self.assertNotIn("patent-encumbered", doc)
        self.assertNotIn("patent surface", doc)
        self.assertIn("by policy", doc)


# --- Submodule path bounds --------------------------------------------------
BAD_SUBMODULE_PATHS = [
    "../../etc/evil",
    "/etc/evil",
    "lib/../../escape",
    "..",
    "",
    "a/./b",
    "a//b",
    "lib\\evil",
    "lib/..",
    "~",
]


def _make_archive(directory: Path, name: str, commit: str, files: dict) -> Path:
    top = f"{name}-{commit}"
    archive = directory / f"{top}.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for relative, body in files.items():
            info = tarfile.TarInfo(f"{top}/{relative}")
            info.size = len(body)
            handle.addfile(info, io.BytesIO(body))
    return archive


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(name: str, archive: Path, *, commit: str, submodules=None, url=None) -> dict:
    record = {
        "name": name,
        "url": "https://example.invalid/" + name + ".git",
        "sha256": "",
        "kind": "source-git",
        "license": "Apache-2.0",
        "redistribution": "source-git-pinned",
        "commit": commit,
        "archive_url": url if url is not None else archive.resolve().as_uri(),
        "archive_sha256": _sha256(archive),
    }
    if submodules:
        record["submodules"] = submodules
    return record


def _submodule(path: str, archive: Path, commit: str) -> dict:
    return {
        "path": path,
        "url": "https://example.invalid/sub.git",
        "commit": commit,
        "archive_url": archive.resolve().as_uri(),
        "archive_sha256": _sha256(archive),
    }


class SendspinSubmodulePathTests(unittest.TestCase):
    def test_loader_rejects_every_escaping_submodule_path(self):
        for bad in BAD_SUBMODULE_PATHS:
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as tmp:
                _make_archive(Path(tmp), "sub", "a" * 40, {"x.txt": b"x"})
                path = Path(tmp) / "inputs.json"
                path.write_text(json.dumps({
                    "schema": module.SCHEMA,
                    "inputs": [{
                        "name": "sendspin-micro-flac",
                        "url": "https://github.com/esphome-libs/micro-flac.git",
                        "commit": EXPECTED["sendspin-micro-flac"]["commit"],
                        "archive_url": "https://github.com/esphome-libs/micro-flac/archive/"
                                       + EXPECTED["sendspin-micro-flac"]["commit"] + ".tar.gz",
                        "archive_sha256": EXPECTED["sendspin-micro-flac"]["sha"],
                        "kind": "source-git",
                        "license": "Apache-2.0",
                        "redistribution": "source-git-pinned",
                        "submodules": [{
                            "path": bad,
                            "url": "https://github.com/esphome-libs/micro-ogg-demuxer.git",
                            "commit": "b" * 40,
                            "archive_url": "https://github.com/esphome-libs/micro-ogg-demuxer/archive/"
                                           + "b" * 40 + ".tar.gz",
                            "archive_sha256": "c" * 64,
                        }],
                    }],
                }))
                with self.assertRaises(ValueError):
                    module.load(path)

    def test_loader_rejects_duplicate_submodule_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            sub = {
                "path": "lib/micro-ogg-demuxer",
                "url": "https://github.com/esphome-libs/micro-ogg-demuxer.git",
                "commit": "b" * 40,
                "archive_url": "https://github.com/esphome-libs/micro-ogg-demuxer/archive/" + "b" * 40 + ".tar.gz",
                "archive_sha256": "c" * 64,
            }
            path.write_text(json.dumps({
                "schema": module.SCHEMA,
                "inputs": [{
                    "name": "sendspin-micro-flac",
                    "url": "https://github.com/esphome-libs/micro-flac.git",
                    "commit": EXPECTED["sendspin-micro-flac"]["commit"],
                    "archive_url": "https://github.com/esphome-libs/micro-flac/archive/"
                                   + EXPECTED["sendspin-micro-flac"]["commit"] + ".tar.gz",
                    "archive_sha256": EXPECTED["sendspin-micro-flac"]["sha"],
                    "kind": "source-git",
                    "license": "Apache-2.0",
                    "redistribution": "source-git-pinned",
                    "submodules": [dict(sub), dict(sub)],
                }],
            }))
            with self.assertRaises(ValueError):
                module.load(path)

    def test_loader_rejects_over_bound_submodule_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            subs = [{
                "path": f"lib/m{i}",
                "url": "https://github.com/esphome-libs/micro-ogg-demuxer.git",
                "commit": "b" * 40,
                "archive_url": "https://github.com/esphome-libs/micro-ogg-demuxer/archive/" + "b" * 40 + ".tar.gz",
                "archive_sha256": "c" * 64,
            } for i in range(module.MAX_SUBMODULES_PER_SOURCE + 1)]
            path.write_text(json.dumps({
                "schema": module.SCHEMA,
                "inputs": [{
                    "name": "sendspin-micro-flac",
                    "url": "https://github.com/esphome-libs/micro-flac.git",
                    "commit": EXPECTED["sendspin-micro-flac"]["commit"],
                    "archive_url": "https://github.com/esphome-libs/micro-flac/archive/"
                                   + EXPECTED["sendspin-micro-flac"]["commit"] + ".tar.gz",
                    "archive_sha256": EXPECTED["sendspin-micro-flac"]["sha"],
                    "kind": "source-git",
                    "license": "Apache-2.0",
                    "redistribution": "source-git-pinned",
                    "submodules": subs,
                }],
            }))
            with self.assertRaises(ValueError):
                module.load(path)

    def test_stage_rejects_escaping_path_before_fetching(self):
        inventory = {"inputs": [{
            "name": "sendspin-micro-flac",
            "commit": "a" * 40,
            "archive_url": "https://invalid.invalid/micro-flac.tar.gz",
            "archive_sha256": "d" * 64,
            "submodules": [{
                "path": "../escape",
                "commit": "b" * 40,
                "archive_url": "https://invalid.invalid/sub.tar.gz",
                "archive_sha256": "e" * 64,
            }],
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage"
            with self.assertRaises(ValueError):
                module.stage_sendspin(inventory, output)
            self.assertFalse(output.exists(), "no output must be created when a path is invalid")

    def test_symlink_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "component"
            outside = Path(tmp) / "outside"
            outside.mkdir()
            base.mkdir()
            (base / "lib").symlink_to(outside)
            with self.assertRaises(ValueError):
                module._assert_no_symlink_ancestors(base, "lib/micro-ogg-demuxer")

    def test_path_outside_output_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stage"
            root.mkdir()
            with self.assertRaises(ValueError):
                module._ensure_within(root, Path(tmp) / "escape")


class SendspinStagingBehaviorTests(unittest.TestCase):
    def test_stage_fails_closed_on_archive_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _make_archive(root, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            record = _record("sendspin-sdk", archive, commit="a" * 40)
            record["archive_sha256"] = "0" * 64  # drifted pin
            output = root / "stage"
            with self.assertRaises(ValueError):
                module.stage_sendspin({"inputs": [record]}, output)
            self.assertFalse((output / "sendspin-sdk").exists())
            # No partial archive is left behind either.
            leftovers = [p for p in output.iterdir()] if output.exists() else []
            self.assertEqual(leftovers, [])

    def test_stage_extracts_verified_archive_and_reports_content_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _make_archive(root, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            record = _record("sendspin-sdk", archive, commit="a" * 40)
            output = root / "stage"
            receipt = module.stage_sendspin({"inputs": [record]}, output)
            self.assertTrue(receipt["complete"])
            entry = receipt["entries"]["sendspin-sdk"]
            self.assertTrue(entry["archive_verified"])
            self.assertEqual(entry["archive_sha256_observed"], entry["archive_sha256_declared"])
            self.assertEqual(
                (output / "sendspin-sdk" / "include" / "sdk.h").read_bytes(), b"real bytes"
            )
            digest, count = module.tree_digest(output / "sendspin-sdk")
            self.assertEqual(entry["tree_sha256"], digest)
            self.assertEqual(entry["file_count"], count)
            self.assertTrue(Path(receipt["receipt_path"]).is_file())

    def test_stage_materializes_pinned_submodule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = _make_archive(root, "micro-flac", "a" * 40, {"src/flac.c": b"flac"})
            sub = _make_archive(root, "micro-ogg-demuxer", "b" * 40, {"src/ogg.c": b"ogg"})
            record = _record(
                "sendspin-micro-flac", parent, commit="a" * 40,
                submodules=[_submodule("lib/micro-ogg-demuxer", sub, "b" * 40)],
            )
            output = root / "stage"
            receipt = module.stage_sendspin({"inputs": [record]}, output)
            self.assertEqual(
                (output / "sendspin-micro-flac" / "lib" / "micro-ogg-demuxer" / "src" / "ogg.c").read_bytes(),
                b"ogg",
            )
            self.assertIn("sendspin-micro-flac::lib/micro-ogg-demuxer", receipt["entries"])

    def test_stage_refuses_to_merge_into_stale_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _make_archive(root, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            record = _record("sendspin-sdk", archive, commit="a" * 40)
            output = root / "stage"
            stale = output / "sendspin-sdk"
            stale.mkdir(parents=True)
            (stale / "stale.txt").write_bytes(b"old")
            with self.assertRaises(ValueError):
                module.stage_sendspin({"inputs": [record]}, output)
            self.assertEqual((stale / "stale.txt").read_bytes(), b"old")
            self.assertFalse((stale / "include").exists())

    # --- P1-1/P1-2: real submodule staging is re-runnable and self-consistent ---

    def _submodule_fixture(self, root: Path) -> dict:
        parent = _make_archive(root, "micro-flac", "a" * 40, {"src/flac.c": b"flac"})
        sub = _make_archive(root, "micro-ogg-demuxer", "b" * 40, {"src/ogg.c": b"ogg"})
        return _record(
            "sendspin-micro-flac", parent, commit="a" * 40,
            submodules=[_submodule("lib/micro-ogg-demuxer", sub, "b" * 40)],
        )

    def test_stage_sendspin_two_run_rerun_with_real_submodule_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self._submodule_fixture(root)
            output = root / "stage"
            first = module.stage_sendspin({"inputs": [record]}, output)
            second = module.stage_sendspin({"inputs": [record]}, output)
            self.assertEqual(
                first["entries"]["sendspin-micro-flac"]["tree_sha256"],
                second["entries"]["sendspin-micro-flac"]["tree_sha256"],
            )
            for receipt in (first, second):
                with self.subTest(run=id(receipt)):
                    disk, count = module.tree_digest(output / "sendspin-micro-flac")
                    self.assertEqual(
                        receipt["entries"]["sendspin-micro-flac"]["tree_sha256"], disk
                    )
                    self.assertEqual(
                        receipt["entries"]["sendspin-micro-flac"]["file_count"], count
                    )

    def test_stage_sendspin_rerun_with_empty_placeholder_parent_submodule(self):
        # The parent archive carries an empty placeholder directory at the
        # submodule path; a second stage into the same output must still succeed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent.tar.gz"
            with tarfile.open(parent, "w:gz") as handle:
                placeholder = tarfile.TarInfo("top/lib/micro-ogg-demuxer")
                placeholder.type = tarfile.DIRTYPE
                handle.addfile(placeholder)
                readme = tarfile.TarInfo("top/README")
                readme.size = 3
                handle.addfile(readme, io.BytesIO(b"abc"))
            sub = _make_archive(root, "micro-ogg-demuxer", "b" * 40, {"src/ogg.c": b"ogg"})
            record = _record(
                "sendspin-micro-flac", parent, commit="a" * 40,
                submodules=[_submodule("lib/micro-ogg-demuxer", sub, "b" * 40)],
            )
            output = root / "stage"
            first = module.stage_sendspin({"inputs": [record]}, output)
            second = module.stage_sendspin({"inputs": [record]}, output)
            self.assertEqual(
                first["entries"]["sendspin-micro-flac"]["tree_sha256"],
                second["entries"]["sendspin-micro-flac"]["tree_sha256"],
            )
            self.assertEqual((output / "sendspin-micro-flac/README").read_bytes(), b"abc")
            self.assertEqual(
                (output / "sendspin-micro-flac/lib/micro-ogg-demuxer/src/ogg.c").read_bytes(),
                b"ogg",
            )
            disk, _ = module.tree_digest(output / "sendspin-micro-flac")
            self.assertEqual(second["entries"]["sendspin-micro-flac"]["tree_sha256"], disk)

    def test_stage_sendspin_receipt_matches_final_disk_for_every_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self._submodule_fixture(root)
            output = root / "stage"
            receipt = module.stage_sendspin({"inputs": [record]}, output)
            self.assertEqual(
                set(receipt["entries"]),
                {"sendspin-micro-flac", "sendspin-micro-flac::lib/micro-ogg-demuxer"},
            )
            for key, entry in receipt["entries"].items():
                with self.subTest(entry=key):
                    staged = output / entry["staged_path"]
                    disk, count = module.tree_digest(staged)
                    self.assertEqual(entry["tree_sha256"], disk)
                    self.assertEqual(entry["file_count"], count)
                    self.assertTrue(entry["archive_verified"])
                    self.assertEqual(
                        entry["archive_sha256_observed"], entry["archive_sha256_declared"]
                    )

    def test_stage_refuses_divergent_parent_content_at_submodule_path(self):
        # The parent archive ships *content* at the declared submodule path;
        # overlaying the submodule onto it would silently merge stale bytes.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent.tar.gz"
            with tarfile.open(parent, "w:gz") as handle:
                nested = tarfile.TarInfo("top/lib/micro-ogg-demuxer/vendored.c")
                nested.size = 5
                handle.addfile(nested, io.BytesIO(b"stale"))
            sub = _make_archive(root, "micro-ogg-demuxer", "b" * 40, {"src/ogg.c": b"ogg"})
            record = _record(
                "sendspin-micro-flac", parent, commit="a" * 40,
                submodules=[_submodule("lib/micro-ogg-demuxer", sub, "b" * 40)],
            )
            output = root / "stage"
            with self.assertRaises(ValueError):
                module.stage_sendspin({"inputs": [record]}, output)
            self.assertFalse((output / "sendspin-micro-flac").exists())
            self.assertFalse((output / "sendspin-stage-receipt.json").exists())

    def test_stage_bad_submodule_hash_leaves_no_partial_component_or_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self._submodule_fixture(root)
            record["submodules"][0]["archive_sha256"] = "0" * 64  # drifted child pin
            output = root / "stage"
            with self.assertRaises(ValueError):
                module.stage_sendspin({"inputs": [record]}, output)
            self.assertFalse((output / "sendspin-micro-flac").exists())
            self.assertFalse((output / "sendspin-stage-receipt.json").exists())
            leftovers = sorted(p.name for p in output.iterdir()) if output.exists() else []
            self.assertEqual(leftovers, [])

    # --- P2-1: no vacuous PASS on an empty or incomplete closure --------------

    def test_stage_rejects_empty_sendspin_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage"
            with self.assertRaises(ValueError):
                module.stage_sendspin({"inputs": []}, output)
            self.assertFalse(output.exists())

    def test_stage_rejects_closure_with_no_sendspin_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage"
            inventory = {"inputs": [{
                "name": "tts-northern-upstream", "url": "https://x.invalid/y.onnx",
                "sha256": "a" * 64, "kind": "archive", "license": "MIT",
                "redistribution": "cleared",
            }]}
            with self.assertRaises(ValueError):
                module.stage_sendspin(inventory, output)
            self.assertFalse(output.exists())

    def test_stage_enforces_injected_required_closure(self):
        # A small injectable closure keeps the test independent of the real
        # production closure while still proving the gate fail-closes.
        required = {"sendspin-alpha", "sendspin-beta"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _make_archive(root, "alpha", "a" * 40, {"a.txt": b"a"})
            beta = _make_archive(root, "beta", "b" * 40, {"b.txt": b"b"})
            records = [
                _record("sendspin-alpha", alpha, commit="a" * 40),
                _record("sendspin-beta", beta, commit="b" * 40),
            ]
            output = root / "stage"
            receipt = module.stage_sendspin(
                {"inputs": records}, output, required_closure=required
            )
            self.assertEqual(set(receipt["entries"]), required)

            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": records[:1]}, root / "stage-incomplete",
                    required_closure=required,
                )

            gamma = _make_archive(root, "gamma", "c" * 40, {"c.txt": b"c"})
            extra = _record("sendspin-gamma", gamma, commit="c" * 40)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": records + [extra]}, root / "stage-divergent",
                    required_closure=required,
                )

    def test_stage_rejects_incomplete_production_closure_from_real_inventory(self):
        inventory = module.load(INVENTORY)
        drifted = json.loads(json.dumps(inventory))
        drifted["inputs"] = [r for r in drifted["inputs"] if r["name"] != "sendspin-sdk"]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    drifted, Path(tmp) / "stage",
                    required_closure=module.SENDSPIN_REQUIRED_SOURCES,
                )

    def test_stage_is_idempotent_when_destination_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _make_archive(root, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            record = _record("sendspin-sdk", archive, commit="a" * 40)
            output = root / "stage"
            first = module.stage_sendspin({"inputs": [record]}, output)
            second = module.stage_sendspin({"inputs": [record]}, output)
            self.assertEqual(
                first["entries"]["sendspin-sdk"]["tree_sha256"],
                second["entries"]["sendspin-sdk"]["tree_sha256"],
            )

    def test_stage_uses_verified_local_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            archive = _make_archive(cache, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            record = _record(
                "sendspin-sdk", archive, commit="a" * 40,
                url="https://invalid.invalid/never-fetched.tar.gz",
            )
            output = root / "stage"
            with mock.patch.object(module, "download_to", side_effect=AssertionError("network used")):
                receipt = module.stage_sendspin(
                    {"inputs": [record]}, output, archive_dir=cache
                )
            self.assertTrue(receipt["entries"]["sendspin-sdk"]["archive_verified"])

    def test_stage_missing_cached_archive_fails_without_network(self):
        # An explicitly selected archive pool is exclusive: when the pinned
        # archive is absent from it the focused stage must fail closed with the
        # named cache-miss error and never reach the network.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commit = "a" * 40
            record = {
                "name": "sendspin-sdk",
                "url": "https://example.invalid/sendspin-cpp.git",
                "sha256": "",
                "kind": "source-git",
                "license": "Apache-2.0",
                "redistribution": "source-git-pinned",
                "commit": commit,
                "archive_url": (
                    "https://example.invalid/sendspin-cpp/archive/" + commit + ".tar.gz"),
                "archive_sha256": "b" * 64,
            }
            cache = root / "empty-cache"
            cache.mkdir()
            calls = []

            def sentinel(*args, **kwargs):
                calls.append(args)
                raise AssertionError("network download attempted")

            with mock.patch.object(module, "download_to", side_effect=sentinel):
                with self.assertRaisesRegex(
                    FileNotFoundError, "no cached archive embeds commit"
                ):
                    module.stage_sendspin(
                        {"inputs": [record]}, root / "stage", archive_dir=cache)
            self.assertEqual(calls, [])
            self.assertFalse((root / "stage" / "sendspin-sdk").exists())

    def test_focused_sendspin_stage_ignores_unrelated_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sendspin = _make_archive(root, "spec", "a" * 40, {"spec.md": b"spec"})
            unrelated = _make_archive(root, "piper", "b" * 40, {"model.onnx": b"model"})
            inventory = {"inputs": [
                _record("sendspin-protocol-spec", sendspin, commit="a" * 40),
                {
                    "name": "tts-northern-upstream", "url": unrelated.resolve().as_uri(),
                    "sha256": _sha256(unrelated), "kind": "archive", "license": "MIT",
                    "redistribution": "cleared",
                },
            ]}
            output = root / "stage"
            receipt = module.stage_sendspin(inventory, output)
            self.assertEqual(set(receipt["entries"]), {"sendspin-protocol-spec"})
            self.assertFalse(any(p.name.startswith("piper") for p in output.rglob("*")))
            self.assertFalse(any(p.name.endswith(".onnx") for p in output.rglob("*")))

    def test_download_to_falls_back_to_wget_when_curl_is_absent(self):
        calls = {}

        def fake_run(command, check, timeout):
            calls["command"] = command
            calls["timeout"] = timeout
            Path(command[command.index("--output-document") + 1]).write_bytes(b"payload")

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "archive.tar.gz"
            with mock.patch.object(
                module.shutil, "which",
                side_effect=lambda name: None if name == "curl" else ("/usr/bin/wget" if name == "wget" else None),
            ), mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                module.download_to("https://example.invalid/archive.tar.gz", destination)
            self.assertEqual(calls["command"][0], "/usr/bin/wget")
            self.assertIn("--tries=5", calls["command"])
            self.assertTrue(calls["timeout"] > 0)
            self.assertEqual(destination.read_bytes(), b"payload")

    def test_cli_feature_stage_ignores_unrelated_blocked_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            archive = _make_archive(cache, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            commit = "a" * 40
            # The focused CLI stage must materialize the complete production
            # closure; build every required source from the same cached archive.
            records = []
            for name in sorted(module.SENDSPIN_REQUIRED_SOURCES):
                record = _record(name, archive, commit=commit)
                record["archive_url"] = (
                    f"https://github.com/Sendspin/{name}/archive/{commit}.tar.gz"
                )
                records.append(record)
            inventory = {"schema": module.SCHEMA, "inputs": [*records, {
                "name": "arm32-musl-toolchain", "url": "", "sha256": "",
                "commit": "", "kind": "runtime-import-contract", "license": "",
                "redistribution": "blocked-private",
            }]}
            inventory_path = root / "inputs.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            output = root / "stage"
            exit_code = module.main([
                str(inventory_path), "--feature", "sendspin",
                "--output", str(output), "--archive-dir", str(cache),
            ])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output / "sendspin-sdk" / "include" / "sdk.h").is_file())
            for name in module.SENDSPIN_REQUIRED_SOURCES:
                with self.subTest(source=name):
                    self.assertTrue((output / name).is_dir())

    def test_cli_feature_stage_fails_closed_on_incomplete_production_closure(self):
        # A sendspin-only subset must not print a vacuous PASS.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            archive = _make_archive(cache, "sdk", "a" * 40, {"include/sdk.h": b"real bytes"})
            record = _record("sendspin-sdk", archive, commit="a" * 40)
            record["archive_url"] = (
                "https://github.com/Sendspin/sendspin-cpp/archive/" + "a" * 40 + ".tar.gz"
            )
            inventory_path = root / "inputs.json"
            inventory_path.write_text(
                json.dumps({"schema": module.SCHEMA, "inputs": [record]}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                module.main([
                    str(inventory_path), "--feature", "sendspin",
                    "--output", str(root / "stage"), "--archive-dir", str(cache),
                ])


# --- Cross-contract: Platform SOURCE.lock -----------------------------------


def _platform_lock_path() -> Path | None:
    candidates = []
    explicit = os.environ.get("LIBREECHO_PLATFORM_SRC")
    if explicit:
        candidates.append(Path(explicit) / "tools/mt8163-arm32/sendspin/SOURCE.lock")
    candidates.append(ROOT.parent / "platform/tools/mt8163-arm32/sendspin/SOURCE.lock")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _lock_from_inventory(inventory: dict) -> dict:
    records = {item["name"]: item for item in inventory["inputs"]}

    def entry(name: str) -> dict:
        record = records[name]
        lock_entry = {
            "repository": record["url"],
            "commit": record["commit"],
            "archive_url": record["archive_url"],
            "archive_sha256": record["archive_sha256"],
            "license": record["license"],
        }
        if record.get("submodules"):
            lock_entry["submodules"] = [{
                "path": sub["path"],
                "repository": sub["url"],
                "commit": sub["commit"],
                "archive_url": sub["archive_url"],
                "archive_sha256": sub["archive_sha256"],
            } for sub in record["submodules"]]
        return lock_entry

    lock = {
        "target": "arm-linux-gnueabihf-glibc-dynamic",
        "identity": {role: entry(name) for role, name in module.SENDSPIN_LOCK_ROLES.items()},
        "dependencies": [
            {"name": lock_name, **entry(name)}
            for lock_name, name in module.SENDSPIN_LOCK_DEPENDENCIES.items()
        ],
    }
    if inventory.get("patch_inventory") is not None:
        lock["patch_inventory"] = json.loads(json.dumps(inventory["patch_inventory"]))
    return lock


class SendspinLockContractTests(unittest.TestCase):
    def test_lock_only_drift_is_rejected(self):
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        module.reconcile_source_lock(inventory, lock)  # baseline agrees
        lock["identity"]["sdk"]["archive_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(inventory, lock)

    def test_inventory_only_drift_is_rejected(self):
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        drifted = json.loads(json.dumps(inventory))
        for record in drifted["inputs"]:
            if record["name"] == "sendspin-sdk":
                record["commit"] = "9" * 40
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(drifted, lock)

    def test_platform_source_lock_matches_product_inventory(self):
        inventory = module.load(INVENTORY)
        lock_path = _platform_lock_path()
        if lock_path is None:
            if os.environ.get("SENDSPIN_CROSS_CONTRACT") in ("1", "required"):
                self.fail("focused cross-contract mode requires LIBREECHO_PLATFORM_SRC or a sibling platform/")
            self.skipTest("Platform SOURCE.lock not discovered (set LIBREECHO_PLATFORM_SRC)")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        module.reconcile_source_lock(inventory, lock)
        # The target must describe the dynamic glibc companion, not a static link.
        self.assertEqual(lock["target"], "arm-linux-gnueabihf-glibc-dynamic")
        self.assertNotIn("static", lock["target"])
        # The enforced runtime closure is the fixture-verified one, not a
        # placeholder: the C++ runtime is bound statically, so the dynamic
        # closure is exactly the reviewed glibc and loader.
        runtime = lock["runtime_requirements"]
        self.assertEqual(runtime["interpreter"], "/lib/ld-linux-armhf.so.3")
        self.assertEqual(set(runtime["needed"]),
                         {"libm.so.6", "libc.so.6", "ld-linux-armhf.so.3"})
        self.assertNotIn("libstdc++.so.6", runtime["needed"])
        self.assertNotIn("libgcc_s.so.1", runtime["needed"])
        self.assertIn("static", runtime["verified_closure"]["cpp_runtime"].lower())
        # The build must enforce the locked closure, not merely document it.
        self.assertIn("elf-closure", runtime["enforced_by"])
        # The future daemon closure is explicitly unknown (no version-validated
        # claim); compile-input provenance is explicitly not pinned.
        self.assertIn("unknown", runtime["future_daemon"]["status"].lower())
        self.assertFalse(runtime["compile_sysroot"]["pinned"])
        # The patch inventory is a closed, explicitly mirrored contract: the lock,
        # the Product inventory mirror and the architecture document declare the
        # same one patch (file, digest, target, pristine anchor, order), owned by
        # the Platform. A pristine/empty inventory or a lock-only patch must not
        # pass this test.
        lock_pi = lock["patch_inventory"]
        self.assertEqual(lock_pi["schema"], module.PATCH_SCHEMA)
        applied = lock_pi["applied"]
        self.assertEqual(
            [(e["file"], e["sha256"], e["target"], e["pristine_archive_sha256"]) for e in applied],
            [
                (SDK_PATCH_FILE, SDK_PATCH_SHA256, "identity::sdk", SDK_SHA256),
                (SDK_PATCH_FILE_2, SDK_PATCH_SHA256_2, "identity::sdk", SDK_SHA256),
            ],
        )
        # The Product mirror carries the same declaration AND the ownership.
        mirror = inventory["patch_inventory"]
        self.assertEqual(mirror["owner"], "LibreEcho-Platform")
        self.assertEqual(
            module._patch_applied_tuples(mirror), module._patch_applied_tuples(lock_pi))
        # The architecture document pins every declared digest, anchor and owner.
        doc = ARCH_DOC.read_text(encoding="utf-8")
        for entry in applied:
            self.assertIn(entry["sha256"], doc)
            self.assertIn(entry["pristine_archive_sha256"], doc)
        self.assertIn("LibreEcho-Platform", doc)
        # ... and the canonical mode-aware staged-SDK tree digest (not a
        # umask-dependent tar-CLI value).
        self.assertIn(CANONICAL_SDK_PATCHED_TREE_SHA256, doc)
        serialized = json.dumps(lock).lower()
        self.assertNotIn("patent-encumbered", serialized)
        opus = next(e for e in lock["excluded_dependencies"] if e["name"] == "micro-opus")
        self.assertIn("by policy", opus["reason"])

    # --- The patch transform is part of the same cross-repo contract ----------

    def test_reconcile_rejects_lock_patch_digest_drift(self):
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        module.reconcile_source_lock(inventory, lock)  # baseline agrees
        lock["patch_inventory"]["applied"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(inventory, lock)

    def test_reconcile_rejects_lock_only_patch(self):
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        lock.pop("patch_inventory")
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(inventory, lock)

    def test_reconcile_rejects_mirror_only_patch(self):
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        drifted = json.loads(json.dumps(inventory))
        extra = json.loads(json.dumps(lock["patch_inventory"]["applied"][0]))
        extra["file"] = "0002-extra.patch"
        extra["sha256"] = "1" * 64
        drifted["patch_inventory"]["applied"].append(extra)
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(drifted, lock)

    def test_reconcile_rejects_patch_order_drift(self):
        # Order is part of the contract: the same patches in a different order
        # are a different transform.
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        drifted = json.loads(json.dumps(inventory))
        entry = json.loads(json.dumps(lock["patch_inventory"]["applied"][0]))
        entry["file"] = "0000-first.patch"
        entry["sha256"] = "2" * 64
        drifted["patch_inventory"]["applied"] = [entry, *drifted["patch_inventory"]["applied"]]
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(drifted, lock)

    def test_reconcile_requires_platform_patch_ownership(self):
        inventory = module.load(INVENTORY)
        lock = _lock_from_inventory(inventory)
        drifted = json.loads(json.dumps(inventory))
        drifted["patch_inventory"]["owner"] = "LibreEcho-UI"
        with self.assertRaises(ValueError):
            module.reconcile_source_lock(drifted, lock)

    def test_loader_rejects_malformed_patch_mirror(self):
        cases = (
            # digest is not 64-hex
            {"schema": module.PATCH_SCHEMA,
             "applied": [{"file": "a.patch", "sha256": "nothex", "target": "identity::sdk"}]},
            # path component in the file name
            {"schema": module.PATCH_SCHEMA,
             "applied": [{"file": "sub/a.patch", "sha256": "0" * 64, "target": "identity::sdk"}]},
            # applied without the required schema
            {"applied": [{"file": "a.patch", "sha256": "0" * 64, "target": "identity::sdk"}]},
            # empty target
            {"schema": module.PATCH_SCHEMA,
             "applied": [{"file": "a.patch", "sha256": "0" * 64, "target": ""}]},
        )
        for bad in cases:
            with self.subTest(mirror=bad), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "inputs.json"
                path.write_text(json.dumps(
                    {"schema": module.SCHEMA, "inputs": [], "patch_inventory": bad}))
                with self.assertRaises(ValueError):
                    module.load(path)


# --- Patch materialization: the Product stage applies the declared patch -----


class SendspinPatchStagingTests(unittest.TestCase):
    # A minimal single-apply diff against include/sdk.h (content b"original\n").
    PATCH_BODY = (
        b"--- a/include/sdk.h\n+++ b/include/sdk.h\n@@ -1 +1 @@\n-original\n+patched\n"
    )

    @staticmethod
    def _lock_for(mirror: dict) -> dict:
        """A Platform SOURCE.lock whose patch_inventory mirrors ``mirror``."""
        return {"patch_inventory": {
            "schema": module.PATCH_SCHEMA,
            "applied": [dict(entry) for entry in mirror["applied"]],
        }}

    def _fixture(self, root: Path):
        archive = _make_archive(root, "sendspin-cpp", "a" * 40, {"include/sdk.h": b"original\n"})
        record = _record("sendspin-sdk", archive, commit="a" * 40)
        patch_dir = root / "patches"
        patch_dir.mkdir()
        patch_file = patch_dir / SDK_PATCH_FILE
        patch_file.write_bytes(self.PATCH_BODY)
        mirror = {
            "schema": module.PATCH_SCHEMA,
            "owner": "LibreEcho-Platform",
            "patch_dir": "tools/mt8163-arm32/sendspin/patches",
            "applied": [{
                "file": SDK_PATCH_FILE,
                "sha256": _sha256(patch_file),
                "target": "identity::sdk",
                "pristine_archive_sha256": record["archive_sha256"],
            }],
        }
        return record, patch_dir, mirror, self._lock_for(mirror)

    def test_stage_applies_declared_patch_and_receipt_matches_final_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            inventory = {"inputs": [record], "patch_inventory": mirror}
            output = root / "stage"
            receipt = module.stage_sendspin(inventory, output, patch_dir=patch_dir, lock=lock)
            # The patch bytes landed (fresh archive + exactly this patch).
            self.assertEqual(
                (output / "sendspin-sdk/include/sdk.h").read_bytes(), b"patched\n")
            entry = receipt["entries"]["sendspin-sdk"]
            self.assertTrue(entry["archive_verified"])
            self.assertEqual(entry["archive_sha256_declared"], record["archive_sha256"])
            self.assertEqual(entry["patches"], [{"file": SDK_PATCH_FILE, "sha256": _sha256(patch_dir / SDK_PATCH_FILE)}])
            # Receipt digests describe the *final* patched tree.
            digest, count = module.tree_digest(output / "sendspin-sdk")
            self.assertEqual(entry["tree_sha256"], digest)
            self.assertEqual(entry["file_count"], count)
            self.assertEqual(receipt["patches_applied"][0]["file"], SDK_PATCH_FILE)
            self.assertEqual(receipt["patch_inventory_owner"], "LibreEcho-Platform")
            on_disk = json.loads((output / "sendspin-stage-receipt.json").read_text())
            self.assertEqual(on_disk["entries"]["sendspin-sdk"]["tree_sha256"], digest)

    def test_stage_requires_explicit_patch_dir_when_mirror_declares_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, _patch_dir, mirror, lock = self._fixture(root)
            output = root / "stage"
            with self.assertRaises(ValueError):
                # No patch_dir, but an authenticated lock: this exercises the
                # patch_dir gate specifically.
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, output, lock=lock)
            self.assertFalse(output.exists(), "no output before the patch dir is validated")

    def test_stage_rejects_corrupt_patch_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            mirror["applied"][0]["sha256"] = "0" * 64  # drifted digest
            lock = self._lock_for(mirror)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_missing_declared_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            mirror["applied"][0]["file"] = "0009-absent.patch"
            lock = self._lock_for(mirror)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_extra_undeclared_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            (patch_dir / "0002-extra.patch").write_bytes(self.PATCH_BODY)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_pristine_anchor_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            mirror["applied"][0]["pristine_archive_sha256"] = "0" * 64
            lock = self._lock_for(mirror)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_unsafe_patch_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            (patch_dir / SDK_PATCH_FILE).write_bytes(
                b"--- a/../escape.txt\n+++ b/../escape.txt\n@@ -1 +1 @@\n-original\n+patched\n")
            mirror["applied"][0]["sha256"] = _sha256(patch_dir / SDK_PATCH_FILE)
            lock = self._lock_for(mirror)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_patch_target_outside_the_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            mirror["applied"][0]["target"] = "noise-c"  # -> sendspin-noise-c, not staged
            lock = self._lock_for(mirror)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_lock_mirror_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            lock = {"patch_inventory": {
                "schema": module.PATCH_SCHEMA,
                "applied": [{
                    "file": SDK_PATCH_FILE, "sha256": "0" * 64,
                    "target": "identity::sdk",
                    "pristine_archive_sha256": record["archive_sha256"],
                }],
            }}
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_patched_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            inventory = {"inputs": [record], "patch_inventory": mirror}
            output = root / "stage"
            first = module.stage_sendspin(inventory, output, patch_dir=patch_dir, lock=lock)
            second = module.stage_sendspin(inventory, output, patch_dir=patch_dir, lock=lock)
            self.assertEqual(
                first["entries"]["sendspin-sdk"]["tree_sha256"],
                second["entries"]["sendspin-sdk"]["tree_sha256"],
            )
            self.assertEqual(
                (output / "sendspin-sdk/include/sdk.h").read_bytes(), b"patched\n")

    def test_stage_forged_receipt_is_overwritten_by_the_recomputed_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            output = root / "stage"
            output.mkdir(parents=True)
            forged = output / "sendspin-stage-receipt.json"
            forged.write_text(json.dumps({
                "schema": module.RECEIPT_SCHEMA, "feature": "sendspin",
                "complete": True, "entries": {"sendspin-sdk": {"tree_sha256": "f" * 64}},
            }))
            receipt = module.stage_sendspin(
                {"inputs": [record], "patch_inventory": mirror}, output, patch_dir=patch_dir, lock=lock)
            written = json.loads(forged.read_text())
            digest, _ = module.tree_digest(output / "sendspin-sdk")
            self.assertEqual(written["entries"]["sendspin-sdk"]["tree_sha256"], digest)
            self.assertNotEqual(written["entries"]["sendspin-sdk"]["tree_sha256"], "f" * 64)
            self.assertEqual(receipt["entries"]["sendspin-sdk"]["tree_sha256"], digest)


    # --- Hardening: required authentication, closed enumeration, frozen bytes ---

    def test_stage_requires_source_lock_when_mirror_declares_patches(self):
        # Without a Platform SOURCE.lock the mirror cannot be authenticated, so the
        # focused stage refuses before creating any output.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, _lock = self._fixture(root)
            output = root / "stage"
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, output,
                    patch_dir=patch_dir)
            self.assertFalse(output.exists(), "no output without an authenticated lock")

    def test_stage_requires_exact_platform_owner_even_with_lock(self):
        # Ownership is a hard gate independent of the lock: a missing/None owner
        # (the old fail-open branch) or a foreign owner must be refused.
        for bad_owner in (None, "LibreEcho-UI"):
            with self.subTest(owner=bad_owner), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record, patch_dir, mirror, lock = self._fixture(root)
                if bad_owner is None:
                    mirror.pop("owner")
                else:
                    mirror["owner"] = bad_owner
                output = root / "stage"
                with self.assertRaises(ValueError):
                    module.stage_sendspin(
                        {"inputs": [record], "patch_inventory": mirror}, output,
                        patch_dir=patch_dir, lock=lock)
                self.assertFalse(output.exists(), "no output without the Platform owner")

    def test_stage_rejects_dotfile_in_patch_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            (patch_dir / ".hidden.patch").write_bytes(self.PATCH_BODY)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_subdirectory_entry_in_patch_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            (patch_dir / "sub").mkdir()
            (patch_dir / "sub" / "nested.patch").write_bytes(self.PATCH_BODY)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_symlink_in_patch_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            elsewhere = root / "elsewhere.patch"
            elsewhere.write_bytes(self.PATCH_BODY)
            (patch_dir / SDK_PATCH_FILE).unlink()
            (patch_dir / SDK_PATCH_FILE).symlink_to(elsewhere)
            with self.assertRaises(ValueError):
                module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, root / "stage",
                    patch_dir=patch_dir, lock=lock)

    def test_stage_rejects_malicious_symlink_patch_body(self):
        # A digest-pinned patch whose *body* creates a symlink out of the tree (a
        # git `new file mode 120000` diff) must be refused before publication: the
        # header path (b/link) is safe, so only a whole-tree confinement check
        # catches the escaping symlink body. Each body line carries the `+`
        # prefix -- without it GNU patch refuses the diff as malformed, which
        # would raise at the *apply* gate and pass this test without ever
        # reaching the confinement gate it names.
        cases = (
            (b"+/etc/passwd\n\\ No newline at end of file\n",
             "symlink to an absolute path"),
            (b"+../../../../etc/passwd\n\\ No newline at end of file\n",
             "escaping symlink"),
        )
        for body, expected in cases:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record, patch_dir, mirror, lock = self._fixture(root)
                symlink_diff = (
                    b"diff --git a/link b/link\nnew file mode 120000\n"
                    b"index 0000000..1111111\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n"
                    + body
                )
                (patch_dir / SDK_PATCH_FILE).write_bytes(symlink_diff)
                mirror["applied"][0]["sha256"] = _sha256(patch_dir / SDK_PATCH_FILE)
                lock = self._lock_for(mirror)
                output = root / "stage"
                with self.assertRaisesRegex(ValueError, expected):
                    module.stage_sendspin(
                        {"inputs": [record], "patch_inventory": mirror}, output,
                        patch_dir=patch_dir, lock=lock)
                self.assertFalse((output / "sendspin-sdk").exists())
                self.assertFalse((output / "sendspin-stage-receipt.json").exists())

    def test_stage_uses_frozen_patch_bytes_across_a_concurrent_swap(self):
        # The patch directory is mutated *after* authentication but before the
        # transform runs: the applied bytes must be the frozen verified buffer, not
        # the swapped file, so the result is deterministic and no unexpected
        # content can land.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            declared = mirror["applied"][0]["sha256"]
            output = root / "stage"
            original = module.resolve_patch_map

            def swap_after_authentication(*args, **kwargs):
                result = original(*args, **kwargs)
                (patch_dir / SDK_PATCH_FILE).write_bytes(
                    b"--- a/include/sdk.h\n+++ b/include/sdk.h\n@@ -1 +1 @@\n"
                    b"-original\n+SWAPPED\n")
                return result

            with mock.patch.object(
                module, "resolve_patch_map", side_effect=swap_after_authentication
            ):
                receipt = module.stage_sendspin(
                    {"inputs": [record], "patch_inventory": mirror}, output,
                    patch_dir=patch_dir, lock=lock)
            self.assertEqual(
                (output / "sendspin-sdk/include/sdk.h").read_bytes(), b"patched\n")
            self.assertEqual(
                receipt["entries"]["sendspin-sdk"]["patches"],
                [{"file": SDK_PATCH_FILE, "sha256": declared}])
            self.assertEqual(receipt["patches_applied"][0]["sha256"], declared)


class SendspinWholeInventoryRouteTests(unittest.TestCase):
    """The whole-inventory ``stage()`` route must never stage a pristine SDK.

    ``main()`` with ``--output`` but no ``--feature`` is the release-workflow
    route; it previously ignored the declared ``patch_inventory`` and materialized
    a pristine tree under an inventory that declared the patch applied.
    """

    PATCH_BODY = SendspinPatchStagingTests.PATCH_BODY

    def _fixture(self, root: Path):
        archive = _make_archive(root, "sendspin-cpp", "a" * 40, {"include/sdk.h": b"original\n"})
        record = _record("sendspin-sdk", archive, commit="a" * 40)
        patch_dir = root / "patches"
        patch_dir.mkdir()
        patch_file = patch_dir / SDK_PATCH_FILE
        patch_file.write_bytes(self.PATCH_BODY)
        mirror = {
            "schema": module.PATCH_SCHEMA,
            "owner": "LibreEcho-Platform",
            "patch_dir": "tools/mt8163-arm32/sendspin/patches",
            "applied": [{
                "file": SDK_PATCH_FILE,
                "sha256": _sha256(patch_file),
                "target": "identity::sdk",
                "pristine_archive_sha256": record["archive_sha256"],
            }],
        }
        lock = {"patch_inventory": {
            "schema": module.PATCH_SCHEMA,
            "applied": [dict(entry) for entry in mirror["applied"]],
        }}
        return record, patch_dir, mirror, lock

    def test_output_route_refuses_declared_patches_without_lock_and_patch_dir(self):
        # The release workflow's exact call shape (no --feature, no --patch-dir,
        # no --source-lock) must fail closed, not produce a pristine SDK.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, _patch_dir, mirror, _lock = self._fixture(root)
            record["archive_url"] = (
                "https://github.com/Sendspin/sendspin-cpp/archive/" + record["commit"] + ".tar.gz")
            inventory_path = root / "inputs.json"
            inventory_path.write_text(json.dumps({
                "schema": module.SCHEMA, "inputs": [record], "patch_inventory": mirror,
            }), encoding="utf-8")
            output = root / "public-deps"
            with self.assertRaises(ValueError):
                module.main([str(inventory_path), "--output", str(output)])
            self.assertFalse(output.exists(), "the whole-inventory route staged pristine output")

    def test_output_route_missing_cached_archive_fails_without_network(self):
        # The whole-inventory route must thread an explicitly selected archive
        # pool into every commit-pinned archive fetch: with an empty pool the
        # route fails closed with the named cache-miss error and never reaches
        # the network.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = []
            for name in sorted(module.SENDSPIN_REQUIRED_SOURCES):
                commit = hashlib.sha256(name.encode("utf-8")).hexdigest()[:40]
                records.append({
                    "name": name,
                    "url": "https://example.invalid/" + name + ".git",
                    "sha256": "",
                    "kind": "source-git",
                    "license": "Apache-2.0",
                    "redistribution": "source-git-pinned",
                    "commit": commit,
                    "archive_url": (
                        "https://example.invalid/" + name + "/archive/" + commit + ".tar.gz"),
                    "archive_sha256": "c" * 64,
                })
            patch_dir = root / "patches"
            patch_dir.mkdir()
            (patch_dir / SDK_PATCH_FILE).write_bytes(self.PATCH_BODY)
            mirror = {
                "schema": module.PATCH_SCHEMA,
                "owner": "LibreEcho-Platform",
                "patch_dir": "tools/mt8163-arm32/sendspin/patches",
                "applied": [{
                    "file": SDK_PATCH_FILE,
                    "sha256": _sha256(patch_dir / SDK_PATCH_FILE),
                    "target": "identity::sdk",
                    "pristine_archive_sha256": "c" * 64,
                }],
            }
            inventory = {
                "schema": module.SCHEMA, "inputs": records, "patch_inventory": mirror,
            }
            lock = _lock_from_inventory(inventory)
            inventory_path = root / "inputs.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            lock_path = root / "SOURCE.lock"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            cache = root / "empty-cache"
            cache.mkdir()
            calls = []

            def sentinel(*args, **kwargs):
                calls.append(args)
                raise AssertionError("network download attempted")

            output = root / "public-deps"
            with mock.patch.object(module, "download_to", side_effect=sentinel):
                with self.assertRaisesRegex(
                    FileNotFoundError, "no cached archive embeds commit"
                ):
                    module.main([
                        str(inventory_path), "--output", str(output),
                        "--archive-dir", str(cache),
                        "--source-lock", str(lock_path), "--patch-dir", str(patch_dir),
                    ])
            self.assertEqual(calls, [])
            leftovers = sorted(p.name for p in output.iterdir()) if output.exists() else []
            self.assertEqual(leftovers, [], "a cache miss must not publish a staged tree")

    def test_output_route_requires_both_lock_and_patch_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            inventory = {"inputs": [record], "patch_inventory": mirror}
            with self.assertRaises(ValueError):
                module.stage(inventory, root / "a", root, patch_dir=patch_dir)
            self.assertFalse((root / "a").exists(), "no output without a lock")
            with self.assertRaises(ValueError):
                module.stage(inventory, root / "b", root, lock=lock)
            self.assertFalse((root / "b").exists(), "no output without a patch dir")

    def test_output_route_applies_patches_when_authenticated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            inventory = {"inputs": [record], "patch_inventory": mirror}
            output = root / "public-deps"
            module.stage(inventory, output, root, patch_dir=patch_dir, lock=lock)
            self.assertEqual(
                (output / "sendspin-sdk/include/sdk.h").read_bytes(), b"patched\n")

    def test_output_route_fails_closed_on_an_unapplied_declared_patch(self):
        # If the authenticated transform is not actually applied to a staged tree,
        # the route must refuse rather than silently ignoring the declared patch.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, patch_dir, mirror, lock = self._fixture(root)
            inventory = {"inputs": [record], "patch_inventory": mirror}
            with mock.patch.object(
                module, "resolve_patch_map", return_value={"sendspin-absent": []}
            ):
                with self.assertRaises(ValueError):
                    module.stage(inventory, root / "public-deps", root,
                                 patch_dir=patch_dir, lock=lock)


class SendspinOfflineArchiveRouteTests(unittest.TestCase):
    """Both CLI stage routes consume the real pinned archive pool offline.

    The whole-inventory route and the focused ``--feature sendspin`` route must
    thread an explicitly selected ``--archive-dir`` into every commit-pinned
    archive fetch: all eight real archives (the seven sources plus the declared
    micro-ogg-demuxer submodule child) come from the local pool and no downloader
    is ever invoked. Gated on the real Platform lock and archive pool; when
    ``SENDSPIN_CROSS_CONTRACT`` is forced and either is absent the test fails
    closed rather than skipping to a false pass.
    """

    def test_both_cli_routes_stage_every_cached_archive_without_network(self):
        lock_path = _platform_lock_path()
        archive_dir = os.environ.get("LIBREECHO_SENDSPIN_ARCHIVE_DIR")
        if lock_path is None or not archive_dir:
            if os.environ.get("SENDSPIN_CROSS_CONTRACT") in ("1", "required"):
                self.fail(
                    "offline archive routes require LIBREECHO_PLATFORM_SRC (or sibling "
                    "platform/) and LIBREECHO_SENDSPIN_ARCHIVE_DIR")
            self.skipTest("real Platform lock/archives not available")
        archive_dir = Path(archive_dir)
        if not archive_dir.is_dir():
            self.skipTest(f"archive pool missing: {archive_dir}")

        inventory = module.load(INVENTORY)
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        module.reconcile_source_lock(inventory, lock)
        patch_dir = lock_path.parent / "patches"
        sendspin = [r for r in inventory["inputs"] if r["name"].startswith("sendspin-")]
        self.assertEqual(len(sendspin), len(module.SENDSPIN_REQUIRED_SOURCES))

        calls = []

        def sentinel(*args, **kwargs):
            calls.append(args)
            raise AssertionError("network download attempted")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory_path = root / "inputs.json"
            inventory_path.write_text(json.dumps({
                "schema": module.SCHEMA,
                "inputs": sendspin,
                "patch_inventory": inventory["patch_inventory"],
            }), encoding="utf-8")
            common = [
                "--archive-dir", str(archive_dir),
                "--source-lock", str(lock_path),
                "--patch-dir", str(patch_dir),
            ]
            with mock.patch.object(module, "download_to", side_effect=sentinel):
                whole = module.main([
                    str(inventory_path), "--output", str(root / "whole"), *common])
                focused = module.main([
                    str(inventory_path), "--feature", "sendspin",
                    "--output", str(root / "focused"), *common])
            self.assertEqual(whole, 0)
            self.assertEqual(focused, 0)
            self.assertEqual(calls, [], "an offline route attempted a network download")
            for route in ("whole", "focused"):
                with self.subTest(route=route):
                    digest, count = module.tree_digest(root / route / "sendspin-sdk")
                    self.assertEqual(digest, CANONICAL_SDK_PATCHED_TREE_SHA256)
                    self.assertEqual(count, 184)


class SendspinRealPatchClosureTests(unittest.TestCase):
    """Real Platform lock + real archives: the Product stage must equal archive+patch.

    Gated on the Platform worktree and a real archive pool; when
    ``SENDSPIN_CROSS_CONTRACT`` is forced and either is absent the test fails
    closed rather than skipping to a false pass.
    """

    def test_real_stage_equals_platform_archive_plus_patch(self):
        lock_path = _platform_lock_path()
        archive_dir = os.environ.get("LIBREECHO_SENDSPIN_ARCHIVE_DIR")
        if lock_path is None or not archive_dir:
            if os.environ.get("SENDSPIN_CROSS_CONTRACT") in ("1", "required"):
                self.fail(
                    "real patch closure requires LIBREECHO_PLATFORM_SRC (or sibling platform/) "
                    "and LIBREECHO_SENDSPIN_ARCHIVE_DIR")
            self.skipTest("real Platform lock/archives not available")
        archive_dir = Path(archive_dir)
        if not archive_dir.is_dir():
            self.skipTest(f"archive pool missing: {archive_dir}")

        inventory = module.load(INVENTORY)
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        module.reconcile_source_lock(inventory, lock)
        patch_dir = lock_path.parent / "patches"
        verifier = lock_path.parent / "verify_sendspin_sources.py"

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage"
            receipt = module.stage_sendspin(
                inventory, output, archive_dir=archive_dir, patch_dir=patch_dir, lock=lock,
                required_closure=module.SENDSPIN_REQUIRED_SOURCES,
            )
            entry = receipt["entries"]["sendspin-sdk"]
            self.assertTrue(entry["archive_verified"])
            self.assertEqual(
                entry["tree_sha256"], CANONICAL_SDK_PATCHED_TREE_SHA256,
                "the staged SDK tree is not the canonical archive-plus-patch tree",
            )

            # GREEN: the Platform verifier accepts the Product stage.
            green = subprocess.run([
                sys.executable, str(verifier), "--lock", str(lock_path),
                "--archive-dir", str(archive_dir), "--compare-tree", str(output),
                "--compare-work", str(Path(tmp) / "cmp"),
                "--patch-dir", str(patch_dir),
            ], capture_output=True, text=True)
            self.assertEqual(green.returncode, 0, green.stdout + green.stderr)

            # RED: reverting the applied patch set (in reverse declared order) in
            # a copy is refused for identity::sdk.
            import shutil
            pristine = Path(tmp) / "pristine"
            shutil.copytree(output, pristine)
            for patch_name, _digest in reversed(SDK_PATCHES):
                reverted = subprocess.run([
                    "patch", "-R", "-p1", "--no-backup-if-mismatch",
                    "-i", str(patch_dir / patch_name),
                    "-d", str(pristine / "sendspin-sdk"),
                ], capture_output=True, text=True)
                self.assertEqual(reverted.returncode, 0, reverted.stdout + reverted.stderr)
            red = subprocess.run([
                sys.executable, str(verifier), "--lock", str(lock_path),
                "--archive-dir", str(archive_dir), "--compare-tree", str(pristine),
                "--compare-work", str(Path(tmp) / "cmp2"),
                "--patch-dir", str(patch_dir),
            ], capture_output=True, text=True)
            self.assertNotEqual(red.returncode, 0, "a pristine SDK tree was accepted")
            self.assertIn("identity::sdk", red.stderr)


if __name__ == "__main__":
    unittest.main()
