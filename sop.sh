#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
RUN="$ROOT/.run"
PID_FILE="$RUN/server.pid"
LOG="$RUN/server.log"
PY="$ROOT/.venv/bin/python"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18765}"
ACTION="${1:-help}"
export PYTHONUNBUFFERED=1

fail() { echo "错误：$*" >&2; exit 1; }
help() {
    cat <<'HELP'
用法：./sop.sh [start|stop|status|restart|admin|service]
  已安装 words.service 时，start/stop/status/restart 转交 systemctl。
  不带参数时显示此帮助。
  start    未安装系统服务时：检查环境、补齐依赖、构建前端、后台启动
  restart  先准备依赖和构建，再停止旧服务并启动
  stop     优雅停止本脚本启动的服务
  service  安装或更新 systemd 服务（需 root 或 sudo）
  status   查看进程状态（不进入分页器）
  admin    交互重置管理员密码；没有管理员时输入用户名创建（无需重启服务）
  环境检查在 start、restart 时自动执行；日志位于 .run/server.log。
环境变量：PYTHON=python3 HOST=0.0.0.0 PORT=18765
局域网：HOST=0.0.0.0 WORDS_HOSTS=localhost,127.0.0.1,主机IP ./sop.sh start
TLS：WORDS_HTTPS=1 TLS_CERT=/证书路径 TLS_KEY=/私钥路径
不会自动执行 .env 文件。数据库配置沿用 WORDS_DB。
HELP
}

check_env() {
    local python="${PYTHON:-python3}"
    command -v "$python" >/dev/null || fail '缺少 Python，请安装 Python 3.11+'
    "$python" -c 'import sys, sqlite3, venv; assert sys.version_info >= (3,11), "需要 Python 3.11+"' || fail 'Python 环境不满足要求'
    command -v node >/dev/null || fail '缺少 Node.js，请安装 Node.js 22.12+'
    command -v npm >/dev/null || fail '缺少 npm'
    node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(!((a===22&&b>=12)||a>22))process.exit(1)' || fail '需要 Node.js 22.12+（或更高主版本）'
    command -v ps >/dev/null || fail '缺少 ps，无法安全识别服务进程'
    [[ "$PORT" =~ ^[0-9]+$ ]] || fail 'PORT 必须为数字'
    "$python" -c 'import sys; assert 1 <= int(sys.argv[1]) <= 65535' "$PORT" || fail 'PORT 必须为 1–65535'
    [[ -f backend/requirements.lock && -f frontend/package-lock.json ]] || fail '缺少依赖锁文件'
    if [[ "${WORDS_HTTPS:-0}" == 1 ]]; then
        [[ -r "${TLS_CERT:-}" && -r "${TLS_KEY:-}" ]] || fail 'HTTPS 模式需要可读的 TLS_CERT 和 TLS_KEY'
    elif [[ -n "${TLS_CERT:-}${TLS_KEY:-}" ]]; then
        fail '使用 TLS_CERT/TLS_KEY 时必须设置 WORDS_HTTPS=1'
    fi
    "$python" --version
    echo "Node.js $(node --version)，npm $(npm --version)"
    echo "环境检查通过；监听 $HOST:$PORT"
}

# Compare PID, creation time and exact application path before sending signals.
read_service() {
    [[ -f "$PID_FILE" ]] || return 1
    IFS= read -r PID < "$PID_FILE"
    [[ "$PID" =~ ^[0-9]+$ ]] || fail 'PID 文件损坏，请检查 .run/server.pid'
    SAVED_START="$(sed -n '2p' "$PID_FILE")"
    ACTUAL_START="$(ps -p "$PID" -o lstart= 2>/dev/null || true)"
    [[ -n "$ACTUAL_START" && "$ACTUAL_START" == "$SAVED_START" ]] || return 1
    local command
    command="$(ps -p "$PID" -o command=)"
    [[ "$command" == *"-m uvicorn backend.app.main:app --app-dir $ROOT --host "* ]] || fail 'PID 身份不匹配，拒绝操作该进程'
}

stop_service() {
    if ! read_service; then
        rm -f "$PID_FILE"
        echo '服务未运行'
        return
    fi
    kill -TERM "$PID"
    for ((i=0; i<100; i++)); do
        if ! read_service; then
            rm -f "$PID_FILE"
            echo '服务已停止'
            return
        fi
        sleep 0.1
    done
    fail "服务未在 10 秒内停止，未强制杀进程。请检查 PID $PID 和 $LOG"
}

