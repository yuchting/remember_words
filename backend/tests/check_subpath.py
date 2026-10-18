"""独立运行：.venv/bin/python -m backend.tests.check_subpath（仅临时数据库）。"""
import os
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        os.environ['WORDS_DB'] = str(Path(directory) / 'test.sqlite3')
        os.environ['WORDS_BASE_PATH'] = '/words'
        os.environ['WORDS_HTTPS'] = '1'
        from fastapi.testclient import TestClient
        from backend.app.db import connect
        from backend.app.main import app, hasher

        with TestClient(app, base_url='https://testserver') as client:
            with connect() as db:
                db.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',
                           ('parent', hasher.hash('password123'), 'parent'))
            assert client.get('/api/health').headers['cache-control'] == 'no-store'
            from unittest.mock import AsyncMock, patch
            gate = AsyncMock()
            with patch('backend.app.main.api_gate', gate):
                assert client.get('/words/api/health').headers['cache-control'] == 'no-store'
            gate.__aenter__.assert_awaited_once()
            gate.__aexit__.assert_awaited_once()
            # Large database restores reach authentication; ordinary APIs stay at 2 MiB.
            assert client.post('/words/api/admin/database/restore', content=b'x' * (2 * 1024 * 1024 + 1)).status_code == 401
            import re
            page = client.get('/words/')
            assert page.status_code == 200, page.text
            assets = re.findall(r'(?:src|href)="(/words/assets/[^"]+)"', page.text)
            assert len(assets) >= 2, '请先 WORDS_BASE_PATH=/words 构建前端'
            for asset in assets:
                assert client.get(asset).status_code == 200, asset
            login = client.post('/api/auth/login', json={'username': 'parent', 'password': 'password123'})
            assert login.status_code == 200, login.text
            cookie = login.headers['set-cookie']
            assert 'Path=/words/' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie
            assert client.get('/words/api/auth/me').status_code == 200
            assert client.post('/words/api/auth/logout').status_code == 403
            headers = {'X-CSRF-Token': login.json()['csrf_token']}
            assert client.post('/words/api/auth/logout', headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
            assert client.post('/words/api/auth/login', content=b'x' * (2 * 1024 * 1024 + 1)).status_code == 413
            logout = client.post('/words/api/auth/logout', headers=headers)
            assert logout.status_code == 200
            assert 'Path=/words/' in logout.headers['set-cookie']
            assert client.get('/words/api/auth/me').status_code == 401
    print('subpath Cookie、登录退出、CSRF、Origin、请求大小检查通过')


if __name__ == '__main__':
    main()
