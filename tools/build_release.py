"""Create an independent, copy-ready distribution; never overwrite an old build."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CONTENTS = ('bat', 'src', 'runtime', 'tiles', 'docs', 'README.md', 'requirements.txt')


def ignore_release_files(directory, names):
    ignored = set(shutil.ignore_patterns('__pycache__', '*.pyc', '*.tmp', '.gitkeep', '.keep')(directory, names))
    if Path(directory).resolve() == (ROOT / 'docs').resolve():
        ignored.add('development')
    return ignored


def build(destination):
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT.is_relative_to(destination):
        raise ValueError('配布先に開発フォルダ自身やその親は指定できません。')
    for name in CONTENTS:
        source = ROOT / name
        if not source.exists():
            raise FileNotFoundError(source)
        if destination.is_relative_to(source):
            raise ValueError('配布先をコピー元の中に指定できません。')
    if not (ROOT / 'runtime/python/pythonw.exe').is_file():
        raise FileNotFoundError('同梱Pythonがありません。')
    destination.mkdir(parents=True, exist_ok=False)
    for name in CONTENTS:
        source = ROOT / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=ignore_release_files)
        else:
            shutil.copy2(source, target)
    (destination / '00_ETC_launcher.bat').write_bytes(
        b'@echo off\r\ncall "%~dp0bat\\00_ETC_launcher.bat" %*\r\n')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='配布用一式を新しいフォルダに作成します。')
    parser.add_argument('--output', type=Path, default=ROOT / 'release' / ('ETC_' + datetime.now().strftime('%Y%m%d_%H%M%S')))
    args = parser.parse_args()
    print(build(args.output))
