"""Saf veri modelleri (Qt'ye bağımlı değildir)."""

from app.models.conversion import ConversionItem, ConversionStatus
from app.models.download import DownloadStatus, DownloadTask
from app.models.history import HistoryEntry, OperationKind, OperationStatus
from app.models.notification import Notification, NotificationLevel
from app.models.settings import AppSettings

__all__ = [
    "AppSettings", "ConversionItem", "ConversionStatus", "DownloadStatus", "DownloadTask", "HistoryEntry", "Notification",
    "NotificationLevel", "OperationKind", "OperationStatus",
]
