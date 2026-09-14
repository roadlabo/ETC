"""Native host for zoning, sharing the project with the other tools."""
import base64
import json
from pathlib import Path
import sys
import tempfile

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))
from PyQt6.QtCore import QObject, QUrl, pyqtSlot
from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from common.project_settings import get_project, set_project


def save_zoning(project, folder, filename, payload):
    root = Path(project).resolve()
    if not root.is_dir() or folder != '12_ゾーニングデータ':
        raise ValueError('保存先が正しくありません。')
    if not filename or any(c in filename for c in '\\/:*?"<>|') or filename.endswith(('.', ' ')) or not filename.endswith('.csv'):
        raise ValueError('CSVファイル名が正しくありません。')
    target = (root / folder / filename).resolve()
    if not target.is_relative_to(root):
        raise ValueError('保存先がプロジェクトの外を指しています。')
    data = base64.b64decode(payload, validate=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return str(target)


class Bridge(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.project = get_project()

    @pyqtSlot(result=str)
    def getProject(self):
        return json.dumps({'ok': True, 'project': str(self.project) if self.project else None}, ensure_ascii=False)

    @pyqtSlot(result=str)
    def chooseProject(self):
        selected = QFileDialog.getExistingDirectory(self.window, 'プロジェクトフォルダを選択', str(self.project or ''))
        if not selected:
            return json.dumps({'ok': True, 'cancelled': True})
        self.project = set_project(selected)
        return self.getProject()

    @pyqtSlot(str, str, str, result=str)
    def saveFile(self, folder, filename, payload):
        try:
            if not self.project:
                raise ValueError('先にプロジェクトフォルダを選択してください。')
            path = save_zoning(self.project, folder, filename, payload)
            return json.dumps({'ok': True, 'path': path}, ensure_ascii=False)
        except Exception as error:
            return json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False)


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main):
        return not main or (url.isLocalFile() and Path(url.toLocalFile()).resolve() == SRC_DIR / '12_polygon_builder.html')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('12 ゾーニングデータ作成')
        self.resize(1280, 850)
        self.web = QWebEngineView(self)
        self.web.setPage(Page(self.web))
        for attribute in [QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                          QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls]:
            self.web.settings().setAttribute(attribute, True)
        self.channel = QWebChannel(self.web.page())
        self.bridge = Bridge(self)
        self.channel.registerObject('projectBridge', self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.setCentralWidget(self.web)
        self.web.setUrl(QUrl.fromLocalFile(str(SRC_DIR / '12_polygon_builder.html')))


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
