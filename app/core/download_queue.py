"""İndirme kuyruğu durum modeli.

İki sinyal vardır:
  * `changed`       – öğe eklendi/kaldırıldı veya bir öğenin durumu değişti (yapısal değişiklik)
  * `task_updated`  – tek bir öğenin ilerlemesi/hızı değişti (sık gelir; satır yerinde güncellenir)
İndirme motoru (app/services/downloader.py) bu modeli günceller; arayüz yalnızca dinler.
"""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QObject, Signal

from app.models.download import DownloadStatus, DownloadTask


class DownloadQueue(QObject):
    changed = Signal()
    task_updated = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tasks: dict[int, DownloadTask] = {}

    def tasks(self) -> list[DownloadTask]:
        return list(self._tasks.values())

    def get(self, task_id: int) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def next_queued(self) -> DownloadTask | None:
        return next((t for t in self._tasks.values() if t.status is DownloadStatus.QUEUED), None)

    def add(self, task: DownloadTask) -> DownloadTask:
        self._tasks[task.id] = task
        self.changed.emit()
        return task

    def update(self, task_id: int, **fields) -> DownloadTask:
        task = self._tasks[task_id]
        for key in fields:
            if not hasattr(task, key):
                raise AttributeError(key)
        status_changed = "status" in fields and fields["status"] != task.status
        for key, value in fields.items():
            setattr(task, key, value)
        self.task_updated.emit(task_id)
        if status_changed:
            self.changed.emit()
        return task

    def remove(self, task_id: int) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status.is_active:
            return False  # aktif indirme önce iptal edilmeli
        del self._tasks[task_id]
        self.changed.emit()
        return True

    def clear_finished(self) -> int:
        finished = [tid for tid, t in self._tasks.items() if t.status.is_finished]
        for tid in finished:
            del self._tasks[tid]
        if finished:
            self.changed.emit()
        return len(finished)

    def counts(self) -> Counter:
        return Counter(t.status for t in self._tasks.values())

    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status.is_active)

    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status is DownloadStatus.QUEUED)
