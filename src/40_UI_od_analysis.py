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

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLineEdit, QPushButton, QLabel, QFileDialog, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QDateEdit, QMessageBox, QProgressBar, QScrollArea, QSplitter, QDialog,
    QDialogButtonBox, QListWidget, QListWidgetItem)
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


class MapPanel(QWidget):
    def __init__(self, zonal, owner):
        super().__init__()
        self.owner, self.zonal, self.ready = owner, zonal, False
        self.closed = False
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.side = QComboBox(); self.side.addItems(['起点 / 発生', '終点 / 集中'])
        self.palette = QComboBox(); self.palette.addItems(['暖色', '寒色', '緑'])
        self.opacity = QDoubleSpinBox(); self.opacity.setRange(.05, 1); self.opacity.setSingleStep(.05); self.opacity.setValue(.75)
        self.maximum = QDoubleSpinBox(); self.maximum.setRange(0, 1e9); self.maximum.setSpecialValueText('自動')
        for name, widget in [('対象', self.side), ('配色', self.palette), ('不透明度', self.opacity), ('上限/日', self.maximum)]:
            controls.addWidget(QLabel(name)); controls.addWidget(widget)
        controls.addStretch()
        self.save = QPushButton('JPEG画像を保存'); self.save.setEnabled(False); self.save.clicked.connect(self.save_jpeg)
        controls.addWidget(self.save); layout.addLayout(controls)
        extra = QHBoxLayout()
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
        extra.addStretch(); layout.addLayout(extra)
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
                      opacity=self.opacity.value(), max=self.maximum.value())
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

    def update_map(self, *_):
        if self.ready:
            self.web.page().runJavaScript('window.updateOD(' + json.dumps(self.settings(), ensure_ascii=False) + ')')

    def save_jpeg(self):
        if not self.ready or not self.owner.output: return
        self.save.setEnabled(False)
        QTimer.singleShot(250, self.capture)

    def capture(self):
        prefix = 'ODzoneheatmap' if self.zonal else 'ODheatmap'
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
            path.with_suffix('.json').write_text(json.dumps(dict(settings=self.settings(), target_dates=self.owner.result['dates'],
                image_size=[self.web.width(), self.web.height()]), ensure_ascii=False, indent=2), encoding='utf-8')
            self.owner.statusBar().showMessage(f'保存しました: {path}')
        except Exception as exc: QMessageBox.critical(self, '画像保存', str(exc))
        finally: self.save.setEnabled(self.ready)


class ODWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.records, self.result, self.output, self.worker = [], None, None, None
        self.excluded_dates = set()
        self.setWindowTitle('40 OD分析 | ETC2.0アナライザー'); self.setWindowIcon(QIcon(str(LOGO))); self.resize(1460, 900)
        root = QWidget(); self.setCentralWidget(root); main = QVBoxLayout(root)
        header = QHBoxLayout()
        logo = QLabel(); logo.setPixmap(QPixmap(str(LOGO)).scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)); header.addWidget(logo)
        title = QLabel('OD分析\n様式1-3の起終点から、交通のつながりと分布を読み解く'); title.setObjectName('title'); header.addWidget(title, 1)
        self.open_output = QPushButton('出力フォルダを開く'); self.open_output.setEnabled(False); self.open_output.clicked.connect(lambda: os.startfile(str(self.output)))
        header.addWidget(self.open_output); main.addLayout(header)
        splitter = QSplitter(); main.addWidget(splitter, 1)
        sidebar = QWidget(); side_layout = QVBoxLayout(sidebar); side_layout.setContentsMargins(0, 0, 0, 0); splitter.addWidget(sidebar)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(365)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setup = QWidget(); left = QVBoxLayout(self.setup); scroll.setWidget(self.setup); side_layout.addWidget(scroll, 1)
        left.addWidget(QLabel('01  プロジェクト・入力'))
        self.project = self.path_field(left, 'プロジェクト')
        self.input = self.path_field(left, '第1 / 第1.5 / 第2スクリーニング')
        self.zipdir = self.path_field(left, '様式1-3 ZIPフォルダ')
        self.create = QPushButton('様式1-3参照ODリストを作成'); self.create.clicked.connect(self.extract); left.addWidget(self.create)
        self.load_button = QPushButton('既存のODリストを読み込む'); self.load_button.clicked.connect(self.load_files); left.addWidget(self.load_button)
        self.summary = QLabel('元トリップ単位で重複を除去します。\n第1.5の区域内起終点ではなく、\n様式1-3の起終点を使用します。'); self.summary.setWordWrap(True); left.addWidget(self.summary)
        left.addSpacing(18); left.addWidget(QLabel('02  対象日・ゾーニング'))
        form = QFormLayout(); left.addLayout(form)
        self.start = QDateEdit(QDate.currentDate()); self.end = QDateEdit(QDate.currentDate())
        for field in (self.start, self.end): field.setCalendarPopup(True); field.setDisplayFormat('yyyy/MM/dd')
        form.addRow('開始日', self.start); form.addRow('終了日', self.end)
        for field in (self.start, self.end): field.setMaximumWidth(210)
        days = QHBoxLayout(); self.weekdays = []
        for name in '月火水木金土日':
            box = QCheckBox(name); box.setChecked(True); days.addWidget(box); self.weekdays.append(box); box.toggled.connect(self.day_count)
        left.addLayout(days)
        self.day_label = QLabel(); self.day_label.setWordWrap(True); left.addWidget(self.day_label)
        self.start.dateChanged.connect(self.day_count); self.end.dateChanged.connect(self.day_count)
        specific = QPushButton('対象日を個別に選択'); specific.clicked.connect(self.choose_dates); left.addWidget(specific)
        hint = QLabel('ゾーン: 12_ゾーニングデータ\nゼロ件の日も分母に含めます。\n欠損日は「対象日を個別に選択」で除外。'); hint.setWordWrap(True); left.addWidget(hint)
        self.run = QPushButton('集計してマップを表示'); self.run.clicked.connect(self.analyze); side_layout.addWidget(self.run); left.addStretch()
        self.tabs = QTabWidget(); splitter.addWidget(self.tabs); splitter.setSizes([375, 1085])
        table_panel = QWidget(); tl = QVBoxLayout(table_panel)
        self.result_label = QLabel('ODリストを作成・読込後、対象日を確認して集計してください。'); self.result_label.setWordWrap(True); tl.addWidget(self.result_label)
        self.unit = QComboBox(); self.unit.addItems(['日平均（トリップ/日）', '全期間合計（トリップ）']); self.unit.currentIndexChanged.connect(self.populate_table); tl.addWidget(self.unit)
        self.table = QTableWidget(); self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.setAlternatingRowColors(True); tl.addWidget(self.table)
        self.tabs.addTab(table_panel, 'ODマトリクス')
        self.heat = MapPanel(False, self); self.zone = MapPanel(True, self)
        self.tabs.addTab(self.heat, 'ODヒートマップ'); self.tabs.addTab(self.zone, 'ゾーン別発生・集中マップ')
        bottom = QHBoxLayout(); self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.cancel = QPushButton('中止'); self.cancel.setEnabled(False); self.cancel.clicked.connect(self.stop)
        bottom.addWidget(self.progress, 1); bottom.addWidget(self.cancel); main.addLayout(bottom)
        self.setStyleSheet('''QMainWindow,QWidget{background:#101e2b;color:#e3edf5;font:13px "Yu Gothic UI";}
        QLabel#title{font-size:21px;font-weight:600;color:#88dfed;padding:8px;}
        QLineEdit,QDateEdit,QSpinBox,QDoubleSpinBox,QComboBox{background:#1b3041;border:1px solid #395369;border-radius:5px;padding:5px;}
        QPushButton{background:#20445b;border:1px solid #3a6e85;border-radius:6px;padding:9px;} QPushButton:hover{background:#2b6076;} QPushButton:disabled{color:#798c9b;background:#182c3c;}
        QTabBar::tab{padding:12px;background:#1b3041;} QTabBar::tab:selected{background:#286176;color:white;}
        QTableWidget{background:#f5f8fb;color:#162f43;alternate-background-color:#e7eff5;gridline-color:#cfdae2;}
        QHeaderView::section{background:#23465d;color:white;padding:8px;border:0;} QProgressBar{max-height:9px;border:0;background:#21394b;} QProgressBar::chunk{background:#59cee0;}''')
        saved = get_project()
        if saved: self.project.setText(str(saved))
        self.day_count()

    def path_field(self, layout, label):
        layout.addWidget(QLabel(label)); row = QHBoxLayout(); field = QLineEdit(); button = QPushButton('…'); button.setFixedWidth(38)
        def choose():
            path = QFileDialog.getExistingDirectory(self, label, field.text())
            if path: field.setText(path)
        button.clicked.connect(choose); row.addWidget(field); row.addWidget(button); layout.addLayout(row); return field

    def dates(self):
        dates = self.candidate_dates()
        dates = [d for d in dates if d not in self.excluded_dates]
        if not dates: raise ValueError('対象日がありません。')
        return dates

    def candidate_dates(self):
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
        self.setup.setEnabled(False); self.run.setEnabled(False); self.cancel.setEnabled(True); self.progress.setRange(0, 0)
        self.worker = Worker(function); self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.done.connect(callback); self.worker.failed.connect(self.failure); self.worker.finished.connect(self.finished); self.worker.start()

    def finished(self):
        self.setup.setEnabled(True); self.run.setEnabled(True); self.cancel.setEnabled(False); self.progress.setRange(0, 1); self.progress.setValue(1)

    def failure(self, message):
        self.statusBar().showMessage(message); QMessageBox.warning(self, 'OD分析', message)

    def stop(self):
        if self.worker: self.worker.requestInterruption(); self.statusBar().showMessage('中止処理中…')

    def extract(self):
        try:
            project = self.project_path(); source, zips = Path(self.input.text()), Path(self.zipdir.text())
            if not self.input.text().strip() or not self.zipdir.text().strip() or not source.is_dir() or not zips.is_dir(): raise ValueError('入力フォルダを選択してください。')
            output = project / '40_OD分析' / ('od_list_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.csv')
            def work(progress, cancel):
                path = engine.extract(source, zips, output, progress, cancel)
                records, duplicates = engine.read_od([path], progress, cancel)
                return records, duplicates, [str(path)]
            self.launch(work, self.loaded_records)
        except Exception as exc: self.failure(str(exc))

    def load_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '様式1-3参照ODリスト', self.project.text(), 'CSV (*.csv)')
        if paths: self.launch(lambda progress, cancel: (*engine.read_od(paths, progress, cancel), paths), self.loaded_records)

    def loaded_records(self, value):
        self.records, duplicates, self.sources = value; dates = sorted({r['operation_date'] for r in self.records})
        self.excluded_dates = set()
        self.result = None; self.output = None; self.table.setRowCount(0); self.table.setColumnCount(0); self.open_output.setEnabled(False)
        self.result_label.setText('新しいODリストを読み込みました。対象日を確認して再集計してください。')
        for panel in (self.heat, self.zone):
            panel.ready_timer.stop(); panel.ready = False; panel.save.setEnabled(False); panel.web.setHtml('<html><body>対象日を確認して集計してください。</body></html>')
        self.start.setDate(QDate.fromString(dates[0], 'yyyyMMdd')); self.end.setDate(QDate.fromString(dates[-1], 'yyyyMMdd'))
        self.summary.setText(f'読込: {len(self.records):,} 元トリップ\n重複除去: {duplicates:,} 件\n対象日を確認して集計してください。')
        self.statusBar().showMessage('ODリスト読込完了。期間・曜日を確認してください。')

    def analyze(self):
        try:
            project = self.project_path(); dates = self.dates()
            if not self.records: raise ValueError('先にODリストを作成または読み込んでください。')
            records, sources = self.records, self.sources
            output = project / '40_OD分析' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            def work(progress, cancel):
                zones = engine.load_zones(project / '12_ゾーニングデータ')
                result = engine.analyze(records, zones, dates, progress, cancel)
                progress('集計表・地図を保存しています…'); engine.check_cancel(cancel); engine.export(result, output)
                (output / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding='utf-8')
                for name, zonal in [('heatmap.html', False), ('zones.html', True)]:
                    (output / name).write_text(map_html(result, zonal), encoding='utf-8')
                return result, output
            self.launch(work, self.show_result)
        except Exception as exc: self.failure(str(exc))

    def show_result(self, value):
        self.result, self.output = value
        excluded = ' / '.join(f'{k}: {v:,}' for k, v in self.result['excluded'].items()) or 'なし'
        self.result_label.setText(f"対象 {self.result['days']} 日 ｜ 採用 {len(self.result['points']):,} トリップ ｜ 除外: {excluded}\n出力: {self.output}")
        self.populate_table(); self.heat.load(self.output / 'heatmap.html'); self.zone.load(self.output / 'zones.html')
        self.open_output.setEnabled(True); self.statusBar().showMessage('集計完了。地図の表示を調整してJPEG保存できます。')

    def populate_table(self, *_):
        if not self.result: return
        r = self.result; labels = r['labels']; divisor = r['days'] if self.unit.currentIndex() == 0 else 1
        self.table.setRowCount(len(labels) + 1); self.table.setColumnCount(len(labels) + 1)
        self.table.setHorizontalHeaderLabels(labels + ['合計']); self.table.setVerticalHeaderLabels(labels + ['合計'])
        for i in range(len(labels) + 1):
            for j in range(len(labels) + 1):
                value = (r['matrix'][labels[i], labels[j]] if i < len(labels) and j < len(labels) else r['origins'][labels[i]] if i < len(labels) else r['destinations'][labels[j]] if j < len(labels) else len(r['points']))
                item = QTableWidgetItem(f'{value / divisor:,.2f}' if self.unit.currentIndex() == 0 else f'{value:,}')
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); self.table.setItem(i, j, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning(): self.stop(); event.ignore(); return
        for panel in (self.heat, self.zone):
            panel.closed = True; panel.ready_timer.stop(); panel.web.stop(); panel.web.setUrl(QUrl('about:blank'))
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description='ODリスト・マトリクス・ヒートマップ統合UI'); parser.parse_args()
    app = QApplication(sys.argv); window = ODWindow(); window.show(); sys.exit(app.exec())


if __name__ == '__main__': main()
