"""Bildirim merkezi: her katman buraya bildirim gönderir, arayüz bunları toast olarak gösterir."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from app.core.errors import MediaStudioError
from app.models.notification import Notification, NotificationLevel

log = logging.getLogger(__name__)

_LOG_LEVELS = {
    NotificationLevel.INFO: logging.INFO,
    NotificationLevel.SUCCESS: logging.INFO,
    NotificationLevel.WARNING: logging.WARNING,
    NotificationLevel.ERROR: logging.ERROR,
}


class NotificationCenter(QObject):
    posted = Signal(object)  # Notification

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.recent: list[Notification] = []

    def post(self, level: NotificationLevel, title: str, message: str = "", details: str = "") -> Notification:
        note = Notification(NotificationLevel(level), title, message, details)
        log.log(_LOG_LEVELS[note.level], "Bildirim [%s] %s %s", note.level.value, title, message)
        self.recent = (self.recent + [note])[-50:]
        self.posted.emit(note)
        return note

    def info(self, title: str, message: str = "") -> Notification:
        return self.post(NotificationLevel.INFO, title, message)

    def success(self, title: str, message: str = "") -> Notification:
        return self.post(NotificationLevel.SUCCESS, title, message)

    def warning(self, title: str, message: str = "", details: str = "") -> Notification:
        return self.post(NotificationLevel.WARNING, title, message, details)

    def error(self, title: str, message: str = "", details: str = "") -> Notification:
        return self.post(NotificationLevel.ERROR, title, message, details)

    def exception(self, exc: BaseException, title: str = "İşlem başarısız", details: str = "") -> Notification:
        """Bir istisnayı kullanıcı dostu bildirime çevirir."""
        if isinstance(exc, MediaStudioError):
            return self.error(title, exc.user_message, details or exc.detail)
        return self.error(title, str(exc) or exc.__class__.__name__, details)
