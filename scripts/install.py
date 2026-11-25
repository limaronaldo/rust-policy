#!/usr/bin/env python3
"""Installs or upgrades rust-policy in a consuming repository checkout.

  python3 scripts/install.py --target ../my-repo --owners @mbras/rust-policy-owners [options]

Managed files and blocks are always rewritten. Project-owned files (rust-policy.toml, deny.toml,
rustfmt.toml, rust-toolchain.toml, the exception register, opencode.json, the PR template) are
created only when missing. Cargo.toml edits are append-only and limited to the lint opt-in; source
files change only with --edit-sources. Profile options (--mode, --checks, toolchains, libraries and
feature sets) are creation defaults; an existing rust-policy.toml remains authoritative. All planned
writes are checked before applying them. Nothing is committed: review the diff and open a pull request.
"""
import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib

sys.dont_write_bytecode = True
_SCRIPT_DIR = str(pathlib.Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import kit

# Share validation and Rust tokenization with the installed checker.
_spec = importlib.util.spec_from_file_location("rust_policy_tools", kit.KIT / "tools/policy.py")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

BASELINE_WS = """
[workspace.lints.rust]
unsafe_op_in_unsafe_fn = "deny"

[workspace.lints.clippy]
undocumented_unsafe_blocks = "warn"
allow_attributes_without_reason = "warn"
"""
BASELINE_PKG = BASELINE_WS.replace("workspace.lints", "lints")
MEMBER_OPT_IN = "\n[lints]\nworkspace = true\n"
DEFAULT_NIGHTLY = "nightly-2026-10-15"


def log(msg):
    print(f"  {msg}")


class Repo:
    def __init__(self, root, dry):
        self.root, self.dry, self.changed = root, dry, []
        self.pending = {}

    def path(self, rel):
        """Reject redirected destinations before touching any consumer files."""
        relpath = pathlib.Path(rel)
        if relpath.is_absolute() or ".." in relpath.parts:
            sys.exit(f"refusing a path outside the consumer checkout: {rel}")
        path = self.root
        for part in relpath.parts:
            path /= part
            if path.is_symlink():
                sys.exit(f"refusing to manage {rel}: {path.relative_to(self.root)} is a symlink")
            if path != self.root / relpath and path.exists() and not path.is_dir():
                sys.exit(f"refusing to manage {rel}: {path.relative_to(self.root)} is not a directory")
        if path.exists() and not path.is_file():
            sys.exit(f"refusing to manage {rel}: the destination is not a regular file")
        return path

    def read(self, rel):
        if rel in self.pending:
            return self.pending[rel][0]
        p = self.path(rel)
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def write(self, rel, content, why):
        p = self.path(rel)
        repair_mode = rel.endswith(".sh") and p.is_file() and os.name != "nt" and p.stat().st_mode & 0o111 != 0o111
        if self.read(rel) == content and not repair_mode:
            return
        if rel not in self.pending:
            self.changed.append(rel)
        self.pending[rel] = content, why

    def flush(self):
        # Every destination and all generated content have been checked before the first write.
        for rel in self.pending:
            self.path(rel)
        for rel, (content, why) in self.pending.items():
            log(f"{'would write' if self.dry else 'write'} {rel}: {why}")
            if self.dry:
                continue
            p = self.path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:   # LF on every platform
                f.write(content)
            if rel.endswith(".sh"):
                p.chmod(0o755)


def replace_block(existing, block, begin, end_re, at_end):
    pattern = re.compile(rf"^{re.escape(begin)}.*?^{end_re}[^\n]*\n?", re.S | re.M)
    if existing and pattern.search(existing):
        return pattern.sub(lambda _: block, existing, count=1)
    if not existing:
        return block
    if at_end:
        return existing.rstrip("\n") + "\n\n" + block
    return block + "\n" + existing


def default_branch(root):
    out = subprocess.run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode == 0 and "/" in out.stdout:
        return out.stdout.strip().split("/", 1)[1]
    branches = []
    current = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True).stdout.strip()
    for branch in ("main", "master"):
        out = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=root)
        if out.returncode == 0 or current == branch:   # also supports a new checkout's unborn main/master
            branches.append(branch)
    if len(branches) == 1:
        return branches[0]
    sys.exit("cannot determine the default branch; pass --default-branch (origin/HEAD is unavailable and local main/master is ambiguous)")


