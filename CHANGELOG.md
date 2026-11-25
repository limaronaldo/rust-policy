# Changelog

## 1.3.1 (2026-11-23)

Patch hardening closing all four residual findings (`R-01`–`R-04`) from the independent `claude-fable-5-2` (`medium`) sign-off review of `1.3.0`. Guide stays at `1.3.0`.

- `P15` (`tools/policy.py`, `R-01`): fail closed on double-quoted backslash-escaped trigger scalars (e.g. `"pull_request_t\u0061rget"`) and YAML aliases (`on: *t`) under `on:`, and extend the backstop trigger scan to the entire workflow when any YAML alias is present.
- `P02` (`tools/policy.py`, `R-02`): enforce lint monotonicity against `--base` for workspace members with their own `[lints]` table (not only the root `Cargo.toml`).
- `P12` (`tools/policy.py`, `R-03`): strip HTML comments in `.github/pull_request_template.md` under `adoption.visibility = "public"` and require an actual `## Human-owned` heading plus `Responsible engineer` and `M34` sign-off fields.
- `P17` (`tools/policy.py`, `R-04`): require an explicit `manifest` exception `scope = "nested:<dir>"` for local non-workspace `Cargo.toml` directories even when referenced by an approved `[patch.*]` entry.
- Self-test suite expanded from 198 to 199 Python tests across six modules (`13 + 88 + 28 + 9 + 28 + 33`).

## 1.3.0 (2026-11-23)

Optional/OSS posture and defense-in-depth upgrades (`P2`) from the independent dual-model council review (`gpt-6.2-sol` + `claude-opus-5-5`). Guide stays at `1.3.0`.

- Public/OSS visibility posture (`tools/policy.py`, `scripts/install.py`, `templates/rust-policy.toml`, `docs/org-settings.md`):
  - Add optional `adoption.visibility = "internal" | "public"` (default `"internal"`, CLI `--visibility public`). When `"public"`, `P12` enforces a `.github/pull_request_template.md` containing a `Human-owned` disclosure section and `P16` warns when CODEOWNERS lacks a catch-all `*` rule even in standalone mode.
- Feature-matrix rustdoc (`tools/check.sh`, `.github/workflows/rust-ci.yml`, `O-07`):
  - Run `cargo doc --workspace --no-deps --locked` across every configured `feature_sets` row in both `check.sh ci` and the reusable `doc` workflow job.
- Post-execution integrity re-hash (`.github/workflows/rust-ci.yml`, `docs/threat-model.md`, `O-05`):
  - Add an `if: ${{ always() }}` step `Re-verify managed files after execution` at the end of every Cargo/consumer-code job (`clippy`, `test`, `doc`, `msrv`, `deny`, `ratchet`, `miri`) to catch tampering of `.rust-policy/` files during build/test execution.
- Conformance & installer hardening (`tools/policy.py`, `scripts/install.py`, `templates/*`, `docs/threat-model.md`, `O-06`, `O-07`, plus v1.2.0 council sign-off fixes `F-01`–`F-09` / `N-01`–`N-07` and `forja` local pilot findings):
  - `P02`: prevent removing or lowering `[workspace.lints]` / `[lints]` levels compared to `--base` without a `lint-config` exception (`F-01`/`N-06`), and canonicalize repeated `--features=` flags in `feature_set_key()` (`F-08`).
  - `P00` / `P11`: forbid `--cfg=miri` and `--cfg=test` in `[build] rustflags`, reject non-canonical `./` segments in `[crates]` paths (`F-05`), reject `[advisories] db-urls` / `db-path` overrides in `deny.toml` (`O-02`), and clear `GIT_*_PATHSPECS` in `git()`.
  - `P15`: harden structural YAML `on:` trigger extraction for unindented `- trigger` sequences, multiline flow lists/maps, anchors/tags, BOM, indented roots, and a non-`jobs:` fail-closed backstop (`F-03`/`N-01`).
  - `P16`: evaluate effective last `*` catch-all ownership (`F-06`), scope root `"."` critical crate checks to exclude unrelated workspace members (`N-05`), and fix `install.py --reviewers` for trailing ownerless `*` lines (`O-06`).
  - `P17`: reject undeclared `required-features` even when `--all-features` is configured (`F-02`) and align `workspace.exclude` exemption matching with Cargo's literal path-prefix semantics (`F-07`).
  - `P19`: count base and head symmetrically (`F-04`/`N-02`), follow git renames (`N-03`), bind `EXC-nnnn` strictly to `ignore = "..."` reason values, and recognize `#[cfg_attr(all(miri, ...), ignore)]`.
  - `P09`: prevent non-`#[cfg(test)]` `mod name;` production modules from being downgraded to test-only by a duplicate `#[cfg(test)] mod name;` (`O-07`).
  - `scripts/install.py`: support `python3 -I scripts/install.py`, recognize `[workspace.lints.rust] unsafe_code = "forbid"` in `source_edits()`, and emit a `CODEOWNERS` reminder in `--mode minimal`.
  - Templates: include `crates.critical` and safe code upholding `unsafe` preconditions in `rust-reviewer` escalation rules and explicit domain-owner sign-off in `pull_request_template.md` (`F-09`/`N-07`).
