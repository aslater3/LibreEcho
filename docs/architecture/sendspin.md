# Sendspin integration architecture (LibreEcho ARM32)

Status: **design contract — not production-ready.** This document freezes the
input identity, the host source closure, the ownership boundary, and the
`LE_AUDIO_SINK/1` sink protocol for the Sendspin integration. It authorizes no
build, image, or hardware claim. Every pin below is an immutable commit or a
SHA-256-verified archive; nothing here is fetched from a moving tag at build or
runtime.

## 1. Frozen upstream identity

All three protocol/SDK/oracle references are frozen to commits verified against
the live upstream repositories. A future bump changes this table, the Product
inventory (`build/inputs/public-inputs.json`), and the Platform source lock
together, in one coordinated change — they must never disagree.

| Role | Repository | Frozen ref (commit) | Archive SHA-256 |
| --- | --- | --- | --- |
| Protocol spec (RC1) | `Sendspin/spec` | `671a34d408581fc25ea56b3528a4a3f13e3be901` | `f0890562729069498cc5da57b63d1eea5f24cd661dc6dfc0c168614a216ffd3e` |
| C++ SDK (RC1) | `Sendspin/sendspin-cpp` (`encryption-support-rc1`) | `8cdd4b38d029f3ef754756d494e0b53c42b81d75` | `2749ee07e4974f3879297af12d651349df33f46125a100cc935bf6966ed8af45` |
| Server conformance oracle | `Sendspin/aiosendspin` | `83209af414e1950dbbd0ebf60a9c0567b2c5f0c8` | `805dc716dc6bb80115040c476610342f27b68b44462bbcc0dd2713c2e98fba15` |

The C++ SDK is taken from the **RC1 branch** (`encryption-support-rc1`), never
from `main`. `main` carries the legacy `client/hello` handshake and is **not**
substituted: the integration depends on the RC1 encrypted handshake and pairing
surface (Noise `KKpsk2`, `ChaChaPoly20-Poly1305`, CPACE-X25519 pairing).

## 2. Host source closure and offline staging

The host build closure is the C++ SDK plus its transitive host dependencies. The
list below is the entire closure; Opus and the unused roles are excluded, and
the ESP-IDF-only `esp_websocket_client` is not a host dependency.

| Dependency | Repository | Commit | Archive SHA-256 | In closure |
| --- | --- | --- | --- | --- |
| ArduinoJson | `bblanchon/ArduinoJson` | `32520135092970120a5ac165cf45f48e658c421d` | `9e48d9b1c690eed365e95a690c76f917af104eb74ffe3916568351dc78d45b2e` | yes |
| IXWebSocket | `machinezone/IXWebSocket` | `c5a02f1066fb0fde48f80f51178429a27f689a39` | `ef272693e67daef33275daa8d3685f48e8fe4dbe098338750f9dad3013016d96` | yes (`USE_TLS=OFF`, `USE_ZLIB=OFF`) |
| micro-flac | `esphome-libs/micro-flac` | `9f8bfe5c9ee46cea175084b49ae8ac95545705b5` | `bab7a0adc5a5a016d32bcb7cb17f38477f2397d4ef2d6f6de2dfecb990388bb7` | yes |
| micro-ogg-demuxer | `esphome-libs/micro-ogg-demuxer` | `865ad9d831e7dc76bb9c142607bae33fc75648e7` | `bb81cc1b64d1d888e5d5c93d12d07629887d1a197ab699e196fc37698e9f47bd` | yes (micro-flac submodule `lib/micro-ogg-demuxer`) |
| noise-c | `esphome-libs/noise-c` | `a1e08809a1b8f65cd91765ba7d68a6d00648ad61` | `0bfe220508f412c3944a9fe1ed38b741940a5bbcb001cd751f79fb0ee6ec63a8` | yes (crypto/pairing, pure-C reference backend) |
| micro-opus / libopus | `esphome-libs/micro-opus` | (v0.3.5, excluded) | — | **no — `SENDSPIN_ENABLE_OPUS=OFF`** |

