"""Animasyonlu açma/kapama anahtarı."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QWidget

from app.ui.theme import theme


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme.bind(self, self.update, call_now=False)

    def sizeHint(self) -> QSize:
        return QSize(42, 24)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        if not theme.animations or not self.isVisible():
            self._set_offset(1.0 if checked else 0.0)
            return
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        track_h = 22
        track = QRectF(1, (h - track_h) / 2, 40, track_h)
        off_color = QColor(theme.color("border_strong"))
        on_color = QColor(theme.color("primary"))
        t = self._offset
        mixed = QColor(
            int(off_color.red() + (on_color.red() - off_color.red()) * t),
            int(off_color.green() + (on_color.green() - off_color.green()) * t),
            int(off_color.blue() + (on_color.blue() - off_color.blue()) * t),
        )
        if not self.isEnabled():
            mixed.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mixed)
        p.drawRoundedRect(track, track_h / 2, track_h / 2)
        knob = 16
        x = track.left() + 3 + (track.width() - knob - 6) * t
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, track.top() + 3, knob, knob))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            pen_color = QColor(theme.color("primary_hover"))
            pen_color.setAlpha(160)
            p.setPen(pen_color)
            p.drawRoundedRect(track.adjusted(-1, -1, 1, 1), track_h / 2 + 1, track_h / 2 + 1)
        p.end()
