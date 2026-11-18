"""yt-dlp ile ilgili saf yardımcılar: güvenli seçenekler, format seçimi, analiz, hata çevirisi, dosya adları.

Güvenlik ilkesi: Media Studio yalnızca kullanıcının indirme hakkı olan içerikler içindir. Bu yüzden
erişim kısıtlamalarını aşmaya yarayan hiçbir yt-dlp seçeneği kullanılmaz (tarayıcı çerezleri, oturum
bilgisi, coğrafi engel aşma, oynatılamayan/DRM'li formatları zorlama vb.). DRM'li, giriş/üyelik
gerektiren ve bölge kısıtlamalı içerikler açık bir mesajla reddedilir.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from pathlib import Path

from app.core.errors import MediaStudioError
from app.models.history import OperationKind
from app.models.media import MediaInfo

log = logging.getLogger("yt_dlp")

MAX_STEM_LENGTH = 150
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)

# Bilinçli olarak YER ALMAYAN seçenekler: cookiefile, cookiesfrombrowser, username, password,
# usenetrc, geo_bypass*, allow_unplayable_formats, ap_* (TV sağlayıcı girişi).
SAFE_BASE_OPTIONS = {
    "quiet": True,
    "noprogress": True,
    "noplaylist": True,
    "geo_bypass": False,
    "allow_unplayable_formats": False,
    "socket_timeout": 20,
    "retries": 3,
    "fragment_retries": 3,
    "windowsfilenames": True,
    "overwrites": False,
    "continuedl": False,
    "color": {"stdout": "never", "stderr": "never"},
}
FORBIDDEN_OPTIONS = ("cookiefile", "cookiesfrombrowser", "username", "password", "usenetrc",
                     "geo_bypass_country", "geo_bypass_ip_block", "ap_mso", "ap_username", "ap_password")


class DownloadFailure(MediaStudioError):
    pass


class _Logger:
    """yt-dlp çıktısını konsol yerine uygulama günlüğüne yönlendirir."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.info("uyarı: %s", msg)

    def error(self, msg: str) -> None:
        log.info("hata: %s", msg)


def base_options(**extra) -> dict:
    opts = {**SAFE_BASE_OPTIONS, "logger": _Logger(), **extra}
    leaked = [k for k in FORBIDDEN_OPTIONS if opts.get(k)]
    if leaked:  # savunma amaçlı: kod yanlışlıkla eklerse bile çalışmaz
        raise ValueError(f"İzin verilmeyen yt-dlp seçeneği: {leaked}")
    return opts


# ---------------------------------------------------------------- hata çevirisi
_ERROR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("drm",), "Bu içerik DRM ile korunuyor; indirilmesi desteklenmez."),
    (("sign in", "log in", "login", "private video", "members-only", "members only", "confirm your age",
      "age-restricted", "age restricted", "requires authentication", "premium", "subscriber",
      "requires payment", "purchase", "rental"),
     "Bu içerik giriş, üyelik veya özel izin gerektiriyor. Media Studio erişim kısıtlamalarını aşmaz."),
    (("available in your country", "geo restrict", "geo-restrict", "your location", "georestrict",
      "in your region"),
     "Bu içerik bölgenizde erişime kapalı. Bölgesel kısıtlamalar aşılmaz."),
    (("is live", "live event", "premieres in", "live stream"), "Canlı yayınlar indirilemez."),
    (("unsupported url",), "Bu bağlantı desteklenmiyor."),
    (("http error 404", "not found"), "İçerik bulunamadı (404). Bağlantıyı kontrol edin."),
    (("http error 403", "forbidden"), "Sunucu erişimi reddetti (403)."),
    (("http error 429", "too many requests"), "Kaynak çok fazla istek aldı (429). Biraz sonra tekrar deneyin."),
    (("ffmpeg", "ffprobe"), "FFmpeg gerekli ama bulunamadı veya çalışmadı. Ayarlar → Sistem bileşenleri."),
    (("no space left", "errno 28", "disk full"), "Diskte yeterli boş alan yok."),
    (("permission denied", "errno 13", "access is denied"), "Kayıt klasörüne yazılamıyor (erişim reddedildi)."),
    (("requested format is not available", "no video formats"), "İstenen kalite bu içerik için mevcut değil."),
    (("timed out", "getaddrinfo", "name resolution", "connection", "network is unreachable", "urlopen error",
      "remote end closed", "connection reset"), "Ağ bağlantısı kurulamadı. İnternet bağlantınızı kontrol edin."),
)


def friendly_error(text: str) -> str:
    lowered = (text or "").lower()
    for needles, message in _ERROR_RULES:
        if any(n in lowered for n in needles):
            return message
    return "İndirme başarısız oldu."


