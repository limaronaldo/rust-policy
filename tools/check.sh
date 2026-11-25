#!/usr/bin/env bash
# rust-policy check runner. Managed file: CI verifies its sha256 before running it.
# Developers, agents and CI run the same commands:
#   .rust-policy/check.sh fast           fmt, clippy, test, doc, conformance
#   .rust-policy/check.sh full           fast plus msrv and deny
#   .rust-policy/check.sh ci --base REF  conformance against REF, then every adopted feature set
#   .rust-policy/check.sh STEP [-- ARGS] one step: fmt clippy test doc msrv deny miri ratchet conformance
# ARGS are feature flags (--no-default-features, --all-features, --features=a,b) for clippy, test,
# msrv, miri and ratchet. `ratchet --update` lowers the counts in docs/rust-policy/ratchet.toml;
# `ratchet --init` fills a new entry (one with no files yet) with today's counts.
set -euo pipefail

cd "$(dirname "$0")/.."
POLICY=(python3 -I .rust-policy/policy.py)
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "rust-policy: python3 3.11 or newer is required (tomllib)" >&2
  exit 64
fi

usage() { sed -n '2,9p' "$0" >&2; exit 64; }
[ $# -ge 1 ] || usage
cmd="$1"; shift
explicit_features=false
if [ "${1:-}" = "--" ]; then explicit_features=true; shift; fi
extra=("$@")
base_args=()
case "$cmd" in
  ci|conformance)
    if [ "$#" -eq 2 ] && [ "$1" = --base ] && [ -n "$2" ]; then
      base_args=(--base "$2")
    elif [ "$#" -ne 0 ]; then
      usage
    elif [ "$cmd" = ci ]; then
      echo "rust-policy: ci requires --base REF" >&2
      exit 64
    fi
    extra=() ;;
  fast|full|fmt|doc|deny) [ "$#" -eq 0 ] || usage ;;
  clippy|test|msrv|miri|ratchet) ;;
  *) usage ;;
esac

# Flags from the environment replace every rustflags entry in .cargo/config.toml, so the change
# under test cannot cap or lower lints through Cargo configuration (conformance P05 also refuses it).
# The only extra flags are the audited ones in rust-policy.toml [build] rustflags.
unset -v CARGO_ENCODED_RUSTFLAGS CARGO_ENCODED_RUSTDOCFLAGS \
  CARGO_BUILD_RUSTFLAGS CARGO_BUILD_RUSTDOCFLAGS \
  RUSTC RUSTDOC RUSTC_WRAPPER RUSTC_WORKSPACE_WRAPPER RUSTC_BOOTSTRAP \
  CARGO_BUILD_RUSTC CARGO_BUILD_RUSTDOC CARGO_BUILD_RUSTC_WRAPPER CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER
while IFS= read -r _rp_var; do
  case "$_rp_var" in
    CARGO_TARGET_*_RUSTFLAGS|CARGO_TARGET_*_RUSTDOCFLAGS|CARGO_TARGET_*_RUNNER|CARGO_TARGET_*_LINKER|CARGO_ALIAS_*|CARGO_PROFILE_DEV_OVERFLOW_CHECKS|CARGO_PROFILE_DEV_DEBUG_ASSERTIONS|CARGO_PROFILE_TEST_OVERFLOW_CHECKS|CARGO_PROFILE_TEST_DEBUG_ASSERTIONS)
      unset -v "$_rp_var" ;;
  esac
done < <(compgen -v CARGO_ || true)
unset -v _rp_var
RUSTFLAGS="$("${POLICY[@]}" get rustflags)" || exit 1
export RUSTFLAGS RUSTDOCFLAGS=""
# The log checks below read plain text; colored output and non-C locales would hide what they look for.
export CARGO_TERM_COLOR=never LC_ALL=C
# Snapshot runners must not silently bless or force-pass local evidence.
unset -v INSTA_FORCE_PASS TRYBUILD UPDATE_EXPECT
export INSTA_UPDATE=no

note() { printf 'rust-policy: %s\n' "$*" >&2; }

profile() { "${POLICY[@]}" get "$1"; }

# fmt, clippy, test and doc must run on the recorded toolchain (rust-policy.toml toolchain.stable).
assert_toolchain() {
  local want got
  want="$(profile toolchain)" || return 1
  got="$(rustc -vV | sed -n 's/^release: //p')" || return 1
  if [ "$got" != "$want" ]; then
    note "rustc is $got but rust-policy.toml records $want (rust-toolchain.toml selects it; CI sets RUSTUP_TOOLCHAIN)"
    return 1
  fi
}

