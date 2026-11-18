"""Sol sabit gezinme çubuğu: marka, bölümlere ayrılmış menü, kuyruk rozeti, sistem durumu kartı."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from app import APP_NAME, APP_VERSION
from app.core.dependencies import DependencyReport
from app.ui.theme import icons, theme
from app.ui.widgets.common import StatusDot, label

SIDEBAR_WIDTH = 248


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str
    section: str


class NavButton(QPushButton):
    def __init__(self, item: NavItem, parent: QWidget | None = None) -> None:
        super().__init__(item.label, parent)
        self.item = item
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(icons.icon_size(18))
        self.setMinimumHeight(40)
        self.setAccessibleName(item.label)
        inner = QHBoxLayout(self)
        inner.setContentsMargins(0, 0, 10, 0)
        inner.addStretch(1)
        self.badge = QLabel()
        self.badge.setObjectName("NavBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedHeight(18)
        self.badge.hide()
        inner.addWidget(self.badge)
        theme.bind(self, self.refresh_icon)

    def refresh_icon(self) -> None:
        self.setIcon(icons.icon(self.item.icon, theme.color("text_3"), active=theme.color("text"),
                                checked=theme.color("primary_hover")))

    def set_badge(self, count: int) -> None:
        self.badge.setText(str(count))
        self.badge.setVisible(count > 0)


class SystemStatusCard(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SystemCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setToolTip("Sistem bileşenlerini Ayarlar'da yönet")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        col.addWidget(label("Sistem durumu", "Faint"))
        self.rows: dict[str, tuple[StatusDot, QLabel]] = {}
        for key, name in (("ffmpeg", "FFmpeg"), ("ffprobe", "FFprobe"), ("ytdlp", "yt-dlp")):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = StatusDot("neutral")
            row.addWidget(dot)
            row.addWidget(label(name, "Muted"), 1)
            value = label("…", "Faint")
            row.addWidget(value)
            col.addLayout(row)
            self.rows[key] = (dot, value)

    def set_checking(self) -> None:
        for dot, value in self.rows.values():
            dot.set_tone("neutral")
            value.setText("kontrol…")

    def set_report(self, report: DependencyReport) -> None:
        for key in ("ffmpeg", "ffprobe", "ytdlp"):
            tool = getattr(report, key)
            dot, value = self.rows[key]
            dot.set_tone("success" if tool else "danger")
            value.setText(tool.short_version[:12] if tool else "yok")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Sidebar(QFrame):
    page_requested = Signal(str)

    def __init__(self, items: list[NavItem], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 20, 14, 14)
        col.setSpacing(2)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(36, 36)
        brand.addWidget(self.logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(APP_NAME, "BrandName"))
        names.addWidget(label(f"Sürüm {APP_VERSION}", "BrandTag"))
        brand.addLayout(names, 1)
        col.addLayout(brand)
        col.addSpacing(10)
        theme.bind(self, self._refresh_logo)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, NavButton] = {}
        section = None
        for item in items:
            if item.section != section:
                section = item.section
                col.addWidget(label(section.upper(), "NavSection"))
            button = NavButton(item)
            button.clicked.connect(lambda _=False, k=item.key: self.page_requested.emit(k))
            self.group.addButton(button)
            self.buttons[item.key] = button
            col.addWidget(button)

        col.addStretch(1)
        self.system_card = SystemStatusCard()
        col.addWidget(self.system_card)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_system_card)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_system_card)

    def _fit_system_card(self) -> None:
        """Kısa pencerelerde sistem kartı menünün üstüne binmesin; sığmıyorsa gizlenir."""
        last = list(self.buttons.values())[-1]
        margins = self.layout().contentsMargins()
        needed = last.geometry().bottom() + 12 + self.system_card.sizeHint().height() + margins.bottom()
        self.system_card.setVisible(self.height() >= needed)

    def _refresh_logo(self) -> None:
        self.logo.setPixmap(icons.logo_pixmap(36, theme.color("primary"), theme.color("secondary")))

    def set_current(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def set_badge(self, key: str, count: int) -> None:
        self.buttons[key].set_badge(count)
