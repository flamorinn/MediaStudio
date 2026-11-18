"""AppContext: uygulama genelinde paylaşılan servisler ve durum değişikliği sinyalleri."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from app.core.dependencies import DependencyChecker, DependencyReport
from app.core.download_queue import DownloadQueue
from app.core.errors import SettingsError
from app.core.history_store import HistoryStore
from app.core.notifications import NotificationCenter
from app.core.settings_manager import SettingsManager
from app.core.workers import Worker

log = logging.getLogger(__name__)


class AppContext(QObject):
    settings_changed = Signal(list)          # değişen alan adları
    dependencies_checking = Signal()
    dependencies_changed = Signal(object)    # DependencyReport
    history_changed = Signal()

    def __init__(self, settings: SettingsManager | None = None, history: HistoryStore | None = None,
                 checker: DependencyChecker | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings_manager = settings or SettingsManager()
        self.history = history or HistoryStore()
        self.checker = checker or DependencyChecker()
        self.notifications = NotificationCenter(self)
        self.queue = DownloadQueue(self)
        self.dependencies: DependencyReport | None = None
        self.checking_dependencies = False
        self.thread_pool = QThreadPool.globalInstance()
        # Servis katmanı tarafından bağlanır (app/application.py); çekirdek, servisleri import etmez.
        self.downloads = None
        self.conversions = None
        self.cuts = None

    @property
    def settings(self):
        return self.settings_manager.settings

    # ------------------------------------------------------------------ ayarlar
    def update_settings(self, **changes) -> bool:
        try:
            changed = self.settings_manager.update(**changes)
        except SettingsError as exc:
            self.notifications.exception(exc, "Ayarlar kaydedilemedi")
            return False
        if changed:
            self.settings_changed.emit(changed)
            if "ffmpeg_path" in changed:
                self.refresh_dependencies()
        return True

    def reset_settings(self) -> None:
        before = self.settings.ffmpeg_path
        self.settings_manager.reset()
        self.settings_changed.emit(["*"])
        if before != self.settings.ffmpeg_path:
            self.refresh_dependencies()

    # ------------------------------------------------------------------ bağımlılıklar
    def refresh_dependencies(self) -> None:
        if self.checking_dependencies:
            return
        self.checking_dependencies = True
        self.dependencies_checking.emit()
        worker = Worker(self.checker.detect, self.settings.ffmpeg_path)
        worker.signals.result.connect(self._on_dependencies)
        worker.signals.error.connect(self._on_dependencies_error)
        self.thread_pool.start(worker)

    @Slot(object)
    def _on_dependencies(self, report: DependencyReport) -> None:
        first_check = self.dependencies is None
        self.checking_dependencies = False
        self.dependencies = report
        self.dependencies_changed.emit(report)
        if report.custom_path_rejected:
            self.notifications.warning(
                "Kayıtlı FFmpeg yolu geçersiz",
                "Otomatik algılanan kopya kullanılıyor." if report.ffmpeg else "FFmpeg bulunamadı.")
        elif not report.ffmpeg:
            self.notifications.error("FFmpeg bulunamadı",
                                     "Dönüştürme ve kesme için gerekli. Ayarlar → Sistem bileşenleri.")
        elif not report.ffprobe:
            self.notifications.warning("FFprobe bulunamadı", "Medya bilgisi okuma özellikleri sınırlı olacak.")
        elif not first_check:
            self.notifications.success("Sistem bileşenleri hazır", f"FFmpeg {report.ffmpeg.short_version}")

    @Slot(object, str)
    def _on_dependencies_error(self, exc: BaseException, tb: str) -> None:
        self.checking_dependencies = False
        self.notifications.exception(exc, "Bağımlılık kontrolü başarısız", tb)
        self._on_dependencies(DependencyReport())

    # ------------------------------------------------------------------ kapanış
    def shutdown(self) -> None:
        for service in (self.downloads, self.conversions, self.cuts):
            if service is not None:
                service.shutdown()
        self.thread_pool.waitForDone(3000)
        self.history.close()
