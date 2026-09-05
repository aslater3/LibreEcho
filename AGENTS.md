# LibreEcho Product Repository — Agent Instructions

This repository is the product and release home for LibreEcho: landing content,
cross-repository public documentation, release notes, and release tooling. Read
`CONTRIBUTING.md` for change-ownership routing before non-trivial work.

## Agent operating contract

These rules take priority over the procedural sections below for normal task
execution.

1. The user's current request defines the scope. Work only on the named problem
   and the files needed to solve it. Do not fix adjacent bugs, refactor nearby
   code, clean up unrelated issues, or improve the architecture unless asked.

2. Before editing, identify:
   - the requested outcome;
   - the files likely to change;
   - the smallest relevant validation command.
   Do not begin a broader investigation without explaining why it is necessary.

3. Make the smallest change that satisfies the request. Do not expand the task
   because you notice a possible improvement or a failure in another subsystem.

4. Stop when the requested acceptance criterion is met. A successful focused
   test is a stopping point, not an invitation to search for more work.

5. Run one relevant, bounded, focused host check by default. Do not run the
   aggregate suite, full image builds, release workflows, CI polling, hardware
   validation, flashing, rebooting, watchers, servers, or background processes
   unless the user explicitly requests that operation.

6. Every command must have a bounded timeout. If a command hangs, exceeds its
   timeout, or begins doing work outside the request, stop it and report the
   command and observed state. Do not retry indefinitely.

7. If a test or check fails outside the requested scope, do not fix it as part of
   the current task. Report the exact failure and whether it blocks the
   requested acceptance criterion.

8. If the work would touch another repository, another subsystem, more files
   than originally expected, or a second unrelated root cause, stop and ask
   before expanding scope.

9. Do not create or switch branches, commit, push, open a pull request, merge,
   tag, wait for CI, or perform hardware actions unless the user explicitly
   requests that specific action.

10. The branching, pull-request, CI, release, and versioning sections below
    apply when the user requests those workflows. They do not create permission
    to perform them automatically.

11. If the user asks only for a review, diagnosis, explanation, or plan, do not
    edit files. Return findings and proposed changes instead.

# Branching, Pull Requests, and Versioning

These rules govern how changes flow through this repository. They apply equally
to human contributors and AI coding agents (Claude Code, Codex, Hermes, Cursor,
Copilot, and any similar tool). Repository-specific build, test, and safety
rules elsewhere in this repository remain in force unchanged; this section
governs branch, PR, and release flow only.

## Branch model

```text
feature/<purpose>   fix/<issue-or-purpose>
       |                   |
       | PR                | PR
       v                   v
release/X.Y.0        release/X.Y.Z
 (major release)      (minor release)
       \______  _________/
               v
              main         (release branch merges back when release-ready)
```

- `main` is the integration branch. Nothing is committed directly to `main` or
  to any `release/*` branch; every change lands through a pull request.
- **Features** start from the current `main` (or from the active release branch
  when the feature is scoped to that release) on a branch named
  `feature/<short-purpose>` and merge into the next **major release branch**,
  `release/X.(Y+1).0` (for example `release/0.14.0`).
- **Fixes** follow the same flow and merge into the corresponding **minor
  release branch**, `release/X.Y.(Z+1)` (for example `release/0.13.8`).
- Because the current line is pre-1.0 (`0.Y.Z`), a `Y` bump is a *major*
  product release and a `Z` bump is a *minor* (fix) release; this is the
  terminology used for release branches throughout. In SemVer terms a `Y`
  bump is a MINOR bump and a `Z` bump is a PATCH bump. The `Release impact:`
  label always uses SemVer terms: a pre-1.0 product major release (`Y` bump)
  is `Release impact: minor`, and a pre-1.0 product minor release (`Z` bump)
  is `Release impact: patch`.
- A release branch is cut from `main`, receives its features and fixes by PR,
  and merges back into `main` when it is validated and release-ready.
- If the release branch for a fix already exists, base the fix PR on that
  release branch rather than on `main`. If a fix was merged into `main`
  before the release branch was cut, carry it over by cherry-picking the
  merged commit onto a new `fix/<purpose>` branch cut from the release
  branch and opening a PR against the release branch that references the
  original PR; never push directly to a release branch and never hand-edit a
  divergent copy.
- After a release merges to `main`, subsequent work starts from `main` again.
  A new release branch is cut for the next release; release branches are not
  reused or revived.

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
    data/config migration, partition/boot contract, unsupported rollback).
  - `minor` (Y): new backward-compatible capability, service, payload, or
    additive API/UI contract; reset Z to 0.
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

## AI agent rules

- Before editing, read this file, the relevant part of `README.md`, and any
  directly applicable `CONTRIBUTING.md`; do not recursively inspect unrelated
  documentation or historical source.
- Re-read the live branch, status, and diff before making changes.
- Work only in a purpose-named branch when an edit has been explicitly
  requested and the current branch is appropriate for that task.
- Run the smallest relevant focused check first. Broader validation is
  conditional on the user's request or explicit PR preparation.
- Report exact commands and results. Do not claim image, CI, or hardware
  evidence when only host-source checks were run.
- Record the exact commit SHA that was tested; the PR head must equal the
  tested head at merge time.
- Do not merge, push to protected branches, tag releases, or perform hardware
  actions without explicit human authorization for that specific step.
