# Community-noncommercial source and payload closure

This record establishes the exact corresponding-source/relink and payload
identities for LibreEcho's five-feature `community-noncommercial` profile. It is
host-build evidence, not publication, legal advice, flashing, boot, playback,
capture, wakeword-inference, or hardware-runtime acceptance.

## Hash-derivation probe

The source-offer hashes were derived from this immutable no-publish probe:

- Run ID: `20260809T202654Z-19efd9685e22-clean-ota-production-community-nc-ssh0-ui50b9dedb9eed-44be97e50ff0`
- Status: `PREPARED_NOT_FLASHED`
- Published current: `false`
- Feature policy: `community-noncommercial`
- Linux source: `19efd9685e22f96cf1cb70551eae4c0075692e5c`
- Platform source: `9eb5906d8104c20c6643d322c233cb87b6bd5673`
- UI source: `50b9dedb9eedb21b0ea45b805421138a061db253`
- Build source: `776ff299b483d3728a598c914025a585df6bf2ab`
- Product policy source used by the probe: `cc0e1da04f9b117a0ff6b89081d1239b265dc241`
- Boot SHA-256: `c65f67d7f40bc384ac1dc39274ea2875fd2b3141929f581c2488fb6934293aa3`
- Source-offer index SHA-256: `b4f0d5188d3bfca9994bc14e87e4d4729cfa4923ca1d918ef413a9a1ce22cc19`

A final release candidate must be rebuilt after this catalog record is committed,
attest that later Product commit, and reproduce the six archive hashes below.

## Corresponding-source and relink offers

Every sidecar-listed member was re-read from its archive and independently
verified for exact size and SHA-256. Each tar contains one additional generated
`SOURCE-OFFER-MANIFEST.json` member.

| Component | Source-offer SHA-256 | Listed members verified |
|---|---|---:|
| Core runtime closure | `fcbcd29d4cf5fbe3b4c511c9a365bcd7adc7bf0eb6454d693fbcd5ddf31bc51f` | 117 |
| AirPlay payload | `8678aba5db5359a988f9718552c18f9446763128215ec45e1596159031c9659e` | 438 |
| STT payload | `5a494d737a3865675fba0ca1996aa062acf08e43fcdf9c149f2f364bef9396ce` | 1,169 |
| TTS payload | `41fe96ec156379ddae46a6e6816de2673c12e5b4eb88b9b055d4573c148ba33e` | 1,169 |
| Wakeword payload | `75d4e47bb784d9c21b41f351b16508c1b5a90ad837ee6fbf615c425e09158fc8` | 818 |
| Assistant payload | `2b15821a7b44b02767f3f8c7029d3ba0637599644e2550aa39b273c075bc895d` | 561 |

Total sidecar-listed members independently verified: **4,272**.

## Five feature payloads

| Feature | Payload SHA-256 | Bytes |
|---|---|---:|
| AirPlay | `7f9e39a754cbed79f6e0520fc5f8df9695c890fb0bed364d4d593258604f4db7` | 8,036,352 |
| STT | `5ff4e5c3a9d3da95b56f8d7c34f6efa0584993c41466de61084750fb7fcff554` | 57,663,488 |
| TTS | `f086e8b1677e26ba984b2ecaf592e4f37bf04d24d99b3efb6a6eb25a53593a3c` | 150,216,704 |
| Wakeword | `91445b330f5deaf0f1ce1bd1c34bc81d0dcb8b93b0aae25e4c1305dee80c90d6` | 8,192,000 |
| Assistant | `117e7e3d454a63dd12b728d909619834a532b0b07c4b902b930cae71675c0154` | 2,973,696 |

The image and userdata manifests require all five payloads. The wakeword
packager separately enforces the reviewed model identities:

- `melspectrogram.onnx`: `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f`
- `embedding_model.onnx`: `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f`
- `alexa_v0.1.onnx`: `6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e`

It also requires and embeds `MODEL-LICENSE.txt`, the complete
`CC-BY-NC-SA-4.0.txt`, and the runtime licence bundle.

## Distribution boundary

The wakeword model is cleared only for the explicitly labelled
`community-noncommercial` scope. Recipients must preserve attribution and the
full licence, indicate modifications, use the model only as permitted by the
NonCommercial condition, and apply ShareAlike to adaptations. This record does
not authorize unrestricted commercial distribution.

The probe did not alter canonical `out/CURRENT` and was not published, flashed,
booted, OTA-installed, runtime-tested, or slot-confirmed.
