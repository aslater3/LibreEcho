# LibreEcho Product Repository — Agent Instructions

This repository is the product and release home for LibreEcho: landing content,
cross-repository public documentation, release notes, and release tooling. Read
`CONTRIBUTING.md` for change-ownership routing before non-trivial work.

## Agent operating contract

These rules govern normal task execution, subject to higher-priority system and
developer instructions and the repository/workspace safety boundaries below.

1. The user's current request defines the intended scope. A request to fix,
   implement, or update explicitly authorizes reversible local preparation in
   that scope: create or use a purpose-named branch/worktree, investigate the
   necessary in-scope files, make the required edits, create local commits, and
   run appropriate validation. Do not stop merely because the work needs more
   in-scope files than first expected; expected file count is not a boundary.

2. Work only on the named problem and the files needed to solve it. Do not fix
   adjacent bugs, refactor nearby code, clean up unrelated issues, or improve
   the architecture unless asked. Preserve unrelated dirty changes and other
   worktrees; do not reset, stash, clean, switch, delete, or absorb them merely
   to make the requested task convenient.

3. Before editing, identify the requested outcome, likely files, and validation
   needed to establish it. Ask before a material scope expansion, destructive or
   irreversible operation, or unresolved decision; first complete independent
   authorized work that does not depend on that decision.

4. Reviews, diagnoses, explanations, and plans do not authorize project-file
   edits unless the user explicitly asks for edits. Publication, push, pull
   request creation, merge, release/tagging, deployment, flashing, rebooting,
   and other hardware-changing actions remain separate explicit gates.

5. Every command must have a bounded timeout. Bounded background work,
   watchers, or servers are permitted only when needed for the authorized
   outcome; define their scope and timeout, capture their result, and stop and
   clean them up on completion or timeout. Do not retry indefinitely.

6. Run focused, meaningful checks that establish every acceptance criterion and
   all applicable repository-required checks. Do not impose a one-test limit or
   a blanket aggregate-suite requirement: broaden or repeat validation when
   changed paths, failures, required checks, or unresolved risk justify it. If
   authorized PR publication is part of the workflow, required CI must complete
   and be green on the exact head; otherwise do not poll remote CI unprompted.
   Keep host, CI, image/release, and hardware evidence distinct. Stop when every
   acceptance criterion is evidenced and required checks pass. Report unrelated
   failures and whether they block acceptance; do not fix them silently. Avoid
   tests that merely mirror reversible, low-impact implementation details.

7. Subject to higher-priority system/developer instructions and explicit
   project safety boundaries, the current request takes precedence over
   procedural defaults in skills or later non-safety guidance. If an instruction
   blocks completion, identify its exact source file and section, quote the
   exact blocking rule, explain the conflict, and complete independent
   authorized work rather than silently abandoning it.

8. Delegate independent, bounded research, review, or test analysis when tools
   support it and delegation improves speed or quality. Give each delegate
   disjoint write ownership or read-only work; never permit shared writes, and
   verify delegated findings before reporting success. Do not require recursive
   delegation when the harness does not support it.

9. Use concise plain language. Give brief progress updates during long tasks;
   report results first, with exact evidence and remaining blockers at a
   proportionate level of detail.

## Boot-chain safety

Current devices run an **amonet v2.0.0** chain. One partition changed purpose
between chain generations, and writing it the old way destroys the chain:

| Partition | Legacy amonet v1.x | **v2.0.0 (current)** |
|---|---|---|
| `expdb` (p7) | `FASTBOOT_PLEASE` marker | **`*-kaeru.bin` — the LK-stage patch payload, at offset 0** |
| `misc` | — | `FASTBOOT_PLEASE` marker, at `misc[0:16]` |

1. **Never write `FASTBOOT_PLEASE` to `expdb`, and never erase `expdb`, on a
   v2.0.0 chain.** Those 15 bytes land exactly on the kaeru payload's MTK image
   header (`88 16 88 58 | size | "LK"`); the LK-stage patch can then never run
   and the device bricks. It is harmless on v1.x chains, which is why the old
   location still appears in code.
2. **Establish the chain generation before any flash write.** Read
   `expdb[0:16]`: MTK magic `88 16 88 58` means the kaeru payload is present and
   v2.0.0 is in use; ASCII `FASTBOOT_PLEASE` means the chain is already broken.
   A partition *name* check (`PARTNAME=expdb`) cannot detect this — the name is
   identical in both generations and only the content differs.