def kit_sha():
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=kit.KIT, capture_output=True, text=True)
    if top.returncode != 0 or pathlib.Path(top.stdout.strip()).resolve() != kit.KIT:
        return None   # an enclosing project's unrelated HEAD is not this release's provenance
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=kit.KIT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=kit.KIT, capture_output=True, text=True)
    if out.returncode == 0 and not dirty.stdout.strip():
        return out.stdout.strip()
    return None


def metadata(root):
    out = subprocess.run(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"cargo metadata failed in {root}:\n{out.stderr}")
    return json.loads(out.stdout)


def csv_values(value, option):
    vals = [x.strip() for x in value.split(",")] if value else []
    if any(not x for x in vals) or len(set(vals)) != len(vals):
        sys.exit(f"{option} must contain distinct, non-empty comma-separated values")
    return vals


def validate_creation_args(args):
    """Reject bad CLI values even when they would otherwise be omitted from a generated profile."""
    for option, value, pattern in (("--stable", args.stable, policy.VERSION3),
                                    ("--msrv", args.msrv, policy.VERSION2),
                                    ("--nightly", args.nightly, policy.NIGHTLY)):
        if value is not None and not pattern.fullmatch(value):
            sys.exit(f"invalid {option}: {value!r}")
    if args.edition is not None and args.edition not in ("2021", "2024"):
        sys.exit('--edition must be "2021" or "2024"')
    try:
        datetime.date.fromisoformat(args.nightly.removeprefix("nightly-"))
    except ValueError:
        sys.exit(f"invalid --nightly date: {args.nightly!r}")
    if args.checks is not None:
        checks = csv_values(args.checks, "--checks")
        if not set(checks) <= set(policy.CHECKS):
            sys.exit(f"--checks must list checks from {list(policy.CHECKS)}")
    if args.libraries is not None:
        csv_values(args.libraries, "--libraries")


def members(root, meta):
    out = {}
    for p in meta["packages"]:
        if p["id"] in meta["workspace_members"]:
            path = pathlib.Path(p["manifest_path"]).parent.resolve().relative_to(root.resolve()).as_posix() or "."
            out[path] = p
    return out


def detect(root, meta):
    ms = members(root, meta)
    member_roots = {path: (root / path).resolve() for path in ms}
    libs, unsafe = [], []
    for path, p in sorted(ms.items()):
        if any(set(t["kind"]) & {"lib", "rlib", "dylib", "proc-macro"} for t in p["targets"]):
            libs.append(path)
        this_root = member_roots[path]
        for f in (root / path).rglob("*.rs"):
            if not f.is_file():
                continue
            parts = f.relative_to(root).parts
            if ".git" in parts or "target" in parts:
                continue
            f_res = f.resolve()
            enclosing = [mr for mr in member_roots.values() if f_res.is_relative_to(mr)]
            if not enclosing or max(enclosing, key=lambda mr: len(mr.parts)) != this_root:
                continue
            _, bare = policy.lex(f.read_text(encoding="utf-8", errors="replace"))
            if re.search(r"\bunsafe\b", bare):
                unsafe.append(path)
                break
    return libs, unsafe


def toml_list(items):
    return json.dumps(items)


