"""Video Kes: bir dosyanın seçilen zaman aralığını MP4 veya MP3 olarak dışa aktarır.

Akış: dosya seç / sürükle → FFprobe ile incele → aralığı kaydırıcı veya zaman kodu alanlarıyla
belirle (başlangıç/bitiş kareleri önizlenir) → biçim ve mod seç → Dışa aktar (kuyruk).
"""

from __future__ import annotations

import itertools
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QStackedWidget,
    QVBoxLayout, QWidget,
)

from app.core.errors import MediaStudioError
from app.core.workers import Worker
from app.models.cut import CutFormat, CutJobItem, CutMode, CutStatus, MediaDetails
from app.models.settings import AUDIO_BITRATES, CONVERT_OUTPUT_MODES
from app.services.converter import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from app.services.cutter import KEYFRAME_TOLERANCE, extract_frame, keyframe_before, probe_details
from app.ui.pages.base import BasePage
from app.ui.theme import icons, theme
from app.ui.theme.tokens import rgba
from app.ui.widgets.cards import Card
from app.ui.widgets.common import (
    ElidedLabel, EmptyState, IconBadge, StatusPill, divider, icon_button, label, repolish, themed_button,
)
from app.ui.widgets.dialogs import ErrorDetailsDialog
from app.ui.widgets.range_slider import RangeSlider
from app.ui.widgets.responsive import AdaptiveRow
from app.utils.formatting import human_duration, human_size
from app.utils.system import reveal_in_explorer
from app.utils.timecode import format_timecode, parse_timecode

SUPPORTED = VIDEO_EXTENSIONS + AUDIO_EXTENSIONS
FILE_FILTER = ("Video ve ses dosyaları (" + " ".join(f"*{e}" for e in SUPPORTED) + ");;Tüm dosyalar (*)")
CUSTOM = "custom"
PREVIEW_W, PREVIEW_H = 240, 135
CODEC_NAMES = {"h264": "H.264", "hevc": "H.265", "vp9": "VP9", "vp8": "VP8", "av1": "AV1", "mpeg4": "MPEG-4",
               "aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC", "ac3": "AC-3"}


# Arka plan işleri: sonuç istek numarasıyla döner, sayfa slotunda ana iş parçacığında işlenir
def _probe_job(request: int, ffprobe: str, path: str) -> tuple:
    try:
        return request, probe_details(ffprobe, path), None
    except Exception as exc:  # noqa: BLE001
        return request, None, exc


def _frame_job(request: int, which: str, ffmpeg: str, path: str, t: float) -> tuple:
    return request, which, extract_frame(ffmpeg, path, t, width=PREVIEW_W * 2)


def _keyframe_job(request: int, ffprobe: str, path: str, t: float) -> tuple:
    return request, t, keyframe_before(ffprobe, path, t)