run_fmt() {
  assert_toolchain || return 1
  local err status=0
  err="$(mktemp)" || return 1
  cargo fmt --all -- --check 2>"$err" || status=$?
  cat "$err" >&2
  # Stable rustfmt only warns about nightly-only options and still exits 0.
  if grep -q "Warning: can't set" "$err"; then
    note "rustfmt ignored options this toolchain cannot apply; remove them from rustfmt.toml"
    status=1
  fi
  rm -f "$err"
  return "$status"
}

# Cargo resolves unqualified --features names against the selected workspace members. Each
# package Clippy/Miri invocation must retain the workspace feature meaning after selection.
# Capture both metadata and the Python result before iterating: process substitution hides failures.
load_workspace() {
  local metadata selection line first=true
  metadata="$(cargo metadata --no-deps --format-version 1 --locked)" || return 1
  selection="$(printf '%s' "$metadata" | python3 -I -c '
import json, sys
m = json.load(sys.stdin)
members = set(m["workspace_members"])
packages = [p for p in m["packages"] if p["id"] in members]
if not packages:
    raise ValueError("workspace contains no packages")
print(" ".join(p["name"] for p in packages))
normalized = []
for arg in sys.argv[1:]:
    if arg.startswith("--features="):
        result = []
        for feature in arg.split("=", 1)[1].split(","):
            names = [p["name"] + "/" + feature for p in packages if feature in p["features"]]
            result.extend(names if "/" not in feature and names else [feature])
        normalized.append("--features=" + ",".join(result))
    elif arg in ("--all-features", "--no-default-features"):
        normalized.append(arg)
        if arg == "--all-features":
            features = [p["name"] + "/" + f for p in packages for f in p["features"]]
            if features:
                normalized.append("--features=" + ",".join(features))
    else:
        raise ValueError("only feature flags are accepted: " + arg)
by_name = {p["name"]: p for p in packages}
all_known_deps = set(by_name)
for p in packages:
    for d in p.get("dependencies", []):
        all_known_deps.add(d["name"])
        all_known_deps.add(d.get("rename") or d["name"])
for package in packages:
    # Cargo can address the selected package and its direct dependency aliases only.
    # Transitive feature requirements must be forwarded through project-defined features.
    direct_names = set()
    by_pkg_name = {}
    for d in package.get("dependencies", []):
        alias = d.get("rename") or d["name"]
        direct_names.add(alias)
        aliases = by_pkg_name.setdefault(d["name"], [])
        if alias not in aliases:
            aliases.append(alias)
    args = []
    for arg in normalized:
        if arg.startswith("--features="):
            features = []
            for f in arg.split("=", 1)[1].split(","):
                if "/" not in f:
                    features.append(f)
                else:
                    prefix, rest = f.split("/", 1)
                    dep_key = prefix.rstrip("?")
                    qmark = "?" if prefix.endswith("?") else ""
                    if dep_key == package["name"]:
                        features.append(f)
                    elif dep_key in by_pkg_name:
                        features.extend(f"{a}{qmark}/{rest}" for a in by_pkg_name[dep_key])
                    elif dep_key in direct_names or dep_key not in all_known_deps:
                        features.append(f)
            features = list(dict.fromkeys(features))
            if features:
                args.append("--features=" + ",".join(features))
        else:
            args.append(arg)
    print(package["name"], " ".join(args))
' ${extra[@]+"${extra[@]}"})" || return 1
  workspace_packages=()
  workspace_feature_rows=()
  while IFS= read -r line; do
    if [ "$first" = true ]; then
      read -r -a workspace_packages <<<"$line"
      first=false
    else
      workspace_feature_rows+=("$line")
    fi
  done <<<"$selection"
}

select_package_features() {
  local row package="$1"
  qualified_extra=()
  for row in ${workspace_feature_rows[@]+"${workspace_feature_rows[@]}"}; do
    if [ "${row%% *}" = "$package" ]; then
      read -r -a qualified_extra <<<"${row#* }"
      return 0
    fi
  done
  note "package $package is not a workspace member"
  return 1
}

