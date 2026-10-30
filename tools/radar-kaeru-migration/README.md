# Radar kaeru-chain migration tool

`migrate.sh` converts a `radar_puffin` that is still running the **old biscuit/k32
boot chain** (the LK used by the pre-radar LibreEcho installs) to the current
**kaeru-based radar amonet chain**, then converts the partition table from the
legacy `_x` layout to the plain layout the kaeru LK expects. The installed
LibreEcho OS image stays where it is, but you should not boot it again: the next
step is a reinstall from fastboot (see [After the migration](#after-the-migration)).

> **Why the GPT step exists.** The kaeru LK resolves the partition *name*
> `boot_a` and has **no `_x` redirect**. On a unit left on the `_x` layout,
> `fastboot flash boot_a` writes the 110 MiB wrapper (p17), the LK boots p17,
> but bootctl/OTA and the installer use p10/p11 — so the one-shot installer
> fails with `boot_a_x readback hash mismatch` and OTA slot switching would not
> change what boots. Earlier copies of this kit skipped the GPT step.

Typical symptom this fixes: `fastboot getvar product` reports `BISCUIT` on a
radar_puffin, and the one-shot installer warns
`boot-chain identity differs from board: product=BISCUIT explicit-target=radar_puffin`
because the old chain is already unlocked, so the installer never converts it.

> **Read this first.**
>
> - **Do not use this kit on a development-profile LibreEcho image.** Those images
>   write `FASTBOOT_PLEASE` to `expdb` at every boot, which destroys the kaeru
>   chain. The kit checks `/etc/libreecho/image-profile` and refuses anything
>   other than `ota`.
> - **Once the kaeru chain is written, never run `adb reboot bootloader`,
>   `adb reboot-bootloader` or `adb reboot fastboot` from the old LibreEcho OS.**
>   On images before 30 September 2026, that command writes `FASTBOOT_PLEASE`
>   over the start of the kaeru payload in `expdb`, and the unit then loops in
>   USB download mode with `Failed to load LK` (issue #325). The kit does its own
>   reboot safely and leaves the unit in fastboot.

## Quickstart (do this in order)

With LibreEcho running and ADB up:

```sh
./migrate.sh -s SERIAL --dry-run     # checks + backups, no writes — always run first
./migrate.sh -s SERIAL --verify      # the conversion, the guarded reboot, then a fastboot check
```

The second command writes the chain and the GPT, reboots the unit into kaeru
fastboot by itself, and checks `product=RADAR` and unlocked. `--verify` belongs
in this run; there is no separate third command. Leave the unit in fastboot and
go to [After the migration](#after-the-migration).

Run on its own later, `./migrate.sh -s SERIAL --verify` only reads: if the unit
is already in fastboot it repeats the `getvar` check; if it is running LibreEcho
it prints what to do and reboots nothing.

### Already migrated with an older copy of this kit (`boot_a_x readback hash mismatch`)

The chain is already kaeru but the partition table is still `_x`
(`adb shell grep PARTNAME /sys/class/block/mmcblk0p10/uevent` shows `boot_a_x`).
With LibreEcho running and ADB up:

```sh
./migrate.sh -s SERIAL --gpt-fix --dry-run   # validates, saves the current GPT, writes nothing
./migrate.sh -s SERIAL --gpt-fix --verify    # writes backup GPT, then primary, then the guarded reboot
```

Then continue with [After the migration](#after-the-migration). Do not reboot
the unit by hand to get there.

`--gpt-fix` refuses on an old-chain unit (the biscuit LK needs `_x`; run the full
migration instead). On a plain table it changes no partition table but still
finishes with the guarded reboot into fastboot, so it is also the safe way to get
an already-converted unit from the old OS into fastboot.
`./migrate.sh -s SERIAL --gpt-restore` writes the saved `backup/gpt-*-orig.bin`
back (checked against `backup/GPT-MANIFEST.sha256`).
`--no-gpt-fix` runs the old chain-only migration.

If the device is stuck in fastboot with rainbow LEDs **and `fastboot getvar
product` reports `BISCUIT`** (the old chain left a `FASTBOOT_PLEASE` marker in
expdb), unstick it first with `fastboot -s SERIAL erase expdb` +
`fastboot -s SERIAL reboot`, get it back into LibreEcho over ADB, then run the
kit. Erasing expdb is safe **only** while the old chain is in place; once kaeru
is installed (`product` reports `RADAR`), expdb holds the kaeru payload and must
never be erased.

## What it checks before writing anything

1. **The running image.** `/etc/libreecho/image-profile` must be `ota`
   (development images are refused). `/etc/libreecho/target`, when present, must
   say `target_id=radar_puffin`. Images before 0.14 have no target file; they
   were built for radar_puffin only (LibreEcho's own init makes the same
   assumption), so the kit accepts a missing file and relies on the geometry
   check below. A kernel that writes expdb at boot (`echo-marker` in `dmesg`, or
   the `echo_fastboot_marker_init` symbol) is refused. A pending
   `/tmp/reboot.request` file is refused.
2. **Partition names.** `PARTNAME=expdb` must be exactly `mmcblk0p7` and
   `PARTNAME=misc` exactly `mmcblk0p8` (from sysfs).
3. **Chain identity (expdb).**
   - LK header at offset 0 with a kaeru header chained after it → already kaeru,
     refuses (except with `--gpt-fix`, which requires it).
   - `FASTBOOT_PLEASE` or all zeros at the start **and** a kaeru header still
     at its offset → a **clobbered kaeru chain** (the issue #325 state). The kit
     refuses and writes nothing. Do not reboot that unit; ask for help (see
     [Recovery](#recovery-failed-to-load-lk-usb-download-loop) if it already
     stopped booting).
   - `FASTBOOT_PLEASE` or all zeros with no kaeru header → old chain, proceeds.
   - Anything else → refuses.
4. **LK identity strings.** Warns if `lk_a` does not contain `biscuit` — confirm
   the device really is on the old chain before continuing.
5. **`_x` geometry** against the LibreEcho radar contract
   (`/proc/partitions`, KiB): p3/p5=1024, p4/p6=5120, p7=10240, p10/p11=16384,
   p16=1068544 (legacy radar GPT) or 1076736 (legacy biscuit GPT, seen on units
   that report `BISCUIT`). Mismatch → refuses.

## What it writes (and nothing else)

Before the first write (including the backups), the kit makes
`/tmp/reboot.request` a **directory**. Until the next boot, the old OS's adb
reboot handler then fails with `File exists` instead of writing to expdb.

Sources are the pinned `amonet-radar-v1.0.0` radar package in `stage/`,
SHA-256-pinned by `MANIFEST.sha256` and re-verified on-device after push:

| Source | SHA-256 | Destination |
|---|---|---|
| `lk.bin` | `5070e18839496f18ce8c7e4d3f07e27e3376b96f487ca9c63b081e35e2230db3` | `lk_a` (p3), `lk_b` (p5) |
| `tee-payload.bin` | `91bed859bbcfc3347d9fa5d732332fb1947311dbcb9384b0d7a9c1863af4709f` | `tee1` (p4) |
| `tz.img` | `15cd40caafa6cd8bb8c4e4ed173c973569a8a2b026235d81776ec888f1522c72` | `tee2` (p6) |
| `radar-kaeru.bin` | `02e981b2e055e08e09c71c34f9d88a22d9b1ad316adafb0986e203246566013b` | `expdb` (p7), offset 0 |
| `preloader.img` | `4c7aac4efa7a1bce6922a355c3636e1da575f99bee960d6afd07e1ba107668f8` | `mmcblk0boot0` (mknod + force_ro, written LAST) |

Then the GPT step (`gpt-fix.py`, pinned in `MANIFEST.sha256`, stdlib Python 3 on
the host): `boot_a_x`/`boot_b_x` (p10/p11) are renamed `boot_a`/`boot_b` on the
same sectors and the p17/p18 wrapper entries are removed. `userdata` keeps its
start/end (unlike upstream amonet `restore_gpt`, which extends it; the installer
pins its size). The result is byte-identical to the GPT of the hardware-validated
pinned-kaeru dev unit. The tool refuses on any CRC error, primary/backup
disagreement, or geometry that is not the radar `_x` contract.

Finally, `misc[0:16]` gets amonet's force-fastboot marker `FASTBOOT_PLEASE\0`
(the same bytes and offset as amonet's own `force_fastboot()`), so kaeru stops
in fastboot instead of booting the old OS. The rest of misc, including the boot
control block at `0x360`, is checked to be unchanged.

Never touched: OS image contents, recovery, userdata contents. Every write is
followed by a readback SHA check; the write order (chain first, preloader last)
is copied from the tested `radar-full-stock-20261009` restore.

## How it reboots

After all writes and readbacks, in this order:

1. Re-checks expdb: an `LK` header at offset 0 and a `kaeru` header chained
   after it (the same check the installer uses).
2. Makes expdb read-only for the rest of this boot (`blockdev --setro`, then
   `blockdev --getro` must say `1`). If that cannot be confirmed it carries on
   only while the `/tmp/reboot.request` guard directory is still in place.
3. Writes and reads back the misc force-fastboot marker.
4. Reboots with `adb shell 'sync; sync; reboot -f'`. `reboot -f` calls the
   kernel directly, so no LibreEcho reboot handler runs. The kit never uses
   `adb reboot`, `adb reboot-bootloader` or `fastboot reboot`.

With `--verify` it then waits up to 120 s for fastboot and checks
`product=RADAR` and `unlock_status`. It leaves the unit in fastboot.

## After the migration

Keep the unit in fastboot and run the one-shot installer's reinstall from there,
with `--target radar_puffin` and an install id you have **not used before**:

```sh
./run-one-shot.sh "$TAG" --reinstall --target radar_puffin --install-id <new-name> --fastboot-serial SERIAL --execute-hardware
```

When the unit is already in fastboot, the installer does not reboot it first.
Do not boot the old LibreEcho to start the reinstall and do not use
`adb reboot bootloader` on it. A new install id is needed when an older
installer state for this unit exists (for example one left at
`BRICK_SUBMITTED`); keep that old state directory as it is.

## Backups

`backup/` holds full-partition captures of boot0, lk_a, lk_b, tee1, tee2, expdb
and misc, captured and SHA-verified **before** any write, listed in
`backup/MANIFEST.sha256`. `--gpt-fix` and `--gpt-restore` runs save misc as
`backup/p8-<timestamp>.img`. The original GPT is in `backup/gpt-*-orig.bin`.

There is **no rollback of the boot chain** from this kit. The old preloader can
only be put back through a case-open BROM short, and writing the old `tee1`
back removes the Mute-button recovery path described below. Do not write these
files back yourself. Keep `backup/` safe and share it with support if asked.
The GPT alone can be put back with `--gpt-restore` while LibreEcho is running.

## Recovery: "Failed to load LK" USB download loop

Symptom: the unit never reaches LibreEcho or fastboot, and the host's `dmesg`
shows it re-appearing every few seconds as `0e8d:2000` with
`Manufacturer: ERROR`, `Product: Failed to load LK`. This is what happens when
`expdb` is overwritten after the kaeru chain was installed (issue #325). The
preloader and the `tee1` recovery payload are still working, so it can usually
be fixed without opening the case. **If you are unsure at any step, stop and
ask on the issue.**

You need the `amonet-radar-v1.0.0.zip` archive the kit's payloads come from
(sha256 `ecdb07bc05a508532e5ffed77121592d492b1a91572839e0f17545421f398f1a`),
extracted, with Python 3 and `pyserial`. Its `bin/radar-kaeru.bin` is the same
file as this kit's `stage/radar-kaeru.bin`. Run the commands from its `amonet/`
directory. Do not use mtkclient.

> **Never run `bootrom-step.sh` with no arguments.** That runs the full amonet
> install (RPMB, GPT restore and more). Only use the exact `read` and `flash`
> commands below.

**Entering PWNED mode.** Each command below waits for the device. Start the
command first. Then unplug the barrel power, keep USB connected to the host,
**hold Mute** and plug the barrel power back in. Keep holding Mute until the
command logs `Found port` (in `dmesg`: `0e8d:2000`, `Manufacturer: PWNED`,
`Product: MT8163 Preloader`). The unit reboots when each command finishes, so
repeat this for every command.

1. **Diagnose (read-only).**

   ```sh
   ./bootrom-step.sh read expdb expdb-now.bin
   ./bootrom-step.sh read misc misc-now.bin
   xxd expdb-now.bin | head -4
   cmp -i 15 -n 264529 expdb-now.bin bin/radar-kaeru.bin && echo "rest of expdb matches"
   ```

   If `expdb-now.bin` starts with `FASTBOOT_PLEASE` and the rest matches, the
   cause is confirmed; continue. If expdb looks intact or the rest does not
   match, **stop here** and post the `xxd` output on the issue. Keep both files.

2. **Set the misc force-fastboot marker first**, so the old OS cannot boot
   when expdb is fixed. Only the first 512-byte sector is written; the boot
   control block at `0x360` is in the next sector and is not touched.

   ```sh
   head -c 512 misc-now.bin > misc-sector0.bin
   printf 'FASTBOOT_PLEASE\0' | dd of=misc-sector0.bin bs=16 count=1 conv=notrunc
   ./bootrom-step.sh flash misc misc-sector0.bin
   ./bootrom-step.sh read misc misc-after.bin
   xxd misc-after.bin | head -1     # must start with FASTBOOT_PLEASE
   ```

3. **Rewrite expdb and read it back.**

   ```sh
   ./bootrom-step.sh flash expdb bin/radar-kaeru.bin
   ./bootrom-step.sh read expdb expdb-after.bin
   cmp -n 264544 expdb-after.bin bin/radar-kaeru.bin && echo "expdb restored"
   ```

4. The unit reboots by itself after the last command. Without Mute held it
   should stop in fastboot with `fastboot getvar product` = `RADAR`. Continue with
   [After the migration](#after-the-migration).

## Support logging — what to share if something goes wrong

Every run writes, under this directory:

- `logs/run-<serial>-<UTC timestamp>.log` — the complete transcript: host
  kernel + fastboot/adb versions, device kernel + uid, serial, the image checks,
  every gate decision (with the raw expdb head and geometry table when it
  refuses), all backup/staging/write hashes, and a `SUPPORT SUMMARY` line on
  abort or success.
- `migration.jsonl` — one JSON event per backup/stage/write/reboot step with
  SHA-256 and UTC timestamp.

If a run aborts or behaves unexpectedly, share **both files** with LibreEcho
support. They contain no secrets, but they do contain your serial; replace it
before posting them publicly.

## Provenance and status

- Payloads: `amonet-radar-v1.0.0` radar package, pinned by SHA above.
- Write/readback/backup discipline copied from the tested
  `radar-full-stock-20261009/flash.sh` (successful full Radar stock restore log
  ends `ALLDONE`).
- Gates, backups and staging verified live on a kaeru dev unit (correctly
  refused: already kaeru). That dev unit is on the **plain** layout (p10/p11
  named `boot_a`/`boot_b`, no p17/p18), so it never proved the kaeru LK works on
  `_x`. A field unit (kaeru chain, `_x` GPT) showed it does not: one-shot
  `flash boot_a` booted fine but p10 kept the previous image.
- `gpt-fix.py` output on the `_x` form of the dev unit's table is byte-identical
  to the dev unit's real primary and backup GPT.
- Host tests (`tools/test_release_tools.py`) run the whole kit against a fake
  adb/fastboot and a file-backed device: full migration, `--gpt-fix`, the
  separate read-only `--verify`, and every refusal above.
- Issue #325: a real run wrote the chain correctly, then a manual
  `adb reboot-bootloader` from the old OS overwrote expdb. The reboot guard,
  the expdb lock, the misc marker and the `reboot -f` reboot were added for
  that, and have **not yet been run on real hardware**.
