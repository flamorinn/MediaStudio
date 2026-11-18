"""Geçmiş sayfası: arama, türe göre filtre, klasörde gösterme, tek kayıt silme, tümünü temizleme."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QStackedWidget, QTableWidget,
    QTableWidgetItem, QWidget,
)

from app.models.history import HistoryEntry, OperationKind
from app.ui.pages.base import BasePage
from app.ui.theme import icons, theme
from app.ui.widgets.cards import Card
from app.ui.widgets.common import EmptyState, label, themed_button
from app.ui.widgets.dialogs import confirm
from app.ui.widgets.responsive import AdaptiveRow
from app.utils.formatting import human_size
from app.utils.system import reveal_in_explorer

COLUMNS = ("Tarih", "Tür", "Başlık", "Boyut", "Durum", "Çıktı dosyası")


class HistoryPage(BasePage):
    key = "history"

    def __init__(self, ctx, navigate) -> None:
        super().__init__(ctx, navigate, "Geçmiş", "Tüm indirme, dönüştürme ve kesme işlemlerinin kaydı.",
                         scrollable=False)
        self._entries: list[HistoryEntry] = []
        self.clear_button = themed_button("Geçmişi temizle", "trash", kind="Danger")
        self.clear_button.clicked.connect(self.confirm_clear)



        self.search = QLineEdit()
        self.search.setPlaceholderText("Başlık, kaynak veya dosya ara…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(380)
        self._search_action = self.search.addAction(icons.icon("search", theme.color("text_3")),
                                                    QLineEdit.ActionPosition.LeadingPosition)
        theme.bind(self.search, lambda: self._search_action.setIcon(icons.icon("search", theme.color("text_3"))),
                   call_now=False)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("Tüm işlemler", None)
        for kind in OperationKind:
            self.kind_filter.addItem(kind.label, kind)
        self.kind_filter.setMinimumWidth(150)
        self.summary = label("", "Faint")
        self.reveal_button = themed_button("Klasörde göster", "folder-open")
        self.delete_button = themed_button("Kaydı sil", "x")
        self.search.setMinimumWidth(160)
        # Dar pencerede filtreler ve kayıt düğmeleri iki satıra bölünür
        filters = QWidget()
        filters_row = QHBoxLayout(filters)
        filters_row.setContentsMargins(0, 0, 0, 0)
        filters_row.setSpacing(10)
        filters_row.addWidget(self.search, 1)
        filters_row.addWidget(self.kind_filter)
        actions = QWidget()
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(8)
        actions_row.addStretch(1)
        actions_row.addWidget(self.reveal_button)
        actions_row.addWidget(self.delete_button)
        self.toolbar = AdaptiveRow(breakpoint=720, spacing=10)
        self.toolbar.add(filters, 3)
        self.toolbar.add(actions, 2)
        self.body.addWidget(self.toolbar)
        self.header.add_action(self.summary)
        self.header.add_action(self.clear_button)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        empty_card = Card(shadow=False)
        self.empty = EmptyState("history", "Kayıt bulunamadı", "")
        empty_card.body.addWidget(self.empty)
        self.stack.addWidget(empty_card)
        self.body.addWidget(self.stack, 1)

        self.search.textChanged.connect(self.reload)
        self.kind_filter.currentIndexChanged.connect(self.reload)
        self.reveal_button.clicked.connect(self.reveal_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *_: self.reveal_selected())
        ctx.history_changed.connect(self.reload)
        self.reload()

    def on_shown(self) -> None:
        self.reload()

    @property
    def filtering(self) -> bool:
        return bool(self.search.text().strip()) or self.kind_filter.currentData() is not None

    def reload(self) -> None:
        self._entries = self.ctx.history.list(kind=self.kind_filter.currentData(), search=self.search.text())
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            try:
                when = datetime.fromisoformat(entry.created_at).strftime("%d.%m.%Y  %H:%M")
            except ValueError:
                when = entry.created_at
            status = QTableWidgetItem(entry.status.label)
            status.setForeground(QColor(theme.tone(entry.status.tone)))
            if entry.details:
                status.setToolTip(entry.details)
            title = QTableWidgetItem(entry.title)
            title.setToolTip(entry.source or entry.title)
            output = QTableWidgetItem(entry.output_path)
            output.setToolTip(entry.output_path)
            items = [QTableWidgetItem(when), QTableWidgetItem(entry.kind.label), title,
                     QTableWidgetItem(human_size(entry.size_bytes) if entry.size_bytes else "—"), status, output]
            for col, item in enumerate(items):
                self.table.setItem(row, col, item)

        total = self.ctx.history.count()
        if self._entries:
            self.stack.setCurrentIndex(0)
        else:
            self.stack.setCurrentIndex(1)
            if self.filtering:
                self.empty.title.setText("Eşleşen kayıt yok")
                self.empty.text.setText("Aramayı veya filtreyi değiştirmeyi deneyin.")
            else:
                self.empty.title.setText("Henüz geçmiş yok")
                self.empty.text.setText("Tamamlanan ve başarısız olan işlemler burada tarih sırasıyla listelenecek.")
        shown = len(self._entries)
        self.summary.setText(f"{shown} / {total} kayıt" if self.filtering else f"{total} kayıt")
        self.clear_button.setEnabled(total > 0)
        self._update_buttons()

    def selected_entry(self) -> HistoryEntry | None:
        rows = self.table.selectionModel().selectedRows()
        return self._entries[rows[0].row()] if rows else None

    def _update_buttons(self) -> None:
        entry = self.selected_entry()
        self.delete_button.setEnabled(entry is not None)
        exists = bool(entry and entry.output_path and Path(entry.output_path).exists())
        self.reveal_button.setEnabled(exists)
        self.reveal_button.setToolTip("" if exists or not entry else "Çıktı dosyası bulunamadı")

    def reveal_selected(self) -> None:
        entry = self.selected_entry()
        if entry and not reveal_in_explorer(entry.output_path):
            self.ctx.notifications.warning("Dosya bulunamadı", "Çıktı dosyası taşınmış veya silinmiş olabilir.")

    def delete_selected(self) -> None:
        entry = self.selected_entry()
        if entry and self.ctx.history.delete(entry.id):
            self.ctx.history_changed.emit()

    def confirm_clear(self) -> None:
        if confirm(self, "Geçmişi temizle",
                   "Tüm geçmiş kayıtları silinecek. İndirilen dosyalar diskten silinmez.\nDevam edilsin mi?",
                   "Tümünü sil", danger=True):
            self.ctx.history.clear()
            self.ctx.history_changed.emit()
            self.ctx.notifications.success("Geçmiş temizlendi")
