import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class LauncherTests(unittest.TestCase):
    def test_launch_opens_bootstrap_url_and_retries_busy_port_without_changing_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            shutil.copy2(Path(__file__).resolve().parents[1] / 'scripts' / 'launch.sh', root / 'scripts' / 'launch.sh')
            (root / '.env').write_text('YANDEX_BOOK_TOKEN=existing-private-key\n')
            bins = root / 'bin'
            bins.mkdir()
            commands = {
                'uname': '#!/bin/sh\necho Darwin\n',
                'ipconfig': '#!/bin/sh\necho 192.168.1.20\n',
                'curl': '#!/bin/sh\nexit 0\n',
                'open': '#!/bin/sh\nprintf "%s" "$1" > "$LAUNCH_TEST_ROOT/opened"\n',
                'docker': '''#!/bin/sh
case "$1:$2" in
 info:*) exit 0;;
 compose:version) exit 0;;
 compose:up)
  if [ ! -f "$LAUNCH_TEST_ROOT/first-attempt" ]; then
   touch "$LAUNCH_TEST_ROOT/first-attempt"
   echo 'port is already allocated' >&2; exit 1
  fi
  exit 0;;
 compose:exec) printf 'http://localhost:%s/setup#private-bootstrap-key\n' "$APP_PORT";;
 *) exit 2;;
esac
''',
            }
            for name, text in commands.items():
                file = bins / name
                file.write_text(text)
                file.chmod(0o755)
            env = {**os.environ, 'PATH': str(bins) + ':/usr/bin:/bin', 'LAUNCH_TEST_ROOT': str(root), 'APP_PORT': '8000'}
            result = subprocess.run(['/bin/sh', str(root / 'scripts' / 'launch.sh')], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'data' / 'launcher.port').read_text(), '8001')
            self.assertEqual((root / 'opened').read_text(), 'http://localhost:8001/setup#private-bootstrap-key')
            self.assertEqual((root / '.env').read_text(), 'YANDEX_BOOK_TOKEN=existing-private-key\n')
            self.assertNotIn('private-bootstrap-key', result.stdout + result.stderr)
