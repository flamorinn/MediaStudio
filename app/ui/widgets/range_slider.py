"""İki tutamaçlı aralık kaydırıcısı (başlangıç–bitiş seçimi). Değerler milisaniye cinsindendir.

Fare: tutamacı sürükleyin veya parçaya tıklayın (en yakın tutamaç oraya gelir).
Klavye: ←/→ etkin tutamacı 0,1 sn, Shift ile 1 sn kaydırır; Boşluk tuşu etkin tutamacı değiştirir.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.ui.theme import theme

HANDLE_RADIUS = 9
TRACK_HEIGHT = 6


class RangeSlider(QWidget):
    rangeChanged = Signal(int, int)

    def __init__(self, parent: QWidget | None = None, min_gap_ms: int = 100) -> None:
        super().__init__(parent)
        self._max = 1000
        self._lo = 0
        self._hi = 1000
        self._gap = min_gap_ms
        self._active = 0          # 0 = başlangıç, 1 = bitiş
        self._dragging = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(HANDLE_RADIUS * 2 + 16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Kesim aralığı")
        theme.bind(self, self.update, call_now=False)

    def sizeHint(self) -> QSize:
        return QSize(400, HANDLE_RADIUS * 2 + 16)

    # ------------------------------------------------------------------ değerler
    def maximum(self) -> int:
        return self._max

    def values(self) -> tuple[int, int]:
        return self._lo, self._hi

    @property
    def active_handle(self) -> int:
        return self._active

    def setMaximum(self, value: int) -> None:
        self._max = max(int(value), self._gap)
        self.setRange(min(self._lo, self._max - self._gap), min(self._hi, self._max), emit=False)

    def setRange(self, lo: int, hi: int, emit: bool = True) -> None:
        lo = max(0, min(int(lo), self._max - self._gap))
        hi = max(lo + self._gap, min(int(hi), self._max))
        if (lo, hi) == (self._lo, self._hi):
            return
        self._lo, self._hi = lo, hi
        self.update()
        if emit:
            self.rangeChanged.emit(lo, hi)

    def _set_handle(self, handle: int, value: int) -> None:
        if handle == 0:
            self.setRange(min(value, self._hi - self._gap), self._hi)
        else:
            self.setRange(self._lo, max(value, self._lo + self._gap))

    # ------------------------------------------------------------------ geometri
    def _track(self) -> QRectF:
        x0 = HANDLE_RADIUS + 2
        return QRectF(x0, self.height() / 2 - TRACK_HEIGHT / 2, max(self.width() - 2 * x0, 1), TRACK_HEIGHT)

    def _x_for(self, value: int) -> float:
        track = self._track()
        return track.left() + track.width() * (value / self._max if self._max else 0)

    def _value_for(self, x: float) -> int:
        track = self._track()
        ratio = min(max((x - track.left()) / track.width(), 0.0), 1.0)
        return int(round(ratio * self._max))

    # ------------------------------------------------------------------ çizim
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        radius = TRACK_HEIGHT / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.color("border_strong")))
        p.drawRoundedRect(track, radius, radius)

        x_lo, x_hi = self._x_for(self._lo), self._x_for(self._hi)
        selected = QRectF(x_lo, track.top(), max(x_hi - x_lo, 1), track.height())
        gradient = QLinearGradient(selected.topLeft(), selected.topRight())
        gradient.setColorAt(0, QColor(theme.color("primary")))
        gradient.setColorAt(1, QColor(theme.color("secondary")))
        p.setBrush(gradient)
        p.drawRoundedRect(selected, radius, radius)

        cy = self.height() / 2
        for index, x in enumerate((x_lo, x_hi)):
            focused = self.hasFocus() and index == self._active
            if focused:
                ring = QColor(theme.color("primary"))
                ring.setAlpha(70)
                p.setBrush(ring)
                p.drawEllipse(QPointF(x, cy), HANDLE_RADIUS + 5, HANDLE_RADIUS + 5)
            p.setBrush(QColor("#ffffff"))
            p.setPen(QPen(QColor(theme.color("primary")), 3))
            p.drawEllipse(QPointF(x, cy), HANDLE_RADIUS - 1, HANDLE_RADIUS - 1)
            p.setPen(Qt.PenStyle.NoPen)
        p.end()

    # ------------------------------------------------------------------ fare
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        x = event.position().x()
        d_lo, d_hi = abs(x - self._x_for(self._lo)), abs(x - self._x_for(self._hi))
        if d_lo == d_hi:  # tutamaçlar üst üste: tıklanan yöne göre seç
            self._active = 0 if x < self._x_for(self._lo) else 1
        else:
            self._active = 0 if d_lo < d_hi else 1
        self._dragging = True
        self._set_handle(self._active, self._value_for(x))
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._set_handle(self._active, self._value_for(event.position().x()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------ klavye
    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = 1000 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 100
        current = self._lo if self._active == 0 else self._hi
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._set_handle(self._active, current - step)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._set_handle(self._active, current + step)
        elif event.key() == Qt.Key.Key_Space:
            self._active = 1 - self._active
            self.update()
        else:
            super().keyPressEvent(event)
