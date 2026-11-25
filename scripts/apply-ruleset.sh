#!/usr/bin/env bash
# Creates or updates the rust-policy ruleset of one repository with the GitHub CLI.
#   scripts/apply-ruleset.sh OWNER/REPO disabled|active
# Run it as a repository admin, never with an agent's credentials. Start with "disabled" during the
# pilot, confirm the check name on a real pull request, then apply again with "active".
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: $0 OWNER/REPO disabled|active" >&2; exit 64; }
repo="$1" enforcement="$2"
case "$enforcement" in disabled|active) ;; *) echo "enforcement must be disabled or active" >&2; exit 64 ;; esac
here="$(cd "$(dirname "$0")/.." && pwd)"
name="rust-policy: default branch"

# The required check is bound to the GitHub Actions app, so a commit status posted with a token
# cannot satisfy it. Read the app id instead of trusting the value stored in the JSON file.
app_id="$(gh api /apps/github-actions --jq .id)"
[[ "$app_id" =~ ^[0-9]+$ ]] || { echo "could not read the GitHub Actions app id" >&2; exit 1; }

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
jq --arg e "$enforcement" --argjson app "$app_id" \
  '.enforcement = $e
   | (.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].integration_id) = $app' \
  "$here/rulesets/default-branch.json" >"$body"

existing="$(gh api --paginate "/repos/$repo/rulesets?includes_parents=false&per_page=100" --jq ".[] | select(.name == \"$name\") | .id")"
if [ -n "$existing" ] && [[ ! "$existing" =~ ^[0-9]+$ ]]; then
  echo "multiple or invalid repository rulesets named $name; resolve them before applying" >&2
  exit 1
fi
if [ -n "$existing" ]; then
  gh api --method PUT "/repos/$repo/rulesets/$existing" --input "$body" >/dev/null
  echo "updated ruleset $existing on $repo ($enforcement)"
else
  gh api --method POST "/repos/$repo/rulesets" --input "$body" >/dev/null
  echo "created ruleset on $repo ($enforcement)"
fi

branch="$(gh api "/repos/$repo" --jq .default_branch)"
echo "rules now in effect on $branch:"
gh api "/repos/$repo/rules/branches/$branch" --jq '.[] | .type'
if [ "$enforcement" = disabled ]; then
  echo "reminder: the ruleset blocks nothing until you apply it again with: $0 $repo active"
fi