run_clippy() {
  assert_toolchain || return 1
  load_workspace || return 1
  local log status=0 pkg flags unsafe_text ratcheted=() lints=()
  unsafe_text="$(profile unsafe_args)" || return 1
  log="$(mktemp)" || return 1
  # Apply ratchet allowances only to their package. An exception in one library cannot
  # remove -D warnings from another library. All-unsafe workspaces need no empty safe run.
  for pkg in ${workspace_packages[@]+"${workspace_packages[@]}"}; do
    select_package_features "$pkg" || { rm -f "$log"; return 1; }
    flags="$(profile "ratchet_allow:$pkg")" || { rm -f "$log"; return 1; }
    read -r -a ratcheted <<<"$flags"
    lints=(-D warnings -F unsafe_op_in_unsafe_fn -F clippy::undocumented_unsafe_blocks
      -F clippy::missing_safety_doc -F clippy::allow_attributes_without_reason ${ratcheted[@]+"${ratcheted[@]}"})
    case " $unsafe_text " in
      *" -p $pkg "*) ;;
      *) lints+=(-F unsafe_code) ;;
    esac
    cargo clippy -p "$pkg" --all-targets --no-deps --locked ${qualified_extra[@]+"${qualified_extra[@]}"} \
      -- ${lints[@]+"${lints[@]}"} 2>&1 | tee -a "$log" || status=$?
  done
  # Cargo [lints] can emit E0602 without failing -D warnings.
  if grep -Eq '^(warning|error)(\[E0602\])?: unknown lint' "$log"; then
    note "a lint name is unknown to this toolchain; fix the name in Cargo.toml or the attribute"
    status=1
  fi
  rm -f "$log"
  return "$status"
}

run_test() {
  assert_toolchain || return 1
  local log status=0
  log="$(mktemp)" || return 1
  cargo test --workspace --locked ${extra[@]+"${extra[@]}"} 2>&1 | tee "$log" || status=$?
  # Doctest lines look like: "test src/lib.rs - parse (line 12) ... ignored" (M26: use no_run instead).
  if grep -Eq '^test .+ \(line [0-9]+\)( - compile)? \.\.\. ignored' "$log"; then
    note "ignored doctests give no evidence; use no_run for examples that need an external environment"
    status=1
  fi
  rm -f "$log"
  return "$status"
}

run_doc() {
  assert_toolchain || return 1
  RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps --locked ${extra[@]+"${extra[@]}"}
}

run_msrv() {
  local v pattern verbose
  v="${MSRV:-$(profile msrv)}" || return 1
  # A two-component rust-version names the first patch, not rustup latest patch.
  [[ "$v" =~ ^[0-9]+\.[0-9]+$ ]] && v="$v.0"
  pattern="^release: ${v//./\\.}$"
  if ! verbose="$(rustc "+$v" -vV)" || ! grep -Eq "$pattern" <<<"$verbose"; then
    note "the MSRV toolchain $v is not installed or not selected (rustup toolchain install $v)"
    return 1
  fi
  # A build check: lints and their names are the stable toolchain's job.
  cargo "+$v" check --workspace --locked ${extra[@]+"${extra[@]}"}
}

run_deny() {
  if ! command -v cargo-deny >/dev/null; then
    note "cargo-deny is not installed (cargo install --locked cargo-deny@0.20.2)"
    return 1
  fi
  # --workspace: every member is a graph root; --all-features: optional dependencies count too.
  cargo deny --workspace --all-features --locked check
}

