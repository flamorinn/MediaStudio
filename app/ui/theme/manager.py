"""Canlı tema yöneticisi. Vurgu rengi değişince QSS ve ikonlar anında yenilenir."""

from __future__ import annotations

import itertools
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from app.ui.theme.stylesheet import build_stylesheet
from app.ui.theme.tokens import FONT_FAMILIES, Palette, build_palette


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.palette: Palette = build_palette("aurora")
        self.animations = True
        self._hooks: dict[int, Callable[[], None]] = {}
        self._ids = itertools.count()

    def color(self, key: str) -> str:
        return self.palette[key]

    def tone(self, tone: str) -> str:
        return self.palette.tone(tone)

    def bind(self, owner: QObject, fn: Callable[[], None], call_now: bool = True) -> None:
        """`fn`'i tema değişimlerinde çağırır; `owner` yok edilince kayıt kendiliğinden silinir.

        Qt sinyal bağlantısı yerine kayıt defteri kullanılır: silinmiş bir widget'ın bağlantısını
        koparmaya çalışmak PySide6'da konsola hata basıyordu.
        """
        key = next(self._ids)
        self._hooks[key] = fn
        owner.destroyed.connect(lambda *_args, k=key: self._hooks.pop(k, None))
        if call_now:
            fn()

    @property
    def hook_count(self) -> int:
        return len(self._hooks)

    def _run_hooks(self) -> None:
        for fn in list(self._hooks.values()):
            fn()

    def apply(self, app: QApplication, accent: str | None = None, animations: bool | None = None) -> None:
        if animations is not None:
            self.animations = animations
        if accent is not None:
            self.palette = build_palette(accent)
        c = self.palette.colors
        app.setStyle("Fusion")
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPointSizeF(10)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        app.setFont(font)

        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(c["surface_2"]))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["surface"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
        pal.setColor(QPalette.ColorRole.Button, QColor(c["surface_2"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c["primary"]))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["surface_2"]))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["text_3"]))
        pal.setColor(QPalette.ColorRole.Link, QColor(c["primary_hover"]))
        app.setPalette(pal)
        app.setStyleSheet(build_stylesheet(self.palette))
        self._run_hooks()
        self.changed.emit()


theme = ThemeManager()