def render_profile(root, args, meta):
    manifest = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
    pkg = (manifest.get("workspace") or {}).get("package") or manifest.get("package") or {}
    msrv = args.msrv or pkg.get("rust-version")
    edition = args.edition or pkg.get("edition")
    if not isinstance(msrv, str) or not isinstance(edition, str):
        sys.exit("cannot read rust-version and edition from Cargo.toml; pass --msrv and --edition")
    libs, unsafe = detect(root, meta)
    if args.libraries is not None:
        libs = csv_values(args.libraries, "--libraries")
    nightly = f'nightly = "{args.nightly}"  # Miri only; pick a nightly that ships the miri component' if unsafe else \
        f'# nightly = "{args.nightly}"  # required once crates.unsafe_allowed is not empty'
    all_features = '\n[[feature_sets]]\nid = "all"\nargs = "--all-features"\n' if args.all_features else ""
    out = kit.text("templates/rust-policy.toml")
    for k, v in {"{stable}": args.stable, "{msrv}": msrv, "{edition}": edition, "{nightly_line}": nightly,
                 "{libraries}": toml_list(libs), "{unsafe_allowed}": toml_list(unsafe),
                 "{all_features}": all_features}.items():
        out = out.replace(k, v)
    out = out.replace("{owners}", args.owners)
    checks = csv_values(args.checks, "--checks") if args.checks is not None else list(policy.CHECKS)
    ci, agents = ("standalone", "external") if args.mode == "minimal" else ("reusable", "block")
    vis_line = f'\nvisibility = "{args.visibility}"' if args.visibility == "public" else ""
    out = out.replace("{adoption}", f'ci = "{ci}"\nagents = "{agents}"{vis_line}\nchecks = {toml_list(checks)}')
    log(f"profile: libraries {libs or 'none'}; unsafe_allowed {unsafe or 'none'} (detected; review both)")
    return out, edition


def cargo_edits(repo, meta):
    root_text = repo.read("Cargo.toml")
    manifest = tomllib.loads(root_text)
    todo = []
    if "workspace" in manifest:
        if "lints" not in manifest["workspace"]:
            repo.write("Cargo.toml", root_text.rstrip("\n") + "\n" + BASELINE_WS, "add the [workspace.lints] baseline")
        else:
            lints = manifest["workspace"]["lints"]
            for tool, lint, level in (("rust", "unsafe_op_in_unsafe_fn", "deny"),
                                      ("clippy", "undocumented_unsafe_blocks", "warn"),
                                      ("clippy", "allow_attributes_without_reason", "warn")):
                if lint not in (lints.get(tool) or {}):
                    todo.append(f'Cargo.toml: add {lint} = "{level}" under [workspace.lints.{tool}]')
        for path, p in members(repo.root, meta).items():
            rel = (pathlib.Path(path) / "Cargo.toml").as_posix() if path != "." else "Cargo.toml"
            text = repo.read(rel)
            m = tomllib.loads(text)
            if "lints" not in m:
                repo.write(rel, text.rstrip("\n") + "\n" + MEMBER_OPT_IN, "opt in to the workspace lints")
            elif (m["lints"] or {}).get("workspace") is not True:
                todo.append(f"{rel}: replace its [lints] table with `workspace = true` (Cargo refuses both)")
    elif "lints" not in manifest:
        repo.write("Cargo.toml", root_text.rstrip("\n") + "\n" + BASELINE_PKG, "add the [lints] baseline")
    else:
        for tool, lint, level in (("rust", "unsafe_op_in_unsafe_fn", "deny"),
                                  ("clippy", "undocumented_unsafe_blocks", "warn"),
                                  ("clippy", "allow_attributes_without_reason", "warn")):
            if lint not in (manifest["lints"].get(tool) or {}):
                todo.append(f'Cargo.toml: add {lint} = "{level}" under [lints.{tool}]')
    return todo


