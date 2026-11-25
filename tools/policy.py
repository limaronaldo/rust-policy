#!/usr/bin/env python3
"""rust-policy: profile validation, CI plan, managed-file verification and conformance checks.

Managed file: CI verifies its sha256 before running it. Run it with `python3 -I .rust-policy/policy.py`
from anywhere inside the repository. Python 3.11 or newer, standard library only.

Commands:
  plan              validate rust-policy.toml and print the CI plan (also appended to $GITHUB_OUTPUT)
  get KEY           print one plan value: toolchain, msrv, edition, nightly, run_miri, miri_args, ...
  verify            P01 only: managed files, managed blocks and the caller workflow match the release
  check [--base R]  every conformance check, P00 to P19; --base compares with revision R
  ratchet FILE [--update|--init]  compare clippy JSON in FILE with docs/rust-policy/ratchet.toml;
                    --update lowers counts, --init records today's counts for new entries
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib

PROFILE = "rust-policy.toml"
REGISTER = "docs/rust-policy/exceptions.toml"
RATCHET = "docs/rust-policy/ratchet.toml"
LOCK = ".rust-policy/lock.toml"
CALLER = ".github/workflows/rust-policy.yml"
# Required in every consumer; the optional ones are verified when present.
MANAGED_FILES = (".rust-policy/check.sh", ".rust-policy/policy.py", "docs/rust-policy/guide.md")
OPTIONAL_FILES = (".claude/agents/rust-reviewer.md", ".codex/agents/rust-reviewer.toml",
                  "docs/rust-policy/AGENTS.rust.md")
CHECKS = ("fmt", "clippy", "test", "doc", "msrv", "deny", "miri")   # conformance always runs
MANAGED_BLOCKS = ("AGENTS.md", "CLAUDE.md")
BLOCK = re.compile(r"^<!-- BEGIN rust-policy .*?^<!-- END rust-policy -->\n?", re.S | re.M)
OWNERS_BLOCK = re.compile(r"^# BEGIN rust-policy.*?^# END rust-policy[^\n]*\n?", re.S | re.M)

OS_ALLOWED = ("ubuntu-24.04", "ubuntu-24.04-arm")
FEATURE_TOKEN = re.compile(r"--no-default-features|--all-features|--features=[A-Za-z0-9_+./:,-]+")
RUSTFLAG_TOKEN = re.compile(r"--cfg=[A-Za-z_][A-Za-z0-9_]*|-Ctarget-cpu=[A-Za-z0-9_.-]+|-Ctarget-feature=[+-][A-Za-z0-9_.+,-]+")
VERSION3 = re.compile(r"\d+\.\d+\.\d+")
VERSION2 = re.compile(r"\d+\.\d+(\.\d+)?")
NIGHTLY = re.compile(r"nightly-\d{4}-\d{2}-\d{2}")
REL_PATH = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")
OWNER = re.compile(r"@[\w.-]+(/[\w.-]+)?")
EXC_ID = re.compile(r"EXC-\d{4}")

# Lints that back a MUST with no exception path. CI forbids the first four on the command line,
# and forbids unsafe_code for every crate outside crates.unsafe_allowed.
FORBIDDEN_LINTS = {"unsafe_op_in_unsafe_fn", "clippy::undocumented_unsafe_blocks",
                   "clippy::allow_attributes_without_reason", "clippy::missing_safety_doc", "unsafe_code"}
# Lint groups that silence whole families; allowing one anywhere needs an exception for that group.
GROUP_LINTS = {"warnings", "unused", "future_incompatible", "nonstandard_style", "rust_2018_idioms",
               "rust_2021_compatibility", "rust_2024_compatibility", "clippy::all", "clippy::pedantic",
               "clippy::nursery", "clippy::restriction", "clippy::style", "clippy::complexity", "clippy::perf",
               "clippy::correctness", "clippy::suspicious", "clippy::cargo"}
BASELINE = {("rust", "unsafe_op_in_unsafe_fn"): {"deny", "forbid"},
            ("clippy", "undocumented_unsafe_blocks"): {"warn", "deny", "forbid"},
            ("clippy", "allow_attributes_without_reason"): {"warn", "deny", "forbid"}}
# MUST rows of the guide (guide/rules.toml): True allows an exception, "partial" allows one for the
# part the guide names, False refuses it. The central self-test keeps this table equal to rules.toml.
RULES = {f"M{i:02d}": True for i in range(1, 35)}
RULES.update({r: False for r in ("M03", "M04", "M05", "M07", "M08", "M12", "M13", "M14", "M15", "M16",
                                 "M18", "M19", "M20", "M21", "M22", "M24", "M25", "M28", "M31", "M32", "M34")})
RULES.update({"M11": "partial", "M23": "partial"})
KIT_RULES = {"P05", "P15", "P17", "P19"}   # conformance checks that accept a registered exception
KIT_RULE_KIND = {"P05": "cargo-config", "P15": "workflow", "P17": "manifest", "P19": "guide"}
KINDS = {"lint", "lint-config", "deny-ignore", "cargo-config", "workflow", "manifest", "guide"}
EXC_MAX_DAYS = 180
CARGO_CONFIG_ALLOWED = {"net", "http", "registries", "registry", "term", "cargo-new"}
CARGO_BUILD_ALLOWED = {"jobs", "target-dir", "incremental", "pipelining", "dep-info-basedir"}
# Patterns without a leading slash match at any depth (CODEOWNERS follows gitignore rules).
PROTECTED = ("AGENTS.md", "CLAUDE.md", "AGENTS.override.md", "/opencode.json", "/rust-policy.toml",
             "rust-toolchain.toml", "rustfmt.toml", ".rustfmt.toml", "clippy.toml", ".clippy.toml", "/deny.toml",
             ".cargo/", "/.github/", "/.claude/", "/.codex/", "/.opencode/", "/.rust-policy/", "/docs/rust-policy/",
             "/CODEOWNERS", "/docs/CODEOWNERS", "/.gitattributes", "build.rs")
AGENTS_CHAIN_LIMIT = 24 * 1024   # Codex stops at 32 KiB in total; leave room for a personal file
TEST_KINDS = {"test", "bench", "example"}


def root_dir():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True).stdout.strip()
        return pathlib.Path(out)
    except (OSError, subprocess.CalledProcessError):
        return pathlib.Path.cwd()


ROOT = root_dir()


def read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.is_file() else None


def load_toml(rel):
    text = read(rel)
    return None if text is None else tomllib.loads(text)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mask_caller(text):
    """The caller is compared without its pinned reference and default-branch name."""
    text = re.sub(r"(?m)^(\s*uses:\s*).*$", r"\1<masked>", text)
    return re.sub(r"(?m)^(\s*branches:\s*).*$", r"\1<masked>", text)


def block_of(text):
    m = BLOCK.search(text or "")
    return m.group(0) if m else None


def git(*args):
    env = {k: v for k, v in os.environ.items() if k not in (
        "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"
    )}
    r = subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def tracked(*patterns):
    out = git("ls-files", "-z", "--", *patterns)
    return [f for f in (out or "").split("\0") if f]


def version_tuple(v):
    return tuple(int(x) for x in str(v).split("."))


def norm_version(v):
    t = version_tuple(v)
    return t + (0,) * (3 - len(t))


def rel(path):
    try:
        return pathlib.Path(path).resolve().relative_to(ROOT.resolve()).as_posix() or "."
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- profile and plan

class ProfileError(Exception):
    pass


def validate_profile(p):
    """Returns the normalized plan; raises ProfileError with every problem found."""
    errs = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    def table_of(name):
        v = p.get(name)
        if v is None:
            return {}
        if not isinstance(v, dict):
            errs.append(f"{name} must be a table")
            return {}
        return v

    if p is None:
        raise ProfileError([f"{PROFILE} is missing"])
    if not isinstance(p, dict):
        raise ProfileError([f"{PROFILE} must be a TOML table"])
    top = {"schema", "policy", "adoption", "toolchain", "crates", "build", "feature_sets"}
    need(p.get("schema") == 1, "schema must be 1")
    need(set(p) <= top, f"unknown top-level keys: {sorted(set(p) - top)}")
    policy_tbl = table_of("policy")
    need(set(policy_tbl) <= {"owners", "unsafe_owners"}, f"unknown policy keys: {sorted(set(policy_tbl) - {'owners', 'unsafe_owners'})}")
    owners = policy_tbl.get("owners")
    need(isinstance(owners, list) and owners and all(isinstance(o, str) and OWNER.fullmatch(o) for o in owners),
         "policy.owners must list the code owners of the policy surface, e.g. [\"@mbras/rust-policy-owners\"]")
    unsafe_owners = policy_tbl.get("unsafe_owners", [])
    need(isinstance(unsafe_owners, list) and all(isinstance(o, str) and OWNER.fullmatch(o) for o in unsafe_owners),
         "policy.unsafe_owners must list valid code owners, e.g. [\"@mbras/rust-safety-owners\"]")
    adoption_tbl = table_of("adoption")
    need(set(adoption_tbl) <= {"checks", "ci", "agents", "visibility"}, f"unknown adoption keys: {sorted(adoption_tbl)}")
    checks = adoption_tbl.get("checks", list(CHECKS))
    need(isinstance(checks, list) and set(checks) <= set(CHECKS) and len(set(checks)) == len(checks),
         f"adoption.checks must list distinct checks from {list(CHECKS)}")
    ci = adoption_tbl.get("ci", "reusable")
    need(ci in ("reusable", "standalone"), "adoption.ci must be \"reusable\" or \"standalone\"")
    agents = adoption_tbl.get("agents", "block")
    need(agents in ("block", "external"), "adoption.agents must be \"block\" or \"external\"")
    visibility = adoption_tbl.get("visibility", "internal")
    need(visibility in ("internal", "public"), "adoption.visibility must be \"internal\" or \"public\"")
    tc = table_of("toolchain")
    need(set(tc) <= {"stable", "msrv", "edition", "nightly"}, f"unknown toolchain keys: {sorted(tc)}")
    stable, msrv, edition = tc.get("stable"), tc.get("msrv"), tc.get("edition")
    need(isinstance(stable, str) and VERSION3.fullmatch(stable), "toolchain.stable must be an exact version such as 1.97.0")
    need(isinstance(msrv, str) and VERSION2.fullmatch(msrv), "toolchain.msrv must be a version such as 1.85")
    need(edition in ("2021", "2024"), "toolchain.edition must be \"2021\" or \"2024\"")
    if isinstance(stable, str) and VERSION3.fullmatch(stable) and isinstance(msrv, str) and VERSION2.fullmatch(msrv):
        need(norm_version(msrv) <= norm_version(stable), "toolchain.msrv is newer than toolchain.stable")
    crates = table_of("crates")
    need(set(crates) <= {"libraries", "unsafe_allowed", "miri_exempt", "critical"}, f"unknown crates keys: {sorted(crates)}")
    paths, names = {}, {}
    for key in ("libraries", "unsafe_allowed", "miri_exempt", "critical"):
        vals = crates.get(key, [])
        need(isinstance(vals, list) and all(isinstance(v, str) for v in vals), f"crates.{key} must be a list of paths")
        vals = [v.rstrip("/") for v in vals if isinstance(v, str)]
        names[key] = []
        for v in vals:
            need(v == "." or (REL_PATH.fullmatch(v) and "." not in v.split("/") and ".." not in v.split("/")), f"crates.{key}: bad path {v!r}")
            try:
                manifest = load_toml(f"{v}/Cargo.toml") if (ROOT / v / "Cargo.toml").is_file() else None
            except tomllib.TOMLDecodeError as e:
                errs.append(f"crates.{key}: {v}/Cargo.toml is not valid TOML: {e}")
                manifest = None
            pkg = manifest.get("package") if isinstance(manifest, dict) else None
            name = pkg.get("name") if isinstance(pkg, dict) else None
            need(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name), f"crates.{key}: {v}/Cargo.toml has no package")
            names[key].append(str(name))
        paths[key] = vals
    need(set(paths["miri_exempt"]) <= set(paths["unsafe_allowed"]), "crates.miri_exempt must be a subset of crates.unsafe_allowed")
    miri_names = [n for v, n in zip(paths["unsafe_allowed"], names["unsafe_allowed"]) if v not in paths["miri_exempt"]]
    run_miri = "miri" in (checks if isinstance(checks, list) else []) and bool(miri_names)
    nightly = tc.get("nightly", "")
    if run_miri:
        need(isinstance(nightly, str) and NIGHTLY.fullmatch(nightly),
             "toolchain.nightly (nightly-YYYY-MM-DD) is required when Miri runs (crates.unsafe_allowed minus miri_exempt)")
    elif nightly:
        need(isinstance(nightly, str) and NIGHTLY.fullmatch(nightly), "toolchain.nightly must look like nightly-YYYY-MM-DD")
    build = table_of("build")
    need(set(build) <= {"rustflags"}, f"unknown build keys: {sorted(build)}")
    flags = build.get("rustflags", [])
    need(isinstance(flags, list) and all(isinstance(f, str) and RUSTFLAG_TOKEN.fullmatch(f) for f in flags),
         "build.rustflags accepts only --cfg=name, -Ctarget-cpu=x and -Ctarget-feature=+x tokens")
    if isinstance(flags, list):
        for f in flags:
            if f in ("--cfg=miri", "--cfg=test"):
                errs.append(f"build.rustflags forbids `{f}` (would spoof Miri or test-only cfg gates)")
    sets = p.get("feature_sets")
    if sets is None:
        sets = [{"id": "default", "args": ""}]
    need(isinstance(sets, list) and sets, "feature_sets must be a non-empty array of tables")
    matrix, ids = [], set()
    for s in sets if isinstance(sets, list) else []:
        if not isinstance(s, dict):
            errs.append("feature_sets entries must be tables")
            continue
        need(set(s) <= {"id", "args", "os"}, f"feature_sets: unknown keys {sorted(set(s) - {'id', 'args', 'os'})}")
        sid, args, osl = s.get("id"), s.get("args", ""), s.get("os", "ubuntu-24.04")
        need(isinstance(sid, str) and re.fullmatch(r"[a-z0-9-]{1,24}", sid), f"feature_sets.id is invalid: {sid!r}")
        need(sid not in ids, f"feature_sets.id is repeated: {sid!r}")
        ids.add(sid)
        need(isinstance(args, str) and all(FEATURE_TOKEN.fullmatch(t) for t in args.split()),
             f"feature_sets.args allows only --no-default-features, --all-features and --features=a,b: {args!r}")
        need(osl in OS_ALLOWED, f"feature_sets.os must be one of {OS_ALLOWED}: {osl!r}")
        norm_args = " ".join(args.split()) if isinstance(args, str) else ""
        matrix.append({"id": sid, "args": norm_args, "os": osl})
    ratchet, ratchet_errs = load_ratchet()
    errs += ratchet_errs
    if errs:
        raise ProfileError(errs)
    unsafe_names = names["unsafe_allowed"]
    out = {f"run_{c}": "true" if c in checks else "false" for c in CHECKS}
    return out | {
        "toolchain": stable, "msrv": ".".join(map(str, norm_version(msrv))), "edition": edition, "nightly": nightly or "",
        "matrix": json.dumps(matrix), "msrv_matrix": json.dumps([{**m, "os": "ubuntu-24.04"} for m in matrix]),
        "checks": " ".join(checks), "ci": ci, "agents": agents, "visibility": visibility,
        "run_miri": "true" if run_miri else "false",
        "miri_args": " ".join(f"-p {n}" for n in miri_names),
        "run_ratchet": "true" if ratchet else "false",
        "ratchet_packages": " ".join(sorted({e["package"] for e in ratchet})),
        "feature_args": "\n".join(m["args"] or "-" for m in matrix),
        "safe_args": " ".join(f"--exclude {n}" for n in unsafe_names),
        "unsafe_args": " ".join(f"-p {n}" for n in unsafe_names),
        "rustflags": " ".join(flags),
        "owners": " ".join(owners),
        "unsafe_owners": " ".join(unsafe_owners),
        "libraries": paths["libraries"], "unsafe_allowed": paths["unsafe_allowed"],
        "miri_exempt": paths["miri_exempt"], "critical": paths["critical"], "ratchet": ratchet,
    }


RATCHET_LINT = re.compile(r"(clippy::)?[a-z][a-z0-9_]*")


def load_ratchet():
    """docs/rust-policy/ratchet.toml: lints not met yet, counted per file, that may only go down."""
    try:
        data = load_toml(RATCHET) if (ROOT / RATCHET).is_file() else {}
    except tomllib.TOMLDecodeError as e:
        return [], [f"{RATCHET}: invalid TOML: {e}"]
    if not isinstance(data, dict):
        return [], [f"{RATCHET}: must be a TOML table"]
    errs, entries = [], []
    if set(data) - {"ratchet"}:
        errs.append(f"{RATCHET}: unknown top-level keys {sorted(set(data) - {'ratchet'})}")
    ratchet_list = data.get("ratchet", [])
    if not isinstance(ratchet_list, list):
        return [], errs + [f"{RATCHET}: ratchet must be an array of tables"]
    seen = set()
    for e in ratchet_list:
        if not isinstance(e, dict):
            errs.append(f"{RATCHET}: ratchet entries must be tables")
            continue
        lint, pkg, exc, files = e.get("lint"), e.get("package"), e.get("exception"), e.get("files", {})
        where = f"{RATCHET}: ratchet {pkg}/{lint}"
        if set(e) - {"lint", "package", "exception", "files"}:
            errs.append(f"{where}: unknown keys {sorted(set(e) - {'lint', 'package', 'exception', 'files'})}")
        if not (isinstance(lint, str) and RATCHET_LINT.fullmatch(lint)) or lint in FORBIDDEN_LINTS or lint in GROUP_LINTS:
            errs.append(f"{where}: lint must name one lint, not a group or a lint without an exception path")
        if not (isinstance(pkg, str) and re.fullmatch(r"[A-Za-z0-9_-]+", pkg)):
            errs.append(f"{where}: package must be a workspace package name")
        if not (isinstance(exc, str) and EXC_ID.fullmatch(exc)):
            errs.append(f"{where}: exception must cite the register entry that approved this ratchet (EXC-nnnn)")
        if not (isinstance(files, dict) and all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in files.items())):
            errs.append(f"{where}: files maps repository paths to counts")
        if (pkg, lint) in seen:
            errs.append(f"{where}: defined twice")
        seen.add((pkg, lint))
        entries.append({"lint": lint, "package": pkg, "exception": exc, "files": files if isinstance(files, dict) else {}})
    return entries, errs


def cmd_plan():
    try:
        plan = validate_profile(load_toml(PROFILE))
    except tomllib.TOMLDecodeError as e:
        print(f"P00 {PROFILE}: invalid TOML: {e}", file=sys.stderr)
        return 1
    except ProfileError as e:
        for m in e.args[0]:
            print(f"P00 {PROFILE}: {m}", file=sys.stderr)
        return 1
    lines = [f"{k}={v}" for k, v in plan.items() if isinstance(v, str) and "\n" not in v]
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def cmd_get(key):
    try:
        plan = validate_profile(load_toml(PROFILE))
    except tomllib.TOMLDecodeError as e:
        print(f"{PROFILE}: invalid TOML: {e}", file=sys.stderr)
        return 1
    except ProfileError as e:
        print("\n".join(e.args[0]), file=sys.stderr)
        return 1
    if key.startswith("ratchet_allow:"):
        package = key.split(":", 1)[1]
        print(" ".join(f"-A {lint}" for lint in sorted({e["lint"] for e in plan["ratchet"]
                                                     if e["package"] == package})))
        return 0
    if key not in plan or not isinstance(plan[key], str):
        print(f"unknown key {key!r}", file=sys.stderr)
        return 64
    print(plan[key])
    return 0


# ---------------------------------------------------------------- findings

class Report:
    def __init__(self):
        self.findings, self.warnings = [], []

    def fail(self, check, msg):
        self.findings.append(f"{check} {msg}")

    def warn(self, check, msg):
        self.warnings.append(f"{check} {msg}")


def expected_sums(r):
    """CI passes the sums embedded in the pinned workflow; local runs use the lock file."""
    if os.environ.get("POLICY_FILE_SUMS"):
        def parse(var):
            out = {}
            for line in os.environ.get(var, "").splitlines():
                if line.strip():
                    digest, path = line.split(maxsplit=1)
                    out[path.strip()] = digest
            return out
        return "the pinned workflow", parse("POLICY_FILE_SUMS"), parse("POLICY_BLOCK_SUMS"), \
            os.environ.get("POLICY_CALLER_SUM", "")
    # Explicit standalone adopters use their project-owned lock, including in their own CI.
    # Reusable CI must receive the immutable release sums from the pinned workflow.
    if os.environ.get("GITHUB_ACTIONS") == "true" and adoption()["ci"] != "standalone":
        r.fail("P01", "POLICY_FILE_SUMS is not set; run conformance only from the pinned rust-ci.yml")
        return "nothing", {}, {}, ""
    try:
        lock = load_toml(LOCK) or {}
    except tomllib.TOMLDecodeError as e:
        r.fail("P01", f"{LOCK}: invalid TOML: {e}")
        return "nothing", {}, {}, ""
    files = lock.get("files") if isinstance(lock.get("files"), dict) else {}
    blocks = lock.get("blocks") if isinstance(lock.get("blocks"), dict) else {}
    caller = lock.get("caller", "") if isinstance(lock.get("caller", ""), str) else ""
    return LOCK, files, blocks, caller


def adoption():
    """The adoption table, read leniently so that P01 works even when the profile is invalid."""
    try:
        prof = load_toml(PROFILE) or {}
        a = prof.get("adoption") if isinstance(prof, dict) else {}
    except tomllib.TOMLDecodeError:
        a = {}
    if not isinstance(a, dict):
        a = {}
    return {"ci": a.get("ci", "reusable"), "agents": a.get("agents", "block")}


def check_managed(r):
    source, files, blocks, caller = expected_sums(r)
    if not files:
        if source != "nothing":
            r.fail("P01", f"no expected sums found in {source}; reinstall with rust-policy's install.py")
        return
    mode = adoption()
    required = MANAGED_FILES + (("docs/rust-policy/AGENTS.rust.md",) if mode["agents"] == "external" else ())
    for path in required + tuple(f for f in OPTIONAL_FILES if f not in required):
        p = ROOT / path
        if path not in required and not p.is_file():
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
        if files.get(path) != got:
            r.fail("P01", f"{path}: differs from {source} ({'missing' if got == 'missing' else 'modified'}); "
                          "managed files change only through a rust-policy release")
    for path in MANAGED_BLOCKS if mode["agents"] == "block" else ():
        if (ROOT / path).is_symlink():
            r.fail("P01", f"{path}: must be a regular file, not a symlink")
        b = block_of(read(path))
        if b is None:
            r.fail("P01", f"{path}: the rust-policy managed block is missing")
        elif blocks.get(path) != sha256(b):
            r.fail("P01", f"{path}: the managed block differs from {source}")
    text = read(CALLER)
    if mode["ci"] == "standalone":
        if os.environ.get("POLICY_FILE_SUMS"):
            r.fail("P01", "adoption.ci is \"standalone\" but this run comes from the reusable workflow; set it to \"reusable\"")
        return
    if text is None:
        r.fail("P01", f"{CALLER} is missing")
    elif caller != sha256(mask_caller(text)):
        r.fail("P01", f"{CALLER} differs from the release template (only the pinned reference and branch may differ)")


def cmd_verify():
    r = Report()
    check_managed(r)
    for f in r.findings:
        print(f, file=sys.stderr)
    print("managed content:", "ok" if not r.findings else f"{len(r.findings)} finding(s)")
    return 1 if r.findings else 0


# ---------------------------------------------------------------- rust source scanning

CHAR_LIT = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}|.)|[^\\'\n])'")
RAW_STR = re.compile(r'(?:b|c)?r(#*)"')


def lex(src):
    """Returns (code, bare): comments become spaces in both; string and char literal contents stay in
    `code` and become spaces in `bare`. Both keep the length and line breaks of `src`."""
    code, bare, i, n = [], [], 0, len(src)

    def blank(s):
        return re.sub(r"[^\n]", " ", s)

    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            code.append(" " * (j - i)), bare.append(" " * (j - i))
            i = j
            continue
        if src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            code.append(blank(src[i:j])), bare.append(blank(src[i:j]))
            i = j
            continue
        prev = src[i - 1] if i else " "
        m = RAW_STR.match(src, i)
        if m and not (prev.isalnum() or prev == "_"):
            end = src.find('"' + m.group(1), m.end())
            end = n if end < 0 else end + 1 + len(m.group(1))
            code.append(src[i:end]), bare.append(src[i:m.end()] + blank(src[m.end():end - 1 - len(m.group(1))]) + src[end - 1 - len(m.group(1)):end])
            i = end
            continue
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            j = min(j, n - 1)
            code.append(src[i:j + 1]), bare.append('"' + blank(src[i + 1:j]) + '"')
            i = j + 1
            continue
        if c == "'":
            m = CHAR_LIT.match(src, i)
            if m:
                code.append(m.group(0)), bare.append("'" + blank(m.group(0)[1:-1]) + "'")
                i = m.end()
                continue
        code.append(c), bare.append(c)
        i += 1
    return "".join(code), "".join(bare)


def strip_comments(src):
    return lex(src)[0]


def attr_end(bare, j):
    """Index just after the `]` that closes an attribute whose `[` ends at j (strings are blank)."""
    depth, n = 1, len(bare)
    while j < n and depth:
        if bare[j] == "[":
            depth += 1
        elif bare[j] == "]":
            depth -= 1
        j += 1
    return j


ATTR = re.compile(r"#\s*(!?)\s*\[")
SUPPRESS = re.compile(r"\b(?:r#)?(allow|expect)\s*\(")
RUSTFMT_SKIP = re.compile(r"\b(?:r#)?rustfmt\s*::\s*(?:r#)?skip\b|\b(?:r#)?rustfmt_skip\b")
IGNORE = re.compile(r"(?:^#\s*\[\s*(?:r#)?ignore\b|\bcfg_attr\s*\(.*?,\s*(?:r#)?ignore\b)", re.S)
TEST_MOD_OPEN = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\][^;{}]*?\bmod\s+\w+\s*\{\s*(#\s*!\s*\[[^\]]*\]\s*)*$", re.S)
MOD_ITEM = re.compile(r"\s*(pub(\s*\([^)]*\))?\s+)?mod\s+(\w+)\s*([;{])")
TEST_MOD_DECL = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*(?:#\s*\[[^\]]*\]\s*)*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+(\w+)\s*;")


def attributes(code, bare):
    """Yields (inner, start, end, text, depth) for every attribute; depth is the brace depth."""
    depth, pos = 0, 0
    for m in ATTR.finditer(bare):
        depth += bare.count("{", pos, m.start()) - bare.count("}", pos, m.start())
        pos = m.start()
        end = attr_end(bare, m.end())
        yield m.group(1) == "!", m.start(), end, code[m.start():end], depth


def lint_names(bare_attr):
    """Lint paths named in allow/expect clauses of a bare attribute, without the reason."""
    out = []
    for m in SUPPRESS.finditer(bare_attr):
        depth, pos, n = 1, m.end(), len(bare_attr)
        while pos < n and depth:
            if bare_attr[pos] == "(":
                depth += 1
            elif bare_attr[pos] == ")":
                depth -= 1
            pos += 1
        inner = bare_attr[m.end():pos - 1 if depth == 0 else pos]
        inner = re.sub(r'\b(?:r#)?reason\s*=\s*(?:[bc]?r(#*)".*?"\1|[bc]?"(?:[^"\\]|\\.)*")', "", inner, flags=re.S)
        inner = re.sub(r"\br#(?=[A-Za-z_])", "", inner)
        inner = re.sub(r"\s*::\s*", "::", inner)
        out.extend(x for x in re.findall(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*", inner)
                   if x not in ("allow", "expect", "reason"))
    return out


def has_crate_lint(code, bare, levels, lint):
    """True when a crate-root inner attribute (brace depth 0) sets `lint` at one of `levels`."""
    for inner, start, end, text, depth in attributes(code, bare):
        if not (inner and depth == 0):
            continue
        norm = re.sub(r"\br#(?=[A-Za-z_])", "", bare[start:end])
        norm = re.sub(r"\s*::\s*", "::", norm)
        m = re.fullmatch(r"#\s*!\s*\[\s*(\w+)\s*\((.*)\)\s*\]", norm, re.S)
        if m and m.group(1) in levels and lint in re.findall(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*", m.group(2)):
            return True
    return False


def line_of(code, pos):
    return code.count("\n", 0, pos) + 1


# ---------------------------------------------------------------- conformance

def register_entries(r):
    try:
        reg = load_toml(REGISTER)
    except tomllib.TOMLDecodeError as e:
        r.fail("P10", f"{REGISTER}: invalid TOML: {e}")
        return {}
    if reg is None:
        r.fail("P10", f"{REGISTER} is missing")
        return {}
    if set(reg) - {"exception"}:
        r.fail("P10", f"{REGISTER}: unknown top-level keys {sorted(set(reg) - {'exception'})}")
    excs = reg.get("exception", [])
    if not isinstance(excs, list):
        r.fail("P10", f"{REGISTER}: `exception` must be an array of tables ([[exception]])")
        return {}
    today = datetime.date.today()
    entries = {}
    fields = {"id", "kind", "rule", "lint", "scope", "reason", "approved_in", "created", "expires"}
    for e in excs:
        if not isinstance(e, dict):
            r.fail("P10", f"{REGISTER}: each [[exception]] entry must be a table, got {type(e).__name__}")
            continue
        i = e.get("id")
        if not (isinstance(i, str) and EXC_ID.fullmatch(i)):
            r.fail("P10", f"{REGISTER}: exception id {i!r} must look like EXC-0001")
            continue
        if i in entries:
            r.fail("P10", f"{REGISTER}: {i} is defined twice")
            entries[i]["valid"] = False
            continue
        bad = []
        if set(e) - fields:
            bad.append(f"unknown keys {sorted(set(e) - fields)}")
        if e.get("kind") not in KINDS:
            bad.append(f"kind must be one of {sorted(KINDS)}")
        rule = e.get("rule")
        if rule in RULES:
            if RULES[rule] is False:
                bad.append(f"rule {rule} has no exception path in the guide's MUST table")
        elif rule not in KIT_RULES:
            bad.append(f"rule must be a MUST id (M01 to M34) or one of {sorted(KIT_RULES)}")
        elif e.get("kind") in KINDS and e.get("kind") != KIT_RULE_KIND[rule]:
            bad.append(f"rule {rule} requires kind {KIT_RULE_KIND[rule]!r}, not {e.get('kind')!r}")
        lints = e.get("lint")
        lints = [lints] if isinstance(lints, str) else lints
        if e.get("kind") in ("lint", "lint-config"):
            if not (isinstance(lints, list) and lints and all(isinstance(x, str) and x.strip() and x == x.strip() for x in lints)):
                bad.append("kinds lint and lint-config need `lint`: a lint name or a list of them")
            elif set(lints) & FORBIDDEN_LINTS:
                bad.append(f"{sorted(set(lints) & FORBIDDEN_LINTS)} back a MUST without exceptions")
        elif lints is not None:
            bad.append(f"kind {e.get('kind')!r} cannot specify `lint`")
        for k in ("scope", "reason", "approved_in"):
            if not (isinstance(e.get(k), str) and e[k].strip()):
                bad.append(f"{k} is required")
        created, expires = e.get("created"), e.get("expires")
        if not (type(created) is datetime.date and type(expires) is datetime.date):
            bad.append("created and expires must be TOML dates (2026-11-01)")
        elif created > today:
            bad.append("created must not be in the future")
        elif expires <= created or (expires - created).days > EXC_MAX_DAYS:
            bad.append(f"expires must be after created and at most {EXC_MAX_DAYS} days later")
        elif expires < today:
            bad.append(f"expired on {expires}: renew it in review or remove the deviation")
        elif (expires - today).days <= 14:
            r.warn("P10", f"{i} expires on {expires}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning title=rust-policy exception expiring::{i} expires on {expires}")
        for b in bad:
            r.fail("P10", f"{REGISTER}: {i}: {b}")
        entries[i] = dict(e, valid=not bad, lints=lints if isinstance(lints, list) else [])
    return entries


def in_scope(path, scope):
    scope = scope.rstrip("/")
    return scope in (".", "") or path == scope or path.startswith(scope + "/")


def cite_ok(r, check, where, text, entries, kind, path=None, lints=(), subject=None):
    """The attribute or entry must cite an exception of `kind` that covers its file and every lint."""
    ids = EXC_ID.findall(text)
    if not ids:
        r.fail(check, f"{where}: needs a registered exception id in its reason (EXC-nnnn)")
        return
    cited = []
    for i in ids:
        e = entries.get(i)
        if e is None:
            r.fail(check, f"{where}: {i} is not in {REGISTER}")
        elif not e["valid"]:
            r.fail(check, f"{where}: {i} is not a valid, unexpired exception")
        elif e.get("kind") != kind:
            r.fail(check, f"{where}: {i} has kind {e.get('kind')!r}, expected {kind!r}")
        else:
            cited.append(e)
    if not cited:
        return
    if path is not None and not any(in_scope(path, e["scope"]) for e in cited):
        r.fail(check, f"{where}: {path} is outside the scope of {', '.join(e['id'] for e in cited)}")
    for lint in lints:
        if not any(lint in e["lints"] and (path is None or in_scope(path, e["scope"])) for e in cited):
            r.fail(check, f"{where}: {lint} is not covered by {', '.join(e['id'] for e in cited)} in this scope")
    if subject is not None and not any(e["scope"] == subject for e in cited):
        r.fail(check, f"{where}: {', '.join(e['id'] for e in cited)} does not name {subject} in its scope")


def cargo_metadata(r):
    try:
        out = subprocess.run(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
        r.fail("P03", f"cargo metadata failed: {getattr(e, 'stderr', e)}".strip())
        return None


def members(meta):
    out = {}
    for p in (meta or {}).get("packages", []):
        if p["id"] in meta["workspace_members"]:
            out[rel(pathlib.Path(p["manifest_path"]).parent)] = p
    return out


def feature_set_key(entry):
    tokens, feats = [], set()
    for tok in (entry.get("args", "") or "").split():
        if tok.startswith("--features="):
            feats.update(f for f in tok.split("=", 1)[1].split(",") if f)
        else:
            tokens.append(tok)
    if feats:
        tokens.append("--features=" + ",".join(sorted(feats)))
    return (" ".join(sorted(tokens)), entry.get("os", "ubuntu-24.04"))


def lint_enforced_without_ratchet(pkg_name, lint, root_manifest, meta, entries):
    if lint in FORBIDDEN_LINTS:
        return True
    if entries and manifest_exception(entries, f"ratchet:{pkg_name}:{lint}"):
        return True
    if not meta:
        return False
    if lint.startswith("clippy::"):
        tool, name = "clippy", lint.split("::", 1)[1]
    elif lint.startswith("rustdoc::"):
        tool, name = "rustdoc", lint.split("::", 1)[1]
    else:
        tool, name = "rust", lint
    for _, p in members(meta).items():
        if p["name"] != pkg_name:
            continue
        try:
            m = tomllib.loads(pathlib.Path(p["manifest_path"]).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return False
        m_lints = m.get("lints")
        if isinstance(m_lints, dict) and m_lints.get("workspace") is True and isinstance(root_manifest, dict):
            ws_lints = (root_manifest.get("workspace") or {}).get("lints") or {}
            if isinstance(ws_lints, dict) and lint_level((ws_lints.get(tool) or {}).get(name)) in ("warn", "deny", "forbid"):
                return True
        elif isinstance(m_lints, dict):
            if lint_level((m_lints.get(tool) or {}).get(name)) in ("warn", "deny", "forbid"):
                return True
        if lint == "missing_docs":
            for t in p["targets"]:
                if set(t["kind"]) & {"lib", "rlib", "dylib", "proc-macro"}:
                    try:
                        code, bare = lex(pathlib.Path(t["src_path"]).read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError):
                        continue
                    if has_crate_lint(code, bare, {"warn", "deny", "forbid"}, "missing_docs"):
                        return True
        return False
    return False


def check_versions(r, base, root_manifest=None, meta=None, entries=None):
    try:
        lock = load_toml(LOCK) or {}
    except tomllib.TOMLDecodeError as e:
        r.fail("P02", f"{LOCK}: invalid TOML: {e}")
        lock = {}
    want = os.environ.get("POLICY_VERSION")
    if want and lock.get("version") != want:
        r.fail("P02", f"{LOCK} records version {lock.get('version')!r} but the pinned workflow is {want}")
    if not base:
        return
    if git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is None:
        r.fail("P02", f"base revision {base} is not available; fetch it before running downgrade checks")
        return
    old_lock = git("show", f"{base}:{LOCK}")
    if old_lock and isinstance(lock.get("version"), str):
        try:
            old = tomllib.loads(old_lock).get("version", "0.0.0")
            old_t = version_tuple(old)
        except (ValueError, AttributeError, TypeError, tomllib.TOMLDecodeError) as e:
            r.fail("P02", f"base {base}:{LOCK} is invalid: {e}")
        else:
            try:
                if version_tuple(lock["version"]) < old_t:
                    r.fail("P02", f"rust-policy version goes down from {old} to {lock['version']}")
            except (ValueError, AttributeError, TypeError) as e:
                r.fail("P02", f"{LOCK} version {lock['version']!r} is invalid: {e}")
    old_profile = git("show", f"{base}:{PROFILE}")
    try:
        profile = load_toml(PROFILE) or {}
    except tomllib.TOMLDecodeError:
        profile = {}
    if old_profile is not None:
        try:
            old_p = tomllib.loads(old_profile)
            old_tc = old_p.get("toolchain") or {}
            if not isinstance(old_tc, dict):
                raise ValueError("toolchain must be a table")
            old_stable = old_tc.get("stable")
            if not (isinstance(old_stable, str) and VERSION3.fullmatch(old_stable)):
                raise ValueError("toolchain.stable must be an exact version")
            old_ad = old_p.get("adoption", {})
            if not isinstance(old_ad, dict) or old_ad.get("ci", "reusable") not in ("reusable", "standalone"):
                raise ValueError("adoption must be a table and adoption.ci reusable or standalone")
            old_checks = old_ad.get("checks", list(CHECKS))
            if not isinstance(old_checks, list) or not all(isinstance(x, str) and x in CHECKS for x in old_checks):
                raise ValueError("adoption.checks must list known checks")
            old_sets = old_p.get("feature_sets")
            if old_sets is None:
                old_sets = [{"id": "default", "args": ""}]
            if not isinstance(old_sets, list) or not old_sets or not all(
                isinstance(s, dict) and isinstance(s.get("args", ""), str) and isinstance(s.get("os", "ubuntu-24.04"), str)
                for s in old_sets
            ):
                raise ValueError("feature_sets must be a non-empty array of tables")
        except (ValueError, AttributeError, TypeError, tomllib.TOMLDecodeError) as e:
            r.fail("P02", f"base {base}:{PROFILE} is invalid: {e}")
        else:
            new_tc = profile.get("toolchain")
            new_stable = new_tc.get("stable") if isinstance(new_tc, dict) else None
            if isinstance(new_stable, str) and (VERSION2.fullmatch(new_stable) or VERSION3.fullmatch(new_stable)):
                if norm_version(new_stable) < norm_version(old_stable):
                    r.fail("P02", f"toolchain.stable goes down from {old_stable} to {new_stable}")
            new_ad = profile.get("adoption")
            if isinstance(new_ad, dict):
                new_checks = new_ad.get("checks", CHECKS)
                if isinstance(new_checks, list) and all(isinstance(x, str) for x in new_checks):
                    dropped = set(old_checks) - set(new_checks)
                    if dropped:
                        r.fail("P02", f"adoption.checks only grows; this change drops {sorted(dropped)}")
                if old_ad.get("ci", "reusable") == "reusable" and new_ad.get("ci", "reusable") == "standalone":
                    r.fail("P02", "adoption.ci cannot go from reusable to standalone")
            new_sets = profile.get("feature_sets")
            if new_sets is None:
                new_sets = [{"id": "default", "args": ""}]
            if isinstance(new_sets, list) and all(
                isinstance(s, dict) and isinstance(s.get("args", ""), str) and isinstance(s.get("os", "ubuntu-24.04"), str)
                for s in new_sets
            ):
                dropped_fs = {feature_set_key(s) for s in old_sets} - {feature_set_key(s) for s in new_sets}
                if dropped_fs:
                    desc = ", ".join(f"{a or 'default'} ({o})" for a, o in sorted(dropped_fs))
                    r.fail("P02", f"feature_sets cannot drop existing configurations: {desc}")
    rank = {"allow": 0, "warn": 1, "deny": 2, "forbid": 3}
    old_cargo = git("show", f"{base}:Cargo.toml")
    if old_cargo and isinstance(root_manifest, dict):
        try:
            old_m = tomllib.loads(old_cargo)
        except tomllib.TOMLDecodeError:
            old_m = {}
        old_tbl = ((old_m.get("workspace") or {}).get("lints") if "workspace" in old_m else old_m.get("lints")) or {}
        new_tbl = ((root_manifest.get("workspace") or {}).get("lints") if "workspace" in root_manifest else root_manifest.get("lints")) or {}
        if isinstance(old_tbl, dict) and isinstance(new_tbl, dict):
            scopes = {"workspace", "Cargo.toml"} if "workspace" in root_manifest else {".", "Cargo.toml"}
            for tool, lints in old_tbl.items():
                if not isinstance(lints, dict):
                    continue
                new_tool = new_tbl.get(tool) if isinstance(new_tbl.get(tool), dict) else {}
                for lint, ov in lints.items():
                    old_lvl = lint_level(ov)
                    if old_lvl not in ("warn", "deny", "forbid"):
                        continue
                    new_lvl = lint_level(new_tool.get(lint))
                    if new_lvl is None or rank.get(new_lvl, -1) < rank[old_lvl]:
                        name = lint if tool == "rust" else f"{tool}::{lint}"
                        if not any(
                            e["valid"] and e.get("kind") == "lint-config" and name in e["lints"]
                            and e.get("scope") in scopes
                            for e in (entries or {}).values()
                        ):
                            r.fail("P02", f"Cargo.toml: lint {name} lowered or removed ({old_lvl!r} -> {new_lvl!r}) compared to {base}; "
                                          f"keep the lint or register a lint-config exception for {name}")
    if meta and isinstance(root_manifest, dict):
        ws_lints = (root_manifest.get("workspace") or {}).get("lints") or {}
        for mpath, p in sorted(members(meta).items()):
            mwhere = rel(p["manifest_path"])
            if mwhere == "Cargo.toml":
                continue
            old_mc = git("show", f"{base}:{mwhere}")
            if not old_mc:
                continue
            try:
                old_mm = tomllib.loads(old_mc)
                new_mm = tomllib.loads(pathlib.Path(p["manifest_path"]).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                continue
            old_ml = old_mm.get("lints")
            if not isinstance(old_ml, dict) or old_ml.get("workspace") is True:
                continue
            new_ml = new_mm.get("lints") or {}
            eff_new = ws_lints if (isinstance(new_ml, dict) and new_ml.get("workspace") is True) else (new_ml if isinstance(new_ml, dict) else {})
            for tool, lints in old_ml.items():
                if not isinstance(lints, dict):
                    continue
                new_tool = eff_new.get(tool) if isinstance(eff_new.get(tool), dict) else {}
                for lint, ov in lints.items():
                    old_lvl = lint_level(ov)
                    if old_lvl not in ("warn", "deny", "forbid"):
                        continue
                    new_lvl = lint_level(new_tool.get(lint))
                    if new_lvl is None or rank.get(new_lvl, -1) < rank[old_lvl]:
                        name = lint if tool == "rust" else f"{tool}::{lint}"
                        if not any(
                            e["valid"] and e.get("kind") == "lint-config" and name in e["lints"]
                            and (in_scope(mpath, e["scope"]) or e.get("scope") in {"workspace", "Cargo.toml", mwhere})
                            for e in (entries or {}).values()
                        ):
                            r.fail("P02", f"{mwhere}: lint {name} lowered or removed ({old_lvl!r} -> {new_lvl!r}) compared to {base}; "
                                          f"keep the lint or register a lint-config exception for {name}")
    old_ratchet = git("show", f"{base}:{RATCHET}")
    if old_ratchet:
        try:
            old = {(e["package"], e["lint"]): e.get("files", {})
                   for e in tomllib.loads(old_ratchet).get("ratchet", [])}
            if not all(isinstance(files, dict) and all(isinstance(f, str) and type(n) is int and n >= 0
                       for f, n in files.items()) for files in old.values()):
                raise ValueError("files must map paths to nonnegative counts")
        except (ValueError, KeyError, AttributeError, TypeError, tomllib.TOMLDecodeError) as e:
            r.fail("P18", f"base {base}:{RATCHET} is invalid: {e}")
            return
        new = {(e["package"], e["lint"]): e["files"] for e in load_ratchet()[0]}
        for key, files in old.items():
            total = sum(files.values())
            if key not in new and total > 0:
                r.fail("P18", f"{RATCHET}: ratchet {key[0]}/{key[1]} was removed with {total} warnings left; "
                              "turn the lint on instead, or keep the ratchet")
            elif key not in new and not lint_enforced_without_ratchet(key[0], key[1], root_manifest, meta, entries):
                r.fail("P18", f"{RATCHET}: ratchet {key[0]}/{key[1]} was removed at 0 without enabling {key[1]} in [lints]; "
                              f"keep the 0 ratchet, enable {key[1]} in Cargo.toml, or register a manifest exception with scope \"ratchet:{key[0]}:{key[1]}\"")
            elif key in new:
                for path, count in new[key].items():
                    allowed = files.get(path, 0)
                    if count > allowed:
                        r.fail("P18", f"{RATCHET}: ratchet {key[0]}/{key[1]} {path} grows from {allowed} to {count}; "
                                      "counts only go down per file")


def lint_level(v):
    return v if isinstance(v, str) else v.get("level") if isinstance(v, dict) else None


def check_lints(r, root_manifest, meta, entries):
    is_ws = "workspace" in root_manifest
    if is_ws and meta and ((root_manifest.get("workspace") or {}).get("lints") or adoption()["ci"] == "reusable"):
        for path, p in members(meta).items():
            m = tomllib.loads(pathlib.Path(p["manifest_path"]).read_text(encoding="utf-8"))
            if (m.get("lints") or {}).get("workspace") is not True:
                r.fail("P03", f"{rel(p['manifest_path'])}: set [lints] workspace = true")
    table = ((root_manifest.get("workspace") or {}).get("lints") if is_ws else root_manifest.get("lints")) or {}
    if not isinstance(table, dict):
        table = {}
    where = "[workspace.lints]" if is_ws else "[lints]"
    # check.sh forbids these on the command line; the table makes plain `cargo clippy` show them too.
    # Standalone adopters may skip the table (a warning), the reusable mode requires it.
    standalone = adoption()["ci"] == "standalone"
    for (tool, lint), ok in BASELINE.items():
        tool_tbl = table.get(tool)
        v = lint_level(tool_tbl.get(lint) if isinstance(tool_tbl, dict) else None)
        if v is None and standalone:
            r.warn("P04", f"Cargo.toml {where}: {tool}.{lint} is not set; check.sh forbids it, but plain cargo clippy will not show it")
        elif v not in ok:
            r.fail("P04", f"Cargo.toml {where}: {tool}.{lint} must be one of {sorted(ok)} (found {v!r})")
    for tool, lints in table.items():
        if not isinstance(lints, dict):
            continue
        for lint, v in lints.items():
            if lint_level(v) == "allow":
                name = lint if tool == "rust" else f"{tool}::{lint}"
                scopes = {"workspace", "Cargo.toml"} if is_ws else {".", "Cargo.toml"}
                if not any(e["valid"] and e.get("kind") == "lint-config" and name in e["lints"]
                           and e.get("scope") in scopes for e in entries.values()):
                    r.fail("P04", f"Cargo.toml {where}: {name} = \"allow\" needs a lint-config exception for {name} with scope in {sorted(scopes)}")
    if is_ws and meta:
        for path, p in sorted(members(meta).items()):
            m = tomllib.loads(pathlib.Path(p["manifest_path"]).read_text(encoding="utf-8"))
            mlints = m.get("lints") or {}
            if not isinstance(mlints, dict) or mlints.get("workspace") is True:
                continue
            mwhere = rel(p["manifest_path"])
            for (tool, lint), ok in BASELINE.items():
                tool_tbl = mlints.get(tool)
                v = lint_level(tool_tbl.get(lint) if isinstance(tool_tbl, dict) else None)
                if v is not None and v not in ok:
                    r.fail("P04", f"{mwhere} [lints]: {tool}.{lint} must be one of {sorted(ok)} (found {v!r})")
            for tool, lints in mlints.items():
                if not isinstance(lints, dict):
                    continue
                for lint, v in lints.items():
                    if lint_level(v) == "allow":
                        name = lint if tool == "rust" else f"{tool}::{lint}"
                        if not any(e["valid"] and e.get("kind") == "lint-config" and name in e["lints"]
                                   and (in_scope(path, e["scope"]) or e.get("scope") in {"workspace", "Cargo.toml", mwhere})
                                   for e in entries.values()):
                            r.fail("P04", f"{mwhere} [lints]: {name} = \"allow\" needs a lint-config exception for {name} covering {path}")


def check_profiles(r, root_manifest, entries):
    profiles = root_manifest.get("profile") or {}
    if not isinstance(profiles, dict):
        return
    for prof in ("dev", "test"):
        table = profiles.get(prof) or {}
        if not isinstance(table, dict):
            continue
        settings = [(f"profile.{prof}", table)]
        pkg_table = table.get("package") or {}
        if isinstance(pkg_table, dict):
            settings += [(f"profile.{prof}.package.{package}", values)
                         for package, values in pkg_table.items() if isinstance(values, dict)]
        for scope, values in settings:
            for key in ("overflow-checks", "debug-assertions"):
                if values.get(key) is False:
                    dotted = f"{scope}.{key}"
                    if not any(e["valid"] and e.get("kind") == "cargo-config" and e.get("scope") == dotted for e in entries.values()):
                        r.fail("P04", f"Cargo.toml: {dotted} = false hides overflow and assertion failures in tests (M27)")


def check_cargo_config(r, entries):
    for name in (".cargo/config.toml", ".cargo/config"):
        try:
            cfg = load_toml(name)
        except tomllib.TOMLDecodeError as e:
            r.fail("P05", f"{name}: invalid TOML: {e}")
            continue
        if not cfg:
            continue
        keys = []
        for k, v in cfg.items():
            if k == "build" and isinstance(v, dict):
                keys += [f"build.{b}" for b in v if b not in CARGO_BUILD_ALLOWED]
            elif k == "target" and isinstance(v, dict):
                keys += [f"target.{t}.{x}" for t, tv in v.items() for x in (tv if isinstance(tv, dict) else {"": 0})]
            elif k not in CARGO_CONFIG_ALLOWED:
                keys.append(k)
        for key in keys:
            if key in ("build.rustflags", "build.rustdocflags") or (
                    key.startswith("target.") and key.rsplit(".", 1)[-1] in ("rustflags", "rustdocflags")):
                r.fail("P05", f"{name}: `{key}` is ignored or forbidden by the checks; put audited flags in rust-policy.toml [build] rustflags")
            elif not any(e["valid"] and e.get("kind") == "cargo-config" and e.get("scope") == key for e in entries.values()):
                r.fail("P05", f"{name}: `{key}` can change how checks build or run (runner, alias, env, linker); "
                              "remove it or register a cargo-config exception with scope = \"" + key + "\"")
    for f in tracked("*/.cargo/config.toml", "*/.cargo/config"):
        r.fail("P05", f"{f}: Cargo configuration below the repository root changes builds run from that directory; keep it at the root")


def check_toolchain(r, plan, meta, msrv_adopted=True):
    try:
        tc = load_toml("rust-toolchain.toml")
        tc_err = False
    except tomllib.TOMLDecodeError as e:
        r.fail("P06", f"rust-toolchain.toml: invalid TOML: {e}")
        tc = None
        tc_err = True
    if (ROOT / "rust-toolchain").exists():
        r.fail("P06", "use rust-toolchain.toml instead of the legacy rust-toolchain file")
    if not tc_err:
        if tc is None:
            r.fail("P06", "rust-toolchain.toml is missing")
        else:
            tc_tbl = tc.get("toolchain")
            if not isinstance(tc_tbl, dict) or tc_tbl.get("channel") != plan["toolchain"]:
                r.fail("P06", f"rust-toolchain.toml channel must equal toolchain.stable ({plan['toolchain']})")
    for path, p in members(meta).items():
        where = rel(p["manifest_path"])
        if not msrv_adopted:
            pass
        elif not p.get("rust_version"):
            r.fail("P06", f"{where}: declare rust-version (toolchain.msrv is {plan['msrv']})")
        elif norm_version(p["rust_version"]) < norm_version(plan["msrv"]):
            r.fail("P06", f"{where}: rust-version {p['rust_version']} is below toolchain.msrv ({plan['msrv']})")
        elif norm_version(p["rust_version"]) > norm_version(plan["msrv"]):
            r.warn("P06", f"{where}: rust-version {p['rust_version']} is above toolchain.msrv; the MSRV job builds it anyway")
        if int(p.get("edition", "2015")) > int(plan["edition"]):
            r.fail("P06", f"{where}: edition {p['edition']} is newer than toolchain.edition ({plan['edition']})")
    for name in ("rustfmt.toml", ".rustfmt.toml"):
        try:
            fmt = load_toml(name)
        except tomllib.TOMLDecodeError as e:
            r.fail("P06", f"{name}: invalid TOML: {e}")
            continue
        if fmt is not None:
            if fmt.get("disable_all_formatting") is True:
                r.fail("P06", f"{name}: disable_all_formatting = true disables rustfmt checks")
            for key in ("edition", "style_edition"):
                if key in fmt and str(fmt[key]) != plan["edition"]:
                    r.fail("P06", f"{name}: {key} must equal toolchain.edition ({plan['edition']})")
    for f in tracked("*/rustfmt.toml", "*/.rustfmt.toml", "*/clippy.toml", "*/.clippy.toml", "*/rust-toolchain.toml", "*/rust-toolchain"):
        r.fail("P06", f"{f}: formatter, Clippy and toolchain configuration lives only at the repository root")


def check_crates(r, plan, meta, entries):
    for v in plan["miri_exempt"]:   # guide M17: unsupported cases are recorded with an alternative check
        if not any(e["valid"] and e.get("kind") == "guide" and e.get("rule") == "M17"
                   and in_scope(v, e["scope"]) for e in entries.values()):
            r.fail("P07", f"crates.miri_exempt: {v} needs an exception with rule M17 and kind guide naming the alternative check")
    if not meta:
        return
    ms = members(meta)
    ratcheted = {e["package"] for e in plan["ratchet"] if e["lint"] == "missing_docs"}
    for key, check in (("unsafe_allowed", "P07"), ("libraries", "P08")):
        for v in plan[key]:
            if v not in ms:
                r.fail(check, f"crates.{key}: {v} is not a workspace member")
    for path, p in sorted(ms.items()):
        if path in plan["libraries"]:
            libs = [t for t in p["targets"] if set(t["kind"]) & {"lib", "rlib", "dylib", "proc-macro"}]
            if not libs:
                r.fail("P08", f"crates.libraries: {path} has no library target")
            for t in libs:
                code, bare = lex(pathlib.Path(t["src_path"]).read_text(encoding="utf-8"))
                if not has_crate_lint(code, bare, {"warn", "deny", "forbid"}, "missing_docs") and p["name"] not in ratcheted:
                    r.fail("P08", f"{rel(t['src_path'])}: library crates need #![warn(missing_docs)] at the crate root, "
                                  f"or a missing_docs ratchet in {RATCHET} while the backlog is paid down")


def check_ratchet(r, plan, meta, entries):
    """P18: every ratchet names a real package and an approved exception for its lint."""
    names = {p["name"]: path for path, p in members(meta).items()} if meta else {}
    for e in plan["ratchet"]:
        where = f"{RATCHET}: ratchet {e['package']}/{e['lint']}"
        if meta and e["package"] not in names:
            r.fail("P18", f"{where}: {e['package']} is not a workspace package")
            continue
        x = entries.get(e["exception"])
        if x is None or not x["valid"] or x.get("kind") not in ("lint", "lint-config") or e["lint"] not in x["lints"]:
            r.fail("P18", f"{where}: {e['exception']} must be a valid lint exception covering {e['lint']}")
        elif meta and not in_scope(names[e["package"]], x["scope"]):
            r.fail("P18", f"{where}: {e['exception']} does not cover {names[e['package']]}")
        for f in e["files"]:
            if meta and not in_scope(f, names[e["package"]]):
                r.fail("P18", f"{where}: {f} is outside the package")


def ratchet_compare(path, update, init=False):
    """Counts the ratchet lints in clippy JSON output and compares them with the baseline."""
    entries, errs = load_ratchet()
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    lints = {e["lint"] for e in entries}
    report = Report()
    meta = cargo_metadata(report)
    if meta is None:
        print("\n".join(report.findings), file=sys.stderr)
        return 1
    pkg_dir = {p["name"]: rel(pathlib.Path(p["manifest_path"]).parent) for p in members(meta).values()} if meta else {}
    dir_pkg = {v: k for k, v in pkg_dir.items()}
    seen, counts, saw_finished = set(), {}, False
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        m = msg.get("message") or {}
        if (msg.get("reason") == "build-finished" and msg.get("success") is not True) or \
                (msg.get("reason") == "compiler-message" and m.get("level") == "error"):
            print("error: ratchet: compilation failed; baseline was not changed", file=sys.stderr)
            return 1
        if msg.get("reason") == "build-finished" and msg.get("success") is True:
            saw_finished = True
            continue
        code = (m.get("code") or {}).get("code")
        if msg.get("reason") != "compiler-message" or code not in lints:
            continue
        span = next((s for s in m.get("spans", []) if s.get("is_primary")), None)
        if not span:
            continue
        f = rel(ROOT / span["file_name"])
        if pathlib.Path(f).is_absolute() or not (ROOT / f).is_file():
            continue   # an expansion of a macro from a dependency: not this repository's file
        pkg = dir_pkg.get(rel(pathlib.Path(msg.get("manifest_path", "")).parent))
        key = (code, f, span["line_start"], span["column_start"])
        if key in seen:   # --all-targets compiles a library twice (lib and its tests)
            continue
        seen.add(key)
        counts[(pkg, code, f)] = counts.get((pkg, code, f), 0) + 1
    if not saw_finished:
        print("error: ratchet: compilation did not finish cleanly (missing build-finished); baseline was not changed", file=sys.stderr)
        return 1
    if init:   # a new ratchet starts from today's counts; P18 keeps them from growing afterwards
        for e in entries:
            if not e["files"]:
                e["files"] = {f: n for (pkg, code, f), n in counts.items() if code == e["lint"] and pkg == e["package"]}
                print(f"ratchet: {e['package']}/{e['lint']} starts at {sum(e['files'].values())} in {len(e['files'])} file(s)")
        write_ratchet(entries, read(RATCHET))
        return 0
    worse, better = [], []
    for e in entries:
        for (pkg, code, f), n in sorted(counts.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2])):
            if code == e["lint"] and pkg == e["package"]:
                allowed = e["files"].get(f, 0)
                if n > allowed:
                    worse.append(f"{f}: {n} {code} warnings, ratchet allows {allowed}")
        for f, allowed in sorted(e["files"].items()):
            n = counts.get((e["package"], e["lint"], f), 0)
            if n < allowed:
                better.append((e, f, n))
    for w in worse:
        print(f"error: ratchet: {w}", file=sys.stderr)
    if worse:
        print(f"ratchet: {len(worse)} file(s) got worse; baseline was not changed")
        return 1
    if better and update:
        text = read(RATCHET)
        for e, f, n in better:
            e["files"][f] = n
        print(f"ratchet: lowered {len(better)} count(s); commit {RATCHET}")
        write_ratchet(entries, text)
    elif better:
        for e, f, n in better:
            print(f"ratchet: {f} is down to {n} {e['lint']} warnings; run `check.sh ratchet --update` to lock it in")
    print("ratchet:", "ok" if not worse else f"{len(worse)} file(s) got worse")
    return 1 if worse else 0


def write_ratchet(entries, old_text):
    head = "".join(l + "\n" for l in (old_text or "").splitlines() if l.startswith("#") and not l.startswith("# generated"))
    out = [head.rstrip("\n")] if head else []
    for e in entries:
        out += ["", "[[ratchet]]", f'lint = "{e["lint"]}"', f'package = "{e["package"]}"', f'exception = "{e["exception"]}"',
                "[ratchet.files]"]
        out += [f'"{f}" = {n}' for f, n in sorted(e["files"].items()) if n > 0]
    (ROOT / RATCHET).write_text("\n".join(out).lstrip("\n") + "\n", encoding="utf-8")


def manifest_exception(entries, scope):
    return any(e["valid"] and e.get("kind") == "manifest" and e.get("scope") == scope for e in entries.values())


def feature_row_enables(p, args, required):
    tokens = args.split()
    feats = p.get("features") or {}
    if "--all-features" in tokens:
        return all(f in feats or "/" in f for f in required)
    enabled = set()
    if "--no-default-features" not in tokens and "default" in feats:
        enabled.add("default")
    for tok in tokens:
        if tok.startswith("--features="):
            for item in tok.split("=", 1)[1].split(","):
                if not item:
                    continue
                if item in feats:
                    enabled.add(item)
                elif item.startswith(p["name"] + "/"):
                    sub = item.split("/", 1)[1]
                    if sub in feats:
                        enabled.add(sub)
    changed = True
    while changed:
        changed = False
        for f in list(enabled):
            for dep in feats.get(f, []):
                if dep in feats and dep not in enabled:
                    enabled.add(dep)
                    changed = True
    return set(required) <= enabled


def check_manifest_graph(r, root_manifest, meta, entries):
    """P17 (always on): every crate that builds is a workspace member, dependency resolution is not
    overridden without an exception, and build scripts live at package roots."""
    if not meta:
        return
    ms = members(meta)
    names = {p["name"] for p in ms.values()}
    exempt_exclude_dirs = []
    ws = root_manifest.get("workspace")
    exclude = ws.get("exclude") if isinstance(ws, dict) else None
    for x in (exclude if isinstance(exclude, list) else []):
        if manifest_exception(entries, f"workspace.exclude:{x}"):
            if isinstance(x, str) and x.strip("/"):
                exempt_exclude_dirs.append(x.strip("/"))
        else:
            r.fail("P17", f"Cargo.toml: [workspace] exclude = {x!r} takes code out of every check; register a manifest exception with scope \"workspace.exclude:{x}\"")
    patch = root_manifest.get("patch")
    if isinstance(patch, dict):
        for source, deps in patch.items():
            if isinstance(deps, dict):
                for dep in deps:
                    scope = f"patch:{source}:{dep}"
                    if not manifest_exception(entries, scope):
                        r.fail("P17", f"Cargo.toml: [patch.{source}] {dep} overrides dependency resolution; "
                                      f"register a manifest exception with scope \"{scope}\"")
            elif not manifest_exception(entries, f"patch:{source}"):
                r.fail("P17", f"Cargo.toml: [patch.{source}] overrides dependency resolution")
    elif patch is not None:
        r.fail("P17", "Cargo.toml: [patch] overrides dependency resolution")
    if "replace" in root_manifest and not manifest_exception(entries, "replace"):
        r.fail("P17", "Cargo.toml: [replace] overrides dependency resolution; "
                      "register a manifest exception with scope \"replace\"")
    try:
        lock = load_toml("Cargo.lock") or {}
    except tomllib.TOMLDecodeError as e:
        r.fail("P17", f"Cargo.lock: invalid TOML: {e}")
        lock = {}
    pkgs = lock.get("package", []) if isinstance(lock, dict) else []
    for pkg in (pkgs if isinstance(pkgs, list) else []):
        if isinstance(pkg, dict) and "source" not in pkg and pkg.get("name") not in names and not manifest_exception(entries, f"path-dependency:{pkg.get('name')}"):
            r.fail("P17", f"Cargo.lock: {pkg.get('name')} builds from a local path but is not a workspace member, so no check lints or tests it")
    for path, p in sorted(ms.items()):
        where = rel(p["manifest_path"])
        for t in p["targets"]:
            if "custom-build" in set(t["kind"]) and rel(t["src_path"]) != (f"{path}/build.rs" if path != "." else "build.rs"):
                r.fail("P17", f"{where}: the build script must be build.rs at the package root (code owners review it); found {rel(t['src_path'])}")
    member_manifests = {rel(p["manifest_path"]) for p in ms.values()}
    for f in tracked("Cargo.toml", "*/Cargo.toml"):
        if f != "Cargo.toml" and f not in member_manifests:
            parent = pathlib.PurePosixPath(f).parent.as_posix()
            covered = (manifest_exception(entries, f"nested:{parent}")
                       or any(in_scope(parent, d) for d in exempt_exclude_dirs))
            if not covered:
                r.fail("P17", f"{f}: nested Cargo.toml is not a workspace member, so no check lints or tests it; "
                              f"add it to [workspace] members or register a manifest exception with scope \"nested:{parent}\"")


def check_manifest_targets(r, plan, meta, entries):
    """P17 (test adoption): no target opts out of tests, doctests, default harnesses or matrix features."""
    if not meta:
        return
    ms = members(meta)
    tracked_set = set(tracked())
    matrix = json.loads(plan["matrix"]) if plan else [{"id": "default", "args": "", "os": "ubuntu-24.04"}]
    for path, p in sorted(ms.items()):
        manifest = tomllib.loads(pathlib.Path(p["manifest_path"]).read_text(encoding="utf-8"))
        pkg = manifest.get("package") or {}
        where = rel(p["manifest_path"])
        declared = {rel(t["src_path"]) for t in p["targets"]}
        base = pathlib.Path(p["manifest_path"]).parent
        conventional = {"autotests": ["tests/*.rs", "tests/*/main.rs"], "autobenches": ["benches/*.rs", "benches/*/main.rs"],
                        "autoexamples": ["examples/*.rs", "examples/*/main.rs"], "autobins": ["src/main.rs", "src/bin/*.rs", "src/bin/*/main.rs"],
                        "autolib": ["src/lib.rs"]}
        for key, globs in conventional.items():
            if pkg.get(key) is not False or manifest_exception(entries, f"{path}:{key}"):
                continue
            hidden = sorted(rel(f) for g in globs for f in base.glob(g) if rel(f) not in declared and rel(f) in tracked_set)
            if hidden:
                r.fail("P17", f"{where}: {key} = false hides {', '.join(hidden[:3])}{' ...' if len(hidden) > 3 else ''} "
                              "from cargo test and clippy --all-targets; declare them as targets")
        for t in p["targets"]:
            kinds = set(t["kind"])
            label = f"{path}:{t['name']}"
            if kinds & {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro", "bin", "test"} and t.get("test") is False \
                    and not manifest_exception(entries, f"{label}:test"):
                r.fail("P17", f"{where}: target {t['name']} sets test = false, so its tests never run")
            if kinds & {"lib", "rlib", "dylib", "proc-macro"} and t.get("doctest") is False and not manifest_exception(entries, f"{label}:doctest"):
                r.fail("P17", f"{where}: target {t['name']} sets doctest = false, so its doctests never run (M26)")
            req = t.get("required-features") or []
            if req and kinds & {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro", "bin", "test"} \
                    and not manifest_exception(entries, f"{label}:required-features") \
                    and not any(feature_row_enables(p, row.get("args", ""), req) for row in matrix):
                r.fail("P17", f"{where}: target {t['name']} requires features {req}, which no configured feature_sets row enables; "
                              f"add a feature_sets row or register a manifest exception with scope \"{label}:required-features\"")
        for section in ("lib", "bin", "test"):   # [[bench]] harness = false is normal (Criterion)
            items = manifest.get(section)
            for item in (items if isinstance(items, list) else [items] if isinstance(items, dict) else []):
                if item.get("harness") is False and not manifest_exception(entries, f"{path}:{item.get('name', section)}:harness"):
                    r.fail("P17", f"{where}: [{section}] harness = false replaces the test runner; register a manifest exception if it is a real custom harness")


def test_files(meta):
    """Files that hold only test code: test, bench and example targets, and #[cfg(test)] mod files."""
    out = set()
    for path, p in members(meta).items():
        prefix = "" if path == "." else path + "/"
        out |= {prefix + "tests/", prefix + "benches/", prefix + "examples/"}
        for t in p["targets"]:
            if set(t["kind"]) & TEST_KINDS:
                out.add(rel(t["src_path"]))
                # A custom test target may live beside production sources (e.g. src/it.rs).
                # Only the target itself is known to be test-only; its siblings are not.
    return out


