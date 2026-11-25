#!/usr/bin/env python3
"""Small, portable file edit for prove_gates.sh: prove_edit.py FILE OLD NEW.

Replaces the first OLD with NEW; an empty OLD appends NEW. Escapes \\n, \\t, \\" and \\\\ in NEW are unescaped.
"""
import pathlib
import re
import sys

if len(sys.argv) != 4:
    print("usage: prove_edit.py FILE OLD NEW", file=sys.stderr)
    sys.exit(64)

ESCAPES = {"\\": "\\", "n": "\n", "t": "\t", '"': '"'}
path, old = sys.argv[1], sys.argv[2]
new = re.sub(r'\\([\\nt"])', lambda m: ESCAPES[m.group(1)], sys.argv[3])
p = pathlib.Path(path)
text = p.read_text(encoding="utf-8") if p.exists() else ""
if old and old not in text:
    sys.exit(f"{path}: {old!r} not found")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(text.replace(old, new, 1) if old else text + new, encoding="utf-8")
