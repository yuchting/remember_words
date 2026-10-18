import argparse
import getpass
import sqlite3
from pathlib import Path

from argon2 import PasswordHasher
from .db import DB_PATH, connect, initialize
from .study import normalize


def main():
    parser = argparse.ArgumentParser(description='单词记忆：账户与备份')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create-parent')
    create.add_argument('username')
    backup = commands.add_parser('backup')
    backup.add_argument('destination')
    args = parser.parse_args()
    initialize()
    if args.command == 'create-parent':
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