class ExportRow(QFrame):
    def __init__(self, item: CutJobItem, page: "CutterPage") -> None:
        super().__init__()
        self.item_id = item.id
        self.setObjectName("ListRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(14)
        self.badge = IconBadge("scissors", "neutral", size=36)
        row.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(5)
        top = QHBoxLayout()
        self.title = ElidedLabel("")
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
        manager = page.ctx.cuts
        self.open_button.clicked.connect(lambda: page.reveal(self.item_id))
        self.details_button.clicked.connect(lambda: page.show_details(self.item_id))
        self.retry_button.clicked.connect(lambda: manager.retry(self.item_id))
        self.cancel_button.clicked.connect(lambda: manager.cancel(self.item_id))
        self.remove_button.clicked.connect(lambda: manager.remove(self.item_id))
        self.update_from(item)

    def update_from(self, item: CutJobItem) -> None:
        s = item.status
        span = f"{format_timecode(item.start)} – {format_timecode(item.end)}"
        self.title.set_full_text(f"{item.name}  ·  {span}")
        self.pill.set_state(s.tone, s.label)
        self.badge.set_icon("music" if item.fmt is CutFormat.MP3 else "scissors",
                            "accent" if s is CutStatus.RUNNING else s.tone if not s.is_active else "neutral")
        self.progress.setVisible(s in (CutStatus.RUNNING, CutStatus.COMPLETED, CutStatus.QUEUED))
        self.progress.setValue(int(item.progress * 1000))
        mode = "MP3 " + f"{item.bitrate} kbps" if item.fmt is CutFormat.MP3 else f"MP4 · {item.mode.label}"
        if s is CutStatus.RUNNING:
            parts = [f"%{item.progress * 100:.0f}"]
            if item.speed:
                parts.append(f"hız {item.speed.replace('.', ',')}")
            if item.eta_seconds is not None:
                parts.append(f"kalan {human_duration(item.eta_seconds)}")
            meta = " · ".join([mode] + parts)
        elif s is CutStatus.COMPLETED:
            meta = f"→ {item.output_path}"
        elif s is CutStatus.FAILED:
            meta = item.error
        elif s is CutStatus.CANCELLED:
            meta = "İptal edildi; yarım dosya silindi."
        else:
            meta = f"{mode} · sırada"
        self.meta.set_full_text(meta)
        exists = bool(item.output_path) and Path(item.output_path).exists()
        self.open_button.setVisible(s is CutStatus.COMPLETED and exists)
        self.details_button.setVisible(s is CutStatus.FAILED and bool(item.error_details))
        self.retry_button.setVisible(s in (CutStatus.FAILED, CutStatus.CANCELLED))
        self.cancel_button.setVisible(s.is_active)
        self.remove_button.setVisible(not s.is_active)


class FramePreview(QWidget):
    def __init__(self, caption: str) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self.image = QLabel()
        self.image.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.image)
        self.caption = label(caption, "Faint")
        col.addWidget(self.caption)
        self._caption = caption
        self.has_frame = False
        theme.bind(self, self.clear)

    def clear(self, loading: bool = False) -> None:
        self.has_frame = False
        self.image.setStyleSheet(f"background: {rgba(theme.color('primary'), 0.08)};"
                                 f"border: 1px solid {theme.color('border')}; border-radius: 10px;")
        self.image.setPixmap(icons.pixmap("clock" if loading else "video", theme.color("text_3"), 28))

    def set_frame(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            self.clear()
            return
        ratio = self.devicePixelRatioF()
        scaled = pixmap.scaled(int(PREVIEW_W * ratio), int(PREVIEW_H * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.image.setStyleSheet("background: #000; border-radius: 10px;")
        self.image.setPixmap(scaled)
        self.has_frame = True

    def set_time(self, seconds: float) -> None:
        self.caption.setText(f"{self._caption} · {format_timecode(seconds)}")


class CutterPage(BasePage):
    key = "cutter"
    _requests = itertools.count(1)

    def __init__(self, ctx, navigate) -> None:
        super().__init__(ctx, navigate, "Video Kes",
                         "Videonun yalnızca seçtiğiniz aralığını MP4 veya MP3 olarak dışa aktarın.")
        self.manager = ctx.cuts
        self.details: MediaDetails | None = None
        self._probe_request = 0
        self._frame_requests = {"start": 0, "end": 0}
        self._keyframe_request = 0
        self.keyframe: float | None = None
        self._keyframe_for: float | None = None
        self._custom_dir: str | None = None
        self.setAcceptDrops(True)

        # --- 1. Kaynak ------------------------------------------------------
        self.source_card = Card("Kaynak dosya", icon="video")
        picker = QFrame()
        picker.setObjectName("DropZone")
        self.picker = picker
        picker_col = QVBoxLayout(picker)
        picker_col.setContentsMargins(20, 18, 20, 18)
        picker_col.setSpacing(8)
        picker_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = label("Video dosyasını buraya sürükleyin", "CardTitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picker_col.addWidget(hint)
        sub = label("ya da düğmeyle seçin (ses dosyaları da olur)", "Faint")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picker_col.addWidget(sub)
        self.choose_button = themed_button("Dosya seç…", "file", kind="Primary")
        picker_col.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignHCenter)

        # Seçici ve yüklü dosya satırı ayrı ayrı gösterilir/gizlenir: kart yalnızca görünen
        # içerik kadar yer kaplar (yığın bileşeni gizli sayfanın yüksekliğini koruyordu).
        loaded = QWidget()
        self.loaded_widget = loaded
        loaded_row = QHBoxLayout(loaded)
        loaded_row.setContentsMargins(0, 0, 0, 0)
        loaded_row.setSpacing(14)
        self.file_badge = IconBadge("video", "accent", size=44)
        loaded_row.addWidget(self.file_badge)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.file_name = ElidedLabel("", "CardTitle")
        self.file_meta = label("", "Muted", wrap=True)
        texts.addWidget(self.file_name)
        texts.addWidget(self.file_meta)
        loaded_row.addLayout(texts, 1)
        self.change_button = themed_button("Başka dosya…", "folder")
        loaded_row.addWidget(self.change_button)
        loaded.hide()
        self.source_card.body.addWidget(picker)
        self.source_card.body.addWidget(loaded)
        self.source_status = label("", "Muted", wrap=True)
        self.source_status.hide()
        self.source_card.body.addWidget(self.source_status)
        self.body.addWidget(self.source_card)

        # --- 2. Aralık -----------------------------------------------------
        self.range_card = Card("Kesim aralığı", "Kaydırıcıyı sürükleyin veya zamanları yazın (ss:dd:sn.ms).",
                               icon="clock")
        self.slider = RangeSlider()
        self.range_card.body.addWidget(self.slider)
        fields = QHBoxLayout()
        fields.setSpacing(10)
        self.start_edit = QLineEdit()
        self.end_edit = QLineEdit()
        for edit, name in ((self.start_edit, "Başlangıç"), (self.end_edit, "Bitiş")):
            edit.setFixedWidth(140)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setAccessibleName(name)
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(label(name, "Faint"))
            box.addWidget(edit)
            fields.addLayout(box)
        fields.addSpacing(10)
        length_box = QVBoxLayout()
        length_box.setSpacing(4)
        length_box.addWidget(label("Seçilen süre", "Faint"))
        self.length_label = label("", "CardTitle")
        length_box.addWidget(self.length_label)
        fields.addLayout(length_box)
        fields.addStretch(1)
        self.range_card.body.addLayout(fields)
        self.range_error = label("", "Muted", wrap=True)
        self.range_error.setStyleSheet(f"color: {theme.color('danger')};")
        self.range_error.hide()
        self.range_card.body.addWidget(self.range_error)
        self.previews = AdaptiveRow(breakpoint=560, spacing=16)
        self.start_preview = self.previews.add(FramePreview("Başlangıç karesi"), 0)
        self.end_preview = self.previews.add(FramePreview("Bitiş karesi"), 0)
        spacer = QWidget()
        self.previews.add(spacer, 1)
        self.range_card.body.addWidget(self.previews)
        self.range_card.hide()
        self.body.addWidget(self.range_card)

        # --- 3. Çıktı -------------------------------------------------------
        self.options_card = Card("Çıktı", icon="sliders")
        self.format_combo = QComboBox()
        for fmt in CutFormat:
            self.format_combo.addItem(fmt.label, fmt)
        self.mode_combo = QComboBox()
        for mode in CutMode:
            self.mode_combo.addItem(mode.label, mode)
        self.bitrate_combo = QComboBox()
        for rate in AUDIO_BITRATES:
            self.bitrate_combo.addItem(f"MP3 · {rate} kbps", rate)
        self.output_combo = QComboBox()
        for value, text in CONVERT_OUTPUT_MODES:
            self.output_combo.addItem(text, value)
        self.output_combo.addItem("Özel klasör…", CUSTOM)
        for combo in (self.format_combo, self.mode_combo, self.bitrate_combo, self.output_combo):
            combo.setFixedWidth(280)
        self.options_card.body.addLayout(self._row("Biçim", "MP4 görüntü ve sesi, MP3 yalnızca sesi içerir.",
                                                   self.format_combo))
        self.options_card.body.addWidget(divider())
        self.mode_row_widget = QWidget()
        mode_col = QVBoxLayout(self.mode_row_widget)
        mode_col.setContentsMargins(0, 0, 0, 0)
        mode_col.setSpacing(6)
        mode_col.addLayout(self._row("Kesim modu", "Kayıpsız: yeniden kodlama yok, çok hızlı. "
                                                   "Kare hassasiyetinde: tam seçilen kareden başlar.", self.mode_combo))
        self.keyframe_note = label("", "Muted", wrap=True)
        mode_col.addWidget(self.keyframe_note)
        self.options_card.body.addWidget(self.mode_row_widget)
        self.bitrate_row_widget = QWidget()
        bitrate_col = QVBoxLayout(self.bitrate_row_widget)
        bitrate_col.setContentsMargins(0, 0, 0, 0)
        bitrate_col.addLayout(self._row("Ses kalitesi", "MP3 bit hızı.", self.bitrate_combo))
        self.options_card.body.addWidget(self.bitrate_row_widget)
        self.options_card.body.addWidget(divider())
        self.options_card.body.addLayout(self._row("Çıktı konumu", "Aynı adlı dosya varsa numaralandırılır; "
                                                                    "kaynak dosya değişmez.", self.output_combo))
        self.output_label = ElidedLabel("", "Mono")
        self.options_card.body.addWidget(self.output_label)
        self.blocker = label("", "Muted", wrap=True)
        self.blocker.hide()
        self.options_card.body.addWidget(self.blocker)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.export_button = themed_button("Dışa aktar", "scissors", kind="Primary")
        actions.addWidget(self.export_button)
        self.options_card.body.addLayout(actions)
        self.options_card.hide()
        self.body.addWidget(self.options_card)

        # --- 4. Dışa aktarımlar --------------------------------------------
        self.exports_card = Card("Dışa aktarımlar", icon="layers")
        self.clear_button = self.exports_card.add_header_widget(themed_button("Bitenleri temizle", "trash", kind="Ghost"))
        self.exports_stack = QStackedWidget()
        self.exports_widget = QWidget()
        self.exports_layout = QVBoxLayout(self.exports_widget)
        self.exports_layout.setContentsMargins(0, 0, 0, 0)
        self.exports_layout.setSpacing(2)
        self.exports_empty = EmptyState("scissors", "Henüz kesim yok",
                                        "Dışa aktardığınız klipler burada sıralanır; ilerlemelerini buradan izleyebilirsiniz.")
        self.exports_stack.addWidget(self.exports_widget)
        self.exports_stack.addWidget(self.exports_empty)
        self.exports_card.body.addWidget(self.exports_stack)
        self.body.addWidget(self.exports_card)
        self.body.addStretch(1)

        self.rows: dict[int, ExportRow] = {}
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._refresh_previews)

        # --- Bağlantılar ----------------------------------------------------
        self.choose_button.clicked.connect(self.choose_file)
        self.change_button.clicked.connect(self.choose_file)
        self.slider.rangeChanged.connect(self._slider_changed)
        self.start_edit.editingFinished.connect(self._edits_changed)
        self.end_edit.editingFinished.connect(self._edits_changed)
        self.format_combo.currentIndexChanged.connect(self._update_options)
        self.mode_combo.currentIndexChanged.connect(self._update_options)
        self.output_combo.activated.connect(self._output_chosen)
        self.export_button.clicked.connect(self.export)
        self.clear_button.clicked.connect(lambda: self.manager.clear())
        self.manager.changed.connect(self.refresh_exports)
        self.manager.item_updated.connect(self._update_row)
        ctx.dependencies_changed.connect(lambda _r: self._update_options())
        ctx.settings_changed.connect(lambda _k: self._load_defaults())
        self._load_defaults()
        self.refresh_exports()
        self._update_options()

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

    # ------------------------------------------------------------------ yardımcılar
    def _tools(self) -> tuple[str, str] | None:
        try:
            return self.manager.tools()
        except MediaStudioError:
            return None

    def selection(self) -> tuple[float, float]:
        lo, hi = self.slider.values()
        return lo / 1000, hi / 1000

    def output_mode(self) -> str:
        return self.output_combo.currentData()

    # QComboBox, str tabanlı enum'ları düz metin olarak geri verir; her zaman enum'a çevrilir.
    def current_format(self) -> CutFormat:
        return CutFormat(self.format_combo.currentData())

    def current_mode(self) -> CutMode:
        return CutMode(self.mode_combo.currentData())

    def _load_defaults(self) -> None:
        self.bitrate_combo.setCurrentIndex(max(0, self.bitrate_combo.findData(self.ctx.settings.audio_bitrate)))
        if self.output_combo.currentData() != CUSTOM:
            self.output_combo.setCurrentIndex(max(0, self.output_combo.findData(self.ctx.settings.cut_output_mode)))
        self._update_output_label()

    def _update_output_label(self) -> None:
        mode = self.output_mode()
        if mode == CUSTOM and self._custom_dir:
            self.output_label.set_full_text(self._custom_dir)
        elif mode == "source":
            self.output_label.set_full_text(str(Path(self.details.path).parent) if self.details
                                            else "Kaynak dosyanın klasörüne kaydedilir.")
        else:
            self.output_label.set_full_text(str(self.ctx.settings_manager.download_dir()))

    def _output_chosen(self, _index: int) -> None:
        mode = self.output_mode()
        if mode == CUSTOM:
            path = QFileDialog.getExistingDirectory(self, "Kliplerin kaydedileceği klasör",
                                                    self._custom_dir or str(self.ctx.settings_manager.download_dir()))
            if path:
                self._custom_dir = str(Path(path))
            elif not self._custom_dir:
                self.output_combo.setCurrentIndex(self.output_combo.findData(self.ctx.settings.cut_output_mode))
        else:
            self.ctx.update_settings(cut_output_mode=mode)
        self._update_output_label()

    def output_dir(self) -> str:
        mode = self.output_mode()
        if mode == CUSTOM and self._custom_dir:
            return self._custom_dir
        if mode == "source" and self.details:
            return str(Path(self.details.path).parent)
        return str(self.ctx.settings_manager.download_dir())

    # ------------------------------------------------------------------ dosya yükleme
    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Kesilecek dosyayı seçin", "", FILE_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        tools = self._tools()
        if tools is None:
            self.ctx.notifications.error("Dosya açılamadı", "Kesme için FFmpeg ve FFprobe gerekli.")
            return
        self._probe_request = next(self._requests)
        self.source_status.setText(f"{Path(path).name} inceleniyor…")
        self.source_status.setStyleSheet("")
        self.source_status.show()
        worker = Worker(_probe_job, self._probe_request, tools[1], str(Path(path)))
        worker.signals.result.connect(self._on_probed)
        self.ctx.thread_pool.start(worker)

    @Slot(object)
    def _on_probed(self, result: tuple) -> None:
        request, details, exc = result
        if request != self._probe_request:
            return
        self._probe_request = 0
        if exc is not None:
            message = exc.user_message if isinstance(exc, MediaStudioError) else "Dosya açılamadı."
            self.source_status.setText(message)
            self.source_status.setStyleSheet(f"color: {theme.color('danger')};")
            self.source_status.setToolTip(getattr(exc, "detail", "") or "")
            return
        self.source_status.hide()
        self.set_details(details)

    def set_details(self, details: MediaDetails) -> None:
        self.details = details
        self.keyframe = None
        self._keyframe_for = None
        self.file_badge.set_icon("video" if details.has_video else "music")
        self.file_name.set_full_text(details.name)
        meta = [human_duration(details.duration)]
        if details.has_video:
            meta.append(f"{details.width}×{details.height}")
            if details.fps:
                meta.append(f"{details.fps:.3g} fps".replace(".", ","))
        codecs = [CODEC_NAMES.get(c, c.upper()) for c in (details.video_codec, details.audio_codec) if c]
        if codecs:
            meta.append(" / ".join(codecs))
        if details.size:
            meta.append(human_size(details.size))
        self.file_meta.setText(" · ".join(meta))
        self.picker.hide()
        self.loaded_widget.show()

        self.slider.blockSignals(True)
        self.slider.setMaximum(int(details.duration * 1000))
        self.slider.setRange(0, int(details.duration * 1000), emit=False)
        self.slider.blockSignals(False)

        model = self.format_combo.model()
        model.item(self.format_combo.findData(CutFormat.MP4)).setEnabled(details.has_video)
        model.item(self.format_combo.findData(CutFormat.MP3)).setEnabled(details.has_audio)
        self.format_combo.setCurrentIndex(self.format_combo.findData(CutFormat.MP4 if details.has_video
                                                                     else CutFormat.MP3))
        lossless_ok = details.lossless_mp4_possible
        self.mode_combo.model().item(self.mode_combo.findData(CutMode.LOSSLESS)).setEnabled(lossless_ok)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(CutMode.LOSSLESS if lossless_ok else CutMode.PRECISE))

        self.start_preview.setVisible(details.has_video)
        self.end_preview.setVisible(details.has_video)
        self.range_card.show()
        self.options_card.show()
        self._sync_edits()
        self._update_output_label()
        self._update_options()
        self._schedule_previews()

    # Sürükle-bırak (tek dosya)
    def _dropped_path(self, event) -> str | None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if len(paths) != 1 or Path(paths[0]).suffix.lower() not in SUPPORTED:
            return None
        return paths[0]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._dropped_path(event):
            event.acceptProposedAction()
            self.picker.setProperty("active", True)
            repolish(self.picker)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.picker.setProperty("active", False)
        repolish(self.picker)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.picker.setProperty("active", False)
        repolish(self.picker)
        path = self._dropped_path(event)
        if path:
            event.acceptProposedAction()
            self.load_file(path)

    # ------------------------------------------------------------------ aralık
    def _sync_edits(self) -> None:
        start, end = self.selection()
        self.start_edit.setText(format_timecode(start))
        self.end_edit.setText(format_timecode(end))
        self.length_label.setText(format_timecode(end - start))
        self.start_preview.set_time(start)
        self.end_preview.set_time(end)

    def _slider_changed(self, _lo: int, _hi: int) -> None:
        self.range_error.hide()
        self._sync_edits()
        self._update_options()
        self._schedule_previews()

    def _edits_changed(self) -> None:
        if self.details is None:
            return
        try:
            start = parse_timecode(self.start_edit.text())
            end = parse_timecode(self.end_edit.text())
        except ValueError as exc:
            self._show_range_error(str(exc))
            return
        if end > self.details.duration + 0.001:
            self._show_range_error(f"Bitiş, videonun süresini ({format_timecode(self.details.duration)}) aşıyor.")
            return
        if end - start < 0.1:
            self._show_range_error("Bitiş, başlangıçtan en az 0,1 saniye sonra olmalı.")
            return
        self.range_error.hide()
        self.slider.setRange(int(round(start * 1000)), int(round(end * 1000)))
        self._sync_edits()

    def _show_range_error(self, message: str) -> None:
        self.range_error.setText(message)
        self.range_error.show()
        self._update_options()

    def set_selection(self, start: float, end: float) -> None:
        self.start_edit.setText(format_timecode(start))
        self.end_edit.setText(format_timecode(end))
        self._edits_changed()

    # ------------------------------------------------------------------ önizleme ve anahtar kare
    def _schedule_previews(self) -> None:
        if self.details is not None:
            if self.details.has_video:
                self.start_preview.clear(loading=True)
                self.end_preview.clear(loading=True)
            self._preview_timer.start()

    def _refresh_previews(self) -> None:
        tools = self._tools()
        if self.details is None or tools is None:
            return
        start, end = self.selection()
        if self.details.has_video:
            for which, t in (("start", start), ("end", max(end - 0.05, 0))):
                self._frame_requests[which] = next(self._requests)
                worker = Worker(_frame_job, self._frame_requests[which], which, tools[0], self.details.path, t)
                worker.signals.result.connect(self._on_frame)
                self.ctx.thread_pool.start(worker)
            if self.details.lossless_mp4_possible and self._keyframe_for != start:
                self._keyframe_request = next(self._requests)
                self.keyframe_note.setText("Anahtar kare konumu hesaplanıyor…")
                worker = Worker(_keyframe_job, self._keyframe_request, tools[1], self.details.path, start)
                worker.signals.result.connect(self._on_keyframe)
                self.ctx.thread_pool.start(worker)

    @Slot(object)
    def _on_frame(self, result: tuple) -> None:
        request, which, data = result
        if request != self._frame_requests.get(which):
            return
        (self.start_preview if which == "start" else self.end_preview).set_frame(data)

    @Slot(object)
    def _on_keyframe(self, result: tuple) -> None:
        request, t, keyframe = result
        if request != self._keyframe_request:
            return
        self._keyframe_for = t
        self.keyframe = keyframe
        self._update_options()

    # ------------------------------------------------------------------ seçenekler
    def _update_options(self) -> None:
        fmt = self.current_format()
        mode = self.current_mode()
        is_mp4 = fmt is CutFormat.MP4
        self.mode_row_widget.setVisible(is_mp4)
        self.bitrate_row_widget.setVisible(not is_mp4)

        note = ""
        if self.details is not None and is_mp4:
            start, _end = self.selection()
            if not self.details.lossless_mp4_possible:
                note = ("Bu dosyanın kodekleri MP4'e kayıpsız kopyalanamıyor; kesim yeniden kodlanarak "
                        "(kare hassasiyetinde) yapılır.")
            elif mode is CutMode.LOSSLESS:
                if self._keyframe_for != start:
                    note = "Anahtar kare konumu hesaplanıyor…"
                elif self.keyframe is None:
                    note = "Anahtar kare bulunamadı; kesim seçilen andan başlatılacak."
                elif start - self.keyframe <= KEYFRAME_TOLERANCE:
                    note = "Başlangıç bir anahtar kareye denk geliyor: kesim kayıpsız ve tam bu kareden başlar."
                else:
                    gap = f"{start - self.keyframe:.2f}".replace(".", ",")
                    note = (f"Kayıpsız kesim, {format_timecode(self.keyframe)} anındaki anahtar kareden başlar "
                            f"(seçimden {gap} sn önce). Tam kare için 'Kare hassasiyetinde' modunu seçin.")
            else:
                note = "Kesim tam seçilen kareden başlar; görüntü yüksek kalitede (H.264, CRF 18) yeniden kodlanır."
        self.keyframe_note.setText(note)
        self.keyframe_note.setVisible(bool(note))

        reason = ""
        if self._tools() is None:
            reason = "Kesme için FFmpeg ve FFprobe gerekli. Ayarlar → Sistem bileşenleri."
        self.blocker.setText(reason)
        self.blocker.setVisible(bool(reason))
        self.choose_button.setEnabled(not reason)
        self.export_button.setEnabled(self.details is not None and not reason and self.range_error.isHidden())

    # ------------------------------------------------------------------ dışa aktarma
    def export(self) -> None:
        if self.details is None:
            return
        if self.output_mode() == CUSTOM and not self._custom_dir:
            self.ctx.notifications.warning("Klasör seçilmedi", "Özel çıktı klasörünü seçin.")
            return
        start, end = self.selection()
        fmt = self.current_format()
        mode = self.current_mode()
        keyframe = self.keyframe if self._keyframe_for == start else None
        try:
            item = self.manager.export(self.details, start, end, fmt, mode=mode,
                                       bitrate=int(self.bitrate_combo.currentData()),
                                       output_dir=self.output_dir(), keyframe=keyframe)
        except MediaStudioError as exc:
            self.ctx.notifications.exception(exc, "Kesim başlatılamadı")
            return
        self.ctx.notifications.info("Kesim kuyruğa eklendi",
                                    f"{item.name} · {format_timecode(start)} – {format_timecode(end)}")

    # ------------------------------------------------------------------ liste
    def refresh_exports(self) -> None:
        items = self.manager.list()
        if [i.id for i in items] != list(self.rows):
            while self.exports_layout.count():
                entry = self.exports_layout.takeAt(0)
                if entry.widget():
                    entry.widget().deleteLater()
            self.rows = {i.id: ExportRow(i, self) for i in items}
            for row in self.rows.values():
                self.exports_layout.addWidget(row)
            self.exports_layout.addStretch(1)
        else:
            for item in items:
                self.rows[item.id].update_from(item)
        self.exports_stack.setCurrentWidget(self.exports_widget if items else self.exports_empty)
        self.clear_button.setEnabled(any(not i.status.is_active for i in items))

    def _update_row(self, item_id: int) -> None:
        row, item = self.rows.get(item_id), self.manager.items.get(item_id)
        if row is not None and item is not None:
            row.update_from(item)

    def reveal(self, item_id: int) -> None:
        item = self.manager.items.get(item_id)
        if item and not reveal_in_explorer(item.output_path):
            self.ctx.notifications.warning("Dosya bulunamadı", "Çıktı dosyası taşınmış veya silinmiş olabilir.")

    def show_details(self, item_id: int) -> None:
        item = self.manager.items.get(item_id)
        if item:
            ErrorDetailsDialog("Kesme hatası", f"{item.name}: {item.error}", item.error_details, self).exec()
