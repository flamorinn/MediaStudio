"""İndirme kuyruğu: canlı ilerleme, hız, kalan süre; iptal, yeniden dene, klasörde göster, kaldır.

Satırlar yapısal değişiklikte (ekleme/kaldırma) yeniden kurulur; ilerleme güncellemelerinde
yalnızca ilgili satır yerinde güncellenir (sık gelen sinyallerde titreme ve maliyet olmaz).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QStackedWidget, QVBoxLayout, QWidget

from app.models.download import DownloadStatus, DownloadTask
from app.ui.pages.base import BasePage
from app.ui.widgets.cards import Card
from app.ui.widgets.common import (
    ElidedLabel, EmptyState, IconBadge, StatusPill, icon_button, themed_button,
)
from app.ui.widgets.dialogs import ErrorDetailsDialog, confirm
from app.utils.formatting import human_duration, human_size, human_speed
from app.utils.timecode import format_timecode
from app.utils.system import reveal_in_explorer


def describe(task: DownloadTask) -> str:
    """Satırın alt bilgi metni."""
    s = task.status
    if s is DownloadStatus.RUNNING:
        parts = []
        if task.progress is not None:
            parts.append(f"%{task.progress * 100:.0f}")
        if task.downloaded_bytes:
            size = human_size(task.downloaded_bytes)
            parts.append(f"{size} / {human_size(task.total_bytes)}" if task.total_bytes else size)
        if task.speed_bps:
            parts.append(human_speed(task.speed_bps))
        if task.eta_seconds is not None:
            parts.append(f"kalan {human_duration(task.eta_seconds)}")
        return " · ".join([task.phase] + parts if task.phase else parts) or "Başlıyor…"
    if s is DownloadStatus.PROCESSING:
        return task.phase or "İşleniyor…"
    if s is DownloadStatus.QUEUED:
        option = f"MP3 {task.audio_bitrate} kbps" if task.is_audio else \
            ("En yüksek kalite" if task.quality == "best" else f"{task.quality}p")
        span = (f" · aralık {format_timecode(task.section_start, millis=False)}–"
                f"{format_timecode(task.section_end, millis=False)}") if task.has_section else ""
        return f"Sırada bekliyor · {task.kind.label} · {option}{span}"
    if s is DownloadStatus.COMPLETED:
        return f"{human_size(task.total_bytes)} · {task.output_path}" if task.total_bytes else task.output_path
    if s is DownloadStatus.FAILED:
        return task.error or "Bilinmeyen hata"
    return "İndirme iptal edildi; yarım dosyalar temizlendi."


class QueueRow(QFrame):
    def __init__(self, task: DownloadTask, page: "QueuePage", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = task.id
        self.page = page
        self.setObjectName("ListRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(14)
        self.badge = IconBadge(task.kind.icon, "neutral", size=38)
        row.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(6)
        top = QHBoxLayout()
        self.title = ElidedLabel("", "")
        top.addWidget(self.title, 1)
        self.pill = StatusPill("", "neutral")
        top.addWidget(self.pill)
        col.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        col.addWidget(self.progress)
        self.meta = ElidedLabel("", "Faint")
        col.addWidget(self.meta)
        row.addLayout(col, 1)

        self.open_button = icon_button("folder-open", "Klasörde göster")
        self.details_button = icon_button("info", "Hata ayrıntıları")
        self.retry_button = icon_button("refresh", "Yeniden dene")
        self.cancel_button = icon_button("x-circle", "İptal et")
        self.remove_button = icon_button("trash", "Kuyruktan kaldır")
        # Sabit genişlikli düğme alanı: düğme sayısı değişse de ilerleme çubukları hizalı kalır
        actions = QWidget()
        actions.setFixedWidth(3 * 32)
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(4)
        actions_row.addStretch(1)
        for button in (self.open_button, self.details_button, self.retry_button, self.cancel_button,
                       self.remove_button):
            button.setFixedSize(28, 28)
            actions_row.addWidget(button)
        row.addWidget(actions)
        self.open_button.clicked.connect(lambda: page.reveal(self.task_id))
        self.details_button.clicked.connect(lambda: page.show_details(self.task_id))
        self.retry_button.clicked.connect(lambda: page.ctx.downloads.retry(self.task_id))
        self.cancel_button.clicked.connect(lambda: page.ctx.downloads.cancel(self.task_id))
        self.remove_button.clicked.connect(lambda: page.remove_task(self.task_id))
        self.update_from(task)

    def update_from(self, task: DownloadTask) -> None:
        s = task.status
        title = task.display_title
        if task.has_section:
            title += (f"  ·  {format_timecode(task.section_start, millis=False)}–"
                      f"{format_timecode(task.section_end, millis=False)}")
        self.title.set_full_text(title)
        self.pill.set_state(s.tone, s.label)
        self.badge.set_icon(tone="accent" if s.is_active else s.tone if s.is_finished else "neutral")
        if task.progress is None and s.is_active:
            self.progress.setRange(0, 0)  # belirsiz ilerleme (boyut bilinmiyor / işleniyor)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int((task.progress or 0) * 1000))
        self.progress.setVisible(not s.is_finished or s is DownloadStatus.COMPLETED)
        self.meta.set_full_text(describe(task))
        exists = bool(task.output_path) and Path(task.output_path).exists()
        self.open_button.setVisible(s is DownloadStatus.COMPLETED and exists)
        self.details_button.setVisible(s is DownloadStatus.FAILED and bool(task.error_details))
        self.retry_button.setVisible(s in (DownloadStatus.FAILED, DownloadStatus.CANCELLED))
        self.cancel_button.setVisible(not s.is_finished)
        self.cancel_button.setEnabled(task.phase != "İptal ediliyor…")
        self.remove_button.setVisible(s.is_finished)


class QueuePage(BasePage):
    key = "queue"

    def __init__(self, ctx, navigate) -> None:
        super().__init__(ctx, navigate, "İndirme Kuyruğu", "Sıradaki, devam eden ve biten indirmeler.")
        self.cancel_all_button = self.header.add_action(themed_button("Tümünü iptal et", "x-circle", kind="Danger"))
        self.clear_button = self.header.add_action(themed_button("Bitenleri temizle", "trash"))
        self.cancel_all_button.clicked.connect(self.confirm_cancel_all)
        self.clear_button.clicked.connect(self.clear_finished)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_active = StatusPill("", "accent")
        self.chip_pending = StatusPill("", "neutral")
        self.chip_done = StatusPill("", "success")
        self.chip_failed = StatusPill("", "danger")
        for chip in (self.chip_active, self.chip_pending, self.chip_done, self.chip_failed):
            chips.addWidget(chip)
        chips.addStretch(1)
        self.body.addLayout(chips)

        self.card = Card()
        self.stack = QStackedWidget()
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.empty = EmptyState("layers", "Kuyruk boş",
                                "Video veya ses indirmeye başladığınızda indirmeler burada sıralanır; "
                                "hızlarını ve ilerlemelerini buradan izleyebilirsiniz.")
        self.go_video = self.empty.add_button(themed_button("Video İndir", "video", kind="Primary"))
        self.go_audio = self.empty.add_button(themed_button("Ses İndir", "music"))
        self.go_video.clicked.connect(lambda: navigate("video_download"))
        self.go_audio.clicked.connect(lambda: navigate("audio_download"))
        self.stack.addWidget(self.list_widget)
        self.stack.addWidget(self.empty)
        self.card.body.addWidget(self.stack)
        self.body.addWidget(self.card)
        self.body.addStretch(1)

        self.rows: dict[int, QueueRow] = {}
        ctx.queue.changed.connect(self.refresh)
        ctx.queue.task_updated.connect(self.update_row)
        ctx.settings_changed.connect(lambda _k: self._update_summary())
        self.refresh()

    # ------------------------------------------------------------------ görünüm
    def refresh(self) -> None:
        tasks = self.ctx.queue.tasks()
        ids = [t.id for t in tasks]
        if ids != list(self.rows):
            while self.list_layout.count():
                item = self.list_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.rows = {t.id: QueueRow(t, self) for t in tasks}
            for row in self.rows.values():
                self.list_layout.addWidget(row)
            self.list_layout.addStretch(1)
        else:
            for task in tasks:
                self.rows[task.id].update_from(task)
        self.stack.setCurrentWidget(self.list_widget if tasks else self.empty)
        self._update_summary()

    def update_row(self, task_id: int) -> None:
        row, task = self.rows.get(task_id), self.ctx.queue.get(task_id)
        if row is not None and task is not None:
            row.update_from(task)

    def _update_summary(self) -> None:
        counts = self.ctx.queue.counts()
        active = sum(n for s, n in counts.items() if s.is_active)
        pending = counts.get(DownloadStatus.QUEUED, 0)
        self.chip_active.setText(f"Aktif: {active}")
        self.chip_pending.setText(f"Sırada: {pending}")
        self.chip_done.setText(f"Tamamlanan: {counts.get(DownloadStatus.COMPLETED, 0)}")
        self.chip_failed.setText(f"Başarısız: {counts.get(DownloadStatus.FAILED, 0)}")
        self.header.subtitle.setText("Sıradaki, devam eden ve biten indirmeler · aynı anda en fazla "
                                     f"{self.ctx.settings.max_concurrent_downloads} indirme")
        self.clear_button.setEnabled(any(t.status.is_finished for t in self.ctx.queue.tasks()))
        self.cancel_all_button.setVisible(active + pending > 0)

    # ------------------------------------------------------------------ eylemler
    def reveal(self, task_id: int) -> None:
        task = self.ctx.queue.get(task_id)
        if task and not reveal_in_explorer(task.output_path):
            self.ctx.notifications.warning("Dosya bulunamadı", "Çıktı dosyası taşınmış veya silinmiş olabilir.")

    def show_details(self, task_id: int) -> None:
        task = self.ctx.queue.get(task_id)
        if task:
            ErrorDetailsDialog("İndirme başarısız", f"{task.display_title}: {task.error}", task.error_details,
                               self).exec()

    def remove_task(self, task_id: int) -> None:
        if not self.ctx.queue.remove(task_id):
            self.ctx.notifications.warning("Kaldırılamadı", "Devam eden bir indirme önce iptal edilmelidir.")

    def clear_finished(self) -> None:
        removed = self.ctx.queue.clear_finished()
        if removed:
            self.ctx.notifications.info("Kuyruk temizlendi", f"{removed} biten indirme kaldırıldı.")

    def confirm_cancel_all(self) -> None:
        if confirm(self, "Tümünü iptal et", "Devam eden ve sıradaki tüm indirmeler iptal edilecek.\n"
                   "Yarım kalan dosyalar silinir.", "Tümünü iptal et", danger=True):
            count = self.ctx.downloads.cancel_all()
            self.ctx.notifications.info("İndirmeler iptal edildi", f"{count} indirme iptal edildi.")
