# BROM Inactive-Slot LibreEcho Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate a BROM-native installer that first inventories and backs up the Echo with zero writes, then can install the signed LibreEcho development boot image into one inactive redirected slot and activate it with one verified BCB-sector write.

**Architecture:** A small Python package under `tools/brom-install/` owns pure release, GPT, BCB, inventory, and transaction logic. A thin live adapter imports only the hash-pinned Amonet transport primitives, while a shell launcher on nixtop creates the Nix environment, runs all preflight checks, and admits only USB BROM ID `0e8d:0003`. All device behavior is tested against an operation-logging fake before the launcher is copied to nixtop.

**Tech Stack:** Python 3 standard library, PyNaCl for Ed25519 verification, `unittest`, Bash, Nix/PySerial, existing Amonet K32 Python transport.

**Spec:** `docs/superpowers/specs/2026-08-17-brom-inactive-slot-install-design.md`

## Global Constraints

- The first live run is `--inventory-only`; it has no write-capable code path.
- Accept only `libreecho-radar-puffin-dev.ota.tar` with SHA-256 `5eb210077527449fc831b88bc0776022b72538a635c76720c9f01e781af58a3f`.
- Accept only embedded `boot.img` size 16777216 and SHA-256 `5ca302c958c1449a569db646b4b743ae5be5baaf8ec58a2eb86161ab1c286e15`.
- Require disk GUID `081BEED7-DF86-4A71-864E-435385BA18D9` and the exact 18-partition Amonet Biscuit layout.
- Install writes are limited to all 32768 sectors of one inactive `boot_a_x` or `boot_b_x` and one 512-byte `misc` sector.
- Never invoke Amonet `main()` or any full-install helper that writes GPT, wrappers, vendor boot-chain partitions, RPMB, userdata, or eMMC boot areas.
- An invalid or ambiguous Amazon `ABB` BCB stops installation before all writes; BCB migration is out of scope.
- Feature payloads, owner-local firmware, Wi-Fi, slot confirmation, and custom image builds are out of scope.
- Source files are committed in this repository; runnable copies and private backups live under `/home/lucaspick/Downloads` on nixtop and are never committed.

---

### Task 1: Package Skeleton and Operation-Logging Fake

**Files:**
- Create: `tools/brom-install/libreecho_brom/__init__.py`
- Create: `tools/brom-install/libreecho_brom/types.py`
- Create: `tools/brom-install/tests/__init__.py`
- Create: `tools/brom-install/tests/fakes.py`
- Test: `tools/brom-install/tests/test_fakes.py`

**Interfaces:**
- Produces: `Partition(name: str, start_lba: int, sectors: int)`, `Operation(kind: str, part: int | None, lba: int | None, size: int)`, and `FakeDevice` implementing `emmc_switch(part)`, `emmc_read(lba)`, `emmc_write(lba, data)`, and `reboot()`.
- `FakeDevice` consumes a mapping of `(emmc_part, lba) -> bytes` and records every operation in order.

- [ ] **Step 1: Write the failing fake-device tests**

```python
class FakeDeviceTests(unittest.TestCase):
    def test_reads_switches_writes_and_reboots_are_recorded(self):
        dev = FakeDevice({(0, 7): b"A" * 512})
        dev.emmc_switch(0)
        self.assertEqual(dev.emmc_read(7), b"A" * 512)
        dev.emmc_write(8, b"B" * 512)
        dev.reboot()
        self.assertEqual([op.kind for op in dev.operations],
                         ["switch", "read", "write", "reboot"])

    def test_write_requires_exactly_one_sector(self):
        with self.assertRaisesRegex(ValueError, "512"):
            FakeDevice({}).emmc_write(8, b"short")
```

- [ ] **Step 2: Run the tests and observe the expected import failure**

Run: `python3 -m unittest tools/brom-install/tests/test_fakes.py -v`

Expected: FAIL because `Partition`, `Operation`, and `FakeDevice` do not exist.

- [ ] **Step 3: Implement the minimal dataclasses and fake**

```python
@dataclass(frozen=True)
class Partition:
    name: str
    start_lba: int
    sectors: int

@dataclass(frozen=True)
class Operation:
    kind: str
    part: int | None = None
    lba: int | None = None
    size: int = 0
```

Implement `FakeDevice` with a mutable current partition, sector map, operation list, optional injected read/write failures, and exact 512-byte write enforcement.

