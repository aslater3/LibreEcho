# Training a custom wake word

LibreEcho can run an owner-trained openWakeWord model in place of the stock
"Alexa" model. You train the model with outside tools, then use the host tool
in `tools/wakeword/` to check, package and host-score it, and finally stage the
folder on the device. This guide covers the exact contract the device loads.

What this does not give you yet:

- LibreEcho does not train models. Training data and the training run are
  yours (see section 2).
- Only one model is active at a time. The stock detector is always used; only
  the model file is replaceable.
- The device still reports the wake phrase as "Alexa" in its status and UI,
  even when a custom model is loaded.
- A host score is a proxy. The device adds its own gating (see section 4).
- The device-side load and detection of a newly trained word has not been
  verified in this repository. Treat your first run as an experiment and keep
  your backups (see section 5).

## 1. What the device loads

The wake engine (`LibreEcho-UI`, `src/adapter/wake_engine_onnx.cpp`) runs three
ONNX graphs for every 80 ms block (1280 samples at 16 kHz, mono, 16-bit):

| File | Input | Output | Notes |
|---|---|---|---|
| `melspectrogram.onnx` | `input` float `[batch, samples]` | `output` | Fed the 480 overlap samples from the previous block plus the 1280 new ones (1280 for the first block). Raw int16 values as float. Output is scaled by `x / 10 + 2`. |
| `embedding_model.onnx` | `input_1` float `[1, 76, 32, 1]` | `conv2d_19` | 76 mel frames of 32 bins in; 96 features out. |
| `alexa_v0.1.onnx` (the classifier) | `onnx::Flatten_0` float `[1, 16, 96]` | `13` float `[1, 1]` | Score in 0..1, one per block. |

The classifier file must be named `alexa_v0.1.onnx` inside the model folder,
whatever your word is. The name is a fixed contract in the engine.

Classifier rules, enforced by the tool:

- Input and output names and shapes exactly as above, opset 13 as the stock
  model uses.
- Operators limited to `Flatten`, `Gemm`, `Relu` and `Sigmoid`, as in the
  stock classifier. Other operators are refused. Layers that export to
  `ReduceMean` or `Sqrt` (a LayerNorm, for example) were reported to fail to
  load on the device's stock detector runtime ([LibreEcho#241](https://github.com/aslater3/LibreEcho/issues/241)).
  Remove such layers, or fold them into the neighbouring `Gemm` weights, before
  export.
- The model must end in `Sigmoid`, so the score is a probability.
- One self-contained `.onnx` file. No external weight files.

Feature models: copy `melspectrogram.onnx` and `embedding_model.onnx` from the
same openWakeWord release your trainer uses. The LibreEcho build inputs pin
these SHA-256 values; the tool reports whether your copies match them:

| File | SHA-256 (0.14.x build inputs) |
|---|---|
| `melspectrogram.onnx` | `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f` |
| `embedding_model.onnx` | `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f` |
| `alexa_v0.1.onnx` (stock Alexa classifier) | `6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e` |

## 2. Train the classifier (outside tools)

Use openWakeWord's own training pipeline (the upstream project is
`dscripka/openWakeWord`; its automatic model training notebook is the usual
starting point). That pipeline needs data this repository does not provide:

- positive clips of your word, from text-to-speech, from recordings, or both;
- a large set of negative clips and features (speech without the word, music,
  room noise);
- a Python environment with its training dependencies.

When you export the trained classifier to ONNX, set the graph names the engine
looks for and the opset:

```python
torch.onnx.export(
    model, dummy_input, "alexa_v0.1.onnx",
    input_names=["onnx::Flatten_0"],
    output_names=["13"],
    opset_version=13,
)
```

Use your trainer's own export code where it differs; the names and shapes must
still match section 1. The tool checks them.

Licensing: the stock Alexa classifier is pinned in the build as CC-BY-NC-SA-4.0.
Check the licence of every data set, TTS voice and recording you train on, and
of the trained model if you share it. Do not share recordings of other people.
Recordings of your household are personal data; keep them on your own machine.

## 3. Check and package with the host tool

