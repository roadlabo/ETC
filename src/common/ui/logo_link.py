from __future__ import annotations

import webbrowser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QLabel, QMessageBox, QWidget

ROADLABO_URL = "https://etc.roadlabo.com"


def _can_open_roadlabo(timeout: float = 2.5) -> bool:
    try:
        req = Request(
            ROADLABO_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            method="GET",
        )
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 400
    except (URLError, HTTPError, TimeoutError, OSError):
        return False
    except Exception:
        return False


def open_roadlabo_or_warn(parent: QWidget | None = None) -> bool:
    if not _can_open_roadlabo():
        QMessageBox.information(
            parent,
            "道路ラボ",
            "インターネット接続がされていません。",
            QMessageBox.StandardButton.Ok,
        )
        return False

    try:
        webbrowser.open(ROADLABO_URL)
        return True
    except Exception:
        QMessageBox.information(
            parent,
            "道路ラボ",
            "インターネット接続がされていません。",
            QMessageBox.StandardButton.Ok,
        )
        return False


class ClickableLogoLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip("道路ラボを開く")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            open_roadlabo_or_warn(self.window())
            event.accept()
            return
        super().mousePressEvent(event)
