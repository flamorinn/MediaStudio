"""Video İndir / Ses İndir sayfalarının uçtan uca testleri (gerçek pencere + yerel test sunucusu)."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from app.application import create_app
from app.core.context import AppContext
from app.models import DownloadStatus, OperationKind, OperationStatus


@pytest.fixture
def ui(qtbot, qapp, deps, tmp_path):
    ctx = AppContext()
    ctx.update_settings(download_dir=str(tmp_path / "indirilenler"))
    _app, ctx, window = create_app([], ctx)
    qtbot.addWidget(window)
    window.resize(1366, 768)
    window.show()
    qtbot.waitExposed(window)
    qtbot.waitUntil(lambda: ctx.dependencies is not None, timeout=30_000)
    yield window
    ctx.shutdown()


def analyze(qtbot, page, url):
    page.url_edit.setText(url)
    assert page.analyze_button.isEnabled()
    page.analyze_button.click()
    assert not page.analyze_button.isEnabled() and page.analyze_button.text() == "Analiz ediliyor…"
    qtbot.waitUntil(lambda: page._request == 0, timeout=30_000)
    # Sonuç kartı belirince yerleşim bir sonraki olay döngüsünde güncellenir; tıklamadan önce otursun
    window = page.window()
    qtbot.waitUntil(lambda: not window.stack.animating, timeout=2_000)
    qtbot.wait(30)


def test_video_page_full_flow(ui, qtbot, server):
    page = ui.pages["video_download"]
    ui.show_page("video_download")
    assert not page.analyze_button.isEnabled() and not page.start_button.isEnabled()

    analyze(qtbot, page, server.url("dash/manifest.mpd"))
    assert page.media is not None and page.result_card.isVisibleTo(page)
    assert page.media_title.text() == "manifest"
    assert [page.quality_combo.itemData(i) for i in range(page.quality_combo.count())] == ["best", "360"]
    assert not page.start_button.isEnabled()  # hak onayı verilmeden başlatılamaz
    assert "onaylayın" in page.start_button.toolTip()

    qtbot.mouseClick(page.rights_toggle, Qt.MouseButton.LeftButton)
    assert page.start_button.isEnabled()
    page.start_button.click()
    assert page.media is None and not page.result_card.isVisibleTo(page) and page.url_edit.text() == ""
    assert "kuyruğa eklendi" in page.added_label.text()
    assert "etkin" in page.queue_link.text()

    task = ui.ctx.queue.tasks()[0]
    assert task.kind is OperationKind.VIDEO_DOWNLOAD and task.quality == "best" and task.title == "manifest"
    queue_page = ui.pages["queue"]
    assert task.id in queue_page.rows
    qtbot.waitUntil(lambda: ui.ctx.queue.get(task.id).status.is_finished, timeout=60_000)
    task = ui.ctx.queue.get(task.id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    assert Path(task.output_path).is_file()
    assert queue_page.rows[task.id].open_button.isVisibleTo(queue_page.rows[task.id])
    assert ui.pages["history"].table.rowCount() == 1
    assert ui.pages["dashboard"].stat_downloads.value.text() == "1"
    assert ui.toasts.toasts[0].note.title == "İndirme tamamlandı"


def test_audio_page_uses_selected_bitrate(ui, qtbot, server):
    page = ui.pages["audio_download"]
    assert page.quality_combo.currentData() == ui.ctx.settings.audio_bitrate
    analyze(qtbot, page, server.url("sample.mp4"))
    page.quality_combo.setCurrentIndex(page.quality_combo.findData(256))
    page.rights_toggle.setChecked(True)
    page.start_button.click()
    task = ui.ctx.queue.tasks()[0]
    assert task.kind is OperationKind.AUDIO_DOWNLOAD and task.audio_bitrate == 256
    qtbot.waitUntil(lambda: ui.ctx.queue.get(task.id).status.is_finished, timeout=60_000)
    assert ui.ctx.queue.get(task.id).output_path.endswith(".mp3")
    assert ui.ctx.history.list()[0].kind is OperationKind.AUDIO_DOWNLOAD


def test_analysis_error_shown_inline(ui, qtbot, server):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("yok.mp4"))
    assert page.media is None and "404" in page.analyze_status.text()
    page.url_edit.setText("bağlantı değil")
    page.analyze_button.click()
    qtbot.waitUntil(lambda: page._request == 0, timeout=10_000)
    assert "Geçerli bir" in page.analyze_status.text()
    assert not page.start_button.isEnabled()


def test_changing_url_invalidates_analysis(ui, qtbot, server):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    page.rights_toggle.setChecked(True)
    assert page.start_button.isEnabled()
    page.url_edit.setText(server.url("dash/manifest.mpd"))
    assert page.media is None and not page.start_button.isEnabled()


def test_paste_reset_and_enter_key(ui, qtbot, server):
    page = ui.pages["video_download"]
    QGuiApplication.clipboard().setText(server.url("sample.mp4"))
    page.paste_button.click()
    qtbot.waitUntil(lambda: page.media is not None, timeout=30_000)
    page.reset_button.click()
    assert page.url_edit.text() == "" and page.media is None
    page.url_edit.setText(server.url("sample.mp4"))
    QTest.keyClick(page.url_edit, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.media is not None, timeout=30_000)


def test_custom_folder_for_one_download(ui, qtbot, server, tmp_path, monkeypatch):
    page = ui.pages["audio_download"]
    target = tmp_path / "ozel"
    monkeypatch.setattr("app.ui.pages.download_page.QFileDialog.getExistingDirectory", lambda *a, **k: str(target))
    page.folder_button.click()
    assert page.folder_label.full_text() == str(target)
    analyze(qtbot, page, server.url("sample.mp4"))
    page.rights_toggle.setChecked(True)
    page.start_button.click()
    task = ui.ctx.queue.tasks()[0]
    qtbot.waitUntil(lambda: ui.ctx.queue.get(task.id).status.is_finished, timeout=60_000)
    assert Path(ui.ctx.queue.get(task.id).output_path).parent == target
    assert ui.ctx.settings.download_dir != str(target)  # genel ayar değişmedi


def test_missing_ffmpeg_blocks_start(ui, qtbot, server):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    page.rights_toggle.setChecked(True)
    deps = ui.ctx.dependencies
    ui.ctx.dependencies = type(deps)(ffmpeg=None, ffprobe=None, ytdlp=deps.ytdlp)
    ui.ctx.dependencies_changed.emit(ui.ctx.dependencies)
    assert not page.start_button.isEnabled() and "FFmpeg bulunamadı" in page.blocker.text()


def test_close_with_active_download_asks(ui, qtbot, server, monkeypatch):
    task = ui.ctx.downloads.enqueue(server.url("slow/big.mp4"), OperationKind.VIDEO_DOWNLOAD)
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: False)
    assert not ui.close() and ui.isVisible()  # vazgeçildi: pencere açık, indirme sürüyor
    assert not ui.ctx.queue.get(task.id).status.is_finished
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: True)
    assert ui.close()
    qtbot.waitUntil(lambda: ui.ctx.queue.get(task.id).status is DownloadStatus.CANCELLED, timeout=30_000)
    assert ui.ctx.history.list()[0].status is OperationStatus.CANCELLED
