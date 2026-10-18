"""隔离验证 sop.sh：临时项目、mock npm/systemctl，不连接服务或数据库。
运行：python3 -m unittest backend.tests.check_sop -v
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
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
[ ! -e missing-index ] || exit 0
printf '%s' "$WORDS_BASE_PATH" > "$7/index.html"
''',
                    'mv': f'''#!/bin/sh
if [ -e fail-publish ] && [ "$1" = "$PWD/.run/build-dist" ]; then
 echo publish-failed >> events
 exit 1
fi
exec "{shutil.which('mv')}" "$@"
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
                for marker in ('missing-index', 'fail-publish'):
                    with self.subTest(failure=marker):
                        hashes = {name: (root / '.run' / name).read_bytes()
                                  for name in ('build-input.hash', 'build-dist.hash')}
                        (root / marker).touch()
                        before = (root / 'events').read_text().splitlines()
                        after = run(False)
                        expected = ['build:/new']
                        if marker == 'fail-publish':
                            expected.append('publish-failed')
                        self.assertEqual(after, before + expected)
                        self.assertEqual((root / 'frontend/dist/index.html').read_text(), '/new')
                        self.assertFalse((root / '.run/previous-dist').exists())
                        for name, content in hashes.items():
                            self.assertEqual((root / '.run' / name).read_bytes(), content)
                        (root / marker).unlink()
                (root / '.run/lock').mkdir()
                result = subprocess.run(['bash', 'sop.sh', 'restart'], cwd=root, env=env,
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / 'events').read_text().splitlines(), after)


class BuildStateCheck(unittest.TestCase):
    def test_systemd_base(self) -> None:
        with tempfile.TemporaryDirectory(prefix='words-build-state-') as folder:
            root = Path(folder)
            first = root / 'first.env'
            second = root / 'second.env'
            missing = root / 'missing.env'
            marker = root / 'must-not-exist'
            first.write_text('# comment\n; comment\n\nWORDS_BASE_PATH=/first\nSECRET=do-not-print\n')
            second.write_text('WORDS_BASE_PATH=/second/\n')
            quoted = root / 'quoted.env'
            quoted.write_text('WORDS_BASE_PATH="/quoted"\nSECRET=do-not-print\n')
            shell = root / 'shell.env'
            shell.write_text(f'WORDS_BASE_PATH=$(touch {marker})\n')
            unsupported = root / 'unsupported.env'
            unsupported.write_text('WORDS_BASE_PATH=/space \n')

            def spec(path: Path, optional: bool = False) -> str:
                return f'{path} (ignore_errors={"yes" if optional else "no"})'

            # EnvironmentFiles 覆盖 Environment；后文件优先；可选缺失不清空前值。
            cases = [
                ('empty', '', '', ''),
                ('unit', 'WORDS_BASE_PATH=/unit/', '', '/unit'),
                ('files-order', 'WORDS_BASE_PATH=/unit', f'{spec(first)} {spec(second)}', '/second'),
                ('files-reversed', '', f'{spec(second)} {spec(first)}', '/first'),
                ('optional-missing', 'WORDS_BASE_PATH=/unit', spec(missing, True), '/unit'),
                ('optional-between', '', f'{spec(first)} {spec(missing, True)} {spec(second)}', '/second'),
                ('required-missing', '', spec(missing), None),
                ('relative-file', '', 'relative.env (ignore_errors=no)', None),
                ('unknown-file-format', '', str(first), None),
                ('unknown-ignore-errors', '', f'{first} (ignore_errors=maybe)', None),
                ('quoted-value', '', spec(quoted), None),
                ('value-whitespace', '', spec(unsupported), None),
                ('shell-value', '', spec(shell), None),
                ('unclosed-unit-quote', 'WORDS_BASE_PATH="/unit', '', None),
            ]
            for base in ('relative', '/a/../b', '/has space', '/a?b', '//double', '/中文'):
                cases.append((f'invalid-base:{base}', f'WORDS_BASE_PATH="{base}"', '', None))
            for name, environment, files, expected in cases:
                with self.subTest(case=name):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'deploy/build_state.py'), 'base'],
                        input=f'{environment}\n{files}', cwd=root, text=True, capture_output=True,
                    )
                    self.assertNotIn('do-not-print', result.stdout + result.stderr)
                    self.assertFalse(marker.exists())
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')
                        self.assertIn('构建检查失败', result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, expected + '\n')


if __name__ == '__main__':
    unittest.main()
