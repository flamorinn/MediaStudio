"""Temel görsel yapı taşları: temaya duyarlı ikon, rozet, durum etiketi, başlık, boş durum."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from app.ui.theme import icons, theme
from app.ui.theme.tokens import rgba


def apply_shadow(widget: QWidget, blur: int = 30, y: int = 8, alpha: int = 110) -> None:
    """Kartlara yumuşak gölge verir."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def label(text: str = "", object_name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    lbl.setWordWrap(wrap)
    return lbl


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    return line


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ElidedLabel(QLabel):
    """Uzun metni (ör. dosya yolu) ortadan kısaltır; yerleşimi asla genişletmez. Tam metin ipucunda."""

    def __init__(self, text: str = "", object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self._full = ""
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 40)))

    def resizeEvent(self, event) -> None:
        self._elide()
        super().resizeEvent(event)


class ThemedIcon(QLabel):
    """Tema değişince kendini yeniden boyayan ikon. `tone` bir ton adı veya renk anahtarıdır."""

    def __init__(self, name: str, tone: str = "text_2", size: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name, self._tone, self._size = name, tone, size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        theme.bind(self, self.refresh)

    def set_icon(self, name: str | None = None, tone: str | None = None) -> None:
        self._name = name or self._name
        self._tone = tone or self._tone
        self.refresh()

    def refresh(self) -> None:
        self.setPixmap(icons.pixmap(self._name, theme.tone(self._tone), self._size))


class IconBadge(QLabel):
    """Ton renginde yarı saydam zemin üzerinde ikon (kart başlıkları, istatistikler)."""

    def __init__(self, name: str, tone: str = "accent", size: int = 40, icon_size: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name, self._tone, self._size = name, tone, size
        self._icon_size = icon_size or int(size * 0.5)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.bind(self, self.refresh)

    def set_icon(self, name: str | None = None, tone: str | None = None) -> None:
        self._name = name or self._name
        self._tone = tone or self._tone
        self.refresh()

    def refresh(self) -> None:
        color = theme.tone(self._tone)
        radius = max(8, self._size // 4)
        self.setStyleSheet(f"background: {rgba(color, 0.14)}; border: 1px solid {rgba(color, 0.22)};"
                           f"border-radius: {radius}px;")
        self.setPixmap(icons.pixmap(self._name, color, self._icon_size))


class StatusPill(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Pill")
        self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_state(self, tone: str, text: str) -> None:
        self.setText(text)
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            repolish(self)


class StatusDot(QLabel):
    def __init__(self, tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._tone = tone
        theme.bind(self, self.refresh)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.refresh()

    def refresh(self) -> None:
        self.setStyleSheet(f"background: {theme.tone(self._tone)}; border-radius: 4px;")


def icon_button(name: str, tooltip: str, size: int = 16) -> QPushButton:
    button = QPushButton()
    button.setObjectName("IconButton")
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)

    def refresh() -> None:
        button.setIcon(icons.icon(name, theme.color("text_3"), active=theme.color("text"), size=size))
        button.setIconSize(icons.icon_size(size))

    theme.bind(button, refresh)
    return button


def themed_button(text: str, icon_name: str | None = None, kind: str = "", tone: str = "text") -> QPushButton:
    """Metinli düğme; `kind` = Primary | Ghost | Link | Danger | '' (varsayılan)."""
    button = QPushButton(text)
    if kind:
        button.setObjectName(kind)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        def refresh() -> None:
            color = "#ffffff" if kind == "Primary" else theme.tone(
                {"Danger": "danger", "Link": "primary_hover", "Ghost": "text_2"}.get(kind, tone))
            button.setIcon(icons.icon(icon_name, color, active=color if kind else theme.color("text"),
                                      disabled=theme.color("text_3"), size=16))
            button.setIconSize(icons.icon_size(16))

        theme.bind(button, refresh)
    return button


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", eyebrow: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        if eyebrow:
            self.eyebrow = label(eyebrow.upper(), "Eyebrow")
            text_col.addWidget(self.eyebrow)
        self.title = label(title, "PageTitle")
        self.subtitle = label(subtitle, "PageSubtitle", wrap=True)
        text_col.addWidget(self.title)
        if subtitle:
            text_col.addWidget(self.subtitle)
        row.addLayout(text_col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)
        row.setAlignment(self.actions, Qt.AlignmentFlag.AlignBottom)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 28, 24, 28)
        col.setSpacing(8)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge = IconBadge(icon_name, "accent", size=56)
        col.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(6)
        self.title = label(title, "CardTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.title)
        self.text = label(text, "Muted", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Ortalanmış yerleşimde satır kaydıran etiketin yüksekliği ancak genişliği sabitse doğru
        # hesaplanır; aksi halde metin dar sığdırılıp üstten/alttan kırpılıyordu.
        self.text.setFixedWidth(420)
        col.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addSpacing(6)
        col.addLayout(self.buttons)

    def add_button(self, button: QPushButton) -> QPushButton:
        self.buttons.addWidget(button)
        return button
