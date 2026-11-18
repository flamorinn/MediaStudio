"""İndirme motoru: tek bir indirmeyi yürüten iş (DownloadJob) ve kuyruğu yöneten DownloadManager.

Akış:  enqueue() → kuyruk (Sırada) → boş yuva varsa iş başlar (İndiriliyor) → gerekiyorsa
birleştirme/MP3 dönüştürme (İşleniyor) → Tamamlandı / Başarısız / İptal edildi → geçmişe kayıt.
İşler kendi QThreadPool'unda çalışır; ilerleme sinyallerle ana iş parçacığına iletilir.
"""

from __future__ import annotations

import copy
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
    PREFETCH_MAX_AGE, DownloadFailure, base_options, check_info, clean_error_text, cleanup_partials,
    expected_section_bytes, files_with_stem, format_selector, friendly_error, unique_stem, validate_section,
    validate_url,
)
from app.utils.timecode import filename_timecode, format_timecode

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


# ---------------------------------------------------------------- FFmpeg süreç takibi
# Aralık indirmede yt-dlp işi FFmpeg'e devreder ve ilerleme kancası çağırmaz; bu yüzden iptal
# kancayla yapılamaz. yt-dlp'nin harici indiricide kullandığı Popen sınıfı sarmalanır: her süreç,
# onu başlatan iş parçacığına göre kaydedilir ve iptalde yalnızca o işin süreçleri durdurulur.
_PROCS: dict[int, list] = {}
_PROCS_LOCK = threading.Lock()


