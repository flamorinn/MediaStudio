"""Bir bağlantının analiz sonucu (indirmeden önce gösterilen bilgiler)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MediaInfo:
    url: str                       # kullanıcının girdiği bağlantı
    webpage_url: str
    title: str
    extractor: str                 # kaynak/site adı (yt-dlp çıkarıcısı)
    uploader: str = ""
    duration: float | None = None  # saniye
    thumbnail: str = ""
    heights: tuple[int, ...] = field(default_factory=tuple)  # mevcut video yükseklikleri (büyükten küçüğe)
    has_video: bool = True
    has_audio: bool = True
    filesize_approx: int = 0
    # Ham yt-dlp bilgisi (indirmede yeniden analiz yapmamak için); karşılaştırmaya katılmaz
    raw: dict | None = field(default=None, repr=False, compare=False)
    analyzed_at: float = field(default=0.0, compare=False)

    @property
    def best_height(self) -> int | None:
        return self.heights[0] if self.heights else None