`noise-c` is built with the pure-C reference backend (`NOISE_USE_REFERENCE_BACKEND=1`,
`NOISE_USE_AES=0`, `NOISE_USE_LIBSODIUM=0`, OS RNG), so no libsodium/OpenSSL is
required on the target. `micro-opus` and its `lib/opus` submodule are excluded
by policy when freezing PCM-only (see §6) — a scope exclusion only, with no
licensing or patent conclusion asserted; the loader rejects any staged
`sendspin-*opus*` record.

**Offline staging.** The product build stages every source above with
`build/ci/fetch-public-deps.py`, which downloads each committed archive and
verifies its SHA-256 *before* extraction, then materializes pinned submodules the
same way. The CMake build consumes the staged trees through explicit
`FETCHCONTENT_SOURCE_DIR_ARDUINOJSON`, `FETCHCONTENT_SOURCE_DIR_IXWEBSOCKET`,
`FETCHCONTENT_SOURCE_DIR_MICRO_FLAC`, and `FETCHCONTENT_SOURCE_DIR_NOISE_C`
overrides, so `FetchContent` never reaches the network and never resolves a
`GIT_TAG`. An unset override or a digest mismatch fails the staging step closed.

A focused command,
`fetch-public-deps.py <inventory> --feature sendspin --output <dir>
[--archive-dir <cache>]`, stages only the pinned Sendspin closure — no unrelated
audio/model inputs and no libplist host tools. The downloader prefers `curl`,
falls back to `wget`, then to a bounded stdlib request, and a verified local
archive cache can be consumed through the same digest-checked path. Each source
is extracted into a private temp tree and atomically renamed into place; an
existing, non-identical destination fails closed rather than merging stale and
fresh files. The command writes a stage receipt recording, per source, the
declared archive digest, the *observed* archive digest, and the staged tree
content digest — a declared digest is never reported as verified unless it
matched the bytes actually staged.

### Target and runtime closure

The intended Sendspin companion target is the same dynamic glibc ARMHF pattern as
the AirPlay 2 companion: a C++20 process dynamically linked against the device
glibc runtime and shipped with an audited loader/library closure — **not** a
static link. Three separate things must not be conflated:

**Fixture-verified runtime closure (observed and enforced).** The LibreEcho
adapter fixture that links the pinned RC1 SDK has been cross-built for armhf and
its ELF audited. Its dynamic closure is the interpreter
`/lib/ld-linux-armhf.so.3` plus `libm.so.6` and `libc.so.6`; the C++ runtime
(`libstdc++.so.6`, `libgcc_s.so.1`) is bound **statically**, so it is *not* a
dynamic dependency. The highest required `GLIBC_*` symbol version is 2.38, within
the reviewed runtime bound of 2.39. The Platform source lock records this as
`runtime_requirements.needed`, and both the build (`build_sendspin.sh
--elf-closure`, which writes an `elf-closure.json` receipt) and
`test_sdk_build.py` fail closed if a later build's `NEEDED` set or interpreter
diverges from the lock.

**Future daemon requirement (unknown).** The production companion daemon has not
been built. If it links the C++ runtime dynamically it would add
`libstdc++.so.6`/`libgcc_s.so.1`, which are outside the reviewed mdns runtime
closure unless they are bound statically as the fixture is. Its exact closure is
therefore **not** claimed; a daemon cross-build must pin and enforce it before any
production claim. The lock records this explicitly as
`runtime_requirements.future_daemon`.

