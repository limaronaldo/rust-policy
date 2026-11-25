"""Invariants of the reusable workflow and the caller, and the gate's truth table. Needs PyYAML."""
import json
import os
import re
import subprocess
import sys
import unittest
import pathlib
import tempfile

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from helpers import KIT  # noqa: E402

WF = yaml.safe_load((KIT / ".github/workflows/rust-ci.yml").read_text())
CALLER_TEXT = (KIT / "templates/.github/workflows/rust-policy.yml").read_text()
JOBS = WF["jobs"]
GATE_CODE = JOBS["gate"]["steps"][0]["run"]


def gate(results, run_miri="false", plan_outputs=True, off=()):
    needs = {j: {"result": "success", "outputs": {}} for j in JOBS if j != "gate"}
    if plan_outputs:
        needs["plan"]["outputs"] = {"run_miri": run_miri, "run_ratchet": "false"} | {f"run_{j}": "false" for j in off}
    for j, r in results.items():
        needs[j]["result"] = r
    env = dict(os.environ, NEEDS=json.dumps(needs), GITHUB_STEP_SUMMARY=os.devnull)
    return subprocess.run([sys.executable, "-I", "-c", GATE_CODE], env=env, capture_output=True).returncode


class Workflow(unittest.TestCase):
    def test_permissions_and_pins(self):
        self.assertEqual(WF["permissions"], {})
        for name, job in JOBS.items():
            with self.subTest(job=name):
                self.assertIn("timeout-minutes", job)
                self.assertIn("permissions", job)
                self.assertLessEqual(set(job["permissions"].items()), {("contents", "read")})
                for step in job["steps"]:
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
                        if step["uses"].startswith("actions/checkout@"):
                            self.assertIs(step["with"]["persist-credentials"], False)
                    if "run" in step:
                        self.assertNotIn("${{", step["run"], "expressions in run: allow injection; pass them through env")

    def test_tools_are_verified_before_they_run(self):
        for name, job in JOBS.items():
            runs = [s.get("run", "") for s in job["steps"]]
            uses_tools = [i for i, r in enumerate(runs) if ".rust-policy/" in r]
            if not uses_tools:
                continue
            with self.subTest(job=name):
                verify = [i for i, r in enumerate(runs) if "POLICY_FILE_SUMS" in r and "sha256sum --check --strict" in r]
                self.assertTrue(verify and verify[0] < uses_tools[0])

    def test_gate_needs_every_job_and_always_runs(self):
        gate_job = JOBS["gate"]
        self.assertEqual(set(gate_job["needs"]), set(JOBS) - {"gate"})
        self.assertEqual(gate_job["if"], "${{ always() }}")
        for name, job in JOBS.items():
            if name in ("plan", "conformance"):
                self.assertNotIn("if", job, f"{name} must not be skippable")
            elif name != "gate":
                self.assertEqual(job["if"], f"${{{{ needs.plan.outputs.run_{name} == 'true' }}}}")
            self.assertNotIn("continue-on-error", job)

    def test_ratchet_uses_the_feature_and_os_matrix(self):
        job = JOBS["ratchet"]
        self.assertEqual(job["runs-on"], "${{ matrix.os }}")
        self.assertEqual(job["strategy"], {"fail-fast": False, "matrix": {"include": "${{ fromJSON(needs.plan.outputs.matrix) }}"}})
        step = next(s for s in job["steps"] if "check.sh ratchet" in s.get("run", ""))
        self.assertEqual(step["env"]["FEATURE_ARGS"], "${{ matrix.args }}")
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / ".rust-policy").mkdir()
            (root / ".rust-policy/check.sh").write_text('printf "%s\\n" "$@"\n')
            for flags, want in (("", ["ratchet", "--"]), ("--all-features", ["ratchet", "--", "--all-features"])):
                with self.subTest(flags=flags):
                    r = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], cwd=root,
                                       env=dict(os.environ, FEATURE_ARGS=flags), text=True, capture_output=True)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout.splitlines(), want)

    def test_conformance_compares_the_event_base_and_rejects_unavailable_history(self):
        job = JOBS["conformance"]
        checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        step = next(s for s in job["steps"] if s.get("name") == "Conformance checks")
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            def commit():
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "x"], cwd=root, check=True)
                return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            base = commit()
            commit()
            commit()  # merge-group base need not be HEAD^1
            subprocess.run(["git", "update-ref", "refs/remotes/origin/main", base], cwd=root, check=True)
            (root / ".rust-policy").mkdir()
            (root / ".rust-policy/policy.py").write_text("import sys; print(sys.argv[-1])\n")
            for event in ("pull_request", "merge_group", "push", "schedule", "workflow_dispatch"):
                with self.subTest(event=event):
                    env = dict(os.environ, EVENT_NAME=event, BASE_SHA=base if event in ("pull_request", "merge_group", "push") else "", DEFAULT_BRANCH="main")
                    r = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], cwd=root, env=env, text=True, capture_output=True)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout.strip(), base)
            for bad in ("", "0" * 40, "f" * 40, "HEAD^1"):
                with self.subTest(unavailable=bad):
                    env = dict(os.environ, EVENT_NAME="merge_group", BASE_SHA=bad, DEFAULT_BRANCH="main")
                    r = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], cwd=root, env=env, capture_output=True)
                    self.assertNotEqual(r.returncode, 0)

    def test_toolchain_is_forced_by_environment(self):
        for name in ("conformance", "fmt", "clippy", "test", "doc", "msrv", "deny", "miri"):
            self.assertIn("RUSTUP_TOOLCHAIN", JOBS[name]["env"], name)

    def test_caller(self):
        caller = yaml.safe_load(CALLER_TEXT.replace("{default_branch}", "main").replace("{policy_repo}", "o/r")
                                 .replace("{policy_sha}", "0" * 40).replace("{version}", "1"))
        self.assertEqual(list(caller["jobs"]), ["rust-policy"])
        triggers = caller[True]   # PyYAML reads the key `on` as True
        self.assertEqual(set(triggers), {"pull_request", "merge_group", "push", "schedule", "workflow_dispatch"})
        self.assertNotIn("paths", json.dumps(triggers))
        self.assertEqual(caller["permissions"], {})
        self.assertNotIn("pull_request_target", CALLER_TEXT)

    def test_release_sums_are_current(self):
        r = subprocess.run([sys.executable, str(KIT / "scripts/release.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rust_cache_keys_depend_on_rust_policy_toml(self):
        cache_steps = [
            (name, step)
            for name, job in JOBS.items()
            for step in job["steps"]
            if step.get("uses", "").startswith("Swatinem/rust-cache@")
        ]
        self.assertTrue(cache_steps)
        for name, step in cache_steps:
            with self.subTest(job=name):
                self.assertIn("hashFiles('rust-policy.toml')", step["with"]["key"])

    def test_doc_uses_feature_matrix_and_cargo_jobs_reverify_managed_files(self):
        doc_job = JOBS["doc"]
        self.assertEqual(doc_job["runs-on"], "${{ matrix.os }}")
        self.assertEqual(doc_job["strategy"], {"fail-fast": False, "matrix": {"include": "${{ fromJSON(needs.plan.outputs.matrix) }}"}})
        doc_step = next(s for s in doc_job["steps"] if "check.sh doc" in s.get("run", ""))
        self.assertEqual(doc_step["env"]["FEATURE_ARGS"], "${{ matrix.args }}")
        for name in ("clippy", "test", "doc", "msrv", "deny", "ratchet", "miri"):
            with self.subTest(job=name):
                steps = JOBS[name]["steps"]
                last = steps[-1]
                self.assertEqual(last.get("if"), "${{ always() }}")
                self.assertIn("POLICY_FILE_SUMS", last.get("run", ""))
                self.assertIn("sha256sum --check --strict", last.get("run", ""))


class Gate(unittest.TestCase):
    def test_truth_table(self):
        cases = [
            ("all success, miri planned", {}, "true", 0),
            ("miri skipped as planned", {"miri": "skipped"}, "false", 0),
            ("miri skipped although planned", {"miri": "skipped"}, "true", 1),
            ("miri failed", {"miri": "failure"}, "true", 1),
            ("clippy skipped", {"clippy": "skipped", "miri": "skipped"}, "false", 1),
            ("clippy failed", {"clippy": "failure", "miri": "skipped"}, "false", 1),
            ("test cancelled", {"test": "cancelled", "miri": "skipped"}, "false", 1),
            ("conformance failed", {"conformance": "failure", "miri": "skipped"}, "false", 1),
            ("deny failed", {"deny": "failure", "miri": "skipped"}, "false", 1),
        ]
        for name, results, run_miri, want in cases:
            with self.subTest(name):
                self.assertEqual(gate(results, run_miri), want)

    def test_planned_off_checks_may_skip(self):
        self.assertEqual(gate({"test": "skipped", "msrv": "skipped", "miri": "skipped", "ratchet": "skipped"}, off=("test", "msrv")), 0)
        self.assertEqual(gate({"test": "skipped", "miri": "skipped", "ratchet": "skipped"}, off=("msrv",)), 1)
        self.assertEqual(gate({"conformance": "skipped", "miri": "skipped", "ratchet": "skipped"}, off=("conformance",)), 1)

    def test_plan_failure_blocks_even_if_miri_skipped(self):
        skipped = {j: "skipped" for j in JOBS if j not in ("gate", "plan")}
        self.assertEqual(gate({"plan": "failure", **skipped}, plan_outputs=False), 1)
        self.assertEqual(gate({"plan": "failure", **skipped}, run_miri="false"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
