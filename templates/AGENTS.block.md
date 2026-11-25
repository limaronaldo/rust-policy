<!-- BEGIN rust-policy 1.3.2 (managed by rust-policy; edits inside this block fail CI) -->
# Rust policy for agents and humans

This repository adopts the Rust Super Guide through rust-policy. These rules summarize human-authored guidance (`docs/rust-policy/guide.md`, section 15) and apply to every change, delegated task and review.

## Read first
1. `rust-policy.toml`: this repository's profile (toolchains, MSRV, edition, feature sets, library crates, `unsafe_allowed`, optional `unsafe_owners` and `critical` crates). Never guess these values.
2. `docs/rust-policy/exceptions.toml`: approved deviations. Only the ids listed there exist.
3. `docs/rust-policy/guide.md`: read the MUST table (M01 to M34), then touched sections: organization 2, lints 3, naming 4, API 5, errors 6, ownership 7, unsafe/FFI 8, async 9, macros 10, docs 11, tests 12, security/deps 13, review 14, agents/LLMs 15.
4. Project architecture docs and ADRs. They win where the guide leaves a choice open, never waiving a MUST.

Rows marked "Review only" in the MUST table are checked by no tool: uphold and review them explicitly.

## Commands (CI runs the same script)
- `.rust-policy/check.sh ci --base <base-commit>`: conformance and every adopted check across `feature_sets`. Use the actual base commit so downgrade, ratchet and ignore-growth checks (`P02`, `P18`, `P19`) compare it. Run before committing and reporting.
- `fast` and `full` are broader presets that may include unadopted checks.
- `.rust-policy/check.sh miri`: focused run on `unsafe_allowed` crates using the pinned nightly. Unsupported Miri tests use `#[cfg_attr(miri, ignore = "reason")]` with the required M17 exception or alternative check.
- If a command cannot run here, say so. Record the tested commit (or base + uncommitted diff), rerun after edits, and report a check as passed only for the tested state.

## Hard rules
- Policy surface (out of scope unless named): `AGENTS.md`, `CLAUDE.md`, `opencode.json`, `rust-policy.toml`, `rust-toolchain.toml`, `rustfmt.toml`, `clippy.toml`, `deny.toml`, `.cargo/`, `.github/`, `.claude/`, `.codex/`, `.rust-policy/`, `docs/rust-policy/`, CODEOWNERS, every `build.rs`, and manifest `[lints]`, `[workspace.lints]`, `[profile.*]`, `[patch]`, `[workspace] members`/`exclude`, and target `test`/`doctest`/`harness`/`build` keys. Stop and report if blocked; do not weaken rules.
- Never weaken checks: no crate/module `#![allow]`/`#![expect]` without an EXC id, no `--cap-lints`/RUSTFLAGS/`.cargo/config.toml` overrides, no bare `#[ignore]` or unapproved `#[ignore]` growth (`P19`), no ignored doctests (`no_run` allowed), no ratchet growth, no `continue-on-error`/`|| true`, and no blind snapshot `--bless`/`cargo insta accept`/`TRYBUILD=overwrite`.
- Local suppressions use `#[expect(lint, reason = "...")]` when reliable, else `#[allow(lint, reason = "...")]`. Wider deviations need an approved `docs/rust-policy/exceptions.toml` entry cited first (`reason = "EXC-0007: ..."`).
- Safety contracts have no exception (`M11`, `M14`, `M15`, `M34`). CI forbids lowering `unsafe_op_in_unsafe_fn`, `clippy::undocumented_unsafe_blocks`, `clippy::missing_safety_doc` and `clippy::allow_attributes_without_reason`. Agents only propose `// SAFETY:`/`# Safety` prose; a human domain owner must author or verify them (`M34`). `unsafe` lives only in `unsafe_allowed`.
- Expected failures return `Result`, absence returns `Option`, panics are for violated invariants. Public library items document `# Errors`, `# Panics` and `# Safety`.
- New direct dependencies need purpose, license, features and maintenance status in the PR. External/paid calls keep a stable id and reconciliation path (guide section 9).
- Bug fixes cite a red-green test (fails on base, passes on fix); mass rewrites use syntax tools (`cargo fix`, `clippy --fix`, `ast-grep`) in dedicated commits (`M31`). Leave `Human-owned` PR sections empty for the responsible engineer.
- Agents do not push to the default branch, merge or approve PRs, or change repo settings, rulesets, secrets or tokens.

## Delegation
Subagents may start without this file. Every brief MUST state: goal and acceptance criteria; base commit; files/crates in scope; policy surface out of scope; applicable guide sections, MUST and EXC ids; exact commands; report format; and a verbatim copy of "Hard rules". Use a separate read-only reviewer (`rust-reviewer`)—preferably from a different model family when available—for unsafe, FFI, async, dependency and public API changes. Agent review is advisory and never replaces human approval.

## Reports
Implementer: what changed and why; commit SHA (or base SHA + diff); commands run and results; checks not run and why; red-green test or snapshot diffs inspected; EXC ids; remaining risks.

Reviewer: one line per finding, `rule | MUST or SHOULD | path:line | evidence | consequence | fix`; then range/diff reviewed, commands run, areas not reviewed, and verdict (`changes-required`, `no-findings`, `needs-human-judgment`; at least `needs-human-judgment` for `unsafe`/`SAFETY`). Cite only rules in the guide or profile.
<!-- END rust-policy -->