- Self-test suite expanded from 190 to 198 Python tests across six modules (`13 + 87 + 28 + 9 + 28 + 33`).

## 1.2.0 (2026-11-23)

Human-first LLM/agent governance upgrades (`P1`) from the independent dual-model council review (`gpt-6.2-sol` + `claude-opus-5-5`) against the official `rust-lang/rust` LLM Usage Policy and `rustc-dev-guide` LLM guidance. Guide advances from `1.2.1` to `1.3.0`.

- Guide (`guide/rust-guide.md`, `guide/rules.toml` — version `1.3.0`):
  - Add `M34` ("Soundness-critical code and safety prose verified by a human domain owner", `enforcement = "both"`, `exception = false`).
  - Add Section 15 ("Changes authored with agents and LLMs") and matching review checklist items establishing human-first canonical documentation, full-diff human self-review, meaningful LLM extent/purpose disclosure, soundness authorship boundaries, deterministic `clippy.toml` rules before prompt rules, cross-model advisory review, red-green bug evidence, mechanical rewrites (`M31`) and snapshot inspection discipline.
- Conformance engine (`tools/policy.py`, `P00` to `P19`):
  - `P15`: replace raw substring search for `pull_request_target` / `workflow_run` with a pure-stdlib structural YAML `on:` trigger extractor so mentions inside `run:` scripts or step names no longer trigger false positives.
  - `P16`: support optional `policy.unsafe_owners` and `crates.critical` in `rust-policy.toml` to require domain-owner CODEOWNERS coverage on soundness-critical crates (`M34`); emit a warning in reusable mode when CODEOWNERS has no catch-all `*` rule.
  - `P17`: detect tracked nested `Cargo.toml` files outside the workspace unless registered via a `manifest` exception (`nested:<dir>`).
  - `P19`: prevent per-file growth of non-Miri `#[ignore]` attributes compared to `--base` unless covered by a registered `guide` exception (`rule = "P19"`) cited in `#[ignore = "EXC-nnnn: ..."]`.
- Templates, installer & docs (`templates/*`, `scripts/install.py`, `docs/*`):
  - Upgrade `templates/pull_request_template.md` to a two-layer structure (`Human-owned` section left empty by agents + factual `What and why` / `Verification` sections).
  - Add project-owned `templates/clippy.toml` (`disallowed-methods` / `disallowed-types` starter) installed by `scripts/install.py` in full mode when neither `clippy.toml` nor `.clippy.toml` exists.
  - Update `templates/AGENTS.block.md` (`< 6 KiB`), `templates/CLAUDE.block.md`, `templates/.claude/agents/rust-reviewer.md`, `templates/.codex/agents/rust-reviewer.toml`, `templates/rust-policy.toml`, `templates/exceptions.toml`, `docs/agents.md`, `docs/org-settings.md` and `docs/rollout.md` for `M34`, cross-model advisory review, `needs-human-judgment` minimum verdict on `unsafe`/`SAFETY` diffs, and review-bot isolation rules.
- Self-test suite expanded from 184 to 190 Python tests across six modules (`12 + 84 + 25 + 9 + 27 + 33`).

