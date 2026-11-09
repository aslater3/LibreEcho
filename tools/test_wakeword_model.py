"""Tests for tools/wakeword/wakeword_model.py.

Run from the repository root:
    python -m unittest tools.test_wakeword_model

Contract tests build small synthetic classifiers in a temporary directory.
Stock-model tests run only when WAKEWORD_STOCK_DIR names a folder holding the
three stock models (melspectrogram.onnx, embedding_model.onnx,
alexa_v0.1.onnx). Fixture tests also need WAKEWORD_FIXTURE_DIR, holding the
16 kHz WAV clips alexa-twice.wav and no-wake-word.wav.
"""

import json
import os
import shutil
import tempfile
import unittest

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from tools.wakeword import wakeword_model as wm

STOCK_DIR = os.environ.get("WAKEWORD_STOCK_DIR", "")
FIXTURE_DIR = os.environ.get("WAKEWORD_FIXTURE_DIR", "")
HAVE_STOCK = bool(STOCK_DIR) and all(
    os.path.isfile(os.path.join(STOCK_DIR, f)) for f in wm.MODEL_FILES)


def make_classifier(path, input_name=wm.CLASSIFIER_INPUT, output_name=wm.CLASSIFIER_OUTPUT,
                    extra_nodes=(), sigmoid=True):
    """Write a classifier with the stock I/O shape: Flatten, Gemm, Sigmoid."""
    rng = np.random.RandomState(0)
    weights = numpy_helper.from_array(rng.randn(16 * 96, 1).astype(np.float32), "W")
    bias = numpy_helper.from_array(np.zeros(1, np.float32), "B")
    nodes = [
        helper.make_node("Flatten", [input_name], ["flat"], axis=1),
        helper.make_node("Gemm", ["flat", "W", "B"], ["logit"] if sigmoid else [output_name]),
    ]
    if sigmoid:
        nodes.append(helper.make_node("Sigmoid", ["logit"], [output_name]))
    nodes.extend(extra_nodes)
    graph = helper.make_graph(
        nodes, "classifier",
        [helper.make_tensor_value_info(input_name, TensorProto.FLOAT, [1, 16, 96])],
        [helper.make_tensor_value_info(output_name, TensorProto.FLOAT, [1, 1])],
        initializer=[weights, bias])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 7
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


class NameRuleTests(unittest.TestCase):
    def test_accepts_documented_characters(self):
        for name in ("my-word", "word_1", "a.b", "Computer2", "-x"):
            wm.validate_name(name)

    def test_rejects_paths_spaces_and_hidden_names(self):
        for name in ("", ".hidden", "../escape", "a/b", "a\\b", "two words", "x\n", "é"):
            with self.assertRaises(wm.CheckError, msg=name):
                wm.validate_name(name)


class ClassifierContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wakeword-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_accepts_stock_shaped_classifier(self):
        info = wm.check_classifier(make_classifier(self.path("ok.onnx")))
        self.assertEqual(info["operators"], ["Flatten", "Gemm", "Sigmoid"])

    def test_refuses_operator_outside_the_stock_set(self):
        # A LayerNorm-style ReduceMean/Sqrt pair, as reported in LibreEcho#241.
        bad = make_classifier(self.path("norm.onnx"), extra_nodes=[
            helper.make_node("ReduceMean", ["flat"], ["mean"], axes=[1], keepdims=1),
            helper.make_node("Sqrt", ["mean"], ["root"]),
        ])
        with self.assertRaisesRegex(wm.CheckError, "ReduceMean"):
            wm.check_classifier(bad)

    def test_refuses_wrong_input_name(self):
        path = make_classifier(self.path("in.onnx"), input_name="input.1")
        with self.assertRaisesRegex(wm.CheckError, "onnx::Flatten_0"):
            wm.check_classifier(path)

    def test_refuses_wrong_output_name(self):
        path = make_classifier(self.path("out.onnx"), output_name="score")
        with self.assertRaisesRegex(wm.CheckError, "named 13"):
            wm.check_classifier(path)

    def test_refuses_model_without_sigmoid(self):
        path = make_classifier(self.path("nosig.onnx"), sigmoid=False)
        with self.assertRaisesRegex(wm.CheckError, "Sigmoid"):
            wm.check_classifier(path)

    def test_refuses_symlinked_classifier(self):
        real = make_classifier(self.path("real.onnx"))
        link = self.path("link.onnx")
        os.symlink(real, link)
        with self.assertRaisesRegex(wm.CheckError, "regular file"):
            wm.check_classifier(link)

    def test_package_refuses_bad_name_before_reading_models(self):
        path = make_classifier(self.path("ok.onnx"))
        with self.assertRaisesRegex(wm.CheckError, "may not start with"):
            wm.package(path, ".hidden", "/nonexistent", self.tmp)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".hidden")))

    def test_threshold_matches_device_mapping(self):
        self.assertAlmostEqual(wm.threshold_for(0), 0.85)
        self.assertAlmostEqual(wm.threshold_for(100), 0.41)
        self.assertAlmostEqual(wm.threshold_for(50), 0.63)
        with self.assertRaises(wm.CheckError):
            wm.threshold_for(101)


