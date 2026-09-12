"""Project-first route analysis UI. Analysis runs off the GUI thread."""
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
                             QFileDialog, QComboBox, QTextBrowser, QProgressBar)
from common.route_path import scan_project, analyze, LABELS

class Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, project, target, engine):
        super().__init__()
        self.project, self.target, self.engine = project, target, engine

    def run(self):
        try:
            self.done.emit(analyze(self.project, self.target, self.engine, self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))

class RouteWindow(QWidget):
    def __init__(self, engine, project=None):
        super().__init__()
        self.engine, self.project, self.targets, self.report = engine, None, [], None
        self.worker = None
        self.setWindowTitle('50 経路分析 — ルート通過交通')
        self.resize(900, 720)
        self.setStyleSheet('''
            QWidget { font-family: "Meiryo UI", "Yu Gothic UI", sans-serif; font-size: 14px; color: #163047; }
            QPushButton { padding: 10px; }
            QComboBox { padding: 6px; }
            QTextBrowser { background: #f5f8fa; padding: 12px; }
        ''')
        layout = QVBoxLayout(self)
        self.choose = QPushButton('STEP 1 プロジェクトフォルダ [選択]')
        self.choose.clicked.connect(self.pick)
        layout.addWidget(self.choose)
        self.checks = QLabel('プロジェクトフォルダを選択してください')
        self.checks.setWordWrap(True)
        layout.addWidget(self.checks)
        layout.addWidget(QLabel('STEP 2 分析対象路線'))
        self.routes = QComboBox()
        self.routes.currentIndexChanged.connect(self.selection)
        layout.addWidget(self.routes)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.run_button = QPushButton('分析を実行')
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.start)
        layout.addWidget(self.run_button)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.result = QTextBrowser()
        layout.addWidget(self.result)
        self.open_button = QPushButton('HTMLレポート・経路地図を開く')
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.report)))
        layout.addWidget(self.open_button)
        if project:
            self.load(project)

    def pick(self):
        path = QFileDialog.getExistingDirectory(self, 'プロジェクトフォルダ')
        if path:
            self.load(Path(path))

    def load(self, project):
        self.routes.clear()
        self.targets = []
        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.result.clear()
        try:
            _, self.targets = scan_project(project)
            self.project = Path(project)
            self.checks.setText(f'{project}\n✓ 12_エリアデータ\n✓ 15_area.geojson\n✓ analysis_area\n✓ 20_第２スクリーニング(ルート)')
            self.routes.addItems([t.name for t in self.targets])
        except Exception as exc:
            self.checks.setText(str(exc))

    def selection(self, index):
        if 0 <= index < len(self.targets):
            target = self.targets[index]
            self.status.setText(f'トリップ数：{len(target.files):,}\n' + ('⚠ '+target.warning if target.warning else '✓ 第1.5スクリーニング由来\n状態：分析可能（実行時にCSV・Gateを検証）'))
            self.run_button.setEnabled(True)
            self.open_button.setEnabled(False)

    def start(self):
        self.run_button.setEnabled(False)
        self.choose.setEnabled(False)
        self.routes.setEnabled(False)
        self.open_button.setEnabled(False)
        self.worker = Worker(self.project, self.targets[self.routes.currentIndex()], self.engine)
        self.worker.progress.connect(self.update_progress)
        self.worker.done.connect(self.finished)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.unlock)
        self.worker.start()

    def update_progress(self, done, total):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def unlock(self):
        self.run_button.setEnabled(True)
        self.choose.setEnabled(True)
        self.routes.setEnabled(True)

    def failed(self, message):
        self.result.setPlainText('分析エラー：' + message)

    def finished(self, result):
        self.report = result['report']
        n = result['counts']['ALL']
        self.update_progress(n, n)
        lines = [f'対象路線：{self.routes.currentText()}', f'対象トリップ：{n:,}']
        for k, label in LABELS.items():
            if k != 'ALL':
                count = result['counts'][k]
                lines.append(f'{label}  {count:,}  ({count/n:.1%})' if n else f'{label}  0')
        lines.append('通過交通率：' + (f"{result['through_rate']:.1%}" if result['official'] else '正式値無効'))
        lines += ['主要通過OD'] + [f"{r['start_gate']} → {r['end_gate']}  {r['trip_count']:,}" for r in result['ranking'][:20]]
        lines += result['warnings']
        self.result.setPlainText('\n'.join(lines))
        self.open_button.setEnabled(True)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore()
        else:
            event.accept()

def launch(engine, project=None):
    app = QApplication.instance() or QApplication([])
    window = RouteWindow(engine, project)
    window.show()
    app.exec()
