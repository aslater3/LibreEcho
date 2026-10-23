# Release cadence

Every LibreEcho release, stable or patch, follows the same sequence and is
tracked in one **release checklist issue** (open it from the *Release
checklist* issue template). The issue stays open until every step below is
ticked or explicitly waived with a reason.

The order matters. A stable (`prd`) build that succeeds publishes immediately,
because the `public-release` environment has no required reviewer. So nothing is
dispatched to `prd` until the same sources have passed on hardware as a `dev`
build.

## 1. Freeze the sources

- All fixes are merged into `release/X.Y.Z` in every repository (Product,
  Platform, UI, Linux).
- Record the four branch heads in the checklist issue. These are the sources the
  hardware test and the stable build must both use.
- The release note `release/radar-puffin-vX.Y.Z.md` is merged into
  `release/X.Y.Z` **before** the stable dispatch, because the build copies it
  into the GitHub Release and into the checksummed `*-release-notes.md` assets.
  A commit pushed to the notes branch after its PR merges is not in the release.

## 2. Dev build

- Dispatch `build-release.yml` from `release/X.Y.Z` with `build_purpose=dev`
  and the same targets, OTA format and release version that `prd` will use.
- Approve the `dev-release` environment. Do **not** advance the dev OTA pointer
  unless dev-channel devices should be offered the build.
- Record the dev release tag and confirm its `build.json` matches the frozen
  heads.
- Verify every `*SHA256SUMS` file of the published dev release.

## 3. Hardware validation

On at least one Echo 2nd Gen (`radar_puffin`) and one Echo Dot (`biscuit`), from
the previous stable release:

- Install the dev OTA. The device must boot the new slot, reach startup-ready,
  pass its health probes and confirm the slot. A rollback fails the release.
- Reboot once more; the same slot and transaction must persist.
- Check every fix the release note claims, on the device (UI or API), not only
  in host tests.
- Record which units, which paths (web UI update, device updater, fresh install,
  `--reinstall`) and which results. Anything not exercised goes into the
  release note's **Validation** section as "not yet tested".

## 4. Stable build

- Re-dispatch `build-release.yml` from `release/X.Y.Z` with `build_purpose=prd`.
- Before approving `stable-release`, confirm the contract job checked out the
  frozen heads.
- After publication, verify every `*SHA256SUMS` file, confirm
  `releases/latest` is the new tag, and confirm
  `https://dl.libreecho.org/latest/download/libreecho-<target>-stable.ota.tar`
  (the URL devices fetch; there is no un-prefixed copy) has the same SHA-256 as
  the release's `libreecho-<target>-vX.Y.Z.ota.tar` for each target.
- Update at least one unit through its own web UI on the stable channel
  (Check for updates → Install update → Reboot) and return validation units to
  the stable channel.

## 5. Documentation sync

These drift if they are not checked on every release:

- **GitHub Release body**: matches the merged release note, including the
  upgrade paths and the hardware **Validation** results. Edit the body only;
  never replace checksummed assets.
- **Install guide** (`docs/install/README.md` on `main`, which is the copy the
  website and release notes link to): covers every upgrade path in the release
  note (OTA, reinstall from older series, boot-chain migration) and any new
  installer flags.
- **Website** (LibreEcho-Docs): current release number, release date, download
  links, checksum filenames and upgrade notes point at the new tag.
- `CHANGELOG.md` entry for the release.

## 6. Announce and close

- Discord announcement: what changed, who must update and how, any reinstall or
  migration requirement, and only the validation that actually happened.
- Close the issues the release fixes, with the hardware evidence.
- Merge `release/X.Y.Z` into `main` once the release is final.
