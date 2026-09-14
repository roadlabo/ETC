"""Shared last-used project, kept outside replaceable application sources."""
import json
import logging
import os
from pathlib import Path
import tempfile

STATE_PATH = Path(os.environ['ETC_PROJECT_STATE']) if os.environ.get('ETC_PROJECT_STATE') else Path(__file__).resolve().parents[2] / 'userdata' / 'project.json'


def get_project():
    try:
        value = json.loads(STATE_PATH.read_text(encoding='utf-8')).get('project', '')
        path = Path(value) if value else None
        return path.resolve() if path and path.is_dir() else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def set_project(project):
    path = Path(project).resolve()
    if not path.is_dir():
        raise ValueError('プロジェクトフォルダがありません。')
    temporary = None
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=STATE_PATH.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'project': str(path)}, stream, ensure_ascii=False)
        os.replace(temporary, STATE_PATH)
    except OSError:
        logging.exception('プロジェクト設定を保存できませんでした。')
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return path


def restore_project(callback):
    """Restore after the window and its signals are fully initialized."""
    from PyQt6.QtCore import QTimer
    project = get_project()
    if project:
        QTimer.singleShot(0, lambda: callback(str(project)))
