# rust-policy

The central repository that adopts the Rust Super Guide across MBRAS repositories. It owns the guide,
the reusable CI workflow, the tools every consumer runs, the templates the installer writes, the
ruleset, and the tests that prove all of it. Version 1.2.0, guide 1.3.0.

## How it works

| Central source | Installed consumer file | Integrity model |
| --- | --- | --- |
| `guide/rust-guide.md` | `docs/rust-policy/guide.md` | Managed bytes |
| `tools/check.sh`, `tools/policy.py` | `.rust-policy/` | Managed bytes |
| Agent block templates | `AGENTS.md`, `CLAUDE.md` | Managed block; surrounding project content preserved |
| Reviewer templates | `.claude/agents/`, `.codex/agents/` | Managed bytes when installed |
| Caller template | `.github/workflows/rust-policy.yml` | Reusable workflow pinned to a full commit SHA |


1. Every pull request runs the consumer's caller, which runs `rust-ci.yml` from a pinned commit of
   this repository. GitHub fetches that file itself, so the change under test cannot alter it.
2. `rust-ci.yml` carries the sha256 of every managed file. Each job that runs them verifies the
   consumer's copies first, so the tools that run are the ones this release shipped. The policy
   repository can stay private: no extra token or second checkout, only the Actions access setting.
3. The jobs: `plan`, `conformance` (P00 to P18), `fmt`, `clippy` and `test` per feature set, `doc`,
   `msrv` per feature set, `deny`, `miri` per feature set (when a crate may use unsafe), and `gate`.
   Clippy forbids `unsafe_code` in every crate outside `crates.unsafe_allowed`, for all targets.
4. Only `rust-policy / gate` is required by the ruleset. It runs even when other jobs fail or are
   skipped and fails unless every job succeeded (Miri may be skipped only when the plan says no
   crate uses unsafe), because GitHub counts a skipped required check as passing.
5. Humans and agents run `.rust-policy/check.sh ci --base <actual-base-commit>` locally.
   It follows staged adoption; `fast` and `full` are explicit broader presets.

The project decides its choices in `rust-policy.toml` (toolchains, MSRV, edition, feature sets,
library crates, crates allowed to use unsafe) and its deviations in
`docs/rust-policy/exceptions.toml`. Both are reviewed by the policy owners through CODEOWNERS.

## Layout

| Path | Purpose |
| --- | --- |
| `VERSION`, `CHANGELOG.md` | Kit version; consumers record it in `.rust-policy/lock.toml` |
| `guide/rust-guide.md` | Canonical guide, version 1.2.1, MUST ids M01 to M33 |
| `guide/rules.toml` | Per MUST id: enforced by CI, review or both; whether an exception is allowed |
| `tools/check.sh` | The command runner for CI, humans and agents |
| `tools/policy.py` | Profile validation, CI plan, managed-file verification, conformance P00 to P18 |
| `templates/` | Everything `install.py` writes into a consumer |
| `.github/workflows/rust-ci.yml` | The reusable workflow, with the managed sums block |
| `.github/workflows/self-test.yml` | Runs `tests/run.sh` on this repository |
| `scripts/install.py` | Installs or upgrades rust-policy in a consumer checkout |
| `scripts/release.py` | Writes the sums and version into `rust-ci.yml` (`--check` for CI) |
| `scripts/apply-ruleset.sh` | Creates or updates the consumer ruleset with `gh` |
| `scripts/prove_gates.sh`, `prove_edit.py` | Live experiments in a canary repository (written, not yet run) |
| `rulesets/` | Repository ruleset and an organization variant targeted by custom property |
| `tests/` | Self-tests: workflow invariants, gate truth table, conformance, plan, real cargo runs |
| `docs/` | Rollout, organization settings, agent instruction loading, threat model |

## Adopt in a repository

Prerequisites: Python 3.11 or newer (`tomllib`), git, rustup, and a repository whose `Cargo.toml`
(workspace or package) is at the root. Repositories with Rust in a subdirectory are not supported
in 1.2.0. Instruction files managed in full mode must be regular files. Minimal/external mode
leaves its existing instruction-file symlinks alone. Every managed write rejects symlinked
destinations or ancestor directories, and the installer validates its planned writes before applying them.

```bash
git clone git@github.com:mbras/rust-policy.git
cd rust-policy
git switch --detach v1.2.0
python3 scripts/install.py --target ../my-repo --owners @mbras/rust-policy-owners \
  --reviewers @mbras/engineers --policy-sha "$(git rev-parse HEAD)" --edit-sources
cd ../my-repo
# Use the actual target branch revision; main is only this example's branch name.
base_sha="$(git rev-parse origin/main)"
bash .rust-policy/check.sh ci --base "$base_sha"
git switch -c adopt-rust-policy
git add -A
git commit -m "Adopt rust-policy 1.2.0"
```

