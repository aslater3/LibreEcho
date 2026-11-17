# Porting LibreEcho to another Echo model

This guide is for contributors who want LibreEcho to run on an Echo model it
does not support yet. It explains what a *target* is, which files in which
repository define one, how the device tree, audio, lights and buttons are
mapped, and what has to pass before a new model counts as supported. The Echo
Dot 2nd Gen (`biscuit`) is the worked example throughout: it was the second
target, added next to the Echo 2nd Gen (`radar_puffin`).

Read the [hardware roadmap](../../README.md#hardware-roadmap) first to see
where your model stands. Being able to unlock a model is only the start.

## Scope: which models this guide covers

Today every target is an **MT8163, ARM32** board. The Product target schema
pins `soc: mt8163`, `arch: arm32`, one kernel defconfig and one tools profile,
so this guide covers models on the same chip: the Echo Plus 1st Gen (`sonar`),
Echo Spot 1st Gen (`rook`), Echo Show 5 1st Gen (`checkers`) and similar.

A model on another SoC (MT8516 for the Echo Dot 3rd Gen, Echo 3rd Gen, Echo
Plus 2nd Gen, Echo Studio) needs a kernel platform port, a new tools profile and
a schema change before any of the steps below apply. Open a product issue to
plan that first.

## Safety first

Porting means experimenting with a device's boot chain. Before you write
anything to flash, read the boot-chain rules in [`AGENTS.md`](../../AGENTS.md#boot-chain-safety):

- **Never write `FASTBOOT_PLEASE` to `expdb` and never erase `expdb`** on an
  amonet v2.0.0 (Kaeru) chain. That partition holds the LK-stage payload; the
  device bricks one boot later.
- Work out how to get back to fastboot on your model before you need it. On
  the supported models that is holding **MUTE** at power-on (LibreEcho-Platform
  issue #195 has the recovery details).
- Do kernel and userspace bring-up from a RAM boot or a test unit you can
  recover, not from a unit you rely on.

## Before you start: collect the hardware facts

Fill this sheet in for your model. Every later step uses it, and reviewers will
ask for it in the target PRs.

| Fact | Where to find it | `biscuit` (Echo Dot 2nd Gen) |
|---|---|---|
| Codename and target ID | Amazon build props, amonet project name | `biscuit` |
| Fastboot product | `fastboot getvar product` on the stock bootloader | `BISCUIT` |
| Unlock route | the community amonet project for the model | `amonet-biscuit-v2.0.0.zip` (Kaeru) |
| Reviewed LK builds | `fastboot getvar` build description after unlock | `63cb91b-20221007_072309` (+ default payload) |
| Stock userdata size | the model's stock GPT | its own size, pinned in the installer |
| Speaker | teardown, stock audio tuning files | one full-range driver |
| Speaker codec | stock device tree / `dmesg` | TLV320AIC32x4 at I²C bus 2, `0x18` |
| Microphone ADCs | stock device tree | four TLV320AIC3101 at bus 0, `0x18`-`0x1b` |
| Light ring | stock device tree | IS31FL3236 at bus 0, `0x3f`, 12 RGB pixels |
| Buttons and their codes | stock DTS, `/proc/bus/input/devices` | volume up/down, action, mute |
| Privacy (mute) latch | stock DTS `amz_privacy` node | present, kernel-owned |
| Sensors | stock DTS | TSL2584 and TSL2540 light sensors, three thermistors |

Get the stock device tree from the model's own boot image (decompile it with
`dtc -I dtb -O dts`). It is the reference for every address, GPIO and key code
in the next step.

## How a target is identified

One target ID runs through every repository. Each link below is checked by
tests, and an unknown ID fails closed at every stage:

```text
stock fastboot "product"  (BISCUIT)
  -> installer target         tools/libreecho-install.py, Installer-Web profiles.js
  -> Product descriptor       release/targets/biscuit.json
  -> kernel target table      LibreEcho-Linux libreecho/targets.json  (defconfig + DTB)
  -> Platform target table    LibreEcho-Platform libreecho_platform_targets.py
  -> image identity files     /etc/libreecho/target, /etc/libreecho/audio-profile
  -> runtime                  updater, recovery installer, ping, audio engine
```

The image identity files on a running Echo Dot look like this:

```text
# /etc/libreecho/target
target_id=biscuit
release_slug=biscuit
hw_profile=biscuit@0
descriptor_sha256=<SHA-256 of the canonical Product descriptor>

# /etc/libreecho/audio-profile
schema=1
target_id=biscuit
speaker_chain=biscuit
codec_profile=Flat
hp_driver_gain=6
pre_gain_db=6.00
bass_makeup_db=2.50
```

Code that has to behave differently per model reads these files. Never pick a
target from a device serial prefix, a hostname or a guess from the hardware.

## Step 1: choose the identifiers

| Identifier | Rule | `biscuit` |
|---|---|---|
| `target_id` | lowercase, `[a-z0-9_]`; the Amazon codename | `biscuit` |
| `release_slug` | the target ID with `_` replaced by `-`; names release assets (`libreecho-<slug>-…`) | `biscuit` |
| `hw_profile` | `<target_id>@<revision>`; bump the revision for an incompatible board revision | `biscuit@0` |
| `fastboot_products` | the exact stock `product` strings | `["BISCUIT"]` |
| `display_name` | the retail name | `Amazon Echo Dot (2nd Gen)` |

## Step 2: kernel and device tree (LibreEcho-Linux)

The kernel lives in [LibreEcho-Linux](https://github.com/aslater3/LibreEcho-Linux).
Its target table is `libreecho/targets.json`, which maps each target to a
defconfig and a DTB:

```json
"biscuit": {
  "defconfig": "mt8163_arm32_defconfig",
  "dtb": "libreecho-radar-puffin.dtb"
}
```

`libreecho/validate_targets.py` holds the list of target IDs and checks that
both files exist. It builds one CI lane for each distinct defconfig and DTB
pair, so a target that shares the Radar pair costs no extra kernel build.

**Reuse the Radar DTB if you can.** The Echo Dot 2nd Gen runs the Radar device
tree (`arch/arm/boot/dts/libreecho-radar-puffin.dts`) unchanged: the boards
share the SoC, PMIC, codecs, LED driver and button wiring. Its speaker
differences are runtime settings (Step 4), not device tree changes. Compare
your model's stock device tree with the Radar DTS node by node. If every node
LibreEcho uses matches, reuse the Radar DTB and add only the table entry.

**If something differs, add a new DTS** (for example
`arch/arm/boot/dts/libreecho-<target>.dts`), point the table entry at it and
add its contract tests. These are the nodes that matter, with the Radar values
as a reference:

| Function | Radar DTS node | What to check against stock |
|---|---|---|
| Mute button | `mt6323keys/power`, `linux,keycodes = <0x71>` (`KEY_MUTE`) | The PMIC key is the mute button on Radar, not power. A wrong code makes the button look dead. |
| Action button | `action_key/action@36`, GPIO 36, `KEY_HELP` (`0x8a`) | The vendor puts it on the keypad matrix, which has no driver here, so it needs its own `gpio-keys` node. |
| Volume buttons | `keys/volumeup@115`, `keys/volumedn@114`, `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` | GPIO numbers and active level. |
| Privacy latch | `amz_privacy`, `gpios` and `bright-state-gpio` | The latch GPIO and the mute-lamp GPIO. |
| Light ring | `is31fl3236@3F`, `channel-offset`, `play-boot-animation` | Driver chip, address, and which channel is pixel 0. |
| Speaker codec | `tlv320aic32x4@18` on I²C bus 2, `puffin_codec_mclk` | Address, MCLK rate (9.6 MHz on Radar), reset and amplifier GPIOs. |
| Microphone ADCs | `tlv320aic3101@18` to `@1b`, `enable-gpio` on the first | Count and addresses; this sets the capture lane count. |
| Audio front end | `mt_soc_*` PCM nodes, `audiosys`, AFE pinctrl states | The pinctrl state names the Platform DTB verifier requires. |
| Light sensors, thermistors | `tsl2584tsv@29`, `tsl2540@39`, `thermistor@0..2` | Which are populated. |

Find buttons by counting, not by correlation: declare every candidate pin with
its own key code, press one button a known number of times and see which input
reports that many events. The Radar DTS comments record how each key was
identified; copy that habit into yours.

The Radar machine driver is `sound/soc/mediatek/mt8163/mt8163-radar-puffin.c`.
It exposes the `Speaker Codec Profile` control (`Radar` crossover or `Flat`)
that the audio engine selects per target. A model with a different amplifier
or codec path needs its own machine driver work here.

Kernel host checks:

```sh
python3 -B libreecho/test_targets.py
python3 -B libreecho/validate_targets.py --matrix
python3 -B libreecho/run_contract_tests.py
```

## Step 3: Platform target (LibreEcho-Platform)

The Platform target table is `TARGETS` in
`tools/mt8163-arm32/libreecho_platform_targets.py`. One entry per model:

```python
'biscuit': {
    'release_slug': 'biscuit', 'hw_profile': 'biscuit@0',
    'fastboot_products': ('BISCUIT',), 'dtb_verifier': 'radar_puffin',
    'audio': {'speaker_chain': 'biscuit', 'codec_profile': 'Flat',
              'hp_driver_gain': 6, 'pre_gain_db': 6.0, 'bass_makeup_db': 2.5},
},
```

The image build (`build_recovery_image.py --target`) renders this entry into
`/etc/libreecho/target` and `/etc/libreecho/audio-profile`, and the image
verifier (`verify_recovery_image.py`) requires both to match the table byte for
byte.

**DTB verifier.** `dtb_verifier` names a fail-closed check of the built DTB in
`verify_target_dtb.py` (`DTB_VERIFIERS`). `radar_puffin` runs
`verify_radar_puffin_dtb.py`, which pins the clocks, pinmux, key codes and the
amplifier and DAC-mux control path that a working image depends on. A target
that reuses the Radar DTB reuses this verifier. A target with its own DTB needs
its own verifier.

**Shell code that names targets.** The initramfs and installers are BusyBox
shell. They match target IDs explicitly so that an unknown ID fails closed. Add
your target to each of these:

| File (under `tools/mt8163-arm32/`) | What it maps |
|---|---|
| `initramfs/libreecho-init` | target ID to release slug at boot |
| `initramfs/libreecho-update` | target ID to release slug for OTA |
| `initramfs/libreecho-update-fetch` | release tag pattern and target ID to slug |
| `initramfs/libreecho-target-manifest` | target ID to slug for the signed target grammar |
| `initramfs/libreecho-ping` | target ID to the `hw` value in the weekly ping (see [privacy](../privacy.md)) |
| `recovery-install/src/META-INF/com/google/android/update-binary` | target ID to recovery product, and the bundle manifest name |
| `recovery-install/src/libreecho-direct-install.sh` | target ID to fastboot product, and the OTA v3 slug |

Search for the existing IDs (`git grep -n -w biscuit`) to find any place this
list has missed.

Platform host checks, from `tools/mt8163-arm32/`:

```sh
python3 -B -m unittest test_multi_target test_recovery_image_tools
```

`test_multi_target` covers the table, the identity files, the per-target audio
profile and the target checks in the shell scripts. One of its tests needs
`dtc` installed.

## Step 4: audio, lights and buttons at run time

### Speaker

The audio engine (`tools/mt8163-arm32/airplay/audio_engine.c` in Platform)
reads `/etc/libreecho/audio-profile` once at start-up and applies:

| Key | Meaning | Range |
|---|---|---|
| `speaker_chain` | DSP chain: `radar_puffin` (two-way with crossover) or `biscuit` (one driver) | `AUDIO_CHAINS` |
| `codec_profile` | kernel `Speaker Codec Profile`: `Radar` or `Flat` | `CODEC_PROFILES` |
| `hp_driver_gain` | codec `HP Driver Gain Volume` index | 0..35 |
| `pre_gain_db` | digital gain before the chain (`biscuit` chain only) | 0..12 dB |
| `bass_makeup_db` | makeup gain on the bass bands (`biscuit` chain only) | 0..6 dB |

If your speaker resembles one of the two existing chains, a table entry is all
you need. Tune it by listening on the real device and by comparing with stock.
The Echo Dot values came from the owner listening on hardware.

If it does not, add a chain. The Echo Dot chain is
`airplay/biscuit_speaker_dsp.h`: a 16-section loudness EQ, the stock volume
ladder, four-band dynamics and a look-ahead limiter, all fitted to measured
stock behaviour. No vendor coefficient files are shipped; fit your own from
measurements. A new chain also needs its output latency measured and added
next to `BISCUIT_OUTPUT_LATENCY_US`, because synchronised playback (AirPlay,
Sendspin) depends on it. Add its name to `AUDIO_CHAINS` and give it a test like
`test_biscuit_speaker_dsp.sh`. Changes must leave Radar output bit-identical;
`test_radar_speaker_equivalence.py` checks this.

### Microphones

The capture path reads every lane of the TLV320AIC3101 ADCs. The array
geometry the beamformer uses is in LibreEcho-UI
`src/adapter/mic_array_geometry.h`. It was measured on a Radar: six ring
microphones plus a centre one, lane order, angles and a 36 mm ring radius. It is
**not yet per target**. A model with a different array needs its own
measurement (scratch each port in turn and record which lane lights) and a
per-target geometry. Open an issue in LibreEcho-UI before you start that work.

### Light ring

`ledd` (LibreEcho-UI `src/adapter/ledd.c`) picks a backend at start-up, in
this order:

1. **sysfs LEDs**: a `/sys/class/leds/*/multi_intensity` node, or separate
   `red`, `green` and `blue` LEDs.
2. **IS31FL3236**: an I²C device under `/sys/bus/i2c/devices/` with `frame` and
   `boot_animation` files (and optionally `scaled_frame`). This is the Radar
   and Echo Dot ring.
3. **I²C stub**: an LED-like address answers but no driver is known.
4. **memory stub**: no LED hardware.

The logical frame is 12 RGB pixels (`LE_LED_PIXELS`). The device tree
`channel-offset` rotates the frame so that pixel 0 lands on the right LED. A
ring with another pixel count or driver needs changes in `led_output.h` and
`ledd.c`. The calibration in `led_output.c` is a
defensive placeholder, not a measured current limit, so keep new boards inside
it until someone measures them.

On an Echo Dot the log shows the backend it chose:

```text
using IS31FL3236 LED backend (/sys/bus/i2c/devices/0-003f/frame, current-scaled levels)
LED boot-animation handover complete
```

### Buttons and mute

`buttond` (LibreEcho-UI `src/adapter/buttond.c`) opens every
`/dev/input/event*` that reports any of `KEY_VOLUMEUP`, `KEY_VOLUMEDOWN`,
`KEY_MUTE`, `KEY_MICMUTE`, `KEY_POWER` or `KEY_HELP`:

| Code | Action |
|---|---|
| `KEY_VOLUMEUP`, `KEY_VOLUMEDOWN` | volume, with a rising or falling cue |
| `KEY_HELP` | action button |
| `KEY_MUTE`, `KEY_MICMUTE`, `KEY_POWER` | microphone mute |

So the device tree only has to deliver the right codes; no per-target code is
needed. The mute latch and lamp belong to the kernel `amz_privacy` driver:
`buttond` reads `/sys/devices/platform/amz_privacy/privacy_state` and never
drives the latch itself, so an API unmute cannot override hardware privacy. On
an Echo Dot:

```text
$ grep -E '^(N|H):' /proc/bus/input/devices
N: Name="mtk-pmic-keys"
H: Handlers=amz_privacy event0
N: Name="action_key"
H: Handlers=event1
N: Name="keys"
H: Handlers=event2
```

and `buttond` logs `watching /dev/input/eventN [...]` for all three.

### Web UI

The About page device table (`web/js/app.js`, `aboutPage`) lists models as
*Confirmed* or *Likely, untested*. `tests/test_about_supported_devices.py`
stops a model being marked Confirmed by accident. Move a model to Confirmed
only in the PR that records its hardware acceptance.

## Step 5: Product target and installers (this repository)

**Target descriptor.** Add `release/targets/<target_id>.json`. The Echo Dot's:

```json
{
  "schema": "libreecho-target-v1",
  "soc": "mt8163",
  "arch": "arm32",
  "kernel": {"defconfig": "mt8163_arm32_defconfig", "dtb": "libreecho-radar-puffin.dtb"},
  "layout_profiles": ["amonet", "pinned"],
  "features": ["airplay2", "assistant", "stt", "tts", "wakeword"],
  "hardware_accepted": true,
  "target_id": "biscuit",
  "release_slug": "biscuit",
  "display_name": "Amazon Echo Dot (2nd Gen)",
  "fastboot_products": ["BISCUIT"],
  "platform": {"tools_profile": "mt8163-arm32", "dtb_verifier": "radar_puffin", "hw_profile": "biscuit@0"}
}
```

Start a new target with **`"hardware_accepted": false`**. Build metadata copies
the flag, and the browser installer refuses to install a build whose metadata
says `false`. Only the maintainer flips it to `true`, after the acceptance
checklist below.

Then add the target to:

| File | What to add |
|---|---|
| `release/target.schema.json` | the new `target_id`, `release_slug`, fastboot product and `hw_profile` enums, plus an `allOf` identity block like the `biscuit` one |
| `build/ci/target_registry.py` | `KNOWN_TARGETS` and the expected `(slug, products, hw_profile)` identity |
| `build/ci/validate-targets.py`, `build/build.sh` | the default and usage target lists |
| `.github/workflows/build-release.yml`, `.github/workflows/publish-release.yml` | the `targets` input description and the target choice list |
| `release/amonet-pins.json` | the model's community amonet archive: name, size, SHA-256 and each reviewed LK build's payload |
| `tools/libreecho-install.py` | `AMONET_PINS` (kept equal to `amonet-pins.json` by tests), `USERDATA_TARGET_BYTES`, `ADB_DEVICE_PROP`, `TARGET_INDEX`; then regenerate `tools/libreecho-install.py.sha256` |
| `tools/run-one-shot.sh` | the board, recovery and fastboot product matches, and the slug |

The browser installer ([LibreEcho-Installer-Web](https://github.com/aslater3/LibreEcho-Installer-Web))
has its own product table in `js/profiles.js` and checks in `js/stages.js` and
`js/app.js`; it needs a matching PR.

Release names: Radar keeps the legacy asset names, and every other target's
assets are prefixed `libreecho-<slug>-`. See
[multi-target release scaffolding](../../release/MULTI-TARGET.md) for how one
combined release carries every target.

**A target's first release.** OTA v2 needs a published baseline for the **same**
target, and Product never reuses a Radar baseline for another model. A target
that has never been released therefore ships its first release as a fresh
install only: name it in `--bootstrap-targets` for
`build/ci/fetch-ota-bases.py`, which refuses Radar, unselected targets and any
target that already has a baseline. Its OTA updates start from the release
after that.

Product host checks:

```sh
python3 build/ci/validate-targets.py --targets radar_puffin,biscuit,<target_id>
python3 -B -m unittest tools.test_initial_installer_publication tools.test_fastbrick_gate
```

The full set of maintained Product suites is listed in the `Run maintained
Product pipeline suites` step of `build-release.yml`. They need PyNaCl and a
LibreEcho-Platform checkout in `LIBREECHO_PLATFORM_SRC`.

## Step 6: open the pull requests

A port is a cross-repository change. Open a product issue in this repository
for the model, then one PR per repository, all for the same release:

1. LibreEcho-Linux: `targets.json` entry, DTS if needed, contract tests.
2. LibreEcho-Platform: target table entry, shell target maps, DTB verifier if
   needed, audio chain if needed, tests.
3. LibreEcho-UI: only if the model needs microphone geometry, LED or button
   work, plus the About page once accepted.
4. LibreEcho (this repository): descriptor with `hardware_accepted: false`,
   schema, registry, installer pins and maps.
5. LibreEcho-Installer-Web: product table.

Each PR follows the rules in [`AGENTS.md`](../../AGENTS.md): Conventional
Commit title, changed files, exact test evidence with host and hardware results
kept apart, `Release impact:` and `Release note:`. Never put device serials, MAC
addresses or Wi-Fi details in a PR.

## Acceptance checklist

A model is supported when every item below has evidence recorded on the
product issue for the exact build tested. Until then its descriptor stays at
`hardware_accepted: false` and the About page lists it as untested.

**Host and CI**

- [ ] Kernel: `test_targets.py`, `validate_targets.py` and `run_contract_tests.py` pass; CI builds the target's kernel lane.
- [ ] Platform: `test_multi_target` and `test_recovery_image_tools` pass; the image verifier accepts the target's image, DTB and identity files.
- [ ] Product: `validate-targets.py` prints the new target and digest; the maintained suites pass.
- [ ] A combined build packages the new target next to the existing ones, and the existing targets' outputs are unchanged.

**Install**

- [ ] The one-shot installer identifies the stock fastboot product, refuses a mismatched `--target`, and verifies the pinned amonet archive and payload before any write.
- [ ] A fresh install from stock boots and confirms its slot (`libreecho-update status`).
- [ ] The recovery route (MUTE at power-on or the model's equivalent) is known and has been tried.

**On the device** (general health first, then each subsystem)

- [ ] `/etc/libreecho/target` and `/etc/libreecho/audio-profile` name the new target.
- [ ] Startup completes: the web control centre and API answer, and the required services (`web`, `watchdogd`, `networkd`, `audiod`, `logd`) are running.
- [ ] No `Oops`, `BUG:` or panic in `dmesg`.
- [ ] Speaker: playback at low, medium and full volume is clean, with no clipping or rattle; the engine applied the profile's chain and codec profile.
- [ ] Microphones: every capture lane carries signal; the wake word triggers from across the room; mute silences capture.
- [ ] Light ring: `ledd` chose a hardware backend, pixel 0 is where the UI expects it, colours are correct and brightness follows the setting.
- [ ] Buttons: each button produces its event and action; mute lights the mute lamp and the UI shows the privacy state.
- [ ] Wi-Fi, Bluetooth and the features in the descriptor work.
- [ ] Sensors that exist (light, temperature) report plausible values.

**Updates**

- [ ] After the first release, a signed OTA to a newer build of the **same** target installs, boots and confirms.
- [ ] A failed or unconfirmed update rolls back to the previous slot.

**Docs**

- [ ] The README supported-devices table, the hardware roadmap, the install
  guide and [Going back to stock Fire OS](../install/restore-stock.md) cover the
  model.

## Worked example: how `biscuit` was added

The Echo Dot 2nd Gen went in as a series of focused PRs. Its kernel PRs
landed in the older LibreEcho-Linux-6.1 repository, which holds the kernel
source for releases up to 0.14.4; their changes are carried in
[LibreEcho-Linux](https://github.com/aslater3/LibreEcho-Linux), where new
kernel work goes.

| Step | PR | What it did |
|---|---|---|
| Kernel target table | LibreEcho-Linux-6.1 #48 | `targets.json` with `biscuit` at parity: same defconfig and Radar DTB; CI matrix |
| Platform identity | LibreEcho-Platform #211 | target table, `/etc/libreecho/target`, target-aware OTA, per-target recovery bundle |
| Product descriptors | LibreEcho #200 | `release/targets/*.json`, schema, combined release with per-target TWRP installs |
| First release | LibreEcho #203 | fresh-install-only first release for a never-released target |
| Install geometry | LibreEcho #164, #231 | accept the Echo Dot's stock and amonet userdata sizes |
| One-shot install | LibreEcho #227 | one-shot fastbrick migration for both targets |
| Boot store | LibreEcho-Platform #233 | write the boot store the pinned Echo Dot chain actually boots |
| Codec profile | LibreEcho-Linux-6.1 #49 | selectable `Speaker Codec Profile` (`Radar` crossover or `Flat`) |
| Speaker tuning | LibreEcho-Platform #230 | per-target audio profile and the Echo Dot speaker chain, with Radar output proved bit-identical |

What the Echo Dot did **not** need is just as useful: no new DTS (the Radar
device tree drives its codecs, ring, buttons and privacy latch), no LED or
button code, and no web UI changes beyond the About page. Its differences were
the fastboot identity, the unlock archive, the userdata size, the boot store
the pinned chain boots, and the speaker. Expect a new MT8163 model to differ in
the same places first.