**Compile-input provenance (package identities pinned; consumer still open).**
Compilation headers and objects come from a *host-local* compile sysroot (an
armhf glibc-2.39 development sysroot such as an Ubuntu 24.04 rootfs exposing
`usr/arm-linux-gnueabihf`), not from the pinned runtime `.deb`s, which are
runtime-only and carry no headers. Compile-input provenance is separate from the
runtime symbol closure verified above. The materializer's *package identities*
(the amd64 cross compiler/binutils and the armhf cross development sysroot) are
now pinned by the authenticated lock described below; what remains explicitly
**open** is the consumer boundary — the build does not yet consume the staged
prefix and `runtime_requirements.compile_sysroot.pinned` stays `False`, so a
byte-identical rebuild is not yet reproducible end-to-end from the committed
inventory alone. The compile sysroot must be a usable `CMAKE_SYSROOT`; a partial
path such as `<root>/usr/arm-linux-gnueabihf` fails to link, and the build probes
this before configuring.

The Product repository now carries the authenticated compile-input lock
`build/inputs/armhf-cross-toolchain.lock.json` — 30 packages (the amd64 cross
compiler/binutils and the arch-independent armhf glibc-2.39 development
sysroot), each pinned by name, epoch-bearing version, architecture, URL, size,
SHA-256, and the authenticated upstream evidence (suite, `Packages` index
digest, signed `InRelease` digest, signing-key fingerprint). A stdlib
materializer/verifier, `build/ci/armhf_toolchain.py`, stages those archives
offline into a private prefix (`stage`), re-derives the expected full tree from
the archives (`verify`), and prints the consumer contract (`SYSROOT`,
`CROSS_PREFIX`, `LD_LIBRARY_PATH`). This does **not** change the Platform
consumer or the `runtime_requirements.compile_sysroot.pinned` flag: accepting
the staged prefix in `build_sendspin.sh` and flipping that flag remain a
separate, later gate. The amd64 host glibc and the host tools (`cmake`, `make`,
`python3`, `bash`, `tar`, `dpkg-deb`, `readelf`, `file`) stay unpinned host
dependencies, and no host independence is claimed. Every locked archive carries
a `data.tar.zst` member, so the materializer also needs **one** zstd decoder:
the Python >= 3.14 stdlib `compression.zstd`, the `zstandard` module, or the
`zstd` command-line tool on PATH. That decoder is a declared host dependency
(the lock records the three alternatives under
`host_requirements.zstd_backend`), not a pinned archive, so the pinned
compiler/binutils/sysroot identities above are unchanged and no host
independence is claimed.

### Patch inventory

The Sendspin closure carries **two ordered** reviewed patches, owned by
LibreEcho-Platform and applied in list order (0001 then 0002) to the pinned SDK
archive before the SDK tree is staged. They are declared identically in the
Platform source lock (`tools/mt8163-arm32/sendspin/SOURCE.lock`,
`patch_inventory.applied`), in this contract, and in the Product inventory
(`build/inputs/public-inputs.json`, the mirrored top-level `patch_inventory`
block). The three must never diverge: they carry the same files, SHA-256
digests, targets, pristine-archive anchors, and order. `0002` is listed after
`0001`, so it diffs the already-`0001`-patched tree.

| Order | Patch | SHA-256 | Target | Pristine archive anchor |
| --- | --- | --- | --- | --- |
| 1 | `0001-stream-end-reason.patch` | `d9273acc1e014ddc1550b5791a41d8ad360b9639b9f8059c3c6cb4f38ebe7090` | `identity::sdk` (`sendspin-sdk`) | `2749ee07e4974f3879297af12d651349df33f46125a100cc935bf6966ed8af45` |
| 2 | `0002-stream-clear-boundary.patch` | `b01f3088e52e779070de4f1ef5191aad1c1e9501e320fb25746b1e244c59007b` | `identity::sdk` (`sendspin-sdk`) | `2749ee07e4974f3879297af12d651349df33f46125a100cc935bf6966ed8af45` |

