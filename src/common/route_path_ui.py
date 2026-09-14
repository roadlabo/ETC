"""Project-first route analysis UI. Analysis runs off the GUI thread."""
from pathlib import Path
from html import escape
from time import monotonic
from PyQt6.QtCore import QThread, pyqtSignal, QUrl, Qt, QTimer, QPropertyAnimation
from PyQt6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
                             QFileDialog, QComboBox, QTextBrowser, QProgressBar,
                             QHBoxLayout, QGridLayout, QFrame, QGraphicsOpacityEffect,
                             QScrollArea, QLayout, QSizePolicy)
from common.route_path import scan_project, analyze, LABELS
from common.ui.logo_link import ClickableLogoLabel

LOGO_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'logos' / 'logo_50_Path_Analysis.png'

STYLE = '''
QWidget { background: #050908; color: #d6ffe8; font-family: "Meiryo UI", "Segoe UI"; }
QFrame#panel { background: #08120e; border: 1px solid #1c4f33; border-radius: 8px; }
QFrame#metric { background: #0a1b14; border: 1px solid #208956; border-radius: 6px; }
QLabel { background: transparent; border: none; }
QLabel#eyebrow { color: #72c698; }
QLabel#missionState { color: #00ff99; border: 1px solid #208956; border-radius: 5px; padding: 6px 12px; }
QPushButton { background: #0a1b14; border: 1px solid #2ef29a; border-radius: 7px; padding: 10px 12px; font-weight: bold; }
QPushButton:hover { background: #103322; }
QPushButton:pressed { background: #195638; }
QPushButton:disabled { color: #597262; border-color: #224432; }
QPushButton#execute { background: #123e28; }
QComboBox, QTextBrowser, QProgressBar { background: #0a120f; border: 1px solid #1f3f2d; border-radius: 6px; }
QComboBox { padding: 8px; }
QComboBox QAbstractItemView { background: #0a120f; color: #d6ffe8; selection-background-color: #195638; }
QProgressBar { min-height: 22px; text-align: center; }
QProgressBar::chunk { background: #00b878; border-radius: 4px; }
QTextBrowser { padding: 10px; selection-background-color: #195638; }
QToolTip { background: #103322; color: #d6ffe8; border: 1px solid #208956; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #08120e; width: 10px; }
QScrollBar::handle:vertical { background: #208956; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
'''

def text_label(text, points=10, bold=False, object_name=None):
    label = QLabel(text)
    label.setFont(QFont('Meiryo UI', points, QFont.Weight.Bold if bold else QFont.Weight.Normal))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if object_name:
        label.setObjectName(object_name)
    return label

