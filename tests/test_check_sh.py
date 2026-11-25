"""check.sh against real cargo runs: the baseline passes and each defusal attempt fails.

Needs the toolchain recorded in the fixture profile (1.97.0) with clippy and rustfmt, and network or a
warm cargo cache for the fixture's single dependency (itoa). Slow: each case compiles the fixture.
"""
import atexit
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from helpers import commit, consumer, edit, minimal_consumer  # noqa: E402

TARGET = pathlib.Path(tempfile.mkdtemp(prefix="rp-check-target-"))
atexit.register(shutil.rmtree, TARGET, True)


def check(repo, step, *args, color="always", extra_env=None):
    # CI used to force colors; check.sh must work whatever the caller sets.
    env = dict(os.environ, CARGO_TARGET_DIR=str(TARGET), CARGO_TERM_COLOR=color)
    env.pop("RUSTFLAGS", None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([str(repo / ".rust-policy/check.sh"), step, *(["--", *args] if args else [])], cwd=repo,
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class CheckScript(unittest.TestCase):
    def expect(self, step, mutate, want_fail=True, needle=None, args=()):
        repo = consumer()
        mutate(repo)
        commit(repo)
        rc, out = check(repo, step, *args)
        if want_fail:
            self.assertNotEqual(rc, 0, out[-3000:])
        else:
            self.assertEqual(rc, 0, out[-3000:])
        if needle:
            self.assertIn(needle, out)
        shutil.rmtree(repo.parent, ignore_errors=True)

    def test_baseline_fast_passes(self):
        self.expect("fast", lambda r: None, want_fail=False)

    def test_fmt(self):
        self.expect("fmt", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t.replace("    input.parse()", "input.parse( )")))
        self.expect("fmt", lambda r: edit(r, "rustfmt.toml", lambda t: t + 'imports_granularity = "Crate"\n'), needle="cannot apply")

    def test_clippy_warning_fails(self):
        self.expect("clippy", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t + "\n/// Probe.\npub fn probe() -> u8 {\n    return 1;\n}\n"))

    def test_unknown_lint_in_cargo_toml_fails(self):
        self.expect("clippy", lambda r: edit(r, "Cargo.toml", lambda t: t + 'this_lint_does_not_exist = "warn"\n'), needle="unknown to this toolchain")

    def test_source_allow_of_forbidden_lint_fails(self):
        self.expect("clippy", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t.replace(
            "#![forbid(unsafe_code)]", '#![forbid(unsafe_code)]\n#![allow(clippy::allow_attributes_without_reason, reason = "x")]')), needle="E0453")

    def test_allow_and_expect_without_reason_fail(self):
        for attr in ("allow", "expect"):
            with self.subTest(attr):
                self.expect("clippy", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t + f"\n/// Probe.\n#[{attr}(clippy::needless_return)]\npub fn probe() -> u8 {{\n    return 1;\n}}\n"))

    def test_cargo_config_rustflags_cannot_cap_lints(self):
        def cap(r):
            edit(r, ".cargo/config.toml", lambda t: '[build]\nrustflags = ["--cap-lints", "allow"]\n')
            edit(r, "crates/core/src/lib.rs", lambda t: t + "\n/// Probe.\npub fn probe() -> u8 {\n    return 1;\n}\n")
        self.expect("clippy", cap)

    def test_unsafe_without_safety_comment_fails(self):
        self.expect("clippy", lambda r: edit(r, "crates/ffi/src/lib.rs", lambda t: t.replace(
            "            // SAFETY: checks establish non-nullness and the size bound;\n            // the caller supplies the remaining documented guarantees.\n", "")))

    def test_failing_test_fails(self):
        self.expect("test", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t.replace('parse_port("70000").is_err()', 'parse_port("70000").is_ok()')))

    def test_ignored_doctest_fails(self):
        self.expect("test", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t.replace("/// ```\n/// # fn main()", "/// ```ignore\n/// # fn main()")), needle="ignored doctests")

    def test_broken_doc_link_fails(self):
        self.expect("doc", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t.replace("/// Parses a decimal port number.", "/// Parses a decimal port number, see [`NoSuchItem`].")))

    def test_missing_docs_fails(self):
        self.expect("clippy", lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t + "\npub fn undocumented() {}\n"))

    def test_wrong_toolchain_is_refused(self):
        self.expect("clippy", lambda r: edit(r, "rust-policy.toml", lambda t: t.replace('stable = "1.97.0"', 'stable = "1.98.0"')), needle="rust-policy.toml records 1.98.0")


    def test_unsafe_outside_the_allowlist_fails_in_every_target(self):
        forged = lambda r: edit(r, "crates/core/src/lib.rs", lambda t: t.replace("#![forbid(unsafe_code)]\n", "") +
                                '\nconst _M: &str = "#![forbid(unsafe_code)]";\n\n/// Reads.\n#[must_use]\npub fn first(v: &[u8]) -> u8 {\n    // SAFETY: callers pass a non-empty slice.\n    unsafe { *v.as_ptr() }\n}\n')
        self.expect("clippy", forged, needle="unsafe")
        in_test = lambda r: edit(r, "crates/core/tests/raw.rs", lambda t: "#[test]\nfn raw() {\n    let v = [1u8];\n    // SAFETY: v has one element.\n    assert_eq!(unsafe { *v.as_ptr() }, 1);\n}\n")
        self.expect("clippy", in_test, needle="unsafe")

    def test_unsafe_crate_still_passes(self):
        self.expect("clippy", lambda r: None, want_fail=False)

    def test_deny_passes_on_the_fixture(self):
        self.expect("deny", lambda r: None, want_fail=False)


    def ratcheted(self, repo, count):
        undocumented = "\npub fn a() {}\n\npub fn b() {}\n"
        edit(repo, "crates/core/src/lib.rs", lambda t: t.replace("#![warn(missing_docs)]\n", "").replace("#[cfg(test)]", undocumented.lstrip() + "\n#[cfg(test)]"))
        edit(repo, "docs/rust-policy/ratchet.toml", lambda t: f'[[ratchet]]\nlint = "missing_docs"\npackage = "sample-core"\nexception = "EXC-0001"\n[ratchet.files]\n"crates/core/src/lib.rs" = {count}\n')
        edit(repo, "docs/rust-policy/exceptions.toml", lambda t: t + '[[exception]]\nid = "EXC-0001"\nkind = "lint"\nrule = "M06"\nlint = "missing_docs"\nscope = "crates/core"\nreason = "backlog"\napproved_in = "x"\ncreated = 2026-10-31\nexpires = 2027-03-31\n')

    def test_ratchet_holds_the_backlog(self):
        self.expect("ratchet", lambda r: self.ratcheted(r, 2), want_fail=False)
        self.expect("ratchet", lambda r: self.ratcheted(r, 1), needle="ratchet allows 1")
        hidden = lambda r: (self.ratcheted(r, 1), edit(r, "crates/core/src/lib.rs", lambda t: t.replace("//! Port parsing", '#![allow(missing_docs, reason = "hide")]\n//! Port parsing')))
        self.expect("ratchet", hidden, needle="ratchet allows 1")

    def test_ratchet_update_only_lowers(self):
        repo = consumer()
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        self.ratcheted(repo, 5)
        self.single_feature_profile(repo)
        commit(repo)
        rc, out = check(repo, "ratchet", "--update")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn('"crates/core/src/lib.rs" = 2', (repo / "docs/rust-policy/ratchet.toml").read_text())

    def test_ratchet_init_records_todays_counts_for_this_package_only(self):
        repo = consumer()
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        self.ratcheted(repo, 0)
        self.single_feature_profile(repo)
        edit(repo, "docs/rust-policy/ratchet.toml", lambda t: t.split("[ratchet.files]")[0])
        edit(repo, "crates/app/src/main.rs", lambda t: t + "\n/// Not a library item.\nfn _unused() {}\n")
        commit(repo)
        rc, out = check(repo, "ratchet", "--init")
        self.assertEqual(rc, 0, out[-2000:])
        text = (repo / "docs/rust-policy/ratchet.toml").read_text()
        self.assertIn('"crates/core/src/lib.rs" = 2', text)
        self.assertNotIn("crates/app", text)

    def test_clippy_step_leaves_ratcheted_lints_to_the_ratchet(self):
        self.expect("clippy", lambda r: self.ratcheted(r, 2), want_fail=False)

    @staticmethod
    def single_feature_profile(repo):
        edit(repo, "rust-policy.toml", lambda t: t.split("[[feature_sets]]", 1)[0] +
             '[[feature_sets]]\nid = "default"\nargs = ""\n')

    def test_ratchet_allowance_does_not_leak_to_another_package(self):
        def mutate(repo):
            self.ratcheted(repo, 2)
            edit(repo, "crates/ffi/src/lib.rs", lambda t: t + "\npub fn undocumented() {}\n")
        self.expect("clippy", mutate, needle="missing documentation")

    def test_workspace_feature_is_valid_when_clippy_selects_other_packages(self):
        def mutate(repo):
            edit(repo, "crates/core/Cargo.toml", lambda t: t + "\n[features]\nprobe = []\n")
            edit(repo, "crates/core/src/lib.rs", lambda t: t.replace("#[cfg(test)]",
                 '/// Feature-only API.\n#[cfg(feature = "probe")]\npub fn feature_only() {}\n\n#[cfg(test)]'))
            edit(repo, "crates/app/src/main.rs", lambda t: t +
                 "\nfn _uses_workspace_feature() {\n    sample_core::feature_only();\n}\n")
        for feature_args in (("--features=probe",), ("--all-features",)):
            with self.subTest(args=feature_args):
                self.expect("clippy", mutate, want_fail=False, args=feature_args)

    def test_workspace_feature_supports_renamed_dependency(self):
        def mutate(repo):
            edit(repo, "crates/app/Cargo.toml", lambda t: t.replace(
                 'sample-core = { path = "../core" }',
                 'renamed-core = { package = "sample-core", path = "../core" }'))
            edit(repo, "crates/core/Cargo.toml", lambda t: t + "\n[features]\nprobe = []\n")
            edit(repo, "crates/core/src/lib.rs", lambda t: t.replace("#[cfg(test)]",
                 '/// Feature-only API.\n#[cfg(feature = "probe")]\npub fn feature_only() {}\n\n#[cfg(test)]'))
            edit(repo, "crates/app/src/main.rs", lambda t: t.replace("sample_core::", "renamed_core::") +
                 "\nfn _uses_workspace_feature() {\n    renamed_core::feature_only();\n}\n")
        for feature_args in (("--features=probe",), ("--all-features",)):
            with self.subTest(args=feature_args):
                self.expect("clippy", mutate, want_fail=False, args=feature_args)

    def test_workspace_features_do_not_forward_impossible_transitive_prefixes(self):
        def mutate(repo):
            edit(repo, "crates/leaf/Cargo.toml", lambda t:
                 '[package]\nname = "sample-leaf"\nversion = "0.1.0"\nedition.workspace = true\n'
                 'rust-version.workspace = true\nlicense.workspace = true\npublish.workspace = true\n'
                 '[lints]\nworkspace = true\n[features]\nprobe = []\n')
            edit(repo, "crates/leaf/src/lib.rs", lambda t:
                 '//! Leaf.\n#![forbid(unsafe_code)]\n/// Leaf API.\npub fn leaf() {}\n')
            edit(repo, "crates/core/Cargo.toml", lambda t: t +
                 '\n[dependencies]\nsample-leaf = { path = "../leaf" }\n')
            # Refresh the lock before --locked checks because this introduces a workspace member.
            subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=repo, check=True,
                           capture_output=True, text=True)
        for feature_args in (("--features=probe",), ("--all-features",)):
            with self.subTest(args=feature_args):
                self.expect("clippy", mutate, want_fail=False, args=feature_args)

    def test_ci_ratchet_catches_new_warning_in_second_feature_row(self):
        def mutate(repo):
            self.ratcheted(repo, 2)
            edit(repo, "crates/core/Cargo.toml", lambda t: t + "\n[features]\nprobe = []\n")
            edit(repo, "crates/core/src/lib.rs", lambda t: t.replace("#[cfg(test)]",
                 '#[cfg(feature = "probe")]\npub fn new_undocumented() {}\n\n#[cfg(test)]'))
            edit(repo, "rust-policy.toml", lambda t: re.sub(r"^checks = .*", 'checks = ["clippy"]',
                 t.split("[[feature_sets]]", 1)[0], flags=re.M) +
                 '[[feature_sets]]\nid = "default"\nargs = ""\n'
                 '[[feature_sets]]\nid = "probe"\nargs = "--features=probe"\n')
        self.expect("ci", mutate, needle="ratchet allows 2", args=("--base", "HEAD"))

    def test_ratchet_compile_failure_never_lowers_baseline(self):
        repo = consumer()
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        self.ratcheted(repo, 5)
        self.single_feature_profile(repo)
        edit(repo, "crates/core/src/lib.rs", lambda t: t + "\ncompile_error!(\"broken evidence\");\n")
        before = (repo / "docs/rust-policy/ratchet.toml").read_bytes()
        rc, out = check(repo, "ratchet", "--update")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("could not compile", out)
        self.assertEqual((repo / "docs/rust-policy/ratchet.toml").read_bytes(), before)

    def test_ratchet_update_rejects_incomplete_feature_matrix(self):
        self.expect("ratchet", lambda r: self.ratcheted(r, 5),
                    needle="single configured feature set", args=("--update",))

    def test_standalone_ci_runs_only_adopted_checks(self):
        repo = minimal_consumer()
        rc, out = check(repo, "ci", "--base", "HEAD")
        self.assertEqual(rc, 0, out[-3000:])
        self.assertIn("== test (not adopted yet)", out)
        self.assertIn("all adopted checks passed", out)

    def test_missing_safety_doc_suppression_is_forbidden(self):
        self.expect(
            "clippy",
            lambda r: edit(
                r,
                "crates/ffi/src/lib.rs",
                lambda t: t + '\n/// Reads a byte.\n#[allow(clippy::missing_safety_doc, reason = "waive")]\n'
                '#[must_use]\npub unsafe fn raw(p: *const u8) -> u8 {\n'
                '    // SAFETY: caller guarantees p is valid for reads.\n'
                '    unsafe { *p }\n}\n',
            ),
            needle="E0453",
        )

    def test_env_compiler_and_target_rustflags_cannot_cap_lints(self):
        repo = consumer()
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        edit(repo, "crates/core/src/lib.rs", lambda t: t + "\n/// Probe.\npub fn probe() -> u8 {\n    return 1;\n}\n")
        commit(repo)
        rc, out = check(
            repo,
            "clippy",
            extra_env={
                "CARGO_BUILD_RUSTFLAGS": "--cap-lints=allow",
                "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS": "--cap-lints=allow",
                "CARGO_TARGET_AARCH64_APPLE_DARWIN_RUSTFLAGS": "--cap-lints=allow",
                "RUSTC_BOOTSTRAP": "1",
                "CARGO_ALIAS_CLIPPY": "check",
            },
        )
        self.assertNotEqual(rc, 0, out[-3000:])
        self.assertIn("needless_return", out)

    def test_external_dep_feature_is_scoped_to_declaring_package(self):
        def add_ext(repo):
            edit(repo, "crates/core/vendor/ext/Cargo.toml",
                 lambda _: '[package]\nname = "ext"\nversion = "0.1.0"\nedition = "2024"\n[features]\nprobe = []\n')
            edit(repo, "crates/core/vendor/ext/src/lib.rs", lambda _: "")
            edit(repo, "crates/core/Cargo.toml", lambda t: t + '\n[dependencies]\next = { path = "vendor/ext" }\n')
            subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=repo, check=True,
                           capture_output=True, text=True)

        self.expect("clippy", add_ext, want_fail=False, args=("--features=ext/probe",))
        self.expect("clippy", lambda r: None, want_fail=True, args=("--features=no_such_dep/probe",))

    def test_ratchet_update_respects_single_feature_row(self):
        repo = consumer()
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        self.ratcheted(repo, 5)
        edit(repo, "crates/core/Cargo.toml", lambda t: t + "\n[features]\nprobe = []\n")
        edit(repo, "crates/core/src/lib.rs", lambda t: t.replace(
            "#[cfg(test)]", '#[cfg(feature = "probe")]\npub fn feat_undoc() {}\n\n#[cfg(test)]'))
        edit(repo, "rust-policy.toml", lambda t: t.split("[[feature_sets]]", 1)[0] +
             '[[feature_sets]]\nid = "probe"\nargs = "--features=probe"\n')
        commit(repo)
        rc, out = check(repo, "ratchet", "--update")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn('"crates/core/src/lib.rs" = 3', (repo / "docs/rust-policy/ratchet.toml").read_text())

    def test_safe_crate_depending_on_unsafe_allowed_crate_passes_clippy(self):
        def link_ffi(repo):
            edit(repo, "crates/app/Cargo.toml", lambda t: t + 'sample-ffi = { path = "../ffi" }\n')
            edit(repo, "crates/app/src/main.rs",
                 lambda t: t.replace("fn main() {", "fn main() {\n    let _ = sample_ffi::demo_rust_process;\n"))
            subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=repo, check=True,
                           capture_output=True, text=True)

        self.expect("clippy", link_ffi, want_fail=False)

        def add_unsafe_to_safe_app(repo):
            link_ffi(repo)
            edit(repo, "crates/app/src/main.rs",
                 lambda t: t.replace("#![forbid(unsafe_code)]\n", "") + "\nfn bad() { unsafe {} }\n")

        self.expect("clippy", add_unsafe_to_safe_app, want_fail=True, needle="unsafe-code")


if __name__ == "__main__":
    unittest.main(verbosity=1)
