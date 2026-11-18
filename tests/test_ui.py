"""Arayüz testleri: pencere, gezinme, bütün düğmeler, sayfalar, tema, bildirimler, responsive yerleşim."""

import sys

import pytest
from PySide6.QtCore import SIGNAL, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from app.application import create_app, install_excepthook
from app.core.context import AppContext
from app.models import DownloadStatus, DownloadTask, OperationKind, OperationStatus
from app.ui.main_window import NAV_ITEMS
from app.ui.theme import theme
from app.ui.theme.tokens import ACCENT_PRESETS
from app.ui.widgets.cards import ActionCard

K, S = OperationKind, OperationStatus


@pytest.fixture
def ui(qtbot, qapp):
    ctx = AppContext()
    _app, ctx, window = create_app([], ctx)
    qtbot.addWidget(window)
    window.resize(1366, 768)
    window.show()
    qtbot.waitExposed(window)
    with qtbot.waitSignal(ctx.dependencies_changed, timeout=30000, raising=False):
        pass
    qtbot.waitUntil(lambda: ctx.dependencies is not None, timeout=30000)
    yield window
    ctx.shutdown()


def wait_anim(qtbot, window):
    qtbot.waitUntil(lambda: not window.stack.animating, timeout=2000)


# ============================================================ pencere ve gezinme
def test_window_starts_on_dashboard(ui):
    assert ui.current_page_key() == "dashboard"
    assert ui.sidebar.buttons["dashboard"].isChecked()
    assert ui.windowTitle() == "Dashboard — Media Studio"
    assert ui.minimumWidth() <= 1366 and ui.minimumHeight() <= 700


def test_sidebar_has_all_pages_in_order(ui):
    assert [b.text() for b in ui.sidebar.buttons.values()] == [
        "Dashboard", "Video İndir", "Ses İndir", "Dönüştürücü", "Video Kes", "İndirme Kuyruğu", "Geçmiş", "Ayarlar"]


def test_every_sidebar_button_navigates(ui, qtbot):
    for item in NAV_ITEMS:
        qtbot.mouseClick(ui.sidebar.buttons[item.key], Qt.MouseButton.LeftButton)
        wait_anim(qtbot, ui)
        assert ui.current_page_key() == item.key
        assert ui.sidebar.buttons[item.key].isChecked()
        assert sum(b.isChecked() for b in ui.sidebar.buttons.values()) == 1
        assert ui.stack.currentWidget().pos().x() == 0  # kaydırma animasyonu yerine oturdu


def test_keyboard_shortcuts_navigate(ui, qtbot):
    for index, item in enumerate(NAV_ITEMS, start=1):
        QTest.keyClick(ui, Qt.Key(Qt.Key.Key_0 + index), Qt.KeyboardModifier.ControlModifier)
        wait_anim(qtbot, ui)
        assert ui.current_page_key() == item.key


def test_page_transition_animates_and_cleans_up(ui, qtbot):
    theme.animations = True
    ui.show_page("history")
    assert ui.stack.animating
    wait_anim(qtbot, ui)
    assert ui.stack.currentWidget().pos().x() == 0
    assert ui.stack.findChildren(type(ui.stack._overlay)) == [] or ui.stack._overlay is None


def test_every_push_button_is_wired(ui):
    """Sahte düğme yok: her düğmenin tıklamasına bağlı en az bir işlem olmalı."""
    unwired = []
    for page_key, page in ui.pages.items():
        for button in page.findChildren(QPushButton):
            if button.receivers(SIGNAL("clicked()")) + button.receivers(SIGNAL("clicked(bool)")) == 0:
                unwired.append(f"{page_key}: {button.text() or button.toolTip()}")
    for button in ui.sidebar.findChildren(QPushButton):
        if button.receivers(SIGNAL("clicked(bool)")) + button.receivers(SIGNAL("clicked()")) == 0:
            unwired.append(f"sidebar: {button.text()}")
    assert unwired == []