`--reviewers` adds a catch-all CODEOWNERS line, so every pull request needs a human from that team
(keep agent identities out of it). If the repository was committed from Windows, run
`git update-index --chmod=+x .rust-policy/check.sh` once.

Review the generated `rust-policy.toml` (the installer guesses library and unsafe crates), open the
pull request, and apply the ruleset as described in `docs/rollout.md`. `install.py --help` lists the
options; it never commits, and it creates project-owned files only when they are missing.

## Two ways to adopt

| | `--mode full` (default) | `--mode minimal` |
| --- | --- | --- |
| For | a repository without its own CI policy or agent harness | a repository that has both, in this organization or another |
| CI | the caller pins `rust-ci.yml` of this repository; the gate is `rust-policy / gate` | the repository's own CI runs `bash .rust-policy/check.sh ci --base REF` |
| Tool integrity | sha256 embedded in the pinned workflow, checked before each run | CODEOWNERS on `/.rust-policy/`, like any CI script of the repository; P01 compares with the lock |
| Agent instructions | managed blocks in `AGENTS.md` and `CLAUDE.md` | untouched; `docs/rust-policy/AGENTS.rust.md` for the project's own read order |
| CODEOWNERS | a managed block, appended last | untouched; P16 reports the policy files the project's own lines leave unowned |
| Files changed | many | only new files: `.rust-policy/`, `docs/rust-policy/`, `rust-policy.toml` |

In both modes `adoption.checks` decides what the gate enforces today, and the list only grows
(P02). Conformance checks that guard a job wait until that job is adopted: P11 (`deny.toml`) with
`deny`, P09 and P04 with `clippy`, P17 with `test`, and the `rust-version` part of P06 with `msrv`.
A repository can start with `--checks fmt,clippy,deny` and add the rest one pull request at a time.

A reusable workflow in a private repository is callable only from the same organization. For
another organization, either make the policy repository public (the workflow and tools carry no
secrets), fork the kit into that organization and set `POLICY_REPO` in `scripts/kit.py` before
`scripts/release.py`, or use `--mode minimal`.

## Ratchets: legacy debt without a crate-wide exception

A crate-level `#![allow(missing_docs)]` frees new code along with the old. A ratchet instead counts
one lint per file and lets the counts only go down:

```toml
# docs/rust-policy/ratchet.toml
[[ratchet]]
lint = "missing_docs"
package = "engram-core"
exception = "EXC-0004"          # the approved register entry, renewed as the count falls
[ratchet.files]
"crates/engram-core/src/storage/mod.rs" = 41
"crates/engram-core/src/search/mod.rs" = 17
```

- `check.sh ratchet` runs Clippy with `--force-warn` for each ratcheted lint, so a source
  `#[allow]` cannot hide an occurrence, and fails when any file exceeds its count; a new file starts
  at zero. `check.sh ratchet --init` fills a new entry (written without `[ratchet.files]`) with
  today's counts; `check.sh ratchet --update` lowers them after a cleanup and never raises them.
  These automatic writes support a single configured feature row only; a multi-row profile is
  rejected before writing. For multiple rows, collect evidence from every supported row and submit
  a reviewed per-file baseline that covers them all. Do not remove matrix rows to bypass this check.
- The clippy job allows a ratcheted lint (`-A`) only for its registered package. Other packages
  remain strict. Reusable and standalone CI measure the ratchet in every configured feature row.
- P18 refuses any per-file count that grows against the base branch (including a budget moved
  from another file), a ratchet removed while it still
  counts warnings, a lint group, a lint without an exception path, and a ratchet without a valid
  exception for that lint and package. A `missing_docs` ratchet satisfies P08 for that library.

A failed compiler invocation, missing metadata or a worsening file leaves the baseline unchanged.
Paths in `[ratchet.files]` are relative to the repository root. A count budget bounds totals per
file; it does not identify individual legacy diagnostics. Replacing an old violation with a new one
in the same file may preserve the count, so review still checks newly introduced code.

## Workspace features and minimum compiler

Clippy checks each package separately to keep unsafe permission and ratchet allowances scoped.
Feature prefixes use a direct dependency's Cargo alias when it is renamed. Cargo cannot accept a
feature path through multiple dependency levels. Forward required transitive features through each
package's declared direct dependency features; do not rely on another workspace member implicitly
enabling a feature as a side effect of a whole-workspace build. Projects relying on that feature
unification need an explicit profile/design adjustment before adopting this package-scoped lint run.
This condition fails compilation visibly; it does not silently waive a check.

A two-component MSRV such as `1.85` means `1.85.0` for validation. Both the CI plan and the local
runner select that exact minimum rather than rustup's latest patch for the minor channel.

## Base revisions in standalone CI

