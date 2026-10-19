# LibreEcho radar-puffin v0.14.1

LibreEcho 0.14.1 is a fix release for the Amazon Echo (2nd Gen, `radar_puffin`)
and the experimental Amazon Echo Dot (2nd Gen, `biscuit`), based on 0.14.0.

## Fresh installs on units with a larger data partition

Some Echo (2nd Gen) units keep the stock partition table with a `userdata`
partition larger than the two sizes 0.14.0 accepted. On those units the 0.14.0
image refused to mount `/data`, so a fresh install appeared to finish but never
reached `startup-ready`. 0.14.1 accepts any `userdata` partition that is large
enough and carries the LibreEcho ext4 label (`LIBREECHO_DATA`). Init never
formats or repairs a partition, and it refuses one that is too small or does
not carry that label. Boot, system and other layout partitions are still
checked exactly.

**If your install stopped at "waiting for startup-ready" on 0.14.0**, the
device is already unlocked. Install 0.14.1 with the new reinstall option; it
does not repeat the unlock step:

```sh
./run-one-shot.sh radar-puffin-v0.14.1 --reinstall --execute-hardware
```

`--reinstall` **erases userdata** (settings, Wi-Fi, accounts) and asks you to
type `ERASE` first. `--continue` still only resumes the release recorded in the
saved state and will refuse a 0.14.0 install. The old state is kept as
`state.json.before-reinstall-<time>`.

## Installer

- Added `--reinstall` (above).
- The feature stager no longer mounts `userdata` itself, which used to hide a
  boot-time `/data` failure until the final reboot. An unmounted `/data` is now
  reported as such, with the device's init messages, instead of as a payload
  hash mismatch.
- The final restart is confirmed by a changed boot ID or ADB connection. A
  `reboot failed: File exists` message from the device no longer decides the
  outcome either way.

## Baby monitor and sleep sounds

- The Baby Monitor page is now **Nursery**. It holds the monitor and the sleep
  sounds (moved from the Audio page). The old `/baby-monitor` address redirects.
- Clearer controls: **Background whoosh** and **Heart rate (beats per minute)**.
- The heartbeat is now audible on the Echo speaker, and brown and pink noise no
  longer clip.
- The action button can be set to **Sleep sound on/off**.
- A button action the API did not recognise used to be silently ignored while
  answering success; it is now rejected.

## SSH

- SSH starts only when the SSH switch saved in the web interface is on, and it
  stops when the switch is turned off. **Existing 0.14 development devices that
  relied on SSH starting once web users existed will have SSH off until the
  switch is turned on.**
- The SSH host key is kept on the device so clients do not see a changed key
  after a reboot. It is flushed to storage before use, and an empty key left by
  a power loss is regenerated.
- Release bundles verify Dropbear, its key generator and `scp`, and no longer
  stage a root password hash.

## Validation

Host and CI tests only: the userdata check was exercised in an ARM virtual
machine against the affected partition size, and the installer against a stub
device. This release has **not** yet been run on a physical unit with a larger
`userdata` partition, and the `--reinstall` path has not been run on hardware.
Hardware acceptance is separate evidence.

## Known issues

- Units that were flashed with an older Echo Dot (Biscuit) bootloader before a
  Radar-specific one existed report `BISCUIT` in fastboot and may not boot the
  Radar image. They need the newer Kaeru-based chain first; a conversion guide
  is not published yet.
- The 0.14.0 known issues otherwise still apply.

## Build notes

0.14 candidates built before the SSH bundle change carry no `scp_sha256`, so
stable packaging rejects them. Rebuild them from the 0.14.1 sources.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual
component notices, unchanged from 0.14.0.