The patch bytes are owned by the **Platform** repository
(`tools/mt8163-arm32/sendspin/patches/`), not the Product repository. **Every**
Product staging route that declares these patches consumes them only through an
**explicit**, authenticated Platform-owned patch directory and lock —
`fetch-public-deps.py [--feature sendspin] --patch-dir <dir> --source-lock <lock>`
(or the `patch_dir=`/`lock=` arguments of `stage_sendspin`/`stage`); there is no
implicit default, no owner `None` back-compat, and no arbitrary checkout is
consulted. A whole-inventory run (the release workflow's `--output` route with no
`--feature`) fails closed *before* creating its output path when the inventory
declares patches but the lock or patch directory is absent, so it can never stage
a pristine tree under an inventory that declares the patch applied.

Before any source is materialized the lane: validates that the Product mirror and
the Platform lock agree on the exact Platform `owner`; enumerates the patch
directory **closed** (every entry — including dotfiles, subdirectories and
symlinks — must be one of the declared bare `*.patch` regular files); reads each
patch's bytes **once** through a no-follow descriptor and checks them against the
declared digest; requires each target to map to a staged source and to match that
source's pinned archive anchor; and requires every diff header to stay inside the
target tree. The **frozen, digest-verified bytes** — never a re-opened mutable
path — are then applied, in declared order and exactly once, to a **freshly
archive-derived** tree, from the same buffer the receipt records, so a concurrent
swap in the patch directory after validation cannot change what is applied. The
assembled tree (parent plus every submodule) is then confined-checked before
publication: a digest-pinned patch that smuggles an escaping symlink body (a git
`new file mode 120000` diff) or a setuid/setgid/special entry is refused. The
stage receipt's tree content digests are computed **after** every patch (and
submodule) lands, and repeated staging is idempotent.

Any future patch — or any change to these — must move the lock, this contract,
and the Product inventory mirror together in one coordinated change, and patch
ownership stays with LibreEcho-Platform. A tree that is not the archive plus
exactly this ordered patch list is refused; the pinned archive `2749ee07…` and
its SHA-256 are unchanged.

Staged identity is the mode-aware tree digest the Platform verifier computes.
The patched SDK tree digest is
`b2aa25866dd445f6e2756e7e490986382a9639871936b7eafde6a9dc1a4acb80` (184 files),
and the pinned archive `2749ee07…` is unchanged. Both the Product materializer
and the Platform verifier extract with `tarfile` `filter="data"`, which keeps
the stored member permissions (it clears only setuid/setgid/sticky and
group/other write bits) and applies them with an explicit `chmod`, so this
digest is **umask-independent**: stored executable bits survive extraction
instead of being normalised away (a `0755` member stays `0755`), and unlike a
`tar` CLI extraction, no member mode is masked by the process umask — after
which the mode-aware digest would be spuriously different.

## 3. Ownership boundary

| Owner | Responsibility |
| --- | --- |
| LibreEcho-UI | Native **C++20 companion** that links the staged SDK, owns the client session, pairing/identity persistence, and the `LE_AUDIO_SINK/1` **sink client**. |
| LibreEcho-Platform | The **C99 shared audio engine** remains the **sole ALSA owner**, and the `LE_AUDIO_SINK/1` **sink worker** that feeds it. Platform also owns the source lock `tools/mt8163-arm32/sendspin/SOURCE.lock`. |
| LibreEcho (product) | This contract, the pinned input inventory, and cross-repository coordination. |

The SDK **owns** decode, buffering, scheduling, and drift correction. LibreEcho
runs **no second servo**: the shared engine is a passive, clock-exact sink. If
the engine ran its own resampling/drift control it would fight the SDK's
corrections and both would drift. The engine reports true DAC-finish time and
does nothing else to the timeline.

## 4. `LE_AUDIO_SINK/1` sink contract

The C++ companion and the C99 engine do not share address space. They exchange a
versioned, bounded, explicitly-serialized protocol over a Unix domain socket.

### 4.1 Transport

- **Endpoint:** `/run/libreecho-audio/sendspin.sock`.
- **Type:** bounded Unix `SOCK_SEQPACKET` — message boundaries preserved, no
  stream reassembly, backpressure inherent. Datagram size bounds the frame.