@unittest.skipUnless(HAVE_STOCK, "set WAKEWORD_STOCK_DIR to the stock model folder")
class StockModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wakeword-stock-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stock_classifier_passes_contract(self):
        info = wm.check_classifier(os.path.join(STOCK_DIR, wm.CLASSIFIER_FILE))
        self.assertEqual(info["operators"], ["Flatten", "Gemm", "Relu", "Sigmoid"])

    def test_stock_feature_models_match_engine_io(self):
        wm.check_feature_models(STOCK_DIR)

    def test_package_writes_three_regular_files_and_manifest(self):
        classifier = os.path.join(STOCK_DIR, wm.CLASSIFIER_FILE)
        target, manifest = wm.package(classifier, "stock-copy", STOCK_DIR, self.tmp)
        self.assertEqual(sorted(os.listdir(target)), sorted(wm.MODEL_FILES))
        for name in wm.MODEL_FILES:
            path = os.path.join(target, name)
            self.assertFalse(os.path.islink(path))
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(manifest["files"][name]["sha256"], wm.sha256_file(path))
            self.assertTrue(manifest["matches_stock_build"][name])
        with open(os.path.join(self.tmp, "stock-copy.manifest.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["name"], "stock-copy")

    def test_package_refuses_overwrite_without_force(self):
        classifier = os.path.join(STOCK_DIR, wm.CLASSIFIER_FILE)
        wm.package(classifier, "twice", STOCK_DIR, self.tmp)
        with self.assertRaisesRegex(wm.CheckError, "--force"):
            wm.package(classifier, "twice", STOCK_DIR, self.tmp)
        wm.package(classifier, "twice", STOCK_DIR, self.tmp, force=True)

    @unittest.skipUnless(FIXTURE_DIR and os.path.isfile(os.path.join(FIXTURE_DIR, "alexa-twice.wav")),
                         "set WAKEWORD_FIXTURE_DIR to the UI voice-e2e fixtures")
    def test_host_score_separates_wake_word_from_silence_clip(self):
        model_dir = os.path.join(self.tmp, "stock")
        os.makedirs(model_dir)
        for name in wm.MODEL_FILES:
            shutil.copyfile(os.path.join(STOCK_DIR, name), os.path.join(model_dir, name))
        threshold, rows = wm.score_clips(model_dir, [
            os.path.join(FIXTURE_DIR, "alexa-twice.wav"),
            os.path.join(FIXTURE_DIR, "no-wake-word.wav"),
        ], sensitivity=50)
        self.assertAlmostEqual(threshold, 0.63)
        by_clip = {row["clip"]: row for row in rows}
        self.assertGreaterEqual(by_clip["alexa-twice.wav"]["peak"], threshold)
        self.assertLess(by_clip["no-wake-word.wav"]["peak"], threshold)
        self.assertEqual(by_clip["no-wake-word.wav"]["blocks_at_or_above_threshold"], 0)


class StreamTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_STOCK, "set WAKEWORD_STOCK_DIR to the stock model folder")
    def test_startup_suppression_and_block_count(self):
        stream = wm.EngineStream(STOCK_DIR)
        scores = stream.feed(np.zeros(wm.BLOCK_SAMPLES * 7 + 10, np.int16))
        self.assertEqual(len(scores), 7)
        self.assertEqual(scores[:5], [0.0] * 5)
        self.assertEqual(len(stream.pending), 10)


if __name__ == "__main__":
    unittest.main()
