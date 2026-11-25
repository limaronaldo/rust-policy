#!/usr/bin/env bash
# Live experiments that prove the gates block, against a canary repository with rust-policy
# installed and the ruleset active. NOT YET RUN: written from the GitHub CLI documentation; run it in
# the pilot and fix whatever differs before relying on it.
#
#   OWNERS_TEAM=rust-policy-owners AGENT_TOKEN=<agent token> scripts/prove_gates.sh OWNER/CANARY
#
# Your own gh login creates branches and pull requests; AGENT_TOKEN is used only for the actions an
# agent must not be able to perform. The canary PRs are never approved, so every scenario checks the
# specific control it targets instead of only trying to merge:
#   gate   the "rust-policy / gate" check run (GitHub Actions app) concludes FAILURE
#   owner  the pull request waits for a review requested from the owners team
set -euo pipefail

[ $# -eq 1 ] || { echo "usage: OWNERS_TEAM=... AGENT_TOKEN=... $0 OWNER/CANARY" >&2; exit 64; }
repo="$1"
: "${AGENT_TOKEN:?set AGENT_TOKEN to the token of the agent identity}"
: "${OWNERS_TEAM:?set OWNERS_TEAM to the slug of the policy owners team}"
# BEGIN OWNER HELPER
normalize_owners_team() {
  local slug="${1#@}"
  slug="${slug#*/}"
  if [[ ! "$slug" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "OWNERS_TEAM must be a GitHub team slug or @org/team (got: $1)" >&2
    return 64
  fi
  printf '%s\n' "$slug"
}
# END OWNER HELPER
owners_slug="$(normalize_owners_team "$OWNERS_TEAM")" || exit $?
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
gh repo clone "$repo" "$work/canary" -- --quiet
cd "$work/canary"
base="$(gh api "/repos/$repo" --jq .default_branch)"
lib="${CANARY_LIB:-crates/core/src/lib.rs}"   # a library crate root in the canary
failures=0

report() { printf '%-28s %-6s %s\n' "$1" "$2" "$3"; [ "$3" = ok ] || failures=$((failures + 1)); }

# BEGIN API proof helper
# Only an authenticated permission denial proves the control. A missing resource, bad token,
# invalid request, rate limit or network failure leaves the experiment inconclusive.
expect_forbidden_api() {
  local label="$1" endpoint="$2" response code
  shift 2
  if response="$(GH_TOKEN="$AGENT_TOKEN" gh api --include --method POST "$endpoint" "$@" 2>&1)"; then
    report "$label" api ALLOWED
  else
    code="$(awk '/^HTTP\// {sub(/\r$/, "", $2); code=$2} END {print code}' <<<"$response")"
    if [ "$code" = 403 ] && grep -Eq 'Resource not accessible by (integration|personal access token)|Must have admin rights to' <<<"$response"; then
      report "$label" api ok
    else
      report "$label" api "INCONCLUSIVE (HTTP ${code:-unavailable})"
    fi
  fi
}
agent_clone_url() {
  local url
  url="$(jq -er 'select(.full_name | type == "string" and length > 0) | .clone_url | strings' <<<"$1")" || return 1
  if [[ ! "$url" =~ ^https://[^/@[:space:]?#]+/[^[:space:]?#]+\.git$ ]]; then
    echo "agent repository metadata must supply a credential-free HTTPS clone URL" >&2
    return 1
  fi
  printf '%s\n' "$url"
}
expect_forbidden_push() {
  local auth_header
  # Git HTTPS uses a username and token as the HTTP password. Keep both raw and encoded tokens
  # out of command arguments and output; --config-env reads this header from the child environment.
  auth_header="Authorization: Basic $(printf 'x-access-token:%s' "$AGENT_TOKEN" | base64 | tr -d '\n')"
  if AGENT_GIT_AUTH="$auth_header" GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c http.extraHeader= --config-env=http.extraHeader=AGENT_GIT_AUTH push -q "$agent_push_url" "HEAD:refs/heads/$base" 2>"$work/push-error"; then
    report "agent-direct-push" git PUSHED
  elif grep -Eq 'GH006:|GH013:' "$work/push-error"; then
    report "agent-direct-push" git ok
  else
    report "agent-direct-push" git "INCONCLUSIVE (expected a branch-rule rejection)"
  fi
}
# END API proof helper

# Verify the token can authenticate to this exact repository before any negative experiments.
if ! agent_repo="$(GH_TOKEN="$AGENT_TOKEN" gh api "/repos/$repo")"; then
  echo "agent token cannot authenticate to $repo; permission experiments are inconclusive" >&2
  exit 1
fi
# Do not use origin: the owner's gh git_protocol may have cloned it over SSH.
if ! agent_push_url="$(agent_clone_url "$agent_repo")"; then
  echo "agent repository metadata is unusable; permission experiments are inconclusive" >&2
  exit 1
fi

open_pr() {   # $1 name, $2 shell mutation; prints the PR URL
  local branch
  branch="prove/$1-$(date +%s)"
  git checkout -q -B "$branch" "origin/$base"
  bash -c "$2"
  git add -A
  git -c user.email=prove@invalid -c user.name=prove commit -qm "prove: $1"
  git push -q origin "$branch"
  gh pr create --repo "$repo" --base "$base" --head "$branch" --title "prove: $1" --body "Automated gate experiment; close it."
}

scenario() {   # $1 name, $2 expectation (gate|owner), $3 mutation
  local pr verdict count
  pr="$(open_pr "$1" "$3")"
  for _ in 1 2 3 4 5 6; do
    count="$(gh pr view "$pr" --json statusCheckRollup --jq '.statusCheckRollup | length' 2>/dev/null || echo 0)"
    [[ "$count" =~ ^[1-9][0-9]*$ ]] && break
    sleep 2
  done
  gh pr checks "$pr" --watch >/dev/null 2>&1 || true
  case "$2" in
    gate)
      verdict="$(gh pr view "$pr" --json statusCheckRollup --jq \
        '[.statusCheckRollup[] | select(.name == "rust-policy / gate") | .conclusion] | first // "MISSING"')"
      [ "$verdict" = FAILURE ] && verdict=ok ;;
    owner)
      verdict="$(gh pr view "$pr" --json reviewDecision,reviewRequests --jq \
        "if .reviewDecision == \"REVIEW_REQUIRED\" and ([.reviewRequests[] | .slug? // .login?] | index(\"$owners_slug\")) then \"ok\" else .reviewDecision end")" ;;
  esac
  if gh pr merge "$pr" --squash >/dev/null 2>&1; then verdict="MERGED"; fi
  report "$1" "$2" "$verdict"
  gh pr close "$pr" --delete-branch >/dev/null 2>&1 || true
}

# Edits use prove_edit.py (portable, unlike sed -i). New code goes before #[cfg(test)], so that
# clippy::items_after_test_module cannot fail the run for an unrelated reason.
E="python3 $here/prove_edit.py"
crate="$(dirname "$(dirname "$lib")")"
scenario fmt-violation      gate  "$E $lib '#[cfg(test)]' 'pub  fn  prove_fmt() {}\n\n#[cfg(test)]'"
scenario clippy-warning     gate  "$E $lib '#[cfg(test)]' '/// x\npub fn prove() -> u8 {\n    return 1;\n}\n\n#[cfg(test)]'"
scenario crate-allow        gate  "$E $lib '#![' '#![allow(clippy::all, reason = \"x\")]\n#!['"
scenario item-group-allow   gate  "$E $lib '#[cfg(test)]' '/// x\n#[allow(clippy::all, reason = \"x\")]\npub fn prove() -> u8 {\n    return 1;\n}\n\n#[cfg(test)]'"
scenario failing-test       gate  "$E $lib '#[cfg(test)]' '#[test]\nfn prove_fail() {\n    panic!(\"prove\");\n}\n\n#[cfg(test)]'"
scenario ignored-test       gate  "$E $lib '#[test]' '#[test]\n    #[ignore]'"
scenario doctest-off        gate  "$E $crate/Cargo.toml '' '\n[lib]\ndoctest = false\n'"
scenario nested-clippy-toml gate  "$E $crate/clippy.toml '' 'too-many-arguments-threshold = 100\n'"
scenario test-runner-true   owner "$E .cargo/config.toml '' '[target.x86_64-unknown-linux-gnu]\nrunner = \"true\"\n'"
scenario edit-check-script  owner "$E .rust-policy/check.sh '' '\nexit 0\n'"
scenario edit-agents-block  owner "$E AGENTS.md 'Hard rules' 'Soft rules'"
scenario lower-toolchain    owner "$E rust-policy.toml 'stable = ' 'stable = \"1.85.0\" # '"
scenario fake-gate-workflow owner "$E .github/workflows/fake.yml '' 'on: pull_request\njobs:\n  fake:\n    name: \"rust-policy / gate\"\n    runs-on: ubuntu-24.04\n    steps:\n      - run: \"true\"\n'"
# The same mutations that need an owner review also fail the gate (P01, P02, P05); check the gate
# table in the run summary of those PRs as a second confirmation.

sha="$(git rev-parse "origin/$base")"
expect_forbidden_api agent-fake-status "/repos/$repo/statuses/$sha" -f state=success -f context="rust-policy / gate"
jq '.name = "rust-policy: permission proof" | .enforcement = "disabled"' "$here/../rulesets/default-branch.json" >"$work/probe-ruleset.json"
expect_forbidden_api agent-edits-rulesets "/repos/$repo/rulesets" --input "$work/probe-ruleset.json"
git checkout -q --detach "origin/$base"
git -c user.email=prove@invalid -c user.name=prove commit -q --allow-empty -m "prove: direct push"
expect_forbidden_push

echo "scenarios not proven: $failures"
[ "$failures" -eq 0 ]
