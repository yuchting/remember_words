import argparse
import getpass
import sqlite3
from pathlib import Path

from argon2 import PasswordHasher
from .db import DB_PATH, connect, initialize
from .study import normalize


def manage_admin(parser: argparse.ArgumentParser) -> None:
    with connect() as db:
        parents = db.execute("SELECT id,username,is_active FROM users WHERE role='parent' ORDER BY id").fetchall()
    if not parents:
        name = normalize(input('尚无管理员，请输入管理员用户名：'))
        if not name or len(name) > 50:
            parser.error('账户名长度为 1–50 字符')
        target = None
    else:
        if len(parents) == 1:
            target = parents[0]
        else:
            for index, parent in enumerate(parents, 1):
                print(f'{index}. {parent["username"]}' + ('（已停用）' if not parent['is_active'] else ''))
            choice = input('请选择管理员序号：').strip()
            if not choice.isascii() or not choice.isdigit() or not 1 <= int(choice) <= len(parents):
                parser.error('管理员序号无效')
            target = parents[int(choice) - 1]
        name = target['username']
        if not target['is_active']:
            parser.error('该管理员已停用，不会重置密码或自动启用')
        if input(f'确认重置管理员 {name} 的密码并使其登录会话失效？[y/N]：').strip().lower() != 'y':
            print('已取消')
            return
    password = getpass.getpass('新密码（8–128 字符）：')
    if not 8 <= len(password) <= 128:
        parser.error('密码长度错误')
    if password != getpass.getpass('再次输入密码：'):
        parser.error('两次密码不同')
    password_hash = PasswordHasher().hash(password)
    try:
        with connect() as db:
            if target is None:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM users WHERE role='parent'").fetchone():
                    parser.error('管理员已发生变化，请重新运行')
                db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'parent')", (name, password_hash))
            else:
                updated = db.execute("UPDATE users SET password_hash=? WHERE id=? AND role='parent' AND is_active=1",
                                     (password_hash, target['id']))
                if updated.rowcount != 1:
                    parser.error('管理员已发生变化，请重新运行')
                db.execute('DELETE FROM auth_sessions WHERE user_id=?', (target['id'],))
    except sqlite3.IntegrityError:
        parser.error('账户名已存在，请使用其他用户名')
    print('管理员已创建' if target is None else '管理员密码已重置，原登录会话已失效')


def main() -> None:
    parser = argparse.ArgumentParser(description='单词记忆：账户与备份')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create-parent')
    create.add_argument('username')
    commands.add_parser('admin', help='创建管理员或重置管理员密码')
    backup = commands.add_parser('backup')
    backup.add_argument('destination')
    args = parser.parse_args()
    initialize()
    if args.command == 'admin':
        try:
            manage_admin(parser)
        except (EOFError, KeyboardInterrupt):
            parser.exit(1, '\n操作已取消，未保存更改\n')
    elif args.command == 'create-parent':
        name = normalize(args.username)
        if not name or len(name) > 50:
            parser.error('账户名长度为 1–50 字符')
        password = getpass.getpass('密码（8–128 字符）：')
        if not 8 <= len(password) <= 128:
            parser.error('密码长度错误')
        if password != getpass.getpass('再次输入密码：'):
            parser.error('两次密码不同')
        try:
            with connect() as db:
                db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'parent')",
                           (name, PasswordHasher().hash(password)))
        except sqlite3.IntegrityError:
            parser.error('账户名已存在')
        print('家长账户已创建')
    else:
        destination = Path(args.destination).resolve()
        if destination.exists() or destination == DB_PATH.resolve():
            parser.error('备份目标已存在，请使用新文件名')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        print(f'备份完成：{destination.as_posix()}')


if __name__ == '__main__':
    main()
