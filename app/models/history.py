"""İşlem geçmişi kaydı."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OperationKind(str, Enum):
    VIDEO_DOWNLOAD = "video_download"
    AUDIO_DOWNLOAD = "audio_download"
    CONVERT = "convert"
    CUT = "cut"

    @property
    def label(self) -> str:
        return {
            "video_download": "Video indirme",
            "audio_download": "Ses indirme",
            "convert": "Dönüştürme",
            "cut": "Kesme",
        }[self.value]

    @property
    def icon(self) -> str:
        return {
            "video_download": "video",
            "audio_download": "music",
            "convert": "repeat",
            "cut": "scissors",
        }[self.value]

    @property
    def is_download(self) -> bool:
        return self in (OperationKind.VIDEO_DOWNLOAD, OperationKind.AUDIO_DOWNLOAD)


class OperationStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {"success": "Tamamlandı", "failed": "Başarısız", "cancelled": "İptal edildi"}[self.value]

    @property
    def tone(self) -> str:
        return {"success": "success", "failed": "danger", "cancelled": "warning"}[self.value]


@dataclass(frozen=True)
class HistoryEntry:
    id: int | None
    created_at: str  # ISO 8601, saniye hassasiyeti
    kind: OperationKind
    title: str
    status: OperationStatus
    source: str = ""
    output_path: str = ""
    size_bytes: int = 0
    details: str = ""