- The sink worker (Platform) defines the canonical header layout. Both sides
  parse the header explicitly; **no C structs are cast onto the wire, and no
  compiler padding or host endianness leaks**. Every integer is serialized
  **little endian** by explicit byte packing.

### 4.2 Message types

- `DATA` — one PCM period of interleaved S16 stereo frames. **Maximum one audio
  period per DATA message.**
- `PROGRESS` — playback progress feedback from the engine.
- `FINISH` — the stream reached physical drain.
- `RESET` — fence and discard an unsent generation.

### 4.3 Counters (all distinct, all 64-bit)

Every message carries four independent unsigned 64-bit counters. They are
**distinct quantities** and must never be conflated:

1. **epoch** — the session incarnation; bumped on re-handshake or device reset.
2. **generation** — the logical stream within an epoch; bumped on `RESET`.
3. **seq** — the per-message sequence number within a generation.
4. **cumulative counters** — three separate running totals, each 64-bit and
   monotonic within a generation:
   - **accepted** — frames admitted by the engine,
   - **submitted** — frames handed to ALSA,
   - **played** — frames confirmed finished at the DAC.

### 4.4 Buffer capacity and framing

- Engine-side capacity is **two 2048-frame periods**.
- `DATA` carries **at most one period** (2048 frames), so the producer can never
  overrun a two-period ring in a single message.
- The SDK write callback returns the number of **acknowledged bytes**, and that
  value **must be a multiple of 4** (one S16 stereo frame), so the caller never
  advances the stream by a partial frame.

### 4.5 `PROGRESS` semantics

`PROGRESS` reports an **exactly-once delta** since the previous `PROGRESS` and a
**future monotonic DAC-finish estimate**. The finish estimate is the projected
local timestamp at which the already-submitted frames *will* have physically
played — it is **not** the enqueue time, and it never moves backwards within a
generation. Reporting enqueue time (or a non-monotonic estimate) would corrupt
the SDK's sync math and is a contract violation.

### 4.6 `FINISH` and `RESET`

- `FINISH` is emitted only after **physical drain** of the generation, not when
  the last message is queued.
- `RESET` fences the **unsent** generation — it discards frames that have not yet
  been submitted, and does **not** flush already-submitted/played PCM. The engine
  completes what it has accepted to the DAC boundary, then binds to the new
  generation.

## 5. Feature packaging

- The existing **AirPlay2 support is a physical feature and is unchanged**.
- Sendspin is a **new, independent logical feature**, **default-off**. Enabling it
  does not alter, replace, or gate AirPlay2, and disabling it leaves AirPlay2
  untouched. The two share the C99 engine as the sole ALSA owner but have no
  ordering dependency.

## 6. Format freeze

- **First deliverable: PCM only** — 48 kHz, stereo, signed 16-bit little endian.
- **FLAC later**, once PCM sync is proven; the micro-flac closure is already
  pinned for it.
- **Opus and every unused role are off** (`SENDSPIN_ENABLE_OPUS=OFF`; controller,
  metadata, color, artwork, and visualizer roles excluded). This keeps the
  closure minimal (a policy exclusion; no licensing conclusion is asserted).

## 7. Security posture

- Production requires the **strict encrypted RC1** handshake and pairing. There
  is **no fallback** to legacy `client/hello` and no "allow non-compliant clients"
  path in the integration.
- Identity/pairing material is generated device-local; no key or pairing secret
  is ever committed or uploaded (the device-local runtime import contract stays
  in force).

## 8. Validation ladder (not yet performed)

This document is a contract, not evidence. Ordered gates, each producing its own
evidence before the next:

1. Product: `test_sendspin_inputs.py` — pin/identity/closure contract (host-only).
2. Platform: `SOURCE.lock` digest agreement and offline staging digest check.
3. UI: host C++20 build of the companion against staged sources.
4. Cross-repo: session wiring against the aiosendspin oracle, host-only.
5. Image + hardware: deferred; requires explicit authorization.

**No production-ready claim is made here.** Nothing above has been built,
flashed, or validated on hardware.
