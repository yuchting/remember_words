#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE=/etc/remember-words.env
fail() { echo "错误：$*" >&2; exit 1; }
if [[ "${1:-}" == --help ]]; then
    echo '用法：sudo ./setup_service.sh'
    echo '原地安装或更新 words.service；数据库固定为当前项目 data/words.sqlite3。'
    echo '不安装或修改 Nginx。保留已有其他配置；旧数据库在线备份迁入，原库保留。'
    echo '首次可设置 WORDS_BASE_PATH=/words WORDS_HTTPS=1；以后沿用 /etc/remember-words.env。'
    exit
fi
[[ $# == 0 ]] || fail '不支持该参数'
[[ $EUID == 0 ]] || fail '请以 root 或 sudo 运行'
for command in python3 node npm systemctl flock; do
    command -v "$command" >/dev/null || fail "缺少 $command"
done
python3 -c 'import sys,venv; assert sys.version_info >= (3,11)' || fail '需要 Python 3.11+'
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(!((a===22&&b>=12)||a>22))process.exit(1)' || fail '需要 Node.js 22.12+'
exec 9>/run/remember-words-setup.lock
flock -n 9 || fail '另一个安装正在进行'
cd "$ROOT"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip check

# Parse only literal KEY=value entries; never execute an environment file.
.venv/bin/python - "$ROOT" "$ENV_FILE" <<'PY'
import os
import socket
import sys
from pathlib import Path

root, env_file = map(Path, sys.argv[1:])
settings: dict[str, str] = {}
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, separator, value = line.partition('=')
            if not separator or value != value.strip() or any(c in value for c in '\"\'\\'):
                sys.exit('环境文件仅支持无引号 KEY=value，请先检查 ' + str(env_file))
            settings[key] = value
base = settings.setdefault('WORDS_BASE_PATH', os.environ.get('WORDS_BASE_PATH', ''))
os.environ['WORDS_BASE_PATH'] = base
from backend.app.main import BASE_PATH  # Validate before changing service configuration.
settings.setdefault('WORDS_HTTPS', os.environ.get('WORDS_HTTPS', '0'))
if settings['WORDS_HTTPS'] not in ('0', '1'):
    sys.exit('WORDS_HTTPS 必须为 0 或 1')
hosts = {'localhost', '127.0.0.1'}
try:
    hosts.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect(('192.0.2.1', 80))
        hosts.add(connection.getsockname()[0])
except OSError:
    pass
settings.setdefault('WORDS_HOSTS', os.environ.get('WORDS_HOSTS', ','.join(sorted(hosts))))
old_db = Path(settings.get('WORDS_DB', str(root / 'data/words.sqlite3')))
if not old_db.is_absolute():
    sys.exit('已有 WORDS_DB 必须为绝对路径，避免迁移错误数据库')
(root / '.run').mkdir(mode=0o700, exist_ok=True)
(root / '.run/service-old-db').write_text(str(old_db))
(root / '.run/service.env').write_text('\n'.join(f'{k}={v}' for k, v in settings.items()) + '\n')
(root / '.run/service-base').write_text(BASE_PATH)
PY
npm --prefix frontend ci
WORDS_BASE_PATH="$(cat .run/service-base)" npm --prefix frontend run build

# Stop all managed writers before taking the final snapshot. Never overwrite a target database.
if [[ "$(systemctl show words.service -p LoadState --value)" != not-found ]]; then
    systemctl stop words.service
fi
if [[ -f .run/server.pid ]]; then
    WORDS_SKIP_SYSTEMD=1 ./sop.sh stop
fi
.venv/bin/python - "$ROOT" <<'PY'
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

root = Path(sys.argv[1])
old = Path((root / '.run/service-old-db').read_text())
target = root / 'data/words.sqlite3'
target.parent.mkdir(mode=0o700, exist_ok=True)
if old.resolve() != target.resolve() and old.exists():
    if target.exists():
        sys.exit('旧库和当前目录库同时存在；拒绝自动选库或覆盖，请人工核对后修改 WORDS_DB')
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(old.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('迁移数据库完整性检查失败')
    except BaseException:
        target.unlink()
        raise
    print('数据库已在线备份迁入；原库保留：', old)
settings = (root / '.run/service.env').read_text().splitlines()
settings = [line for line in settings if not line.startswith('WORDS_DB=')]
settings.append('WORDS_DB=' + str(target))
(root / '.run/service.env').write_text('\n'.join(settings) + '\n')
PY
install -m 600 .run/service.env "$ENV_FILE"
# Run as project owner: supports repositories under /root without changing directory permissions.
.venv/bin/python - "$ROOT" <<'PY'
import grp
import pwd
import sys
from pathlib import Path

root = Path(sys.argv[1])
owner = root.stat()
text = (root / 'deploy/words.service').read_text()
# systemd expands specifiers even within quoted values.
if any(c in str(root) for c in '\n\r"\\'):
    sys.exit('项目路径不能包含换行、双引号或反斜杠')
for key, value in {'@ROOT@': str(root).replace('%', '%%'), '@USER@': pwd.getpwuid(owner.st_uid).pw_name, '@GROUP@': grp.getgrgid(owner.st_gid).gr_name}.items():
    text = text.replace(key, value)
Path('/etc/systemd/system/words.service').write_text(text)
PY
chown -R "$(stat -c '%u:%g' "$ROOT")" "$ROOT/data"
systemd-analyze verify /etc/systemd/system/words.service
systemctl daemon-reload
systemctl enable words.service
systemctl restart words.service
.venv/bin/python - "$(cat .run/service-base)" <<'PY'
import sys
import time
import urllib.request

client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(40):
    try:
        with client.open('http://127.0.0.1:18765' + sys.argv[1] + '/api/health', timeout=1) as response:
            if response.status == 200 and b'"ok":true' in response.read(1024):
                break
    except OSError:
        pass
    time.sleep(0.25)
else:
    sys.exit('健康检查失败：journalctl -u words -n 100 --no-pager')
PY
rm -f .run/service.env .run/service-old-db .run/service-base
systemctl is-active words.service
echo "服务已安装：$ROOT；数据库：$ROOT/data/words.sqlite3"
echo '管理：./sop.sh start|stop|status|restart；配置：/etc/remember-words.env'
echo "首次创建家长：$ROOT/.venv/bin/python -m backend.app.cli create-parent parent"
