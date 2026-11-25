#!/usr/bin/env bash
# Runs every self-test of rust-policy. Needs Python 3.11+, PyYAML, actionlint, shellcheck, cargo
# metadata and, for test_check_sh.py, Rust 1.97.0 with Clippy/rustfmt, cargo-deny 0.20.2 and network
# access to refresh RustSec advisories. Compiler checks in test_install.py also use cargo.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/release.py --check
actionlint .github/workflows/*.yml
shellcheck tools/check.sh scripts/*.sh tests/run.sh
python3 tests/test_workflow.py
python3 tests/test_policy.py
python3 tests/test_install.py
python3 tests/test_admin_scripts.py
python3 tests/test_check_orchestration.py
if [ "${SKIP_CARGO_TESTS:-0}" != 1 ]; then python3 tests/test_check_sh.py; fi
echo "self-test: all passed"