def install_process_tracker() -> None:
    import yt_dlp.downloader.external as external

    if getattr(external, "_mediastudio_tracked", False):
        return
    base = external.Popen

    class TrackedPopen(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with _PROCS_LOCK:
                _PROCS.setdefault(threading.get_ident(), []).append(self)

    external.Popen = TrackedPopen
    external._mediastudio_tracked = True


def _kill_processes(thread_id: int | None) -> None:
    if thread_id is None:
        return
    with _PROCS_LOCK:
        procs = list(_PROCS.get(thread_id, []))
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
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
        self.section = (task.section_start, task.section_end) if task.has_section else None
        fresh = task.prefetched_info is not None and time.time() - task.prefetched_at < PREFETCH_MAX_AGE
        self.prefetched = copy.deepcopy(task.prefetched_info) if fresh else None
        self.signals = JobSignals()
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._thread_id: int | None = None
        self._stem: str | None = None
        self._preexisting: set[str] = set()
        self._parts = 1
        self._done_parts = 0
        self._last_emit = 0.0

    # ------------------------------------------------------------------ dış API
    def cancel(self) -> None:
        self._cancel.set()
        _kill_processes(self._thread_id)  # aralık indirmede çalışan FFmpeg hemen durur

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    @property
    def used_prefetched_info(self) -> bool:
        return self.prefetched is not None

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
            if not self.section:
                self._emit_progress(1.0, d, force=True)
            return
        if status != "downloading" or self.section:
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

    def _watch_section(self, expected: int) -> None:
        """Aralık indirmede ilerlemeyi, yazılan dosyaların büyümesinden hesaplar (ayrı iş parçacığı)."""
        start, end = self.section
        span = f"{format_timecode(start, millis=False)}–{format_timecode(end, millis=False)}"
        last_size, last_time, speed = 0, time.monotonic(), 0.0
        while not self._done.wait(0.5):
            size = 0
            for name in files_with_stem(self.output_dir, self._stem) - self._preexisting:
                try:
                    size += (self.output_dir / name).stat().st_size
                except OSError:
                    pass
            now = time.monotonic()
            if size > last_size:
                current = (size - last_size) / max(now - last_time, 1e-3)
                speed = current if not speed else speed * 0.6 + current * 0.4
            last_size, last_time = size, now
            fraction = min(size / expected, 0.99) if expected else None
            eta = int((expected - size) / speed) if expected and speed and size < expected else None
            self.signals.progress.emit(self.task_id, {
                "progress": fraction, "speed_bps": speed, "eta_seconds": eta,
                "downloaded_bytes": size, "phase": f"Aralık indiriliyor ({span})",
            })

    # ------------------------------------------------------------------ çalıştırma
    def _options(self, outtmpl: str | None = None) -> dict:
        opts = base_options(
            format=format_selector(self.kind, self.quality, ranged=self.section is not None),
            ffmpeg_location=self.ffmpeg_path,
            progress_hooks=[self._progress_hook],
            postprocessor_hooks=[self._postprocessor_hook],
        )
        if outtmpl:
            opts["outtmpl"] = outtmpl
        if self.section:
            from yt_dlp.utils import download_range_func

            # Yalnızca seçilen aralık indirilir; kesim tam seçilen kareden başlar (kenarlar yeniden kodlanır)
            opts["download_ranges"] = download_range_func(None, [self.section])
            opts["force_keyframes_at_cuts"] = True
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

        self._thread_id = threading.get_ident()
        install_process_tracker()
        watcher = None
        try:
            if self._cancel.is_set():
                raise _Cancelled
            self.output_dir.mkdir(parents=True, exist_ok=True)
            if self.prefetched is not None:
                info = self.prefetched  # analiz sonucu yeniden kullanılır: bağlantı tekrar çekilmez
            else:
                with yt_dlp.YoutubeDL(self._options()) as probe:
                    info = probe.extract_info(self.url, download=False)
            if not info:
                raise DownloadFailure("Bağlantıdan medya bilgisi alınamadı.")
            check_info(info)
            if self.section:
                validate_section(*self.section, info.get("duration"))
            self._check_cancel()

            title = (info.get("title") or "").strip() or "medya"
            stem_title = title
            if self.section:
                stem_title = f"{title} ({filename_timecode(self.section[0])}-{filename_timecode(self.section[1])})"
            self._stem = unique_stem(self.output_dir, stem_title)
            self._preexisting = files_with_stem(self.output_dir, self._stem)

            outtmpl = str(self.output_dir / f"{self._stem}.%(ext)s")
            with yt_dlp.YoutubeDL(self._options(outtmpl)) as ydl:
                # Format seçimi bu işin seçicisiyle (gerekirse aralık için HLS öncelikli) yeniden yapılır
                selected = ydl.process_ie_result(info, download=False)
                requested = selected.get("requested_formats") or []
                self._parts = max(len(requested), 1)
                if self.section:
                    estimate = expected_section_bytes(selected, *self.section)
                else:
                    estimate = sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in requested) \
                        or int(selected.get("filesize") or selected.get("filesize_approx") or 0)
                self.signals.started.emit(self.task_id, title, estimate)
                if self.section:
                    watcher = threading.Thread(target=self._watch_section, args=(estimate,), daemon=True)
                    watcher.start()
                result = ydl.process_ie_result(selected, download=True)
            self._done.set()
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
            self._done.set()
            if watcher is not None:
                watcher.join(timeout=2)
            self._cleanup()
            self.signals.cancelled.emit(self.task_id)
        except Exception as exc:  # noqa: BLE001 - her hata kullanıcıya anlaşılır biçimde iletilir
            self._done.set()
            if watcher is not None:
                watcher.join(timeout=2)
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
        finally:
            self._done.set()
            with _PROCS_LOCK:
                _PROCS.pop(self._thread_id, None)

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
                audio_bitrate: int | None = None, output_dir: str | None = None,
                section: tuple[float, float] | None = None, media=None) -> DownloadTask:
        """İndirmeyi kuyruğa ekler.

        `section`: (başlangıç, bitiş) saniye; verilirse yalnızca bu aralık indirilir.
        `media`: analiz sonucu (MediaInfo); ham bilgisi varsa indirmede bağlantı yeniden analiz edilmez.
        """
        url = validate_url(url)
        self.check_ready(kind)
        if section is not None:
            validate_section(float(section[0]), float(section[1]), getattr(media, "duration", None))
        settings = self.ctx.settings
        raw = getattr(media, "raw", None) if media is not None and getattr(media, "url", url) == url else None
        task = DownloadTask(
            url=url, kind=kind, title=title,
            quality=quality or settings.video_quality,
            audio_bitrate=int(audio_bitrate or settings.audio_bitrate),
            output_dir=output_dir or str(self.ctx.settings_manager.download_dir()),
            phase="Sırada bekliyor",
            section_start=float(section[0]) if section else None,
            section_end=float(section[1]) if section else None,
            prefetched_info=raw,
            prefetched_at=getattr(media, "analyzed_at", 0.0) if raw is not None else 0.0,
        )
        self.queue.add(task)
        log.info("Kuyruğa eklendi #%s %s (%s)%s", task.id, url, kind.value,
                 f" aralık {section[0]:.1f}-{section[1]:.1f} sn" if section else "")
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
            task = self.queue.get(task_id)
            phase = "Aralık indirme başlıyor" if task.has_section else "İndirme başlıyor"
            self.queue.update(task_id, title=title, total_bytes=total, phase=phase,
                              progress=None if task.has_section else 0.0)

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
            title = task.display_title
            if task.has_section:
                title += (f" [{format_timecode(task.section_start, millis=False)}–"
                          f"{format_timecode(task.section_end, millis=False)}]")
            self.ctx.history.add(kind=task.kind, title=title, status=status, source=task.url,
                                 output_path=task.output_path, size_bytes=size, details=details)
        except Exception as exc:  # noqa: BLE001 - geçmiş yazılamazsa indirme sonucu kaybolmasın
            log.error("Geçmiş kaydı yazılamadı: %s", exc)
        self.ctx.history_changed.emit()


def warm_up() -> None:
    """yt-dlp'yi önceden yükler (uygulama açılışında arka planda çağrılır).

    İlk analizde modül yükleme ve çıkarıcı hazırlığı beklenmez; FFmpeg süreç takibi de kurulur.
    """
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        ydl.get_info_extractor("Generic")
    install_process_tracker()