def test_all_modules_are_real_pages(ui):
    """Aşama 4 ile birlikte hiçbir sayfa 'geliştiriliyor' önizlemesi değildir."""
    from app.ui.pages.converter_page import ConverterPage
    from app.ui.pages.cutter_page import CutterPage
    from app.ui.pages.download_page import DownloadPage

    assert isinstance(ui.pages["video_download"], DownloadPage)
    assert isinstance(ui.pages["audio_download"], DownloadPage)
    assert isinstance(ui.pages["converter"], ConverterPage)
    assert isinstance(ui.pages["cutter"], CutterPage)


# ============================================================ dashboard
def test_dashboard_stats_and_recent(ui):
    page = ui.pages["dashboard"]
    assert page.stat_downloads.value.text() == "0"
    assert page.recent_stack.currentWidget() is page.recent_empty
    ui.ctx.history.add(kind=K.VIDEO_DOWNLOAD, title="Kendi videom", status=S.SUCCESS, size_bytes=2048)
    ui.ctx.history.add(kind=K.CONVERT, title="Ses", status=S.FAILED)
    ui.ctx.history_changed.emit()
    assert page.stat_downloads.value.text() == "1"
    assert page.stat_completed.value.text() == "1"
    assert page.stat_failed.value.text() == "1"
    assert page.recent_stack.currentWidget() is page.recent_list
    assert sum(1 for i in range(page.recent_layout.count()) if page.recent_layout.itemAt(i).widget()) == 2


def test_dashboard_active_downloads_follow_queue(ui):
    page = ui.pages["dashboard"]
    task = ui.ctx.queue.add(DownloadTask("https://ornek.test/v", K.VIDEO_DOWNLOAD))
    assert page.stat_active.value.text() == "0" and "1 sırada" in page.stat_active.caption.text()
    ui.ctx.queue.update(task.id, status=DownloadStatus.RUNNING)
    assert page.stat_active.value.text() == "1"
    assert ui.sidebar.buttons["queue"].badge.text() == "1"


def test_dashboard_quick_actions_and_links(ui, qtbot):
    page = ui.pages["dashboard"]
    cards = page.findChildren(ActionCard)
    assert len(cards) == 4
    for key, card in page.action_cards.items():
        ui.show_page("dashboard")
        wait_anim(qtbot, ui)
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton)
        wait_anim(qtbot, ui)
        assert ui.current_page_key() == key
    ui.show_page("dashboard")
    page.action_cards["cutter"].setFocus()
    QTest.keyClick(page.action_cards["cutter"], Qt.Key.Key_Return)
    assert ui.current_page_key() == "cutter"
    for button, target in ((page.new_download, "video_download"), (page.open_settings, "settings")):
        ui.show_page("dashboard")
        button.click()
        assert ui.current_page_key() == target


def test_dashboard_system_status(ui):
    page = ui.pages["dashboard"]
    for _detail, pill in page.system_rows.values():
        assert pill.text() == "Hazır"
    dot, value = ui.sidebar.system_card.rows["ffmpeg"]
    assert value.text() != "…"
    ui.sidebar.system_card.clicked.emit()
    assert ui.current_page_key() == "settings"