3. **On v2.0.0 the marker target is `misc[0:16]`**, matching the amonet package's
   own `force_fastboot()`.
4. **The failure appears one boot later**, so it reads as a sudden hardware
   fault. When a unit stops booting after an image that writes flash at boot,
   inspect `expdb[0:16]` before concluding hardware. The recovery window is
   entered by holding MUTE at power-on; recovery and the full byte-level analysis
   are in LibreEcho-Platform issue #195.
5. **This repository owns the build-time marker contract.** `build/check_marker_contract.sh`
   still enforces the pre-v2.0.0 target (the `FASTBOOT_PLEASE` literal in the
   kernel driver plus the `PARTNAME=expdb` gate). Any marker write that is
   retained must be retargeted to `misc`, and this contract updated in the same
   change, so the old location cannot silently return.

# Branching, Pull Requests, and Versioning

These rules govern how changes flow through this repository. They apply equally
to human contributors and AI coding agents (Claude Code, Codex, Hermes, Cursor,
Copilot, and any similar tool). Repository-specific build, test, and safety
rules elsewhere in this repository remain in force unchanged; this section
governs branch, PR, and release flow only.

## Branch model

```text
feature/<purpose>   fix/<issue-or-purpose>
        \                 /
         \ PR         PR /      (fix for an open release branch: PR to that branch)
          v              v
               main               integration branch
                 |
                 | cut when the next release is ready to freeze
                 v
         release/X.Y.Z           one active release line; fixes only by PR
                 |
                 | back-merge by PR when the release is final
                 v
               main
```

- `main` is the integration branch. Nothing is committed directly to `main` or
  to any `release/*` branch; every change lands through a pull request.
- **Features** start from `main` on a branch named `feature/<short-purpose>`
  and merge into `main`. They ship in the next **feature release**
  (`release/0.(Y+1).0`), which is cut from `main` on a regular train.
  Features never go into a fix release.
- **Fixes** start from `main` on `fix/<purpose>` and merge into `main`,
  unless a release branch is open for the release they fix; then they are
  based on and merged into that release branch, and reach `main` through the
  back-merge.
- **Breaking changes** (see the bump rules below) stay on their `feature/`
  branch until ready and merge into `main` only when the next release is
  designated a breaking release. Unrelated features do not wait for them.
- Pre-1.0 terminology: a `Y` bump is a **feature release** (or a **breaking
  release** when it contains a breaking change) and a `Z` bump is a **fix
  release**. The `Release impact:` label always uses SemVer terms: a feature
  release change is `Release impact: minor`, a fix is `Release impact: patch`,
  and a breaking change is `Release impact: major` even while pre-1.0.
- Release branches are cut only when their contents are ready to freeze, not
  ahead of time, and at most one feature release branch is open at once. A
  fix release branch is cut from the last stable release branch only when a
  fix cannot wait for the next feature release. If a fix was merged into
  `main` and must also ship on an open release branch, cherry-pick the merged
  commit onto a new `fix/<purpose>` branch cut from the release branch and
  open a PR against the release branch that references the original PR;
  never push directly to a release branch and never hand-edit a divergent
  copy.
- After a release back-merges to `main`, the release branch is not reused or
  revived. The full model, including what qualifies for a fix release and how
  experimental features ship, is in [`docs/RELEASING.md`](docs/RELEASING.md).

Branch name prefixes: `feature/` (new capability), `fix/` (bug fix),
`release/` (release branches only), `docs/` (documentation-only). Other
prefixes require maintainer agreement.

## Pull request rules (strict)

1. One focused change per PR. No unrelated edits, reformatting, dependency
   bumps, or drive-by refactors in the same PR.
2. PR title uses Conventional Commit format: `feat:`, `fix:`, `chore:`,
   `docs:`, `test:`, or `refactor:` followed by an imperative summary.
3. PR base must be the correct release branch (or `main` only when no release
   branch is open for that change class). Retarget rather than merge into the
   wrong base.
4. Every PR body must state, before review is requested:
   - Summary and, for fixes, the root cause.
   - Changed files and why each is touched.
   - Testing and evidence: exact commands run and results, distinguishing
     source/host/CI evidence from image and real-hardware evidence. Never
     claim a validation class that was not actually performed.
   - `Release impact: none | patch | minor | major`
   - `Release note: one user-facing sentence, or none`
   - Linked issue, when one exists.
5. Behavioral changes require a regression test wired into the repository's
   real test runner. Do not weaken, skip, or delete tests to make CI pass.