def insert_inner_attr(src, attr):
    """Places `attr` after the leading inner docs and inner attributes of a crate root."""
    bom = "\ufeff" if src.startswith("\ufeff") else ""
    rest = src.removeprefix("\ufeff")
    shebang = ""
    if rest.startswith("#!") and not re.match(r"#!\s*\[", rest):
        nl = rest.find("\n")
        split = len(rest) if nl < 0 else nl + 1
        shebang, rest = rest[:split], rest[split:]
    _, bare = policy.lex(rest)
    i, n, last_inner = 0, len(rest), 0
    while i < n:
        if rest[i].isspace():
            i += 1
            continue
        if rest.startswith("//", i):
            if rest.startswith("///", i) and not rest.startswith("////", i):
                break
            j = rest.find("\n", i)
            j = n if j < 0 else j
            line_end = j - 1 if j > i and rest[j - 1] == "\r" else j
            if rest.startswith("//!", i) or (last_inner > 0 and "\n" not in rest[last_inner:i]):
                last_inner = line_end
            i = j
            continue
        if rest.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if rest.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif rest.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth or (rest.startswith("/**", i) and j - i > 4 and rest[i + 3] != "*"):
                break
            if rest.startswith("/*!", i) or (last_inner > 0 and "\n" not in rest[last_inner:i]):
                last_inner = j
            i = j
            continue
        m = policy.ATTR.match(bare, i)
        if m and m.group(1) == "!":
            end = policy.attr_end(bare, m.end())
            last_inner = end
            i = end
            continue
        break
    prefix = bom + shebang
    if last_inner == 0:
        lead = "\n" if shebang and not shebang.endswith("\n") else ""
        return f"{prefix}{lead}{attr}\n{rest}"
    head, tail = rest[:last_inner], rest[last_inner:]
    if not tail:
        return f"{prefix}{head}\n{attr}\n"
    if tail.startswith("\r\n"):
        return f"{prefix}{head}\r\n{attr}{tail}"
    if tail.startswith("\n"):
        return f"{prefix}{head}\n{attr}{tail}"
    return f"{prefix}{head}\n{attr}\n{tail.lstrip(' \t')}"


def manifest_forbids_unsafe(repo, path):
    root_m = tomllib.loads(repo.read("Cargo.toml") or "")
    ws_rust = ((root_m.get("workspace") or {}).get("lints") or {}).get("rust") or {}
    ws_forbids = policy.lint_level(ws_rust.get("unsafe_code")) == "forbid"
    rel = f"{path}/Cargo.toml" if path != "." else "Cargo.toml"
    m = tomllib.loads(repo.read(rel) or "")
    lints = m.get("lints") or {}
    if isinstance(lints, dict) and lints.get("workspace") is True:
        return ws_forbids
    pkg_rust = (lints.get("rust") or {}) if isinstance(lints, dict) else {}
    return policy.lint_level(pkg_rust.get("unsafe_code")) == "forbid"