# ============================================================ kuyruk
def test_queue_page(ui, qtbot, tmp_path, monkeypatch):
    page = ui.pages["queue"]
    assert page.stack.currentWidget() is page.empty and not page.clear_button.isEnabled()
    assert not page.cancel_all_button.isVisibleTo(page)
    page.go_audio.click()
    assert ui.current_page_key() == "audio_download"
    page.go_video.click()
    assert ui.current_page_key() == "video_download"

    q = ui.ctx.queue
    running = q.add(DownloadTask("https://ornek.test/1", K.VIDEO_DOWNLOAD, "Video"))
    q.update(running.id, status=DownloadStatus.RUNNING, progress=0.5, speed_bps=2 * 1024 * 1024, eta_seconds=30,
             downloaded_bytes=5_000_000, total_bytes=10_000_000, phase="İndiriliyor")
    out = tmp_path / "ses.mp3"
    out.write_bytes(b"0")
    done = q.add(DownloadTask("https://ornek.test/2", K.AUDIO_DOWNLOAD, "Ses", status=DownloadStatus.COMPLETED,
                              output_path=str(out), total_bytes=1))
    failed = q.add(DownloadTask("https://ornek.test/3", K.VIDEO_DOWNLOAD, "Bozuk", status=DownloadStatus.FAILED,
                                error="İçerik bulunamadı (404).", error_details="HTTP Error 404"))
    assert len(page.rows) == 3 and page.stack.currentWidget() is page.list_widget
    assert page.chip_active.text() == "Aktif: 1" and page.clear_button.isEnabled()
    assert page.cancel_all_button.isVisibleTo(page)

    r_run, r_done, r_fail = (page.rows[t.id] for t in (running, done, failed))
    assert r_run.progress.value() == 500 and "2,0 MB/sn" in r_run.meta.full_text() and "kalan 00:30" in r_run.meta.full_text()
    vis = lambda row: {n for n in ("open", "details", "retry", "cancel", "remove")
                       if getattr(row, f"{n}_button").isVisibleTo(row)}
    assert vis(r_run) == {"cancel"}
    assert vis(r_done) == {"open", "remove"}
    assert vis(r_fail) == {"details", "retry", "remove"}

    q.update(running.id, progress=0.75)                     # ilerleme: satır yerinde güncellenir
    assert page.rows[running.id] is r_run and r_run.progress.value() == 750

    revealed = []
    monkeypatch.setattr("app.ui.pages.queue_page.reveal_in_explorer", lambda p: revealed.append(p) or True)
    r_done.open_button.click()
    assert revealed == [str(out)]
    shown = []
    monkeypatch.setattr("app.ui.pages.queue_page.ErrorDetailsDialog.exec", lambda self: shown.append(self) or 0)
    r_fail.details_button.click()
    assert "404" in shown[0].details.toPlainText()

    cancelled = []
    monkeypatch.setattr(ui.ctx.downloads, "cancel", lambda tid: cancelled.append(tid) or True)
    monkeypatch.setattr(ui.ctx.downloads, "retry", lambda tid: cancelled.append(-tid) or True)
    r_run.cancel_button.click()
    r_fail.retry_button.click()
    assert cancelled == [running.id, -failed.id]

    page.clear_button.click()
    assert q.get(done.id) is None and q.get(failed.id) is None and q.get(running.id) is not None
    q.update(running.id, status=DownloadStatus.CANCELLED)
    page.rows[running.id].remove_button.click()
    assert q.tasks() == [] and page.stack.currentWidget() is page.empty


# ============================================================ geçmiş
def test_history_page(ui, tmp_path, monkeypatch):
    page = ui.pages["history"]
    assert page.stack.currentIndex() == 1 and not page.clear_button.isEnabled()
    out = tmp_path / "video.mp4"
    out.write_bytes(b"0")
    ui.ctx.history.add(kind=K.VIDEO_DOWNLOAD, title="Video bir", status=S.SUCCESS, output_path=str(out))
    ui.ctx.history.add(kind=K.CUT, title="Klip", status=S.FAILED, output_path=str(tmp_path / "yok.mp4"))
    ui.ctx.history_changed.emit()
    assert page.table.rowCount() == 2 and page.summary.text() == "2 kayıt"

    page.search.setText("klip")
    assert page.table.rowCount() == 1 and page.summary.text() == "1 / 2 kayıt"
    page.search.setText("bulunmayan")
    assert page.stack.currentIndex() == 1 and page.empty.title.text() == "Eşleşen kayıt yok"
    page.search.clear()
    page.kind_filter.setCurrentIndex(page.kind_filter.findData(K.VIDEO_DOWNLOAD))
    assert page.table.rowCount() == 1
    page.kind_filter.setCurrentIndex(0)

    page.table.selectRow(1)  # mevcut dosya
    assert page.reveal_button.isEnabled() and page.delete_button.isEnabled()
    page.table.selectRow(0)  # silinmiş dosya
    assert not page.reveal_button.isEnabled()
    page.delete_button.click()
    assert ui.ctx.history.count() == 1

    monkeypatch.setattr("app.ui.pages.history_page.confirm", lambda *a, **k: False)
    page.clear_button.click()
    assert ui.ctx.history.count() == 1
    monkeypatch.setattr("app.ui.pages.history_page.confirm", lambda *a, **k: True)
    page.clear_button.click()
    assert ui.ctx.history.count() == 0 and page.stack.currentIndex() == 1


