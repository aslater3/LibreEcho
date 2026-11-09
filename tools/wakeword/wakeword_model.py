#!/usr/bin/env python3
"""Check, package and host-score openWakeWord models for LibreEcho.

The device wake engine (LibreEcho-UI src/adapter/wake_engine_onnx.cpp) runs
three ONNX graphs in sequence: melspectrogram.onnx, embedding_model.onnx and a
classifier that must be named alexa_v0.1.onnx. A model folder holds exactly
those three regular files, and the owner selects it under
/data/libreecho/wakeword-models/<name>/ (see docs/wake-word-training.md).

Subcommands:
  check    validate a trained classifier against the engine contract
  package  validate and copy a classifier and feature models into <out>/<name>/
  score    run the engine's streaming pipeline over 16 kHz mono WAV clips

This tool does not train a model. Training uses outside tools; see the guide.
Requires onnx, onnxruntime and numpy (tools/wakeword/requirements.txt).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import wave

import numpy as np
import onnx
import onnxruntime as ort

MEL_FILE = "melspectrogram.onnx"
EMBEDDING_FILE = "embedding_model.onnx"
CLASSIFIER_FILE = "alexa_v0.1.onnx"
MODEL_FILES = (MEL_FILE, EMBEDDING_FILE, CLASSIFIER_FILE)

# Mirrors src/adapter/wake_engine_onnx.cpp (LibreEcho-UI release/0.14.6).
SAMPLE_RATE = 16000
BLOCK_SAMPLES = 1280
OVERLAP_SAMPLES = 480
MEL_FRAMES = 76
MEL_BINS = 32
FEATURE_FRAMES = 16
FEATURE_BINS = 96
PRIMING_BLOCKS = 16
STARTUP_SUPPRESSED = 5

MEL_INPUT, MEL_OUTPUT = "input", "output"
EMBEDDING_INPUT, EMBEDDING_OUTPUT = "input_1", "conv2d_19"
CLASSIFIER_INPUT, CLASSIFIER_OUTPUT = "onnx::Flatten_0", "13"

# Operators the stock classifier uses. The device runtime was reported to fail
# on other operators such as ReduceMean and Sqrt (LibreEcho#241), so any model
# using them is refused here rather than failing on the device.
CLASSIFIER_OPS = frozenset({"Flatten", "Gemm", "Relu", "Sigmoid"})

# sha256 of the feature models and classifier in the 0.14.x build inputs.
# Used only to report whether a package matches the stock build.
STOCK_SHA256 = {
    MEL_FILE: "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    EMBEDDING_FILE: "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
    CLASSIFIER_FILE: "6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e",
}

# Same mapping as libreecho-waked (src/adapter/waked.c).
THRESHOLD_BASE = 0.85
THRESHOLD_PER_SENSITIVITY = 0.0044

# Letters, digits, '.', '_' and '-'; may not start with '.'.
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")

PROVIDERS = ["CPUExecutionProvider"]


class CheckError(Exception):
    """A model or input is refused. The message says why."""


def _require(condition, message):
    if not condition:
        raise CheckError(message)


def validate_name(name):
    _require(NAME_PATTERN.fullmatch(name) is not None,
             "model name %r may use only letters, digits, '.', '_' and '-', "
             "and may not start with '.'" % name)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def threshold_for(sensitivity):
    _require(0 <= sensitivity <= 100, "sensitivity must be 0..100")
    return THRESHOLD_BASE - THRESHOLD_PER_SENSITIVITY * sensitivity


def check_classifier(path):
    """Raise CheckError unless the classifier fits the device contract."""
    _require(os.path.isfile(path) and not os.path.islink(path),
             "classifier must be a regular file: %s" % path)
    model = onnx.load(path, load_external_data=False)
    external = [t.name for t in model.graph.initializer
                if t.data_location == onnx.TensorProto.EXTERNAL]
    _require(not external,
             "classifier keeps weights in external data; export one self-contained file")
    domains = {opset.domain for opset in model.opset_import}
    _require(domains <= {"", "ai.onnx"},
             "classifier imports non-default opset domains: %s" % sorted(domains))
    ops = sorted({node.op_type for node in model.graph.node})
    unsupported = [op for op in ops if op not in CLASSIFIER_OPS]
    _require(not unsupported,
             "classifier uses operators %s; the stock classifier uses only %s"
             % (unsupported, sorted(CLASSIFIER_OPS)))
    _require("Sigmoid" in ops,
             "classifier must end in Sigmoid so the score is a 0..1 probability")

    session = ort.InferenceSession(path, providers=PROVIDERS)
    inputs, outputs = session.get_inputs(), session.get_outputs()
    _require(len(inputs) == 1 and inputs[0].name == CLASSIFIER_INPUT,
             "classifier input must be named %s, found %s"
             % (CLASSIFIER_INPUT, [i.name for i in inputs]))
    _require(list(inputs[0].shape) == [1, FEATURE_FRAMES, FEATURE_BINS],
             "classifier input shape must be [1, 16, 96], found %s" % (inputs[0].shape,))
    _require(len(outputs) == 1 and outputs[0].name == CLASSIFIER_OUTPUT,
             "classifier output must be named %s, found %s"
             % (CLASSIFIER_OUTPUT, [o.name for o in outputs]))
    result = session.run(None, {CLASSIFIER_INPUT: np.zeros((1, FEATURE_FRAMES, FEATURE_BINS), np.float32)})[0]
    value = float(np.asarray(result).reshape(-1)[0]) if np.asarray(result).size == 1 else None
    _require(value is not None and np.isfinite(value) and 0.0 <= value <= 1.0,
             "classifier must return one score in 0..1")
    return {"operators": ops, "opset": sorted((o.domain or "ai.onnx", o.version)
                                              for o in model.opset_import)}


def check_feature_models(directory):
    """Raise CheckError unless mel and embedding models match the engine's I/O."""
    mel_path = os.path.join(directory, MEL_FILE)
    embedding_path = os.path.join(directory, EMBEDDING_FILE)
    for path in (mel_path, embedding_path):
        _require(os.path.isfile(path) and not os.path.islink(path),
                 "missing regular feature model: %s" % path)

    mel = ort.InferenceSession(mel_path, providers=PROVIDERS)
    _require([i.name for i in mel.get_inputs()] == [MEL_INPUT]
             and [o.name for o in mel.get_outputs()] == [MEL_OUTPUT],
             "melspectrogram I/O names must be %s -> %s" % (MEL_INPUT, MEL_OUTPUT))
    mel_out = mel.run(None, {MEL_INPUT: np.zeros(
        (1, OVERLAP_SAMPLES + BLOCK_SAMPLES), np.float32)})[0]
    _require(mel_out.size > 0 and mel_out.size % MEL_BINS == 0,
             "melspectrogram output must be a multiple of %d bins" % MEL_BINS)

    embedding = ort.InferenceSession(embedding_path, providers=PROVIDERS)
    _require([i.name for i in embedding.get_inputs()] == [EMBEDDING_INPUT]
             and [o.name for o in embedding.get_outputs()] == [EMBEDDING_OUTPUT],
             "embedding I/O names must be %s -> %s" % (EMBEDDING_INPUT, EMBEDDING_OUTPUT))
    emb_out = embedding.run(None, {EMBEDDING_INPUT: np.zeros(
        (1, MEL_FRAMES, MEL_BINS, 1), np.float32)})[0]
    _require(emb_out.size == FEATURE_BINS,
             "embedding output must hold %d values" % FEATURE_BINS)


