"""Video İndir ve Ses İndir sayfaları.

Akış: bağlantı → Analiz et (arka planda) → bilgi + kalite seçimi → "indirme hakkım var" onayı →
Kuyruğa ekle. İndirmenin kendisi DownloadManager tarafından kuyrukta yürütülür.
"""

from __future__ import annotations

import itertools
import logging
import re
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from app.core.dependencies import DependencyReport
from app.core.errors import MediaStudioError
from app.core.workers import Worker
from app.models.history import OperationKind
from app.models.media import MediaInfo
from app.models.settings import AUDIO_BITRATES, VIDEO_QUALITIES
from app.services.ytdlp_support import analyze
from app.ui.pages.base import BasePage
from app.ui.theme import icons, theme
from app.ui.theme.tokens import rgba
from app.ui.widgets.cards import Card
from app.ui.widgets.common import ElidedLabel, StatusPill, divider, label, themed_button
from app.ui.widgets.responsive import AdaptiveRow
from app.ui.widgets.range_slider import RangeSlider
from app.ui.widgets.toggle import ToggleSwitch
from app.utils.formatting import human_duration, human_size
from app.utils.timecode import format_timecode, parse_flexible_timecode

log = logging.getLogger(__name__)

DOWNLOAD_NOTICE = ("Yalnızca indirme hakkınız olan içerikleri indirin: kendi içerikleriniz, açık lisanslı "
                   "içerikler veya sahibinin indirmeye izin verdiği içerikler. DRM korumalı, ücretli, giriş "
                   "gerektiren ya da bölgesel olarak kısıtlanmış içerikler desteklenmez ve bu kısıtlamalar aşılmaz.")
THUMB_W, THUMB_H = 224, 126
AUTO_ANALYZE_DELAY_MS = 600
_URL_LIKE = re.compile(r"^https?://[^\s/]+\.[^\s]+$", re.IGNORECASE)
MAX_THUMB_BYTES = 3 * 1024 * 1024


def fetch_thumbnail(url: str) -> bytes:
    """Küçük resmi indirir (yalnızca http/https, boyut ve süre sınırlı). Arka planda çağrılır."""
    if not url.lower().startswith(("http://", "https://")):
        return b""
    request = urllib.request.Request(url, headers={"User-Agent": "MediaStudio/0.2"})
    with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - şema yukarıda denetlendi
        return response.read(MAX_THUMB_BYTES + 1)[:MAX_THUMB_BYTES]


def _analysis_job(request: int, url: str) -> tuple:
    try:
        return request, analyze(url), None
    except Exception as exc:  # noqa: BLE001 - sonuç ana iş parçacığında işlenir
        return request, None, exc


def _thumbnail_job(request: int, url: str) -> tuple:
    try:
        return request, fetch_thumbnail(url)
    except Exception as exc:  # noqa: BLE001 - küçük resim isteğe bağlı
        log.debug("Küçük resim alınamadı %s: %s", url, exc)
        return request, b""