# ============================================================ ayarlar
def test_settings_download_dir(ui, tmp_path, monkeypatch):
    page = ui.pages["settings"]
    target = tmp_path / "yeni klasör"
    monkeypatch.setattr("app.ui.pages.settings_page.QFileDialog.getExistingDirectory", lambda *a, **k: str(target))
    page.browse_button.click()
    assert target.is_dir() and page.download_dir_edit.text() == str(target)
    assert page.default_dir_button.isVisibleTo(page) and page.saved_pill.isVisibleTo(page)
    page.default_dir_button.click()
    assert ui.ctx.settings.download_dir == "" and page.download_dir_edit.text() == str(tmp_path / "downloads")


def test_settings_unwritable_dir_rejected(ui, tmp_path):
    blocker = tmp_path / "dosya"
    blocker.write_text("x")
    notes = []
    ui.ctx.notifications.posted.connect(notes.append)
    assert not ui.pages["settings"].set_download_dir(str(blocker / "alt"))
    assert notes[-1].title == "Klasör kullanılamıyor" and ui.ctx.settings.download_dir == ""


def test_settings_defaults_persist(ui):
    page = ui.pages["settings"]
    page.quality_combo.setCurrentIndex(page.quality_combo.findData("1080"))
    page.bitrate_combo.setCurrentIndex(page.bitrate_combo.findData(320))
    page.concurrent_spin.setValue(4)
    s = type(ui.ctx.settings_manager)().settings
    assert (s.video_quality, s.audio_bitrate, s.max_concurrent_downloads) == ("1080", 320, 4)


def test_settings_accent_changes_theme_live(ui, qtbot):
    page = ui.pages["settings"]
    qtbot.mouseClick(page.swatches["electric"], Qt.MouseButton.LeftButton)
    assert theme.color("primary") == ACCENT_PRESETS["electric"]["primary"]
    assert ui.ctx.settings.accent == "electric"
    assert page.swatches["electric"].property("selected") is True
    assert page.swatches["aurora"].property("selected") is False
    QTest.keyClick(page.swatches["aurora"], Qt.Key.Key_Space)
    assert theme.color("primary") == ACCENT_PRESETS["aurora"]["primary"]


def test_settings_animations_toggle(ui, qtbot):
    page = ui.pages["settings"]
    qtbot.mouseClick(page.animations_toggle, Qt.MouseButton.LeftButton)
    assert ui.ctx.settings.animations is False and theme.animations is False
    ui.show_page("dashboard")
    assert not ui.stack.animating  # animasyon kapalıyken anında geçiş
    page.animations_toggle.click()
    assert theme.animations is True