prepare() {
    check_env
    if [[ ! -x "$PY" ]]; then
        "${PYTHON:-python3}" -m venv "$ROOT/.venv"
    fi
    "$PY" -c 'import sys; assert sys.version_info >= (3,11)' || fail '现有 .venv 不兼容，请人工检查后重建'
    local py_hash node_hash
    py_hash="$("$PY" -c 'import hashlib; print(hashlib.sha256(open("backend/requirements.lock","rb").read()).hexdigest())')"
    node_hash="$("$PY" -c 'import hashlib; print(hashlib.sha256(open("frontend/package-lock.json","rb").read()).hexdigest())')"
    if [[ "$(cat "$RUN/python.hash" 2>/dev/null || true)" != "$py_hash" ]] || ! "$PY" -c 'import fastapi,uvicorn,argon2,httpx' 2>/dev/null; then
        "$PY" -m pip install -r backend/requirements.lock
        "$PY" -m pip check
        echo "$py_hash" > "$RUN/python.hash"
    fi
    if [[ ! -d frontend/node_modules ]] || [[ "$(cat "$RUN/node.hash" 2>/dev/null || true)" != "$node_hash" ]]; then
        npm --prefix frontend ci
        echo "$node_hash" > "$RUN/node.hash"
    fi
    npm --prefix frontend run build
    if [[ -z "${WORDS_HOSTS:-}" ]]; then
        WORDS_HOSTS="$("$PY" - <<'PY'
import socket
hosts = {'localhost', '127.0.0.1'}
try:
    hosts.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
except OSError:
    pass
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect(('192.0.2.1', 80))
        hosts.add(connection.getsockname()[0])
except OSError:
    pass
print(','.join(sorted(hosts)))
PY
)"
        export WORDS_HOSTS
    fi
    echo "允许的访问地址：$WORDS_HOSTS"
    local parents
    parents="$("$PY" -c 'from backend.app.db import initialize,connect; initialize(); c=connect(); db=c.__enter__(); print(db.execute("SELECT count(*) FROM users WHERE role=? AND is_active=1",("parent",)).fetchone()[0]); c.__exit__(None,None,None)')"
    if [[ "$parents" == 0 ]]; then
        [[ -t 0 ]] || fail '尚无家长账户，请在终端运行 .venv/bin/python -m backend.app.cli create-parent 账户名'
        local name
        read -r -p '首次运行，请输入家长账户名：' name
        "$PY" -m backend.app.cli create-parent "$name"
    fi
}

start_service() {
    "$PY" - "$HOST" "$PORT" <<'PY'
import socket, sys
try:
    with socket.create_server((sys.argv[1], int(sys.argv[2]))):
        pass
except OSError as exc:
    sys.exit(f'端口检查失败，不会停止占用端口的其他程序：{exc}')
PY
    set -- "$PY" -m uvicorn backend.app.main:app --app-dir "$ROOT" --host "$HOST" --port "$PORT"
    if [[ "${WORDS_HTTPS:-0}" == 1 ]]; then
        set -- "$@" --ssl-certfile "$TLS_CERT" --ssl-keyfile "$TLS_KEY"
    fi
    nohup "$@" >> "$LOG" 2>&1 < /dev/null &
    PID=$!
    echo "$PID" > "$PID_FILE"
    ps -p "$PID" -o lstart= >> "$PID_FILE"
    local scheme=http
    [[ "${WORDS_HTTPS:-0}" != 1 ]] || scheme=https
    if ! "$PY" - "$PID" "$PORT" "$scheme" "$LOG" <<'PY'
import os, ssl, sys, time, urllib.request
pid, port, scheme, log = sys.argv[1:]
# Local readiness only; external TLS trust still needs device verification.
client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
for _ in range(40):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        break
    try:
        with client.open(f'{scheme}://127.0.0.1:{port}/api/health', timeout=0.5) as response:
            if response.status == 200 and b'"ok":true' in response.read(1024):
                sys.exit(0)
    except Exception:
        time.sleep(0.25)
sys.exit(f'启动健康检查失败，请查看 {log}；使用 ./sop.sh stop 停止残留服务')
PY
    then
        tail -n 30 "$LOG"
        return 1
    fi
    echo "服务已启动，PID ${PID}；本机地址 $scheme://127.0.0.1:$PORT"
    echo "日志：${LOG}；状态：./sop.sh status；停止：./sop.sh stop"
    if [[ "$HOST" != 127.0.0.1 && "$HOST" != localhost ]]; then
        echo '局域网访问请将 URL 中的 127.0.0.1 替换为主机 IP，并将该 IP 加入 WORDS_HOSTS。'
        [[ "$scheme" == https ]] || echo '警告：HTTP 不加密密码与会话，仅在可信局域网临时使用。'
    fi
}

install_service() {
ENV_FILE=/etc/remember-words.env
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
}

# Delegate before checking PID files or building; systemd owns the installed process.
if [[ "${WORDS_SKIP_SYSTEMD:-0}" != 1 && "$ACTION" =~ ^(start|stop|status|restart)$ ]] && command -v systemctl >/dev/null; then
    load_state="$(systemctl show words.service -p LoadState --value 2>/dev/null || true)"
    if [[ -n "$load_state" && "$load_state" != not-found ]]; then
        exec systemctl --no-pager "$ACTION" words.service
    fi
fi

case "$ACTION" in
    -h|--help|help) help; exit ;;
    status) if read_service; then echo "服务运行中，PID ${PID}；日志 $LOG"; else echo '服务未运行'; fi; exit ;;
    service)
        [[ $# == 1 ]] || fail 'service 不支持额外参数'
        install_service
        exit ;;
    admin)
        [[ -t 0 && -t 1 ]] || fail 'admin 需要在交互终端运行'
        [[ -x "$PY" ]] || fail '缺少 .venv，请先完成项目环境安装'
        "$PY" -c 'import argon2' 2>/dev/null || fail '缺少 Python 依赖，请使用 .venv/bin/python -m pip install -r backend/requirements.lock 安装'
        "$PY" -m backend.app.cli admin
        exit ;;
    start|restart|stop) ;;
    *) help; exit 1 ;;
esac
mkdir -p "$RUN"
chmod 700 "$RUN"
mkdir "$RUN/lock" 2>/dev/null || fail '已有管理操作进行中；若上次被强制中断，确认无操作后删除 .run/lock'
trap 'rmdir "$RUN/lock"' EXIT
if [[ "$ACTION" == stop ]]; then stop_service; exit; fi
if [[ "$ACTION" == start ]] && read_service; then echo "服务已运行，PID ${PID}；重启用 ./sop.sh restart"; exit; fi
prepare
if [[ "$ACTION" == restart ]]; then stop_service; fi
start_service