## 1.1.3 (2026-11-23)

P0 hardening and regression fixes identified by a dual-model independent council review (`gpt-6.2-sol` + `claude-opus-5-5`) over 1.1.2. The guide stays at 1.2.1; no new guide MUST is introduced.

- Runner (`tools/check.sh`):
  - Pass `--no-deps` to per-package `cargo clippy -p "$pkg"` invocations in `run_clippy()` and `run_ratchet()` so `-F unsafe_code` on a safe crate never leaks into a path-dep `unsafe_allowed` workspace crate (`B-01`).
  - Sanitize snapshot runner environment variables (`unset INSTA_FORCE_PASS TRYBUILD UPDATE_EXPECT`, `export INSTA_UPDATE=no`) so local evidence cannot silently bless or force-pass snapshot tests (`O-03`).
- Lexer & conformance (`tools/policy.py`):
  - Recognize inner attributes with whitespace or comments between `#` and `!` (`# ![...]`, `#/**/![...]`) in `ATTR`, `TEST_MOD_OPEN`, `has_crate_lint()`, and `P09` (`B-02`).
  - Run bare `#[ignore]` checks (`P09`) whenever `"test"` or `"clippy"` is adopted, instead of gating them behind `"clippy"` alone (`B-03`).
  - Normalize `feature_sets.args` whitespace (`" ".join(args.split())`) in `validate_profile()` so multiline TOML strings cannot truncate reusable CI matrix rows or split standalone `feature_args` (`B-04`).
  - Split `P17` so manifest graph checks (`[workspace] exclude`, `[patch]`, `[replace]`, non-workspace local path deps in `Cargo.lock`, non-root `build.rs`) run unconditionally even before `"test"` is adopted, while target-level checks run under `"test"` (`B-05`).
  - Prevent removing a zeroed (`total == 0`) ratchet entry in `P18` unless the lint is enabled in `[workspace.lints]`/`[lints]` (or `FORBIDDEN_LINTS` / library `#![warn(missing_docs)]` / explicit `manifest` exception `ratchet:<pkg>:<lint>`) (`B-06`).
  - Protect `[[feature_sets]]` against row removal in `P02` and verify in `P17` that every target with `required-features` is enabled by at least one configured `feature_sets` row (or covered by a `manifest` exception) (`B-07`).
  - Reject `disable_all_formatting = true` in `rustfmt.toml` / `.rustfmt.toml` (`P06`, `O-02`).
- Installer, canary script & threat model (`scripts/install.py`, `scripts/prove_gates.sh`, `docs/threat-model.md`):
  - Fix `KeyError` in `install.py:source_edits()` when reinstalling over a valid profile that omits `[crates]` (`O-01`).
  - Align `prove_gates.sh` (`scenario ignored-test`) with `P09` by testing bare `#[ignore]`, update `docs/threat-model.md` for `#[ignore]` accurately, and document the residual risk of build scripts / proc-macros / tests modifying runner files as the same OS user (`B-03`, `O-05`).
- Self-test suite expanded from 173 to 184 Python tests across six modules (`12 + 79 + 24 + 9 + 27 + 33`).

## 1.1.2 (2026-11-14)

Hardening, lexer/parser accuracy, and regression fixes building on 1.1.1. The guide remains at 1.2.1; no new guide MUST is introduced.

