# How each agent receives the instructions

The same `AGENTS.md` serves every tool. Its managed block is under 6 KB and contains no `@` outside
code spans, because Claude Code reads `@path` as an import.

| Tool | What it loads | How rust-policy uses it | Confidence |
| --- | --- | --- | --- |
| Codex CLI | `~/.codex/AGENTS.override.md` or `AGENTS.md`, then for each directory from the repository root to the working directory `AGENTS.override.md`, else `AGENTS.md`, else the configured fallback names; closer files later, up to `project_doc_max_bytes` (32 KiB in total by default). Built at session start. References inside the file are not followed. | Root `AGENTS.md` tells it to read `rust-policy.toml` and the register. P12 refuses tracked `AGENTS.override.md` (an untracked local one still wins in that checkout, and P12 warns); P13 keeps every chain under 24 KiB. Restart Codex after policy changes. | Documented; checked by two council members against the docs |
| Codex subagents | Custom agents in `.codex/agents/*.toml` with `name`, `description`, `developer_instructions`; they inherit the parent's sandbox and approvals (a sandbox setting in the agent file does not override them). Whether they receive `AGENTS.md` is not documented. | `.codex/agents/rust-reviewer.toml` names every file the reviewer must read; it is told not to edit, and only a read-only parent session makes that technical. Delegation briefs carry the hard rules. | Field names from the Codex subagents page; not run |
| Claude Code | `CLAUDE.md` files from the working directory upward, `@path` imports (relative to the importing file, up to four hops, not inside code). Native `AGENTS.md` reading requires v2.1.277 or later; by default it is suppressed by a project `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md` on that path. | `CLAUDE.md` holds `@AGENTS.md` and `@rust-policy.toml` in its managed block. Check with `/memory` or `/context` that both loaded. | Documented; the `.toml` import is untested |
| Claude Code subagents | Built-in Explore and Plan start without `CLAUDE.md`; custom agents in `.claude/agents/` load it unless configured otherwise. | `rust-reviewer` is a custom agent that also names its inputs. Briefs carry the hard rules. | Documented |
| OpenCode | `AGENTS.md` from the working directory upward (`CLAUDE.md` only when no `AGENTS.md` exists), plus `instructions` in `opencode.json` (paths, globs, URLs). References inside `AGENTS.md` are not followed. | `opencode.json` adds `rust-policy.toml` to `instructions`. | Documented; not run |

Instruction files are context, not enforcement. The enforcement is CI plus the ruleset; the
instructions only make agents succeed at the first attempt more often.

The template schemas were compared with the official documentation on 2026-10-31:
[Codex subagents](https://developers.openai.com/codex/subagents),
[Claude Code subagents](https://code.claude.com/docs/en/sub-agents),
[Claude Code instruction loading](https://code.claude.com/docs/en/memory), and
[OpenCode rules](https://opencode.ai/docs/rules/). Neither agent CLI was installed in the review
environment, so these are schema and documentation checks, not successful reviewer launches. The
council-review notes above preserve the release author's supplied history.

## Required checks and evidence

Run `.rust-policy/check.sh ci --base <base-commit>` for the change's actual base. It follows
`adoption.checks` and the feature sets in `rust-policy.toml`; conformance, downgrade protection and
ratchets always apply. `fast` and `full` are explicit broader presets and may include checks that
have not been adopted. Minimal/external adoption therefore does not ask the project's harness to
run every preset before each commit.

An uncommitted change can be reviewed and tested. Record its base SHA and identify the working tree
diff tested; for committed work, record the commit range. Rerun after edits and never attribute an
earlier passing command to a later state. Test commands can write normal build artifacts even when
the reviewer does not edit project files. Report tracked-file changes caused by a command, and
report checks that the parent's permissions or missing toolchains prevented from running.

## Delegation brief (from AGENTS.md)

Goal and acceptance criteria; base commit; files and crates in scope; the policy surface as out of
scope; the guide sections, MUST ids and exception ids that apply; the exact commands; the report
format; a verbatim copy of "Hard rules". A subagent may start with none of the repository's
instructions, so a brief without these items is incomplete.

## Reviewer output

One line per finding: `rule | MUST or SHOULD | path:line | evidence | consequence | fix`, then the
commit range reviewed, or base SHA and uncommitted diff, commands run, areas not reviewed and a verdict (`changes-required`,
`no-findings`, `needs-human-judgment`). Any diff touching `unsafe`, `// SAFETY:`, `# Safety`, or an
`unsafe_allowed` crate must return at least `needs-human-judgment` (`M34`). An agent verdict never
replaces the human approval.

## Cross-model adversarial review and human-first governance

- **Human-first canonical docs:** engineering standards and agent rules live in `docs/rust-policy/guide.md`
  (section 15, `M34`); `AGENTS.md` and `CLAUDE.md` only summarize them.
- **Cross-model advisory review:** models exhibit self-preference bias toward code generated by the same
  model. For `unsafe`, FFI, async, dependency or public API changes, prefer running `rust-reviewer` on a
  different model family than the author when available (for example, Codex reviewing a Claude-authored
  diff or vice versa). Agent review remains strictly advisory.
- **Two-layer pull requests:** `templates/pull_request_template.md` separates a `Human-owned` section
  (left empty by agents so the responsible engineer records extent/purpose of LLM use, confirms reading
  the full diff, explains invariants/uncertainties, and signs off on `M34` soundness) from factual
  `What and why` / `Verification` sections.
- **Deterministic linters first:** recurring review findings should become `clippy.toml`
  (`disallowed-methods`, `disallowed-types`) rules or conformance checks rather than prompt instructions.
