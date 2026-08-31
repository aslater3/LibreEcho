#!/usr/bin/env python3
"""Contract checks for the public ARM32 neural dependency boundary."""
from pathlib import Path
import json
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


if __name__ == "__main__":
    unittest.main()