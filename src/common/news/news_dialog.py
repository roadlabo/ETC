from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Optional

# 単体実行時でも src を import できるようにする
SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from common.news.news_fetcher import get_unseen_news, mark_as_seen


class NewsDialog(QDialog):
    """
    UI起動時のお知らせダイアログ
    ・「記事へ」: ブラウザで記事を開き、既読登録して閉じる
    ・「後で見る」: 未読のまま閉じる
    ・右上×: 未読のまま閉じる
    """

    def __init__(self, news_item: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.news_item = news_item
        self.open_clicked = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("道路ラボからのお知らせ")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setSizeGripEnabled(False)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(14)

        # ヘッダー
        header_label = QLabel("道路ラボから新着お知らせがあります")
        header_font = QFont()
        header_font.setPointSize(11)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setWordWrap(True)
        main_layout.addWidget(header_label)

        # タイトル
        title_label = QLabel(self.news_item.get("title", "（タイトルなし）"))
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setWordWrap(True)
        title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        main_layout.addWidget(title_label)

        # 更新日時
        modified_text = self.news_item.get("modified", "").replace("T", " ")
        if modified_text:
            modified_label = QLabel(f"更新日時: {modified_text}")
            modified_label.setWordWrap(True)
            modified_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            main_layout.addWidget(modified_label)

        # 説明
        body_label = QLabel(
            "記事を開くと、このお知らせは既読になります。\n"
            "後で見る場合は、今回は閉じて次回起動時に再度表示されます。"
        )
        body_label.setWordWrap(True)
        main_layout.addWidget(body_label)

        # ボタン行
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self.later_button = QPushButton("後で見る")
        self.later_button.clicked.connect(self._on_later_clicked)
        button_layout.addWidget(self.later_button)

        self.open_button = QPushButton("記事へ")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._on_open_clicked)
        button_layout.addWidget(self.open_button)

        main_layout.addLayout(button_layout)

    def _on_open_clicked(self) -> None:
        url = self.news_item.get("link", "").strip()
        if url:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        try:
            mark_as_seen(self.news_item)
        except Exception:
            # 既読保存失敗でもUIを止めない
            pass

        self.open_clicked = True
        self.accept()

    def _on_later_clicked(self) -> None:
        self.open_clicked = False
        self.reject()


def show_news_dialogs(parent: Optional[QWidget] = None) -> int:
    """
    未読ニュースを順番にダイアログ表示する。
    戻り値: 「記事へ」で既読登録された件数
    """
    try:
        unseen_news = get_unseen_news()
    except Exception:
        return 0

    if not unseen_news:
        return 0

    # 古い順に並べ替えてから表示
    unseen_news = sorted(unseen_news, key=lambda x: x.get("modified", ""))

    seen_count = 0

    for news_item in unseen_news:
        dialog = NewsDialog(news_item, parent=parent)
        dialog.exec()

        if dialog.open_clicked:
            seen_count += 1

    return seen_count


if __name__ == "__main__":
    app = QApplication(sys.argv)
    count = show_news_dialogs()
    print(f"既読登録件数: {count}")
    sys.exit(0)