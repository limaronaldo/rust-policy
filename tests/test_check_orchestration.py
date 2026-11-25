"""Fast shell boundary regressions; real Cargo behavior is covered in test_check_sh."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

KIT = pathlib.Path(__file__).resolve().parents[1]


class Orchestration(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="rp-orchestration-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / ".rust-policy").mkdir()
        shutil.copy2(KIT / "tools/check.sh", self.tmp / ".rust-policy/check.sh")
        self.plan = dict(toolchain="1.97.0", msrv="1.97", nightly="nightly-2026-10-01",
                         rustflags="", feature_args="--features=probe\n-", miri_args="-p core",
                         unsafe_args="-p ffi", safe_args="--exclude ffi", ratchet_packages="core",
                         **{"ratchet_allow:core": "-A missing_docs", "ratchet_allow:ffi": "",
                            "ratchet_allow:app": ""})
        for step in "fmt clippy test doc msrv deny miri ratchet".split():
            self.plan["run_" + step] = "true" if step in ("miri", "ratchet") else "false"
        (self.tmp / ".rust-policy/policy.py").write_text('''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open("calls", "a") as f: f.write(json.dumps(["policy", *args]) + "\\n")
p = json.loads(Path("plan.json").read_text())
if args[0] == "get":
    if args[1] == p.get("fail_key"): sys.exit(7)
    if args[1] not in p: sys.exit(64)
    print(p[args[1]])
elif args[0] == "check": sys.exit(p.get("check_status", 0))
''')
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for exe in ("cargo", "rustc", "cargo-deny"):
            path = bin_dir / exe
            path.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
exe = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open("calls", "a") as f: f.write(json.dumps([exe, *args]) + "\\n")
with open("envs", "a") as f: f.write(json.dumps({"exe": exe, "args": args, "env": dict(os.environ)}) + "\\n")
if exe == "rustc":
    print("release: " + os.environ.get("FAKE_RELEASE", "1.97.0-nightly" if any("nightly" in a for a in args) else "1.97.0"), flush=True)
    if os.environ.get("RUSTC_LONG_OUTPUT"): print("verbose tail\\n" * 100000)
if exe == "cargo" and "metadata" in args:
    if os.environ.get("METADATA_FAIL"): sys.exit(7)
    if os.environ.get("FAKE_METADATA"):
        print(os.environ["FAKE_METADATA"])
    else:
        print(json.dumps({"workspace_members": ["core", "ffi", "app"], "packages": [
            {"id": p, "name": p, "features": {"probe": []} if p == "core" else {},
             "dependencies": [{"name": "core"}] if p == "app" else []}
            for p in ["core", "ffi", "app"]]}))
if exe == "cargo" and "clippy" in args: sys.exit(int(os.environ.get("CLIPPY_STATUS", "0")))
''')
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
        self.env.pop("CI", None)
        self.env.pop("GITHUB_ACTIONS", None)
        self.env.pop("POLICY_BASE_REF", None)

    def run_check(self, *args):
        (self.tmp / "plan.json").write_text(json.dumps(self.plan))
        (self.tmp / "calls").write_text("")
        (self.tmp / "envs").write_text("")
        result = subprocess.run(["bash", ".rust-policy/check.sh", *args], cwd=self.tmp,
                                env=self.env, text=True, capture_output=True)
        self.calls = [json.loads(s) for s in (self.tmp / "calls").read_text().splitlines()]
        self.envs = [json.loads(s) for s in (self.tmp / "envs").read_text().splitlines()]
        return result

    def test_ci_requires_and_forwards_base_before_cargo(self):
        result = self.run_check("ci")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))
        result = self.run_check("ci", "--base", "base-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["policy", "check", "--base", "base-ref"], self.calls)
        first_cargo = next(i for i, c in enumerate(self.calls) if c[0] == "cargo")
        self.assertLess(self.calls.index(["policy", "check", "--base", "base-ref"]), first_cargo)

    def test_invalid_conformance_stops_ci(self):
        self.plan["check_status"] = 1
        result = self.run_check("ci", "--base", "base-ref")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))

    def test_invalid_arguments_are_rejected(self):
        for args in (("ci", "--typo"), ("conformance", "--base"), ("conformance", "ignored")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_check(*args).returncode, 0)

    def test_profile_failure_never_runs_cargo(self):
        self.plan["fail_key"] = "feature_args"
        result = self.run_check("ci", "--base", "base-ref")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))

    def test_ci_runs_miri_and_ratchet_for_both_feature_rows(self):
        result = self.run_check("ci", "--base", "base-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        miri = [c for c in self.calls if c[0] == "cargo" and "miri" in c]
        ratchet = [c for c in self.calls if c[0] == "cargo" and "clippy" in c]
        self.assertEqual(len(miri), 2, self.calls)
        self.assertEqual(len(ratchet), 2, self.calls)
        self.assertTrue(any("--features=core/probe" in c for c in ratchet), ratchet)
        self.assertEqual(sum(any("--features" in a for a in c) for c in ratchet), 1, ratchet)

    def test_explicit_empty_ratchet_args_select_default(self):
        result = self.run_check("ratchet", "--")
        self.assertEqual(result.returncode, 0, result.stderr)
        clippy = next(c for c in self.calls if c[0] == "cargo" and "clippy" in c)
        self.assertFalse(any("--features" in a for a in clippy), clippy)

    def test_empty_miri_selection_does_not_run_workspace(self):
        self.plan["miri_args"] = ""
        result = self.run_check("miri")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))
        self.assertIn("nothing to run", result.stderr)

    def test_failed_clippy_never_compares_or_writes_ratchet(self):
        self.env["CLIPPY_STATUS"] = "1"
        result = self.run_check("ratchet")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[:2] == ["policy", "ratchet"] for c in self.calls), self.calls)

    def test_ratchet_update_requires_single_feature_row(self):
        result = self.run_check("ratchet", "--update")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[:2] == ["policy", "ratchet"] for c in self.calls))

    def test_allow_flags_are_scoped_to_the_package(self):
        result = self.run_check("clippy", "--", "--features=probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = [c for c in self.calls if c[0] == "cargo" and "clippy" in c]
        self.assertEqual(len(cmds), 3, cmds)
        for cmd in cmds:
            self.assertEqual("-A" in cmd, cmd[cmd.index("-p") + 1] == "core", cmd)
            self.assertEqual("--features=core/probe" in cmd, cmd[cmd.index("-p") + 1] != "ffi", cmd)

    def test_all_unsafe_workspace_has_no_empty_safe_invocation(self):
        self.plan["unsafe_args"] = "-p core -p ffi -p app"
        result = self.run_check("clippy")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = [c for c in self.calls if c[0] == "cargo" and "clippy" in c]
        self.assertEqual(len(cmds), 3, cmds)
        self.assertTrue(all("unsafe_code" not in c for c in cmds), cmds)

    def test_ratchet_profile_and_toolchain_failures_stop_before_compare(self):
        self.plan["fail_key"] = "ratchet_allow:core"
        result = self.run_check("ratchet")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[:2] == ["policy", "ratchet"] for c in self.calls))
        self.plan.pop("fail_key")
        self.plan["toolchain"] = "1.98.0"
        result = self.run_check("ratchet")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))

    def test_unknown_run_profile_key_is_not_assumed_true(self):
        self.plan.pop("run_fmt")
        result = self.run_check("ci", "--base", "base-ref")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))

    def test_metadata_failure_cannot_produce_empty_successful_evidence(self):
        self.env["METADATA_FAIL"] = "1"
        result = self.run_check("ratchet")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[:2] == ["policy", "ratchet"] for c in self.calls))

    def test_workspace_all_features_propagates_to_dependency_features(self):
        result = self.run_check("clippy", "--", "--all-features")
        self.assertEqual(result.returncode, 0, result.stderr)
        app = next(c for c in self.calls if c[:2] == ["cargo", "clippy"] and "app" in c)
        self.assertIn("--features=core/probe", app)

    def test_verbose_toolchain_output_is_captured_before_matching(self):
        self.env["RUSTC_LONG_OUTPUT"] = "1"
        self.plan["msrv"] = "1.85"
        self.env["FAKE_RELEASE"] = "1.85.0"
        result = self.run_check("msrv")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["rustc", "+1.85.0", "-vV"], self.calls)
        self.assertTrue(any(c[:3] == ["cargo", "+1.85.0", "check"] for c in self.calls))
        self.env.pop("FAKE_RELEASE")
        result = self.run_check("miri")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[0] == "cargo" and "miri" in c for c in self.calls))

    def test_msrv_rejects_newer_patch_as_minimum_evidence(self):
        self.plan["msrv"] = "1.85"
        self.env["FAKE_RELEASE"] = "1.85.1"
        result = self.run_check("msrv")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == "cargo" for c in self.calls))

    def test_fast_also_checks_ratchet(self):
        result = self.run_check("fast")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[:2] == ["policy", "ratchet"] for c in self.calls), self.calls)

    def test_environment_sanitization_strips_bypass_vars_and_sets_lc_all(self):
        bypass = {
            "CARGO_ENCODED_RUSTFLAGS": "--cap-lints=allow",
            "CARGO_ENCODED_RUSTDOCFLAGS": "--cap-lints=allow",
            "CARGO_BUILD_RUSTFLAGS": "--cap-lints=allow",
            "CARGO_BUILD_RUSTDOCFLAGS": "--cap-lints=allow",
            "RUSTC": "/tmp/fake-rustc",
            "RUSTDOC": "/tmp/fake-rustdoc",
            "RUSTC_WRAPPER": "/tmp/fake-wrapper",
            "RUSTC_WORKSPACE_WRAPPER": "/tmp/fake-ws-wrapper",
            "RUSTC_BOOTSTRAP": "1",
            "CARGO_BUILD_RUSTC": "/tmp/fake-build-rustc",
            "CARGO_BUILD_RUSTDOC": "/tmp/fake-build-rustdoc",
            "CARGO_BUILD_RUSTC_WRAPPER": "/tmp/fake-build-wrapper",
            "CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER": "/tmp/fake-build-ws-wrapper",
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS": "--cap-lints=allow",
            "CARGO_TARGET_AARCH64_APPLE_DARWIN_RUSTDOCFLAGS": "--cap-lints=allow",
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER": "/tmp/evil-runner",
            "CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER": "/tmp/evil-linker",
            "CARGO_ALIAS_CLIPPY": "check",
            "CARGO_PROFILE_DEV_OVERFLOW_CHECKS": "false",
            "CARGO_PROFILE_DEV_DEBUG_ASSERTIONS": "false",
            "CARGO_PROFILE_TEST_OVERFLOW_CHECKS": "false",
            "CARGO_PROFILE_TEST_DEBUG_ASSERTIONS": "false",
        }
        self.env.update(bypass)
        self.env["LC_ALL"] = "pt_BR.UTF-8"
        self.env["CARGO_TARGET_DIR"] = "/tmp/keep-cache"
        self.env["CARGO_TERM_COLOR"] = "always"
        result = self.run_check("clippy")
        self.assertEqual(result.returncode, 0, result.stderr)
        cargo_envs = [e["env"] for e in self.envs if e["exe"] == "cargo"]
        self.assertTrue(cargo_envs, self.envs)
        for env in cargo_envs:
            for key in bypass:
                self.assertNotIn(key, env)
            self.assertEqual(env.get("LC_ALL"), "C")
            self.assertEqual(env.get("CARGO_TERM_COLOR"), "never")
            self.assertEqual(env.get("CARGO_TARGET_DIR"), "/tmp/keep-cache")

    def test_clippy_forbids_missing_safety_doc(self):
        result = self.run_check("clippy")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = [c for c in self.calls if c[:2] == ["cargo", "clippy"]]
        self.assertEqual(len(cmds), 3, cmds)
        for cmd in cmds:
            self.assertTrue(
                any(cmd[i:i + 2] == ["-F", "clippy::missing_safety_doc"] for i in range(len(cmd) - 1)),
                cmd,
            )

    def test_external_dep_features_are_scoped_to_packages_that_depend_on_them(self):
        self.env["FAKE_METADATA"] = json.dumps({
            "workspace_members": ["core", "ffi", "app"],
            "packages": [
                {"id": "core", "name": "core", "features": {"probe": []}, "dependencies": [{"name": "serde"}]},
                {"id": "ffi", "name": "ffi", "features": {}, "dependencies": []},
                {"id": "app", "name": "app", "features": {}, "dependencies": [{"name": "core"}]},
            ],
        })
        result = self.run_check("clippy", "--", "--features=serde/derive,serde?/std")
        self.assertEqual(result.returncode, 0, result.stderr)
        by_pkg = {c[c.index("-p") + 1]: c for c in self.calls if c[:2] == ["cargo", "clippy"]}
        self.assertIn("--features=serde/derive,serde?/std", by_pkg["core"])
        self.assertFalse(any(a.startswith("--features") for a in by_pkg["ffi"]), by_pkg["ffi"])
        self.assertFalse(any(a.startswith("--features") for a in by_pkg["app"]), by_pkg["app"])

    def test_duplicate_workspace_dep_aliases_are_all_preserved(self):
        self.env["FAKE_METADATA"] = json.dumps({
            "workspace_members": ["core", "ffi", "app"],
            "packages": [
                {"id": "core", "name": "core", "features": {"probe": []}, "dependencies": []},
                {"id": "ffi", "name": "ffi", "features": {}, "dependencies": []},
                {"id": "app", "name": "app", "features": {},
                 "dependencies": [{"name": "core"}, {"name": "core", "rename": "core_dev"}]},
            ],
        })
        result = self.run_check("clippy", "--", "--features=probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        by_pkg = {c[c.index("-p") + 1]: c for c in self.calls if c[:2] == ["cargo", "clippy"]}
        self.assertIn("--features=core/probe", by_pkg["core"])
        self.assertIn("--features=core/probe,core_dev/probe", by_pkg["app"])
        self.assertFalse(any(a.startswith("--features") for a in by_pkg["ffi"]), by_pkg["ffi"])

    def test_unknown_dep_feature_is_forwarded_so_cargo_fails_loudly(self):
        result = self.run_check("clippy", "--", "--features=typo_unknown_dep/foo")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = [c for c in self.calls if c[:2] == ["cargo", "clippy"]]
        self.assertEqual(len(cmds), 3, cmds)
        self.assertTrue(all("--features=typo_unknown_dep/foo" in c for c in cmds), cmds)

    def test_ratchet_update_and_init_load_single_feature_row(self):
        self.plan["feature_args"] = "--features=probe"
        for args in (
            ("ratchet", "--update"),
            ("ratchet", "--", "--update"),
            ("ratchet", "--init"),
            ("ratchet", "--", "--init"),
        ):
            with self.subTest(args=args):
                result = self.run_check(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                clippy = next(c for c in self.calls if c[0] == "cargo" and "clippy" in c)
                self.assertIn("--features=core/probe", clippy, (args, clippy))
                self.assertTrue(
                    any(c[:2] == ["policy", "ratchet"] and c[-1] == args[-1] for c in self.calls),
                    self.calls,
                )

    def test_ratchet_update_rejects_extra_feature_flags(self):
        self.plan["feature_args"] = "--features=probe"
        for args in (
            ("ratchet", "--", "--update", "--no-default-features"),
            ("ratchet", "--update", "--no-default-features"),
        ):
            with self.subTest(args=args):
                result = self.run_check(*args)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(c[:2] == ["policy", "ratchet"] for c in self.calls), self.calls)
                self.assertFalse(any(c[0] == "cargo" and "clippy" in c for c in self.calls), self.calls)

    def test_clippy_and_ratchet_pass_no_deps(self):
        self.plan["feature_args"] = "-"
        res_clippy = self.run_check("clippy")
        self.assertEqual(res_clippy.returncode, 0, res_clippy.stderr)
        clippy_cmds = [c for c in self.calls if c[:2] == ["cargo", "clippy"]]
        self.assertEqual(len(clippy_cmds), 3, clippy_cmds)
        self.assertTrue(all("--no-deps" in c for c in clippy_cmds), clippy_cmds)

        res_ratchet = self.run_check("ratchet")
        self.assertEqual(res_ratchet.returncode, 0, res_ratchet.stderr)
        ratchet_cmds = [c for c in self.calls if c[:2] == ["cargo", "clippy"]]
        self.assertTrue(ratchet_cmds and all("--no-deps" in c for c in ratchet_cmds), ratchet_cmds)

    def test_snapshot_env_vars_are_sanitized(self):
        self.env.update({
            "INSTA_UPDATE": "always",
            "INSTA_FORCE_PASS": "1",
            "TRYBUILD": "overwrite",
            "UPDATE_EXPECT": "1",
        })
        result = self.run_check("test")
        self.assertEqual(result.returncode, 0, result.stderr)
        test_envs = [e["env"] for e in self.envs if e["exe"] == "cargo" and "test" in e["args"]]
        self.assertEqual(len(test_envs), 1, self.envs)
        self.assertEqual(test_envs[0].get("INSTA_UPDATE"), "no")
        for var in ("INSTA_FORCE_PASS", "TRYBUILD", "UPDATE_EXPECT"):
            self.assertNotIn(var, test_envs[0])

    def test_ci_runs_doc_for_each_feature_row(self):
        self.plan["run_miri"] = "false"
        self.plan["run_ratchet"] = "false"
        self.plan["run_doc"] = "true"
        result = self.run_check("ci", "--base", "base-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [c for c in self.calls if c[:2] == ["cargo", "doc"]]
        self.assertEqual(len(docs), 2, docs)
        self.assertTrue(any("--features=probe" in c for c in docs), docs)
        self.assertTrue(any(all("--features" not in a for a in c) for c in docs), docs)

        # Direct reusable-CI invocation `check.sh doc -- --all-features` must be accepted
        res_direct = self.run_check("doc", "--", "--all-features")
        self.assertEqual(res_direct.returncode, 0, res_direct.stderr)
        self.assertTrue(any(c[:2] == ["cargo", "doc"] and "--all-features" in c for c in self.calls), self.calls)


if __name__ == "__main__":
    unittest.main()
