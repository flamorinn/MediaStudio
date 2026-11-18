"""Kalıcı uygulama ayarları modeli ve geçerli seçenekler."""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1

# (değer, arayüz etiketi)
VIDEO_QUALITIES: tuple[tuple[str, str], ...] = (
    ("best", "En yüksek kalite"),
    ("2160", "2160p (4K)"),
    ("1440", "1440p (2K)"),
    ("1080", "1080p (Full HD)"),
    ("720", "720p (HD)"),
    ("480", "480p"),
)
AUDIO_BITRATES: tuple[int, ...] = (128, 192, 256, 320)
ACCENTS: tuple[tuple[str, str], ...] = (
    ("aurora", "Aurora (mor → mavi)"),
    ("electric", "Elektrik mavisi"),
    ("violet", "Mor"),
)
MAX_CONCURRENT_RANGE = (1, 5)
CONVERT_OUTPUT_MODES: tuple[tuple[str, str], ...] = (
    ("downloads", "Kayıt klasörü"),
    ("source", "Kaynak dosyanın yanı"),
)


@dataclass
class AppSettings:
    download_dir: str = ""                # boş = varsayılan konum (paths.default_download_dir)
    video_quality: str = "best"
    audio_bitrate: int = 192
    max_concurrent_downloads: int = 2
    accent: str = "aurora"
    animations: bool = True
    ffmpeg_path: str | None = None        # None = otomatik algılama
    window_geometry: str = ""             # base64 QByteArray
    convert_output_mode: str = "downloads"  # dönüştürücü çıktısı: downloads | source
    cut_output_mode: str = "downloads"      # kesici çıktısı: downloads | source
    schema_version: int = SCHEMA_VERSION

    def normalized(self) -> "AppSettings":
        """Geçersiz veya eski değerleri güvenli varsayılanlara çeker."""
        if self.video_quality not in {v for v, _ in VIDEO_QUALITIES}:
            self.video_quality = "best"
        try:
            self.audio_bitrate = int(self.audio_bitrate)
        except (TypeError, ValueError):
            self.audio_bitrate = 192
        if self.audio_bitrate not in AUDIO_BITRATES:
            self.audio_bitrate = 192
        try:
            concurrent = int(self.max_concurrent_downloads)
        except (TypeError, ValueError):
            concurrent = 2
        lo, hi = MAX_CONCURRENT_RANGE
        self.max_concurrent_downloads = max(lo, min(concurrent, hi))
        if self.accent not in {a for a, _ in ACCENTS}:
            self.accent = "aurora"
        self.animations = bool(self.animations)
        if not self.ffmpeg_path:
            self.ffmpeg_path = None
        if not isinstance(self.download_dir, str):
            self.download_dir = ""
        if not isinstance(self.window_geometry, str):
            self.window_geometry = ""
        if self.convert_output_mode not in {m for m, _ in CONVERT_OUTPUT_MODES}:
            self.convert_output_mode = "downloads"
        if self.cut_output_mode not in {m for m, _ in CONVERT_OUTPUT_MODES}:
            self.cut_output_mode = "downloads"
        self.schema_version = SCHEMA_VERSION
        return self
