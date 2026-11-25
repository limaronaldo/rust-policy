#!/usr/bin/env python3
"""Writes the managed sums and version into .github/workflows/rust-ci.yml.

Run after changing anything under tools/, guide/ or templates/, then commit, tag v<VERSION> and pin
consumers to that commit. `--check` exits 1 when the workflow is stale (the self-test runs it).
"""
import pathlib
import re
import sys

import kit

WORKFLOW = kit.KIT / ".github/workflows/rust-ci.yml"
BEGIN = "  # BEGIN managed sums (scripts/release.py writes this block; do not edit by hand)\n"
END = "  # END managed sums\n"


def render():
    files, blocks, caller = kit.sums()
    lines = [BEGIN, f'  POLICY_VERSION: "{kit.VERSION}"\n', f'  POLICY_REPO: "{kit.POLICY_REPO}"\n',
             "  POLICY_FILE_SUMS: |\n"]
    lines += [f"    {d}  {p}\n" for p, d in files.items()]
    lines += ["  POLICY_BLOCK_SUMS: |\n"] + [f"    {d}  {p}\n" for p, d in blocks.items()]
    lines += [f'  POLICY_CALLER_SUM: "{caller}"\n', END]
    return "".join(lines)


def main(argv):
    if argv not in ([], ["--check"]):
        print("usage: release.py [--check]", file=sys.stderr)
        return 64
    wf = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), wf, re.S)
    if not m:
        print(f"{WORKFLOW}: managed sums block not found", file=sys.stderr)
        return 1
    new = wf[:m.start()] + render() + wf[m.end():]
    for name, src in kit.MANAGED_BLOCKS.items():
        if f"BEGIN rust-policy {kit.VERSION} " not in kit.text(src):
            print(f"{src}: the BEGIN line must carry version {kit.VERSION}", file=sys.stderr)
            return 1
    if argv == ["--check"]:
        if new != wf:
            print("rust-ci.yml is stale: run scripts/release.py", file=sys.stderr)
            return 1
        print("rust-ci.yml sums are current")
        return 0
    WORKFLOW.write_text(new, encoding="utf-8")
    print(f"wrote managed sums for {kit.VERSION} into {WORKFLOW.relative_to(kit.KIT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
