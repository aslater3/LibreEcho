# Releasing LibreEcho

This document has two parts. **Release model** says what goes into which
release and which branches carry it. **Release sequence** is the checklist
every release follows, from frozen sources to announcement, and is tracked in
one **release checklist issue** (open it from the *Release checklist* issue
template). The issue stays open until every step is ticked or explicitly
waived with a reason.

## Release model

### Three kinds of release

The product is pre-1.0, so the version is `0.Y.Z`.

| Kind | Version | Contains | When |
| --- | --- | --- | --- |
| **Feature release** | `0.(Y+1).0` | New capability, plus every fix merged since the last release | Regular train: aim for every 2–3 weeks, and cut early when a validated feature is ready |
| **Fix release** | `0.Y.(Z+1)` | Fixes only, for a defect the current stable ships | Only when a fix cannot wait for the next feature release |
| **Breaking release** | `0.(Y+1).0`, marked breaking | One incompatible change and its migration, with as little else as possible | When the breaking change is ready, never as a gate for unrelated work |

A change is **breaking** when the previous stable cannot move to it by a
normal OTA, or when rollback is unsupported: OTA protocol or package format,
partition or boot-chain contract, data or config migration without automatic
upgrade, a reinstall requirement, or a kernel line switch that changes any of
these. Everything else is a feature or a fix.

Features do not wait for a breaking change. Breaking work stays on its
`feature/` branch until it is ready, and merges into `main` only when the next
release is designated breaking. Feature releases keep shipping in the
meantime.

### Which kind does my change go in?

- It repairs behaviour the current stable ships broken → **fix**. It goes in
  the next feature release, or in a fix release if it cannot wait (bricking,
  data loss, failed OTA or confirmation, security, a regression introduced by
  the current stable). Size does not matter; a large repair is still a fix.
- It adds capability, however small → **feature**. It goes in the next
  feature release. It never goes in a fix release.
- It meets the breaking definition above → **breaking release**.

### Features that are not proven on hardware

A feature that has not been validated on both `radar_puffin` and `biscuit`
may still ship, if it is **off by default** behind a setting or switch and the
release note calls it experimental and says which targets were tested. Turning
it on by default later is itself a feature-release change and needs the
hardware validation in step 3.

### One active release line

- `main` is the integration branch. Features and fixes land on `main` by PR
  (unless a release branch is open for them; see below).
- To cut a feature release, branch `release/0.(Y+1).0` from `main` in every
  repository. While that branch is open, only fixes for what it ships land on
  it, by PR; new features keep landing on `main` for the release after.
- When the release is final, back-merge it into `main` (step 6) and stop using
  the branch. At most one feature release branch is open at a time.
- A fix release branch `release/0.Y.(Z+1)` is cut from the last stable
  release branch only when a fix cannot wait. Its fixes reach `main` through
  the back-merge, so there is no separate forward-port PR. If a feature
  release branch is also open, cherry-pick the fix onto it by PR in the same
  sitting.
- Release branches are not created ahead of time. Cut the next one when its
  contents are ready to freeze, not when the previous release ships.

### Older series

When a feature release ships, the previous series stops receiving releases.
The exceptions are a fix for bricking, data loss or security that affects
units which cannot yet move forward, and a bridge release that a breaking
change's migration requires (as 0.13.21 was for 0.14). Both are fix releases
of the old series and follow the full sequence below.

## Release sequence

### 0. Open the release

1. **Cut the branch in all four repositories** (Product, Platform, UI,
   Linux), even those with no change, so the build resolves every component
   from `release/X.Y.Z`. Cut a feature release from `main`, and a fix release
   from the previous stable release branch after its back-merge. Creating the
   ref is the only direct write to a release branch; everything after it lands
   by PR.

   ```sh
   for r in LibreEcho LibreEcho-Platform LibreEcho-UI LibreEcho-Linux; do
     sha=$(gh api repos/aslater3/$r/git/ref/heads/<main|release/X.Y.Z-1> --jq .object.sha)
     gh api -X POST repos/aslater3/$r/git/refs \
       -f ref=refs/heads/release/X.Y.Z -f sha=$sha
   done
   ```

2. **Open the version PR in UI**, titled `chore: version X.Y.Z`, against the
   new branch. It sets `VERSION` and adds `release/X.Y.Z` to both branch lists
   in `.github/workflows/checks.yml`; without this, pull requests to the new
   branch get only the integration check. Check the other repositories with
   `grep -rn "release/X\.Y\.[0-9]" .github/workflows` in case one has gained a
   branch list.