def panel(title):
    frame = QFrame()
    frame.setObjectName('panel')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    layout.addWidget(text_label(title, 11, True, 'eyebrow'))
    return frame, layout

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
    def __init__(self, engine, project=None, *, show_splash=True):
        super().__init__()
        # Combo-box popups are separate windows and inherit the application font.
        application_font = QApplication.font()
        if application_font.pointSizeF() <= 0:
            application_font.setPointSize(10)
            QApplication.setFont(application_font)
        self.engine, self.project, self.targets, self.report = engine, None, [], None
        self.worker = None
        self.splash = None
        self._logo_anim = None
        self._started_at = None
        self.setWindowTitle('50 経路分析 — ルート通過交通')
        self.resize(1180, 820)
        # Pixel-sized fonts have pointSize() == -1. Native Qt styles can pass that
        # value to setPointSize(), producing the reported warning. Keep the entire
        # window on explicit positive point sizes, including the rich-text font.
        self.setFont(QFont('Meiryo UI', 10))
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(text_label('MISSION 50  /  ROUTE PATH ANALYSIS', 10, True, 'eyebrow'))
        titles.addWidget(text_label('経路分析  /  ルート通過交通', 21, True))
        titles.addWidget(text_label('対象路線の交通を分類し、Gate間ODと実走行経路を確認します。', 10))
        header.addLayout(titles, 1)
        self.corner_logo = ClickableLogoLabel(self)
        self.corner_logo.setStyleSheet('background: transparent; border: none;')
        pixmap = QPixmap(str(LOGO_PATH)) if LOGO_PATH.exists() else QPixmap()
        if not pixmap.isNull():
            self.setWindowIcon(QIcon(pixmap))
            self.corner_logo.setPixmap(pixmap.scaledToHeight(90, Qt.TransformationMode.SmoothTransformation))
            self.corner_logo.setFixedSize(self.corner_logo.pixmap().size())
        else:
            self.corner_logo.setText('ETC ANALYZER / 50')
        header.addWidget(self.corner_logo)
        layout.addLayout(header)

        telemetry = QHBoxLayout()
        self.mission_state = text_label('STANDBY  /  待機中', 10, True, 'missionState')
        self.mission_state.setWordWrap(False)
        telemetry.addWidget(self.mission_state)
        telemetry.addStretch()
        self.elapsed = text_label('経過時間  00:00:00', 10, False, 'eyebrow')
        telemetry.addWidget(self.elapsed)
        layout.addLayout(telemetry)

        body = QGridLayout()
        body.setHorizontalSpacing(16)
        body.setColumnStretch(0, 4)
        body.setColumnStretch(1, 6)
        setup_widget = QWidget()
        setup = QVBoxLayout(setup_widget)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        project_panel, project_layout = panel('STEP 1  /  プロジェクト')
        self.choose = QPushButton('プロジェクトフォルダを選択')
        self.choose.clicked.connect(self.pick)
        project_layout.addWidget(self.choose)
        self.checks = text_label('プロジェクトフォルダを選択してください。\n区域とルートデータを自動確認します。')
        self.checks.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.checks.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        project_layout.addWidget(self.checks)
        setup.addWidget(project_panel)
        route_panel, route_layout = panel('STEP 2  /  分析対象路線')
        self.routes = QComboBox()
        self.routes.setMinimumContentsLength(8)
        self.routes.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.routes.currentIndexChanged.connect(self.selection)
        route_layout.addWidget(self.routes)
        self.status = text_label('路線を選択すると、件数と由来を表示します。')
        route_layout.addWidget(self.status)
        self.run_button = QPushButton('分析を実行')
        self.run_button.setObjectName('execute')
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.start)
        route_layout.addWidget(self.run_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        route_layout.addWidget(self.progress)
        setup.addWidget(route_panel)
        setup.addStretch()
        setup.addWidget(text_label('Gate → Gate = 通過交通\nGate → Inside = 外内交通\nInside → Gate = 内外交通\nInside → Inside = 内内交通', 10, False, 'eyebrow'))
        self.setup_scroll = QScrollArea()
        self.setup_scroll.setWidgetResizable(True)
        self.setup_scroll.setWidget(setup_widget)
        body.addWidget(self.setup_scroll, 0, 0)

        result_panel, result_layout = panel('ANALYSIS REPORT  /  分析結果')
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, title in [('ALL', '対象トリップ'), ('THROUGH', '通過交通'), ('RATE', '通過交通率')]:
            card = QFrame()
            card.setObjectName('metric')
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(text_label(title, 9, False, 'eyebrow'))
            value = text_label('—', 22, True)
            card_layout.addWidget(value)
            metrics.addWidget(card, 1)
            self.metrics[key] = value
        result_layout.addLayout(metrics)
        self.result = QTextBrowser()
        self.result.document().setDefaultFont(QFont('Meiryo UI', 10))
        self.result.setPlainText('分析結果はここに表示されます。\n\nプロジェクトと対象路線を選択し、分析を実行してください。')
        result_layout.addWidget(self.result, 1)
        self.open_button = QPushButton('HTMLレポート・経路地図を開く')
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.report)))
        result_layout.addWidget(self.open_button)
        body.addWidget(result_panel, 0, 1)
        layout.addLayout(body, 1)
        layout.addWidget(text_label('ETC2.0 ANALYZER  /  15 → 20 → 50     •     集計単位：区域内サブトリップ', 9, False, 'eyebrow'))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_elapsed)
        from common.project_settings import get_project
        project = project or get_project()
        if project:
            self.load(project)
        if show_splash and not pixmap.isNull():
            QTimer.singleShot(0, self.show_logo_splash)

    def show_logo_splash(self):
        pixmap = QPixmap(str(LOGO_PATH))
        if pixmap.isNull():
            return
        self.splash = QLabel(self)
        self.splash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.splash.setPixmap(pixmap.scaledToWidth(min(600, self.width() - 60), Qt.TransformationMode.SmoothTransformation))
        self.splash.adjustSize()
        self._position_splash()
        self.splash.show()
        self.splash.raise_()
        effect = QGraphicsOpacityEffect(self.splash)
        self.splash.setGraphicsEffect(effect)
        self._logo_anim = QPropertyAnimation(effect, b'opacity', self)
        self._logo_anim.setDuration(450)
        self._logo_anim.setStartValue(0.0)
        self._logo_anim.setEndValue(1.0)
        self._logo_anim.finished.connect(lambda: QTimer.singleShot(1600, self.fade_logo_splash))
        self._logo_anim.start()

    def fade_logo_splash(self):
        if self.splash is None:
            return
        self._logo_anim = QPropertyAnimation(self.splash.graphicsEffect(), b'opacity', self)
        self._logo_anim.setDuration(450)
        self._logo_anim.setStartValue(1.0)
        self._logo_anim.setEndValue(0.0)
        self._logo_anim.finished.connect(self._remove_splash)
        self._logo_anim.start()

    def _remove_splash(self):
        if self.splash:
            self.splash.deleteLater()
            self.splash = None

    def _position_splash(self):
        if self.splash:
            self.splash.move((self.width()-self.splash.width())//2, (self.height()-self.splash.height())//2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_splash()

    def update_elapsed(self):
        seconds = int(monotonic() - self._started_at) if self._started_at is not None else 0
        self.elapsed.setText(f'経過時間  {seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}')

    def reset_results(self):
        self.report = None
        self.open_button.setEnabled(False)
        self.result.clear()
        for value in self.metrics.values():
            value.setText('—')
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._started_at = None
        self.update_elapsed()

    def pick(self):
        path = QFileDialog.getExistingDirectory(self, 'プロジェクトフォルダ')
        if path:
            self.load(Path(path))

    def load(self, project):
        self.reset_results()
        self.routes.clear()
        self.targets = []
        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.result.clear()
        try:
            _, self.targets = scan_project(project)
            from common.project_settings import set_project
            self.project = set_project(project)
            self.checks.setText(f'{self.project.name}\n✓ 14_エリアデータ/14_area.geojson\n✓ analysis_area・指定ゲート\n✓ 12_ゾーニングデータのCSV\n✓ 20_第２スクリーニング(ルート)\n✓ 境界起終点は最寄りの指定ゲートへ割当')
            self.checks.setToolTip(str(project))
            self.choose.setToolTip(str(project))
            self.routes.addItems([t.name for t in self.targets])
        except Exception as exc:
            self.checks.setText(str(exc))
            self.status.setText('プロジェクトのデータ構成を確認してください。')
            self.mission_state.setText('CHECK  /  データ確認が必要')

    def selection(self, index):
        if 0 <= index < len(self.targets):
            self.reset_results()
            target = self.targets[index]
            self.status.setText(f'トリップ数：{len(target.files):,}\n' + ('⚠ '+target.warning if target.warning else '✓ 第1.5スクリーニング由来\n状態：分析可能\n実行時にCSV・Gateを検証'))
            self.run_button.setEnabled(True)
            self.open_button.setEnabled(False)
            self.mission_state.setText('REFERENCE  /  参考表示' if target.warning else 'READY  /  分析準備完了')

    def start(self):
        self.reset_results()
        self._started_at = monotonic()
        self.timer.start()
        self.mission_state.setText('RUNNING  /  分析中')
        self.result.setPlainText('トリップを読み込み、交通分類と経路を集計しています。')
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
        self.timer.stop()
        self.update_elapsed()
        self.run_button.setEnabled(True)
        self.choose.setEnabled(True)
        self.routes.setEnabled(True)

    def failed(self, message):
        self.mission_state.setText('ERROR  /  分析エラー')
        self.result.setPlainText('分析エラー：' + message)

    def finished(self, result):
        self.report = result['report']
        n = result['counts']['ALL']
        self.mission_state.setText('COMPLETE  /  分析完了' if result['official'] else 'REFERENCE  /  正式値無効')
        self.metrics['ALL'].setText(f'{n:,}')
        self.metrics['THROUGH'].setText(f"{result['counts']['THROUGH']:,}")
        self.metrics['RATE'].setText(f"{result['through_rate']:.1%}" if result['official'] else '無効')
        self.update_progress(n, n)
        rows = ''.join(f'<tr><td>{escape(label)}</td><td align="right">{result["counts"][key]:,}</td><td align="right">{result["counts"][key]/n:.1%}</td></tr>'
                       if n else f'<tr><td>{escape(label)}</td><td>0</td><td>—</td></tr>'
                       for key, label in LABELS.items() if key != 'ALL')
        od = ''.join(f'<tr><td>{escape(r["start_gate"])} → {escape(r["end_gate"])}</td><td align="right">{r["trip_count"]:,}</td></tr>' for r in result['ranking'][:20])
        warnings = '<br>'.join(escape(w) for w in result['warnings'])
        self.result.setHtml(f'''<h3>{escape(self.routes.currentText())}</h3>
            <table width="100%" cellspacing="0" cellpadding="7"><tr><th align="left">交通分類</th><th align="right">トリップ数</th><th align="right">割合</th></tr>{rows}</table>
            <h3>主要通過OD</h3><table width="100%" cellpadding="7">{od or '<tr><td>該当するGate間ODはありません。</td></tr>'}</table>
            <p style="color:#f0c674">{warnings}</p>''')
        self.open_button.setEnabled(True)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore()
        else:
            event.accept()

def launch(engine, project=None):
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont('Meiryo UI', 10))
    window = RouteWindow(engine, project)
    window.show()
    app.exec()