- Lexer & conformance (`tools/policy.py`):
  - Recognize `br#...#` and `cr#...#` raw byte and C string literals so `#![...]` scanner and attribute lexer never confuse string contents with Rust code.
  - Parse attributes containing whitespace around `::`, raw `r#` identifiers, raw-string `reason = r#"..."#`, and sibling `cfg_attr` suppressions after `ignore`.
  - Add `clippy::missing_safety_doc` to `FORBIDDEN_LINTS` so crate-level or inline suppressions cannot silence safety documentation requirements.
  - Fix `P16` `codeowners_regex` so single-segment `*` wildcards no longer match across `/`, distinguishing `*` from `**` or directory rules.
  - Run `check_references` (`docs/rust-policy/ratchet.toml` and `.cargo/config.toml`) unconditionally in `P10` even when conformance targets no changed packages.
  - Enforce `P04` standalone `[lints]` validation across workspace members and decouple `check_profiles` (`M27`) from unrelated workspace lint tables.
  - Extend `P17` manifest checks to inspect implicit `src/main.rs` binary targets, `dylib` `doctest = false` settings, and `[patch]` / `[replace]` overrides unless registered as `manifest` exceptions.
  - Harden ratchet configuration validation against booleans mistaken for counts and require `build-finished` marker integrity on compiler JSON output.
  - Require strict `.cargo/config.toml` allowlisting (`P05` removes `"source"` and bans `target.*.rustflags` / `target.*.rustdocflags`) and reject empty/mismatched `branches:` in `.github/workflows/rust-policy.yml` (`P15`).
  - Report malformed TOML syntax and non-table profile sections across policy/config files as structured `P00`–`P11` findings instead of Python tracebacks.
  - Extend `P12` to scan nested `AGENTS.override.md` files recursively and validate `opencode.json` JSON syntax and `rust-policy.toml` instruction registration, and scan `.cargo/config` in `P10`.
  - Tighten `exceptions.toml` validation (`P10`) for rule-to-kind binding (`P05`/`P15`/`P17`), strict `lint` field rules (rejecting blank/padded lints), pure TOML `date` validation (rejecting datetimes), duplicate-ID invalidation, and support for `kind = "manifest"` (`P17`) in `templates/exceptions.toml`.
- Runner & reusable CI (`tools/check.sh`, `.github/workflows/rust-ci.yml`):
  - Sanitize compiler, wrapper, linker, runner, alias, and dev/test profile environment variables and export `LC_ALL=C` while preserving `CARGO_TARGET_DIR`.
  - Pass `-F clippy::missing_safety_doc` explicitly to per-package Clippy invocations.
  - Scope external `dep/feat` feature flags to each declaring workspace package while preserving multi-aliases and forwarding unknown dependencies for Cargo diagnostics.
  - Fix `ratchet -- --update` and `ratchet -- --init` CLI parsing and preserve single-feature-set safety guarantees.
  - Include `hashFiles('rust-policy.toml')` in `Swatinem/rust-cache` cache keys.
- Installer, admin scripts & templates (`scripts/*`, `templates/*`, `docs/rollout.md`):
  - Scope `detect()` strictly to each workspace member's own directory, include `"dylib"` targets in source edits, reuse `policy.lex()` / `ATTR` / `attr_end()` in `insert_inner_attr()`, record only actually installed files in `lock.toml` with warnings for stale optional files, preserve minimal-mode `opencode.json` symlink safety, and guard against malformed `adoption` tables.
  - Fix `prove_edit.py` UTF-8 safe JSON escape translation and harden `prove_gates.sh` multi-header HTTP status parsing, `@org/slug` CODEOWNERS normalization, and `statusCheckRollup` polling.
  - Exit with status `64` on unknown flags in `release.py`; update `templates/AGENTS.block.md` (`clippy::missing_safety_doc`), `templates/exceptions.toml` (`manifest` / `P17` + scope comment), and `docs/rollout.md` (`v1.1.2`, `--component miri,rust-src`, and the four CLI-forbidden lints); isolate Git identity and clean temporary Cargo target directories in self-tests.
- Verification suite expanded from 141 to 173 Python tests across six modules (`12 + 72 + 23 + 9 + 25 + 32`).

## 1.1.1 (2026-10-31)

Correctness and enforcement fixes identified by an independent review of the supplied 1.1.0 kit.
The guide stays at 1.2.1; no new guide MUST is introduced.

- Standalone GitHub Actions supports its declared local-lock trust model. `check.sh ci` requires
  `--base REF`; conformance runs first, unavailable supplied bases fail, and rollback comparisons
  reject malformed baseline policy instead of silently skipping it.
- Reusable CI selects immutable event base revisions, and ratchet/Miri coverage follows every
  configured feature row. Clippy allowances are scoped to each package; workspace feature names
  are resolved for the package and its workspace dependency closure.
- Ratchet allowances cannot move between files. Failed compilation/metadata and worsening counts
  cannot update the baseline. Automatic `--init`/`--update` is limited to single-row profiles;
  multi-row baseline changes require reviewed evidence from every row.
