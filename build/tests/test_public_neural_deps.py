#!/usr/bin/env python3
"""Contract checks for the public ARM32 neural dependency boundary."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
SCRIPT = (ROOT / "build/ci/build-public-neural-deps.sh").read_text()


class PublicNeuralDependencyTests(unittest.TestCase):
    def test_script_is_pinned_and_fail_closed(self):
        for pin in (
            "8f0278c77bf44b0cc83c098c6c722b92a36ac4b5",
            "546df6f963ae719dddd8b8d10749e9d9086b0d86",
            "d17ca363654556a4ff1d02cc13d9eb1fc5a8642c90b40bd54ce266c3807b91a7",
        ):
            self.assertIn(pin, SCRIPT)
        self.assertIn('[[ -d "$ORT_SOURCE/.git" ]]', SCRIPT)
        self.assertIn('[[ -d "$SHERPA_SOURCE/.git" ]]', SCRIPT)
        self.assertIn('[[ -d "$ESPEAK_SOURCE/.git" ]]', SCRIPT)
        self.assertIn('git -C "$ORT_SOURCE" rev-parse HEAD', SCRIPT)
        self.assertIn('git -C "$SHERPA_SOURCE" rev-parse HEAD', SCRIPT)
        self.assertIn('cmake --build "$ESPEAK_BUILD" --target data', SCRIPT)

    def test_script_produces_all_builder_roots(self):
        for root in (
            '"$OUT/onnxruntime-build"',
            '"$OUT/onnxruntime-prefix/lib"',
            '"$OUT/sherpa-onnx-prefix"',
            '"$OUT/flatbuffers-python"',
            '"$OUT/speexdsp-prefix"',
            '"$OUT/espeak-ng-data"',
        ):
            self.assertIn(root, SCRIPT)
        self.assertIn('public_neural_dependencies=PASS', SCRIPT)
        self.assertEqual(SCRIPT.count('-DCMAKE_SYSROOT="$ARMHF_ROOT"'), 2)
        # Sherpa consumes a flat install-style ONNX Runtime include dir.
        self.assertIn(
            'SHERPA_ONNXRUNTIME_INCLUDE_DIR="$OUT/onnxruntime-prefix/include"',
            SCRIPT)
        self.assertNotIn('SHERPA_ONNXRUNTIME_INCLUDE_DIR="$ORT_SOURCE/include"', SCRIPT)
        self.assertIn('"$OUT/onnxruntime-prefix/include/onnxruntime_cxx_api.h"', SCRIPT)
        # SpeexDSP autotools gets the staged cross compiler and sysroot
        # explicitly (configure does not inherit CMake's -DCMAKE_SYSROOT).
        self.assertIn('CC="${CROSS}gcc"', SCRIPT)
        self.assertIn('--sysroot=$ARMHF_ROOT', SCRIPT)
        self.assertIn('cmake --build "$ORT_BUILD" --target re2', SCRIPT)
        self.assertIn("printf 'ADDLIB %s\\n'", SCRIPT)
        self.assertNotIn('pipeline/out/CURRENT', SCRIPT)
        self.assertIn('"$ESPEAK_BUILD/espeak-ng-data/phontab"', SCRIPT)
        self.assertIn('"$ESPEAK_BUILD/espeak-ng-data/phonindex"', SCRIPT)
        self.assertIn('"$ESPEAK_DATA/phontab"', SCRIPT)
        self.assertIn('"$ESPEAK_DATA/phonindex"', SCRIPT)

    def test_tts_voice_metadata_is_sherpa_piper_compatible(self):
        metadata = json.loads(
            (ROOT / "build/inputs/tts-voice-metadata.json").read_text()
        )
        expected = {
            "model_type": "vits",
            "comment": "piper",
            "language": "English",
            "voice": "en-gb-x-rp",
            "has_espeak": "1",
            "n_speakers": "1",
            "sample_rate": "22050",
        }
        northern = metadata["voices"]["northern-male"]
        self.assertEqual(dict(northern["metadata_props"]), expected)
        self.assertEqual(
            northern["reviewed_sha256"],
            "d23e7891af7062eb188283dba94866e25ffd5b01a0d9fb9a23c71a39b75b2308",
        )
        for name, voice in metadata["voices"].items():
            with self.subTest(voice=name):
                properties = dict(voice["metadata_props"])
                self.assertIn("sample_rate", properties)
                self.assertIn("n_speakers", properties)
                self.assertNotIn("vits_sample_rate", properties)


class PublicNeuralNsyncStagingTests(unittest.TestCase):
    """Exercise the shipped staging block, not a duplicate list of its copies.

    These native archives model an ORT -> nsync link dependency. They check the
    staging boundary, not target inference or the upstream nsync implementation.
    """

    def setUp(self):
        for tool in ("bash", "g++", "ar"):
            if not shutil.which(tool):
                self.fail(f"required native link-test tool is unavailable: {tool}")
        self.tmp = tempfile.TemporaryDirectory(prefix="le-neural-nsync-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        self.out = self.root / "stage"
        self.build.mkdir()
        # Execute the real function and its real calls, up to the next build
        # phase. New/removed dependency copies therefore affect this test.
        start = SCRIPT.index("\ncopy_named_archive() {")
        end = SCRIPT.index("\n# ORT v1.27", start)
        self.stage_script = (
            'set -euo pipefail\n'
            'fail() { echo "ERROR: $*" >&2; exit 1; }\n'
            + SCRIPT[start:end]
        )
        archives = (
            "libonnxruntime_session.a", "libonnxruntime_optimizer.a",
            "libonnxruntime_providers.a", "libonnxruntime_graph.a",
            "libonnxruntime_framework.a", "libonnxruntime_common.a",
            "libonnxruntime_mlas.a", "libonnxruntime_util.a",
            "libonnxruntime_flatbuffers.a", "libonnxruntime_lora.a",
            "libonnx.a", "libonnx_proto.a", "libprotobuf-lite.a",
            "libflatbuffers.a", "libabsl_fixture.a",
        )
        for name in archives:
            self.run_ok(["ar", "rcs", str(self.build / name)])
        self.compile("session", """
            namespace nsync { int nsync_mu_lock(int); }
            int ort_fixture() { return nsync::nsync_mu_lock(7); }
        """)
        self.run_ok(["ar", "rcs", str(self.build / "libonnxruntime_session.a"),
                     str(self.root / "session.o")])
        self.compile("sync", """
            namespace nsync { int nsync_mu_lock(int value) { return value; } }
        """)
        self.compile("main", """
            int ort_fixture();
            int main() { return ort_fixture() == 7 ? 0 : 1; }
        """)

    def run_ok(self, command):
        return subprocess.run(command, check=True, capture_output=True,
                              text=True, timeout=15)

    def compile(self, name, content):
        source = self.root / f"{name}.cpp"
        source.write_text(content)
        self.run_ok(["g++", "-c", str(source), "-o", str(self.root / f"{name}.o")])

    def stage(self):
        return subprocess.run(
            ["bash", "-c", self.stage_script],
            env={**os.environ, "ORT_BUILD": str(self.build),
                 "OUT": str(self.out), "CROSS": ""},
            text=True, capture_output=True, timeout=15,
        )

    def test_flat_and_nested_nsync_archive_survives_staging_and_links(self):
        for relative in ("libnsync_cpp.a", "_deps/nsync-build/libnsync_cpp.a"):
            with self.subTest(layout=relative):
                for old in self.build.rglob("libnsync_cpp.a"):
                    old.unlink()
                shutil.rmtree(self.out, ignore_errors=True)
                archive = self.build / relative
                archive.parent.mkdir(parents=True, exist_ok=True)
                self.run_ok(["ar", "rcs", str(archive), str(self.root / "sync.o")])
                result = self.stage()
                self.assertEqual(result.returncode, 0, result.stderr)
                installed = self.out / "onnxruntime-build/_deps/nsync-build/libnsync_cpp.a"
                self.assertTrue(installed.is_file(), "nsync was lost from staged ORT")
                self.assertEqual(installed.read_bytes(), archive.read_bytes())
                binary = self.root / "staged-link"
                staged = sorted((self.out / "onnxruntime-build").rglob("*.a"))
                self.run_ok(["g++", str(self.root / "main.o"), "-Wl,--start-group",
                             *map(str, staged), "-Wl,--end-group", "-o", str(binary)])
                self.run_ok([str(binary)])

    def test_missing_nsync_fails_at_staging(self):
        result = self.stage()
        self.assertNotEqual(result.returncode, 0, "missing nsync was accepted")
        self.assertIn("libnsync_cpp.a", result.stderr)

    def test_malformed_nsync_fails_at_staging(self):
        (self.build / "libnsync_cpp.a").write_text("not a static archive\n")
        result = self.stage()
        self.assertNotEqual(result.returncode, 0, "malformed nsync was accepted")
        self.assertIn("nsync", result.stderr)

    def test_empty_nsync_archive_fails_at_staging(self):
        self.run_ok(["ar", "rcs", str(self.build / "libnsync_cpp.a")])
        result = self.stage()
        self.assertNotEqual(result.returncode, 0, "empty nsync was accepted")
        self.assertIn("nsync", result.stderr)


if __name__ == "__main__":
    unittest.main()