6. CI must be green on the exact current head. After any push, wait for the
   new CI run to complete before requesting review or merging; a stale green
   run does not count. If the changed files match no workflow trigger (for
   example a documentation-only PR), the head has no applicable CI: state
   that explicitly in the PR evidence instead of waiting for a run that will
   never start, and every check that does run must still be green.
7. Review closure: at least one approving review is required. Automated agent
   reviews (e.g. Codex) must be addressed against the exact current head: fix
   or disprove every actionable finding with evidence, reply to each thread
   with the fixing commit or the reason it does not apply, and only then
   resolve the thread.
8. No force-push or history rewrite on a branch under review. Add fixup
   commits instead; never amend away reviewed history.
9. Merging is a separate, explicit authorization. Green CI, `MERGEABLE` state,
   or agent approval is not merge authorization on its own. Use merge commits
   so branch history stays auditable.
10. Never include secrets, Wi-Fi configuration, device serials, MAC addresses,
    tokens, private paths, or private manifests in branch content, PR bodies,
    or comments.
11. Cross-repository changes get one linked PR per owning repository, all
    targeting the same release version, coordinated from a product issue in
    the LibreEcho product repository.

## Versioning (one version across all repositories)

- LibreEcho uses a single product-wide SemVer version `X.Y.Z`. The canonical
  public identity is the product release `radar-puffin-vX.Y.Z`, tagged in the
  LibreEcho product repository.
- All repositories (product, build, platform/kernel tooling, Linux kernel,
  UI) participate in the same version for a given release. No repository
  publishes or documents a divergent version number.
- Release branches are named `release/X.Y.Z` with all three components,
  exactly matching the intended product tag suffix (for example
  `release/0.14.0` or `release/0.13.8`).
- Where a repository carries a machine-readable version marker (for example
  LibreEcho-UI `VERSION`), its value must equal the numeric release version
  included in the image; the release gate fails closed on mismatch.
- Bump rules:
  - `major` (X): intentional incompatible contract or migration (OTA protocol,
    data/config migration, partition/boot contract, reinstall requirement,
    unsupported rollback). While pre-1.0 this ships as a breaking release,
    which bumps Y and is marked breaking in its release note.
  - `minor` (Y): new backward-compatible capability, service, payload, or
    additive API/UI contract; reset Z to 0. Capability not yet validated on
    both targets ships off by default and is marked experimental.
  - `patch` (Z): backward-compatible bug, reliability, packaging, or security
    fix that changes the shipped artifact.
  - `none`: tests, CI-only, internal refactor, or unshipped documentation.
    Such PRs may merge without entering release notes.
- Never reuse a published version for different source or artifact identities.
  A rebuilt candidate after publication needs a new version (normally the next
  patch). Do not use a channel name (`dev`/`stable`) as a version substitute.
- At release cut, the included-fix ledger is collected from the exact component
  commits between the previous release and the release heads of all
  repositories; release notes are generated from that ledger, not from branch
  names or memory.
- The release sequence (freeze, dev build, hardware validation, stable build,
  documentation sync, announcement) is defined in
  [`docs/RELEASING.md`](docs/RELEASING.md).

## AI agent rules

- Before editing, read this file, the relevant part of `README.md`, and any
  directly applicable `CONTRIBUTING.md`; do not recursively inspect unrelated
  documentation or historical source.
- For any release task (dispatching `build-release.yml` for `dev` or `prd`,
  approving `dev-release`/`stable-release`, writing or editing release notes,
  editing a GitHub Release, or updating the install guide or website for a
  release), first read [`docs/RELEASING.md`](docs/RELEASING.md) and follow its
  order. Find or open the release's *Release checklist* issue and record each
  step's evidence there. A successful `prd` run publishes immediately, so never
  dispatch or approve it before the same heads have passed hardware validation
  as a `dev` build.
- Re-read the live branch, status, and diff before making changes.
- For an authorized edit, create or use an isolated purpose-named branch
  based on the current base selected by the branch policy above.
- Run meaningful focused checks and all applicable required checks under
  the operating contract above; broaden only when justified.
- Report exact commands and results. Do not claim image, CI, or hardware
  evidence when only host-source checks were run.
- Record the exact commit SHA that was tested; the PR head must equal the
  tested head at merge time.
- Do not merge, push to protected branches, tag releases, or perform hardware
  actions without explicit human authorization for that specific step.