def is_test_file(path, tests):
    return path in tests or any(d.endswith("/") and path.startswith(d) for d in tests)


def check_suppressions(r, entries, meta, check_lints=True, check_ignores=True):
    tests = test_files(meta) if meta else set()
    files = tracked("*.rs")
    cfg_test_cands, prod_mod_cands = set(), set()
    mod_decl_rx = re.compile(r"\b(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+(\w+)\s*;")
    for f in files:   # files declared ONLY as `#[cfg(test)] mod name;` are test code too
        try:
            code, bare = lex((ROOT / f).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        base = pathlib.PurePosixPath(f).parent
        stem = pathlib.PurePosixPath(f).stem
        owner = base if stem in ("lib", "main", "mod") else base / stem
        cfg_test_spans = set()
        for m in TEST_MOD_DECL.finditer(bare):
            cfg_test_spans.add(m.end())
            for cand in (owner / f"{m.group(1)}.rs", owner / m.group(1) / "mod.rs"):
                cfg_test_cands.add(str(cand))
            cfg_test_cands.add(str(owner / m.group(1)) + "/")
        if not is_test_file(f, tests):
            for m in mod_decl_rx.finditer(bare):
                if m.end() in cfg_test_spans:
                    continue
                for cand in (owner / f"{m.group(1)}.rs", owner / m.group(1) / "mod.rs"):
                    prod_mod_cands.add(str(cand))
                prod_mod_cands.add(str(owner / m.group(1)) + "/")
    tests |= (cfg_test_cands - prod_mod_cands)
    for f in files:
        try:
            src = (ROOT / f).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code, bare = lex(src)
        is_test = is_test_file(f, tests)
        attrs = list(attributes(code, bare))
        for k, (inner, start, end, text, depth) in enumerate(attrs):
            where = f"{f}:{line_of(code, start)} {' '.join(text.split())[:90]}"
            bare_attr = bare[start:end]
            if check_ignores:
                ig = IGNORE.search(re.sub(r"^#\s*!\s*\[", "#[", bare_attr))
                if ig:   # a skipped test must say why: #[ignore = "needs ffmpeg on PATH"]
                    ig_bad = False
                    for im in re.finditer(r"\b(?:r#)?ignore\b", bare_attr):
                        eq = re.match(r"\s*=\s*", bare_attr[im.end():])
                        if not eq:
                            ig_bad = True
                            break
                        pos = im.end() + eq.end()
                        rm = re.match(r'r(#*)"', text[pos:])
                        if rm:
                            end_q = text.find('"' + rm.group(1), pos + rm.end())
                            val = text[pos + rm.end():end_q] if end_q >= 0 else ""
                        elif text[pos:pos + 1] == '"':
                            end_q = bare_attr.find('"', pos + 1)
                            val = text[pos + 1:end_q] if end_q >= 0 else ""
                        else:
                            val = ""
                        if not val.strip():
                            ig_bad = True
                            break
                    if ig_bad:
                        r.fail("P09", f"{where}: give the reason, #[ignore = \"why it cannot run here\"]")
            if not check_lints:
                continue
            skip = bool(RUSTFMT_SKIP.search(bare_attr))
            if not skip and not SUPPRESS.search(bare_attr):
                continue
            names = (["rustfmt::skip"] if skip else []) + lint_names(bare_attr)
            groups = [n for n in names if n in GROUP_LINTS]
            wide = False
            if inner:
                wide = skip or (not is_test and not TEST_MOD_OPEN.search(bare[max(0, start - 400):start]))
            else:
                pos, k2 = end, k + 1   # outer attribute: find the item it decorates
                group = [bare[start:end]]
                while k2 < len(attrs) and not attrs[k2][0] and bare[pos:attrs[k2][1]].strip() == "":
                    group.append(bare[attrs[k2][1]:attrs[k2][2]])
                    pos, k2 = attrs[k2][2], k2 + 1
                j = k - 1
                while j >= 0 and not attrs[j][0] and bare[attrs[j][2]:attrs[j + 1][1]].strip() == "":
                    group.append(bare[attrs[j][1]:attrs[j][2]])
                    j -= 1
                wide = bool(MOD_ITEM.match(bare, pos)) and (skip or (not is_test and
                    not any(re.search(r"cfg\s*\(\s*test\s*\)", g) for g in group)))
            if wide or groups:
                cite_ok(r, "P09", where, text, entries, "lint", f, names if wide else groups)


def check_references(r, entries):
    referenced = set()
    for f in tracked("*.rs"):
        try:
            referenced |= set(EXC_ID.findall(lex((ROOT / f).read_text(encoding="utf-8"))[0]))
        except (OSError, UnicodeDecodeError):
            continue
    toml_files = list(dict.fromkeys(tracked("Cargo.toml", "*/Cargo.toml", "deny.toml",
                                            ".cargo/config.toml", ".cargo/config", RATCHET)))
    if (ROOT / RATCHET).is_file() and RATCHET not in toml_files:
        toml_files.append(RATCHET)
    for f in toml_files:
        try:   # values only: an id inside a TOML comment is not a reference
            data = load_toml(f)
            if data is not None:
                referenced |= set(EXC_ID.findall(json.dumps(data, default=str)))
        except tomllib.TOMLDecodeError:
            r.fail("P10", f"{f}: not valid TOML")
    for i in sorted(referenced - set(entries)):
        r.fail("P10", f"{i} is referenced but not defined in {REGISTER}")
    for i, e in sorted(entries.items()):
        if e["valid"] and i not in referenced and e.get("kind") in ("lint", "deny-ignore"):
            r.warn("P10", f"{i} is not referenced anywhere; remove it if the deviation is gone")


def check_deny(r, entries):
    try:
        cfg = load_toml("deny.toml")
    except tomllib.TOMLDecodeError as e:
        r.fail("P11", f"deny.toml: invalid TOML: {e}")
        return
    if cfg is None:
        r.fail("P11", "deny.toml is missing")
        return
    adv, src = cfg.get("advisories") or {}, cfg.get("sources") or {}
    if not isinstance(adv, dict):
        r.fail("P11", "deny.toml: [advisories] must be a table")
        adv = {}
    if not isinstance(src, dict):
        r.fail("P11", "deny.toml: [sources] must be a table")
        src = {}
    if adv.get("yanked") != "deny":
        r.fail("P11", "deny.toml: [advisories] yanked must be \"deny\"")
    if adv.get("unmaintained") == "none":
        r.fail("P11", "deny.toml: [advisories] unmaintained must not be \"none\"")
    for key in ("db-urls", "db-path"):
        if key in adv:
            r.fail("P11", f"deny.toml: [advisories] {key} overrides the official RustSec advisory database; remove it")
    for key in ("unknown-registry", "unknown-git"):
        if src.get(key) != "deny":
            r.fail("P11", f"deny.toml: [sources] {key} must be \"deny\"")
    graph = cfg.get("graph") or {}
    if not isinstance(graph, dict):
        r.fail("P11", "deny.toml: [graph] must be a table")
        graph = {}
    for key in ("exclude", "targets", "exclude-dev"):
        if graph.get(key):
            r.fail("P11", f"deny.toml: [graph] {key} narrows the checked graph; remove it")
    ignores = adv.get("ignore", [])
    if not isinstance(ignores, list):
        r.fail("P11", "deny.toml: [advisories] ignore must be a list")
        ignores = []
    for item in ignores:
        if not isinstance(item, dict) or "reason" not in item:
            r.fail("P11", f"deny.toml: advisory ignore {item!r} needs the table form with a reason citing EXC-nnnn")
        else:
            subject = item.get("id", item.get("crate"))
            cite_ok(r, "P11", f"deny.toml: ignore {subject}", str(item["reason"]), entries, "deny-ignore", subject=subject)


def check_agent_files(r, plan=None):
    for f in tracked("AGENTS.override.md", "*/AGENTS.override.md", "CLAUDE.local.md", "*/CLAUDE.local.md"):
        r.fail("P12", f"{f}: override files replace the shared instructions; remove it from the repository")
    overrides = sorted(
        p for p in ROOT.rglob("AGENTS.override.md")
        if not set(p.relative_to(ROOT).parts[:-1]) & {".git", "target"}
    )
    for p in overrides:
        r.warn("P12", f"{rel(p)} exists locally: Codex uses it instead of AGENTS.md in this checkout")
    if adoption()["agents"] == "block" and (ROOT / "opencode.json").is_file():
        try:
            oc = json.loads((ROOT / "opencode.json").read_text())
        except json.JSONDecodeError as e:
            r.fail("P12", f"opencode.json: invalid JSON: {e}")
        else:
            inst = oc.get("instructions") if isinstance(oc, dict) else None
            if not (isinstance(inst, list) and any(
                    isinstance(x, str) and x.removeprefix("./") == "rust-policy.toml" for x in inst)):
                r.fail("P12", "opencode.json: `instructions` must include \"rust-policy.toml\" so OpenCode loads the policy")
    if (plan or {}).get("visibility") == "public":
        pr_tpl = read(".github/pull_request_template.md")
        stripped_pr = re.sub(r"<!--.*?-->", "", pr_tpl or "", flags=re.S)
        has_heading = bool(re.search(r"(?m)^##\s+Human-owned\b", stripped_pr))
        has_fields = "Responsible engineer" in stripped_pr and "M34" in stripped_pr
        if pr_tpl is None or not (has_heading and has_fields):
            r.fail("P12", ".github/pull_request_template.md: adoption.visibility = \"public\" requires a PR template with a `## Human-owned` heading, Responsible engineer, and M34 sign-off fields")
    for f in tracked("AGENTS.md", "*/AGENTS.md"):
        parts = pathlib.PurePosixPath(f).parent.parts
        total = 0
        for k in range(len(parts) + 1):
            p = ROOT.joinpath(*parts[:k], "AGENTS.md")
            total += p.stat().st_size if p.is_file() else 0
        if total > AGENTS_CHAIN_LIMIT:
            r.fail("P13", f"{f}: the AGENTS.md chain is {total} bytes; keep it under {AGENTS_CHAIN_LIMIT} (Codex truncates at 32 KiB)")


def check_lockfile(r):
    if not tracked("Cargo.lock"):
        r.fail("P14", "Cargo.lock must be committed: CI builds with --locked (MBRAS policy for every repository)")


MIRI_ONLY_IGNORE = re.compile(
    r"^#\s*!?\s*\[\s*cfg_attr\s*\(\s*(?:miri|all\s*\([^)]*\bmiri\b[^)]*\))\s*,\s*(?:r#)?ignore(?:\s*=\s*.*)?\)\s*\]\Z",
    re.S,
)


def extract_ignore_reasons(bare_attr, text):
    """Return the string literal reason values attached to each `ignore = ...` in an attribute."""
    reasons = []
    for im in re.finditer(r"\b(?:r#)?ignore\b", bare_attr):
        eq = re.match(r"\s*=\s*", bare_attr[im.end():])
        if not eq:
            reasons.append("")
            continue
        pos = im.end() + eq.end()
        rm = re.match(r'r(#*)"', text[pos:])
        if rm:
            end_q = text.find('"' + rm.group(1), pos + rm.end())
            reasons.append(text[pos + rm.end():end_q] if end_q >= 0 else "")
        elif text[pos:pos + 1] == '"':
            end_q = bare_attr.find('"', pos + 1)
            reasons.append(text[pos + 1:end_q] if end_q >= 0 else "")
        else:
            reasons.append("")
    return reasons


def count_non_miri_ignores(src, entries=None, path=None):
    """Count non-Miri #[ignore] attributes in Rust source; authorized P19 exceptions are excluded when entries are given."""
    code, bare = lex(src)
    count = 0
    for _, start, end, text, _ in attributes(code, bare):
        bare_attr = bare[start:end]
        norm = re.sub(r"^#\s*!\s*\[", "#[", bare_attr)
        if not IGNORE.search(norm):
            continue
        if MIRI_ONLY_IGNORE.fullmatch(norm):
            continue
        reasons = extract_ignore_reasons(bare_attr, text)
        for reason in (reasons or [""]):
            if entries is not None and path is not None:
                ids = EXC_ID.findall(reason)
                if any(
                    (e := entries.get(i)) and e["valid"] and e.get("kind") == "guide"
                    and e.get("rule") == "P19" and in_scope(path, e["scope"])
                    for i in ids
                ):
                    continue
            count += 1
    return count


def check_ignores_growth(r, base, entries):
    """P19: non-Miri #[ignore] count per file must not grow compared to base without a registered P19 exception."""
    if not base or git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is None:
        return
    renames = {}
    diff_out = git("diff", "-M", "--name-status", "-z", base, "--")
    if diff_out:
        toks = diff_out.split("\0")
        idx = 0
        while idx < len(toks) and toks[idx]:
            status = toks[idx]
            if status.startswith(("R", "C")) and idx + 2 < len(toks):
                renames[toks[idx + 2]] = toks[idx + 1]
                idx += 3
            elif idx + 1 < len(toks):
                idx += 2
            else:
                break
    for f in tracked("*.rs"):
        try:
            cur_src = (ROOT / f).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_n = count_non_miri_ignores(cur_src, entries, f)
        if new_n == 0:
            continue
        old_path = renames.get(f, f)
        old_src = git("show", f"{base}:{old_path}")
        old_n = count_non_miri_ignores(old_src, entries, old_path) if old_src is not None else 0
        if new_n > old_n:
            r.fail("P19", f"{f}: non-Miri #[ignore] count grew from {old_n} to {new_n} compared to {base}; "
                          "fix the test or register a guide exception (rule P19) cited in #[ignore = \"EXC-nnnn: ...\"]")


def strip_yaml_comment(line):
    q, i, n = None, 0, len(line)
    while i < n:
        ch = line[i]
        if q is None:
            if ch in ("'", '"'):
                q = ch
            elif ch == "#" and (i == 0 or line[i - 1].isspace()):
                return line[:i]
        elif q == "'":
            if ch == "'":
                if i + 1 < n and line[i + 1] == "'":
                    i += 1
                else:
                    q = None
        else:
            if ch == "\\" and i + 1 < n:
                i += 1
            elif ch == '"':
                q = None
        i += 1
    return line


def workflow_triggers(text):
    """Extract top-level GitHub Actions `on:` trigger names structurally without third-party YAML parsers.
    Returns (triggers, unsupported_on_forms) so callers fail closed on YAML aliases or escaped trigger scalars."""
    triggers, unsupported = set(), set()
    clean = text.lstrip("\ufeff")
    lines = [strip_yaml_comment(l).rstrip() for l in clean.splitlines()]
    full_stripped = "\n".join(lines)
    # Determine minimum top-level key indent (handles documents indented uniformly or with ---)
    top_indent = 0
    for l in lines:
        s = l.lstrip(" ")
        if not s or s in ("---", "..."):
            continue
        top_indent = len(l) - len(s)
        break
    # Collect non-jobs top-level text as a fail-closed backstop; if any YAML alias exists, scan full text too
    non_jobs_lines = []
    in_jobs = False
    for l in lines:
        s = l.lstrip(" ")
        ind = len(l) - len(s)
        if s and ind == top_indent and not s.startswith("-"):
            in_jobs = bool(re.match(r"""^(?:jobs|"jobs"|'jobs')[ \t]*:""", s))
        if not in_jobs:
            non_jobs_lines.append(l)
    has_yaml_alias = bool(re.search(r"(?:^|[\s,\[\{:&!])\*[A-Za-z0-9_-]+", full_stripped, re.M))
    backstop_text = full_stripped if has_yaml_alias else "\n".join(non_jobs_lines)
    for trig in ("pull_request_target", "workflow_run"):
        if re.search(rf"\b{trig}\b", backstop_text):
            triggers.add(trig)

    def record_trigger(raw_tok):
        tok = raw_tok.strip()
        if not tok:
            return
        if tok.startswith("*"):
            unsupported.add(f"YAML alias `{tok}` under `on:`")
            return
        if tok.startswith('"') and tok.endswith('"') and "\\" in tok:
            unsupported.add(f"escaped double-quoted scalar `{tok}` under `on:`")
            return
        t = tok.strip("'\"")
        if t:
            triggers.add(t)

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        s = line.lstrip(" ")
        ind = len(line) - len(s)
        m = re.match(r"""^(?:on|"on"|'on')[ \t]*:(.*)$""", s) if ind == top_indent else None
        if not m:
            i += 1
            continue
        rest = m.group(1).strip()
        # Strip leading YAML anchor (&name) or tag (!tag)
        while re.match(r"^(?:&\S+|!\S+)", rest):
            rest = re.sub(r"^(?:&\S+|!\S+)\s*", "", rest)
        if rest:
            if rest.startswith(("[", "{")):
                open_ch, close_ch = rest[0], "]" if rest[0] == "[" else "}"
                buf, depth = rest, rest.count(open_ch) - rest.count(close_ch)
                while depth > 0 and i + 1 < n:
                    i += 1
                    buf += " " + lines[i].strip()
                    depth += lines[i].count(open_ch) - lines[i].count(close_ch)
                inner = buf.strip()
                if inner.startswith(open_ch) and inner.endswith(close_ch):
                    inner = inner[1:-1]
                for item in inner.split(","):
                    part = item.split(":", 1)[0] if open_ch == "{" else item
                    record_trigger(part)
            else:
                record_trigger(rest)
            i += 1
            continue
        i += 1
        child_indent = None
        while i < n:
            cur = lines[i]
            if not cur.strip():
                i += 1
                continue
            indent = len(cur) - len(cur.lstrip(" "))
            body = cur.lstrip(" ")
            # YAML allows sequence items `- trigger` at the same indent as `on:`
            if indent < top_indent or (indent == top_indent and not body.startswith("-")):
                break
            if child_indent is None:
                child_indent = indent
            if indent == child_indent:
                if body.startswith("-"):
                    record_trigger(body[1:])
                elif ":" in body:
                    record_trigger(body.split(":", 1)[0])
            i += 1
    return triggers, unsupported


def check_workflows(r, entries):
    for f in tracked(".github/workflows/*.yml", ".github/workflows/*.yaml"):
        raw = read(f) or ""
        text = "\n".join(strip_yaml_comment(line) for line in raw.splitlines())
        trigs, unsupported = workflow_triggers(raw)
        has_wf_exc = any(e["valid"] and e.get("kind") == "workflow" and e.get("scope") == f for e in entries.values())
        for trig in ("pull_request_target", "workflow_run"):
            if trig in trigs and not has_wf_exc:
                r.fail("P15", f"{f}: `{trig}` runs untrusted pull request code with secrets; register a workflow exception if it is safe")
        for form in sorted(unsupported):
            if not has_wf_exc:
                r.fail("P15", f"{f}: {form} cannot be audited statically; write plain trigger names or register a workflow exception")
        if f != CALLER and re.search(r"/\.github/workflows/rust-ci\.ya?ml@", text):
            r.fail("P15", f"{f}: only {CALLER} may call rust-ci.yml")
    text = read(CALLER)
    try:
        lock = load_toml(LOCK) or {}
    except tomllib.TOMLDecodeError:
        lock = {}
    repo = os.environ.get("POLICY_REPO") or lock.get("source", "")
    if text is not None and adoption()["ci"] == "reusable":
        stripped = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        for m in re.finditer(r"(?m)^\s*branches:[ \t]*(.*)$", stripped):
            val = m.group(1).strip()
            if val in ("[]", '[""]', "['']"):
                r.fail("P15", f"{CALLER}: `branches:` filter cannot be empty ({val!r}); list protected branches or remove it")
            elif not val:
                # YAML block sequence follows; ensure at least one non-empty item at deeper indent
                indent = len(m.group(0)) - len(m.group(0).lstrip())
                rest = stripped[m.end() + 1:].splitlines()
                items = []
                for line in rest:
                    if not line.strip():
                        continue
                    cur_indent = len(line) - len(line.lstrip())
                    if cur_indent <= indent:
                        break
                    items.append(line.strip())
                if not any(it.startswith("-") and it[1:].strip() not in ("", '""', "''") for it in items):
                    r.fail("P15", f"{CALLER}: `branches:` filter must list at least one branch")
        uses = re.findall(r"(?m)^\s*uses:\s*(\S+)", stripped)
        if len(uses) != 1 or not re.fullmatch(re.escape(repo) + r"/\.github/workflows/rust-ci\.yml@[0-9a-f]{40}", uses[0]):
            r.fail("P15", f"{CALLER}: must call {repo or '<policy repository>'}/.github/workflows/rust-ci.yml exactly once, pinned to a full commit SHA")


def codeowners_regex(pattern):
    """GitHub CODEOWNERS pattern (gitignore rules, no negation) as a regex over repository paths."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    pat = pattern.lstrip("/")
    directory = pat.endswith("/")
    pat = pat.rstrip("/")
    if not pat:
        return re.compile(r"(?!)")
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    prefix = "" if anchored else "(?:.*/)?"
    last = pat.rsplit("/", 1)[-1]
    suffix = "/.*" if directory else "" if ("*" in last or "?" in last) else "(?:/.*)?"
    return re.compile(prefix + out + suffix + r"\Z")


# Paths that stand for each protected pattern, plus every tracked file that matches one.
PROBES = {"AGENTS.md": ["AGENTS.md", "crates/x/AGENTS.md"], "CLAUDE.md": ["CLAUDE.md", "crates/x/CLAUDE.md"],
          "AGENTS.override.md": ["AGENTS.override.md", "crates/x/AGENTS.override.md"],
          "rust-toolchain.toml": ["rust-toolchain.toml", "crates/x/rust-toolchain.toml"],
          "rustfmt.toml": ["rustfmt.toml", "crates/x/rustfmt.toml"], ".rustfmt.toml": [".rustfmt.toml", "crates/x/.rustfmt.toml"],
          "clippy.toml": ["clippy.toml", "crates/x/clippy.toml"], ".clippy.toml": [".clippy.toml", "crates/x/.clippy.toml"],
          ".cargo/": [".cargo/config.toml", "crates/x/.cargo/config.toml"], "build.rs": ["build.rs", "crates/x/build.rs"]}


def check_codeowners(r, plan, meta=None):
    """P16: every file of the policy surface is owned, by the last matching line, only by policy.owners."""
    path = next((p for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS") if (ROOT / p).is_file()), None)
    if path is None:
        r.fail("P16", "CODEOWNERS is missing; policy files need a code owner review")
        return
    rules = []
    for line in read(path).splitlines():
        parts = line.split("#", 1)[0].split()
        if parts:
            rules.append((parts[0], codeowners_regex(parts[0]), parts[1:]))
    want = set((plan or {}).get("owners", "").split())
    files = set(tracked())
    for pat in PROTECTED:
        if pat in ("/CODEOWNERS", "/docs/CODEOWNERS", "/.github/CODEOWNERS") and pat.lstrip("/") != path:
            continue   # GitHub reads only the first of .github/, root and docs/; that one must be owned
        probes = PROBES.get(pat, [pat.lstrip("/") + ("x" if pat.endswith("/") else "")])
        rx = codeowners_regex(pat)
        for f in sorted(set(probes) | {f for f in files if rx.match(f)}):
            owners = next((o for _, prx, o in reversed(rules) if prx.match(f)), None)
            if not owners:
                r.fail("P16", f"{path}: {f} (policy surface {pat}) has no code owner")
                break
            if want and not set(owners) <= want:
                r.fail("P16", f"{path}: {f} is owned by {' '.join(owners)}; only {' '.join(sorted(want))} (policy.owners) may own it")
                break
    u_want = set((plan or {}).get("unsafe_owners", "").split())
    if u_want and plan:
        critical_crates = sorted(set(plan.get("unsafe_allowed", [])) | set(plan.get("critical", [])))
        other_members = {p for p in (members(meta) if meta else {}) if p != "."}
        for c in critical_crates:
            probe_base = "src/lib.rs" if c == "." else f"{c}/src/lib.rs"
            if c == ".":
                crate_files = {
                    f for f in files
                    if f.endswith(".rs") and not any(f.startswith(om + "/") for om in other_members)
                }
            else:
                crate_files = {f for f in files if f.startswith(c + "/")}
            for f in sorted({probe_base} | crate_files):
                if any(codeowners_regex(pat).match(f) for pat in PROTECTED):
                    continue
                owners = next((o for _, prx, o in reversed(rules) if prx.match(f)), None)
                if not owners or not set(owners) <= u_want:
                    r.fail("P16", f"{path}: {f} (soundness-critical crate {c}) must be owned by policy.unsafe_owners ({' '.join(sorted(u_want))}) (M34)")
                    break
    last_star = next((o for pat, _, o in reversed(rules) if pat == "*"), None)
    if (adoption()["ci"] == "reusable" or (plan or {}).get("visibility") == "public") and not last_star:
        r.warn("P16", f"{path}: no effective catch-all `* @team` rule found; add one (or reinstall with --reviewers) so every pull request requires human review")


def cmd_check(base):
    r = Report()
    try:
        plan = validate_profile(load_toml(PROFILE))
    except tomllib.TOMLDecodeError as e:
        r.fail("P00", f"{PROFILE}: invalid TOML: {e}")
        plan = None
    except ProfileError as e:
        for m in e.args[0]:
            r.fail("P00", f"{PROFILE}: {m}")
        plan = None
    # Checks that guard a job only apply once that job is adopted (adoption.checks); an invalid
    # profile turns them all on.
    adopted = set(plan["checks"].split()) if plan else set(CHECKS)
    check_managed(r)
    entries = register_entries(r)
    try:
        root_manifest = load_toml("Cargo.toml")
    except tomllib.TOMLDecodeError as e:
        r.fail("P03", f"Cargo.toml: invalid TOML: {e}")
        root_manifest = None
        meta = None
    else:
        meta = cargo_metadata(r) if root_manifest is not None else None
        if root_manifest is None:
            r.fail("P03", "Cargo.toml is missing at the repository root")
    check_versions(r, base, root_manifest, meta, entries)
    if root_manifest is not None:
        check_manifest_graph(r, root_manifest, meta, entries)
        if "clippy" in adopted:
            check_lints(r, root_manifest, meta, entries)
        if "clippy" in adopted or "test" in adopted:
            check_profiles(r, root_manifest, entries)
        if "test" in adopted:
            check_manifest_targets(r, plan, meta, entries)
    check_cargo_config(r, entries)
    if plan:
        check_toolchain(r, plan, meta, "msrv" in adopted)
        check_crates(r, plan, meta, entries)
        check_ratchet(r, plan, meta, entries)
    if "clippy" in adopted or "test" in adopted:
        check_suppressions(r, entries, meta, check_lints="clippy" in adopted, check_ignores="test" in adopted or "clippy" in adopted)
        check_ignores_growth(r, base, entries)
    check_references(r, entries)
    if "deny" in adopted:
        check_deny(r, entries)
    check_agent_files(r, plan)
    check_lockfile(r)
    check_workflows(r, entries)
    check_codeowners(r, plan, meta)
    for w in sorted(r.warnings):
        print(f"warning: {w}")
    for f in sorted(r.findings, key=lambda s: s[:3]):
        print(f"error: {f}")
    print("conformance:", "ok" if not r.findings else f"{len(r.findings)} finding(s)")
    return 1 if r.findings else 0


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 64
    cmd, args = argv[0], argv[1:]
    if cmd == "plan" and not args:
        return cmd_plan()
    if cmd == "get" and len(args) == 1:
        return cmd_get(args[0])
    if cmd == "verify" and not args:
        return cmd_verify()
    if cmd == "ratchet" and args and args[1:] in ([], ["--update"], ["--init"]):
        return ratchet_compare(args[0], args[1:] == ["--update"], args[1:] == ["--init"])
    if cmd == "check":
        if args[:1] == ["--base"] and len(args) == 2:
            return cmd_check(args[1])
        if not args:
            return cmd_check(None)
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
