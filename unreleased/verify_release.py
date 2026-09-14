"""Check distribution completeness and original tile bytes after relocation."""
import ast
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
destination = Path(sys.argv[1]).resolve()
from build_release import CONTENTS

checked = 0
for name in CONTENTS:
    source = ROOT / name
    files = source.rglob('*') if source.is_dir() else [source]
    for path in files:
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc', '.tmp') or path.name in ('.gitkeep', '.keep'):
            continue
        target = destination / path.relative_to(ROOT)
        assert target.is_file(), target
        assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest(), target
        checked += 1
for name in ('unreleased', 'tests', '.git', 'userdata', 'logs', 'src/tiles', 'src/unreleased'):
    assert not (destination / name).exists(), name
for source in (destination / 'src').rglob('*.py'):
    ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
tiles = 0
for entry in subprocess.check_output(['git', 'ls-tree', '-rz', 'HEAD', 'src/tiles'], cwd=ROOT).split(b'\0'):
    if not entry:
        continue
    metadata, name = entry.split(b'\t', 1)
    original_hash = metadata.split()[2].decode()
    target = ROOT / name.decode().removeprefix('src/')
    content = target.read_bytes()
    actual_hash = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    assert actual_hash == original_hash, target
    tiles += 1
print(f'Distribution files verified: {checked}; relocated tile files verified: {tiles}')
