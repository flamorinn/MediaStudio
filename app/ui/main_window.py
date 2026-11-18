"""Ana pencere: sabit sidebar + animasyonlu sayfa yığını + toast bildirimleri."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from app import APP_NAME
from app.core.context import AppContext
from app.models.notification import Notification
from app.ui.pages.base import BasePage
from app.models.history import OperationKind
from app.ui.pages.converter_page import ConverterPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.download_page import DownloadPage
from app.ui.pages.cutter_page import CutterPage
from app.ui.pages.history_page import HistoryPage
from app.ui.pages.queue_page import QueuePage
from app.ui.pages.settings_page import SettingsPage
from app.ui.theme import icons, theme
from app.ui.widgets.dialogs import ErrorDetailsDialog, confirm
from app.ui.widgets.page_stack import AnimatedStack
from app.ui.widgets.sidebar import NavItem, Sidebar
from app.ui.widgets.toast import ToastHost

NAV_ITEMS = [
    NavItem("dashboard", "Dashboard", "layout-dashboard", "Genel"),
    NavItem("video_download", "Video İndir", "video", "Araçlar"),
    NavItem("audio_download", "Ses İndir", "music", "Araçlar"),
    NavItem("converter", "Dönüştürücü", "repeat", "Araçlar"),
    NavItem("cutter", "Video Kes", "scissors", "Araçlar"),
    NavItem("queue", "İndirme Kuyruğu", "layers", "Yönetim"),
    NavItem("history", "Geçmiş", "history", "Yönetim"),
    NavItem("settings", "Ayarlar", "settings", "Yönetim"),
]
MIN_SIZE = (1024, 640)
DEFAULT_SIZE = (1360, 860)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(*MIN_SIZE)
        theme.bind(self, self._refresh_window_icon)

        root = QWidget()
        root.setObjectName("Root")
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = Sidebar(NAV_ITEMS)
        row.addWidget(self.sidebar)

        self.content = QWidget()
        self.content.setObjectName("Content")
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = AnimatedStack()
        content_layout.addWidget(self.stack)
        row.addWidget(self.content, 1)
        self.setCentralWidget(root)

        nav = self.show_page
        self.pages: dict[str, BasePage] = {
            "dashboard": DashboardPage(ctx, nav),
            "video_download": DownloadPage(ctx, nav, OperationKind.VIDEO_DOWNLOAD),
            "audio_download": DownloadPage(ctx, nav, OperationKind.AUDIO_DOWNLOAD),
            "converter": ConverterPage(ctx, nav),
            "cutter": CutterPage(ctx, nav),
            "queue": QueuePage(ctx, nav),
            "history": HistoryPage(ctx, nav),
            "settings": SettingsPage(ctx, nav),
        }
        assert list(self.pages) == [item.key for item in NAV_ITEMS]
        for page in self.pages.values():
            self.stack.addWidget(page)

        self.toasts = ToastHost(self.content)
        self.toasts.details_requested.connect(self.show_error_details)

        self.sidebar.page_requested.connect(self.show_page)
        self.sidebar.system_card.clicked.connect(lambda: self.show_page("settings"))
        ctx.notifications.posted.connect(self.toasts.show)
        ctx.queue.changed.connect(self._update_queue_badge)
        ctx.dependencies_checking.connect(self.sidebar.system_card.set_checking)
        ctx.dependencies_changed.connect(self.sidebar.system_card.set_report)
        if ctx.dependencies is not None:
            self.sidebar.system_card.set_report(ctx.dependencies)

        for index, item in enumerate(NAV_ITEMS, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda k=item.key: self.show_page(k))

        self._restore_geometry()
        self.show_page("dashboard")

    # ------------------------------------------------------------------ gezinme
    def show_page(self, key: str) -> None:
        page = self.pages[key]
        self.stack.switch_to(page)
        self.sidebar.set_current(key)
        label = next(item.label for item in NAV_ITEMS if item.key == key)
        self.setWindowTitle(f"{label} — {APP_NAME}")
        page.on_shown()

    def current_page_key(self) -> str:
        current = self.stack.currentWidget()
        return next(k for k, p in self.pages.items() if p is current)

    def _update_queue_badge(self) -> None:
        self.sidebar.set_badge("queue", self.ctx.queue.active_count() + self.ctx.queue.pending_count())

    def show_error_details(self, note: Notification) -> None:
        ErrorDetailsDialog(note.title, note.message, note.details, self).exec()

    def _refresh_window_icon(self) -> None:
        self.setWindowIcon(icons.logo_icon(theme.color("primary"), theme.color("secondary")))

    # ------------------------------------------------------------------ pencere boyutu
    def _restore_geometry(self) -> None:
        saved = self.ctx.settings.window_geometry
        if saved and self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(*DEFAULT_SIZE)
            return
        avail = screen.availableGeometry()
        width = min(DEFAULT_SIZE[0], int(avail.width() * 0.92))
        height = min(DEFAULT_SIZE[1], int(avail.height() * 0.92))
        self.resize(max(width, MIN_SIZE[0]), max(height, MIN_SIZE[1]))
        self.move(avail.center() - self.rect().center())

    def closeEvent(self, event: QCloseEvent) -> None:
        busy = [s for s in (self.ctx.downloads, self.ctx.conversions, self.ctx.cuts) if s is not None and s.has_active()]
        if busy:
            if not confirm(self, "Çıkış", "Devam eden veya sırada bekleyen indirme/dönüştürme işlemleri var.\n"
                           "Çıkarsanız iptal edilecek ve yarım dosyalar silinecek.", "İptal et ve çık", danger=True):
                event.ignore()
                return
            for service in busy:
                service.cancel_all()
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.ctx.update_settings(window_geometry=geometry)
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F5:
            self.pages[self.current_page_key()].on_shown()
            return
        super().keyPressEvent(event)
