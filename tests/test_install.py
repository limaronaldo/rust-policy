"""Installer regressions against real local git repositories and cargo metadata (no network)."""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

KIT = pathlib.Path(__file__).resolve().parents[1]
INSTALL = KIT / "scripts/install.py"
PIN = "1" * 40  # valid syntax; these tests never call GitHub


@unittest.skipUnless(shutil.which("cargo"), "cargo is required for installer metadata")
class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rp-install-")
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        self.repo = self.home / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "Cargo.toml").write_text(
            '[package]\nname = "installer-proof"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\n')
        (self.repo / "src/main.rs").write_text("fn main() {}\n")
        self.run_cmd("git", "init", "-q", "-b", "main")
        self.run_cmd("cargo", "generate-lockfile", "--offline")
        self.run_cmd("git", "add", "-A")
        self.run_cmd("git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")

    def run_cmd(self, *args, check=True, cwd=None):
        return subprocess.run(args, cwd=cwd or self.repo, capture_output=True, text=True, check=check)

    def install(self, *args, pin=PIN, script=INSTALL):
        cmd = [sys.executable, str(script), "--target", str(self.repo), "--owners", "@org/core"]
        if pin is not None:
            cmd += ["--policy-sha", pin]
        return self.run_cmd(*cmd, *args, check=False)

    def snapshot(self):
        out = {}
        for p in self.repo.rglob("*"):
            rel = p.relative_to(self.repo)
            if ".git" in rel.parts or "target" in rel.parts:
                continue
            if p.is_symlink():
                out[str(rel)] = ("symlink", os.readlink(p))
            elif p.is_file():
                out[str(rel)] = (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
        return out

    def assert_rejected_without_changes(self, before, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before, result.stdout + result.stderr)

    def test_ancestor_symlink_is_rejected_before_any_install_writes(self):
        outside = self.home / "outside"
        outside.mkdir()
        (outside / "guide.md").write_text("keep this outside file\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs/rust-policy").symlink_to(outside, target_is_directory=True)
        before = self.snapshot()
        result = self.install("--mode", "minimal")
        self.assert_rejected_without_changes(before, result)
        self.assertEqual((outside / "guide.md").read_text(), "keep this outside file\n")
        self.assertFalse((self.repo / ".rust-policy").exists())

    def test_file_symlink_is_rejected_before_any_install_writes(self):
        outside = self.home / "outside.py"
        outside.write_text("keep this outside file\n")
        (self.repo / ".rust-policy").mkdir()
        (self.repo / ".rust-policy/policy.py").symlink_to(outside)
        before = self.snapshot()
        result = self.install("--mode", "minimal")
        self.assert_rejected_without_changes(before, result)
        self.assertEqual(outside.read_text(), "keep this outside file\n")
        self.assertFalse((self.repo / ".rust-policy/check.sh").exists())

    def test_minimal_keeps_existing_instruction_symlinks_untouched(self):
        (self.repo / "shared.md").write_text("existing project harness\n")
        for name in ("AGENTS.md", "CLAUDE.md"):
            (self.repo / name).symlink_to("shared.md")
        result = self.install("--mode", "minimal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertTrue((self.repo / name).is_symlink())
            self.assertEqual(os.readlink(self.repo / name), "shared.md")
        self.assertEqual((self.repo / "shared.md").read_text(), "existing project harness\n")
        self.assertFalse((self.repo / ".github/workflows/rust-policy.yml").exists())

    def test_invalid_creation_inputs_are_rejected_without_changes(self):
        for args in (("--checks", "typo"), ("--checks", "fmt,fmt"), ("--checks", "fmt,,clippy"),
                     ("--stable", "stable"), ("--msrv", "nope"), ("--edition", "2018"),
                     ("--nightly", "nightly"), ("--nightly", "nightly-2026-99-99"),
                     ("--libraries", ".,."), ("--libraries", "missing")):
            with self.subTest(args=args):
                before = self.snapshot()
                result = self.install(*args)
                self.assert_rejected_without_changes(before, result)

    def test_full_install_requires_a_usable_pin_before_writing(self):
        # Copying the kit without .git models the distributed archive independently of this checkout.
        detached = self.home / "detached-kit"
        shutil.copytree(KIT, detached, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for pin in (None, "0" * 40):
            with self.subTest(pin=pin):
                before = self.snapshot()
                result = self.install(pin=pin, script=detached / "scripts/install.py")
                self.assert_rejected_without_changes(before, result)
                self.assertIn("--policy-sha", result.stdout + result.stderr)

    def test_shebang_stays_first_when_editing_crate_attributes(self):
        source = self.repo / "src/main.rs"
        source.write_text('#!/usr/bin/env rust-script\n//! Crate documentation.\nfn main() {}\n')
        self.run_cmd("cargo", "check", "--offline")
        result = self.install("--edit-sources")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.read_text().startswith("#!/usr/bin/env rust-script\n"))
        self.assertIn("#![forbid(unsafe_code)]", source.read_text())
        self.run_cmd("cargo", "check", "--offline")

    def test_byte_order_mark_stays_first_when_editing_a_shebang_crate(self):
        source = self.repo / "src/main.rs"
        source.write_text('\ufeff#!/usr/bin/env rust-script\nfn main() {}\n')
        self.run_cmd("cargo", "check", "--offline")
        result = self.install("--edit-sources")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.read_text().startswith("\ufeff#!/usr/bin/env rust-script\n"))
        self.run_cmd("cargo", "check", "--offline")

    def test_unsafe_in_strings_comments_and_identifiers_does_not_grant_permission(self):
        (self.repo / "src/main.rs").write_text(
            '// unsafe {}\n/* outer /* unsafe */ nested */\n'
            'fn main() { let unsafe_label = r###"unsafe"###; println!("unsafe {unsafe_label}"); }\n')
        result = self.install("--mode", "minimal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = tomllib.loads((self.repo / "rust-policy.toml").read_text())
        self.assertEqual(profile["crates"]["unsafe_allowed"], [])
        self.assertNotIn("nightly", profile["toolchain"])

    def test_real_unsafe_code_is_detected(self):
        (self.repo / "src/main.rs").write_text("fn main() { unsafe { core::ptr::read_volatile(&1); } }\n")
        result = self.install("--mode", "minimal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = tomllib.loads((self.repo / "rust-policy.toml").read_text())
        self.assertEqual(profile["crates"]["unsafe_allowed"], ["."])

    def test_existing_main_branch_is_used_instead_of_feature_checkout(self):
        self.run_cmd("git", "switch", "-qc", "feature/adopt")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        caller = (self.repo / ".github/workflows/rust-policy.yml").read_text()
        self.assertIn('branches: ["main"]', caller)

    def test_remote_head_wins_over_conventional_local_branch(self):
        self.run_cmd("git", "update-ref", "refs/remotes/origin/trunk", "HEAD")
        self.run_cmd("git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('branches: ["trunk"]', (self.repo / ".github/workflows/rust-policy.yml").read_text())

    def test_ambiguous_default_branch_requires_an_explicit_choice_without_changes(self):
        self.run_cmd("git", "branch", "-m", "feature/adopt")
        before = self.snapshot()
        result = self.install()
        self.assert_rejected_without_changes(before, result)
        self.assertIn("--default-branch", result.stdout + result.stderr)

    def test_two_conventional_branches_require_an_explicit_choice(self):
        self.run_cmd("git", "branch", "master")
        before = self.snapshot()
        self.assert_rejected_without_changes(before, self.install())

    def test_branch_is_serialized_as_one_yaml_scalar(self):
        result = self.install("--default-branch", "release,old")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('branches: ["release,old"]', (self.repo / ".github/workflows/rust-policy.yml").read_text())

    def test_invalid_branch_is_rejected_without_changes(self):
        before = self.snapshot()
        result = self.install("--default-branch", "bad branch")
        self.assert_rejected_without_changes(before, result)

    @unittest.skipIf(os.name == "nt", "executable bits are not portable on Windows")
    def test_reinstall_repairs_executable_mode_and_is_otherwise_idempotent(self):
        first = self.install("--mode", "minimal")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        script = self.repo / ".rust-policy/check.sh"
        script.chmod(0o644)
        second = self.install("--mode", "minimal")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertTrue(os.access(script, os.X_OK))
        before = self.snapshot()
        third = self.install("--mode", "minimal")
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("0 file(s) changed", third.stdout)

    def test_reinstall_preserves_existing_profile_and_explains_creation_defaults(self):
        first = self.install("--mode", "minimal", "--checks", "fmt")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = (self.repo / "rust-policy.toml").read_bytes()
        second = self.install("--mode", "full", "--checks", "test", "--stable", "1.98.0")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual((self.repo / "rust-policy.toml").read_bytes(), before)
        self.assertFalse((self.repo / ".github/workflows/rust-policy.yml").exists())
        self.assertIn("creation defaults", second.stdout)

    def test_project_config_and_instruction_content_outside_managed_block_is_preserved(self):
        originals = {"deny.toml": "# existing dependency policy\n", "rustfmt.toml": 'edition = "2024"\n',
                     "opencode.json": json.dumps({"instructions": ["CONTRIBUTING.md"]}) + "\n",
                     "AGENTS.md": "# Existing harness\nKeep our instructions.\n"}
        for rel, text in originals.items():
            (self.repo / rel).write_text(text)
        original_source = (self.repo / "src/main.rs").read_bytes()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for rel, text in originals.items():
            if rel == "AGENTS.md":
                self.assertTrue((self.repo / rel).read_text().endswith(text))
            else:
                self.assertEqual((self.repo / rel).read_text(), text)
        self.assertEqual((self.repo / "src/main.rs").read_bytes(), original_source)

    def test_dry_run_does_not_mutate_a_package_that_is_also_a_workspace_root(self):
        manifest = self.repo / "Cargo.toml"
        manifest.write_text(manifest.read_text() + "\n[workspace]\n")
        before = self.snapshot()
        result = self.install("--edit-sources", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.install("--edit-sources")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = tomllib.loads(manifest.read_text())
        self.assertTrue(installed["lints"]["workspace"])
        self.assertEqual(installed["workspace"]["lints"]["rust"]["unsafe_op_in_unsafe_fn"], "deny")
        self.run_cmd("cargo", "metadata", "--format-version", "1", "--no-deps", "--offline")

    def test_detect_excludes_nested_member_unsafe_and_includes_dylib(self):
        (self.repo / "src/main.rs").unlink()
        (self.repo / "src/lib.rs").write_text("/// Safe root library.\npub fn root_safe() -> u8 { 1 }\n")
        (self.repo / "Cargo.toml").write_text(
            '[package]\nname = "installer-proof"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\n\n'
            '[lib]\ncrate-type = ["dylib"]\n\n'
            '[workspace]\nmembers = [".", "crates/ffi"]\n'
        )
        ffi = self.repo / "crates/ffi"
        (ffi / "src").mkdir(parents=True)
        (ffi / "Cargo.toml").write_text(
            '[package]\nname = "ffi"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\n'
        )
        (ffi / "src/main.rs").write_text("unsafe fn raw() {}\nfn main() { unsafe { raw(); } }\n")
        self.run_cmd("cargo", "generate-lockfile", "--offline")
        result = self.install("--mode", "minimal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = tomllib.loads((self.repo / "rust-policy.toml").read_text())
        self.assertEqual(profile["crates"]["unsafe_allowed"], ["crates/ffi"])
        self.assertEqual(profile["crates"]["libraries"], ["."])

    def test_insert_inner_attr_handles_multiline_attrs_and_inner_block_docs(self):
        cases = [
            (
                "multiline_cfg_attr",
                '#![cfg_attr(\n    all(),\n    doc = "has ]\nin string"\n)]\n/// Outer doc.\npub fn f() {}\n',
                ')]',
                '/// Outer doc.',
            ),
            (
                "inner_block_doc",
                '//! Crate header.\n/*! Inner block\n */\n/// Outer item.\npub fn f() {}\n',
                ' */',
                '/// Outer item.',
            ),
            (
                "non_doc_prefix_before_inner_attr_and_outer_block_doc",
                '//// divider\n/**/\n/*** note */\n#![allow(dead_code, reason = "test")]\n/** Outer block doc. */\npub fn f() {}\n',
                'reason = "test")]',
                '/** Outer block doc. */',
            ),
        ]
        for name, initial, after_marker, before_marker in cases:
            with self.subTest(case=name):
                self.setUp()
                (self.repo / "src/main.rs").unlink()
                lib = self.repo / "src/lib.rs"
                lib.write_text(initial)
                self.run_cmd("cargo", "check", "--offline")
                result = self.install("--edit-sources")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                edited = lib.read_text()
                self.assertIn("#![forbid(unsafe_code)]", edited)
                self.assertLess(edited.index(after_marker), edited.index("#![forbid(unsafe_code)]"), edited)
                self.assertLess(edited.index("#![forbid(unsafe_code)]"), edited.index(before_marker), edited)
                self.run_cmd("cargo", "check", "--offline")

    def test_lock_records_only_installed_files_and_minimal_keeps_opencode_symlink(self):
        result = self.install("--mode", "full")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lock_full = tomllib.loads((self.repo / ".rust-policy/lock.toml").read_text())
        self.assertNotIn("docs/rust-policy/AGENTS.rust.md", lock_full["files"])
        self.assertIn(".claude/agents/rust-reviewer.md", lock_full["files"])
        self.assertIn(".codex/agents/rust-reviewer.toml", lock_full["files"])
        self.assertTrue(lock_full["caller"])
        self.assertEqual(set(lock_full["blocks"]), {"AGENTS.md", "CLAUDE.md"})

        reinstall_no_rev = self.install("--mode", "full", "--no-reviewer")
        self.assertEqual(reinstall_no_rev.returncode, 0, reinstall_no_rev.stdout + reinstall_no_rev.stderr)
        self.assertIn("todo: .claude/agents/rust-reviewer.md", reinstall_no_rev.stdout)
        lock_stale = tomllib.loads((self.repo / ".rust-policy/lock.toml").read_text())
        self.assertIn(".claude/agents/rust-reviewer.md", lock_stale["files"])

        self.setUp()
        result = self.install("--mode", "full", "--no-reviewer")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lock_no_rev = tomllib.loads((self.repo / ".rust-policy/lock.toml").read_text())
        self.assertNotIn("docs/rust-policy/AGENTS.rust.md", lock_no_rev["files"])
        self.assertNotIn(".claude/agents/rust-reviewer.md", lock_no_rev["files"])
        self.assertNotIn(".codex/agents/rust-reviewer.toml", lock_no_rev["files"])

        self.setUp()
        (self.repo / "shared-opencode.json").write_text('{"instructions": []}\n')
        (self.repo / "opencode.json").symlink_to("shared-opencode.json")
        result = self.install("--mode", "minimal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.repo / "opencode.json").is_symlink())
        self.assertEqual(os.readlink(self.repo / "opencode.json"), "shared-opencode.json")
        lock_min = tomllib.loads((self.repo / ".rust-policy/lock.toml").read_text())
        self.assertEqual(
            set(lock_min["files"]),
            {
                ".rust-policy/check.sh",
                ".rust-policy/policy.py",
                "docs/rust-policy/guide.md",
                "docs/rust-policy/AGENTS.rust.md",
            },
        )
        self.assertEqual(lock_min["caller"], "")
        self.assertEqual(lock_min["blocks"], {})

    def test_malformed_profile_or_adoption_exits_cleanly_without_traceback(self):
        bad_profiles = [
            "invalid = [\n",
            'schema = 1\nadoption = 1\n[policy]\nowners = ["@org/core"]\n[toolchain]\nstable = "1.97.0"\nmsrv = "1.85"\nedition = "2024"\n',
        ]
        for body in bad_profiles:
            with self.subTest(body=body.splitlines()[0]):
                (self.repo / "rust-policy.toml").write_text(body)
                result = self.install("--mode", "minimal")
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_reinstall_handles_valid_profile_without_crates_section(self):
        (self.repo / "rust-policy.toml").write_text(
            'schema = 1\n'
            '[policy]\nowners = ["@org/core"]\n'
            '[adoption]\nci = "standalone"\nagents = "external"\nchecks = ["fmt", "clippy"]\n'
            '[toolchain]\nstable = "1.97.0"\nmsrv = "1.85"\nedition = "2024"\n'
            '[[feature_sets]]\nid = "default"\nargs = ""\n'
        )
        result = self.install("--mode", "minimal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_full_install_creates_clippy_toml_and_respects_dot_clippy_toml(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        clippy = self.repo / "clippy.toml"
        self.assertTrue(clippy.is_file())
        self.assertIn("disallowed-methods", clippy.read_text())

        clippy.unlink()
        (self.repo / ".clippy.toml").write_text("# existing\n")
        result2 = self.install()
        self.assertEqual(result2.returncode, 0, result2.stdout + result2.stderr)
        self.assertFalse(clippy.exists())
        self.assertEqual((self.repo / ".clippy.toml").read_text(), "# existing\n")

    def test_visibility_public_option_writes_profile(self):
        result = self.install("--visibility", "public")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prof = tomllib.loads((self.repo / "rust-policy.toml").read_text())
        self.assertEqual(prof["adoption"]["visibility"], "public")

    def test_reviewers_adds_catchall_when_existing_star_has_no_owner(self):
        (self.repo / ".github").mkdir(parents=True, exist_ok=True)
        (self.repo / ".github/CODEOWNERS").write_text("*   # unowned comment\n")
        result = self.install("--reviewers", "@org/maintainers")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.repo / ".github/CODEOWNERS").read_text()
        self.assertIn("* @org/maintainers\n", text)

    def test_isolated_python_and_manifest_forbid_unsafe_and_minimal_codeowners_todo(self):
        # 1. python3 -I scripts/install.py works without PYTHONPATH
        iso = subprocess.run(
            [sys.executable, "-I", str(KIT / "scripts/install.py"), "--target", str(self.repo),
             "--owners", "@org/core", "--mode", "minimal", "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertEqual(iso.returncode, 0, iso.stdout + iso.stderr)
        self.assertIn("CODEOWNERS", iso.stdout)

        # 2. [lints.rust] unsafe_code = "forbid" suppresses redundant #![forbid(unsafe_code)] todo
        (self.repo / "Cargo.toml").write_text(
            (self.repo / "Cargo.toml").read_text() + '\n[lints.rust]\nunsafe_code = "forbid"\n'
        )
        res = self.install("--mode", "minimal")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("src/main.rs: add #![forbid(unsafe_code)]", res.stdout)


if __name__ == "__main__":
    unittest.main()
