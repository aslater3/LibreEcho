#!/usr/bin/env python3
"""Contract checks for the public ARM32 neural dependency boundary."""
from pathlib import Path
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
        self.assertIn('git -C "$ORT_SOURCE" rev-parse HEAD', SCRIPT)
        self.assertIn('git -C "$SHERPA_SOURCE" rev-parse HEAD', SCRIPT)

    def test_script_produces_all_builder_roots(self):
        for root in (
            '"$OUT/onnxruntime-build"',
            '"$OUT/onnxruntime-prefix/lib"',
            '"$OUT/sherpa-onnx-prefix"',
            '"$OUT/flatbuffers-python"',
            '"$OUT/speexdsp-prefix"',
        ):
            self.assertIn(root, SCRIPT)
        self.assertIn('public_neural_dependencies=PASS', SCRIPT)
        self.assertIn('cmake --build "$ORT_BUILD" --target re2', SCRIPT)
        self.assertIn("printf 'ADDLIB %s\\n'", SCRIPT)
        self.assertNotIn('pipeline/out/CURRENT', SCRIPT)


if __name__ == "__main__":
    unittest.main()