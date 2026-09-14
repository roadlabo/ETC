"""Native, single-screen ETC tool launcher (no browser required)."""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime

# The bundled Windows Python uses an isolated ._pth configuration.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import Qt, QRect, QSize, QTimer, QUrl, QPoint
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap, QPolygon
from PyQt6.QtWidgets import QApplication, QAbstractButton, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget, QSizePolicy

from common.launcher_catalog import TOOLS, OVERVIEW_URL
from common.project_settings import get_project, set_project
from PyQt6.QtWidgets import QFileDialog

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / 'src/assets/logos/logo_00_launcher.ico'


def launch_tool(tool, root=ROOT):
    """Start a catalogued batch without blocking the launcher or flashing a console."""
    if tool not in TOOLS:
        raise ValueError('登録されていないツールです。')
    target = root / 'bat' / tool.batch
    if not target.is_file():
        raise FileNotFoundError(f'バッチファイルがありません。\n{target}')
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    log = logs / f'launcher_{tool.number.replace("/", "_")}_{datetime.now():%Y%m%d_%H%M%S_%f}.log'
    env = os.environ.copy()
    env['ETC_LAUNCH_TARGET'] = str(target.resolve())
    env['ETC_LAUNCHER'] = '1'
    command = f'"{env.get("COMSPEC", "C:/Windows/System32/cmd.exe")}" /d /s /c ""%ETC_LAUNCH_TARGET%""'
    with log.open('wb') as output:
        process = subprocess.Popen(command, cwd=root, env=env, shell=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=output)
    return process, log


