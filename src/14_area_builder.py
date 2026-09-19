"""Native project selection and direct file saving for the 14 map editor."""
import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# Embeddable Python does not add the script directory to sys.path.
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QObject, QUrl, pyqtSlot
from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from common.screening import project_area_path
from common.project_settings import get_project, set_project

spec = importlib.util.spec_from_file_location('builder14_area_validation', SRC_DIR / '15_area_screening.py')
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)


def area_path(project):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('既存のプロジェクトフォルダを選択してください。')
    target = project_area_path(project).resolve()
    if not target.is_relative_to(project):
        raise ValueError('保存先がプロジェクトの外を指しています。14_エリアデータを確認してください。')
    return target


def read_project(project):
    target = area_path(project)
    data = json.loads(target.read_text(encoding='utf-8-sig')) if target.exists() else None
    return {'project': str(Path(project).resolve()), 'path': str(target), 'data': data}


def save_project(project, data):
    # Validate before touching either the output directory or an existing file.
    definition = validator.parse_area_definition(data)
    for gate in definition.gates:
        if not validator.point_on_any_boundary(validator.Point(gate['lon'], gate['lat']), definition.analysis_polygons, .01):
            raise ValueError('分析エリアの辺上にゲートを設定してください。')
    target = area_path(project)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.14_area_', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return str(target)


class Bridge(QObject):
    def __init__(self, window, project):
        super().__init__(window)
        self.window, self.project = window, Path(project) if project else None

    def result(self, action):
        try:
            return json.dumps({'ok': True, **action()}, ensure_ascii=False)
        except Exception as error:
            return json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False)

    @pyqtSlot(result=str)
    def getProject(self):
        return self.result(lambda: read_project(self.project) if self.project else
                           {'project': None, 'path': None, 'data': None})

    @pyqtSlot(result=str)
    def chooseProject(self):
        def choose():
            selected = QFileDialog.getExistingDirectory(self.window, 'プロジェクトフォルダを選択', str(self.project) if self.project else '')
            if not selected:
                return {'cancelled': True}
            result = read_project(selected)
            self.project = set_project(selected)
            return result
        return self.result(choose)

    @pyqtSlot(str, result=str)
    def saveArea(self, text):
        if self.project is None:
            return json.dumps({'ok': False, 'error': '先にプロジェクトフォルダを選択してください。'}, ensure_ascii=False)
        return self.result(lambda: {'path': save_project(self.project, json.loads(text))})

    @pyqtSlot(str, result=str)
    def openFile(self, kind):
        def choose():
            if self.project is None:
                raise ValueError('先にプロジェクトフォルダを選択してください。')
            if kind == 'area':
                title, file_filter = 'エリア・ゲート設定を読み込む', 'GeoJSON ファイル (*.geojson *.json);;すべてのファイル (*)'
            elif kind == 'zoning':
                title, file_filter = 'ゾーニングCSVを読み込む', 'CSV ファイル (*.csv);;すべてのファイル (*)'
            else:
                raise ValueError('読込種別が不正です。')
            filename, _ = QFileDialog.getOpenFileName(self.window, title, str(self.project), file_filter)
            if not filename:
                return {'cancelled': True}
            return {'path': filename, 'text': Path(filename).read_text(encoding='utf-8-sig')}
        return self.result(choose)


class EditorPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if is_main_frame:
            return url.isLocalFile() and Path(url.toLocalFile()).resolve() == SRC_DIR / '14_area_builder.html'
        return True


class MainWindow(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.setWindowTitle('14 エリアビルダー・ゲート設定')
        self.resize(1280, 850)
        self.web = QWebEngineView(self)
        self.web.setPage(EditorPage(self.web))
        self.web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.setCentralWidget(self.web)
        self.channel = QWebChannel(self.web.page())
        self.bridge = Bridge(self, project or get_project())
        self.channel.registerObject('areaBridge', self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.web.setUrl(QUrl.fromLocalFile(str(SRC_DIR / '14_area_builder.html')))


def main():
    parser = argparse.ArgumentParser(description='14 エリアビルダー・手動ゲート設定')
    parser.add_argument('--project-dir', '--project_dir', type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    project = args.project_dir
    if project is not None and not project.is_dir():
        parser.error('プロジェクトフォルダがありません')
    window = MainWindow(project)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
