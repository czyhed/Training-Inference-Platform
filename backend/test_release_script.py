"""Exercise the release helper without contacting Git or production."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path('C:/Program Files/Git/bin/bash.exe')
BASH = str(GIT_BASH) if os.name == 'nt' and GIT_BASH.exists() else shutil.which('bash')


@unittest.skipUnless(BASH, 'bash is required')
class ReleaseScriptTests(unittest.TestCase):
    def run_release(self, urls, missing=False):
        harness = r'''
        git() {
          case "$1" in
            rev-parse) test "$MISSING_TAG" = 0 ;;
            remote) printf '%s\n' "$PUSH_URLS" ;;
            push) printf 'PUSH'; printf ' <%s>' "$@"; printf '\n' ;;
            *) echo "UNEXPECTED_GIT $*"; return 90 ;;
          esac
        }
        ssh() { echo UNEXPECTED_SSH; return 91; }
        scp() { echo UNEXPECTED_SCP; return 92; }
        export -f git ssh scp
        bash scripts/release-bundle.sh v9.9.999
        '''
        return subprocess.run(
            [BASH, '-c', harness], cwd=ROOT, text=True, capture_output=True,
            env={**os.environ, 'PUSH_URLS': urls, 'MISSING_TAG': '1' if missing else '0'},
        )

    def test_gitea_release_pushes_only_the_requested_tag(self):
        result = self.run_release('http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PUSH <push> <origin> <refs/tags/v9.9.999>', result.stdout)
        self.assertNotIn('UNEXPECTED_', result.stdout)

    def test_ssh_gitea_remote_is_supported(self):
        result = self.run_release('ssh://git@git.leiziapi.cn:2222/leifengyang/cppt-platform.git')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_github_or_multiple_push_destinations_are_rejected(self):
        for urls in [
            'https://github.com/leifengyang/cppt-platform.git',
            'http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git\nhttps://github.com/leifengyang/cppt-platform.git',
        ]:
            with self.subTest(urls=urls):
                result = self.run_release(urls)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('origin must push only to the Gitea', result.stderr)
                self.assertNotIn('PUSH <', result.stdout)

    def test_missing_tag_does_not_push(self):
        result = self.run_release('http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git', missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not exist locally', result.stderr)
        self.assertNotIn('PUSH <', result.stdout)
