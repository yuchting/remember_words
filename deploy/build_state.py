"""sop.sh 构建指纹与只读 systemd 环境解析；仅使用标准库。"""
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys


def fingerprint(paths: list[Path], extra: str = '') -> str:
    digest = hashlib.sha256(extra.encode())
    for path in sorted(set(paths)):
        digest.update(str(path).encode() + b'\0')
        digest.update(path.read_bytes() if path.is_file() else b'<missing>')
        digest.update(b'\0')
    return digest.hexdigest()


def inputs(base: str) -> str:
    paths = [Path('frontend/index.html'), Path('frontend/package.json'),
             Path('frontend/package-lock.json'), Path('backend/requirements.txt'),
             Path('backend/requirements.lock')]
    for folder in ('frontend/src', 'frontend/public', 'backend/app'):
        paths.extend(p for p in Path(folder).rglob('*') if p.is_file()
                     and ('__pycache__' not in p.parts)
                     and (folder != 'backend/app' or p.suffix == '.py'))
    for pattern in ('tsconfig*.json', 'vite.config.*', '.env*'):
        paths.extend(Path('frontend').glob(pattern))
    paths.append(Path(__file__).relative_to(Path.cwd()))
    paths.append(Path('sop.sh'))
    return fingerprint(paths, base)


def service_base(environment: str, files: str) -> str:
    # Match installer policy: literal KEY=value only, never source shell code.
    settings = dict(item.split('=', 1) for item in shlex.split(environment) if '=' in item)
    rest = files.strip()
    while rest:
        match = re.match(r'(.+?) \(ignore_errors=(yes|no)\)(?:\s+|$)', rest)
        if not match:
            raise ValueError('无法安全解析 systemd EnvironmentFiles')
        name, optional = match.groups()
        names = shlex.split(name)
        if len(names) != 1 or not Path(names[0]).is_absolute():
            raise ValueError('不支持的 EnvironmentFile 路径')
        path = Path(names[0])
        if path.exists() or optional != 'yes':
            for line in path.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith(('#', ';')):
                    continue
                key, separator, value = line.partition('=')
                if not separator or value != value.strip() or any(c in value for c in '\"\'\\'):
                    raise ValueError('环境文件仅支持无引号 KEY=value，请检查服务配置')
                settings[key.strip()] = value
        rest = rest[match.end():]
    return settings.get('WORDS_BASE_PATH', '')


def normalize_base(base: str) -> str:
    base = base.rstrip('/')
    if base and not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)+', base):
        raise ValueError('WORDS_BASE_PATH 必须为空或 /words 形式的路径')
    return base


if __name__ == '__main__':
    try:
        action = sys.argv[1]
        if action == 'base':
            environment, files = sys.stdin.read().split('\n', 1)
            print(normalize_base(service_base(environment, files)))
        elif action == 'normalize':
            print(normalize_base(os.environ.get('WORDS_BASE_PATH', '')))
        elif action == 'inputs':
            print(inputs(normalize_base(os.environ.get('WORDS_BASE_PATH', ''))))
        elif action == 'dist':
            files = [p for p in Path('frontend/dist').rglob('*') if p.is_file()]
            print(fingerprint(files) if Path('frontend/dist/index.html').is_file() else '')
        else:
            raise ValueError('未知构建检查操作')
    except (ValueError, OSError):
        sys.exit('构建检查失败：请检查构建文件或 systemd 环境配置（仅支持无引号 KEY=value）')
