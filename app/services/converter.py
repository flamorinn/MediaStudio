"""Toplu MP3 dönüştürücü: FFprobe ile inceleme, FFmpeg ile dönüştürme, liste yönetimi.

Akış: dosya eklenir (İnceleniyor) → FFprobe: süre/ses akışı (Hazır veya Desteklenmiyor) →
Başlat (Sırada) → FFmpeg (Dönüştürülüyor, canlı ilerleme) → Tamamlandı / Başarısız / İptal edildi.
Çıktı önce geçici `Ad.converting.mp3` dosyasına yazılır; yalnızca başarıyla biterse `Ad.mp3` olur.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from app.core.errors import DependencyMissingError, MediaStudioError
from app.core.workers import Worker
from app.models.conversion import ConversionItem, ConversionStatus
from app.models.history import OperationKind, OperationStatus
from app.services.ffmpeg_runner import FFmpegJob
from app.utils.files import same_path, unique_output

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg",
                    ".ts", ".mts", ".m2ts", ".3gp", ".ogv")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS + AUDIO_EXTENSIONS
MAX_PARALLEL = 2
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ConversionFailure(MediaStudioError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    duration: float | None
    has_audio: bool
    has_video: bool
    size: int


_ERROR_RULES = (
    (("matches no streams", "does not contain any stream", "output file #0 does not contain"),
     "Dosyada ses akışı yok."),
    (("invalid data found", "moov atom not found", "could not find codec parameters", "end of file"),
     "Dosya bozuk veya desteklenmeyen bir biçimde."),
    (("no such file", "cannot find the file"), "Kaynak dosya bulunamadı (taşınmış veya silinmiş olabilir)."),
    (("permission denied", "access is denied"), "Çıktı klasörüne yazılamıyor (erişim reddedildi)."),
    (("no space left", "disk full"), "Diskte yeterli boş alan yok."),
)


def friendly_error(text: str) -> str:
    lowered = (text or "").lower()
    for needles, message in _ERROR_RULES:
        if any(n in lowered for n in needles):
            return message
    return "Dönüştürme başarısız oldu."


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def probe(ffprobe: str, path: str) -> ProbeResult:
    """Dosyanın süresini ve akışlarını okur (arka planda çağrılır)."""
    if not Path(path).is_file():
        raise ConversionFailure("Kaynak dosya bulunamadı.")
    try:
        proc = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=30, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConversionFailure("FFprobe çalıştırılamadı.", detail=str(exc)) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        raise ConversionFailure(friendly_error(detail) if detail else "Dosya okunamadı.", detail=detail)
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") not in (None, "N/A") else None
    except ValueError:
        duration = None
    has_video = any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
                    for s in streams)
    return ProbeResult(duration=duration, has_audio=any(s.get("codec_type") == "audio" for s in streams),
                       has_video=has_video, size=int(fmt.get("size") or Path(path).stat().st_size))


def build_command(ffmpeg: str, source: str, output: str, bitrate: int, extra_input_args=()) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", *extra_input_args,
        "-i", source,
        "-map", "0:a:0", "-vn", "-sn", "-dn",            # yalnızca ilk ses akışı
        "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
        "-map_metadata", "0", "-id3v2_version", "3",     # başlık/sanatçı etiketleri korunur
        "-progress", "pipe:1", "-nostats",
        "-f", "mp3", "-y", output,
    ]


class ConversionJob(FFmpegJob):
    """Tek dosyayı MP3'e dönüştürür (ortak FFmpegJob üzerine kurulu)."""

    failure_label = "Dönüştürme"

    def __init__(self, item: ConversionItem, ffmpeg: str, output: Path) -> None:
        super().__init__(item.id, ffmpeg, output, item.duration)
        self.source = item.source
        self.bitrate = item.bitrate

    def build_command(self, temp: str) -> list[str]:
        return build_command(self.ffmpeg, self.source, temp, self.bitrate, self.extra_input_args)

    def friendly_error(self, detail: str) -> str:
        return friendly_error(detail)


def _probe_job(item_id: int, ffprobe: str, path: str) -> tuple:
    try:
        return item_id, probe(ffprobe, path), None
    except Exception as exc:  # noqa: BLE001
        return item_id, None, exc


