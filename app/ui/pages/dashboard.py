"""Dashboard: özet istatistikler, hızlı işlemler, son işlemler ve sistem durumu."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from app.core.dependencies import SOURCE_LABELS, DependencyReport
from app.models.history import HistoryEntry
from app.ui.pages.base import BasePage
from app.ui.widgets.cards import ActionCard, Card, StatCard
from app.ui.widgets.common import EmptyState, IconBadge, StatusPill, label, themed_button
from app.ui.widgets.responsive import AdaptiveRow, ResponsiveGrid
from app.utils.formatting import greeting, human_size, long_date, relative_time

RECENT_LIMIT = 6

QUICK_ACTIONS = (
    ("video_download", "video", "Video indir", "MP4 olarak en yüksek kalitede indir.", "accent"),
    ("audio_download", "music", "Ses indir", "Yalnızca sesi MP3 olarak kaydet.", "secondary"),
    ("converter", "repeat", "MP3'e dönüştür", "Yerel videoları toplu olarak MP3'e çevir.", "accent"),
    ("cutter", "scissors", "Video kes", "Seçilen aralığı kalite kaybı olmadan dışa aktar.", "secondary"),
)


class RecentRow(QFrame):
    def __init__(self, entry: HistoryEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ListRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(12)
        row.addWidget(IconBadge(entry.kind.icon, "accent" if entry.kind.is_download else "secondary", size=36))
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = label(entry.title)
        title.setToolTip(entry.source or entry.title)
        texts.addWidget(title)
        meta = f"{entry.kind.label} · {relative_time(entry.created_at)}"
        if entry.size_bytes:
            meta += f" · {human_size(entry.size_bytes)}"
        texts.addWidget(label(meta, "Faint"))
        row.addLayout(texts, 1)
        row.addWidget(StatusPill(entry.status.label, entry.status.tone))


class DashboardPage(BasePage):
    key = "dashboard"

    def __init__(self, ctx, navigate) -> None:
        super().__init__(ctx, navigate, greeting(), long_date(),
                         eyebrow="Media Studio")
        self.header.title.setText(greeting())
        self.new_download = self.header.add_action(themed_button("Yeni indirme", "download", kind="Primary"))
        self.new_download.clicked.connect(lambda: navigate("video_download"))

        # --- İstatistikler --------------------------------------------------
        self.stats_grid = ResponsiveGrid(min_item_width=220, max_columns=4)
        self.stat_downloads = self.stats_grid.add(StatCard("download", "Toplam indirilen dosya", "accent"))
        self.stat_completed = self.stats_grid.add(StatCard("check-circle", "Tamamlanan işlemler", "success"))
        self.stat_active = self.stats_grid.add(StatCard("activity", "Aktif indirmeler", "secondary"))
        self.stat_failed = self.stats_grid.add(StatCard("alert-triangle", "Başarısız işlemler", "danger"))
        self.body.addWidget(self.stats_grid)

        # --- Hızlı işlemler -------------------------------------------------
        self.body.addWidget(label("Hızlı işlemler", "CardTitle"))
        self.actions_grid = ResponsiveGrid(min_item_width=220, max_columns=4)
        self.action_cards: dict[str, ActionCard] = {}
        for key, icon, title, desc, tone in QUICK_ACTIONS:
            card = ActionCard(icon, title, desc, tone)
            card.clicked.connect(lambda k=key: navigate(k))
            self.action_cards[key] = self.actions_grid.add(card)
        self.body.addWidget(self.actions_grid)

        # --- Son işlemler + sistem durumu -----------------------------------
        self.bottom = AdaptiveRow(breakpoint=900)
        self.recent_card = Card("Son işlemler", "En son tamamlanan veya başarısız olan işlemler")
        self.view_all = self.recent_card.add_header_widget(themed_button("Tümünü gör", "arrow-right", kind="Link"))
        self.view_all.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.view_all.clicked.connect(lambda: navigate("history"))
        self.recent_stack = QStackedWidget()
        self.recent_list = QWidget()
        self.recent_layout = QVBoxLayout(self.recent_list)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_layout.setSpacing(2)
        self.recent_empty = EmptyState("inbox", "Henüz işlem yok",
                                       "İndirdiğiniz, dönüştürdüğünüz veya kestiğiniz dosyalar burada görünecek.")
        self.recent_stack.addWidget(self.recent_list)
        self.recent_stack.addWidget(self.recent_empty)
        self.recent_card.body.addWidget(self.recent_stack)
        self.bottom.add(self.recent_card, 2)

        self.system_card = Card("Sistem durumu", "Medya işlemleri için gerekli bileşenler")
        self.system_rows: dict[str, tuple[QLabel, StatusPill]] = {}
        for key, name, icon in (("ffmpeg", "FFmpeg", "terminal"), ("ffprobe", "FFprobe", "search"),
                                ("ytdlp", "yt-dlp", "package")):
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(IconBadge(icon, "neutral", size=34))
            texts = QVBoxLayout()
            texts.setSpacing(1)
            texts.addWidget(label(name))
            detail = label("Kontrol ediliyor…", "Faint")
            texts.addWidget(detail)
            row.addLayout(texts, 1)
            pill = StatusPill("…", "neutral")
            row.addWidget(pill)
            self.system_card.body.addLayout(row)
            self.system_rows[key] = (detail, pill)
        self.system_card.body.addStretch(1)
        self.open_settings = themed_button("Sistem ayarları", "settings")
        self.open_settings.clicked.connect(lambda: navigate("settings"))
        self.system_card.body.addWidget(self.open_settings)
        self.bottom.add(self.system_card, 1)
        self.body.addWidget(self.bottom)
        self.body.addStretch(1)

        ctx.history_changed.connect(self.refresh)
        ctx.queue.changed.connect(self.refresh)
        ctx.dependencies_checking.connect(self._deps_checking)
        ctx.dependencies_changed.connect(self.show_dependencies)
        self.refresh()
        if ctx.dependencies is not None:
            self.show_dependencies(ctx.dependencies)

    def on_shown(self) -> None:
        self.header.title.setText(greeting())
        self.header.subtitle.setText(long_date())
        self.refresh()

    def refresh(self) -> None:
        stats = self.ctx.history.stats()
        active = self.ctx.queue.active_count()
        pending = self.ctx.queue.pending_count()
        self.stat_downloads.set_value(stats.downloaded_files,
                                      human_size(stats.downloaded_bytes) if stats.downloaded_bytes else "Henüz indirme yok")
        self.stat_completed.set_value(stats.completed, f"Son 7 günde {stats.last_7_days} işlem")
        self.stat_active.set_value(active, f"{pending} sırada bekliyor" if pending else "Kuyruk boş")
        self.stat_failed.set_value(stats.failed, "Geçmiş sayfasında ayrıntılar" if stats.failed else "Sorun yok")

        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = self.ctx.history.list(limit=RECENT_LIMIT)
        for entry in entries:
            self.recent_layout.addWidget(RecentRow(entry))
        self.recent_layout.addStretch(1)
        self.recent_stack.setCurrentWidget(self.recent_list if entries else self.recent_empty)
        self.view_all.setVisible(bool(entries))

    def _deps_checking(self) -> None:
        for detail, pill in self.system_rows.values():
            detail.setText("Kontrol ediliyor…")
            pill.set_state("neutral", "…")

    def show_dependencies(self, report: DependencyReport) -> None:
        for key in ("ffmpeg", "ffprobe", "ytdlp"):
            tool = getattr(report, key)
            detail, pill = self.system_rows[key]
            if tool:
                detail.setText(f"{tool.short_version} · {SOURCE_LABELS.get(tool.source, tool.source)}")
                pill.set_state("success", "Hazır")
            else:
                detail.setText("Bulunamadı")
                pill.set_state("danger", "Eksik")
