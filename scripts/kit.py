"""Shared definitions for install.py and release.py: what rust-policy installs and how it is hashed."""
import hashlib
import pathlib
import re
import sys
import tomllib

sys.dont_write_bytecode = True

KIT = pathlib.Path(__file__).resolve().parents[1]
VERSION = (KIT / "VERSION").read_text().strip()
# owner/name of this repository on GitHub. Consumers may call rust-ci.yml only from here (P15).
POLICY_REPO = "limaronaldo/rust-policy"
GUIDE_VERSION = tomllib.loads((KIT / "guide/rules.toml").read_text())["guide_version"]

# consumer path -> source in this repository; copied byte for byte and verified by CI
MANAGED_FILES = {
    ".rust-policy/check.sh": "tools/check.sh",
    ".rust-policy/policy.py": "tools/policy.py",
    "docs/rust-policy/guide.md": "guide/rust-guide.md",
}
# installed on request; verified when present (P01)
OPTIONAL_FILES = {
    ".claude/agents/rust-reviewer.md": "templates/.claude/agents/rust-reviewer.md",
    ".codex/agents/rust-reviewer.toml": "templates/.codex/agents/rust-reviewer.toml",
    "docs/rust-policy/AGENTS.rust.md": "templates/AGENTS.block.md",
}
# consumer file -> block template; the block is verified, the rest of the file belongs to the project
MANAGED_BLOCKS = {
    "AGENTS.md": "templates/AGENTS.block.md",
    "CLAUDE.md": "templates/CLAUDE.block.md",
}
CALLER = ".github/workflows/rust-policy.yml"
CALLER_TEMPLATE = "templates/.github/workflows/rust-policy.yml"


def text(rel):
    return (KIT / rel).read_text(encoding="utf-8")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def mask_caller(t):
    """Must match mask_caller in tools/policy.py."""
    t = re.sub(r"(?m)^(\s*uses:\s*).*$", r"\1<masked>", t)
    return re.sub(r"(?m)^(\s*branches:\s*).*$", r"\1<masked>", t)


def sums():
    files = {dst: sha256_bytes((KIT / src).read_bytes()) for dst, src in (MANAGED_FILES | OPTIONAL_FILES).items()}
    blocks = {dst: sha256_bytes(text(src).encode()) for dst, src in MANAGED_BLOCKS.items()}
    caller = sha256_bytes(mask_caller(text(CALLER_TEMPLATE)).encode())
    return files, blocks, caller