`check.sh ci` requires `--base REF`, also for local runs. Supply the actual target revision of the
change, ensure it is fetched, and run the script in the same checkout being evaluated. An unavailable
explicit base fails. Use the pull request's base SHA for PRs, the merge group's base SHA for queues,
and the push event's `before` SHA for pushes. A scheduled/manual run should compare the fetched
default branch. An arbitrary `HEAD^1` is not a substitute for these event semantics.

Standalone mode uses the project's own lock and CODEOWNERS trust boundary, including on GitHub
Actions. Reusable mode still requires checksums from its pinned workflow. The reusable workflow
sets and validates its base itself. The full GitHub feature/OS matrix remains the CI's responsibility;
a local standalone process runs the feature rows on its current host, not on every declared OS.

`--mode`, `--checks`, toolchain and feature flags are creation defaults for `install.py`. Existing
profiles are preserved. Review a profile change explicitly before reinstalling to promote adoption.
Full installation from an archive needs a real released `--policy-sha`; it no longer fabricates a
zero commit. Minimal mode can record missing source provenance as an empty value with a warning.
The shipped example uses an unmistakable fixture SHA and must be reinstalled with the published
commit before it is used as a real consumer.

## Unsafe code that Miri cannot run

Miri does not execute system calls or C libraries. List such crates in `crates.miri_exempt` (a
subset of `unsafe_allowed`) and register an exception with rule M17 whose reason names the
alternative check, for example integration tests against the real system. Clippy still forbids
`unsafe` everywhere outside `unsafe_allowed`, and the review-only MUST rows for unsafe still apply.

## Change the policy

1. Edit `tools/`, `guide/` or `templates/`, bump `VERSION` and the version in the BEGIN line of both
   block templates, and add a `CHANGELOG.md` entry. Major: a stricter MUST or check; minor: additive;
   patch: fixes.
2. `python3 scripts/release.py`, then `tests/run.sh`.
3. Merge, tag `v<VERSION>`, and run `install.py --policy-sha <merge commit>` in each consumer.

Conformance P02 refuses a lower version in `.rust-policy/lock.toml` than on the base branch, and P15
refuses a caller that points anywhere but `mbras/rust-policy`. Pointing the caller at an older
commit is a change to `.github/`, so a policy owner must approve it: before approving a caller
change, check that the pinned SHA is a release tag
(`gh api repos/mbras/rust-policy/git/ref/tags/v<version> --jq .object.sha`).

## Policy choices beyond the guide

- `Cargo.lock` is committed in every repository, libraries included (P14), and CI builds `--locked`.
  The guide requires it only for deployed applications; MBRAS chose one rule for all.
- `.cargo/config.toml` rustflags are ignored by the checks. Flags a project really needs go in
  `rust-policy.toml` `[build] rustflags` (only `--cfg=`, `-Ctarget-cpu=`, `-Ctarget-feature=`).
- A new RustSec advisory fails the `deny` job of every open pull request until the dependency is
  updated or a `deny-ignore` exception is approved. Measure the noise in the pilot before relaxing it.
- A skipped test says why: `#[ignore = "needs ffmpeg on PATH"]`; a bare `#[ignore]` fails P09.
  Ignored doctests fail the test step (use `no_run`).

## Exceptions

Add an entry to `docs/rust-policy/exceptions.toml` in the pull request that needs it, cite its id
where the deviation lives (`reason = "EXC-0007: ..."`), and get a policy owner's approval. Entries
expire within 180 days, and each one covers only the lints in its `lint` field and the files under
its `scope`. MUST rows without an exception path (M03, M04, M05, M07, M08, M12 to M16,
M18 to M22, M24, M25, M28, M31, M32) are refused.

## Verification status

This 1.2.0 release (guide 1.3.0) passes 190 Python self-tests across all six test modules (`12` workflow tests,
`84` policy/conformance tests, `25` installer tests, `9` admin script tests, `27` check-runner
orchestration tests, and `33` real-Cargo integration tests), alongside `release.py --check`,
`actionlint`, and `shellcheck`.

The 1.2.0 suite and the complete `example-consumer` CI flow (`bash .rust-policy/check.sh ci --base <base>`)
were verified locally on `darwin/arm64` with `rustc 1.97.0`, `clippy 0.1.97`, exact MSRV `1.85.0`,
Miri on `nightly-2026-10-15`, `cargo-deny 0.19.0`, `actionlint 1.7.12`, `shellcheck 0.11.0`, and
`Python 3.14.7`. Full results, fixed findings, and boundaries are recorded in `../REVISAO.md`. Run
`tests/run.sh` before publishing this release.

No workflow, ruleset or repository permission has been applied to a live GitHub repository as part
of this release. The pilot and canary experiments in `docs/rollout.md` remain required.
