"""Genişliğe göre sütun sayısını değiştiren ızgara (responsive yerleşim)."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QBoxLayout, QGridLayout, QLayout, QSizePolicy, QWidget


class ResponsiveGrid(QWidget):
    """Öğeleri `min_item_width` ölçüsüne göre 1..max_columns sütuna yerleştirir."""

    def __init__(self, min_item_width: int = 240, max_columns: int = 4, spacing: int = 16,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.min_item_width = min_item_width
        self.max_columns = max_columns
        self._items: list[QWidget] = []
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(spacing)
        # Izgara, o anki sütun sayısının toplam genişliğini minimum olarak dayatmamalı;
        # aksi halde pencere daraldığında sütun azalmaz ve sayfa yatay taşar.
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    @property
    def columns(self) -> int:
        return self._columns

    def minimumSizeHint(self) -> QSize:
        return QSize(self.min_item_width, self._grid.minimumSize().height())

    def add(self, widget: QWidget) -> QWidget:
        self._items.append(widget)
        self._relayout(self._columns_for(self.width()), force=True)
        return widget

    def _columns_for(self, width: int) -> int:
        spacing = self._grid.horizontalSpacing()
        cols = max(1, (width + spacing) // (self.min_item_width + spacing)) if width > 0 else self.max_columns
        cols = max(1, min(cols, self.max_columns, len(self._items) or 1))
        # Dengeli satırlar: 4 öğe 3 sütuna 3+1 diye dizilmesin, 2+2 olsun
        while cols > 1 and len(self._items) % cols:
            cols -= 1
        return cols

    def _relayout(self, columns: int, force: bool = False) -> None:
        if columns == self._columns and not force:
            return
        for widget in self._items:
            self._grid.removeWidget(widget)
        for c in range(max(self._columns, columns, self.max_columns)):
            self._grid.setColumnStretch(c, 0)
        for i, widget in enumerate(self._items):
            self._grid.addWidget(widget, i // columns, i % columns)
        for c in range(columns):
            self._grid.setColumnStretch(c, 1)
        self._columns = columns

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._relayout(self._columns_for(event.size().width()))
        super().resizeEvent(event)


class AdaptiveRow(QWidget):
    """Geniş ekranda yan yana (oranlı), dar ekranda alt alta dizilen bölümler."""

    def __init__(self, breakpoint: int = 900, spacing: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.breakpoint = breakpoint
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        self._box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._stretches: list[int] = []
        self._widgets: list[QWidget] = []

    @property
    def horizontal(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.LeftToRight

    def add(self, widget: QWidget, stretch: int = 1) -> QWidget:
        self._stretches.append(stretch)
        self._widgets.append(widget)
        self._box.addWidget(widget, stretch)
        return widget

    def minimumSizeHint(self) -> QSize:
        # Yan yana sığmıyorsa alt alta dizileceği için en geniş tek öğe yeterlidir.
        width = max((w.minimumSizeHint().width() for w in self._widgets), default=0)
        return QSize(width, self._box.minimumSize().height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        wide = event.size().width() >= self.breakpoint
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._box.direction() != direction:
            self._box.setDirection(direction)
            for i, stretch in enumerate(self._stretches):
                self._box.setStretch(i, stretch if wide else 0)
        super().resizeEvent(event)