def stock_match(paths):
    """Return {file: True/False} for whether each file equals the stock build."""
    return {name: sha256_file(path) == STOCK_SHA256[name]
            for name, path in paths.items()}


def package(classifier, name, feature_dir, out_dir, force=False):
    """Validate and install <out_dir>/<name>/ plus <out_dir>/<name>.manifest.json."""
    validate_name(name)
    check_classifier(classifier)
    check_feature_models(feature_dir)

    target = os.path.join(out_dir, name)
    if os.path.lexists(target) and not force:
        raise CheckError("%s already exists; pass --force to replace it" % target)
    os.makedirs(out_dir, exist_ok=True)

    sources = {
        MEL_FILE: os.path.join(feature_dir, MEL_FILE),
        EMBEDDING_FILE: os.path.join(feature_dir, EMBEDDING_FILE),
        CLASSIFIER_FILE: classifier,
    }
    staging = tempfile.mkdtemp(dir=out_dir, prefix=".stage-%s." % name)
    try:
        for dest_name, source in sources.items():
            shutil.copyfile(source, os.path.join(staging, dest_name))
        os.chmod(staging, 0o755)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        elif os.path.lexists(target):
            os.remove(target)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    files = {}
    for dest_name in MODEL_FILES:
        path = os.path.join(target, dest_name)
        files[dest_name] = {"bytes": os.path.getsize(path), "sha256": sha256_file(path)}
    manifest = {
        "name": name,
        "files": files,
        "matches_stock_build": stock_match(
            {dest: os.path.join(target, dest) for dest in MODEL_FILES}),
        "onnxruntime": ort.__version__,
        "tool": "tools/wakeword/wakeword_model.py",
    }
    manifest_path = os.path.join(out_dir, name + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return target, manifest


class EngineStream:
    """Host mirror of WakeEngine::Feed and ProcessBlock in wake_engine_onnx.cpp."""

    def __init__(self, model_dir):
        self.mel = ort.InferenceSession(os.path.join(model_dir, MEL_FILE), providers=PROVIDERS)
        self.embedding = ort.InferenceSession(os.path.join(model_dir, EMBEDDING_FILE), providers=PROVIDERS)
        self.classifier = ort.InferenceSession(os.path.join(model_dir, CLASSIFIER_FILE), providers=PROVIDERS)
        self.mel_history = np.ones(MEL_FRAMES * MEL_BINS, np.float32)
        self.feature_history = np.zeros(FEATURE_FRAMES * FEATURE_BINS, np.float32)
        self.feature_count = 0
        self.overlap = np.zeros(0, np.float32)
        self.pending = np.zeros(0, np.int16)
        self.prediction_count = 0
        for _ in range(PRIMING_BLOCKS):
            self._process(np.zeros(BLOCK_SAMPLES, np.int16), suppress=False)
        self.mel_history[:] = 1.0
        self.overlap = np.zeros(0, np.float32)
        self.prediction_count = 0

    def feed(self, samples):
        """Return one score per completed 80 ms block."""
        self.pending = np.concatenate([self.pending, samples.astype(np.int16)])
        scores = []
        while self.pending.size >= BLOCK_SAMPLES:
            block = self.pending[:BLOCK_SAMPLES]
            self.pending = self.pending[BLOCK_SAMPLES:]
            scores.append(self._process(block, suppress=True))
        return scores

    def _process(self, block, suppress):
        audio = np.concatenate([self.overlap, block.astype(np.float32)])
        mel = np.asarray(self.mel.run(None, {MEL_INPUT: audio.reshape(1, -1)})[0],
                         np.float32).reshape(-1)
        _require(mel.size > 0 and mel.size % MEL_BINS == 0, "invalid mel output shape")
        frames = mel.size // MEL_BINS
        mel = mel / np.float32(10.0) + np.float32(2.0)
        self._append_mel(mel.reshape(frames, MEL_BINS), frames)

        embedding = np.asarray(self.embedding.run(
            None, {EMBEDDING_INPUT: self.mel_history.reshape(1, MEL_FRAMES, MEL_BINS, 1)})[0],
            np.float32).reshape(-1)
        _require(embedding.size == FEATURE_BINS, "invalid embedding output shape")
        self._append_feature(embedding)

        score = 0.0
        if self.feature_count >= FEATURE_FRAMES:
            out = self.classifier.run(None, {CLASSIFIER_INPUT: self.feature_history.reshape(
                1, FEATURE_FRAMES, FEATURE_BINS)})[0]
            score = float(np.asarray(out, np.float32).reshape(-1)[0])
        if suppress:
            count = self.prediction_count
            self.prediction_count += 1
            if count < STARTUP_SUPPRESSED:
                score = 0.0
        self.overlap = block[BLOCK_SAMPLES - OVERLAP_SAMPLES:].astype(np.float32)
        return score

    def _append_mel(self, frames, count):
        if count >= MEL_FRAMES:
            self.mel_history = frames[count - MEL_FRAMES:].reshape(-1).copy()
        else:
            kept = self.mel_history[count * MEL_BINS:]
            self.mel_history = np.concatenate([kept, frames.reshape(-1)])

    def _append_feature(self, feature):
        self.feature_history = np.concatenate([self.feature_history[FEATURE_BINS:], feature])
        if self.feature_count < FEATURE_FRAMES:
            self.feature_count += 1


def read_wav(path):
    with wave.open(path, "rb") as wav:
        _require(wav.getnchannels() == 1 and wav.getsampwidth() == 2
                 and wav.getframerate() == SAMPLE_RATE,
                 "%s must be 16 kHz mono 16-bit PCM WAV" % path)
        raw = wav.readframes(wav.getnframes())
    return np.frombuffer(raw, dtype="<i2")


def score_clips(model_dir, clips, sensitivity):
    threshold = threshold_for(sensitivity)
    rows = []
    for clip in clips:
        stream = EngineStream(model_dir)
        scores = stream.feed(read_wav(clip))
        peak = max(scores) if scores else 0.0
        peak_index = scores.index(peak) if scores else 0
        rows.append({
            "clip": os.path.basename(clip),
            "blocks": len(scores),
            "peak": round(peak, 4),
            "peak_seconds": round((peak_index + 1) * BLOCK_SAMPLES / SAMPLE_RATE, 2),
            "blocks_at_or_above_threshold": sum(1 for s in scores if s >= threshold),
        })
    return threshold, rows


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="validate a classifier against the contract")
    p_check.add_argument("classifier")

    p_pack = sub.add_parser("package", help="install a validated model folder")
    p_pack.add_argument("--classifier", required=True, help="trained classifier .onnx")
    p_pack.add_argument("--name", required=True, help="model name used in /data/.../wakeword-models/")
    p_pack.add_argument("--feature-dir", required=True,
                        help="folder holding melspectrogram.onnx and embedding_model.onnx")
    p_pack.add_argument("--out", required=True, help="output directory")
    p_pack.add_argument("--force", action="store_true", help="replace an existing package")

    p_score = sub.add_parser("score", help="host-score clips with a packaged model folder")
    p_score.add_argument("--model-dir", required=True, help="folder with the three model files")
    p_score.add_argument("--sensitivity", type=int, default=50,
                         help="device wake_sensitivity 0..100 (default 50, the setup default)")
    p_score.add_argument("clips", nargs="+", help="16 kHz mono 16-bit WAV files")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            info = check_classifier(args.classifier)
            print(json.dumps({"ok": True, **info}, sort_keys=True))
        elif args.command == "package":
            target, manifest = package(args.classifier, args.name, args.feature_dir,
                                       args.out, force=args.force)
            print(json.dumps({"ok": True, "installed": target, "manifest": manifest},
                             indent=2, sort_keys=True))
        elif args.command == "score":
            threshold, rows = score_clips(args.model_dir, args.clips, args.sensitivity)
            print(json.dumps({"ok": True, "threshold": round(threshold, 4),
                              "clips": rows}, indent=2, sort_keys=True))
    except CheckError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
