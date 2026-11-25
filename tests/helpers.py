"""Builds throwaway consumer repositories from tests/fixtures/ws with scripts/install.py."""
import atexit
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

KIT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = KIT / "tests/fixtures/ws"
PIN = "1" * 40
_template = None


def sh(*args, cwd, check=True, env=None):
    run_env = env
    if args and args[0] == "git":
        run_env = dict(os.environ if env is None else env)
        run_env["GIT_CONFIG_GLOBAL"] = os.devnull
        run_env["GIT_CONFIG_NOSYSTEM"] = "1"
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True, env=run_env)


def commit(repo, msg="change"):
    sh("git", "add", "-A", cwd=repo)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
       "commit", "--no-verify", "-qm", msg, "--allow-empty", cwd=repo)


def template():
    """One installed consumer, copied for every test (install runs cargo metadata, so cache it)."""
    global _template
    if _template is None:
        root = pathlib.Path(tempfile.mkdtemp(prefix="rp-template-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        repo = root / "repo"
        shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("target"))
        sh("git", "init", "-q", "-b", "main", cwd=repo)
        commit(repo, "base")
        sh("python3", str(KIT / "scripts/install.py"), "--target", str(repo), "--owners", "@mbras/rust-policy-owners",
           "--policy-sha", PIN, "--edit-sources", "--all-features", cwd=KIT)
        commit(repo, "adopt rust-policy")
        _template = repo
    return _template


def consumer():
    root = pathlib.Path(tempfile.mkdtemp(prefix="rp-case-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    d = root / "repo"
    shutil.copytree(template(), d, symlinks=True)
    return d


_minimal = None


def minimal_consumer():
    """A consumer installed with --mode minimal: standalone CI, external agent files, own CODEOWNERS."""
    global _minimal
    if _minimal is None:
        root = pathlib.Path(tempfile.mkdtemp(prefix="rp-minimal-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        repo = root / "repo"
        shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("target"))
        (repo / ".github").mkdir()
        (repo / ".github/CODEOWNERS").write_text(
            "# no default owner on purpose\n/.github/** @org/core\nAGENTS.md @org/core\nCLAUDE.md @org/core\n"
            "AGENTS.override.md @org/core\n/rust-policy.toml @org/core\nrust-toolchain.toml @org/core\n"
            "rustfmt.toml @org/core\n.rustfmt.toml @org/core\nclippy.toml @org/core\n.clippy.toml @org/core\n"
            "/deny.toml @org/core\n.cargo/ @org/core\n/.claude/ @org/core\n/.codex/ @org/core\n/.opencode/ @org/core\n"
            "/.rust-policy/ @org/core\n/docs/rust-policy/ @org/core\n/CODEOWNERS @org/core\n/docs/CODEOWNERS @org/core\n"
            "/.gitattributes @org/core\nbuild.rs @org/core\n/opencode.json @org/core\n")
        (repo / "AGENTS.md").write_text("# Project harness\nRead docs/harness first.\n")
        sh("git", "init", "-q", "-b", "main", cwd=repo)
        commit(repo, "base")
        sh("python3", str(KIT / "scripts/install.py"), "--target", str(repo), "--owners", "@org/core",
           "--policy-sha", PIN, "--mode", "minimal", "--checks", "fmt,clippy,deny", "--libraries=", cwd=KIT)
        commit(repo, "adopt rust-policy, minimal")
        _minimal = repo
    case_root = pathlib.Path(tempfile.mkdtemp(prefix="rp-min-case-"))
    atexit.register(shutil.rmtree, case_root, ignore_errors=True)
    d = case_root / "repo"
    shutil.copytree(_minimal, d, symlinks=True)
    return d


def edit(repo, rel, fn):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text() if p.exists() else ""
    p.write_text(fn(old))


def workflow_env():
    """The managed sums exactly as CI passes them, read from the reusable workflow."""
    wf = (KIT / ".github/workflows/rust-ci.yml").read_text()
    block = wf.split("# BEGIN managed sums", 1)[1].split("# END managed sums", 1)[0]
    env = {"POLICY_VERSION": re.search(r'POLICY_VERSION: "([^"]+)"', block).group(1),
           "POLICY_REPO": re.search(r'POLICY_REPO: "([^"]+)"', block).group(1),
           "POLICY_CALLER_SUM": re.search(r'POLICY_CALLER_SUM: "([^"]+)"', block).group(1)}
    for var in ("POLICY_FILE_SUMS", "POLICY_BLOCK_SUMS"):
        part = block.split(f"{var}: |\n", 1)[1]
        lines = []
        for line in part.splitlines():
            if not line.startswith("    "):
                break
            lines.append(line.strip())
        env[var] = "\n".join(lines) + "\n"
    return env


def policy(repo, *args, ci=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("POLICY_") and k != "GITHUB_ACTIONS"}
    if ci:
        env.update(workflow_env())
    return sh("python3", "-I", ".rust-policy/policy.py", *args, cwd=repo, check=False, env=env)