3. **Open the release checklist issue**, state the release kind (feature, fix
   or breaking), and record the four new branch heads.

4. **Sort the contents.** Every PR in the release declares `Release impact:`.
   A fix release contains only `patch` changes. A feature release that
   contains a breaking change is a breaking release and says so in its note.
   Experimental features are listed as off by default.

### 1. Freeze the sources

- All fixes are merged into `release/X.Y.Z` in every repository (Product,
  Platform, UI, Linux).
- Record the four branch heads in the checklist issue. These are the sources
  the hardware test and the stable build must both use.
- The release note `release/radar-puffin-vX.Y.Z.md` is merged into
  `release/X.Y.Z` **before** the stable dispatch, because the build copies it
  into the GitHub Release and into the checksummed `*-release-notes.md`
  assets. A commit pushed to the notes branch after its PR merges is not in
  the release.

The order of steps 2–4 matters. A stable (`prd`) build that succeeds publishes
immediately, because the `public-release` environment has no required
reviewer. So nothing is dispatched to `prd` until the same sources have passed
on hardware as a `dev` build.

### 2. Dev build

- Dispatch `build-release.yml` from `release/X.Y.Z` with `build_purpose=dev`
  and the same targets, OTA format and release version that `prd` will use.
- Approve the `dev-release` environment. Do **not** advance the dev OTA
  pointer unless dev-channel devices should be offered the build.
- Record the dev release tag and confirm its `build.json` matches the frozen
  heads.
- Verify every `*SHA256SUMS` file of the published dev release.

### 3. Hardware validation

On at least one Echo 2nd Gen (`radar_puffin`) and one Echo Dot (`biscuit`),
from the previous stable release:

- Install the dev OTA. The device must boot the new slot, reach
  startup-ready, pass its health probes and confirm the slot. A rollback fails
  the release.
- Reboot once more; the same slot and transaction must persist.
- Check every fix and every on-by-default feature the release note claims, on
  the device (UI or API), not only in host tests. Experimental features that
  ship off by default need only a check that they stay off and do not affect
  boot or confirmation.
- For a breaking release, also run the migration path the release note gives
  (reinstall, bridge release or boot-chain migration) on each target.
- Record which units, which paths (web UI update, device updater, fresh
  install, `--reinstall`) and which results. Anything not exercised goes into
  the release note's **Validation** section as "not yet tested".

Keep releases small enough that this step stays short. If the release note
has more claims than can be checked in one sitting, the release is too big:
move features to the next train rather than waiving checks.

### 4. Stable build

- Re-dispatch `build-release.yml` from `release/X.Y.Z` with
  `build_purpose=prd`.
- Before approving `stable-release`, confirm the contract job checked out the
  frozen heads.
- After publication, verify every `*SHA256SUMS` file, confirm
  `releases/latest` is the new tag, and confirm
  `https://dl.libreecho.org/latest/download/libreecho-<target>-stable.ota.tar`
  (the URL devices fetch; there is no un-prefixed copy) has the same SHA-256
  as the release's `libreecho-<target>-vX.Y.Z.ota.tar` for each target.
- Update at least one unit through its own web UI on the stable channel
  (Check for updates → Install update → Reboot) and return validation units
  to the stable channel.

### 5. Documentation sync

These drift if they are not checked on every release:

- **GitHub Release body**: matches the merged release note, including the
  upgrade paths and the hardware **Validation** results. Edit the body only;
  never replace checksummed assets.
- **Install guide** (`docs/install/README.md` on `main`, which is the copy the
  website and release notes link to): covers every upgrade path in the
  release note (OTA, reinstall from older series, boot-chain migration) and
  any new installer flags.
- **Website** (LibreEcho-Docs): current release number, release date,
  download links, checksum filenames and upgrade notes point at the new tag.
- `CHANGELOG.md` entry for the release.

### 6. Announce and close

- Discord announcement: what changed, who must update and how, any reinstall
  or migration requirement, which features are experimental, and only the
  validation that actually happened.
- Close the issues the release fixes, with the hardware evidence.
- Back-merge `release/X.Y.Z` into `main` by PR in every repository, in Linux,
  UI, Platform, Product order. If a feature release branch is open, merge
  `main` into it by PR as well; a conflict means a fix was missed.
- Check containment rather than trusting the PR state:
  `git merge-base --is-ancestor origin/release/X.Y.Z origin/main` in each
  repository.
- Stop using the release branch. The next release gets a new branch.