def test_settings_ffmpeg_controls(ui, qtbot, tmp_path, monkeypatch):
    page = ui.pages["settings"]
    for row in page.dep_rows.values():
        assert row.pill.text() == "Hazır"
    fake = tmp_path / "ffmpeg.exe"
    fake.write_text("x")
    monkeypatch.setattr("app.ui.pages.settings_page.QFileDialog.getOpenFileName", lambda *a, **k: (str(fake), ""))
    notes = []
    ui.ctx.notifications.posted.connect(notes.append)
    page.pick_ffmpeg_button.click()
    assert notes[-1].title == "Geçersiz FFmpeg" and ui.ctx.settings.ffmpeg_path is None

    real = ui.ctx.dependencies.ffmpeg.path
    monkeypatch.setattr("app.ui.pages.settings_page.QFileDialog.getOpenFileName", lambda *a, **k: (str(real), ""))
    with qtbot.waitSignal(ui.ctx.dependencies_changed, timeout=30000):
        page.pick_ffmpeg_button.click()
    assert ui.ctx.dependencies.ffmpeg.source == "custom" and page.auto_ffmpeg_button.isVisibleTo(page)
    with qtbot.waitSignal(ui.ctx.dependencies_changed, timeout=30000):
        page.auto_ffmpeg_button.click()
    assert ui.ctx.settings.ffmpeg_path is None and ui.ctx.dependencies.ffmpeg.source != "custom"
    with qtbot.waitSignal(ui.ctx.dependencies_changed, timeout=30000):
        page.rescan_button.click()
        assert not page.rescan_button.isEnabled()
    assert page.rescan_button.isEnabled()


def test_settings_reset(ui, monkeypatch):
    page = ui.pages["settings"]
    ui.ctx.update_settings(audio_bitrate=128, accent="violet")
    monkeypatch.setattr("app.ui.pages.settings_page.confirm", lambda *a, **k: False)
    page.reset_button.click()
    assert ui.ctx.settings.audio_bitrate == 128
    monkeypatch.setattr("app.ui.pages.settings_page.confirm", lambda *a, **k: True)
    page.reset_button.click()
    assert ui.ctx.settings.audio_bitrate == 192 and theme.color("primary") == ACCENT_PRESETS["aurora"]["primary"]
    assert page.bitrate_combo.currentData() == 192


def test_folder_buttons_open_folders(ui, monkeypatch):
    opened = []
    monkeypatch.setattr("app.ui.pages.settings_page.open_folder", lambda p: opened.append(str(p)) or True)
    page = ui.pages["settings"]
    page.open_dir_button.click()
    page.open_data_button.click()
    page.open_logs_button.click()
    assert len(opened) == 3


# ============================================================ bildirimler ve hatalar
def test_notifications_show_toasts(ui, qtbot, monkeypatch):
    ui.ctx.notifications.success("Bitti", "İşlem tamamlandı")
    ui.ctx.notifications.error("Hata", "Bir şey oldu", "Traceback ...")
    toasts = ui.toasts.toasts
    assert len(toasts) == 2 and toasts[0].note.title == "Hata"
    target_x = ui.content.width() - toasts[0].width() - 20
    qtbot.waitUntil(lambda: toasts[0].x() == target_x, timeout=2000)  # kayarak yerine oturur
    assert toasts[0].geometry().right() < ui.content.width()
    shown = []
    monkeypatch.setattr("app.ui.main_window.ErrorDetailsDialog.exec", lambda self: shown.append(self) or 0)
    toasts[0].details_button.click()
    assert shown and "Traceback" in shown[0].details.toPlainText()
    toasts[0].close_button.click()
    qtbot.waitUntil(lambda: len(ui.toasts.toasts) == 1, timeout=2000)
    for _ in range(6):
        ui.ctx.notifications.info("Bilgi")
    qtbot.waitUntil(lambda: len(ui.toasts.toasts) <= 4, timeout=2000)


