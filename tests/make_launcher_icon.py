"""Rebuild the native launcher icon from simple vector shapes."""
import os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication(['make_launcher_icon'])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/arialbd.ttf')
image = QPixmap(256, 256)
image.fill(Qt.GlobalColor.transparent)
p = QPainter(image)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QColor('#0b1c45'))
p.drawRoundedRect(4, 4, 248, 248, 45, 45)
p.setBrush(QColor('#5cd7c1'))
for x in (55, 113, 171):
    p.drawRoundedRect(x, 48, 30, 30, 6, 6)
p.setPen(QColor('white'))
font = QFont('Arial')
font.setBold(True)
font.setPixelSize(83)
p.setFont(font)
p.drawText(QRect(8, 96, 240, 112), Qt.AlignmentFlag.AlignCenter, 'ETC')
p.end()
output = Path(__file__).resolve().parents[1] / 'src/assets/logos/logo_00_launcher.ico'
assert image.save(str(output), 'ICO')
print(output)
