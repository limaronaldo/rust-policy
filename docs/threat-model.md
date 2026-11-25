# What the gate stops, and what it does not

The main risk is not an attacker but a change, often written by an agent, that makes a failing check
pass by weakening the check. Each row names a way to do that and the control that catches it.
"Tested" means `tests/run.sh` proves it; "review" means a human code owner must notice it. Rows
marked (r2) were found by the council's adversarial review of the first candidate and fixed.

| Attempt | Control | Evidence |
| --- | --- | --- |
| Crate-level `#![allow(clippy::all)]` | P09 needs a registered EXC id | tested |
| Item-level allow of a lint group (`clippy::all`, `warnings`) (r2) | P09 group rule | tested |
| Reuse an approved exception id for another lint or crate (r2) | P09 and P11 bind each id to its `lint` and `scope` | tested |
| `#[allow]` or `#[expect]` without a reason | `-F clippy::allow_attributes_without_reason` | tested |
| Allow a no-exception lint in source | `-F` on the command line turns it into error E0453 | tested |
| Lower a lint in `[workspace.lints]` | P04 needs a `lint-config` exception; `-F` beats Cargo levels | tested |
| Unknown lint name in `[lints]` (passes `-D warnings`) | check.sh fails on warning E0602, with colored output too (r2) | tested |
| `--cap-lints` or other rustflags in `.cargo/config.toml` | check.sh sets RUSTFLAGS from the profile allowlist, which replaces config rustflags; P05 | tested |
| `runner = "true"` so tests never run; `[alias] clippy = "check"`; `[env]` | P05 allowlist of config keys | tested |
| `.cargo/config.toml`, `clippy.toml` or `rustfmt.toml` in a subdirectory (r2) | P05, P06; owners at any depth | tested |
| Member leaves the workspace lints | P03 | tested |
| `[workspace] exclude` or a local path crate outside the workspace (r2) | P17 reads `Cargo.lock` and the workspace table | tested |
| `test = false`, `doctest = false`, `harness = false`, `autotests = false` (r2) | P17 | tested |
| Build script under another name, `build = "src/x.rs"` (r2) | P17 requires `build.rs` at the package root | tested |
| `unsafe` in a crate outside `unsafe_allowed`, in any target (r2) | Clippy runs those crates with `-F unsafe_code` | tested |
| Forge `#![warn(missing_docs)]` in a string or nested module (r2) | P08 reads only crate-root attributes outside strings | tested |
| Bare `#[ignore]` on a failing test; growth of non-Miri `#[ignore]`; `ignore` on a doctest | P09 rejects bare `#[ignore]` without a reason; P19 rejects per-file growth of non-Miri `#[ignore]` vs `--base` without a registered `guide` exception (`rule = "P19"`); check.sh fails on ignored doctests | tested |
| Module-wide `#[rustfmt::skip]` (r2) | P09 | tested |
| Nightly-only rustfmt options (stable ignores them) | check.sh fails on "Warning: can't set" | tested |
| Edit `rust-toolchain.toml` to change the compiler | CI sets `RUSTUP_TOOLCHAIN` from the profile; P06 | tested (P06) |
| Lower `toolchain.stable` or the rust-policy version | P02 compares with the base branch | tested |
| Edit `.rust-policy/check.sh` or `policy.py` | sha256 from the pinned workflow, checked before any run | tested (P01) |
| Edit the managed block of `AGENTS.md` or `CLAUDE.md` | P01 | tested |
| Run conformance in CI without the workflow's sums (r2) | P01 fails closed when `GITHUB_ACTIONS` is set | tested |
| Expired or invalid exception | P10 | tested |
| `deny.toml` ignore without a reason, `yanked` lowered, graph narrowed | P11 | tested |
| A dependency only some members or features use (r2) | `cargo deny --workspace --all-features` | tested (runs) |
| Re-own the policy surface in CODEOWNERS (r2) | P16: block last, every pattern owned by exactly `policy.owners`; the CODEOWNERS file is itself owned | tested |
| Point the caller at another repository's workflow (r2) | P15 pins `mbras/rust-policy`; org Actions allowlist; owner review of `.github/` | tested (P15) |
| Pin the caller to an unreleased commit of `mbras/rust-policy` | owners verify the SHA is a release tag; agents have no write access there | review |
| Rewrite the caller, or add a workflow that reports a fake `rust-policy / gate` | code owner review of `.github/`; agent tokens without the Workflows permission | review |
| Post a commit status named `rust-policy / gate` | the ruleset binds the check to the GitHub Actions app | prove_gates.sh |
| Push to the default branch, approve, merge, edit rulesets | agent identity permissions, empty bypass list, `--reviewers` catch-all | prove_gates.sh |
| Suppressions produced by a proc macro or `include!` | review | review |
| Build script, proc macro or test tampers with `check.sh`/`policy.py` after initial sha256 verification (same OS user) | CODEOWNERS on `build.rs`; post-execution `if: always()` managed-file re-hash in `rust-ci.yml` (1.3); review of build scripts, proc macros and tests (in-process subversion remains a residual risk under same-UID execution) | tested (re-hash) + review |
| Code under `#[cfg(any())]` or an undeclared feature (never compiled) | review; the feature matrix covers declared sets | review |
| Rows marked "Review only" in the MUST table | the reviewer agent and the human reviewer | review |
| Drop a check from `adoption.checks`, or go from reusable to standalone (1.1) | P02 compares with a trusted base revision; reusable CI uses the PR/merge-group base SHA or push-before SHA and fails if it is unavailable; standalone CI supplies its base | tested |
| Raise a ratchet, remove it with warnings left, or hide occurrences with `#[allow]` (1.1) | P18; `--force-warn` in every configured feature/OS row of the ratchet step | tested |
| Exempt an unsafe crate from Miri (1.1) | an M17 exception naming the alternative check | tested |
| Edit `.rust-policy/` in `--mode minimal` (1.1) | the project's CODEOWNERS (P16 reports it if unowned); P01 compares with the lock only | review |

The strongest remaining control for the "review" rows is an organization required workflow, which
runs from this repository regardless of the pull request's files (Enterprise Cloud; see
`org-settings.md`). Without it, the gate is only as strong as the human review of `.github/`.

Permission-proof results are valid only for the tested, authenticated canary identity. The proof
script does not treat an authentication error, missing endpoint, malformed request or transport
failure as evidence that a permission is absent. Offline tests exercise that distinction; they
do not replace running the pilot against GitHub.
