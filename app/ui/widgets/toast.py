"""Sağ üstte beliren, otomatik kapanan bildirim kartları (toast)."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout, QWidget

from app.models.notification import Notification, NotificationLevel
from app.ui.theme import theme
from app.ui.widgets.common import IconBadge, icon_button, label, themed_button

_TONES = {
    NotificationLevel.INFO: "info",
    NotificationLevel.SUCCESS: "success",
    NotificationLevel.WARNING: "warning",
    NotificationLevel.ERROR: "danger",
}
WIDTH = 360
MARGIN = 20
GAP = 10
MAX_VISIBLE = 4


class Toast(QFrame):
    closed = Signal(object)
    details_requested = Signal(object)

    def __init__(self, note: Notification, parent: QWidget) -> None:
        super().__init__(parent)
        self.note = note
        self.setObjectName("Toast")
        self.setFixedWidth(WIDTH)
        self._closing = False
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 10, 12)
        row.setSpacing(12)
        row.addWidget(IconBadge(note.level.icon, _TONES[note.level], size=34), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(note.title, "ToastTitle", wrap=True))
        if note.message:
            col.addWidget(label(note.message, "ToastBody", wrap=True))
        if note.details:
            self.details_button = themed_button("Ayrıntılar", kind="Link")
            self.details_button.clicked.connect(lambda: self.details_requested.emit(self.note))
            col.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(col, 1)
        self.close_button = icon_button("x", "Kapat", 14)
        self.close_button.clicked.connect(self.dismiss)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.adjustSize()

    def appear(self, target: QPoint) -> None:
        self.move(target + QPoint(28, 0))
        self.show()
        self.raise_()
        if theme.animations:
            self._animate(b"opacity", 0.0, 1.0, self._effect)
            self.slide_to(target)
        else:
            self._effect.setOpacity(1.0)
            self.move(target)
        self._timer.start(self.note.level.timeout_ms)

    def slide_to(self, target: QPoint) -> None:
        if theme.animations and self.isVisible():
            self._animate(b"pos", self.pos(), target, self)
        else:
            self.move(target)

    def _animate(self, prop: bytes, start, end, obj: QObject) -> QPropertyAnimation:
        anim = QPropertyAnimation(obj, prop, self)
        anim.setDuration(220)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        if theme.animations and self.isVisible():
            anim = self._animate(b"opacity", self._effect.opacity(), 0.0, self._effect)
            anim.finished.connect(self._done)
        else:
            self._done()

    def _done(self) -> None:
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    # Fare üzerindeyken otomatik kapanma durur
    def enterEvent(self, event) -> None:
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._closing:
            self._timer.start(2500)
        super().leaveEvent(event)


class ToastHost(QObject):
    details_requested = Signal(object)

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.host = host
        self.toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(self, note: Notification) -> Toast:
        toast = Toast(note, self.host)
        toast.closed.connect(self._remove)
        toast.details_requested.connect(self.details_requested)
        self.toasts.insert(0, toast)
        for old in self.toasts[MAX_VISIBLE:]:
            old.dismiss()
        self._restack(new=toast)
        return toast

    def _positions(self) -> list[QPoint]:
        y = MARGIN
        points = []
        for toast in self.toasts:
            points.append(QPoint(self.host.width() - WIDTH - MARGIN, y))
            y += toast.sizeHint().height() + GAP
        return points

    def _restack(self, new: Toast | None = None) -> None:
        for toast, point in zip(self.toasts, self._positions()):
            if toast is new:
                toast.appear(point)
            else:
                toast.slide_to(point)

    def _remove(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
            self._restack()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.host and event.type() == QEvent.Type.Resize:
            for toast, point in zip(self.toasts, self._positions()):
                toast.move(point)
        return False