run_miri() {
  local n pkgs=() selected pkg status=0 verbose
  selected="$(profile miri_args)" || return 1
  read -r -a pkgs <<<"$selected"
  if [ ${#pkgs[@]} -eq 0 ]; then
    note "no eligible crate is listed for Miri, so Miri has nothing to run"
    return 0
  fi
  n="${NIGHTLY:-$(profile nightly)}" || return 1
  if [ -z "$n" ] || ! verbose="$(rustc "+$n" -vV)" || ! grep -Eq '^release: .*nightly' <<<"$verbose"; then
    note "the pinned nightly $n is not installed (rustup toolchain install $n --component miri,rust-src)"
    return 1
  fi
  load_workspace || return 1
  # Tests Miri cannot run use #[cfg_attr(miri, ignore = "reason")].
  for pkg in ${pkgs[@]+"${pkgs[@]}"}; do
    [ "$pkg" = -p ] && continue
    select_package_features "$pkg" || return 1
    cargo "+$n" miri test --locked -p "$pkg" ${qualified_extra[@]+"${qualified_extra[@]}"} || status=$?
  done
  return "$status"
}

run_conformance() { "${POLICY[@]}" check ${base_args[@]+"${base_args[@]}"}; }

run_ratchet() {
  assert_toolchain || return 1
  local json status=0 update="" packages pkg flags sets args_text lints=() saved_extra=(${extra[@]+"${extra[@]}"})
  packages="$(profile ratchet_packages)" || return 1
  [ -n "$packages" ] || { note "no ratcheted lints; nothing to count"; return 0; }
  if [ ${#extra[@]} -gt 0 ] && { [ "${extra[0]}" = "--update" ] || [ "${extra[0]}" = "--init" ]; }; then
    update="${extra[0]}"
    extra=("${extra[@]:1}")
  fi
  sets="$(profile feature_args)" || { extra=(${saved_extra[@]+"${saved_extra[@]}"}); return 1; }
  if [ -n "$update" ]; then
    if [[ "$sets" == *$'\n'* ]]; then
      note "ratchet $update requires a single configured feature set; a partial matrix cannot lower the baseline"
      extra=(${saved_extra[@]+"${saved_extra[@]}"})
      return 1
    fi
    if [ ${#extra[@]} -ne 0 ]; then
      note "ratchet $update uses the configured feature set and does not accept extra flags: ${extra[*]}"
      extra=(${saved_extra[@]+"${saved_extra[@]}"})
      return 1
    fi
    args_text="$sets"
    [ "$args_text" = - ] && args_text=""
    read -r -a extra <<<"$args_text"
  elif [ ${#extra[@]} -eq 0 ] && [ "$explicit_features" != true ]; then
    args_text="${sets%%$'\n'*}"
    [ "$args_text" = - ] && args_text=""
    read -r -a extra <<<"$args_text"
  fi
  load_workspace || { extra=(${saved_extra[@]+"${saved_extra[@]}"}); return 1; }
  json="$(mktemp)" || { extra=(${saved_extra[@]+"${saved_extra[@]}"}); return 1; }
  # A validated plan is the source of both package selection and lint names.
  for pkg in $packages; do
    select_package_features "$pkg" || { status=1; break; }
    flags="$(profile "ratchet_allow:$pkg")" || { status=1; break; }
    read -r -a lints <<<"${flags//-A /--force-warn }"
    cargo clippy -p "$pkg" --all-targets --no-deps --locked ${qualified_extra[@]+"${qualified_extra[@]}"} --message-format=json \
      -- ${lints[@]+"${lints[@]}"} >>"$json" || { status=$?; break; }
  done
  # A failed compiler produced incomplete evidence. Never compare, initialize or lower it.
  if [ "$status" -eq 0 ]; then
    "${POLICY[@]}" ratchet "$json" ${update:+"$update"} || status=$?
  fi
  rm -f "$json"
  extra=(${saved_extra[@]+"${saved_extra[@]}"})
  return "$status"
}

# The standalone pipeline: what the reusable workflow runs, in one process, for adoption.ci = standalone.
run_ci() {
  local failed=() step sets=() set_args run feature_text
  note "== conformance"
  run_conformance || return 1
  feature_text="$(profile feature_args)" || return 1
  while IFS= read -r set_args; do sets+=("$set_args"); done <<<"$feature_text"
  for step in fmt clippy test doc msrv deny miri ratchet; do
    run="$(profile "run_$step")" || return 1
    [ "$run" = true ] || { note "== $step (not adopted yet)"; continue; }
    case "$step" in
      clippy|test|doc|msrv|miri|ratchet)
        for set_args in ${sets[@]+"${sets[@]}"}; do
          [ "$set_args" = - ] && set_args=""
          note "== $step $set_args"
          read -r -a extra <<<"$set_args"
          explicit_features=true
          "run_$step" || failed+=("$step $set_args")
        done ;;
      *)
        note "== $step"
        extra=()
        "run_$step" || failed+=("$step") ;;
    esac
  done
  if [ ${#failed[@]} -gt 0 ]; then
    note "FAILED: ${failed[*]}"
    return 1
  fi
  note "all adopted checks passed"
}

run_steps() {
  local failed=() step adopted
  # Validate policy before any cargo command, even when this function runs in a conditional.
  note "== conformance"
  run_conformance || return 1
  for step in "$@"; do
    note "== $step"
    "run_$step" || failed+=("$step")
  done
  adopted="$(profile run_ratchet)" || return 1
  if [ "$adopted" = true ]; then
    # Ratchet allowances in Clippy are valid only with the corresponding countercheck.
    note "== ratchet"
    explicit_features=true
    run_ratchet || failed+=(ratchet)
  fi
  if [ ${#failed[@]} -gt 0 ]; then
    note "FAILED: ${failed[*]}"
    return 1
  fi
  note "all passed: $*"
}

case "$cmd" in
  fmt|clippy|test|doc|msrv|deny|miri|ratchet|conformance|ci) "run_$cmd" ;;
  fast) run_steps fmt clippy test doc ;;
  full) run_steps fmt clippy test doc msrv deny ;;
  *) usage ;;
esac
