"""Tüm sayfaların ortak iskeleti: kaydırılabilir, ortalanmış, en fazla belirli genişlikte gövde."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from app.core.context import AppContext
from app.ui.widgets.common import PageHeader

MAX_CONTENT_WIDTH = 1320

Navigate = Callable[[str], None]


class BasePage(QWidget):
    key: str = ""

    def __init__(self, ctx: AppContext, navigate: Navigate, title: str, subtitle: str = "",
                 eyebrow: str = "", scrollable: bool = True) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.navigate = navigate

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.body_widget = QWidget()
        self.body_widget.setObjectName("PageBody")
        self.body_widget.setMaximumWidth(MAX_CONTENT_WIDTH)
        self.body_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(36, 30, 36, 30)
        self.body.setSpacing(22)

        self.header = PageHeader(title, subtitle, eyebrow)
        self.body.addWidget(self.header)

        center = QWidget()
        center.setObjectName("PageBody")
        row = QHBoxLayout(center)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(0)
        row.addWidget(self.body_widget, 1)
        row.addStretch(0)

        if scrollable:
            self.scroll = QScrollArea()
            self.scroll.setObjectName("PageScroll")
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll.setWidget(center)
            outer.addWidget(self.scroll)
        else:
            self.scroll = None
            outer.addWidget(center)

    def on_shown(self) -> None:
        """Sayfa görünür olduğunda çağrılır (verileri tazelemek için)."""
