# Release cadence

Every LibreEcho release, stable or patch, follows the same sequence and is
tracked in one **release checklist issue** (open it from the *Release
checklist* issue template). The issue stays open until every step below is
ticked or explicitly waived with a reason.

The order matters. A stable (`prd`) build that succeeds publishes immediately,
because the `public-release` environment has no required reviewer. So nothing is
dispatched to `prd` until the same sources have passed on hardware as a `dev`
build.

## 0. Open the release line

A patch release `X.Y.(Z+1)` opens as soon as the first fix for the published
`X.Y.Z` is ready. A fix goes on the patch line when it repairs behaviour that
`X.Y.Z` ships broken, even if the fix is large; new capability with no defect
behind it waits for `release/X.(Y+1).0`.

1. **Cut the branch in all four repositories** (Product, Platform, UI, Linux),
   even those with no change yet, so the build resolves every component from
   `release/X.Y.(Z+1)`. Cut each one from the previous patch tip
   `release/X.Y.Z`, after that release's back-merge to `main` (step 6). Check
   that the tip's tree matches `main`; if it does not, finish the back-merge
   first. Creating the ref is the only direct write to a release branch;
   everything after it lands by PR.

   ```sh
   for r in LibreEcho LibreEcho-Platform LibreEcho-UI LibreEcho-Linux-6.1; do
     sha=$(gh api repos/aslater3/$r/git/ref/heads/release/X.Y.Z --jq .object.sha)
     gh api -X POST repos/aslater3/$r/git/refs \
       -f ref=refs/heads/release/X.Y.(Z+1) -f sha=$sha
   done
   ```

2. **Open the version PR in UI**, titled `chore: version X.Y.(Z+1)`, against
   the new branch. It:
   - sets `VERSION` (the only repository with a version file; Product takes the
     release version from the build dispatch and the release-note path);
   - adds `release/X.Y.(Z+1)` to both branch lists in
     `.github/workflows/checks.yml`. Without this, pull requests to the new
     branch get only the integration check, not the full source suite.

   The other repositories have no branch lists to update, but check with
   `grep -rn "release/X\.Y\.[0-9]" .github/workflows` in each in case one has
   been added.

3. **Open the release checklist issue** from the template and record the four
   new branch heads as the starting point.

4. **Target fixes at the patch line.** Each fix PR is based on
   `release/X.Y.(Z+1)`, titled `fix(<area>): ...`, carries
   `Release impact: patch` in its description, and explains the user-visible
   defect it repairs.
   A fix already open against `main` or `release/X.(Y+1).0` is moved by
   rebasing its commits onto the patch branch (`git rebase --onto`), then
   retargeting the PR's base. Re-run the full suite if the two bases' trees
   differ.

5. **Forward-port every fix** to the active `release/X.(Y+1).0` in the same
   sitting, as a separate PR that names the original. If the two bases have
   identical trees the same commit applies unchanged; otherwise cherry-pick
   onto a branch cut from `release/X.(Y+1).0`. A fix merged only to the patch
   line is lost the next time `X.(Y+1).0` ships.

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
  `releases/latest` is the new tag, and confirm `dl.libreecho.org` serves the new
  `libreecho-<target>-stable.ota.tar` for each target.
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
- Once the release is final, back-merge `release/X.Y.Z` into `main` by PR in
  every repository, in Linux, UI, Platform, Product order. Then forward-merge
  `main` into the active `release/X.(Y+1).0` by PR, so the next line carries
  everything shipped (for 0.14.3 these were UI #374 and #375). Fixes
  forward-ported in step 0 make this merge trivial; any conflict means a fix
  was missed.
- Check containment rather than trusting the PR state:
  `git merge-base --is-ancestor origin/release/X.Y.Z origin/main` in each
  repository.
