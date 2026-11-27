import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHA = '1e6c7b7518c2496384c36a0844e904b10576e569'


class ExactCheckoutTest(unittest.TestCase):
    def test_requested_sha_detached_checkout_and_mismatch_rejection(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                stub = p / 'git'
                stub.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$LOG"
case "$*" in
*"rev-parse FETCH_HEAD"|*"rev-parse HEAD") echo "$RESOLVED" ;;
esac
''')
                stub.chmod(0o755)
                env = dict(os.environ, PATH=tmp + ':' + os.environ['PATH'], LOG=str(p / 'log'),
                           RESOLVED='0' * 40 if mismatch else SHA)
                done = subprocess.run([str(ROOT / 'checkout-sdk.sh'), 'flutter-sdk', SHA, str(p / 'sdk')],
                                      env=env, capture_output=True, text=True)
                log = (p / 'log').read_text()
                self.assertIn('fetch --depth 1 origin ' + SHA, log)
                if mismatch:
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn('SHA mismatch', done.stderr)
                else:
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertIn('checkout --detach -f ' + SHA, log)

    def test_setup_from_root_and_parent(self):
        for parent in (False, True):
            with self.subTest(parent=parent), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                repo = p / 'cross-sdk-tests'
                repo.mkdir()
                shutil.copy2(ROOT / 'setup-sdks.sh', repo / 'setup-sdks.sh')
                (repo / 'sdk-branches.sh').write_text('declare -A SDK_BRANCHES=([flutter-sdk]="' + SHA + '")\n')
                helper = repo / 'checkout-sdk.sh'
                helper.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$LOG"\n')
                helper.chmod(0o755)
                done = subprocess.run(['bash', 'cross-sdk-tests/setup-sdks.sh' if parent else './setup-sdks.sh'],
                                      cwd=p if parent else repo, env=dict(os.environ, LOG=str(p / 'called')),
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn(SHA, (p / 'called').read_text())