def create_shortcut(directory=None):
    """Use Windows' actual Desktop location, including redirected desktops."""
    env = os.environ.copy()
    env['ETC_LAUNCH_ROOT'] = str(ROOT)
    env['ETC_SHORTCUT_DIR'] = str(directory) if directory else ''
    script = r'''
$ErrorActionPreference = 'Stop'
$root = $env:ETC_LAUNCH_ROOT
$desktop = $env:ETC_SHORTCUT_DIR
if (-not $desktop) { $desktop = [Environment]::GetFolderPath('Desktop') }
if (-not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Desktop folder not found' }
$shell = New-Object -ComObject WScript.Shell
$target = Join-Path $root 'runtime\python\pythonw.exe'
$arguments = '"' + (Join-Path $root 'src\00_launcher.py') + '"'
$path = Join-Path $desktop 'ETCアナライザー.lnk'
$i = 2
while (Test-Path -LiteralPath $path) {
    $existing = $shell.CreateShortcut($path)
    if ($existing.TargetPath -eq $target -and $existing.Arguments -eq $arguments) { break }
    $path = Join-Path $desktop ("ETCアナライザー ($i).lnk")
    $i++
}
$link = $shell.CreateShortcut($path)
$link.TargetPath = $target
$link.Arguments = $arguments
$link.WorkingDirectory = $root
$link.IconLocation = (Join-Path $root 'src\assets\logos\logo_00_launcher.ico') + ',0'
$link.Description = 'ETCアナライザー：すべての分析ツールを起動'
$link.WindowStyle = 1
$link.Save()
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Write-Output $path
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


class ToolCard(QAbstractButton):
    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self.tool = tool
        self.logo = QPixmap(str(ROOT / 'src/assets/logos' / tool.logo)) if tool.logo else QPixmap()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f'{tool.number} {tool.title}。{tool.purpose}')
        self.setToolTip(f'{tool.purpose}\n{tool.features}\n起動: {tool.batch}')
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.missing = not (ROOT / 'bat' / tool.batch).is_file()
        self.running = False
        self.started_at = 0.0

    def sizeHint(self):
        return QSize(280, 180)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor('#55948e' if self.hasFocus() or self.underMouse() else '#d8dce1'))
        p.setBrush(QColor('#edf5f4' if self.isDown() else '#ffffff'))
        p.drawRoundedRect(QRect(1, 1, w-2, h-2), 10, 10)
        margin = 12
        compact = h < 185
        font = QFont('Yu Gothic UI')
        font.setPixelSize(12 if compact else 14)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor('#25313b'))
        title = f'{self.tool.number}  {self.tool.title}'
        title_rect = QRect(margin, 9, w-2*margin, 34)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, title)
        logo_h = 27 if compact else min(57, int(h * .25))
        logo_rect = QRect(margin, 43 if not compact else 31, w-2*margin, logo_h)
        if not self.logo.isNull():
            logo = self.logo.scaled(QSize(132, logo_h), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(logo_rect.x() + (logo_rect.width()-logo.width())//2, logo_rect.y(), logo)
        else:
            p.setPen(QColor('#238678'))
            font.setPixelSize(20)
            p.setFont(font)
            p.drawText(logo_rect.adjusted(24, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, 'ZONING')
            x, y = w//2-61, logo_rect.center().y()
            p.setBrush(QColor('#e2f1ed'))
            p.drawPolygon(QPolygon([QPoint(x-10,y-7), QPoint(x+3,y-12), QPoint(x+12,y), QPoint(x+5,y+11), QPoint(x-10,y+7)]))
        body_y = logo_rect.bottom() + 7
        body = QRect(margin, body_y, w-2*margin, h-body_y-10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#f3f4f6'))
        p.drawRoundedRect(body, 5, 5)
        text_rect = body.adjusted(8, 5, -8, -5)
        text = self.tool.purpose + '\n' + self.tool.features
        font.setBold(False)
        # Fit all explanatory text at each screen size instead of truncating it.
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        for size in range(13 if not compact else 12, 8, -1):
            font.setPixelSize(size)
            p.setFont(font)
            measured = p.fontMetrics().boundingRect(text_rect, int(flags), text)
            if measured.height() <= text_rect.height():
                break
        self.text_fits = measured.height() <= text_rect.height()
        self.text_pixel_size = size
        p.setPen(QColor('#53606c'))
        p.drawText(text_rect, flags, text)
        if self.running:
            elapsed = time.monotonic() - self.started_at
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#e1f2ee'))
            p.drawRoundedRect(QRect(margin, 34, w-2*margin, h-44), 6, 6)
            font.setPixelSize(16 if compact else 20)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor('#176c60'))
            message = ('起動中' + '・' * (int(elapsed * 3) % 4)) if elapsed < 8 else '起動要求を送信済み'
            p.drawText(QRect(margin, 38, w-2*margin, (h-44)//2), Qt.AlignmentFlag.AlignCenter, message)
            font.setPixelSize(11 if compact else 13)
            font.setBold(False)
            p.setFont(font)
            p.drawText(QRect(margin+5, 38+(h-44)//2, w-2*margin-10, (h-44)//2),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       'クリックは受け付けました。\n追加のクリックは不要です。' if elapsed < 8 else
                       'ツールのウィンドウをご確認ください。\n重複起動を抑止しています。')
        if self.missing:
            p.fillRect(self.rect().adjusted(2, 2, -2, -2), QColor(255, 255, 255, 210))
            p.setPen(QColor('#a13d36'))
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       f'{self.tool.number} {self.tool.title}\nバッチがありません')
        p.end()


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ETCアナライザー | ツール概要')
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1440, 900)
        self.setStyleSheet('QMainWindow, QWidget#surface {background:#f7f8fa;} QLabel {color:#303943;} QPushButton {background:white; border:1px solid #d8dce1; border-radius:6px; padding:7px 12px;} QPushButton:hover {border-color:#55948e;}')
        surface = QWidget()
        surface.setObjectName('surface')
        self.setCentralWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(18, 12, 18, 10)
        header = QHBoxLayout()
        title = QLabel('ETCアナライザー  /  ツール概要')
        title.setStyleSheet('font-size:20px; font-weight:700;')
        header.addWidget(title)
        header.addStretch()
        for label, action in [('ツール概要 Web', lambda: QDesktopServices.openUrl(QUrl(OVERVIEW_URL))),
                              ('フォルダを開く', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT)))),
                              ('デスクトップに配置', self.shortcut)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            header.addWidget(button)
        layout.addLayout(header)
        project_row = QHBoxLayout()
        self.project_label = QLabel()
        self.project_label.setWordWrap(True)
        project_row.addWidget(self.project_label, 1)
        project_button = QPushButton('プロジェクトフォルダを選択・変更')
        project_button.clicked.connect(self.choose_project)
        project_row.addWidget(project_button)
        layout.addLayout(project_row)
        self.refresh_project()
        self.project_timer = QTimer(self)
        self.project_timer.timeout.connect(self.refresh_project)
        self.project_timer.start(1500)
        layout.addWidget(QLabel('1回クリックで起動（ダブルクリック不要）  •  準備 → 抽出 → 分析の順に番号で整理しています'))
        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        layout.addLayout(self.grid, 1)
        self.cards = []
        self.processes = []
        for tool in TOOLS:
            card = ToolCard(tool)
            card.clicked.connect(lambda checked=False, c=card: self.launch(c))
            self.cards.append(card)
        self.columns = 0
        self.reflow()
        self.status = QLabel(f'全{len(TOOLS)}ツール  |  エリア・ゲートは14、ゾーニングは12で作成します。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def refresh_project(self):
        project = get_project()
        self.project_label.setText(f'共通プロジェクト: {project}' if project else '共通プロジェクト: 未選択')

    def choose_project(self):
        project = QFileDialog.getExistingDirectory(self, 'プロジェクトフォルダを選択', str(get_project() or ROOT))
        if project:
            set_project(project)
            self.refresh_project()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'cards'):
            self.reflow()

    def reflow(self):
        cols = 5 if self.width() >= 1180 else 4
        if self.columns == cols:
            return
        self.columns = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for i in range(8):
            self.grid.setRowStretch(i, 0)
            self.grid.setColumnStretch(i, 0)
        for i, card in enumerate(self.cards):
            self.grid.addWidget(card, i//cols, i%cols)
        for col in range(cols):
            self.grid.setColumnStretch(col, 1)
        for row in range(math.ceil(len(self.cards)/cols)):
            self.grid.setRowStretch(row, 1)

    def launch(self, card):
        if card.running:
            self.status.setText(f'{card.tool.number} のクリックは受け付け済みです。追加クリックは不要です。')
            return
        card.running = True
        card.started_at = time.monotonic()
        card.setCursor(Qt.CursorShape.BusyCursor)
        self.status.setText(f'{card.tool.number} {card.tool.title} を起動中です。追加クリックせず、そのままお待ちください。')
        card.update()
        # Return to Qt first so feedback is painted before creating the process.
        QTimer.singleShot(50, lambda: self.dispatch(card))

    def dispatch(self, card):
        try:
            process, log = launch_tool(card.tool)
            self.processes.append((process, card, log))
        except Exception as exc:
            self.release_card(card)
            QMessageBox.warning(self, '起動できません', str(exc))

    def release_card(self, card):
        card.running = False
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.update()

    def poll(self):
        for item in self.processes[:]:
            process, card, log = item
            tool = card.tool
            code = process.poll()
            card.update()
            # Browser-launching batches exit immediately; still debounce double clicks.
            if code is not None and (code or time.monotonic() - card.started_at >= 8):
                self.processes.remove(item)
                self.release_card(card)
                self.status.setText(f'{tool.number} の起動処理が終了しました。')
                if code:
                    self.status.setText(f'{tool.number} のバッチが終了コード {code} で終了しました。ログ: {log}')
                    detail = log.read_bytes()[-3000:].decode('utf-8', errors='replace')
                    QMessageBox.warning(self, 'ツールを起動できませんでした',
                                        f'{tool.number} {tool.title}\n\n{detail}\n\nログ: {log}')

    def shortcut(self):
        try:
            path = create_shortcut()
            QMessageBox.information(self, 'ショートカットを作成しました', path)
        except Exception as exc:
            QMessageBox.warning(self, 'ショートカット作成エラー', str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--create-shortcut', action='store_true')
    args = parser.parse_args()
    if args.create_shortcut:
        print(create_shortcut())
        return
    app = QApplication(sys.argv)
    app.setFont(QFont('Yu Gothic UI', 9))
    window = Launcher()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