def clean_error_text(text: str) -> str:
    """yt-dlp hata metnindeki 'ERROR:' öneki ve renk kodlarını temizler (ayrıntılar bölümü için)."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    return re.sub(r"^\s*ERROR:\s*", "", text).strip()


# ---------------------------------------------------------------- doğrulama
def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise DownloadFailure("Bir bağlantı girin.")
    if not _URL_RE.match(url):
        raise DownloadFailure("Geçerli bir http:// veya https:// bağlantısı girin.")
    return url


def check_info(info: dict) -> None:
    """Analiz sonucunu güvenlik ve kapsam kurallarına göre denetler."""
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        raise DownloadFailure("Oynatma listeleri bu sürümde desteklenmiyor; tek bir içeriğin bağlantısını girin.")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise DownloadFailure("Canlı yayınlar indirilemez.")
    formats = [f for f in (info.get("formats") or [info]) if f]
    if formats and all(f.get("has_drm") for f in formats):
        raise DownloadFailure("Bu içerik DRM ile korunuyor; indirilmesi desteklenmez.")


def to_media_info(url: str, info: dict) -> MediaInfo:
    formats = [f for f in (info.get("formats") or [info]) if f and not f.get("has_drm")]
    heights = sorted({int(f["height"]) for f in formats
                      if f.get("height") and f.get("vcodec") not in ("none",)}, reverse=True)
    has_video = any(f.get("vcodec") != "none" for f in formats) if formats else True
    has_audio = any(f.get("acodec") != "none" for f in formats) if formats else True
    return MediaInfo(
        url=url,
        webpage_url=info.get("webpage_url") or url,
        title=(info.get("title") or "").strip() or "Adsız medya",
        extractor=info.get("extractor_key") or info.get("extractor") or "",
        uploader=info.get("uploader") or info.get("channel") or "",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail") or "",
        heights=tuple(heights),
        has_video=has_video,
        has_audio=has_audio,
        filesize_approx=int(info.get("filesize") or info.get("filesize_approx") or 0),
    )


def analyze(url: str) -> MediaInfo:
    """Bağlantıyı indirmeden analiz eder (arka plan iş parçacığında çağrılmalı)."""
    import yt_dlp

    url = validate_url(url)
    try:
        with yt_dlp.YoutubeDL(base_options(skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp çok çeşitli hata türleri üretir
        detail = clean_error_text(str(exc))
        raise DownloadFailure(friendly_error(detail), detail=detail) from exc
    if not info:
        raise DownloadFailure("Bağlantıdan medya bilgisi alınamadı.")
    check_info(info)
    return to_media_info(url, info)


# ---------------------------------------------------------------- format ve dosya adı
def format_selector(kind: OperationKind, quality: str = "best") -> str:
    if kind is OperationKind.AUDIO_DOWNLOAD:
        return "ba/b"
    if quality == "best" or not str(quality).isdigit():
        # Önce MP4 uyumlu akışlar (birleştirmede yeniden kodlama gerekmez), sonra en iyi akışlar
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
    h = int(quality)
    return (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]"
            f"/bv*+ba/b")  # kaynakta bu sınırın altında akış yoksa mevcut en iyisi


def safe_stem(title: str) -> str:
    from yt_dlp.utils import sanitize_filename

    stem = sanitize_filename(title or "", restricted=False) or "medya"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")  # Windows için ek güvenlik
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if stem.upper() in reserved:
        stem = f"{stem}_"
    return (stem[:MAX_STEM_LENGTH].rstrip(" .") or "medya")


def files_with_stem(directory: Path, stem: str) -> set[str]:
    return {os.path.basename(p) for p in glob.glob(str(directory / f"{glob.escape(stem)}.*"))}


def unique_stem(directory: Path, title: str) -> str:
    """Klasörde bu kökle başlayan HİÇBİR dosya yoksa kökü kullanır; varsa 'Başlık (2)', 'Başlık (3)' ...

    Böylece indirmenin ürettiği her dosya (geçici parçalar dahil) yalnızca bu indirmeye ait olur.
    """
    base = safe_stem(title)
    stem, n = base, 2
    while files_with_stem(directory, stem):
        stem = f"{base} ({n})"
        n += 1
    return stem


def cleanup_partials(directory: Path, stem: str, preexisting: set[str] | frozenset = frozenset()) -> list[str]:
    """İptal/hata sonrası bu indirmenin bıraktığı dosyaları siler.

    Yalnızca indirme başladığında var OLMAYAN dosyalara dokunur; kullanıcının dosyaları korunur.
    """
    removed = []
    for name in sorted(files_with_stem(directory, stem) - set(preexisting)):
        try:
            os.remove(directory / name)
            removed.append(name)
        except OSError:
            pass
    return removed
