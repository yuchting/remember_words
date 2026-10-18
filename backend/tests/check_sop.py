"""隔离验证 sop.sh：临时项目、mock npm/systemctl，不连接服务或数据库。
运行：python3 -m unittest backend.tests.check_sop -v
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SopCheck(unittest.TestCase):
    def test_restart_build_cache(self) -> None:
        for systemd in (False, True):
            with self.subTest(systemd=systemd), tempfile.TemporaryDirectory(prefix='words-sop-') as folder:
                root = Path(folder)
                for name in ('deploy', 'backend/app', 'frontend/src', '.venv/bin', 'bin'):
                    (root / name).mkdir(parents=True)
                shutil.copy(ROOT / 'deploy/build_state.py', root / 'deploy/build_state.py')
                script = (ROOT / 'sop.sh').read_text()
                # Only replace process/DB boundaries; build, dispatch and locks stay real.
                script = script.replace('# systemd owns the process;', '''start_service() { echo start >> "$ROOT/events"; }
stop_service() { echo stop >> "$ROOT/events"; }
# systemd owns the process;''')
                (root / 'sop.sh').write_text(script)
                for name in ('backend/requirements.lock', 'backend/requirements.txt', 'backend/app/main.py',
                             'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
                             'frontend/src/App.vue', 'frontend/vite.config.ts', 'frontend/tsconfig.json'):
                    (root / name).write_text('initial')
                env_file = root / 'service.env'
                env_file.write_text('WORDS_BASE_PATH=/words\nSECRET=do-not-print\n')
                executables = {
                    'node': '#!/bin/sh\nexit 0\n',
                    'npm': '''#!/bin/sh
if [ "$1" = --version ]; then echo mock; exit; fi
if [ "$3" = ci ]; then mkdir -p frontend/node_modules; echo ci >> events; exit; fi
echo "build:$WORDS_BASE_PATH" >> events
[ ! -e fail-build ] || exit 1
mkdir -p "$7"
printf '%s' "$WORDS_BASE_PATH" > "$7/index.html"
''',
                    'systemctl': '''#!/bin/sh
if [ "$1" = show ]; then
 case "$4" in
 LoadState) echo loaded ;;
 Environment) echo 'WORDS_BASE_PATH=/unit' ;;
 EnvironmentFiles) printf '%s (ignore_errors=no)\n' "$PWD/service.env" ;;
 esac
else
 echo "$2" >> events
fi
''',
                    'python-mock': f'''#!/bin/sh
case "$*" in
 *"import fastapi"*) exit 0 ;;
 *"from backend.app.db"*) echo 1; exit 0 ;;
 *"-m pip"*) echo pip >> events; exit 0 ;;
esac
exec "{shutil.which('python3')}" "$@"
''',
                }
                for name, text in executables.items():
                    path = root / 'bin' / name
                    path.write_text(text)
                    path.chmod(0o755)
                (root / '.venv/bin/python').symlink_to(root / 'bin/python-mock')
                env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}",
                           WORDS_SKIP_SYSTEMD='0' if systemd else '1', WORDS_BASE_PATH='/caller',
                           WORDS_HOSTS='localhost', PYTHON=str(root / 'bin/python-mock'))

                def run(ok: bool = True) -> list[str]:
                    result = subprocess.run(['bash', 'sop.sh', 'restart'], cwd=root, env=env,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
                    self.assertNotIn('do-not-print', result.stdout + result.stderr)
                    self.assertFalse((root / '.run/lock').exists())
                    return (root / 'events').read_text().splitlines()

                base = '/words' if systemd else '/caller'
                events = run()
                self.assertIn('build:' + base, events)
                self.assertEqual((root / 'frontend/dist/index.html').read_text(), base)
                count = events.count('build:' + base)
                self.assertEqual(run().count('build:' + base), count)
                for name in ('frontend/src/App.vue', 'backend/app/main.py', 'frontend/package-lock.json'):
                    (root / name).write_text('changed')
                    count += 1
                    self.assertEqual(run().count('build:' + base), count)
                self.assertEqual((root / 'events').read_text().splitlines().count('ci'), 2)
                (root / 'backend/requirements.lock').write_text('changed')
                count += 1
                self.assertEqual(run().count('build:' + base), count)
                self.assertEqual((root / 'events').read_text().splitlines().count('pip'), 4)
                for overwritten in (False, True):
                    if overwritten:
                        (root / 'frontend/dist/index.html').write_text('manual wrong build')
                    else:
                        (root / 'frontend/dist/index.html').unlink()
                    count += 1
                    self.assertEqual(run().count('build:' + base), count)
                if systemd:
                    env_file.write_text('WORDS_BASE_PATH=/new\n')
                else:
                    env['WORDS_BASE_PATH'] = '/new'
                self.assertIn('build:/new', run())
                before = (root / 'events').read_text().splitlines()
                (root / 'frontend/src/App.vue').write_text('broken')
                (root / 'fail-build').touch()
                after = run(False)
                self.assertEqual(after, before + ['build:/new'])
                self.assertEqual((root / 'frontend/dist/index.html').read_text(), '/new')
                (root / 'fail-build').unlink()
                (root / '.run/lock').mkdir()
                result = subprocess.run(['bash', 'sop.sh', 'restart'], cwd=root, env=env,
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / 'events').read_text().splitlines(), after)


if __name__ == '__main__':
    unittest.main()
