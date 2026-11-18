"""FFmpeg süreçlerini arka planda çalıştıran ortak iş sınıfı (dönüştürücü ve kesici kullanır).

Sağladıkları:
  * `-progress pipe:1` çıktısından canlı ilerleme, hız ve kalan süre
  * iptal (süreç sonlandırılır)
  * güvenli yazma: çıktı önce `Ad.converting.ext` geçici dosyasına yazılır, yalnızca başarıyla
    biterse asıl ada taşınır; iptal/hata durumunda geçici dosya silinir
Alt sınıflar `build_command(temp_path)` ve gerekirse `friendly_error(detail)` uygular.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from app.utils.files import temp_path_for

log = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROGRESS_INTERVAL = 0.2


class FFmpegJobSignals(QObject):
    progress = Signal(int, float, str, object)   # id, oran (0-1), hız ("38x"), kalan saniye
    succeeded = Signal(int, str, int)            # id, çıktı yolu, boyut
    failed = Signal(int, str, str)               # id, kullanıcı mesajı, ayrıntı
    cancelled = Signal(int)


def parse_progress_seconds(value: str) -> float | None:
    """`out_time_us` / `out_time_ms` (ikisi de mikrosaniye) değerini saniyeye çevirir."""
    try:
        return int(value) / 1_000_000
    except (TypeError, ValueError):
        return None


class FFmpegJob(QRunnable):
    """Tek bir FFmpeg sürecini çalıştırır. `duration`: çıktının beklenen süresi (ilerleme oranı için)."""

    extra_input_args: tuple[str, ...] = ()  # testler için (ör. "-re" ile gerçek zamanlı okuma)
    failure_label = "İşlem"

    def __init__(self, item_id: int, ffmpeg: str, output: Path, duration: float | None) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.item_id = item_id
        self.ffmpeg = ffmpeg
        self.output = Path(output)
        self.temp = temp_path_for(self.output)
        self.duration = duration
        self.signals = FFmpegJobSignals()
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._seconds = 0.0
        self._speed = ""

    # ------------------------------------------------------------------ alt sınıflar için
    def build_command(self, temp: str) -> list[str]:  # pragma: no cover - soyut
        raise NotImplementedError

    def friendly_error(self, detail: str) -> str:
        return f"{self.failure_label} başarısız oldu."

    # ------------------------------------------------------------------ API
    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def _remove_temp(self) -> None:
        try:
            self.temp.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Geçici dosya silinemedi %s: %s", self.temp, exc)

    def run(self) -> None:  # iş parçacığında çalışır
        errors: deque[str] = deque(maxlen=40)
        try:
            if self._cancel.is_set():
                self.signals.cancelled.emit(self.item_id)
                return
            self.output.parent.mkdir(parents=True, exist_ok=True)
            cmd = self.build_command(str(self.temp))
            log.debug("FFmpeg: %s", cmd)
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            reader = threading.Thread(target=lambda: errors.extend(self._proc.stderr), daemon=True)
            reader.start()
            last = 0.0
            for line in self._proc.stdout:
                if self._cancel.is_set():
                    self.cancel()
                    break
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms"):
                    seconds = parse_progress_seconds(value)
                    if seconds is not None:
                        self._seconds = seconds
                elif key == "speed":
                    self._speed = value.strip()
                elif key == "progress":
                    now = time.monotonic()
                    if value == "end" or now - last >= PROGRESS_INTERVAL:
                        last = now
                        self._emit_progress(value == "end")
            self._proc.wait()
            reader.join(timeout=5)
            if self._cancel.is_set():
                self._remove_temp()
                self.signals.cancelled.emit(self.item_id)
                return
            if self._proc.returncode != 0:
                detail = "".join(errors).strip() or f"FFmpeg çıkış kodu {self._proc.returncode}"
                self._remove_temp()
                self.signals.failed.emit(self.item_id, self.friendly_error(detail), detail)
                return
            os.replace(self.temp, self.output)
            self.signals.succeeded.emit(self.item_id, str(self.output), self.output.stat().st_size)
        except Exception as exc:  # noqa: BLE001 - kullanıcıya anlaşılır biçimde iletilir
            self._remove_temp()
            if self._cancel.is_set():
                self.signals.cancelled.emit(self.item_id)
                return
            detail = f"{exc.__class__.__name__}: {exc}"
            log.warning("%s başarısız (%s): %s", self.failure_label, self.output.name, detail)
            self.signals.failed.emit(self.item_id, self.friendly_error(str(exc)), detail)

    def _emit_progress(self, done: bool) -> None:
        seconds = self._seconds or 0.0
        fraction = 1.0 if done else (min(seconds / self.duration, 0.999) if self.duration else 0.0)
        speed = self._speed if self._speed != "N/A" else ""
        eta = None
        try:
            factor = float(speed.rstrip("x")) if speed else 0.0
            if factor > 0 and self.duration:
                eta = int(max(self.duration - seconds, 0) / factor)
        except ValueError:
            pass
        self.signals.progress.emit(self.item_id, fraction, speed, eta)