class ConversionManager(QObject):
    changed = Signal()          # öğe eklendi/kaldırıldı veya durum değişti
    item_updated = Signal(int)  # tek öğe güncellendi (ilerleme)

    def __init__(self, ctx, parent: QObject | None = None) -> None:
        super().__init__(parent or ctx)
        self.ctx = ctx
        self.items: dict[int, ConversionItem] = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(MAX_PARALLEL)
        self._jobs: dict[int, ConversionJob] = {}
        self._reserved: dict[int, str] = {}
        self._announced: set[int] = set()  # "tamamlandı" bildirimi verilmiş öğeler

    # ------------------------------------------------------------------ yardımcılar
    def _tools(self) -> tuple[str, str]:
        deps = self.ctx.dependencies
        if deps is None:
            raise DependencyMissingError("Sistem bileşenleri henüz kontrol ediliyor; birkaç saniye sonra deneyin.")
        if deps.ffmpeg is None or deps.ffprobe is None:
            raise DependencyMissingError("Dönüştürme için FFmpeg ve FFprobe gerekli. Ayarlar → Sistem bileşenleri.")
        return str(deps.ffmpeg.path), str(deps.ffprobe.path)

    def _update(self, item_id: int, **fields) -> ConversionItem | None:
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

    def list(self) -> list[ConversionItem]:
        return list(self.items.values())

    def counts(self) -> dict[ConversionStatus, int]:
        result: dict[ConversionStatus, int] = {}
        for item in self.items.values():
            result[item.status] = result.get(item.status, 0) + 1
        return result

    def startable(self) -> list[ConversionItem]:
        return [i for i in self.items.values() if i.status.can_start]

    def has_active(self) -> bool:
        return any(i.status in (ConversionStatus.QUEUED, ConversionStatus.CONVERTING) for i in self.items.values())

    # ------------------------------------------------------------------ ekleme
    def add_files(self, paths) -> tuple[list[ConversionItem], list[tuple[str, str]]]:
        """Dosyaları ekler; (eklenenler, [(yol, atlanma nedeni)]) döndürür."""
        _ffmpeg, ffprobe = self._tools()
        added, skipped = [], []
        for raw in paths:
            path = str(Path(raw))
            if Path(path).is_dir():
                skipped.append((path, "klasör"))
                continue
            if not is_supported(path):
                skipped.append((path, "desteklenmeyen biçim"))
                continue
            if any(same_path(path, i.source) for i in self.items.values()):
                skipped.append((path, "zaten listede"))
                continue
            item = ConversionItem(source=path)
            self.items[item.id] = item
            added.append(item)
            worker = Worker(_probe_job, item.id, ffprobe, path)
            worker.signals.result.connect(self._on_probed)
            self.ctx.thread_pool.start(worker)
        if added:
            self.changed.emit()
        return added, skipped

    def add_folder(self, folder: str | Path) -> tuple[list[ConversionItem], list[tuple[str, str]]]:
        folder = Path(folder)
        files = sorted((p for p in folder.iterdir() if p.is_file() and is_supported(p)),
                       key=lambda p: p.name.lower()) if folder.is_dir() else []
        return self.add_files(files)

    @Slot(object)
    def _on_probed(self, result: tuple) -> None:
        item_id, info, exc = result
        if item_id not in self.items:
            return
        if exc is not None:
            message = exc.user_message if isinstance(exc, MediaStudioError) else "Dosya okunamadı."
            detail = getattr(exc, "detail", "") or str(exc)
            self._update(item_id, status=ConversionStatus.INVALID, error=message, error_details=detail)
        elif not info.has_audio:
            self._update(item_id, status=ConversionStatus.INVALID, duration=info.duration, size_bytes=info.size,
                         has_video=info.has_video, error="Dosyada ses akışı yok.")
        else:
            self._update(item_id, status=ConversionStatus.READY, duration=info.duration, size_bytes=info.size,
                         has_video=info.has_video)

    # ------------------------------------------------------------------ başlatma / iptal
    def output_dir_for(self, item: ConversionItem, mode: str, custom_dir: str | None = None) -> str:
        if custom_dir:
            return custom_dir
        if mode == "source":
            return str(Path(item.source).parent)
        return str(self.ctx.settings_manager.download_dir())

    def start(self, bitrate: int, mode: str = "downloads", custom_dir: str | None = None,
              item_ids: list[int] | None = None) -> int:
        self._tools()
        targets = [i for i in self.startable() if item_ids is None or i.id in item_ids]
        for item in targets:
            self._update(item.id, status=ConversionStatus.QUEUED, bitrate=int(bitrate), progress=0.0, speed="",
                         eta_seconds=None, error="", error_details="", output_path="",
                         output_dir=self.output_dir_for(item, mode, custom_dir))
        self.schedule()
        return len(targets)

    def retry(self, item_id: int) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status not in (ConversionStatus.FAILED, ConversionStatus.CANCELLED):
            return False
        self._update(item_id, status=ConversionStatus.QUEUED, progress=0.0, error="", error_details="")
        self.schedule()
        return True

    def schedule(self) -> None:
        while len(self._jobs) < MAX_PARALLEL:
            item = next((i for i in self.items.values() if i.status is ConversionStatus.QUEUED), None)
            if item is None:
                return
            try:
                ffmpeg, _ = self._tools()
            except DependencyMissingError as exc:  # özyineleme yok: düz döngüde işaretlenir
                self._update(item.id, status=ConversionStatus.FAILED, error=exc.user_message)
                continue
            out_dir = Path(item.output_dir)
            output = unique_output(out_dir, Path(item.source).stem, "mp3", set(self._reserved.values()))
            if same_path(output, item.source):  # kaynak zaten bu adda bir MP3 ise üzerine yazma
                output = unique_output(out_dir, Path(item.source).stem, "mp3",
                                       set(self._reserved.values()) | {str(output)})
            self._reserved[item.id] = str(output)
            job = ConversionJob(item, ffmpeg, output)
            job.signals.progress.connect(self._on_progress)
            job.signals.succeeded.connect(self._on_succeeded)
            job.signals.failed.connect(self._on_failed)
            job.signals.cancelled.connect(self._on_cancelled)
            self._jobs[item.id] = job
            self._update(item.id, status=ConversionStatus.CONVERTING, output_path="")
            self.pool.start(job)

    def cancel(self, item_id: int) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        if item.status is ConversionStatus.QUEUED:
            self._update(item_id, status=ConversionStatus.CANCELLED, progress=0.0)
            return True
        job = self._jobs.get(item_id)
        if job is not None:
            job.cancel()
            return True
        return False

    def cancel_all(self) -> int:
        return sum(1 for item in list(self.items.values()) if self.cancel(item.id))

    def remove(self, item_id: int) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status.is_busy:
            return False
        del self.items[item_id]
        self.changed.emit()
        return True

    def clear(self, finished_only: bool = False) -> int:
        removable = [i.id for i in self.items.values()
                     if not i.status.is_busy and (not finished_only or i.status is ConversionStatus.COMPLETED)]
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
        item = self._finish(item_id, status=ConversionStatus.COMPLETED, progress=1.0, output_path=path)
        if item:
            self._record(item, OperationStatus.SUCCESS, size=size)

    @Slot(int, str, str)
    def _on_failed(self, item_id: int, message: str, detail: str) -> None:
        item = self._finish(item_id, status=ConversionStatus.FAILED, error=message, error_details=detail)
        if item:
            self._record(item, OperationStatus.FAILED, details=detail or message)
            self.ctx.notifications.error("Dönüştürme başarısız", f"{item.name}: {message}", detail)

    @Slot(int)
    def _on_cancelled(self, item_id: int) -> None:
        item = self._finish(item_id, status=ConversionStatus.CANCELLED, progress=0.0)
        if item:
            self._record(item, OperationStatus.CANCELLED)

    def _finish(self, item_id: int, **fields) -> ConversionItem | None:
        self._jobs.pop(item_id, None)
        self._reserved.pop(item_id, None)
        item = self._update(item_id, speed="", eta_seconds=None, **fields)
        self.schedule()
        if not self.has_active():
            self._announce_batch()
        return item

    def _announce_batch(self) -> None:
        done = [i for i in self.items.values()
                if i.status is ConversionStatus.COMPLETED and i.id not in self._announced]
        if done:
            self._announced.update(i.id for i in done)
            self.ctx.notifications.success("Dönüştürme tamamlandı",
                                           f"{len(done)} dosya MP3'e dönüştürüldü." if len(done) > 1 else done[0].name)

    def _record(self, item: ConversionItem, status: OperationStatus, size: int = 0, details: str = "") -> None:
        title = f"{item.name} → MP3" if status is not OperationStatus.SUCCESS else f"{item.name} → {Path(item.output_path).name}"
        try:
            self.ctx.history.add(kind=OperationKind.CONVERT, title=title, status=status, source=item.source,
                                 output_path=item.output_path, size_bytes=size, details=details)
        except Exception as exc:  # noqa: BLE001
            log.error("Geçmiş kaydı yazılamadı: %s", exc)
        self.ctx.history_changed.emit()