- [ ] **Step 4: Run the fake tests**

Run: `python3 -m unittest tools/brom-install/tests/test_fakes.py -v`

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit the test infrastructure**

```bash
git add tools/brom-install/libreecho_brom tools/brom-install/tests
git commit -m "test: add BROM installer fake transport"
```

### Task 2: Signed Development OTA Verification

**Files:**
- Create: `tools/brom-install/libreecho_brom/release.py`
- Test: `tools/brom-install/tests/test_release.py`
- Create: `tools/brom-install/tests/fixtures/dev.manifest`

**Interfaces:**
- Produces: `VerifiedRelease(archive_path: Path, manifest: dict[str, str], boot_image: bytes, archive_sha256: str, boot_sha256: str)`.
- Produces: `verify_release(path: Path, public_key_path: Path) -> VerifiedRelease`.
- Consumes PyNaCl `VerifyKey.verify(message, signature)`; the nixtop Nix environment must provide `pynacl`.

- [ ] **Step 1: Add failing tests for every release gate**

Create deterministic temporary tar files in the tests. Derive a PyNaCl `SigningKey` from a fixed 32-byte test-only seed inside the test module, sign the fixture manifest, and pass its derived verify key through a temporary public-key file. Never store or reuse the real release signing key. Cover:

```python
def test_accepts_exact_signed_dev_release(self): ...
def test_rejects_wrong_archive_hash_before_extract(self): ...
def test_rejects_extra_reordered_or_missing_members(self): ...
def test_rejects_bad_signature(self): ...
def test_rejects_unknown_or_duplicate_manifest_keys(self): ...
def test_rejects_wrong_board_soc_arch_profile_policy_or_channel(self): ...
def test_rejects_wrong_boot_size_hash_or_android_magic(self): ...
def test_requires_boot_sha256_and_version(self): ...
def test_rejects_boot_sha256_not_matching_the_extracted_image(self): ...
def test_requires_hex_decoded_key_and_signature(self): ...
```

The happy-path test patches only `EXPECTED_ARCHIVE_SHA256` to the fixture archive hash; every signed manifest field and embedded-image check remains real.

The last three exist because the first version of this contract would have rejected the real OTA. `test_requires_boot_sha256_and_version` must use a manifest whose key set matches production exactly, so that a future tightening of the allowlist fails here rather than on a device.

**Also add a positive fixture taken from the published bundle**, not only from locally generated tars. Generate it with the existing production bundle tooling, or copy the `manifest` and `manifest.sig` members out of a published `.ota.tar`, and assert that the parser accepts them and reproduces the field set. A synthetic fixture signed by a test key proves the code is self-consistent; only a production-derived one proves it agrees with what the release process actually emits.

This has been checked against the published bundle. `manifest` is 415 bytes with 13 keys -- the 11 fixed ones plus `version` and `boot_sha256`; `manifest.sig` is 128 hex characters plus a newline, and the public-key file 64 plus a newline. After stripping and `unhexlify`, `VerifyKey(key).verify(manifest_bytes, signature)` succeeds; passing the undecoded bytes raises `ValueError`.

- [ ] **Step 2: Run release tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_release.py -v`

Expected: FAIL because `verify_release` is missing.

- [ ] **Step 3: Implement strict verification**

Use streaming SHA-256 before tar parsing, `tarfile.getmembers()` for exact ordered names, regular-file/type/size checks, strict ASCII `key=value` parsing with duplicate rejection, `VerifyKey`, `hmac.compare_digest`, and exact manifest allowlists. Never call `extractall()`.

**Decode the key and signature before verifying.** Both are stored hex-encoded. In the published bundle the public-key file is 64 hex characters and `manifest.sig` is 128, each with a trailing newline, decoding to the 32-byte key and 64-byte signature Ed25519 requires. Strip whitespace and `binascii.unhexlify` both before they reach PyNaCl, and verify over the exact manifest bytes as read from the archive. Passing the undecoded bytes raises `ValueError` rather than returning a verification failure, so this is not a silent weakening -- but it does mean an implementation that skips it has never run against a real bundle.

**Separate fixed fields from per-release fields.** Comparing every field against a constant is what makes a strict allowlist reject a genuine release: the production manifest also carries `boot_sha256` and `version`, and unknown-key rejection would refuse it.

```python
EXPECTED_MEMBERS = ("manifest", "manifest.sig", "boot.img")

