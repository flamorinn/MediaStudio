"""Arayüzü dondurmadan arka planda iş çalıştırmak için QRunnable sarmalayıcısı."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object, str)  # istisna, traceback metni
    finished = Signal()


class Worker(QRunnable):
    """`fn(*args, **kwargs)` çağrısını QThreadPool üzerinde çalıştırır.

    Sinyaller ana iş parçacığındaki bir QObject'in slotlarına bağlanmalıdır; Qt bu
    bağlantıları otomatik olarak kuyruklu (thread-safe) yapar.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            value = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - hata arayüze iletilir
            tb = traceback.format_exc()
            log.error("Arka plan işi başarısız:\n%s", tb)
            self.signals.error.emit(exc, tb)
        else:
            self.signals.result.emit(value)
        finally:
            self.signals.finished.emit()
