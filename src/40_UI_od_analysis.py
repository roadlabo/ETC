"""Unified OD workspace: original trip lookup, matrices and exportable maps."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Embedded Python uses python311._pth and omits the script directory.
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal, QUrl, QTimer, QSize, QRect
from PyQt6.QtGui import QIcon, QPixmap, QColor
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLineEdit, QPushButton, QLabel, QFileDialog, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QDateEdit, QMessageBox, QProgressBar, QScrollArea, QSplitter, QDialog,
    QDialogButtonBox, QListWidget, QListWidgetItem, QButtonGroup)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings

from common import od_analysis as engine
from common.od_map import map_html
from common.project_settings import get_project, set_project

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / 'src/assets/logos/logo_40_od_analysis.png'


class Worker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.done.emit(self.function(self.progress.emit, self.isInterruptionRequested))
        except Exception as exc:
            self.failed.emit(str(exc))


class BlankDateEdit(QDateEdit):
    EMPTY = QDate(1752, 9, 14)

    def __init__(self):
        super().__init__()
        self.setMinimumDate(self.EMPTY)
        self.setSpecialValueText(' ')
        self.setDate(self.EMPTY)

    def clear(self):
        self.setDate(self.EMPTY)


class MatrixHeader(QHeaderView):
    """Rotated destination names keep matrix columns independent of name length."""
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setDefaultSectionSize(72); self.setMinimumSectionSize(54)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 170)

    def paintSection(self, painter, rect, index):
        label = str(self.model().headerData(index, self.orientation()) or '')
        painter.save()
        painter.fillRect(rect, QColor('#76542f' if label.startswith('ゲート ') else '#23465d'))
        painter.setPen(QColor('#e3edf5'))
        painter.translate(rect.left(), rect.bottom()); painter.rotate(-90)
        text = self.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, rect.height() - 16)
        painter.drawText(QRect(8, 0, rect.height()-16, rect.width()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class ChoiceButtons(QWidget):
    currentIndexChanged = pyqtSignal(int)

    def __init__(self, labels):
        super().__init__()
        self.group = QButtonGroup(self); self.buttons = []
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(3)
        for i, label in enumerate(labels):
            button = QPushButton(label); button.setCheckable(True)
            self.group.addButton(button, i); self.buttons.append(button); layout.addWidget(button)
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self.currentIndexChanged.emit)

    def currentIndex(self):
        return self.group.checkedId()

    def currentText(self):
        return self.group.checkedButton().text()

    def setCurrentIndex(self, index):
        if index != self.currentIndex():
            self.buttons[index].setChecked(True); self.currentIndexChanged.emit(index)


class MapPanel(QWidget):
    def __init__(self, zonal, owner):
        super().__init__()
        self.owner, self.zonal, self.ready = owner, zonal, False
        self.closed = False
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.side = ChoiceButtons(['起点 / 発生', '終点 / 集中'])
        self.palette = ChoiceButtons(['暖色', '寒色', '緑', '透明→青→赤'])
        choices = QHBoxLayout()
        for name, widget in [('対象', self.side), ('配色', self.palette)]:
            choices.addWidget(QLabel(name)); choices.addWidget(widget)
        choices.addStretch(); layout.addLayout(choices)
        self.opacity = QDoubleSpinBox(); self.opacity.setRange(.05, 1); self.opacity.setSingleStep(.05); self.opacity.setValue(.75)
        self.maximum = QDoubleSpinBox(); self.maximum.setRange(0, 1e9); self.maximum.setSpecialValueText('自動')
        for name, widget in [('不透明度', self.opacity), ('上限/日', self.maximum)]:
            controls.addWidget(QLabel(name)); controls.addWidget(widget)
        self.save = QPushButton('JPEG画像を保存'); self.save.setEnabled(False); self.save.clicked.connect(self.save_jpeg)
        choices.addWidget(self.save)
        extra = controls
        self.gate_labels = QCheckBox('ゲート名・日平均を表示'); self.gate_labels.setChecked(True)
        self.gate_labels.toggled.connect(self.update_map)
        if zonal:
            self.log = QCheckBox('対数配色'); self.labels = QCheckBox('ゾーン名・日平均を表示'); self.labels.setChecked(True)
            extra.addWidget(self.log); extra.addWidget(self.labels)
            self.log.toggled.connect(self.update_map); self.labels.toggled.connect(self.update_map)
        else:
            self.radius = QSpinBox(); self.radius.setRange(2, 150); self.radius.setValue(24)
            self.blur = QDoubleSpinBox(); self.blur.setRange(.05, 1); self.blur.setSingleStep(.05); self.blur.setValue(.65)
            self.gain = QDoubleSpinBox(); self.gain.setRange(.05, 100); self.gain.setSingleStep(.25); self.gain.setValue(1)
            for name, widget in [('半径(px)', self.radius), ('ぼかし', self.blur), ('表示倍率', self.gain)]:
                extra.addWidget(QLabel(name)); extra.addWidget(widget); widget.valueChanged.connect(self.update_map)
        extra.addWidget(self.gate_labels); extra.addStretch(); layout.addLayout(controls)
        for widget in self.findChildren(QDoubleSpinBox) + self.findChildren(QSpinBox):
            widget.setFixedWidth(112 if widget is self.maximum else 88)
        self.web = QWebEngineView()
        self.web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.web.loadFinished.connect(self.loaded); layout.addWidget(self.web, 1)
        self.ready_timer = QTimer(self)
        self.ready_timer.setInterval(250)
        self.ready_timer.timeout.connect(lambda: self.web.page().runJavaScript('window.odReady === true', self.verify_ready))
        for widget in (self.side, self.palette): widget.currentIndexChanged.connect(self.update_map)
        for widget in (self.opacity, self.maximum): widget.valueChanged.connect(self.update_map)

    def settings(self):
        values = dict(side='o' if self.side.currentIndex() == 0 else 'd', palette=self.palette.currentText(),
                      opacity=self.opacity.value(), max=self.maximum.value(), gateLabels=self.gate_labels.isChecked())
        if self.zonal: values.update(log=self.log.isChecked(), labels=self.labels.isChecked())
        else: values.update(radius=self.radius.value(), blur=self.blur.value(), gain=self.gain.value())
        return values

    def load(self, path):
        self.ready = False; self.save.setEnabled(False)
        self.web.load(QUrl.fromLocalFile(str(path)))
        self.ready_timer.start()

    def loaded(self, ok):
        if self.closed: return
        if not ok:
            self.ready_timer.stop()
            self.owner.statusBar().showMessage('地図の読込に失敗しました。再集計してください。'); return
        self.web.page().runJavaScript('window.odReady === true', self.verify_ready)

    def verify_ready(self, ready):
        if self.closed: return
        self.ready = bool(ready); self.save.setEnabled(self.ready)
        if self.ready:
            self.ready_timer.stop(); self.update_map()
            if self.isVisible(): self.web.page().runJavaScript('window.resizeOD && window.resizeOD()')

    def showEvent(self, event):
        super().showEvent(event)
        if self.ready:
            QTimer.singleShot(50, lambda: self.web.page().runJavaScript('window.resizeOD && window.resizeOD()') if not self.closed else None)

    def update_map(self, *_):
        if self.ready:
            self.web.page().runJavaScript('window.updateOD(' + json.dumps(self.settings(), ensure_ascii=False) + ')')

    def save_jpeg(self):
        if not self.ready or not self.owner.output: return
        self.save.setEnabled(False)
        self.pending_result = self.owner.result
        QTimer.singleShot(250, self.capture)

    def capture(self):
        if self.closed or not self.ready or self.pending_result is not self.owner.result:
            return
        prefix = engine.prefix(self.owner.result['method']) + ('ODzoneheatmap' if self.zonal else 'ODheatmap')
        try:
            folder = self.owner.output
            for i in range(1, 1000000):
                path = folder / f'{prefix}({i}).jpg'
                try:
                    with path.open('xb'): pass
                    break
                except FileExistsError: continue
            else: raise ValueError('保存番号の上限です。')
            if not self.web.grab().save(str(path), 'JPEG', 95):
                path.unlink(missing_ok=True); raise OSError('JPEGを書き込めませんでした。')
            path.with_suffix('.json').write_text(json.dumps(dict(od_method=self.owner.result['method'], run_id=self.owner.result.get('run_id',''), settings=self.settings(), target_dates=self.owner.result['dates'],
                image_size=[self.web.width(), self.web.height()]), ensure_ascii=False, indent=2), encoding='utf-8')
            self.owner.statusBar().showMessage(f'保存しました: {path}')
        except Exception as exc: QMessageBox.critical(self, '画像保存', str(exc))
        finally: self.save.setEnabled(self.ready)


class ODWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.records, self.result, self.output, self.worker = [], None, None, None
        self.sources = []
        self.method, self.method_states = 'style13', {}
        self.excluded_dates = set()
        self.date_workers = []; self.date_generation = 0
        self.date_timer = QTimer(self); self.date_timer.setSingleShot(True); self.date_timer.setInterval(350)
        self.date_timer.timeout.connect(self.scan_dates)
        self.setWindowTitle('40 OD分析 | ETC2.0アナライザー'); self.setWindowIcon(QIcon(str(LOGO))); self.resize(1460, 900)
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        root = QWidget(); self.setCentralWidget(root); main = QVBoxLayout(root)
        header = QHBoxLayout()
        logo = QLabel(); logo.setPixmap(QPixmap(str(LOGO)).scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)); header.addWidget(logo)
        title = QLabel('OD分析\n2つの起終点定義で、交通のつながりと分布を読み解く'); title.setObjectName('title'); header.addWidget(title, 1)
        self.open_output = QPushButton('出力フォルダを開く'); self.open_output.setEnabled(False); self.open_output.clicked.connect(lambda: os.startfile(str(self.output)))
        header.addWidget(self.open_output); main.addLayout(header)
        splitter = QSplitter(); main.addWidget(splitter, 1)
        sidebar = QWidget(); side_layout = QVBoxLayout(sidebar); side_layout.setContentsMargins(0, 0, 0, 0); splitter.addWidget(sidebar)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(365)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setup = QWidget(); left = QVBoxLayout(self.setup); scroll.setWidget(self.setup); side_layout.addWidget(scroll, 1)
        left.addWidget(QLabel('OD方式'))
        toggle = QHBoxLayout(); self.method_group = QButtonGroup(self); self.method_buttons = {}
        for method, text in engine.METHODS.items():
            button = QPushButton(text); button.setCheckable(True); button.setChecked(method == self.method)
            self.method_group.addButton(button); self.method_buttons[method] = button
            button.clicked.connect(lambda checked, m=method: self.change_method(m)); toggle.addWidget(button)
        left.addLayout(toggle)
        self.method_hint = QLabel(); self.method_hint.setWordWrap(True); left.addWidget(self.method_hint)
        left.addWidget(QLabel('01  プロジェクト・入力'))
        self.project = self.path_field(left, 'プロジェクト')
        self.input = self.path_field(left, '第1 / 第1.5 / 第2スクリーニング')
        self.zipdir = self.path_field(left, '様式1-3 ZIPフォルダ')
        actions = QHBoxLayout(); left.addLayout(actions)
        self.create = QPushButton(); self.create.clicked.connect(lambda: self.extract()); actions.addWidget(self.create, 1)
        self.load_button = QPushButton('既存のODリストを\n読み込む'); self.load_button.clicked.connect(self.load_files); actions.addWidget(self.load_button, 1)
        self.summary = QLabel('ODリストを作成または読み込んでください。'); self.summary.setWordWrap(True); left.addWidget(self.summary)
        self.options = QWidget(); options = QVBoxLayout(self.options); options.setContentsMargins(0, 0, 0, 0); left.addWidget(self.options)
        options.addSpacing(18); options.addWidget(QLabel('02  対象日・ゾーニング'))
        form = QFormLayout(); options.addLayout(form)
        self.start = BlankDateEdit(); self.end = BlankDateEdit()
        for field in (self.start, self.end): field.setCalendarPopup(True); field.setDisplayFormat('yyyy/MM/dd')
        form.addRow('開始日', self.start); form.addRow('終了日', self.end)
        for field in (self.start, self.end): field.setMaximumWidth(210)
        days = QHBoxLayout(); self.weekdays = []
        for name in '月火水木金土日':
            box = QCheckBox(name); box.setChecked(True); days.addWidget(box); self.weekdays.append(box); box.toggled.connect(self.day_count)
        options.addLayout(days)
        self.day_label = QLabel(); self.day_label.setWordWrap(True); options.addWidget(self.day_label)
        self.start.dateChanged.connect(self.day_count); self.end.dateChanged.connect(self.day_count)
        specific = QPushButton('対象日を個別に選択'); specific.clicked.connect(self.choose_dates); options.addWidget(specific)
        hint = QLabel('ゾーン: 12_ゾーニングデータ\nゼロ件の日も分母に含めます。\n欠損日は「対象日を個別に選択」で除外。'); hint.setWordWrap(True); options.addWidget(hint)
        self.run = QPushButton('集計してマップを表示'); self.run.clicked.connect(self.analyze); side_layout.addWidget(self.run); left.addStretch()
        self.tabs = QTabWidget(); splitter.addWidget(self.tabs); splitter.setSizes([375, 1085])
        table_panel = QWidget(); tl = QVBoxLayout(table_panel)
        self.result_label = QLabel('ODリストを作成・読込後、対象日を確認して集計してください。'); self.result_label.setWordWrap(True); tl.addWidget(self.result_label)
        self.unit = QComboBox(); self.unit.addItems(['日平均（トリップ/日）', '全期間合計（トリップ）']); self.unit.currentIndexChanged.connect(self.populate_table); tl.addWidget(self.unit)
        self.matrix_hint = QLabel('行 ↓ O：起点・発生　／　列 → D：終点・集中\nゾーン・ゲートを統合（内々・内外・外内・外外）｜ 背景色：小＝淡い、大＝濃い（合計は別色）')
        self.matrix_hint.setWordWrap(True); tl.addWidget(self.matrix_hint)
        self.table = QTableWidget(); self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.setAlternatingRowColors(True); tl.addWidget(self.table)
        self.table.setHorizontalHeader(MatrixHeader(self.table)); self.table.setAlternatingRowColors(False)
        self.tabs.addTab(table_panel, 'ODマトリクス')
        self.heat = MapPanel(False, self); self.zone = MapPanel(True, self)
        self.tabs.addTab(self.heat, 'ODヒートマップ'); self.tabs.addTab(self.zone, 'ゾーン別発生・集中マップ')
        bottom = QHBoxLayout(); self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.cancel = QPushButton('中止'); self.cancel.setEnabled(False); self.cancel.clicked.connect(self.stop)
        bottom.addWidget(self.progress, 1); bottom.addWidget(self.cancel); main.addLayout(bottom)
        self.setStyleSheet('''QMainWindow,QWidget{background:#101e2b;color:#e3edf5;font:13px "Yu Gothic UI";}
        QLabel#title{font-size:21px;font-weight:600;color:#88dfed;padding:8px;}
        QLabel:disabled,QCheckBox:disabled,QDateEdit:disabled{color:#718593;}
        QLineEdit,QDateEdit,QSpinBox,QDoubleSpinBox,QComboBox{background:#1b3041;border:1px solid #395369;border-radius:5px;padding:5px;}
        QPushButton{background:#20445b;border:1px solid #3a6e85;border-radius:6px;padding:9px;} QPushButton:hover{background:#2b6076;} QPushButton:disabled{color:#798c9b;background:#182c3c;}
        QPushButton:checked{background:#247489;border:2px solid #85e4ed;color:white;font-weight:bold;}
        QTabBar::tab{padding:12px;background:#1b3041;} QTabBar::tab:selected{background:#286176;color:white;}
        QTableWidget{background:#f5f8fb;color:#162f43;alternate-background-color:#e7eff5;gridline-color:#cfdae2;}
        QHeaderView::section{background:#23465d;color:white;padding:8px;border:0;} QProgressBar{max-height:9px;border:0;background:#21394b;} QProgressBar::chunk{background:#59cee0;}''')
        arrows = (SRC_DIR / 'assets').as_posix()
        self.setStyleSheet(self.styleSheet() + '''
        QSpinBox,QDoubleSpinBox{padding:3px 26px 3px 6px;min-height:28px;}
        QSpinBox::up-button,QDoubleSpinBox::up-button{ subcontrol-origin:border;subcontrol-position:top right;width:23px;height:17px;background:#365d73;border-top-right-radius:4px;}
        QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;width:23px;height:17px;background:#365d73;border-bottom-right-radius:4px;}
        QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover,QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:#48849a;}
        QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{image:url("''' + arrows + '''/od_up.svg");width:10px;height:7px;}
        QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{image:url("''' + arrows + '''/od_down.svg");width:10px;height:7px;}
        ''')
        saved = get_project()
        if saved: self.project.setText(str(saved))
        self.refresh_method_ui()
        self.day_count()
        self.input.textChanged.connect(self.input_changed)
        self.update_steps()

    def input_changed(self):
        self.date_generation += 1
        for worker in self.date_workers: worker.requestInterruption()
        self.start.clear(); self.end.clear(); self.excluded_dates.clear()
        self.records = []; self.sources = []; self.method_states.clear(); self.clear_results()
        self.summary.setText('ODリストを作成または読み込んでください。')
        self.date_timer.start()

    def scan_dates(self):
        source = self.input.text().strip(); generation = self.date_generation
        if not source or not Path(source).is_dir(): return
        worker = Worker(lambda progress, cancel: engine.screening_dates(source, progress, cancel))
        self.date_workers.append(worker)
        def done(value):
            if generation != self.date_generation: return
            self.start.setDate(QDate.fromString(value[0], 'yyyyMMdd')); self.end.setDate(QDate.fromString(value[1], 'yyyyMMdd'))
            self.statusBar().showMessage('スクリーニングフォルダの最初・最後の日付を設定しました。')
        worker.done.connect(done)
        worker.failed.connect(lambda message: self.statusBar().showMessage(message) if generation == self.date_generation else None)
        worker.finished.connect(lambda: self.date_workers.remove(worker))
        worker.start()

    def refresh_method_ui(self):
        trip = self.method == 'trip'
        self.create.setText(engine.METHODS[self.method] + 'リストを\n作成')
        self.zipdir.setEnabled(not trip)
        self.method_hint.setText('各トリップの最初行 → 最終行。\n第1.5の分割区間も個別に保持します。' if trip else
                                 '様式1-3の起点 → 終点。\n運行日・ID・番号で元トリップを照合します。')

    def save_method_state(self):
        if self.records:
            self.method_states[self.method] = dict(data=(self.records, self.duplicates, self.sources),
                result=(self.result, self.output) if self.result else None,
                dates=(self.start.date(), self.end.date(), [b.isChecked() for b in self.weekdays], set(self.excluded_dates)))

    def update_steps(self):
        ready = bool(self.records)
        self.options.setEnabled(ready)
        self.run.setEnabled(ready and self.setup.isEnabled())

    def clear_results(self):
        self.update_steps()
        self.result = None; self.output = None; self.table.setRowCount(0); self.table.setColumnCount(0); self.open_output.setEnabled(False)
        self.result_label.setText(engine.prefix(self.method) + ' 対象日を確認して集計してください。')
        for panel in (self.heat, self.zone):
            panel.ready_timer.stop(); panel.ready = False; panel.save.setEnabled(False)
            panel.web.setHtml('<html><body>ODリストを選び、集計してください。</body></html>')

    def change_method(self, method):
        if method == self.method: return
        current_dates = (self.start.date(), self.end.date(), [b.isChecked() for b in self.weekdays], set(self.excluded_dates))
        self.save_method_state()
        self.method = method; self.method_buttons[method].setChecked(True); self.refresh_method_ui()
        self.records, self.sources = [], []; self.clear_results()
        state = self.method_states.get(method)
        if state:
            self.loaded_records(state['data'])
            start, end, weekdays, excluded = state.get('dates', current_dates)
            self.start.setDate(start); self.end.setDate(end); self.excluded_dates = excluded
            for box, checked in zip(self.weekdays, weekdays): box.setChecked(checked)
            self.day_count()
            if state.get('result'): self.show_result(state['result'])
        else:
            self.summary.setText(engine.METHODS[method] + 'のODリストを作成または読み込んでください。')

    def path_field(self, layout, label):
        layout.addWidget(QLabel(label)); row = QHBoxLayout(); field = QLineEdit(); button = QPushButton('…'); button.setFixedWidth(38)
        field.browse_button = button
        def choose():
            initial = self.project.text().strip() if field is getattr(self, 'input', None) else field.text()
            path = QFileDialog.getExistingDirectory(self, label, initial)
            if path: field.setText(path)
        button.clicked.connect(choose); row.addWidget(field); row.addWidget(button); layout.addLayout(row); return field

    def dates(self):
        dates = self.candidate_dates()
        dates = [d for d in dates if d not in self.excluded_dates]
        if not dates: raise ValueError('対象日がありません。')
        return dates

    def candidate_dates(self):
        if self.start.date() == BlankDateEdit.EMPTY or self.end.date() == BlankDateEdit.EMPTY:
            raise ValueError('スクリーニングフォルダを指定すると対象日を設定します。')
        return engine.target_dates(self.start.date().toString('yyyyMMdd'), self.end.date().toString('yyyyMMdd'), {i for i, b in enumerate(self.weekdays) if b.isChecked()})

    def choose_dates(self):
        try: dates = self.candidate_dates()
        except ValueError as exc: self.failure(str(exc)); return
        dialog = QDialog(self); dialog.setWindowTitle('対象日を個別に選択'); dialog.resize(380, 520)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('欠損日など、分母に含めない日はチェックを外します。'))
        listing = QListWidget(); layout.addWidget(listing)
        for date in dates:
            parsed = QDate.fromString(date, 'yyyyMMdd')
            item = QListWidgetItem(parsed.toString('yyyy/MM/dd') + ' (' + '月火水木金土日'[parsed.dayOfWeek()-1] + ')')
            item.setData(Qt.ItemDataRole.UserRole, date)
            item.setCheckState(Qt.CheckState.Unchecked if date in self.excluded_dates else Qt.CheckState.Checked); listing.addItem(item)
        all_days = QPushButton('すべて選択'); all_days.clicked.connect(lambda: [listing.item(i).setCheckState(Qt.CheckState.Checked) for i in range(listing.count())]); layout.addWidget(all_days)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.excluded_dates = {listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count()) if listing.item(i).checkState() == Qt.CheckState.Unchecked}
            self.day_count()

    def day_count(self, *_):
        try: self.day_label.setText(f'対象日数: {len(self.dates())} 日\n日平均 = 採用トリップ数 ÷ 対象日数')
        except ValueError as exc: self.day_label.setText(str(exc))

    def project_path(self):
        if not self.project.text().strip(): raise ValueError('プロジェクトを選択してください。')
        return set_project(self.project.text().strip())

    def launch(self, function, callback):
        self.date_timer.stop(); self.date_generation += 1
        for worker in self.date_workers: worker.requestInterruption()
        self.setup.setEnabled(False); self.run.setEnabled(False); self.cancel.setEnabled(True); self.progress.setRange(0, 0)
        for panel in (self.heat, self.zone): panel.save.setEnabled(False)
        self.worker = Worker(function); self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.done.connect(callback); self.worker.failed.connect(self.failure); self.worker.finished.connect(self.finished); self.worker.start()

    def finished(self):
        self.setup.setEnabled(True); self.update_steps(); self.cancel.setEnabled(False); self.progress.setRange(0, 1); self.progress.setValue(1)
        for panel in (self.heat, self.zone): panel.save.setEnabled(panel.ready)

    def failure(self, message):
        self.statusBar().showMessage(message); QMessageBox.warning(self, 'OD分析', message)

    def stop(self):
        if self.worker: self.worker.requestInterruption(); self.statusBar().showMessage('中止処理中…')

    def extract(self):
        try:
            project = self.project_path(); source, zips = Path(self.input.text()), Path(self.zipdir.text())
            if not self.input.text().strip() or not source.is_dir(): raise ValueError('スクリーニング済みCSVフォルダを選択してください。')
            methods = [self.method]
            if 'style13' in methods and (not self.zipdir.text().strip() or not zips.is_dir()): raise ValueError('様式1-3 ZIPフォルダを選択してください。')
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            def work(progress, cancel):
                outputs = {}
                for method in methods:
                    output = project / '40_OD分析' / 'ODリスト' / stamp / (engine.prefix(method) + 'od_list.csv')
                    path = engine.extract(source, zips, output, progress, cancel) if method == 'style13' else engine.extract_trip(source, output, progress, cancel)
                    records, duplicates = engine.read_od([path], progress, cancel)
                    outputs[method] = (records, duplicates, [str(path)])
                return outputs
            self.launch(work, self.loaded_methods)
        except Exception as exc: self.failure(str(exc))

    def load_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'ODリスト（同じ方式のCSVを選択）', self.project.text(), 'CSV (*.csv)')
        if paths: self.launch(lambda progress, cancel: (*engine.read_od(paths, progress, cancel), paths), self.loaded_records)

    def loaded_records(self, value):
        method = engine.method_of(value[0])
        if method != self.method: self.change_method(method)
        self.records, self.duplicates, self.sources = value; dates = sorted({r['operation_date'] for r in self.records})
        self.excluded_dates = set()
        self.clear_results()
        self.start.setDate(QDate.fromString(dates[0], 'yyyyMMdd')); self.end.setDate(QDate.fromString(dates[-1], 'yyyyMMdd'))
        self.summary.setText(f'{engine.prefix(self.method)}\n読込: {len(self.records):,} トリップ\n重複除去: {self.duplicates:,} 件')
        self.statusBar().showMessage('ODリスト読込完了。期間・曜日を確認してください。')

    def loaded_methods(self, outputs):
        for method, data in outputs.items(): self.method_states[method] = dict(data=data, result=None)
        self.loaded_records(outputs[self.method])
        self.statusBar().showMessage('ODリスト作成完了: ' + ' / '.join(engine.METHODS[m] for m in outputs))

    def analyze(self):
        try:
            project = self.project_path(); dates = self.dates()
            if not self.records: raise ValueError('先にODリストを作成または読み込んでください。')
            records, sources = self.records, self.sources
            run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            output = project / '40_OD分析' / (engine.prefix(self.method) + run_id)
            def work(progress, cancel):
                zones = engine.load_zones(project / '12_ゾーニングデータ')
                result = engine.analyze(records, zones, dates, progress, cancel)
                result['run_id'] = run_id
                result['project_folder'] = str(project.resolve())
                progress('集計表・地図を保存しています…'); engine.check_cancel(cancel); engine.export(result, output)
                (output / engine.result_name(result, 'sources.json')).write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding='utf-8')
                for name, zonal in [('heatmap.html', False), ('zones.html', True)]:
                    (output / engine.result_name(result, name)).write_text(map_html(result, zonal), encoding='utf-8')
                return result, output
            self.launch(work, self.show_result)
        except Exception as exc: self.failure(str(exc))

    def show_result(self, value):
        self.result, self.output = value
        excluded = ' / '.join(f'{k}: {v:,}' for k, v in self.result['excluded'].items()) or 'なし'
        self.result_label.setText(f"{engine.prefix(self.result['method'])} 対象 {self.result['days']} 日 ｜ 採用 {len(self.result['points']):,} トリップ ｜ 除外: {excluded}\n出力: {self.output}")
        self.result_label.setText(self.result_label.text() + '\n' + ' ｜ '.join(f'{kind}: {sum(counts.values()):,}' for kind, counts in self.result['traffic'].items()))
        self.populate_table(); self.heat.load(self.output / engine.result_name(self.result, 'heatmap.html')); self.zone.load(self.output / engine.result_name(self.result, 'zones.html'))
        self.open_output.setEnabled(True); self.statusBar().showMessage('集計完了。地図の表示を調整してJPEG保存できます。')

    def populate_table(self, *_):
        if not self.result: return
        r = self.result; labels, matrix = engine.combined_matrix(r)
        divisor = r['days'] if self.unit.currentIndex() == 0 else 1
        origins = destinations = labels
        maximum = max(matrix.values(), default=1) or 1
        self.table.setRowCount(len(origins) + 1); self.table.setColumnCount(len(destinations) + 1)
        names = [engine.display_label(s) for s in labels] + ['合計']
        self.table.setHorizontalHeaderLabels(names); self.table.setVerticalHeaderLabels(names)
        for j, name in enumerate(names): self.table.horizontalHeaderItem(j).setToolTip('D：終点・集中\n' + name)
        for i, name in enumerate(names): self.table.verticalHeaderItem(i).setToolTip('O：起点・発生\n' + name)
        for i in range(len(origins) + 1):
            for j in range(len(destinations) + 1):
                value = (matrix[origins[i], destinations[j]] if i < len(origins) and j < len(destinations)
                         else sum(matrix[origins[i], b] for b in destinations) if i < len(origins)
                         else sum(matrix[a, destinations[j]] for a in origins) if j < len(destinations) else sum(matrix.values()))
                item = QTableWidgetItem(f'{value / divisor:,.2f}' if self.unit.currentIndex() == 0 else f'{value:,}')
                total = i == len(origins) or j == len(destinations)
                ratio = value / maximum if not total else 0
                item.setBackground(QColor('#d7e6f0') if total else QColor(
                    round(242+(32-242)*ratio), round(248+(118-248)*ratio), round(252+(155-252)*ratio)))
                item.setForeground(QColor('#ffffff' if ratio > .6 else '#163047'))
                item.setToolTip(f'O：{names[i]}\nD：{names[j]}\n{value / divisor:,.2f}' + (' トリップ/日' if divisor != 1 or self.unit.currentIndex() == 0 else ' トリップ'))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); self.table.setItem(i, j, item)
        width = min(115, max(68, self.table.fontMetrics().horizontalAdvance(f'{sum(matrix.values()) / divisor:,.2f}') + 18))
        for j in range(len(names)): self.table.setColumnWidth(j, width)

    def closeEvent(self, event):
        self.date_timer.stop()
        if any(w.isRunning() for w in self.date_workers):
            for worker in self.date_workers: worker.requestInterruption()
            QTimer.singleShot(100, self.close); event.ignore(); return
        if self.worker and self.worker.isRunning(): self.stop(); event.ignore(); return
        for panel in (self.heat, self.zone):
            panel.closed = True; panel.ready_timer.stop(); panel.web.stop(); panel.web.setUrl(QUrl('about:blank'))
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description='ODリスト・マトリクス・ヒートマップ統合UI')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv); window = ODWindow(); window.show()
    if args.smoke_test: QTimer.singleShot(500, window.close)
    sys.exit(app.exec())


if __name__ == '__main__': main()
