"""Uygulama başlatma: loglama, tema, global hata yakalama, bağlam ve ana pencere."""

from __future__ import annotations

import logging
import os
import sys
import traceback

from PySide6.QtWidgets import QApplication

from app import APP_ID, APP_NAME, APP_VERSION, ORG_NAME
from app.core.context import AppContext
from app.services.converter import ConversionManager
from app.services.cutter import CutManager
from app.services.downloader import DownloadManager
from app.ui.main_window import MainWindow
from app.ui.theme import theme
from app.utils.logging_setup import install_qt_message_handler, setup_logging

log = logging.getLogger(__name__)
_THEME_KEYS = {"accent", "animations", "*"}


def _set_windows_app_id() -> None:
    """Görev çubuğunda python.exe yerine uygulamanın kendi ikonunun görünmesini sağlar."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.Desktop")
    except (AttributeError, OSError):
        pass


def install_excepthook(ctx: AppContext) -> None:
    """Yakalanmamış hataları loglar ve uygulamayı kapatmadan hata bildirimi olarak gösterir."""

    def handler(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        log.critical("Yakalanmamış hata:\n%s", details)
        try:
            ctx.notifications.error("Beklenmeyen bir hata oluştu", str(exc) or exc_type.__name__, details)
        except Exception:  # noqa: BLE001 - son çare: en azından stderr'e yaz
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = handler


def create_app(argv: list[str] | None = None, ctx: AppContext | None = None):
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)

    ctx = ctx or AppContext()
    theme.apply(app, ctx.settings.accent, ctx.settings.animations)

    def on_settings(keys: list) -> None:
        if _THEME_KEYS & set(keys):
            theme.apply(app, ctx.settings.accent, ctx.settings.animations)

    ctx.settings_changed.connect(on_settings)
    if ctx.downloads is None:
        ctx.downloads = DownloadManager(ctx)
    if ctx.conversions is None:
        ctx.conversions = ConversionManager(ctx)
    if ctx.cuts is None:
        ctx.cuts = CutManager(ctx)
    window = MainWindow(ctx)
    if ctx.settings_manager.recovered_from_corruption:
        ctx.notifications.warning("Ayar dosyası onarıldı",
                                  "Bozuk ayar dosyası yedeklendi ve varsayılan ayarlar yüklendi.")
    ctx.refresh_dependencies()
    return app, ctx, window


def main() -> int:
    log_file = setup_logging()
    install_qt_message_handler()
    _set_windows_app_id()
    log.info("%s %s başlatılıyor (log: %s)", APP_NAME, APP_VERSION, log_file)
    app, ctx, window = create_app()
    install_excepthook(ctx)
    window.show()
    code = app.exec()
    ctx.shutdown()
    log.info("%s kapandı (çıkış kodu %s)", APP_NAME, code)
    return code
