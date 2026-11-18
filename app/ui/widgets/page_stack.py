"""Sayfa geçişlerinde yumuşak çapraz geçiş + hafif kaydırma animasyonu yapan yığın."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

from app.ui.theme import theme


class AnimatedStack(QStackedWidget):
    """Eski sayfanın anlık görüntüsünü üstte soldururken yeni sayfayı hafifçe kaydırarak getirir.

    Yalnızca üstteki anlık görüntüye opaklık efekti uygulanır; böylece kartlardaki gölge
    efektleriyle çakışma olmaz ve geçiş ucuzdur.
    """

    DURATION_MS = 220

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overlay: QLabel | None = None
        self._group: QParallelAnimationGroup | None = None

    @property
    def animating(self) -> bool:
        return self._group is not None

    def switch_to(self, widget: QWidget) -> None:
        if widget is self.currentWidget():
            return
        self._finish()
        old = self.currentWidget()
        animate = theme.animations and self.isVisible() and old is not None and self.width() > 0
        if not animate:
            self.setCurrentWidget(widget)
            return

        overlay = QLabel(self)
        # Geçiş sırasında tıklamalar alttaki yeni sayfaya ulaşmalı (görüntü fareyi yutmasın)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.setPixmap(old.grab())
        overlay.setGeometry(old.geometry())
        effect = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(effect)
        self.setCurrentWidget(widget)
        overlay.show()
        overlay.raise_()

        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(self.DURATION_MS - 40)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide = QPropertyAnimation(widget, b"pos", self)
        slide.setDuration(self.DURATION_MS)
        slide.setStartValue(QPoint(18, 0))
        slide.setEndValue(QPoint(0, 0))
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(self._finish)
        self._overlay, self._group = overlay, group
        group.start()

    def _finish(self) -> None:
        group, overlay = self._group, self._overlay
        self._group = self._overlay = None
        if group is not None:
            group.stop()
            group.deleteLater()
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
        current = self.currentWidget()
        if current is not None:
            current.move(0, 0)
