# Community-noncommercial source and payload closure

This record establishes the exact corresponding-source/relink and payload
identities for LibreEcho's five-feature `community-noncommercial` profile. It is
host-build evidence, not publication, legal advice, flashing, boot, playback,
capture, wakeword-inference, or hardware-runtime acceptance.

## Hash-derivation probe

The source-offer hashes were derived from this immutable no-publish probe:

- Run ID: `20260811T214603Z-a6c4b01faae9-clean-ota-production-dev-community-nc-ssh0-ui021001d035c6-71f0be364c08`
- Status: `PREPARED_NOT_FLASHED`
- Published current: `false`
- Feature policy: `community-noncommercial`
- Linux source: `a6c4b01faae9b937f9067d4d14ee0917f662577c`
- Platform source: `583cfbcc400767eb01187bf123abe6ad0dca824c`
- UI source: `021001d035c6044b7f78841434ac3e58c48c5708`
- Build source: `d2ff4c406c3948a2b52f089e1f972baa3399803c`
- Product policy source used by the probe: `c0f3dc3ad89c3a739c0c477b3f98e26acc9a4d70`
- Boot SHA-256: `c73a8cac2a981545b562ef3a4711f2f9622f3ec94432fbc21cedbf6ba3aee9b9`
- Source-offer index SHA-256: `ed49a1bda6bb677fc2d39c4a1bba5c66ee9143193eea54a284ecb833d8b142e8`

A final release candidate must be rebuilt after this catalog record is committed,
attest that later Product commit, and reproduce the six archive hashes below.

## Corresponding-source and relink offers

Every sidecar-listed member was re-read from its archive and independently
verified for exact size and SHA-256. Each tar contains one additional generated
`SOURCE-OFFER-MANIFEST.json` member.

| Component | Source-offer SHA-256 | Listed members verified |
|---|---|---:|
| Core runtime closure | `3e4f611fa07044c1e8e0060b7a1d9cc356493dfb42b963a82eccb9e9ff125952` | 93 |
| AirPlay payload | `f159ecdb4e0381433c78c4e80a360bc6c3eb45e4c0c7f4caadbbe355c37a6031` | 393 |
| STT payload | `e5ccaaed9380493bde952f5435ef6612d60b116c6c6e18bb6f00110d95742d03` | 1,131 |
| TTS payload | `22be3e3cfc0446991a0a9c85c08c39d77212d44684322f6af1d9fa30761e9447` | 1,131 |
| Wakeword payload | `8be7517a3f2feff5effe36f259ec2c35e3ffeded779fbfc4386f0c5bcb9833ac` | 58 |
| Assistant payload | `85ee50f6befa873345b7444510c988e3625987fb4032170099d7c64f27541027` | 432 |

Total sidecar-listed members independently verified: **3,238**.

## Five feature payloads

| Feature | Payload SHA-256 | Bytes |
|---|---|---:|
| AirPlay | `69457590a383c74bafa53183345ff56ecf68907e4fbc8a29c5a712926f5fc96f` | 7,995,392 |
| STT | `969d05c576619a9a6ee986b9cba7dc95face689e4de8961a2a640e7f29e44fe1` | 57,659,392 |
| TTS | `978994158b46b83213cf791db6dfb80b08e6e043c9d2ee4a9e73b106c15db128` | 150,216,704 |
| Wakeword | `4140159883a47fb22286918298865a0364111b730f32160855f516e4ba126302` | 8,192,000 |
| Assistant | `55e7ef2a927221b026e260cf5a1656a42bb13cf8d0725f8b56b9cdaee19a8f47` | 2,981,888 |

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