# Fixed: compared against these exact values.
EXPECTED_MANIFEST = {
    "format": "libreecho-ota-v1",
    "manifest_version": "1",
    "board": "radar_puffin",
    "soc": "mt8163",
    "architecture": "armv7",
    "boot_filename": "boot.img",
    "boot_size": "16777216",
    "feature_policy": "community-noncommercial",
    "image_profile": "ota",
    "service_profile": "production",
    "update_channel": "dev",
}

# Per-release: required to be present and well-formed; the value is a
# property of the build, not a constant.
REQUIRED_RELEASE_FIELDS = {
    "version": re.compile(r"\A[A-Za-z0-9._+~-]+\Z"),
    "boot_sha256": re.compile(r"\A[0-9a-f]{64}\Z"),
}
```

A manifest is accepted only when its key set is exactly `EXPECTED_MANIFEST | REQUIRED_RELEASE_FIELDS`: no unknown keys, and no missing ones. `boot_sha256` is then compared against the SHA-256 of the extracted image with `hmac.compare_digest`, so the manifest must agree with the artifact it describes.

- [ ] **Step 4: Run release tests and verify all pass**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_release.py -v`

Expected: PASS.

- [ ] **Step 5: Verify the real downloaded release**

Run:

```bash
PYTHONPATH=tools/brom-install python3 -c '
from pathlib import Path
from libreecho_brom.release import verify_release
r=verify_release(Path("/private/tmp/libreecho-release-inspect/libreecho-radar-puffin-dev.ota.tar"), Path("/private/tmp/libreecho-release-inspect/libreecho-radar-puffin-v0.1.0-ota-public-key.hex"))
print(r.archive_sha256, r.boot_sha256, len(r.boot_image))'
```

Expected: the two globally pinned hashes and `16777216`.

This step is the one that catches an allowlist that has drifted from the release process: it runs the parser against the real published archive, so a missing per-release field fails here, offline, rather than in front of a device in BROM mode.

- [ ] **Step 6: Commit release verification**

```bash
git add tools/brom-install/libreecho_brom/release.py tools/brom-install/tests
git commit -m "feat: verify signed LibreEcho development OTA"
```

### Task 3: GPT and Amazon BCB Contracts

**Files:**
- Create: `tools/brom-install/libreecho_brom/layout.py`
- Create: `tools/brom-install/libreecho_brom/bcb.py`
- Test: `tools/brom-install/tests/test_layout.py`
- Test: `tools/brom-install/tests/test_bcb.py`

**Interfaces:**
- Produces: `parse_gpt(header_sector: bytes, entry_array: bytes) -> RawGpt`, operating on raw bytes and performing no name-keyed lookup.
- Produces: `validate_layout(primary: RawGpt, backup: RawGpt, expected_guid: str) -> dict[str, Partition]`.

The mapping is an **output**, never an input. Accepting `dict[str, tuple[int, int]]` meant duplicate partition names had already been collapsed by whoever built the dict, so the duplicate-name test this task calls for could not be written, and a disk carrying two `boot_a_x` entries would have validated. Names are checked for uniqueness on the raw entry array, and the mapping is constructed only once that check has passed.
- Produces: `BootControl(slot_a: SlotMetadata, slot_b: SlotMetadata)` and `SlotMetadata(priority: int, tries: int, successful: bool)`.
- Produces: `decode_bcb(record: bytes) -> BootControl`, `selected_slot(control: BootControl) -> str`, and `activate_inactive(record: bytes, target: str) -> bytes`.
- BCB functions operate on exactly seven bytes; sector preservation belongs to the transaction task.

- [ ] **Step 1: Write failing exact-layout tests**

Test this exact captured 18-entry layout (start LBA, sector count):

```python
VALID = {
    "kb": (2048, 2048),
    "dkb": (4096, 2048),
    "lk_a": (32768, 2048),
    "tee1": (49152, 10240),
    "lk_b": (65536, 2048),
    "tee2": (81920, 10240),
    "expdb": (98304, 20480),
    "misc": (118784, 1025),
    "persist": (131072, 32768),
    "boot_a_x": (163840, 32768),
    "boot_b_x": (196608, 32768),
    "recovery": (229376, 32768),
    "system_a": (294912, 1572864),
    "system_b": (1867776, 1572864),
    "cache": (3440640, 1605632),
    "userdata": (5046272, 2137088),
    "boot_a": (7183360, 225280),
    "boot_b": (7408640, 225280),
}
```

