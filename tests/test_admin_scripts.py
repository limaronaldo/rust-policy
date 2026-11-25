"""Offline checks of administration scripts. All gh/git writes are mocked; no network access."""
import json
import base64
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

KIT = pathlib.Path(__file__).resolve().parents[1]


class AdminScripts(unittest.TestCase):
    def ruleset(self, mode):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            log = root / "calls.jsonl"
            gh = root / "gh"
            gh.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['CALL_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if '/apps/github-actions' in args: print('15368')
elif '--method' in args: pass
elif any('/rulesets' in a for a in args):
 if os.environ['CASE'] == 'duplicate': print('41\\n42')
 elif '--paginate' in args and any('includes_parents=false' in a for a in args): print('42')
elif any('/rules/branches/' in a for a in args): print('pull_request')
else: print('main')
''')
            gh.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], CALL_LOG=str(log), CASE=mode)
            r = subprocess.run(['bash', str(KIT / 'scripts/apply-ruleset.sh'), 'owner/repo', 'active'], env=env, text=True, capture_output=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return r, calls

    def test_ruleset_lookup_is_paginated_and_repository_scoped(self):
        r, calls = self.ruleset('later-page')
        self.assertEqual(r.returncode, 0, r.stderr)
        writes = [c for c in calls if '--method' in c]
        self.assertEqual(len(writes), 1)
        self.assertIn('PUT', writes[0])
        self.assertIn('/repos/owner/repo/rulesets/42', writes[0])

    def test_duplicate_ruleset_name_fails_without_mutation(self):
        r, calls = self.ruleset('duplicate')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse([c for c in calls if '--method' in c])

    def test_api_permission_proofs_require_the_expected_denial(self):
        script = (KIT / 'scripts/prove_gates.sh').read_text()
        self.assertIn('# BEGIN API proof helper', script)
        helper = script.split('# BEGIN API proof helper\n', 1)[1].split('# END API proof helper', 1)[0]
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            gh = root / 'gh'
            gh.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$RESPONSE"\nexit "$GH_EXIT"\n')
            gh.chmod(0o755)
            cases = [
                ('permission denial', 'HTTP/2.0 403 Forbidden\n{"message":"Resource not accessible by integration"}', '1', 'ok'),
                ('admin denial', 'HTTP/2.0 403 Forbidden\n{"message":"Must have admin rights to Repository."}', '1', 'ok'),
                ('success', 'HTTP/2.0 201 Created\n{}', '0', 'ALLOWED'),
                ('invalid token', 'HTTP/2.0 401 Unauthorized\n{}', '1', 'INCONCLUSIVE'),
                ('hidden resource', 'HTTP/2.0 404 Not Found\n{}', '1', 'INCONCLUSIVE'),
                ('invalid request', 'HTTP/2.0 422 Unprocessable Entity\n{}', '1', 'INCONCLUSIVE'),
                ('rate limited', 'HTTP/2.0 403 Forbidden\n{"message":"API rate limit exceeded"}', '1', 'INCONCLUSIVE'),
                ('network failure', 'connection refused', '1', 'INCONCLUSIVE'),
                ('proxy connect', 'HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 403 \r\n{"message":"Resource not accessible by integration"}', '1', 'ok'),
                ('proxy connect compact', 'HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 403\r\n{"message":"Resource not accessible by integration"}', '1', 'ok'),
            ]
            for name, response, code, verdict in cases:
                with self.subTest(name=name):
                    env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], RESPONSE=response, GH_EXIT=code, AGENT_TOKEN='offline-test')
                    body = 'set -euo pipefail\nreport() { printf "%s\\n" "$3"; }\n' + helper + '\nexpect_forbidden_api proof /repos/owner/repo/rulesets -f name=proof -f enforcement=disabled\n'
                    r = subprocess.run(['bash', '-c', body], env=env, text=True, capture_output=True)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue(r.stdout.startswith(verdict), r.stdout)

    def test_agent_clone_url_requires_authenticated_https_metadata(self):
        script = (KIT / 'scripts/prove_gates.sh').read_text()
        helper = script.split('# BEGIN API proof helper\n', 1)[1].split('# END API proof helper', 1)[0]
        self.assertIn('agent_clone_url()', helper)
        for url, want_ok in [('https://github.com/owner/canary.git', True),
                             ('https://github.example/owner/canary.git', True),
                             ('git@github.com:owner/canary.git', False),
                             ('file:///tmp/canary.git', False),
                             ('https://token@github.com/owner/canary.git', False)]:
            with self.subTest(url=url):
                env = dict(os.environ, META=json.dumps({'full_name':'owner/canary', 'clone_url':url}))
                r = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + helper + '\nagent_clone_url "$META"\n'],
                                   env=env, text=True, capture_output=True)
                self.assertEqual(r.returncode == 0, want_ok, r.stderr)
                if want_ok:
                    self.assertEqual(r.stdout.strip(), url)

    def test_probe_ruleset_stays_disabled_even_when_template_is_active(self):
        script = (KIT / 'scripts/prove_gates.sh').read_text()
        command = next(line for line in script.splitlines() if line.startswith("jq '.name = "))
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / 'scripts').mkdir()
            (root / 'rulesets').mkdir()
            (root / 'probe').mkdir()
            (root / 'rulesets/default-branch.json').write_text('{"name":"template", "enforcement":"active"}')
            env = dict(os.environ, here=str(root / 'scripts'), work=str(root / 'probe'))
            subprocess.run(['bash', '-c', 'set -euo pipefail\n' + command], env=env, check=True, capture_output=True)
            body = json.loads((root / 'probe/probe-ruleset.json').read_text())
            self.assertEqual(body['enforcement'], 'disabled')
            self.assertEqual(body['name'], 'rust-policy: permission proof')

    def test_push_permission_proof_rejects_authentication_and_transport_errors(self):
        script = (KIT / 'scripts/prove_gates.sh').read_text()
        helper = script.split('# BEGIN API proof helper\n', 1)[1].split('# END API proof helper', 1)[0]
        self.assertIn('expect_forbidden_push()', helper)
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            git = root / 'git'
            git.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "${AGENT_GIT_AUTH:-missing}" >"$AUTH_LOG"\nprintf "%s\\n" "$@" >"$ARGS_LOG"\nprintf "%s\\n" "$PUSH_ERROR" >&2\nexit "$GIT_EXIT"\n')
            git.chmod(0o755)
            cases = [('GH013: Repository rule violations found', '1', 'ok'),
                     ('GH006: Protected branch update failed', '1', 'ok'),
                     ('Authentication failed', '1', 'INCONCLUSIVE'),
                     ('Could not resolve host', '1', 'INCONCLUSIVE'),
                     ('', '0', 'PUSHED')]
            for error, code, verdict in cases:
                with self.subTest(error=error, exit=code):
                    env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], PUSH_ERROR=error, GIT_EXIT=code,
                               AGENT_TOKEN='offline-test', agent_push_url='https://github.example/owner/canary.git', AUTH_LOG=str(root / 'auth'), ARGS_LOG=str(root / 'args'), work=d, base='main')
                    body = 'set -euo pipefail\nreport() { printf "%s\\n" "$3"; }\n' + helper + '\nexpect_forbidden_push\n'
                    r = subprocess.run(['bash', '-c', body], env=env, text=True, capture_output=True)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue(r.stdout.startswith(verdict), r.stdout)
                    header = (root / 'auth').read_text().strip()
                    self.assertTrue(header.startswith('Authorization: Basic '), 'missing Basic Git authentication')
                    self.assertEqual(base64.b64decode(header.split()[-1]).decode(), 'x-access-token:offline-test')
                    self.assertNotIn('offline-test', (root / 'args').read_text())
                    args = (root / 'args').read_text().splitlines()
                    self.assertIn('https://github.example/owner/canary.git', args)
                    self.assertNotIn('origin', args)  # origin may use the owner's SSH identity
                    self.assertNotIn('offline-test', r.stdout + r.stderr)

    def test_prove_edit_handles_utf8_escapes_and_bad_argc(self):
        script = str(KIT / 'scripts/prove_edit.py')
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / 'lib.rs'
            target.write_text('fn main() {}\n', encoding='utf-8')
            new = r'/// ação — €\n\tlet p = \"a\\b\";\n\tlet s = \"\u{1F600}\";\nfn main()'
            r = subprocess.run([sys.executable, script, str(target), 'fn main()', new], text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(
                target.read_text(encoding='utf-8'),
                '/// ação — €\n\tlet p = "a\\b";\n\tlet s = "\\u{1F600}";\nfn main() {}\n',
            )
            for bad_args in ([str(target), 'only_two'], [str(target), 'a', 'b', 'extra']):
                with self.subTest(bad_args=bad_args):
                    bad = subprocess.run([sys.executable, script, *bad_args], text=True, capture_output=True)
                    self.assertNotEqual(bad.returncode, 0)

    def test_owners_team_normalization_extracts_slug_and_rejects_invalid_values(self):
        script = (KIT / 'scripts/prove_gates.sh').read_text()
        self.assertIn('# BEGIN OWNER HELPER', script)
        helper = script.split('# BEGIN OWNER HELPER\n', 1)[1].split('# END OWNER HELPER', 1)[0]
        for raw, want in [('@org/rust-policy-owners', 'rust-policy-owners'),
                          ('rust-policy-owners', 'rust-policy-owners'),
                          ('org/rust-policy-owners', 'rust-policy-owners')]:
            with self.subTest(raw=raw):
                r = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + helper + '\nnormalize_owners_team "$1"\n', '_', raw],
                                   text=True, capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), want)
        for bad in ('@org/a/b', '@org/', 'bad team', 'a"b'):
            with self.subTest(bad=bad):
                r = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + helper + '\nnormalize_owners_team "$1"\n', '_', bad],
                                   text=True, capture_output=True)
                self.assertEqual(r.returncode, 64, r.stdout + r.stderr)

    def test_release_rejects_unknown_arguments_without_touching_workflow(self):
        wf = KIT / '.github/workflows/rust-ci.yml'
        before_bytes = wf.read_bytes()
        before_mtime = wf.stat().st_mtime_ns
        for args in (['--unknown'], ['--check', '--extra']):
            with self.subTest(args=args):
                r = subprocess.run([sys.executable, str(KIT / 'scripts/release.py'), *args],
                                   cwd=KIT, text=True, capture_output=True)
                self.assertEqual(r.returncode, 64, r.stdout + r.stderr)
                self.assertIn('usage:', r.stderr)
                self.assertEqual(wf.read_bytes(), before_bytes)
                self.assertEqual(wf.stat().st_mtime_ns, before_mtime)


if __name__ == '__main__':
    unittest.main()
