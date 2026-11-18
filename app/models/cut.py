"""Video kesme: kaynak bilgisi ve dışa aktarma işi."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

_ids = itertools.count(1)

# MP4 kabına kopyalanabilen kodekler (yeniden kodlamadan kesim için)
MP4_VIDEO_CODECS = {"h264", "hevc", "mpeg4", "av1", "vp9"}
MP4_AUDIO_CODECS = {"aac", "mp3", "alac", "opus", "ac3", "eac3", "flac"}


class CutFormat(str, Enum):
    MP4 = "mp4"
    MP3 = "mp3"

    @property
    def label(self) -> str:
        return {"mp4": "MP4 (video)", "mp3": "MP3 (yalnızca ses)"}[self.value]


class CutMode(str, Enum):
    LOSSLESS = "lossless"   # akış kopyalama: kalite kaybı yok, başlangıç anahtar kareye çekilir
    PRECISE = "precise"     # yeniden kodlama: kare hassasiyetinde

    @property
    def label(self) -> str:
        return {"lossless": "Kayıpsız (hızlı)", "precise": "Kare hassasiyetinde (yeniden kodlama)"}[self.value]


class CutStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {"queued": "Sırada", "running": "Kesiliyor", "completed": "Tamamlandı",
                "failed": "Başarısız", "cancelled": "İptal edildi"}[self.value]

    @property
    def tone(self) -> str:
        return {"queued": "neutral", "running": "accent", "completed": "success",
                "failed": "danger", "cancelled": "warning"}[self.value]

    @property
    def is_active(self) -> bool:
        return self in (CutStatus.QUEUED, CutStatus.RUNNING)


@dataclass(frozen=True)
class MediaDetails:
    path: str
    duration: float
    has_video: bool
    has_audio: bool
    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    size: int = 0

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def lossless_mp4_possible(self) -> bool:
        """Akışlar MP4 kabına yeniden kodlamadan kopyalanabilir mi?"""
        if not self.has_video or self.video_codec not in MP4_VIDEO_CODECS:
            return False
        return not self.has_audio or self.audio_codec in MP4_AUDIO_CODECS


@dataclass
class CutJobItem:
    source: str
    start: float
    end: float
    fmt: CutFormat
    mode: CutMode = CutMode.LOSSLESS
    bitrate: int = 192
    output_dir: str = ""
    effective_start: float | None = None   # kayıpsız kesimde anahtar kare başlangıcı
    status: CutStatus = CutStatus.QUEUED
    progress: float = 0.0
    speed: str = ""
    eta_seconds: int | None = None
    output_path: str = ""
    error: str = ""
    error_details: str = ""
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def length(self) -> float:
        return max(self.end - self.start, 0.0)

    @property
    def output_length(self) -> float:
        """Çıktının beklenen süresi (kayıpsız kesimde anahtar kareden başlar)."""
        start = self.effective_start if self.effective_start is not None else self.start
        return max(self.end - start, 0.0)

    @property
    def name(self) -> str:
        return Path(self.source).name