Add one test each for wrong GUID, missing/extra/duplicate names, shifted start, resized partition, and renamed wrapper.

```python
with self.assertRaisesRegex(LayoutError, "boot_b_x"):
    validate_layout({**VALID, "boot_b_x": (196609, 32768)}, EXPECTED_GUID)
```

- [ ] **Step 2: Run layout tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_layout.py -v`

Expected: FAIL because `validate_layout` is missing.

Duplicate names, duplicate unique GUIDs and overlapping ranges must be expressed as raw entry arrays, since none of them can be represented in a name-keyed mapping:

```python
def test_rejects_duplicate_partition_names(self): ...
def test_rejects_duplicate_unique_guids(self): ...
def test_rejects_overlapping_partition_ranges(self): ...
def test_rejects_entries_outside_the_usable_lba_range(self): ...
def test_rejects_bad_header_crc(self): ...
def test_rejects_bad_entry_array_crc(self): ...
def test_rejects_non_reciprocal_my_lba_and_alternate_lba(self): ...
def test_rejects_primary_and_backup_disagreeing(self): ...
```

- [ ] **Step 3: Implement exact immutable layout validation**

Define `EXPECTED_LAYOUT` with all 18 names/start/size tuples from the captured GPT, not only the writable partitions. Compare sets before values and return frozen `Partition` values.

- [ ] **Step 4: Run layout tests and verify pass**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_layout.py -v`

Expected: PASS.

- [ ] **Step 5: Write failing BCB tests**

Cover valid `b"\x00ABB\x01\x8f\x8e"`, malformed `BCb`, wrong version, no bootable slots, tied priorities, activation of current slot, and exact target encoding.

```python
record = b"\x00ABB\x01\x8f\x8e"
self.assertEqual(selected_slot(decode_bcb(record)), "a")
self.assertEqual(activate_inactive(record, "b"), b"\x00ABB\x01\x8e\x3f")
```

- [ ] **Step 6: Run BCB tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_bcb.py -v`

Expected: FAIL because BCB functions are missing.

- [ ] **Step 7: Implement BCB decoding and activation**

Mirror the public Platform contract exactly: low nibble priority, bits 4–6 tries, bit 7 success. Reject invalid magic/version, ties, no selected slot, and activation of the selected slot.

- [ ] **Step 8: Run both contract suites**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_layout.py tools/brom-install/tests/test_bcb.py -v`

Expected: PASS.

- [ ] **Step 9: Commit GPT and BCB contracts**

```bash
git add tools/brom-install/libreecho_brom tools/brom-install/tests
git commit -m "feat: validate Biscuit GPT and Amazon BCB"
```

### Task 4: Zero-Write Inventory and Backup Manifest

**Files:**
- Create: `tools/brom-install/libreecho_brom/inventory.py`
- Create: `tools/brom-install/libreecho_brom/report.py`
- Test: `tools/brom-install/tests/test_inventory.py`
- Test: `tools/brom-install/tests/test_report.py`

**Interfaces:**
- Produces: `InventoryResult(report_path: Path, backup_dir: Path, report: dict)`.
- Produces: `run_inventory(dev, parsed_gpt, disk_guid: str, backup_dir: Path) -> InventoryResult`.
- Consumes a device with `emmc_switch(0)` and `emmc_read(lba) -> bytes` only; the function receives parsed GPT data and never imports a write method.
- Produces report schema `libreecho-brom-inventory-v1` with tool hashes, geometry, BCB bytes/decoded status, file paths/sizes/hashes, boot headers, and wrapper boundary evidence.

- [ ] **Step 1: Write failing inventory tests**

Build a complete fake sector map. Assert:

```python
result = run_inventory(ReadOnlyDevice(fake), VALID_GPT, EXPECTED_GUID, tempdir)
self.assertFalse(any(op.kind == "write" for op in fake.operations))
self.assertEqual(result.report["schema"], "libreecho-brom-inventory-v1")
self.assertEqual(result.report["bcb"]["raw_hex"], expected.hex())
```

Also inject failures during GPT, `misc`, slot, wrapper, and `expdb` reads and assert no success report is committed. Verify complete `misc`, both 16 MiB slots, GPT sectors, expdb sector, wrapper boundary files, and streamed wrapper hashes.

