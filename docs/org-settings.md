# Organization and repository settings

The gate only works if the people and agents it evaluates cannot switch it off. Apply these settings
before the pilot. Items marked "verify" were taken from documentation and not tested here.

## Agent identities

- Agents never run with an admin's or maintainer's login. Give each agent a dedicated identity (a
  machine user or a GitHub App) or a fine-grained token with Contents and Pull requests write only.
  No Administration, no Workflows, no Statuses, no Checks, no Secrets permission.
- Keep agent identities out of the owners team and out of every ruleset's bypass list.
- An approval by the author of a pull request does not count. `dismiss_stale_reviews_on_push`
  voids approvals given before the last push, and `require_last_push_approval` requires that someone
  other than the last pusher approves, so an agent cannot approve its own work.
- Never give a second agent identity review rights that count: with `--reviewers`, every pull
  request needs a code owner from a human-only team.
- If you enable external LLM review bots, keep them on a separate blockable account outside every
  owners/reviewers team, never as a required status check, and treat their comments as non-blocking
  unless a human reviewer explicitly endorses them.
- Agents get no write access to `mbras/rust-policy`, so they cannot push a weakened workflow commit
  for a consumer to pin.
- Use that agent identity's GitHub App token or personal access token for unattended pull-request
  CI. With `GITHUB_TOKEN`, pull-request `opened`, `synchronize` and `reopened` events create runs
  requiring a person with write access to approve them; ordinary push events still create no runs.
  `workflow_dispatch` and `repository_dispatch` also create runs. See GitHub's
  [GITHUB_TOKEN event rules](https://docs.github.com/en/actions/concepts/security/github_token).

## Actions settings (organization, then inherited)

- Workflow permissions: read repository contents only.
- Turn off "Allow GitHub Actions to create and approve pull requests".
- Require actions to be pinned to a full-length commit SHA (Actions policy, available since
  August 2025; verify on your plan).
- Allowed actions and reusable workflows: GitHub's, plus `Swatinem/rust-cache`,
  `taiki-e/install-action` and `mbras/rust-policy/.github/workflows/rust-ci.yml@*`. This blocks a
  caller that points at a workflow outside the organization.
- Agent tokens without the Workflows permission: GitHub refuses pushes that create or change files
  under `.github/workflows/` from such tokens.
- Fork pull requests from outside collaborators: require approval before workflows run.
- If `mbras/rust-policy` is private: in its Settings > Actions > General > Access, allow access from
  repositories in the organization. No extra token is needed, because the workflow verifies the
  consumer's vendored tools instead of checking out this repository.
- Protect `mbras/rust-policy` itself with a ruleset like the consumers' (pull request, owner review,
  required `self-test`).

## Rulesets

- Use rulesets rather than classic branch protection. Both apply together and the most restrictive
  wins, so migrate a weaker classic rule instead of keeping it.
- `rulesets/default-branch.json` requires: a pull request, one approval, code owner review, stale
  review dismissal, last-push approval, resolved threads, squash merges only, linear history, no
  deletion or force push, and the check `rust-policy / gate` from the GitHub Actions app, up to date
  with the base branch. `bypass_actors` is empty: nobody, admins included, merges around it.
- Break glass: an organization owner sets the ruleset's enforcement to `disabled`, which the audit
  log records, and re-enables it right after. If you prefer a standing bypass, add a human team with
  `bypass_mode: "pull_request"`, never `exempt` (no audit entry) and never an app.
- `evaluate` mode exists only on Enterprise Cloud; elsewhere the pilot uses `disabled`, then `active`.
- `apply-ruleset.sh` searches every page of repository-owned rulesets. If it finds duplicate kit
  names it stops before writing; resolve the duplicate explicitly instead of guessing which to edit.
- Run `prove_gates.sh` only against the canary. Its API probes count an explicit HTTP 403 permission
  denial as proof; invalid credentials, missing resources, validation errors, rate limits and network
  failures are inconclusive and fail the experiment. The Git push probe requires a GitHub branch-rule
  rejection (GH006/GH013), using only the agent token. An accepted probe is a failed control: inspect
  the canary for a posted status, disabled permission-proof ruleset or direct commit and clean it up.
  Offline tests cover classification; live enforcement still needs the pilot experiments.

## Code owners

P16 evaluates the CODEOWNERS file GitHub uses (`.github/`, then the root, then `docs/`) the way
GitHub does: for each file of the policy surface, the last matching line must exist and name only
owners from `policy.owners`. With `--mode full` the installer appends a managed block that
satisfies it; with `--mode minimal` the project writes its own lines. The block covers the policy surface: agent instruction
files at any depth, the profile, toolchain, formatter and Clippy files at any depth, cargo-deny,
`.cargo/` at any depth, `.github/`, `.claude/`, `.codex/`, `.opencode/`, `.rust-policy/`,
`docs/rust-policy/`, the CODEOWNERS file itself, `.gitattributes` and every `build.rs`. The block
must stay last because the last matching line wins, and every line must name only owners from
`rust-policy.toml` `policy.owners`; conformance P16 checks both. With `--reviewers`, a `* @team` line before the block makes a human review every pull request (P16
warns in reusable mode or when `adoption.visibility = "public"` if no `*` catch-all rule is present). Optionally set `policy.unsafe_owners`
(and `crates.critical`) in `rust-policy.toml` to require domain-owner CODEOWNERS rules before the
managed block for soundness-critical crates (`M34`). For public/OSS repositories, set
`adoption.visibility = "public"` (via `--visibility public`) to enforce a `Human-owned` PR disclosure
template (`P12`) and keep observational metrics separate from hard merge gates. GitHub reads
CODEOWNERS from the base branch, so a pull request cannot remove its own reviewers.

## Enterprise Cloud extension: required workflow

An organization ruleset with the "Require workflows to pass before merging" rule runs a workflow
from this repository on every targeted pull request, so even a pull request that rewrites its own
`.github/workflows/` cannot avoid it. GitHub documents it for Enterprise Cloud organizations; the
workflow must trigger on `pull_request` or `merge_group`. It is not included in this release because it has not been tested in an Enterprise Cloud
organization. Until then, the defense against a rewritten caller is code owner review of
`.github/` plus P01, which fails when the caller differs from the release template.
