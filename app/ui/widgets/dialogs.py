"""Türkçe onay diyaloğu ve hata ayrıntıları penceresi."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from app.ui.widgets.common import IconBadge, label, themed_button


def confirm(parent: QWidget, title: str, text: str, confirm_text: str = "Onayla", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setText(text)
    ok = box.addButton(confirm_text, QMessageBox.ButtonRole.AcceptRole)
    if danger:
        ok.setObjectName("Danger")
    cancel = box.addButton("Vazgeç", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is ok


class ErrorDetailsDialog(QDialog):
    def __init__(self, title: str, message: str, details: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hata ayrıntıları")
        self.resize(640, 420)
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("x-circle", "danger", size=40))
        texts = QVBoxLayout()
        texts.addWidget(label(title, "CardTitle"))
        texts.addWidget(label(message, "Muted", wrap=True))
        head.addLayout(texts, 1)
        col.addLayout(head)
        self.details = QPlainTextEdit(details)
        self.details.setReadOnly(True)
        self.details.setObjectName("Mono")
        col.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(label("Ayrıntılar günlük dosyasına da kaydedildi.", "Faint"), 1)
        self.copy_button = themed_button("Kopyala", "copy")
        self.copy_button.clicked.connect(self.copy)
        close = QPushButton("Kapat")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(close)
        col.addLayout(buttons)

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.details.toPlainText())
        self.copy_button.setText("Kopyalandı")