The tool needs Python 3 with the pinned packages in
`tools/wakeword/requirements.txt`:

```sh
python3 -m venv .wakeword-venv
.wakeword-venv/bin/pip install -r tools/wakeword/requirements.txt

# Validate a classifier against the device contract.
.wakeword-venv/bin/python tools/wakeword/wakeword_model.py check my-classifier.onnx

# Install a model folder: <out>/<name>/ holds exactly the three files, plus
# <out>/<name>.manifest.json with SHA-256 values.
.wakeword-venv/bin/python tools/wakeword/wakeword_model.py package \
    --classifier my-classifier.onnx \
    --name my-word \
    --feature-dir /path/to/openwakeword/feature-models \
    --out ./wakeword-packages
```

Names may use only letters, digits, `.`, `_` and `-`, and may not start with
`.`. The tool exits 1 and prints the reason when a check fails. It writes
nothing when the classifier or feature models are refused.

## 4. Host-score clips before the device

Record clips as 16 kHz, mono, 16-bit PCM WAV. Convert other formats with your
usual audio tools first. Then:

```sh
.wakeword-venv/bin/python tools/wakeword/wakeword_model.py score \
    --model-dir ./wakeword-packages/my-word \
    --sensitivity 50 \
    clips/word-*.wav clips/silence-*.wav
```

The output lists, per clip, the highest block score, when it occurred, and how
many blocks reached the threshold. The threshold is the device mapping
`0.85 - 0.0044 * sensitivity` (0.63 at the default sensitivity 50, 0.41 at 100).

Use the sensitivity you have set on the device. Each clip is scored in a fresh
stream, and the first five blocks of every clip are zeroed, which mirrors the
engine's start-up suppression.

Reading the result:

- Positive clips should peak above the threshold, from several speakers and
  distances, or the model is not learning the word.
- Negative clips (speech without the word, TV, music) should stay below it.
- The device's wake worker also requires support from neighbouring frames
  before it accepts a peak, and it applies a 1.75 s cooldown after a detection.
  A block above threshold is necessary to wake the device, not sufficient.

For reference, the stock Alexa model scores the UI test fixtures
`alexa-twice.wav` (peak 1.0) and `no-wake-word.wav` (peak 0.0026) with this
pipeline, so a clean separation between a word and silence is what good
numbers look like.

## 5. Install on the device

Only the owner's device is involved, and these steps write `/data`. Make a
backup of the current configuration first.

1. Copy the model folder, unchanged, to
   `/data/libreecho/wakeword-models/<name>/` on the device. Use the copy method
   you already use for device files. Staged models are excluded from
   configuration backups, so keep your own copy.
2. Write the folder name (only the name, no path) to
   `/data/libreecho/config/wake-model`.
3. Restart the wake service: `/etc/init.d/libreecho-waked.init restart`.
4. Check `/var/log/libreecho-waked.log`. A successful selection logs
   `using owner-staged model`. An incomplete folder, an invalid name, or a model
   the detector cannot load falls back to the stock model, and the log says why.
5. Say the word and watch for a wake. The web "wake-word test" action is
   synthetic and does not prove real detection.

To return to the stock model, delete `/data/libreecho/config/wake-model` and
restart the service. The selection and the staged folder both live on `/data`,
so they are meant to survive reboot and OTA updates. That has not been tested
on hardware yet.

The detector executable always comes from the signed wake payload. Nothing
under `/data` is executed, so a bad model cannot run code, only fail to load.

## 6. What is verified and what is not

Verified in this repository (host only):

- The contract constants and the streaming pipeline in `wakeword_model.py`
  mirror `wake_engine_onnx.cpp` and the classifier contract. `alexa_v0.1.onnx`
  from the build passes the contract check.
- The unit tests in `tools/test_wakeword_model.py` cover the name rules,
  refused operators and names, package layout and manifest, and host scoring
  against the stock model and UI fixtures.

Not verified here:

- A newly trained word, loaded and detected on a device. That needs your own
  training run and a hardware check.
- Device acceptance of operators outside the stock set. The tool refuses them
  on purpose.
