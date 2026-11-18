"""İndirme motoru: tek bir indirmeyi yürüten iş (DownloadJob) ve kuyruğu yöneten DownloadManager.

Akış:  enqueue() → kuyruk (Sırada) → boş yuva varsa iş başlar (İndiriliyor) → gerekiyorsa
birleştirme/MP3 dönüştürme (İşleniyor) → Tamamlandı / Başarısız / İptal edildi → geçmişe kayıt.
İşler kendi QThreadPool'unda çalışır; ilerleme sinyallerle ana iş parçacığına iletilir.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from app.core.errors import DependencyMissingError, MediaStudioError
from app.models.download import DownloadStatus, DownloadTask
from app.models.history import OperationKind, OperationStatus
from app.services.ytdlp_support import (
    DownloadFailure, base_options, check_info, clean_error_text, cleanup_partials, files_with_stem,
    format_selector, friendly_error, unique_stem, validate_url,
)

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 0.2  # saniye: arayüze en fazla bu sıklıkta ilerleme gönderilir
_PP_PHASES = {
    "Merger": "Görüntü ve ses birleştiriliyor",
    "FFmpegMerger": "Görüntü ve ses birleştiriliyor",
    "ExtractAudio": "MP3'e dönüştürülüyor",
    "FFmpegExtractAudio": "MP3'e dönüştürülüyor",
    "Metadata": "Etiketler yazılıyor",
    "FFmpegMetadata": "Etiketler yazılıyor",
}


class JobSignals(QObject):
    started = Signal(int, str, int)          # task_id, başlık, tahmini toplam bayt
    progress = Signal(int, object)           # task_id, {progress, speed_bps, eta_seconds, downloaded, total, phase}
    processing = Signal(int, str)            # task_id, aşama metni
    succeeded = Signal(int, str, int)        # task_id, dosya yolu, boyut
    failed = Signal(int, str, str)           # task_id, kullanıcı mesajı, ayrıntı
    cancelled = Signal(int)


class _Cancelled(Exception):
    pass


class DownloadJob(QRunnable):
    def __init__(self, task: DownloadTask, ffmpeg_path: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.task_id = task.id
        self.url = task.url
        self.kind = task.kind
        self.quality = task.quality
        self.bitrate = task.audio_bitrate
        self.output_dir = Path(task.output_dir)
        self.ffmpeg_path = ffmpeg_path
        self.signals = JobSignals()
        self._cancel = threading.Event()
        self._stem: str | None = None
        self._preexisting: set[str] = set()
        self._parts = 1
        self._done_parts = 0
        self._last_emit = 0.0

    # ------------------------------------------------------------------ dış API
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------ yt-dlp kancaları
    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            from yt_dlp.utils import DownloadCancelled

            raise DownloadCancelled("Kullanıcı iptal etti")

    def _progress_hook(self, d: dict) -> None:
        self._check_cancel()
        status = d.get("status")
        if status == "finished":
            self._done_parts = min(self._done_parts + 1, self._parts)
            self._emit_progress(1.0, d, force=True)
            return
        if status != "downloading":
            return
        downloaded = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total:
            fraction = min(downloaded / total, 1.0)
        elif d.get("fragment_count"):
            fraction = min((d.get("fragment_index") or 0) / d["fragment_count"], 1.0)
        else:
            fraction = None
        self._emit_progress(fraction, d)

    def _emit_progress(self, fraction: float | None, d: dict, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < PROGRESS_INTERVAL:
            return
        self._last_emit = now
        if fraction is None:
            overall = None
        else:
            done = self._done_parts if d.get("status") != "finished" else self._done_parts - 1
            overall = min((max(done, 0) + fraction) / self._parts, 1.0)
        info = d.get("info_dict") or {}
        if self._parts > 1:
            stream = "Ses akışı" if info.get("vcodec") == "none" else "Görüntü akışı"
            phase = f"{stream} ({min(self._done_parts + 1, self._parts)}/{self._parts})"
        else:
            phase = "İndiriliyor"
        self.signals.progress.emit(self.task_id, {
            "progress": overall,
            "speed_bps": float(d.get("speed") or 0.0),
            "eta_seconds": d.get("eta"),
            "downloaded_bytes": int(d.get("downloaded_bytes") or 0),
            "phase": phase,
        })

    def _postprocessor_hook(self, d: dict) -> None:
        self._check_cancel()
        if d.get("status") == "started":
            phase = _PP_PHASES.get(d.get("postprocessor", ""))
            if phase:
                self.signals.processing.emit(self.task_id, phase)

    # ------------------------------------------------------------------ çalıştırma
    def _options(self, outtmpl: str | None = None) -> dict:
        opts = base_options(
            format=format_selector(self.kind, self.quality),
            ffmpeg_location=self.ffmpeg_path,
            progress_hooks=[self._progress_hook],
            postprocessor_hooks=[self._postprocessor_hook],
        )
        if outtmpl:
            opts["outtmpl"] = outtmpl
        if self.kind is OperationKind.AUDIO_DOWNLOAD:
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(self.bitrate)},
                {"key": "FFmpegMetadata"},
            ]
        else:
            opts["merge_output_format"] = "mp4"
            opts["postprocessors"] = [{"key": "FFmpegMetadata"}]
        return opts

    def run(self) -> None:  # iş parçacığında çalışır
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled

        try:
            if self._cancel.is_set():
                raise _Cancelled
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with yt_dlp.YoutubeDL(self._options()) as probe:
                info = probe.extract_info(self.url, download=False)
            if not info:
                raise DownloadFailure("Bağlantıdan medya bilgisi alınamadı.")
            check_info(info)
            self._check_cancel()

            requested = info.get("requested_formats") or []
            self._parts = max(len(requested), 1)
            estimate = sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in requested) \
                or int(info.get("filesize") or info.get("filesize_approx") or 0)
            title = (info.get("title") or "").strip() or "medya"
            self._stem = unique_stem(self.output_dir, title)
            self._preexisting = files_with_stem(self.output_dir, self._stem)
            self.signals.started.emit(self.task_id, title, estimate)

            outtmpl = str(self.output_dir / f"{self._stem}.%(ext)s")
            with yt_dlp.YoutubeDL(self._options(outtmpl)) as ydl:
                result = ydl.process_ie_result(info, download=True)
            self._check_cancel()

            downloads = (result or {}).get("requested_downloads") or []
            path = Path(downloads[0].get("filepath")) if downloads and downloads[0].get("filepath") else None
            if path is None or not path.is_file():
                expected = ".mp3" if self.kind is OperationKind.AUDIO_DOWNLOAD else ".mp4"
                candidates = [self.output_dir / n for n in files_with_stem(self.output_dir, self._stem)
                              if n.endswith(expected)]
                path = candidates[0] if candidates else None
            if path is None or not path.is_file():
                raise DownloadFailure("İndirme tamamlandı ancak çıktı dosyası bulunamadı.")
            self.signals.succeeded.emit(self.task_id, str(path), path.stat().st_size)
        except (_Cancelled, DownloadCancelled):
            self._cleanup()
            self.signals.cancelled.emit(self.task_id)
        except Exception as exc:  # noqa: BLE001 - her hata kullanıcıya anlaşılır biçimde iletilir
            self._cleanup()
            if self._cancel.is_set():
                self.signals.cancelled.emit(self.task_id)
                return
            if isinstance(exc, MediaStudioError):
                message, detail = exc.user_message, exc.detail or exc.user_message
            else:
                detail = clean_error_text(str(exc)) or exc.__class__.__name__
                message = friendly_error(detail)
            log.warning("İndirme başarısız (%s): %s", self.url, detail)
            log.debug(traceback.format_exc())
            self.signals.failed.emit(self.task_id, message, detail)

    def _cleanup(self) -> None:
        if self._stem:
            removed = cleanup_partials(self.output_dir, self._stem, self._preexisting)
            if removed:
                log.info("Geçici dosyalar temizlendi: %s", removed)


class DownloadManager(QObject):
    """Kuyruğu yürütür: eşzamanlılık sınırı, başlatma, iptal, yeniden deneme, geçmiş kaydı."""

    def __init__(self, ctx, parent: QObject | None = None) -> None:
        super().__init__(parent or ctx)
        self.ctx = ctx
        self.queue = ctx.queue
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(5)
        self._jobs: dict[int, DownloadJob] = {}
        ctx.settings_changed.connect(lambda keys: self.schedule() if "max_concurrent_downloads" in keys else None)

    # ------------------------------------------------------------------ hazırlık
    def check_ready(self, kind: OperationKind) -> None:
        deps = self.ctx.dependencies
        if deps is None:
            raise DependencyMissingError("Sistem bileşenleri henüz kontrol ediliyor; birkaç saniye sonra deneyin.")
        if deps.ytdlp is None:
            raise DependencyMissingError("yt-dlp bulunamadı; indirme yapılamıyor.")
        if deps.ffmpeg is None:
            what = "MP3 dönüştürme" if kind is OperationKind.AUDIO_DOWNLOAD else "görüntü ve sesi birleştirme"
            raise DependencyMissingError(f"FFmpeg bulunamadı; {what} için gerekli. Ayarlar → Sistem bileşenleri.")

    # ------------------------------------------------------------------ API
    def enqueue(self, url: str, kind: OperationKind, *, title: str = "", quality: str | None = None,
                audio_bitrate: int | None = None, output_dir: str | None = None) -> DownloadTask:
        url = validate_url(url)
        self.check_ready(kind)
        settings = self.ctx.settings
        task = DownloadTask(
            url=url, kind=kind, title=title,
            quality=quality or settings.video_quality,
            audio_bitrate=int(audio_bitrate or settings.audio_bitrate),
            output_dir=output_dir or str(self.ctx.settings_manager.download_dir()),
            phase="Sırada bekliyor",
        )
        self.queue.add(task)
        log.info("Kuyruğa eklendi #%s %s (%s)", task.id, url, kind.value)
        self.schedule()
        return task

    def schedule(self) -> None:
        limit = self.ctx.settings.max_concurrent_downloads
        while len(self._jobs) < limit:
            task = self.queue.next_queued()
            if task is None:
                return
            self._start(task)

    def cancel(self, task_id: int) -> bool:
        task = self.queue.get(task_id)
        if task is None or task.status.is_finished:
            return False
        job = self._jobs.get(task_id)
        if job is None:  # henüz başlamamış
            self.queue.update(task_id, status=DownloadStatus.CANCELLED, phase="", speed_bps=0.0, eta_seconds=None)
            self._record(task, OperationStatus.CANCELLED)
            return True
        job.cancel()
        self.queue.update(task_id, phase="İptal ediliyor…")
        return True

    def retry(self, task_id: int) -> bool:
        task = self.queue.get(task_id)
        if task is None or task.status not in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            return False
        self.queue.update(task_id, status=DownloadStatus.QUEUED, progress=0.0, speed_bps=0.0, eta_seconds=None,
                          downloaded_bytes=0, error="", error_details="", output_path="", phase="Sırada bekliyor")
        self.schedule()
        return True

    def cancel_all(self) -> int:
        count = 0
        for task in self.queue.tasks():
            if not task.status.is_finished and self.cancel(task.id):
                count += 1
        return count

    def has_active(self) -> bool:
        return bool(self._jobs) or self.queue.pending_count() > 0

    def shutdown(self, timeout_ms: int = 8000) -> bool:
        self.cancel_all()
        return self.pool.waitForDone(timeout_ms)

    # ------------------------------------------------------------------ iç
    def _start(self, task: DownloadTask) -> None:
        deps = self.ctx.dependencies
        ffmpeg = str(deps.ffmpeg.path) if deps and deps.ffmpeg else ""
        job = DownloadJob(task, ffmpeg)
        s = job.signals
        s.started.connect(self._on_started)
        s.progress.connect(self._on_progress)
        s.processing.connect(self._on_processing)
        s.succeeded.connect(self._on_succeeded)
        s.failed.connect(self._on_failed)
        s.cancelled.connect(self._on_cancelled)
        self._jobs[task.id] = job
        self.queue.update(task.id, status=DownloadStatus.RUNNING, phase="Bağlantı analiz ediliyor",
                          progress=None, error="", error_details="")
        self.pool.start(job)

    @Slot(int, str, int)
    def _on_started(self, task_id: int, title: str, total: int) -> None:
        if task_id in self._jobs and self.queue.get(task_id):
            self.queue.update(task_id, title=title, total_bytes=total, phase="İndirme başlıyor", progress=0.0)

    @Slot(int, object)
    def _on_progress(self, task_id: int, data: dict) -> None:
        job = self._jobs.get(task_id)
        if job is None or job.cancel_requested or self.queue.get(task_id) is None:
            return
        self.queue.update(task_id, **data)

    @Slot(int, str)
    def _on_processing(self, task_id: int, phase: str) -> None:
        job = self._jobs.get(task_id)
        if job is None or job.cancel_requested or self.queue.get(task_id) is None:
            return
        self.queue.update(task_id, status=DownloadStatus.PROCESSING, phase=phase, progress=None,
                          speed_bps=0.0, eta_seconds=None)

    @Slot(int, str, int)
    def _on_succeeded(self, task_id: int, path: str, size: int) -> None:
        task = self._finish(task_id, status=DownloadStatus.COMPLETED, progress=1.0, output_path=path,
                            total_bytes=size, downloaded_bytes=size, phase="")
        if task:
            self._record(task, OperationStatus.SUCCESS, size=size)
            self.ctx.notifications.success("İndirme tamamlandı", task.display_title)

    @Slot(int, str, str)
    def _on_failed(self, task_id: int, message: str, detail: str) -> None:
        task = self._finish(task_id, status=DownloadStatus.FAILED, error=message, error_details=detail, phase="")
        if task:
            self._record(task, OperationStatus.FAILED, details=detail or message)
            self.ctx.notifications.error("İndirme başarısız", f"{task.display_title}: {message}", detail)

    @Slot(int)
    def _on_cancelled(self, task_id: int) -> None:
        task = self._finish(task_id, status=DownloadStatus.CANCELLED, phase="", progress=0.0)
        if task:
            self._record(task, OperationStatus.CANCELLED)

    def _finish(self, task_id: int, **fields) -> DownloadTask | None:
        self._jobs.pop(task_id, None)
        task = None
        if self.queue.get(task_id) is not None:
            task = self.queue.update(task_id, speed_bps=0.0, eta_seconds=None, **fields)
        self.schedule()
        return task

    def _record(self, task: DownloadTask, status: OperationStatus, size: int = 0, details: str = "") -> None:
        try:
            self.ctx.history.add(kind=task.kind, title=task.display_title, status=status, source=task.url,
                                 output_path=task.output_path, size_bytes=size, details=details)
        except Exception as exc:  # noqa: BLE001 - geçmiş yazılamazsa indirme sonucu kaybolmasın
            log.error("Geçmiş kaydı yazılamadı: %s", exc)
        self.ctx.history_changed.emit()