- Exception approval binds the lint to the same entry's scope, workspace lint configuration needs
  global scope, Miri exemptions require a guide M17 entry, and future-dated exceptions are rejected.
- Custom test paths no longer classify their production siblings as test-only. Per-package dev/test
  overrides cannot disable overflow checks or debug assertions without the exact exception.
- Installer validates all planned writes before applying them, rejects redirected destinations,
  preserves shebang/BOM and existing profiles, validates creation arguments, and no longer writes
  a zero workflow pin. Minimal adoption preserves untouched instruction-file symlinks.
- Ruleset updates paginate repository-owned results and refuse duplicates. Permission probes
  distinguish expected denials from inconclusive errors, use a disabled ruleset payload, and isolate
  the agent's Git HTTPS credentials from an owner's SSH or credential-helper configuration.
- MSRV resolves to the exact declared minimum patch. Toolchain checks capture rustc output before
  matching it, preventing SIGPIPE under pipefail from falsely reporting an installed compiler missing.
- The FFI example now executes five contract cases under ordinary tests and Miri.
- Self-test CI installs cargo-deny explicitly. Agent templates follow staged adoption and report
  commit/diff evidence honestly. Example, release digests, rollout and review report are refreshed.
- Added installer, shell-orchestration, policy and administration regressions. Exact verification
  results and the remaining live-GitHub boundary are recorded in the included review report.

## 1.1.0 (2026-10-31)

Adoption in stages, for repositories that already have their own CI and agent harness.

- `adoption.checks` in `rust-policy.toml`: the gate enforces only the adopted checks; the list may
  only grow (P02). `adoption.ci = "standalone"` runs everything through `check.sh ci` from the
  repository's own CI; `adoption.agents = "external"` leaves `AGENTS.md` and `CLAUDE.md` alone and
  ships `docs/rust-policy/AGENTS.rust.md`. `install.py --mode minimal` sets both and writes only
  new files.
- Ratchet (P18): `docs/rust-policy/ratchet.toml` counts a lint per file with `--force-warn`, so a
  source `#[allow]` cannot hide one; counts only go down, each ratchet cites an exception, and a
  `missing_docs` ratchet satisfies P08 while a documentation backlog is paid down.
- `crates.miri_exempt`, with an M17 exception naming the alternative check, for unsafe code Miri
  cannot run (system calls, C libraries).
- P16 now evaluates the effective CODEOWNERS (last matching line) for every file of the policy
  surface, so a project keeps its own CODEOWNERS layout; no managed block is required.
- P17 accepts `autobins = false` and friends when every conventional file is declared as a target,
  and `harness = false` on benches. `#[ignore = "reason"]` is accepted; a bare `#[ignore]` is not.

## 1.0.0 (2026-10-30)

First release, designed by a three-model council (Claude Fable 5.1, Claude Sonnet 5.5, GPT-5.6-Sol).

- Guide 1.2.1: stable MUST ids M01 to M33; corrected two lint statements after testing on Rust 1.97
  (`allow_attributes_without_reason` covers `#[expect]`; an unknown lint in Cargo `[lints]` does not
  fail `-D warnings`).
- Reusable workflow with plan, conformance, fmt, clippy, test, doc, msrv, deny, miri and gate; every
  job verifies the vendored tools against sums embedded in the pinned workflow.
- `check.sh` shared by CI, humans and agents; `policy.py` with conformance checks P00 to P17.
- Installer, release script, ruleset files and apply script, live experiment script (not yet run).
- Agent instructions for Codex, Claude Code and OpenCode, plus a reviewer agent for Claude Code and
  Codex.
- Hardened after the council's round 2 review: P17 (workspace membership, test and doctest opt-outs,
  build script path), exceptions bound to lint and scope, item-level lint groups, `#[ignore]` and
  module-wide `rustfmt::skip`, `-F unsafe_code` outside `unsafe_allowed`, color-independent log
  checks, `cargo deny --workspace --all-features`, Miri per feature set, nested Clippy, rustfmt and
  Cargo configuration, CODEOWNERS self-protection and `policy.owners`, the caller pinned to
  `mbras/rust-policy`, an audited `[build] rustflags` allowlist, and installer fixes.
