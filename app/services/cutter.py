"""Video kesme: seçilen zaman aralığını MP4 (video) veya MP3 (ses) olarak dışa aktarır.

Kesim modları (MP4):
  * Kayıpsız  – akışlar yeniden kodlanmadan kopyalanır: kalite kaybı yok, çok hızlı. Video yalnızca
                anahtar karelerden başlayabildiği için başlangıç, seçilen andan önceki en yakın anahtar
                kareye çekilir (kullanıcıya önceden gösterilir). Kodekler MP4'e uygun değilse kullanılamaz.
  * Kare hassasiyetinde – yeniden kodlanır (H.264 CRF 18 + AAC 192k): tam seçilen kareden başlar.
MP3 çıktısı her zaman ses akışını seçilen bit hızında kodlar (örnek hassasiyetinde).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from app.core.errors import DependencyMissingError, MediaStudioError
from app.models.cut import CutFormat, CutJobItem, CutMode, CutStatus, MediaDetails
from app.models.history import OperationKind, OperationStatus
from app.services.ffmpeg_runner import NO_WINDOW, FFmpegJob
from app.utils.files import same_path, unique_output
from app.utils.timecode import filename_timecode, format_timecode

log = logging.getLogger(__name__)

MIN_LENGTH = 0.1          # saniye
MAX_PARALLEL = 1          # kesim CPU yoğun olabilir; sırayla işlenir
KEYFRAME_TOLERANCE = 0.05  # arayüz: başlangıç bu kadar yakınsa "anahtar kareye denk" sayılır
ROUNDING_EPSILON = 0.001   # zaman damgası yuvarlama payı; kesim asla istenen andan SONRA başlamaz


class CutFailure(MediaStudioError):
    pass


_ERROR_RULES = (
    (("could not find tag for codec", "not currently supported in container", "codec not currently supported"),
     "Bu kodek MP4 içine kopyalanamıyor; 'Kare hassasiyetinde' modunu kullanın."),
    (("matches no streams",), "Seçilen çıktı için gerekli akış dosyada yok."),
    (("invalid data found", "moov atom not found"), "Dosya bozuk veya desteklenmeyen bir biçimde."),
    (("no such file", "cannot find the file"), "Kaynak dosya bulunamadı (taşınmış veya silinmiş olabilir)."),
    (("permission denied", "access is denied"), "Çıktı klasörüne yazılamıyor (erişim reddedildi)."),
    (("no space left", "disk full"), "Diskte yeterli boş alan yok."),
)


def friendly_error(text: str) -> str:
    lowered = (text or "").lower()
    for needles, message in _ERROR_RULES:
        if any(n in lowered for n in needles):
            return message
    return "Kesme başarısız oldu."


def _run(args: list[str], timeout: float = 30, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                          **({"text": True, "encoding": "utf-8", "errors": "replace"} if text else {}))


# ---------------------------------------------------------------- inceleme
def _fps(rate: str) -> float:
    try:
        num, _, den = (rate or "0/1").partition("/")
        return float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        return 0.0


def probe_details(ffprobe: str, path: str) -> MediaDetails:
    if not Path(path).is_file():
        raise CutFailure("Kaynak dosya bulunamadı.")
    try:
        proc = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    except (OSError, subprocess.SubprocessError) as exc:
        raise CutFailure("FFprobe çalıştırılamadı.", detail=str(exc)) from exc
    if proc.returncode != 0:
        raise CutFailure("Dosya okunamadı; bozuk veya desteklenmeyen bir biçimde.", detail=proc.stderr.strip())
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise CutFailure("Dosyanın süresi okunamadı; kesme yapılamaz.")
    if video is None and audio is None:
        raise CutFailure("Dosyada görüntü veya ses akışı yok.")
    return MediaDetails(
        path=path, duration=duration, has_video=video is not None, has_audio=audio is not None,
        video_codec=(video or {}).get("codec_name", ""), audio_codec=(audio or {}).get("codec_name", ""),
        width=int((video or {}).get("width") or 0), height=int((video or {}).get("height") or 0),
        fps=_fps((video or {}).get("avg_frame_rate", "")), size=int(fmt.get("size") or 0),
    )


def keyframe_before(ffprobe: str, path: str, t: float) -> float | None:
    """`t` anına eşit veya ondan önceki en yakın video anahtar karesinin zamanı (yoksa None).

    Tüm dosyayı taramamak için yalnızca `t`'nin öncesindeki bir pencere okunur; pencerede anahtar
    kare yoksa pencere genişletilir.
    """
    for window in (20, 120, None):
        start = 0 if window is None else max(t - window, 0)
        interval = f"{start:.3f}%{t + 0.1:.3f}"
        try:
            proc = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                         "-show_entries", "frame=best_effort_timestamp_time,pts_time", "-of", "csv=p=0",
                         "-read_intervals", interval, path], timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        times = []
        for line in proc.stdout.splitlines():
            for value in line.split(","):
                try:
                    times.append(float(value))
                    break
                except ValueError:
                    continue
        candidates = [x for x in times if x <= t + ROUNDING_EPSILON]
        if candidates:
            return max(candidates)
        if start == 0:
            return None
    return None


def extract_frame(ffmpeg: str, path: str, t: float, width: int = 360) -> bytes:
    """`t` anındaki kareyi PNG baytları olarak döndürür (önizleme için)."""
    try:
        proc = _run([ffmpeg, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
                     "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                    timeout=20, text=False)
    except (OSError, subprocess.SubprocessError):
        return b""
    return proc.stdout if proc.returncode == 0 else b""


# ---------------------------------------------------------------- doğrulama ve komut
def validate_range(start: float, end: float, duration: float) -> None:
    if start < 0:
        raise CutFailure("Başlangıç zamanı negatif olamaz.")
    if end > duration + 0.001:
        raise CutFailure(f"Bitiş zamanı videonun süresini ({format_timecode(duration)}) aşıyor.")
    if end - start < MIN_LENGTH:
        raise CutFailure("Bitiş zamanı başlangıçtan en az 0,1 saniye sonra olmalı.")


def build_cut_command(ffmpeg: str, item: CutJobItem, output: str, extra_input_args=()) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", *extra_input_args,
           "-ss", f"{item.start:.3f}", "-i", item.source, "-t", f"{item.length:.3f}"]
    if item.fmt is CutFormat.MP3:
        cmd += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", f"{item.bitrate}k",
                "-map_metadata", "0", "-id3v2_version", "3"]
        container = "mp3"
    elif item.mode is CutMode.LOSSLESS:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
                "-map_metadata", "0", "-movflags", "+faststart"]
        container = "mp4"
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-map_metadata", "0",
                "-movflags", "+faststart"]
        container = "mp4"
    return cmd + ["-progress", "pipe:1", "-nostats", "-f", container, "-y", output]


def output_stem(item: CutJobItem) -> str:
    return f"{Path(item.source).stem} ({filename_timecode(item.start)}-{filename_timecode(item.end)})"


class CutJob(FFmpegJob):
    failure_label = "Kesme"

    def __init__(self, item: CutJobItem, ffmpeg: str, output: Path) -> None:
        super().__init__(item.id, ffmpeg, output, item.output_length)
        self.item = item

    def build_command(self, temp: str) -> list[str]:
        return build_cut_command(self.ffmpeg, self.item, temp, self.extra_input_args)

    def friendly_error(self, detail: str) -> str:
        return friendly_error(detail)


class CutManager(QObject):
    changed = Signal()
    item_updated = Signal(int)

    def __init__(self, ctx, parent: QObject | None = None) -> None:
        super().__init__(parent or ctx)
        self.ctx = ctx
        self.items: dict[int, CutJobItem] = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(MAX_PARALLEL)
        self._jobs: dict[int, CutJob] = {}
        self._reserved: dict[int, str] = {}

    # ------------------------------------------------------------------ yardımcılar
    def tools(self) -> tuple[str, str]:
        deps = self.ctx.dependencies
        if deps is None:
            raise DependencyMissingError("Sistem bileşenleri henüz kontrol ediliyor; birkaç saniye sonra deneyin.")
        if deps.ffmpeg is None or deps.ffprobe is None:
            raise DependencyMissingError("Kesme için FFmpeg ve FFprobe gerekli. Ayarlar → Sistem bileşenleri.")
        return str(deps.ffmpeg.path), str(deps.ffprobe.path)

    def _update(self, item_id: int, **fields) -> CutJobItem | None:
        item = self.items.get(item_id)
        if item is None:
            return None
        status_changed = "status" in fields and fields["status"] != item.status
        for key, value in fields.items():
            setattr(item, key, value)
        self.item_updated.emit(item_id)
        if status_changed:
            self.changed.emit()
        return item

    def list(self) -> list[CutJobItem]:
        return list(self.items.values())

    def has_active(self) -> bool:
        return any(i.status.is_active for i in self.items.values())

    # ------------------------------------------------------------------ API
    def export(self, details: MediaDetails, start: float, end: float, fmt: CutFormat, *,
               mode: CutMode = CutMode.LOSSLESS, bitrate: int = 192, output_dir: str,
               keyframe: float | None = None) -> CutJobItem:
        """Kesimi kuyruğa ekler. `keyframe`: kayıpsız kesimde kullanılacak anahtar kare (bilinmiyorsa hesaplanır)."""
        _ffmpeg, ffprobe = self.tools()
        validate_range(start, end, details.duration)
        fmt, mode = CutFormat(fmt), CutMode(mode)
        if fmt is CutFormat.MP4 and not details.has_video:
            raise CutFailure("Dosyada görüntü akışı yok; yalnızca MP3 olarak dışa aktarılabilir.")
        if fmt is CutFormat.MP3 and not details.has_audio:
            raise CutFailure("Dosyada ses akışı yok; MP3 olarak dışa aktarılamaz.")
        if fmt is CutFormat.MP4 and mode is CutMode.LOSSLESS and not details.lossless_mp4_possible:
            raise CutFailure("Bu dosyanın kodekleri MP4'e kayıpsız kopyalanamıyor; 'Kare hassasiyetinde' modunu seçin.")
        effective = None
        if fmt is CutFormat.MP4 and mode is CutMode.LOSSLESS:
            effective = keyframe if keyframe is not None else keyframe_before(ffprobe, details.path, start)
            if effective is None:
                effective = start
        item = CutJobItem(source=details.path, start=start, end=end, fmt=fmt, mode=mode, bitrate=int(bitrate),
                          output_dir=output_dir, effective_start=effective)
        self.items[item.id] = item
        self.changed.emit()
        self.schedule()
        return item

    def schedule(self) -> None:
        while len(self._jobs) < MAX_PARALLEL:
            item = next((i for i in self.items.values() if i.status is CutStatus.QUEUED), None)
            if item is None:
                return
            try:
                ffmpeg, _ = self.tools()
            except DependencyMissingError as exc:
                self._update(item.id, status=CutStatus.FAILED, error=exc.user_message)
                continue
            directory = Path(item.output_dir)
            reserved = set(self._reserved.values())
            output = unique_output(directory, output_stem(item), item.fmt.value, reserved)
            if same_path(output, item.source):
                output = unique_output(directory, output_stem(item), item.fmt.value, reserved | {str(output)})
            self._reserved[item.id] = str(output)
            job = CutJob(item, ffmpeg, output)
            job.signals.progress.connect(self._on_progress)
            job.signals.succeeded.connect(self._on_succeeded)
            job.signals.failed.connect(self._on_failed)
            job.signals.cancelled.connect(self._on_cancelled)
            self._jobs[item.id] = job
            self._update(item.id, status=CutStatus.RUNNING, progress=0.0)
            self.pool.start(job)

    def cancel(self, item_id: int) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        if item.status is CutStatus.QUEUED and item_id not in self._jobs:
            self._update(item_id, status=CutStatus.CANCELLED)
            return True
        job = self._jobs.get(item_id)
        if job is not None:
            job.cancel()
            return True
        return False

    def cancel_all(self) -> int:
        return sum(1 for item in list(self.items.values()) if item.status.is_active and self.cancel(item.id))

    def retry(self, item_id: int) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status not in (CutStatus.FAILED, CutStatus.CANCELLED):
            return False
        self._update(item_id, status=CutStatus.QUEUED, progress=0.0, error="", error_details="", output_path="")
        self.schedule()
        return True

    def remove(self, item_id: int) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status.is_active:
            return False
        del self.items[item_id]
        self.changed.emit()
        return True

    def clear(self) -> int:
        removable = [i.id for i in self.items.values() if not i.status.is_active]
        for item_id in removable:
            del self.items[item_id]
        if removable:
            self.changed.emit()
        return len(removable)

    def shutdown(self, timeout_ms: int = 8000) -> bool:
        self.cancel_all()
        return self.pool.waitForDone(timeout_ms)

    # ------------------------------------------------------------------ iş sonuçları
    @Slot(int, float, str, object)
    def _on_progress(self, item_id: int, fraction: float, speed: str, eta) -> None:
        if item_id in self._jobs:
            self._update(item_id, progress=fraction, speed=speed, eta_seconds=eta)

    @Slot(int, str, int)
    def _on_succeeded(self, item_id: int, path: str, size: int) -> None:
        item = self._finish(item_id, status=CutStatus.COMPLETED, progress=1.0, output_path=path)
        if item:
            self._record(item, OperationStatus.SUCCESS, size)
            self.ctx.notifications.success("Kesim tamamlandı", Path(path).name)

    @Slot(int, str, str)
    def _on_failed(self, item_id: int, message: str, detail: str) -> None:
        item = self._finish(item_id, status=CutStatus.FAILED, error=message, error_details=detail)
        if item:
            self._record(item, OperationStatus.FAILED, details=detail or message)
            self.ctx.notifications.error("Kesme başarısız", f"{item.name}: {message}", detail)

    @Slot(int)
    def _on_cancelled(self, item_id: int) -> None:
        item = self._finish(item_id, status=CutStatus.CANCELLED, progress=0.0)
        if item:
            self._record(item, OperationStatus.CANCELLED)

    def _finish(self, item_id: int, **fields) -> CutJobItem | None:
        self._jobs.pop(item_id, None)
        self._reserved.pop(item_id, None)
        item = self._update(item_id, speed="", eta_seconds=None, **fields)
        self.schedule()
        return item

    def _record(self, item: CutJobItem, status: OperationStatus, size: int = 0, details: str = "") -> None:
        span = f"{format_timecode(item.start, millis=False)}–{format_timecode(item.end, millis=False)}"
        target = Path(item.output_path).name if item.output_path else item.fmt.value.upper()
        try:
            self.ctx.history.add(kind=OperationKind.CUT, title=f"{item.name} [{span}] → {target}", status=status,
                                 source=item.source, output_path=item.output_path, size_bytes=size, details=details)
        except Exception as exc:  # noqa: BLE001
            log.error("Geçmiş kaydı yazılamadı: %s", exc)
        self.ctx.history_changed.emit()