def source_edits(repo, meta, profile_text, edit):
    prof = tomllib.loads(profile_text)
    crates = prof.get("crates") or {}
    unsafe = set(crates.get("unsafe_allowed") or [])
    libs = set(crates.get("libraries") or [])
    todo = []
    for path, p in sorted(members(repo.root, meta).items()):
        forbids_via_manifest = manifest_forbids_unsafe(repo, path)
        for t in p["targets"]:
            kinds = set(t["kind"])
            src = pathlib.Path(t["src_path"]).resolve().relative_to(repo.root.resolve()).as_posix()
            wants = []
            if path not in unsafe and not forbids_via_manifest and kinds & {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro", "bin"}:
                wants.append(("unsafe_code", "#![forbid(unsafe_code)]"))
            if path in libs and kinds & {"lib", "rlib", "dylib", "proc-macro"}:
                wants.append(("missing_docs", "#![warn(missing_docs)]"))
            for lint, attr in wants:
                text = repo.read(src)
                code, bare = policy.lex(text)
                if policy.has_crate_lint(code, bare, {"forbid", "deny", "warn"}, lint):
                    continue
                if edit:
                    repo.write(src, insert_inner_attr(text, attr), f"add {attr}")
                else:
                    todo.append(f"{src}: add {attr} (or rerun with --edit-sources)")
    return todo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, type=pathlib.Path, help="consumer repository checkout")
    ap.add_argument("--owners", required=True, help="code owners of the policy surface, e.g. @mbras/rust-policy-owners")
    ap.add_argument("--reviewers", help="human team that owns everything else (adds `* TEAM` before the managed block)")
    ap.add_argument("--policy-sha", help="40-hex commit of this repository to pin (default: HEAD when clean)")
    ap.add_argument("--stable", default="1.97.0", help="exact stable toolchain for new profiles")
    ap.add_argument("--msrv"), ap.add_argument("--edition")
    ap.add_argument("--nightly", default=DEFAULT_NIGHTLY, help="pinned nightly for Miri in new profiles")
    ap.add_argument("--libraries", help="comma-separated library crate paths (default: every crate with a lib target)")
    ap.add_argument("--all-features", action="store_true", help="add an --all-features feature set to new profiles")
    ap.add_argument("--default-branch", help="branch for push runs (default: origin HEAD, else unambiguous local main/master)")
    ap.add_argument("--edit-sources", action="store_true", help="insert missing #![forbid(unsafe_code)] and #![warn(missing_docs)]")
    ap.add_argument("--mode", choices=("full", "minimal"), default="full",
                    help="full: reusable workflow, AGENTS.md and CLAUDE.md blocks, CODEOWNERS block, reviewer agents; "
                         "minimal: only the tools, guide, profile, register and lock, for a repository whose own CI "
                         "runs `.rust-policy/check.sh ci --base REF` and whose own harness owns agent instructions and CODEOWNERS")
    ap.add_argument("--checks", help="comma-separated checks for a new profile (default: all); existing profiles are preserved")
    ap.add_argument("--visibility", choices=("internal", "public"), default="internal",
                    help="repository visibility posture for new profiles (public enforces PR disclosure template and catch-all CODEOWNERS warning)")
    ap.add_argument("--no-reviewer", action="store_true", help="do not install the reviewer agents")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = args.target.resolve()
    if not (root / "Cargo.toml").is_file() or not (root / ".git").exists():
        sys.exit(f"{root} must be a git checkout with a Cargo.toml at its root")
    if not re.fullmatch(r"@[\w.-]+(/[\w.-]+)?", args.owners):
        sys.exit("--owners must look like @org/team or @user")
    if args.reviewers and not re.fullmatch(r"@[\w.-]+(/[\w.-]+)?", args.reviewers):
        sys.exit("--reviewers must look like @org/team")
    repo = Repo(root, args.dry_run)
    profile = repo.read("rust-policy.toml")
    new_profile = profile is None
    try:
        existing = tomllib.loads(profile) if profile is not None else None
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"invalid rust-policy.toml: {e}")
    if new_profile:
        validate_creation_args(args)
        full = args.mode == "full"
    else:
        existing_ad = existing.get("adoption", {})
        if not isinstance(existing_ad, dict):
            sys.exit("invalid rust-policy.toml: adoption must be a table")
        full = existing_ad.get("ci", "reusable") == "reusable"
    sha = args.policy_sha if args.policy_sha is not None else kit_sha()
    if sha is not None and (not re.fullmatch(r"[0-9a-f]{40}", sha) or sha == "0" * 40):
        sys.exit("--policy-sha must be a full, non-zero 40-hex commit SHA")
    if sha is None:
        if full:
            sys.exit("full installation needs --policy-sha: this kit is not a clean git commit; no usable workflow pin is available")
        print("warning: source commit unavailable; pass --policy-sha to record release provenance (no reusable workflow in minimal mode)")
        sha = ""
    meta = metadata(root)
    print(f"rust-policy {kit.VERSION} (guide {kit.GUIDE_VERSION}) -> {root}")
    if new_profile:   # computed first: it may stop the install before anything is written
        profile, edition = render_profile(root, args, meta)
    else:
        log("existing rust-policy.toml is authoritative; --mode, --checks, toolchains, --libraries and --all-features are creation defaults")

    policy.ROOT = root
    try:
        prof = tomllib.loads(profile)
        for key in ("policy", "adoption", "toolchain", "crates", "build"):
            if key in prof and not isinstance(prof[key], dict):
                raise policy.ProfileError([f"{key} must be a table"])
        plan = policy.validate_profile(prof)
    except (tomllib.TOMLDecodeError, policy.ProfileError) as e:
        problems = e.args[0] if isinstance(e, policy.ProfileError) else [str(e)]
        sys.exit("invalid rust-policy.toml:\n  " + "\n  ".join(problems))
    ad = prof.get("adoption") or {}
    if not isinstance(ad, dict):
        sys.exit("invalid rust-policy.toml: adoption must be a table")
    full = plan["ci"] == "reusable"
    branch = None
    if full:
        branch = args.default_branch if args.default_branch is not None else default_branch(root)
        valid = subprocess.run(["git", "check-ref-format", "--branch", branch], cwd=root, capture_output=True, text=True)
        if valid.returncode != 0:
            sys.exit(f"invalid --default-branch: {branch!r}")
    for dst, src in kit.MANAGED_FILES.items():
        repo.write(dst, kit.text(src), "managed file")
    optional = {k: v for k, v in kit.OPTIONAL_FILES.items()
                if not (args.no_reviewer or not full) or "rust-reviewer" not in k}
    has_blocks = ad.get("agents", "block") == "block"
    if has_blocks:
        optional.pop("docs/rust-policy/AGENTS.rust.md")
    for dst, src in optional.items():
        repo.write(dst, kit.text(src), "managed file")
    for dst, src in kit.MANAGED_BLOCKS.items() if has_blocks else ():
        repo.write(dst, replace_block(repo.read(dst), kit.text(src), "<!-- BEGIN rust-policy", "<!-- END rust-policy -->", False),
                   "managed block")
    if not full:
        return finish(repo, args, sha, profile, meta, minimal=True, optional=optional, has_blocks=has_blocks)
    caller = kit.text(kit.CALLER_TEMPLATE)
    for k, v in {"{policy_repo}": kit.POLICY_REPO, "{policy_sha}": sha, "{version}": kit.VERSION,
                 "{default_branch}": json.dumps(branch)}.items():
        caller = caller.replace(k, v)
    repo.write(kit.CALLER, caller, f"caller pinned to {kit.POLICY_REPO}@{sha[:12]}")

    owners_path = next((p for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS") if repo.read(p) is not None),
                       ".github/CODEOWNERS")
    block = kit.text("templates/CODEOWNERS.block").replace("{owners}", args.owners)
    existing = repo.read(owners_path)
    if existing:   # the block must be last, so move it there on every run
        existing = re.sub(r"^# BEGIN rust-policy.*?^# END rust-policy[^\n]*\n?", "", existing, flags=re.S | re.M)
    star_rules = [
        parts[1:]
        for parts in (line.split("#", 1)[0].split() for line in (existing or "").splitlines())
        if parts and parts[0] == "*"
    ]
    has_catchall = bool(star_rules and star_rules[-1])
    if args.reviewers and not has_catchall:
        existing = (existing or "").rstrip("\n") + ("\n" if existing else "") + \
            f"# Every change needs a human review; agent identities are not in this team.\n* {args.reviewers}\n"
    repo.write(owners_path, replace_block(existing, block, "# BEGIN rust-policy", "# END rust-policy", True),
               "code owners for the policy surface")
    repo.write(".gitattributes", replace_block(repo.read(".gitattributes"), kit.text("templates/gitattributes.block"),
                                               "# BEGIN rust-policy", "# END rust-policy", True), "LF for managed files")

    return finish(repo, args, sha, profile, meta, minimal=False, optional=optional, has_blocks=has_blocks)


def finish(repo, args, sha, profile, meta, minimal, optional, has_blocks):
    root = repo.root
    edition = tomllib.loads(profile)["toolchain"]["edition"]
    if repo.read("rust-policy.toml") is None:
        repo.write("rust-policy.toml", profile, "project profile (review it)")
    stable = tomllib.loads(profile)["toolchain"]["stable"]
    project = {
        "rust-toolchain.toml": f'[toolchain]\nchannel = "{stable}"\ncomponents = ["clippy", "rustfmt"]\nprofile = "minimal"\n',
        "rustfmt.toml": kit.text("templates/rustfmt.toml").replace("{edition}", edition),
        "clippy.toml": kit.text("templates/clippy.toml"),
        "deny.toml": kit.text("templates/deny.toml"),
        "docs/rust-policy/exceptions.toml": kit.text("templates/exceptions.toml"),
        "opencode.json": kit.text("templates/opencode.json"),
        ".github/pull_request_template.md": kit.text("templates/pull_request_template.md"),
    }
    if minimal:   # the project's own harness owns everything else; only the register is needed
        project = {k: v for k, v in project.items()
                   if k in ("docs/rust-policy/exceptions.toml", "rust-toolchain.toml", "deny.toml")}
    for dst, content in project.items():
        if repo.read(dst) is None and not (
            (dst == "rustfmt.toml" and repo.read(".rustfmt.toml") is not None)
            or (dst == "clippy.toml" and repo.read(".clippy.toml") is not None)
        ):
            repo.write(dst, content, "project-owned starting point")
    if not minimal:
        opencode = repo.read("opencode.json")
        if opencode is not None and "rust-policy.toml" not in opencode:
            print("  todo: add \"rust-policy.toml\" to `instructions` in opencode.json")

    owners = (tomllib.loads(profile).get("policy") or {}).get("owners")
    if owners != [args.owners]:
        print(f"  todo: rust-policy.toml policy.owners is {owners}; set it to [\"{args.owners}\"] to match CODEOWNERS")
    todo = cargo_edits(repo, meta) if not minimal else \
        ["Cargo.toml: lint tables are optional in minimal mode; check.sh forbids the three baseline lints on the command line"]
    if minimal and not any(repo.read(p) is not None for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")):
        todo.append(".github/CODEOWNERS: add CODEOWNERS covering the policy surface for policy.owners (required by P16)")
    for path, p in members(root, meta).items():
        if not p.get("rust_version"):
            todo.append(f"{path}/Cargo.toml: declare rust-version (or rust-version.workspace = true)")
    todo += source_edits(repo, meta, profile, args.edit_sources)

    files, blocks, caller_sum = kit.sums()
    installed_files = set(kit.MANAGED_FILES) | set(optional) | {p for p in kit.OPTIONAL_FILES if (root / p).is_file()}
    for p in sorted(kit.OPTIONAL_FILES):
        if p not in optional and (root / p).is_file():
            todo.append(f"{p}: no longer selected by this mode; remove it if unused")
    lock = ["# Written by rust-policy install.py; CI trusts the sums in the pinned workflow, not this file.",
            f'version = "{kit.VERSION}"', f'guide = "{kit.GUIDE_VERSION}"', f'source = "{kit.POLICY_REPO}"',
            f'commit = "{sha}"', f'caller = "{caller_sum if not minimal else ""}"', "", "[files]"]
    lock += [f'"{p}" = "{d}"' for p, d in files.items() if p in installed_files] + ["", "[blocks]"]
    if has_blocks:
        lock += [f'"{p}" = "{d}"' for p, d in blocks.items()]
    repo.write(".rust-policy/lock.toml", "\n".join(lock) + "\n", "release record")

    repo.flush()
    print(f"{len(repo.changed)} file(s) {'would change' if args.dry_run else 'changed'}.")
    for t in todo:
        print(f"  todo: {t}")
    print("Next: run .rust-policy/check.sh ci --base <base-commit>, fix or register what fails, open a pull request.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
