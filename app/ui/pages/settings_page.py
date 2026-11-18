"""Ayarlar: kayıt konumu, indirme varsayılanları, görünüm (tema), sistem bileşenleri, gelişmiş.

Her değişiklik anında doğrulanır ve kalıcı olarak kaydedilir (ayrı "Kaydet" düğmesi yoktur).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from app.core.dependencies import FFMPEG_EXE, SOURCE_LABELS, DependencyReport
from app.models.settings import ACCENTS, AUDIO_BITRATES, MAX_CONCURRENT_RANGE, VIDEO_QUALITIES
from app.ui.pages.base import BasePage
from app.ui.theme.tokens import ACCENT_PRESETS
from app.ui.widgets.cards import Card
from app.ui.widgets.common import ElidedLabel, IconBadge, StatusPill, divider, label, repolish, themed_button
from app.ui.widgets.dialogs import confirm
from app.ui.widgets.responsive import ResponsiveGrid
from app.ui.widgets.toggle import ToggleSwitch
from app.utils.paths import default_download_dir, logs_dir, user_data_dir
from app.utils.system import is_writable_dir, open_folder

log = logging.getLogger(__name__)


def setting_row(title: str, description: str, control: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(16)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    texts.addWidget(label(title))
    texts.addWidget(label(description, "Faint", wrap=True))
    row.addLayout(texts, 1)
    row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class AccentSwatch(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("Swatch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(name)
        self.setMinimumHeight(74)
        preset = ACCENT_PRESETS[key]
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 8)
        col.setSpacing(6)
        bar = QFrame()
        bar.setFixedHeight(26)
        bar.setStyleSheet("border-radius: 7px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                          f"stop:0 {preset['primary']}, stop:1 {preset['secondary']});")
        col.addWidget(bar)
        col.addWidget(label(name, "Muted"))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.key)
            return
        super().keyPressEvent(event)


class DependencyRow(QWidget):
    def __init__(self, name: str, icon: str, purpose: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(14)
        row.addWidget(IconBadge(icon, "neutral", size=38))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(name, "CardTitle"))
        self.version = label("", "Faint")
        head.addWidget(self.version)
        head.addStretch(1)
        texts.addLayout(head)
        texts.addWidget(label(purpose, "Faint"))
        self.path = ElidedLabel("", "Mono")
        texts.addWidget(self.path)
        row.addLayout(texts, 1)
        self.pill = StatusPill("Kontrol…", "neutral")
        row.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)

    def set_tool(self, tool, missing_text: str) -> None:
        if tool:
            self.version.setText(f"v{tool.short_version}")
            where = str(tool.path) if tool.path else "Python ortamına kurulu"
            self.path.set_full_text(f"{where}  ·  {SOURCE_LABELS.get(tool.source, tool.source)}")
            self.pill.set_state("success", "Hazır")
        else:
            self.version.setText("")
            self.path.set_full_text(missing_text)
            self.pill.set_state("danger", "Bulunamadı")

    def set_checking(self) -> None:
        self.pill.set_state("neutral", "Kontrol…")


class SettingsPage(BasePage):
    key = "settings"

    def __init__(self, ctx, navigate) -> None:
        super().__init__(ctx, navigate, "Ayarlar", "Değişiklikler otomatik olarak kaydedilir.")
        self.saved_pill = self.header.add_action(StatusPill("", "success"))
        self.saved_pill.hide()
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(self.saved_pill.hide)
        self._loading = False

        # --- Kayıt konumu ---------------------------------------------------
        location = Card("Kayıt konumu", "İndirilen ve dönüştürülen dosyaların kaydedileceği klasör.", icon="folder")
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setReadOnly(True)
        self.browse_button = themed_button("Gözat…", "folder")
        self.open_dir_button = themed_button("Klasörü aç", "folder-open")
        self.default_dir_button = themed_button("Varsayılana dön", "rotate-ccw", kind="Ghost")
        path_row.addWidget(self.download_dir_edit, 1)
        path_row.addWidget(self.browse_button)
        path_row.addWidget(self.open_dir_button)
        location.body.addLayout(path_row)
        default_row = QHBoxLayout()
        self.default_hint = ElidedLabel("", "Faint")
        default_row.addWidget(self.default_hint, 1)
        default_row.addWidget(self.default_dir_button)
        location.body.addLayout(default_row)
        self.body.addWidget(location)

        # --- İndirme varsayılanları -----------------------------------------
        defaults = Card("İndirme varsayılanları", "Yeni indirmelerde önceden seçili gelecek değerler.",
                        icon="sliders")
        self.quality_combo = QComboBox()
        for value, text in VIDEO_QUALITIES:
            self.quality_combo.addItem(text, value)
        self.bitrate_combo = QComboBox()
        for rate in AUDIO_BITRATES:
            self.bitrate_combo.addItem(f"{rate} kbps", rate)
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(*MAX_CONCURRENT_RANGE)
        self.concurrent_spin.setSuffix(" indirme")
        for w in (self.quality_combo, self.bitrate_combo, self.concurrent_spin):
            w.setFixedWidth(210)
        defaults.body.addLayout(setting_row("Video kalitesi", "Kaynakta yoksa en yakın düşük kalite seçilir.",
                                            self.quality_combo))
        defaults.body.addWidget(divider())
        defaults.body.addLayout(setting_row("MP3 ses kalitesi", "Ses indirme ve dönüştürmede kullanılan bit hızı.",
                                            self.bitrate_combo))
        defaults.body.addWidget(divider())
        defaults.body.addLayout(setting_row("Eşzamanlı indirme", "Aynı anda çalışacak en fazla indirme sayısı.",
                                            self.concurrent_spin))
        self.body.addWidget(defaults)

        # --- Görünüm --------------------------------------------------------
        appearance = Card("Görünüm", "Vurgu rengi ve arayüz animasyonları.", icon="palette")
        self.swatch_grid = ResponsiveGrid(min_item_width=150, max_columns=3, spacing=12)
        self.swatches: dict[str, AccentSwatch] = {}
        for key, name in ACCENTS:
            swatch = AccentSwatch(key, name)
            swatch.clicked.connect(lambda k: self.ctx.update_settings(accent=k) and self._flash_saved())
            self.swatches[key] = self.swatch_grid.add(swatch)
        appearance.body.addWidget(self.swatch_grid)
        appearance.body.addWidget(divider())
        self.animations_toggle = ToggleSwitch()
        appearance.body.addLayout(setting_row("Animasyonlar", "Sayfa geçişleri ve bildirimlerde yumuşak hareket.",
                                              self.animations_toggle))
        self.body.addWidget(appearance)

        # --- Sistem bileşenleri ---------------------------------------------
        system = Card("Sistem bileşenleri",
                      "FFmpeg otomatik bulunur: seçilen konum → uygulamayla gelen → PATH → bilinen kurulumlar.",
                      icon="terminal")
        self.dep_rows = {
            "ffmpeg": DependencyRow("FFmpeg", "terminal", "Birleştirme, dönüştürme ve kesme"),
            "ffprobe": DependencyRow("FFprobe", "search", "Süre, akış ve kodek bilgisi okuma"),
            "ytdlp": DependencyRow("yt-dlp", "package", "Desteklenen kaynaklardan indirme"),
        }
        for i, row in enumerate(self.dep_rows.values()):
            if i:
                system.body.addWidget(divider())
            system.body.addWidget(row)
        self.dep_help = label("", "Muted", wrap=True)
        self.dep_help.hide()
        system.body.addWidget(self.dep_help)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.rescan_button = themed_button("Yeniden tara", "refresh")
        self.pick_ffmpeg_button = themed_button("FFmpeg konumunu seç…", "folder")
        self.auto_ffmpeg_button = themed_button("Otomatik algılamaya dön", "rotate-ccw", kind="Ghost")
        buttons.addWidget(self.rescan_button)
        buttons.addWidget(self.pick_ffmpeg_button)
        buttons.addWidget(self.auto_ffmpeg_button)
        buttons.addStretch(1)
        system.body.addLayout(buttons)
        self.body.addWidget(system)

        # --- Gelişmiş -------------------------------------------------------
        advanced = Card("Gelişmiş", "Veri, günlük dosyaları ve sıfırlama.", icon="hard-drive")
        adv = QHBoxLayout()
        adv.setSpacing(8)
        self.open_data_button = themed_button("Veri klasörünü aç", "folder-open")
        self.open_logs_button = themed_button("Günlük klasörünü aç", "file")
        self.reset_button = themed_button("Ayarları sıfırla", "rotate-ccw", kind="Danger")
        adv.addWidget(self.open_data_button)
        adv.addWidget(self.open_logs_button)
        adv.addStretch(1)
        advanced.body.addLayout(adv)
        advanced.body.addWidget(ElidedLabel(f"Veri klasörü: {user_data_dir()}", "Faint"))
        advanced.body.addWidget(divider())
        advanced.body.addLayout(setting_row("Ayarları sıfırla", "Tüm ayarları varsayılana döndürür; geçmiş korunur.",
                                            self.reset_button))
        self.body.addWidget(advanced)
        self.body.addStretch(1)

        # --- Bağlantılar ----------------------------------------------------
        self.browse_button.clicked.connect(self.choose_download_dir)
        self.open_dir_button.clicked.connect(self.open_download_dir)
        self.default_dir_button.clicked.connect(lambda: self._save(download_dir=""))
        self.quality_combo.currentIndexChanged.connect(
            lambda: self._save(video_quality=self.quality_combo.currentData()))
        self.bitrate_combo.currentIndexChanged.connect(
            lambda: self._save(audio_bitrate=int(self.bitrate_combo.currentData())))
        self.concurrent_spin.valueChanged.connect(lambda v: self._save(max_concurrent_downloads=v))
        self.animations_toggle.toggled.connect(lambda v: self._save(animations=v))
        self.rescan_button.clicked.connect(ctx.refresh_dependencies)
        self.pick_ffmpeg_button.clicked.connect(self.choose_ffmpeg)
        self.auto_ffmpeg_button.clicked.connect(lambda: self._save(ffmpeg_path=None))
        self.open_data_button.clicked.connect(lambda: open_folder(user_data_dir()))
        self.open_logs_button.clicked.connect(lambda: open_folder(logs_dir()))
        self.reset_button.clicked.connect(self.confirm_reset)
        ctx.settings_changed.connect(lambda _keys: self.load_settings())
        ctx.dependencies_checking.connect(self._deps_checking)
        ctx.dependencies_changed.connect(self.show_dependencies)

        self.load_settings()
        if ctx.dependencies is not None:
            self.show_dependencies(ctx.dependencies)

    # ------------------------------------------------------------------ ayarlar
    def load_settings(self) -> None:
        self._loading = True
        s = self.ctx.settings
        mgr = self.ctx.settings_manager
        self.download_dir_edit.setText(str(mgr.download_dir()))
        custom = bool(s.download_dir)
        self.default_hint.set_full_text("Özel klasör kullanılıyor." if custom
                                  else f"Varsayılan konum kullanılıyor: {default_download_dir()}")
        self.default_dir_button.setVisible(custom)
        self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(s.video_quality)))
        self.bitrate_combo.setCurrentIndex(max(0, self.bitrate_combo.findData(s.audio_bitrate)))
        self.concurrent_spin.setValue(s.max_concurrent_downloads)
        self.animations_toggle.setChecked(s.animations)
        for key, swatch in self.swatches.items():
            swatch.set_selected(key == s.accent)
        self.auto_ffmpeg_button.setVisible(bool(s.ffmpeg_path))
        self._loading = False

    def _save(self, **changes) -> None:
        if self._loading:
            return
        if self.ctx.update_settings(**changes):
            self._flash_saved()

    def _flash_saved(self) -> bool:
        self.saved_pill.set_state("success", f"Kaydedildi · {datetime.now():%H:%M:%S}")
        self.saved_pill.show()
        self._saved_timer.start(2500)
        return True

    def set_download_dir(self, path: str) -> bool:
        ok, error = is_writable_dir(path)
        if not ok:
            log.warning("Klasör yazılabilir değil %s: %s", path, error)
            self.ctx.notifications.error("Klasör kullanılamıyor", f"Seçilen klasöre yazılamıyor: {path}", error)
            return False
        self._save(download_dir=str(Path(path)))
        return True

    def choose_download_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Kayıt klasörünü seçin",
                                                str(self.ctx.settings_manager.download_dir()))
        if path:
            self.set_download_dir(path)

    def open_download_dir(self) -> None:
        try:
            folder = self.ctx.settings_manager.ensure_download_dir()
        except OSError as exc:
            self.ctx.notifications.error("Klasör açılamadı", str(exc))
            return
        open_folder(folder)

    def confirm_reset(self) -> None:
        if confirm(self, "Ayarları sıfırla", "Tüm ayarlar varsayılan değerlere döndürülecek.\n"
                   "Geçmiş ve indirilen dosyalar etkilenmez.", "Sıfırla", danger=True):
            self.ctx.reset_settings()
            self.ctx.notifications.success("Ayarlar sıfırlandı", "Varsayılan değerler geri yüklendi.")

    # ------------------------------------------------------------------ bağımlılıklar
    def set_ffmpeg_path(self, path: str) -> bool:
        if not self.ctx.checker.probe_version(self.ctx.checker.normalize_custom(path)):
            self.ctx.notifications.error("Geçersiz FFmpeg",
                                         "Seçilen dosya çalıştırılabilir bir FFmpeg değil. Mevcut ayar korunuyor.")
            return False
        self._save(ffmpeg_path=path)
        return True

    def choose_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg.exe dosyasını seçin", "",
                                              f"FFmpeg ({FFMPEG_EXE});;Tüm dosyalar (*)")
        if path:
            self.set_ffmpeg_path(path)

    def _deps_checking(self) -> None:
        self.rescan_button.setEnabled(False)
        self.rescan_button.setText("Taranıyor…")
        for row in self.dep_rows.values():
            row.set_checking()

    def show_dependencies(self, report: DependencyReport) -> None:
        self.rescan_button.setEnabled(True)
        self.rescan_button.setText("Yeniden tara")
        self.dep_rows["ffmpeg"].set_tool(report.ffmpeg, "Bulunamadı")
        self.dep_rows["ffprobe"].set_tool(report.ffprobe, "Bulunamadı (FFmpeg ile aynı klasörde olmalı)")
        self.dep_rows["ytdlp"].set_tool(report.ytdlp, "Python paketi yüklü değil: pip install yt-dlp")
        help_lines = []
        if report.custom_path_rejected:
            help_lines.append("Kayıtlı FFmpeg yolu artık geçerli değil; otomatik algılanan kopya kullanılıyor."
                              if report.ffmpeg else "Kayıtlı FFmpeg yolu geçerli değil.")
        if not report.ffmpeg:
            help_lines.append("FFmpeg kurmak için: winget install Gyan.FFmpeg — ardından 'Yeniden tara'. "
                              "Ya da ffmpeg.exe konumunu elle seçin.")
        self.dep_help.setText("\n".join(help_lines))
        self.dep_help.setVisible(bool(help_lines))