- [ ] **Step 2: Run inventory tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_inventory.py -v`

Expected: FAIL because `run_inventory` is missing.

- [ ] **Step 3: Implement a read-only device facade and streaming backup writer**

The facade exposes only `switch_user()` and `read_sector(lba)`. Write each backup to `*.partial`, fsync, close, hash by reopening, then atomically rename. Never retain calibration, serial, MAC, or filesystem content outside the explicitly listed partitions.

- [ ] **Step 4: Implement canonical report creation and validation**

Write sorted, indented JSON to `inventory.json.partial`, fsync, rename, then re-open and validate every backup hash. Also write `inventory.txt` containing only redacted geometry and hashes.

- [ ] **Step 5: Run inventory and report tests**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_inventory.py tools/brom-install/tests/test_report.py -v`

Expected: PASS, with explicit zero-write assertion.

- [ ] **Step 6: Commit inventory implementation**

```bash
git add tools/brom-install/libreecho_brom tools/brom-install/tests
git commit -m "feat: add zero-write BROM inventory backups"
```

### Task 5: Allowlisted Inactive-Slot Transaction

**Files:**
- Create: `tools/brom-install/libreecho_brom/install.py`
- Test: `tools/brom-install/tests/test_install.py`

**Interfaces:**
- Produces: `InstallPlan(target_slot: str, target_partition: Partition, expected_boot_sha256: str, original_misc_sector: bytes, updated_misc_sector: bytes)`.
- Produces: `plan_install(mode: InstallMode, inventory: dict, release: VerifiedRelease, live_gpt, live_guid, live_wrapper_evidence) -> InstallPlan`.
- Produces: `InstallMode` with exactly two members, `KNOWN_GOOD_UPDATE` and `BROM_RECOVERY`. The mode is passed explicitly by the operator and recorded in the report; it is never inferred from device state.

`KNOWN_GOOD_UPDATE` requires the currently selected slot to read `successful=1` live. `BROM_RECOVERY` requires that it does not, and refuses to run if it does, so a device that has a genuine rollback target cannot be updated by the mode that does not preserve one. Neither mode ever *sets* the preserved slot's `successful` bit: it is carried through exactly as read.
- Produces: `execute_install(dev, plan: InstallPlan, boot_image: bytes, result_dir: Path) -> InstallOutcome`.
- Produces: `InstallOutcome(attempted: list[int], acknowledged: list[int], verified: list[int])`, three separate sector lists rather than a boolean.
- Produces: `reconcile(dev, plan: InstallPlan) -> ReconciliationReport`, a read-only path constructed with no writer at all.
- `execute_install` accepts an `AllowlistedWriter` constructed with exactly one target LBA range and one misc LBA.

The transport sends a complete sector before waiting for its acknowledgement, so a lost or timed-out ACK is indistinguishable at the host from a sector that was committed and never confirmed. `attempted` minus `acknowledged` is therefore **indeterminate**, not failed, and must never be reported as either success or failure. The writer must not retry automatically: a retry after a lost ACK can rewrite a sector the device already holds, and on a desynchronised transport it may not land where the first one did. Recovery from an indeterminate outcome is a reconnect followed by `reconcile()`, which re-reads the affected range and reports which state each sector actually reached.

- [ ] **Step 1: Write failing planning tests**

Cover backup hash mismatch, live GPT mismatch, wrapper mismatch, malformed BCB, target ambiguity, release mismatch, and already-active target. Assert all failures occur before constructing a writer.

Also cover the indeterminate outcome, which the fake transport must be able to produce on demand:

```python
def test_lost_ack_is_reported_indeterminate_not_failed(self): ...
def test_indeterminate_outcome_performs_no_further_writes(self): ...
def test_indeterminate_outcome_is_never_retried_automatically(self): ...
def test_reconcile_has_no_writer_and_reports_per_sector_state(self): ...
def test_known_good_update_requires_current_slot_successful(self): ...
def test_brom_recovery_refuses_when_a_known_good_slot_exists(self): ...
def test_preserved_slot_successful_bit_is_carried_through_never_set(self): ...
```

The fake must model the real failure honestly: the sector reaches the device and only the acknowledgement is lost, so a test that simply drops the write would prove nothing.

