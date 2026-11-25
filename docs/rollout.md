# Rollout

## Before the pilot (once)

1. Confirm the GitHub plan: rulesets on private repositories need Pro, Team or Enterprise Cloud.
2. Create the team `rust-policy-owners` with at least two people. An author cannot approve their own
   pull request, so one owner cannot unblock their own policy changes. Give the team Write access to
   every consuming repository (CODEOWNERS ignores owners without write access).
3. Create `mbras/rust-policy` from this kit, run `tests/run.sh`, tag `v1.2.0`.
4. Apply the settings in `org-settings.md`, above all the agent identities.

## Pilot (two weeks, one repository plus a canary)

Pick a small active repository, ideally with a crate that uses `unsafe`, so the Miri job and the
no-exception lints get exercised. Create a throwaway canary repository with the same files.

1. Install: `scripts/install.py --target ../pilot --owners @mbras/rust-policy-owners --reviewers @mbras/engineers --policy-sha "$(git rev-parse HEAD)" --edit-sources`.
   Choose a pilot with a root `Cargo.toml` and `rust-version` in its manifests (the installer lists
   what is missing).
2. Pick the Miri nightly: a date for which `rustup toolchain install nightly-YYYY-MM-DD --component miri,rust-src`
   succeeds. Write it in `rust-policy.toml`. Tests Miri cannot run, such as FFI calls into C, get
   `#[cfg_attr(miri, ignore = "foreign call")]` and keep running in the normal test job.
3. Open the adoption pull request. On its first run, confirm:
   - the check is named exactly `rust-policy / gate`:
     `gh api repos/O/R/commits/SHA/check-runs --jq '.check_runs[] | [.name, .app.id] | @tsv'`;
   - the app id is the GitHub Actions app (`gh api /apps/github-actions --jq .id`);
   - the gate's run summary shows the job table.
4. `scripts/apply-ruleset.sh O/pilot disabled`, read a week of results, then
   `scripts/apply-ruleset.sh O/pilot active`.
5. Also confirm on GitHub: the gate on a `merge_group` run, the check name `prove_gates.sh` reads,
   CODEOWNERS patterns without a leading slash matching at any depth, and the MSRV and Miri jobs.
6. Run `scripts/prove_gates.sh` against the canary with the agent identity's token. Every line must
   end in `ok`. Fix the script where GitHub's output differs from what it expects; it has not run yet.

## Existing code that fails the checks

Do not block all work, and do not weaken the gate. In separate pull requests:

1. One commit with `cargo fmt --all` alone.
2. The four rules without exceptions first (`unsafe_op_in_unsafe_fn`, `undocumented_unsafe_blocks`,
   `clippy::missing_safety_doc`, reasons on every `#[allow]` and `#[expect]`): CI forbids them on the command line.
3. A reviewed `cargo clippy --fix` (guide M31: automated fixes are reviewed).
4. Whatever remains gets narrow suppressions or registered, expiring exceptions: a crate-level
   `#![expect(clippy::x, reason = "EXC-0012: legacy, tracked in #123")]` with a `lint` entry, or a
   `lint-config` entry for an `allow` in `[workspace.lints]`. New code stays strict.
   `cargo clippy --message-format=json` grouped by lint name gives the list.
5. If the MSRV build fails, set `msrv` to the oldest toolchain that honestly works and lower it later.
6. Activate the ruleset once the gate is green on the default branch.

The register then works as a burn-down list: entries expire within 180 days, and conformance fails
when one expires, so each one is either fixed or consciously renewed in review.

## A repository with its own CI and agent harness

Use `--mode minimal` and adopt in short pull requests, ordered so that each one passes on its own.
Land them before work that spreads across many files (parallel branches would conflict).

1. Install with `--mode minimal --checks fmt,clippy,deny --libraries=` and add the policy files
   P16 lists to the project's own CODEOWNERS (no default owner is needed). Call
   `bash .rust-policy/check.sh ci --base "$RUST_POLICY_BASE_SHA"` from the project's CI script.
   Set `RUST_POLICY_BASE_SHA` from the actual PR base, merge-group base or push-before event SHA;
   fetch that commit before checking. Missing or unavailable bases are errors. Scheduled/manual
   runs compare the fetched default branch. The README explains the event-specific choices.
2. `deny.toml`: `yanked`, `unknown-registry` and `unknown-git` set to `deny` (P11).
3. Reasons on every `#[allow]` and `#[expect]`; module-wide allows narrowed or turned into
   ratchets (P09). check.sh then forbids `allow_attributes_without_reason`,
   `undocumented_unsafe_blocks`, `clippy::missing_safety_doc` and `unsafe_op_in_unsafe_fn` with `-F`, which a source `#[allow]`
   cannot lower (a `-D` on the command line can be).
4. Remove the unsafe code that has safe equivalents; list what remains in `unsafe_allowed`, and in
   `miri_exempt` with an M17 exception when it is system calls or C.
5. Add `test` and `msrv` to `adoption.checks`: `rust-version` in every manifest, ignored doctests
   become `no_run`, every `#[ignore]` gets a reason.
6. Documentation: a `missing_docs` ratchet for each published library, so new items
   can be reviewed against a non-growing per-file budget while the backlog shrinks; then `doc`
   in `adoption.checks`. Counts cannot distinguish replacing one old violation with a new one
   in the same file. Automatic ratchet initialization/update supports one configured feature row;
   multi-row baselines require reviewed evidence from every row, without removing matrix coverage.

## Remaining repositories

One adoption pull request per repository, each with the same steps. With many repositories, give
them the custom property `rust-policy = enforced` and apply `rulesets/org-default-branch.json` at the
organization (`gh api --method POST /orgs/ORG/rulesets --input ...`, after the same app id
substitution that `apply-ruleset.sh` does).

## Upgrades and drift

- A release bumps `VERSION`; run `install.py --policy-sha <commit>` in each consumer and merge the PR.
- Each consumer records its version in `.rust-policy/lock.toml` and its pin in the caller's `uses:`.
  A drift report is one `gh api` call per repository:
  `gh api repos/O/R/contents/.rust-policy/lock.toml --jq .content | base64 -d | grep ^version`.
- Dependabot or Renovate keep the third-party action SHAs current in this repository only; consumers
  get them through releases.

## Later extensions

Merge queue (the caller already handles `merge_group`), `cargo semver-checks` for published libraries,
`cargo hack --feature-powerset`, macOS and Windows legs, nextest, and the Enterprise-only organization
"Require workflows to pass" rule described in `org-settings.md`.