class DownloadPage(BasePage):
    _request_ids = itertools.count(1)

    def __init__(self, ctx, navigate, kind: OperationKind) -> None:
        audio = kind is OperationKind.AUDIO_DOWNLOAD
        super().__init__(ctx, navigate, "Ses İndir" if audio else "Video İndir",
                         "İzin verilen kaynaklardan yalnızca sesi MP3 olarak indirin." if audio
                         else "İzin verilen kaynaklardan MP4 video indirin; ayrı akışlar otomatik birleştirilir.")
        self.key = kind.value
        self.kind = kind
        self.audio = audio
        self.media: MediaInfo | None = None
        self._request = 0
        self._analyzing_url = ""
        self._analysis_started = 0.0
        self._custom_dir: str | None = None

        self.queue_link = self.header.add_action(themed_button("Kuyruğu aç", "layers", kind="Link"))
        self.queue_link.clicked.connect(lambda: navigate("queue"))

        # --- 1. Bağlantı ----------------------------------------------------
        source = Card("Bağlantı", "İndirmek istediğiniz içeriğin sayfa bağlantısını yapıştırın.", icon="link")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://…")
        self.url_edit.setClearButtonEnabled(True)
        self.paste_button = themed_button("Yapıştır", "copy")
        self.analyze_button = themed_button("Analiz et", "search", kind="Primary")
        row.addWidget(self.url_edit, 1)
        row.addWidget(self.paste_button)
        row.addWidget(self.analyze_button)
        source.body.addLayout(row)
        self.analyze_status = label("", "Faint", wrap=True)
        self.analyze_status.hide()
        source.body.addWidget(self.analyze_status)
        self.body.addWidget(source)

        # --- 2. Analiz sonucu -----------------------------------------------
        self.result_card = Card(icon="video" if not audio else "music")
        result = AdaptiveRow(breakpoint=620, spacing=18)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.bind(self.thumb, self._thumb_placeholder)
        result.add(self.thumb, 0)
        info = QWidget()
        info_col = QVBoxLayout(info)
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(6)
        self.media_title = label("", "CardTitle", wrap=True)
        self.media_meta = label("", "Muted", wrap=True)
        self.media_url = ElidedLabel("", "Faint")
        self.quality_pills = QHBoxLayout()
        self.quality_pills.setSpacing(6)
        info_col.addWidget(self.media_title)
        info_col.addWidget(self.media_meta)
        info_col.addWidget(self.media_url)
        info_col.addLayout(self.quality_pills)
        info_col.addStretch(1)
        result.add(info, 1)
        self.result_card.body.addWidget(result)
        self.result_card.hide()
        self.body.addWidget(self.result_card)

        # --- 3. Seçenekler ve başlat ----------------------------------------
        self.options_card = Card("İndirme seçenekleri", icon="sliders")
        self.quality_combo = QComboBox()
        self.quality_combo.setFixedWidth(230)
        if audio:
            for rate in AUDIO_BITRATES:
                self.quality_combo.addItem(f"MP3 · {rate} kbps", rate)
            title, desc = "Ses kalitesi", "MP3 bit hızı. Kaynaktaki en iyi ses akışı kullanılır."
        else:
            title, desc = "Video kalitesi", "Seçilen çözünürlük yoksa en yakın düşük kalite indirilir."
        self.options_card.body.addLayout(self._row(title, desc, self.quality_combo))
        self.options_card.body.addWidget(divider())

        # İndirilecek kısım: tümü veya yalnızca bir zaman aralığı
        segmented = QFrame()
        segmented.setObjectName("Segmented")
        seg_row = QHBoxLayout(segmented)
        seg_row.setContentsMargins(3, 3, 3, 3)
        seg_row.setSpacing(2)
        self.part_all = QPushButton("Tümü")
        self.part_range = QPushButton("Zaman aralığı")
        self.part_group = QButtonGroup(self)
        for button in (self.part_all, self.part_range):
            button.setObjectName("Segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.part_group.addButton(button)
            seg_row.addWidget(button)
        self.part_all.setChecked(True)
        self.options_card.body.addLayout(self._row(
            "İndirilecek kısım", "Yalnızca bir bölüm istiyorsanız zaman aralığını seçin; sadece o kısım indirilir.",
            segmented))
        self.range_panel = QWidget()
        range_col = QVBoxLayout(self.range_panel)
        range_col.setContentsMargins(0, 4, 0, 0)
        range_col.setSpacing(10)
        self.range_slider = RangeSlider(min_gap_ms=1000)
        range_col.addWidget(self.range_slider)
        fields = QHBoxLayout()
        fields.setSpacing(10)
        self.range_start = QLineEdit()
        self.range_end = QLineEdit()
        for edit, name in ((self.range_start, "Başlangıç"), (self.range_end, "Bitiş")):
            edit.setFixedWidth(130)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setPlaceholderText("ör. 5.37")
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
        self.range_length = label("", "CardTitle")
        length_box.addWidget(self.range_length)
        fields.addLayout(length_box)
        fields.addStretch(1)
        range_col.addLayout(fields)
        range_col.addWidget(label("Dakika.saniye biçiminde yazabilirsiniz: 5.37 = 5 dk 37 sn, 1.05.37 = 1 sa 5 dk 37 sn. "
                                  "Kesim tam seçtiğiniz kareden başlar.", "Faint", wrap=True))
        self.range_error = label("", "Muted", wrap=True)
        self.range_error.hide()
        range_col.addWidget(self.range_error)
        self.range_panel.hide()
        self.options_card.body.addWidget(self.range_panel)
        self.options_card.body.addWidget(divider())

        self.folder_label = ElidedLabel("", "Mono")
        self.folder_button = themed_button("Değiştir…", "folder")
        self.options_card.body.addLayout(self._row(
            "Kayıt klasörü", "Dosya adı başlıktan oluşturulur; aynı adlı dosya varsa numaralandırılır.",
            self.folder_button))
        self.options_card.body.addWidget(self.folder_label)
        self.options_card.body.addWidget(divider())

        self.rights_toggle = ToggleSwitch()
        self.options_card.body.addLayout(self._row(
            "Bu içeriği indirme hakkım var",
            "Kendi içeriğim, açık lisanslı bir içerik veya sahibinin indirmeye izin verdiği bir içerik.",
            self.rights_toggle))

        self.blocker = label("", "Muted", wrap=True)
        self.blocker.hide()
        self.options_card.body.addWidget(self.blocker)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.added_label = label("", "Faint", wrap=True)
        self.reset_button = themed_button("Temizle", "rotate-ccw", kind="Ghost")
        self.start_button = themed_button("Kuyruğa ekle ve indir", "download", kind="Primary")
        actions.addWidget(self.added_label, 1)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.start_button)
        self.options_card.body.addLayout(actions)
        self.body.addWidget(self.options_card)

        notice = Card("Kullanım koşulu", object_name="NoticeCard", shadow=False, icon="shield-check")
        notice.body.addWidget(label(DOWNLOAD_NOTICE, "Muted", wrap=True))
        self.body.addWidget(notice)
        self.body.addStretch(1)

        # --- Bağlantılar ----------------------------------------------------
        self.url_edit.textChanged.connect(self._url_changed)
        self.url_edit.returnPressed.connect(self.start_analysis)
        self.paste_button.clicked.connect(self.paste)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.folder_button.clicked.connect(self.choose_folder)
        self.rights_toggle.toggled.connect(lambda _v: self._update_state())
        self.part_all.clicked.connect(lambda: self.set_ranged(False))
        self.part_range.clicked.connect(lambda: self.set_ranged(True))
        self.range_slider.rangeChanged.connect(self._range_slider_changed)
        self.range_start.editingFinished.connect(self._range_edits_changed)
        self.range_end.editingFinished.connect(self._range_edits_changed)
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(AUTO_ANALYZE_DELAY_MS)
        self._auto_timer.timeout.connect(self._auto_analyze)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._analysis_tick)
        self.reset_button.clicked.connect(self.reset)
        self.start_button.clicked.connect(self.start_download)
        ctx.dependencies_changed.connect(lambda _r: self._update_state())
        ctx.settings_changed.connect(lambda _k: self._load_defaults())
        ctx.queue.changed.connect(self._update_queue_link)

        self._load_defaults()
        self._update_state()
        self._update_queue_link()

    # ------------------------------------------------------------------ yardımcılar
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

    def _thumb_placeholder(self) -> None:
        if self.thumb.property("loaded"):
            return
        self.thumb.setStyleSheet(f"background: {rgba(theme.color('primary'), 0.10)};"
                                 f"border: 1px solid {theme.color('border')}; border-radius: 12px;")
        self.thumb.setPixmap(icons.pixmap("music" if self.audio else "video", theme.color("text_3"), 36))

    def _set_status(self, text: str, tone: str = "Faint") -> None:
        self.analyze_status.setObjectName(tone)
        self.analyze_status.style().unpolish(self.analyze_status)
        self.analyze_status.style().polish(self.analyze_status)
        self.analyze_status.setText(text)
        self.analyze_status.setVisible(bool(text))
        if tone == "Danger":
            self.analyze_status.setStyleSheet(f"color: {theme.color('danger')};")
        else:
            self.analyze_status.setStyleSheet("")

    def output_dir(self) -> str:
        return self._custom_dir or str(self.ctx.settings_manager.download_dir())

    def _load_defaults(self) -> None:
        if self.audio:
            self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(self.ctx.settings.audio_bitrate)))
        elif self.media is None:
            self._fill_video_qualities(())
        self.folder_label.set_full_text(self.output_dir())

    def _fill_video_qualities(self, heights: tuple[int, ...]) -> None:
        default = self.ctx.settings.video_quality
        self.quality_combo.clear()
        if heights:
            self.quality_combo.addItem(f"En yüksek kalite ({heights[0]}p)", "best")
            for h in heights:
                self.quality_combo.addItem(f"{h}p", str(h))
        else:
            for value, text in VIDEO_QUALITIES:
                self.quality_combo.addItem(text, value)
        index = self.quality_combo.findData(default)
        if index < 0 and default != "best" and heights:  # tercih edilen kalite yoksa altındaki en yakını
            lower = [h for h in heights if h <= int(default)]
            index = self.quality_combo.findData(str(lower[0])) if lower else 0
        self.quality_combo.setCurrentIndex(max(0, index))

    def _update_queue_link(self) -> None:
        active = self.ctx.queue.active_count() + self.ctx.queue.pending_count()
        self.queue_link.setText(f"Kuyruğu aç ({active} etkin)" if active else "Kuyruğu aç")

    def _blocking_reason(self) -> str:
        deps: DependencyReport | None = self.ctx.dependencies
        if deps is None:
            return "Sistem bileşenleri kontrol ediliyor…"
        if deps.ytdlp is None:
            return "yt-dlp bulunamadı; indirme yapılamaz."
        if deps.ffmpeg is None:
            return ("FFmpeg bulunamadı; MP3 dönüştürme için gerekli." if self.audio
                    else "FFmpeg bulunamadı; görüntü ve sesi birleştirmek için gerekli.") + \
                " Ayarlar → Sistem bileşenleri."
        return ""

    def _update_state(self) -> None:
        reason = self._blocking_reason()
        self.blocker.setText(reason)
        self.blocker.setVisible(bool(reason))
        self.analyze_button.setEnabled(bool(self.url_edit.text().strip()) and not self._request
                                       and (self.ctx.dependencies is None or self.ctx.dependencies.ytdlp is not None))
        range_ok = not self.is_ranged() or self.range_error.isHidden()
        ready = self.media is not None and self.rights_toggle.isChecked() and not reason and range_ok
        self.start_button.setEnabled(ready)
        section = self.selected_section()
        self.start_button.setText(f"Aralığı indir ({format_timecode(section[1] - section[0], millis=False)[3:]})"
                                  if section else "Kuyruğa ekle ve indir")
        if not range_ok:
            self.start_button.setToolTip(self.range_error.text())
        elif self.media is None:
            self.start_button.setToolTip("Önce bağlantıyı analiz edin.")
        elif not self.rights_toggle.isChecked():
            self.start_button.setToolTip("İndirme hakkınız olduğunu onaylayın.")
        else:
            self.start_button.setToolTip(reason)

    # ------------------------------------------------------------------ eylemler
    def paste(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)
            self.start_analysis()
        else:
            self._set_status("Panoda metin yok.", "Faint")

    def _url_changed(self) -> None:
        if self.media is not None and self.url_edit.text().strip() != self.media.url:
            self.media = None
            self.result_card.hide()
            if not self.audio:
                self._fill_video_qualities(())
        self.added_label.setText("")
        url = self.url_edit.text().strip()
        if _URL_LIKE.match(url) and (self.media is None or url != self.media.url) and url != self._analyzing_url:
            self._auto_timer.start()  # yazmayı/yapıştırmayı bitirince kendiliğinden analiz et
        else:
            self._auto_timer.stop()
        self._update_state()

    def _auto_analyze(self) -> None:
        url = self.url_edit.text().strip()
        if _URL_LIKE.match(url) and url != self._analyzing_url and (self.media is None or url != self.media.url):
            self.start_analysis()

    def _analysis_tick(self) -> None:
        if self._request:
            seconds = int(time.monotonic() - self._analysis_started)
            self._set_status(f"Bağlantı analiz ediliyor… {seconds} sn")

    def start_analysis(self) -> None:
        url = self.url_edit.text().strip()
        self._auto_timer.stop()
        if not url or (self._request and url == self._analyzing_url):
            return
        # Yeni bağlantı, süren eski analizin yerini alır (eski sonuç istek numarasıyla yok sayılır)
        self._request = next(self._request_ids)
        self._analyzing_url = url
        self._analysis_started = time.monotonic()
        self.media = None
        self.result_card.hide()
        self.analyze_button.setText("Analiz ediliyor…")
        self._set_status("Bağlantı analiz ediliyor…")
        self._elapsed_timer.start()
        self._update_state()
        # Sonuç istek numarasıyla döner; sayfanın kendi slotuna bağlandığı için Qt onu ana iş
        # parçacığına kuyruklar (arayüz arka plandan güncellenmez). Eski isteklerin sonucu yok sayılır.
        worker = Worker(_analysis_job, self._request, url)
        worker.signals.result.connect(self._on_analysis_done)
        self.ctx.thread_pool.start(worker)

    @Slot(object)
    def _on_analysis_done(self, result: tuple) -> None:
        request, info, exc = result
        if exc is not None:
            self._on_analyze_failed(request, exc)
        else:
            self._on_analyzed(request, info)

    def _finish_analysis(self) -> None:
        self._request = 0
        self._analyzing_url = ""
        self._elapsed_timer.stop()
        self.analyze_button.setText("Analiz et")

    def _on_analyzed(self, request: int, info: MediaInfo) -> None:
        if request != self._request:
            return
        took = time.monotonic() - self._analysis_started
        self._finish_analysis()
        log.info("Analiz %.1f sn sürdü: %s", took, info.url)
        if self.url_edit.text().strip() != info.url:  # kullanıcı bu arada bağlantıyı değiştirdi
            self._set_status("")
            self._update_state()
            return
        self.media = info
        self._set_status("")
        self._show_media(info)
        self._update_state()

    def _on_analyze_failed(self, request: int, exc: BaseException) -> None:
        if request != self._request:
            return
        self._finish_analysis()
        message = exc.user_message if isinstance(exc, MediaStudioError) else (str(exc) or "Analiz başarısız.")
        self._set_status(message, "Danger")
        detail = getattr(exc, "detail", "") or ""
        if detail and detail != message:
            self.analyze_status.setToolTip(detail)
        self._update_state()

    def _show_media(self, info: MediaInfo) -> None:
        self.media_title.setText(info.title)
        meta = [info.extractor or "Kaynak"]
        if info.uploader:
            meta.append(info.uploader)
        if info.duration:
            meta.append(human_duration(info.duration))
        if info.filesize_approx:
            meta.append(f"~{human_size(info.filesize_approx)}")
        self.media_meta.setText(" · ".join(meta))
        self.media_url.set_full_text(info.webpage_url)
        while self.quality_pills.count():
            item = self.quality_pills.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self.audio:
            for h in info.heights[:6]:
                self.quality_pills.addWidget(StatusPill(f"{h}p", "accent" if h == info.best_height else "neutral"))
            if not info.heights:
                self.quality_pills.addWidget(StatusPill("Tek dosya · kalite bilgisi yok", "neutral"))
            self._fill_video_qualities(info.heights)
        if not info.has_audio and self.audio:
            self.quality_pills.addWidget(StatusPill("Ses akışı bulunamadı", "warning"))
        self.quality_pills.addStretch(1)
        self._configure_range(info)
        self.thumb.setProperty("loaded", False)
        self._thumb_placeholder()
        if info.thumbnail:
            request = self._request_thumb = next(self._request_ids)
            worker = Worker(_thumbnail_job, request, info.thumbnail)
            worker.signals.result.connect(self._on_thumbnail)
            self.ctx.thread_pool.start(worker)
        self.result_card.show()

    @Slot(object)
    def _on_thumbnail(self, result: tuple) -> None:
        request, data = result
        if request != getattr(self, "_request_thumb", None) or not data:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        ratio = self.devicePixelRatioF()
        scaled = pixmap.scaled(int(THUMB_W * ratio), int(THUMB_H * ratio), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.thumb.setProperty("loaded", True)
        self.thumb.setStyleSheet("border-radius: 12px;")
        self.thumb.setPixmap(scaled)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Bu indirme için kayıt klasörü", self.output_dir())
        if path:
            self._custom_dir = str(Path(path))
            self.folder_label.set_full_text(self._custom_dir)

    def reset(self) -> None:
        self._finish_analysis()
        self._auto_timer.stop()
        self.url_edit.clear()
        self.set_ranged(False)
        self.media = None
        self.result_card.hide()
        self._custom_dir = None
        self._set_status("")
        self.added_label.setText("")
        self._load_defaults()
        self._update_state()

    def start_download(self) -> None:
        if self.media is None or not self.rights_toggle.isChecked():
            return
        options = {"audio_bitrate": self.quality_combo.currentData()} if self.audio \
            else {"quality": self.quality_combo.currentData()}
        try:
            task = self.ctx.downloads.enqueue(self.media.url, self.kind, title=self.media.title,
                                              output_dir=self.output_dir(), section=self.selected_section(),
                                              media=self.media, **options)
        except MediaStudioError as exc:
            self.ctx.notifications.exception(exc, "İndirme başlatılamadı")
            return
        self.ctx.notifications.info("Kuyruğa eklendi", task.display_title)
        title = self.media.title
        self.url_edit.blockSignals(True)
        self.url_edit.clear()
        self.url_edit.blockSignals(False)
        self.media = None
        self.result_card.hide()
        span = ""
        if task.has_section:
            span = f" ({format_timecode(task.section_start, millis=False)}–{format_timecode(task.section_end, millis=False)})"
        self.added_label.setText(f"“{title}”{span} kuyruğa eklendi.")
        self._update_state()
        self.url_edit.setFocus()

    # ------------------------------------------------------------------ zaman aralığı
    def is_ranged(self) -> bool:
        return self.part_range.isChecked()

    def selected_section(self) -> tuple[float, float] | None:
        if not self.is_ranged() or self.media is None:
            return None
        lo, hi = self.range_slider.values()
        return lo / 1000, hi / 1000

    def _configure_range(self, info: MediaInfo) -> None:
        """Analiz sonucu geldiğinde aralık seçiciyi videonun süresine göre hazırlar."""
        duration = info.duration or 0
        self.part_range.setEnabled(duration >= 2)
        self.part_range.setToolTip("" if duration >= 2 else
                                   "Bu kaynak süre bilgisi vermiyor; yalnızca tamamı indirilebilir.")
        if duration < 2 and self.is_ranged():
            self.set_ranged(False)
        if duration >= 2:
            self.range_slider.blockSignals(True)
            self.range_slider.setMaximum(int(duration * 1000))
            self.range_slider.setRange(0, int(duration * 1000), emit=False)
            self.range_slider.blockSignals(False)
            self.range_error.hide()
            self._sync_range_edits()

    def set_ranged(self, ranged: bool) -> None:
        if ranged and not self.part_range.isEnabled():
            ranged = False
        (self.part_range if ranged else self.part_all).setChecked(True)
        self.range_panel.setVisible(ranged)
        if ranged:
            self.range_start.setFocus()
        self._update_state()

    def set_section(self, start: str, end: str) -> None:
        """Zaman alanlarını yazılı metinle doldurur (ör. '5.37', '6.48')."""
        self.range_start.setText(start)
        self.range_end.setText(end)
        self._range_edits_changed()

    def _sync_range_edits(self) -> None:
        lo, hi = self.range_slider.values()
        self.range_start.setText(format_timecode(lo / 1000, millis=False))
        self.range_end.setText(format_timecode(hi / 1000, millis=False))
        self.range_length.setText(format_timecode((hi - lo) / 1000, millis=False))

    def _range_slider_changed(self, _lo: int, _hi: int) -> None:
        self.range_error.hide()
        self._sync_range_edits()
        self._update_state()

    def _range_edits_changed(self) -> None:
        if self.media is None or not self.media.duration:
            return
        try:
            start = parse_flexible_timecode(self.range_start.text())
            end = parse_flexible_timecode(self.range_end.text())
        except ValueError as exc:
            return self._show_range_error(str(exc))
        if end > self.media.duration + 0.5:
            return self._show_range_error(
                f"Bitiş, videonun süresini ({format_timecode(self.media.duration, millis=False)}) aşıyor.")
        if end - start < 1:
            return self._show_range_error("Bitiş, başlangıçtan en az 1 saniye sonra olmalı.")
        self.range_error.hide()
        self.range_slider.setRange(int(round(start * 1000)), int(round(min(end, self.media.duration) * 1000)))
        self._sync_range_edits()
        self._update_state()

    def _show_range_error(self, message: str) -> None:
        self.range_error.setText(message)
        self.range_error.setStyleSheet(f"color: {theme.color('danger')};")
        self.range_error.show()
        self._update_state()