- [ ] **Step 2: Run planning tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_install.py -v`

Expected: FAIL because planning functions are missing.

- [ ] **Step 3: Implement immutable plan generation**

Revalidate every backup file against inventory, compare live evidence, decode BCB, select the inactive slot, preserve all non-BCB bytes, and build the seven replacement bytes with `activate_inactive`.

- [ ] **Step 4: Write failing transaction tests**

Assert the exact operation sequence:

```text
switch user
32768 writes to target start..start+32767
32768 reads from target start..start+32767
read misc BCB sector
one write to misc_start+1
one read from misc_start+1
reboot
```

Inject a failure at the first/middle/last image write, first/middle/last image readback, stale misc read, misc write, and misc readback. Prove no BCB write occurs before complete image verification and no reboot occurs before complete BCB-sector verification.

- [ ] **Step 5: Implement `AllowlistedWriter` and transaction execution**

`AllowlistedWriter.write_sector` rejects every LBA outside the target slot or exact misc sector. It tracks target-sector uniqueness, enforces ascending contiguous image writes, permits the misc write only after `image_verified=True`, and permanently seals after the misc write.

- [ ] **Step 6: Run transaction tests**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_install.py -v`

Expected: PASS.

- [ ] **Step 7: Run the complete offline suite**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest discover -s tools/brom-install/tests -v`

Expected: PASS with zero failures and zero errors.

- [ ] **Step 8: Commit the install transaction**

```bash
git add tools/brom-install/libreecho_brom/install.py tools/brom-install/tests/test_install.py
git commit -m "feat: add allowlisted inactive-slot transaction"
```

### Task 6: Live Amonet Adapter and CLI

**Files:**
- Create: `tools/brom-install/libreecho_brom/amonet_adapter.py`
- Create: `tools/brom-install/libreecho_brom/cli.py`
- Create: `tools/brom-install/libreecho-brom-install.py`
- Test: `tools/brom-install/tests/test_cli.py`

**Interfaces:**
- CLI: `libreecho-brom-install.py --inventory-only --port PORT --amonet-dir DIR --backup-root DIR`.
- CLI: `libreecho-brom-install.py --install --port PORT --amonet-dir DIR --backup DIR --ota FILE --public-key FILE`.
- Adapter produces parsed GPT, disk GUID, and wrapper evidence using only `Device`, `handshake`, `load_payload`, and GPT parsing primitives from the pinned Amonet directory.

- [ ] **Step 1: Write failing CLI tests**

Patch the adapter and assert mutually exclusive modes, required arguments, refusal of unknown Amonet paths, inventory-only call routing, typed confirmation format, and final write-count summary on success and exceptions.

- [ ] **Step 2: Run CLI tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_cli.py -v`

Expected: FAIL because CLI modules are missing.

- [ ] **Step 3: Implement the minimal live adapter**

Insert only `$AMONET_DIR/modules` into `sys.path`. Import named primitives without importing or invoking full `main()`. Wrap the live `Device` in separate read-only and allowlisted facades. Close the serial port in `finally` when available.

- [ ] **Step 4: Implement the CLI and entry point**

Inventory mode must not accept `--ota`, `--backup`, or confirmation flags. Install mode requires an existing inventory directory and prints:

```text
Type INSTALL slot=<slot> image=5ca302c958c1 to continue:
```

EOF or mismatch exits before constructing the writer.

- [ ] **Step 5: Run CLI and complete test suites**

Run:

```bash
PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_cli.py -v
PYTHONPATH=tools/brom-install python3 -m unittest discover -s tools/brom-install/tests -v
python3 -m py_compile tools/brom-install/libreecho_brom/*.py tools/brom-install/libreecho-brom-install.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the live CLI**

```bash
git add tools/brom-install
git commit -m "feat: add BROM installer CLI"
```

### Task 7: NixOS Launcher, Documentation, and nixtop Deployment

**Files:**
- Create: `tools/brom-install/run-libreecho-brom-install.sh`
- Create: `tools/brom-install/update-source-hashes.sh`
- Create: `tools/brom-install/SHA256SUMS`
- Create: `tools/brom-install/README.md`
- Test: `tools/brom-install/tests/test_launcher.py`

**Interfaces:**
- Launcher usage: `./run-libreecho-brom-install.sh --inventory-only` and `./run-libreecho-brom-install.sh --install BACKUP_DIR OTA_PATH`.
- Deployment copies the complete `tools/brom-install/` directory to `/home/lucaspick/Downloads/libreecho-brom-install/` without modifying `/home/lucaspick/amonet-k32`.

- [ ] **Step 1: Write failing launcher contract tests**

Read the shell source as text and require `set -euo pipefail`, exact user/group checks, Nix dependencies `python3`, `pyserial`, and `pynacl`, pinned Amonet hashes, existing Amonet preflight, `udevadm` BROM VID/PID checks, Preloader refusal, and pass-through of only documented CLI modes.

- [ ] **Step 2: Run launcher tests and observe failure**

Run: `PYTHONPATH=tools/brom-install python3 -m unittest tools/brom-install/tests/test_launcher.py -v`

Expected: FAIL because launcher is missing.

- [ ] **Step 3: Implement launcher and README**

Add `tools/brom-install/update-source-hashes.sh`, which deterministically hashes every shipped source/test/README file except `SHA256SUMS` itself and writes sorted relative paths. The launcher verifies all local source hashes from that adjacent generated `SHA256SUMS`, verifies pinned Amonet inputs, runs the full suite and Amonet preflight, waits for stable `0e8d:0003`, resolves its `/dev/ttyACM*`, and executes the CLI. README documents receive-only UART, BROM short timing, backup storage, stop conditions, and exact commands. Tests fail if regenerating the manifest changes committed `SHA256SUMS`.

- [ ] **Step 4: Run local shell and test verification**

Run:

```bash
bash -n tools/brom-install/run-libreecho-brom-install.sh
tools/brom-install/update-source-hashes.sh
git diff --exit-code -- tools/brom-install/SHA256SUMS
PYTHONPATH=tools/brom-install python3 -m unittest discover -s tools/brom-install/tests -v
git diff --check
```

Expected: all exit 0.

- [ ] **Step 5: Deploy to nixtop Downloads**

Run:

```bash
rsync -a tools/brom-install/ \
  lucaspick@192.168.68.2:/home/lucaspick/Downloads/libreecho-brom-install/
ssh lucaspick@192.168.68.2 \
  'chmod 755 /home/lucaspick/Downloads/libreecho-brom-install/run-libreecho-brom-install.sh /home/lucaspick/Downloads/libreecho-brom-install/libreecho-brom-install.py'
```

Expected: files exist only beneath the named Downloads directory.

- [ ] **Step 6: Run nixtop offline verification without device access**

Run:

```bash
ssh lucaspick@192.168.68.2 \
  'cd /home/lucaspick/Downloads/libreecho-brom-install && ./run-libreecho-brom-install.sh --preflight-only'
```

Expected: source hashes, tests, PyNaCl signature check, and Amonet preflight pass; output states `no device access`.

- [ ] **Step 7: Commit launcher and documentation**

```bash
git add tools/brom-install
git commit -m "docs: add nixtop BROM installer launcher"
```

### Task 8: Review and First Live Inventory Checkpoint

**Files:**
- No source changes expected.
- Creates remotely: `/home/lucaspick/Downloads/libreecho-backups/<timestamp>/...`

**Interfaces:**
- Consumes the deployed launcher and physical operator action.
- Produces immutable `inventory.json`, `inventory.txt`, and backup files for review.

- [ ] **Step 1: Perform fresh pre-live verification**

Run local and nixtop test suites, syntax checks, source hashes, real OTA signature verification, and Amonet preflight. Record exact outputs. Do not claim readiness from an earlier run.

- [ ] **Step 2: Request the physical BROM operation**

Have the user start:

```bash
cd ~/Downloads/libreecho-brom-install
./run-libreecho-brom-install.sh --inventory-only
```

The user power-cycles while grounding the documented BROM point, keeps it grounded through handshake, removes it only at the payload prompt, and presses Enter.

- [ ] **Step 3: Monitor without issuing writes**

Observe nixtop USB state and the launcher. If USB disconnects or re-enumerates, stop and preserve partial logs. Inventory mode must report `writes=0` on every exit path.

- [ ] **Step 4: Verify backup outputs independently**

On nixtop, re-run SHA-256 over every backup and compare with `inventory.json`. Confirm BCB raw bytes, selected slot, both boot image headers/hashes, wrapper evidence, disk GUID, and exact GPT geometry.

- [ ] **Step 5: Stop for human review**

Do not run `--install` in the same live session. Present the inventory report, especially `ABB` versus `BCb`, backup completeness, and candidate inactive slot. Installation proceeds only after explicit review and a fresh command in a later checkpoint.