def test_uncaught_exception_becomes_notification(ui, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    install_excepthook(ui.ctx)
    try:
        raise RuntimeError("deneme hatası")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    last = ui.toasts.toasts[0].note
    assert last.title == "Beklenmeyen bir hata oluştu" and "deneme hatası" in last.message
    assert "RuntimeError" in last.details


def test_missing_ffmpeg_reported(ui, monkeypatch, qtbot):
    monkeypatch.setattr(ui.ctx.checker, "_automatic_candidates", lambda: [])
    with qtbot.waitSignal(ui.ctx.dependencies_changed, timeout=30000):
        ui.ctx.refresh_dependencies()
    assert ui.pages["settings"].dep_rows["ffmpeg"].pill.text() == "Bulunamadı"
    assert ui.pages["settings"].dep_help.isVisibleTo(ui.pages["settings"])
    assert ui.toasts.toasts[0].note.title == "FFmpeg bulunamadı"
    assert "FFmpeg ve FFprobe gerekli" in ui.pages["cutter"].blocker.text()
    assert not ui.pages["cutter"].choose_button.isEnabled()


# ============================================================ responsive ve kalıcılık
@pytest.mark.parametrize("size, stat_cols, horizontal", [((1366, 768), 4, True), ((1024, 640), 2, False),
                                                         ((1920, 1080), 4, True)])
def test_responsive_layout(ui, qtbot, size, stat_cols, horizontal):
    ui.resize(*size)
    ui.show_page("dashboard")
    qtbot.waitUntil(lambda: ui.pages["dashboard"].stats_grid.columns == stat_cols, timeout=2000)
    page = ui.pages["dashboard"]
    assert page.bottom.horizontal is horizontal
    assert page.scroll.horizontalScrollBar().maximum() == 0  # yatay taşma yok
    for key, page in ui.pages.items():
        ui.show_page(key)
        wait_anim(qtbot, ui)
        # Hiçbir sayfa, pencerenin içerik alanından daha geniş bir minimum genişlik dayatmamalı
        assert page.body_widget.minimumSizeHint().width() <= page.width(), key
        if page.scroll is not None:
            # kaydırma çubuğu belirince içerik bir olay döngüsünde yeniden boyutlanır
            qtbot.waitUntil(lambda: page.scroll.widget().width() <= page.scroll.viewport().width(), timeout=1000)
            assert page.scroll.horizontalScrollBar().maximum() == 0, key


def test_window_geometry_persisted(ui):
    ui.resize(1200, 700)
    ui.close()
    assert ui.ctx.settings.window_geometry
    assert type(ui.ctx.settings_manager)().settings.window_geometry == ui.ctx.settings.window_geometry


def test_sidebar_system_card_fits_height(ui, qtbot):
    card, last = ui.sidebar.system_card, ui.sidebar.buttons["settings"]
    for size in ((1366, 768), (1024, 640)):
        ui.resize(*size)
        qtbot.wait(50)
        assert last.geometry().bottom() < ui.sidebar.height()  # tüm menü öğeleri görünür
        if card.isVisibleTo(ui.sidebar):
            assert card.geometry().top() > last.geometry().bottom()  # kart menüyle çakışmaz
    # Kart sığmayacak kadar büyürse gizlenir, yer açılınca geri gelir
    from PySide6.QtCore import QSize
    card.sizeHint = lambda: QSize(200, 10_000)  # yalnızca sığma hesabı için devasa kart
    ui.resize(1024, 641)
    qtbot.waitUntil(lambda: not card.isVisibleTo(ui.sidebar), timeout=2000)
    del card.sizeHint
    ui.resize(1366, 768)
    qtbot.waitUntil(lambda: card.isVisibleTo(ui.sidebar), timeout=2000)


def test_grid_avoids_orphan_cards(ui, qtbot):
    grid = ui.pages["dashboard"].stats_grid
    for width, expected in ((1100, 4), (800, 2), (300, 1)):
        grid.resize(width, grid.height())
        assert grid.columns == expected


def test_clicks_reach_new_page_during_transition(ui, qtbot):
    """Geçiş animasyonu sürerken yapılan tıklama yeni sayfadaki düğmeye ulaşmalı."""
    theme.animations = True
    ui.show_page("settings")
    wait_anim(qtbot, ui)
    ui.show_page("queue")
    assert ui.stack.animating
    page = ui.pages["queue"]
    qtbot.mouseClick(page.go_audio, Qt.MouseButton.LeftButton)
    assert ui.current_page_key() == "audio_download"
