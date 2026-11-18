"""Dönüştürücü: yerel video (ve ses) dosyalarını toplu olarak MP3'e çevirir.

Dosyalar sürükle-bırak, "Dosya ekle…" veya "Klasör ekle…" ile eklenir; her dosya FFprobe ile
incelenir. "Dönüştürmeyi başlat" hazır dosyaları seçilen kalitede kuyruğa alır.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QProgressBar, QStackedWidget, QVBoxLayout, QWidget,
)

from app.core.errors import MediaStudioError
from app.models.conversion import ConversionItem, ConversionStatus
from app.models.settings import AUDIO_BITRATES, CONVERT_OUTPUT_MODES
from app.services.converter import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, is_supported
from app.ui.pages.base import BasePage
from app.ui.widgets.cards import Card
from app.ui.widgets.common import (
    ElidedLabel, EmptyState, IconBadge, StatusPill, divider, icon_button, label, repolish, themed_button,
)
from app.ui.widgets.dialogs import ErrorDetailsDialog
from app.utils.formatting import human_duration, human_size
from app.utils.system import reveal_in_explorer

FILE_FILTER = ("Video ve ses dosyaları (" + " ".join(f"*{e}" for e in VIDEO_EXTENSIONS + AUDIO_EXTENSIONS) + ");;"
               "Video dosyaları (" + " ".join(f"*{e}" for e in VIDEO_EXTENSIONS) + ");;Tüm dosyalar (*)")
CUSTOM = "custom"


def describe(item: ConversionItem) -> str:
    base = []
    if item.duration:
        base.append(human_duration(item.duration))
    if item.size_bytes:
        base.append(human_size(item.size_bytes))
    s = item.status
    if s is ConversionStatus.CONVERTING:
        parts = [f"%{item.progress * 100:.0f}"]
        if item.speed:
            parts.append(f"hız {item.speed.replace('.', ',')}")
        if item.eta_seconds is not None:
            parts.append(f"kalan {human_duration(item.eta_seconds)}")
        return " · ".join(parts)
    if s is ConversionStatus.COMPLETED:
        return f"→ {item.output_path}"
    if s in (ConversionStatus.FAILED, ConversionStatus.INVALID):
        return item.error or "Bilinmeyen hata"
    if s is ConversionStatus.QUEUED:
        return " · ".join(base + [f"MP3 {item.bitrate} kbps · sırada"])
    if s is ConversionStatus.PROBING:
        return "Dosya inceleniyor…"
    if s is ConversionStatus.CANCELLED:
        return "İptal edildi; yarım dosya silindi."
    return " · ".join(base + [str(Path(item.source).parent)])


class ConversionRow(QFrame):
    def __init__(self, item: ConversionItem, page: "ConverterPage") -> None:
        super().__init__()
        self.item_id = item.id
        self.setObjectName("ListRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(14)
        self.badge = IconBadge("video" if item.has_video else "music", "neutral", size=36)
        row.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(5)
        top = QHBoxLayout()
        self.title = ElidedLabel(item.name)
        top.addWidget(self.title, 1)
        self.pill = StatusPill("", "neutral")
        top.addWidget(self.pill)
        col.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        col.addWidget(self.progress)
        self.meta = ElidedLabel("", "Faint")
        col.addWidget(self.meta)
        row.addLayout(col, 1)

        actions = QWidget()
        actions.setFixedWidth(3 * 32)
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(4)
        actions_row.addStretch(1)
        self.open_button = icon_button("folder-open", "Klasörde göster")
        self.details_button = icon_button("info", "Hata ayrıntıları")
        self.retry_button = icon_button("refresh", "Yeniden dene")
        self.cancel_button = icon_button("x-circle", "İptal et")
        self.remove_button = icon_button("trash", "Listeden kaldır")
        for button in (self.open_button, self.details_button, self.retry_button, self.cancel_button,
                       self.remove_button):
            button.setFixedSize(28, 28)
            actions_row.addWidget(button)
        row.addWidget(actions)
        manager = page.ctx.conversions
        self.open_button.clicked.connect(lambda: page.reveal(self.item_id))
        self.details_button.clicked.connect(lambda: page.show_details(self.item_id))
        self.retry_button.clicked.connect(lambda: manager.retry(self.item_id))
        self.cancel_button.clicked.connect(lambda: manager.cancel(self.item_id))
        self.remove_button.clicked.connect(lambda: manager.remove(self.item_id))
        self.update_from(item)

    def update_from(self, item: ConversionItem) -> None:
        s = item.status
        self.pill.set_state(s.tone, s.label)
        self.badge.set_icon("video" if item.has_video else "music",
                            "accent" if s is ConversionStatus.CONVERTING else s.tone if s.is_finished else "neutral")
        self.progress.setVisible(s in (ConversionStatus.CONVERTING, ConversionStatus.COMPLETED, ConversionStatus.QUEUED))
        self.progress.setValue(int(item.progress * 1000))
        self.meta.set_full_text(describe(item))
        self.title.setToolTip(item.source)
        exists = bool(item.output_path) and Path(item.output_path).exists()
        self.open_button.setVisible(s is ConversionStatus.COMPLETED and exists)
        self.details_button.setVisible(s in (ConversionStatus.FAILED, ConversionStatus.INVALID)
                                       and bool(item.error_details))
        self.retry_button.setVisible(s in (ConversionStatus.FAILED, ConversionStatus.CANCELLED))
        self.cancel_button.setVisible(s in (ConversionStatus.QUEUED, ConversionStatus.CONVERTING))
        self.remove_button.setVisible(not s.is_busy)


class DropZone(QFrame):
    """Görsel bırakma alanı; asıl bırakma olayını sayfa karşılar (tüm sayfaya bırakılabilir)."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setProperty("active", False)
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 22, 20, 22)
        col.setSpacing(8)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(IconBadge("folder-open", "accent", size=52), 0, Qt.AlignmentFlag.AlignHCenter)
        title = label("Video dosyalarını buraya sürükleyip bırakın", "CardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(title)
        hint = label("MP4, MKV, MOV, AVI, WEBM, WMV, FLV ve daha fazlası\nSes dosyaları da desteklenir", "Faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(hint)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add_files_button = themed_button("Dosya ekle…", "file", kind="Primary")
        self.add_folder_button = themed_button("Klasör ekle…", "folder")
        buttons.addWidget(self.add_files_button)
        buttons.addWidget(self.add_folder_button)
        col.addSpacing(4)
        col.addLayout(buttons)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        repolish(self)


class ConverterPage(BasePage):
    key = "converter"

    def __init__(self, ctx, navigate) -> None:
        super().__init__(ctx, navigate, "Dönüştürücü", "Bilgisayarınızdaki video dosyalarını toplu olarak MP3'e dönüştürün.")
        self.manager = ctx.conversions
        self._custom_dir: str | None = None
        self.setAcceptDrops(True)

        self.clear_button = self.header.add_action(themed_button("Listeyi temizle", "trash", kind="Ghost"))
        self.clear_button.clicked.connect(lambda: self.manager.clear())

        # --- Ekleme alanı ---------------------------------------------------
        self.drop_zone = DropZone()
        self.drop_zone.add_files_button.clicked.connect(self.choose_files)
        self.drop_zone.add_folder_button.clicked.connect(self.choose_folder)
        self.body.addWidget(self.drop_zone)

        # --- Dosya listesi --------------------------------------------------
        self.list_card = Card("Dosyalar", icon="list-checks")
        self.summary = label("", "Faint")
        self.list_card.add_header_widget(self.summary)
        self.stack = QStackedWidget()
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        self.empty = EmptyState("music", "Henüz dosya eklenmedi",
                                "Eklediğiniz dosyalar burada listelenir; süreleri ve ses akışları otomatik kontrol edilir.")
        self.stack.addWidget(self.list_widget)
        self.stack.addWidget(self.empty)
        self.list_card.body.addWidget(self.stack)
        self.body.addWidget(self.list_card)

        # --- Seçenekler -----------------------------------------------------
        options = Card("Dönüştürme seçenekleri", icon="sliders")
        self.bitrate_combo = QComboBox()
        for rate in AUDIO_BITRATES:
            self.bitrate_combo.addItem(f"MP3 · {rate} kbps", rate)
        self.bitrate_combo.setFixedWidth(230)
        options.body.addLayout(self._row("Ses kalitesi", "Daha yüksek bit hızı daha iyi ses ve daha büyük dosya demektir.",
                                         self.bitrate_combo))
        options.body.addWidget(divider())
        self.output_combo = QComboBox()
        for value, text in CONVERT_OUTPUT_MODES:
            self.output_combo.addItem(text, value)
        self.output_combo.addItem("Özel klasör…", CUSTOM)
        self.output_combo.setFixedWidth(230)
        options.body.addLayout(self._row("Çıktı konumu", "Aynı adlı dosya varsa numaralandırılır; kaynak dosyalar değişmez.",
                                         self.output_combo))
        self.output_label = ElidedLabel("", "Mono")
        options.body.addWidget(self.output_label)
        self.blocker = label("", "Muted", wrap=True)
        self.blocker.hide()
        options.body.addWidget(self.blocker)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.cancel_all_button = themed_button("Tümünü iptal et", "x-circle", kind="Danger")
        self.start_button = themed_button("Dönüştürmeyi başlat", "repeat", kind="Primary")
        actions.addWidget(self.cancel_all_button)
        actions.addWidget(self.start_button)
        options.body.addLayout(actions)
        self.body.addWidget(options)
        self.body.addStretch(1)

        self.rows: dict[int, ConversionRow] = {}
        self.cancel_all_button.clicked.connect(lambda: self.manager.cancel_all())
        self.start_button.clicked.connect(self.start)
        self.output_combo.activated.connect(self._output_chosen)
        self.manager.changed.connect(self.refresh)
        self.manager.item_updated.connect(self._update_row)
        ctx.dependencies_changed.connect(lambda _r: self._update_state())
        ctx.settings_changed.connect(lambda _k: self._load_defaults())
        self._load_defaults()
        self.refresh()

    @staticmethod
    def _row(title: str, desc: str, control: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title))
        texts.addWidget(label(desc, "Faint", wrap=True))
        row.addLayout(texts, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    # ------------------------------------------------------------------ ayarlar
    def _load_defaults(self) -> None:
        self.bitrate_combo.setCurrentIndex(max(0, self.bitrate_combo.findData(self.ctx.settings.audio_bitrate)))
        if self.output_combo.currentData() != CUSTOM:
            self.output_combo.setCurrentIndex(max(0, self.output_combo.findData(self.ctx.settings.convert_output_mode)))
        self._update_output_label()

    def output_mode(self) -> str:
        return self.output_combo.currentData()

    def _update_output_label(self) -> None:
        mode = self.output_mode()
        if mode == CUSTOM and self._custom_dir:
            self.output_label.set_full_text(self._custom_dir)
        elif mode == "source":
            self.output_label.set_full_text("Her dosya kendi klasörüne kaydedilir.")
        else:
            self.output_label.set_full_text(str(self.ctx.settings_manager.download_dir()))

    def _output_chosen(self, _index: int) -> None:
        mode = self.output_mode()
        if mode == CUSTOM:
            path = QFileDialog.getExistingDirectory(self, "MP3 dosyalarının kaydedileceği klasör",
                                                    self._custom_dir or str(self.ctx.settings_manager.download_dir()))
            if path:
                self._custom_dir = str(Path(path))
            elif not self._custom_dir:  # vazgeçildi: önceki ayara dön
                self.output_combo.setCurrentIndex(self.output_combo.findData(self.ctx.settings.convert_output_mode))
        else:
            self.ctx.update_settings(convert_output_mode=mode)
        self._update_output_label()

    # ------------------------------------------------------------------ ekleme
    def add_paths(self, paths) -> None:
        try:
            added, skipped = self.manager.add_files(paths)
        except MediaStudioError as exc:
            self.ctx.notifications.exception(exc, "Dosyalar eklenemedi")
            return
        if skipped:
            reasons: dict[str, int] = {}
            for _path, reason in skipped:
                reasons[reason] = reasons.get(reason, 0) + 1
            text = ", ".join(f"{n} {reason}" for reason, n in reasons.items())
            self.ctx.notifications.info(f"{len(added)} dosya eklendi" if added else "Dosya eklenmedi",
                                        f"Atlananlar: {text}.")

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Dönüştürülecek dosyaları seçin", "", FILE_FILTER)
        if paths:
            self.add_paths(paths)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Video dosyalarının bulunduğu klasör")
        if not folder:
            return
        files = sorted((p for p in Path(folder).iterdir() if p.is_file() and is_supported(p)),
                       key=lambda p: p.name.lower())
        if not files:
            self.ctx.notifications.info("Desteklenen dosya bulunamadı", f"{folder} klasöründe video veya ses dosyası yok.")
            return
        self.add_paths(files)

    # Sürükle-bırak: sayfanın her yerine bırakılabilir
    def _local_paths(self, event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = self._local_paths(event)
        if any(Path(p).is_dir() or is_supported(p) for p in paths):
            event.acceptProposedAction()
            self.drop_zone.set_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.drop_zone.set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_zone.set_active(False)
        paths: list[str] = []
        for p in self._local_paths(event):
            if Path(p).is_dir():  # bırakılan klasörün içindeki desteklenen dosyalar
                paths += [str(f) for f in sorted(Path(p).iterdir(), key=lambda f: f.name.lower())
                          if f.is_file() and is_supported(f)]
            else:
                paths.append(p)
        event.acceptProposedAction()
        self.add_paths(paths)

    # ------------------------------------------------------------------ liste
    def refresh(self) -> None:
        items = self.manager.list()
        if [i.id for i in items] != list(self.rows):
            while self.list_layout.count():
                entry = self.list_layout.takeAt(0)
                if entry.widget():
                    entry.widget().deleteLater()
            self.rows = {i.id: ConversionRow(i, self) for i in items}
            for row in self.rows.values():
                self.list_layout.addWidget(row)
            self.list_layout.addStretch(1)
        else:
            for item in items:
                self.rows[item.id].update_from(item)
        self.stack.setCurrentWidget(self.list_widget if items else self.empty)
        self._update_state()

    def _update_row(self, item_id: int) -> None:
        row, item = self.rows.get(item_id), self.manager.items.get(item_id)
        if row is not None and item is not None:
            row.update_from(item)

    def _update_state(self) -> None:
        items = self.manager.list()
        counts = self.manager.counts()
        total = sum(i.duration or 0 for i in items if i.status is not ConversionStatus.INVALID)
        parts = [f"{len(items)} dosya"] if items else []
        if total:
            parts.append(f"toplam {human_duration(total)}")
        done = counts.get(ConversionStatus.COMPLETED, 0)
        if done:
            parts.append(f"{done} tamamlandı")
        self.summary.setText(" · ".join(parts))

        deps = self.ctx.dependencies
        reason = ""
        if deps is None:
            reason = "Sistem bileşenleri kontrol ediliyor…"
        elif deps.ffmpeg is None or deps.ffprobe is None:
            reason = "Dönüştürme için FFmpeg ve FFprobe gerekli. Ayarlar → Sistem bileşenleri."
        self.blocker.setText(reason)
        self.blocker.setVisible(bool(reason))
        self.drop_zone.add_files_button.setEnabled(not reason)
        self.drop_zone.add_folder_button.setEnabled(not reason)

        startable = len(self.manager.startable())
        self.start_button.setText(f"Dönüştürmeyi başlat ({startable} dosya)" if startable else "Dönüştürmeyi başlat")
        self.start_button.setEnabled(startable > 0 and not reason)
        active = self.manager.has_active()
        self.cancel_all_button.setVisible(active)
        self.clear_button.setEnabled(any(not i.status.is_busy for i in items))

    # ------------------------------------------------------------------ eylemler
    def start(self) -> None:
        mode = self.output_mode()
        custom = self._custom_dir if mode == CUSTOM else None
        if mode == CUSTOM and not custom:
            self.ctx.notifications.warning("Klasör seçilmedi", "Özel çıktı klasörünü seçin.")
            return
        try:
            count = self.manager.start(int(self.bitrate_combo.currentData()),
                                       mode="downloads" if mode == CUSTOM else mode, custom_dir=custom)
        except MediaStudioError as exc:
            self.ctx.notifications.exception(exc, "Dönüştürme başlatılamadı")
            return
        if count:
            self.ctx.notifications.info("Dönüştürme başladı", f"{count} dosya sıraya alındı.")

    def reveal(self, item_id: int) -> None:
        item = self.manager.items.get(item_id)
        if item and not reveal_in_explorer(item.output_path):
            self.ctx.notifications.warning("Dosya bulunamadı", "Çıktı dosyası taşınmış veya silinmiş olabilir.")

    def show_details(self, item_id: int) -> None:
        item = self.manager.items.get(item_id)
        if item:
            ErrorDetailsDialog("Dönüştürme hatası", f"{item.name}: {item.error}", item.error_details, self).exec()
