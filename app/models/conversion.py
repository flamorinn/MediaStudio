"""MP3 dönüştürme listesindeki bir dosya."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

_ids = itertools.count(1)


class ConversionStatus(str, Enum):
    PROBING = "probing"          # ffprobe ile inceleniyor
    READY = "ready"              # dönüştürülmeye hazır
    INVALID = "invalid"          # okunamadı / ses akışı yok
    QUEUED = "queued"
    CONVERTING = "converting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "probing": "İnceleniyor",
            "ready": "Hazır",
            "invalid": "Desteklenmiyor",
            "queued": "Sırada",
            "converting": "Dönüştürülüyor",
            "completed": "Tamamlandı",
            "failed": "Başarısız",
            "cancelled": "İptal edildi",
        }[self.value]

    @property
    def tone(self) -> str:
        return {
            "probing": "neutral", "ready": "info", "invalid": "warning", "queued": "neutral",
            "converting": "accent", "completed": "success", "failed": "danger", "cancelled": "warning",
        }[self.value]

    @property
    def is_busy(self) -> bool:
        """İşlem sürüyor (kaldırılamaz)."""
        return self in (ConversionStatus.PROBING, ConversionStatus.QUEUED, ConversionStatus.CONVERTING)

    @property
    def can_start(self) -> bool:
        return self in (ConversionStatus.READY, ConversionStatus.FAILED, ConversionStatus.CANCELLED)

    @property
    def is_finished(self) -> bool:
        return self in (ConversionStatus.COMPLETED, ConversionStatus.FAILED, ConversionStatus.CANCELLED,
                        ConversionStatus.INVALID)


@dataclass
class ConversionItem:
    source: str
    status: ConversionStatus = ConversionStatus.PROBING
    duration: float | None = None
    has_video: bool = True
    size_bytes: int = 0
    progress: float = 0.0
    speed: str = ""             # ffmpeg hız çarpanı, ör. "38x"
    eta_seconds: int | None = None
    bitrate: int = 192
    output_dir: str = ""
    output_path: str = ""
    error: str = ""
    error_details: str = ""
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def name(self) -> str:
        return Path(self.source).name
