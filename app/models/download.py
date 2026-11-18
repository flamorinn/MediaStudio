"""İndirme kuyruğu öğesi."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

from app.models.history import OperationKind

_ids = itertools.count(1)


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PROCESSING = "processing"  # birleştirme / dönüştürme
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "queued": "Sırada",
            "running": "İndiriliyor",
            "processing": "İşleniyor",
            "completed": "Tamamlandı",
            "failed": "Başarısız",
            "cancelled": "İptal edildi",
        }[self.value]

    @property
    def tone(self) -> str:
        return {
            "queued": "neutral",
            "running": "accent",
            "processing": "accent",
            "completed": "success",
            "failed": "danger",
            "cancelled": "warning",
        }[self.value]

    @property
    def is_active(self) -> bool:
        return self in (DownloadStatus.RUNNING, DownloadStatus.PROCESSING)

    @property
    def is_finished(self) -> bool:
        return self in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)


@dataclass
class DownloadTask:
    url: str
    kind: OperationKind
    title: str = ""
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float | None = 0.0   # 0.0 - 1.0; None = belirsiz (boyut bilinmiyor)
    speed_bps: float = 0.0
    eta_seconds: int | None = None
    downloaded_bytes: int = 0
    total_bytes: int = 0
    phase: str = ""                # "Video akışı 1/2", "Birleştiriliyor" ...
    output_path: str = ""
    error: str = ""
    error_details: str = ""
    # İndirme seçenekleri (kuyruğa eklenirken sabitlenir)
    quality: str = "best"          # video: best | 2160 | 1440 | 1080 | 720 | 480
    audio_bitrate: int = 192       # ses: MP3 kbps
    output_dir: str = ""
    # Zaman aralığı (saniye). İkisi de None ise videonun tamamı indirilir.
    section_start: float | None = None
    section_end: float | None = None
    # Analiz sırasında çekilen yt-dlp bilgisi: indirmede bağlantı yeniden analiz edilmez
    prefetched_info: dict | None = field(default=None, repr=False, compare=False)
    prefetched_at: float = 0.0
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def display_title(self) -> str:
        return self.title or self.url

    @property
    def is_audio(self) -> bool:
        return self.kind is OperationKind.AUDIO_DOWNLOAD

    @property
    def has_section(self) -> bool:
        return self.section_start is not None and self.section_end is not None

    @property
    def section_length(self) -> float:
        return (self.section_end - self.section_start) if self.has_section else 0.0
