"""Kart bileşenleri: genel kart, istatistik kartı, hızlı işlem kartı."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from app.ui.widgets.common import IconBadge, ThemedIcon, apply_shadow, label


class Card(QFrame):
    """Başlıklı/başlıksız yuvarlatılmış kart. İçerik `self.body` düzenine eklenir."""

    def __init__(self, title: str = "", subtitle: str = "", icon: str | None = None,
                 object_name: str = "Card", shadow: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)
        self.header = QHBoxLayout()
        self.header.setSpacing(12)
        if icon:
            self.header.addWidget(IconBadge(icon, "accent", size=36))
        if title or subtitle:
            titles = QVBoxLayout()
            titles.setSpacing(2)
            if title:
                self.title_label = label(title, "CardTitle")
                titles.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = label(subtitle, "CardSubtitle", wrap=True)
                titles.addWidget(self.subtitle_label)
            self.header.addLayout(titles, 1)
            outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)
        if shadow:
            apply_shadow(self)

    def add_header_widget(self, widget: QWidget) -> QWidget:
        self.header.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        return widget


class StatCard(QFrame):
    def __init__(self, icon: str, title: str, tone: str = "accent", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 18)
        col.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label(title, "StatLabel", wrap=True), 1)
        top.addWidget(IconBadge(icon, tone, size=34), 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(top)
        self.value = label("0", "StatValue")
        col.addWidget(self.value)
        self.caption = label("", "Faint", wrap=True)
        col.addWidget(self.caption)
        apply_shadow(self)

    def set_value(self, value: str | int, caption: str = "") -> None:
        self.value.setText(str(value))
        self.caption.setText(caption)


class ActionCard(QFrame):
    """Tıklanabilir hızlı işlem kartı (fare ve klavye ile)."""

    clicked = Signal()

    def __init__(self, icon: str, title: str, description: str, tone: str = "accent",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ActionCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(150)
        self.setAccessibleName(title)
        col = QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 16)
        col.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(IconBadge(icon, tone, size=42))
        top.addStretch(1)
        top.addWidget(ThemedIcon("arrow-right", "text_3", 16), 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(top)
        col.addSpacing(6)
        col.addWidget(label(title, "CardTitle", wrap=True))
        desc = label(description, "Muted", wrap=True)
        col.addWidget(desc)
        col.addStretch(1)
        apply_shadow(self, blur=24, y=6, alpha=90)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)
