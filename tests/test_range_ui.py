"""İndirme sayfası: Tümü / Zaman aralığı seçimi ve otomatik analiz (gerçek pencere + yerel sunucu)."""

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from app.application import create_app
from app.core.context import AppContext
from app.models import DownloadStatus, OperationKind
from app.services import ytdlp_support

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    window.show_page("video_download")
    qtbot.waitUntil(lambda: not window.stack.animating, timeout=2_000)
    yield window
    ctx.shutdown()


@pytest.fixture
def with_duration(monkeypatch):
    """Yerel test dosyaları süre bildirmez (YouTube gibi gerçek kaynaklar bildirir); analizi süreyle tamamla."""
    original = ytdlp_support.analyze
    monkeypatch.setattr("app.ui.pages.download_page.analyze",
                        lambda url: dataclasses.replace(original(url), duration=6.0))


def analyze(qtbot, page, url):
    page.url_edit.setText(url)
    page.analyze_button.click()
    qtbot.waitUntil(lambda: page.media is not None or page._request == 0, timeout=30_000)
    qtbot.waitUntil(lambda: page._request == 0, timeout=30_000)
    qtbot.wait(30)


def duration_of(deps, path):
    out = subprocess.run([str(deps.ffprobe.path), "-v", "error", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, creationflags=NO_WINDOW).stdout
    return float(json.loads(out)["format"]["duration"])


def test_range_disabled_without_duration(ui, qtbot, server):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    assert page.media is not None and not page.media.duration
    assert not page.part_range.isEnabled() and "süre bilgisi" in page.part_range.toolTip()
    page.set_ranged(True)
    assert page.part_all.isChecked() and not page.range_panel.isVisibleTo(page)


def test_range_selection_and_turkish_timecodes(ui, qtbot, server, with_duration):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    assert page.part_all.isChecked() and page.start_button.text() == "Kuyruğa ekle ve indir"
    qtbot.mouseClick(page.part_range, Qt.MouseButton.LeftButton)
    assert page.range_panel.isVisibleTo(page) and page.part_range.isChecked()
    assert page.range_start.text() == "00:00:00" and page.range_end.text() == "00:00:06"

    page.set_section("0.02", "0.05")  # dakika.saniye: 0 dk 2 sn – 0 dk 5 sn
    assert page.range_slider.values() == (2000, 5000)
    assert page.range_start.text() == "00:00:02" and page.range_length.text() == "00:00:03"
    assert page.selected_section() == (2.0, 5.0)
    page.rights_toggle.setChecked(True)
    assert page.start_button.isEnabled() and page.start_button.text() == "Aralığı indir (00:03)"

    qtbot.mouseClick(page.part_all, Qt.MouseButton.LeftButton)
    assert page.selected_section() is None and not page.range_panel.isVisibleTo(page)
    assert page.start_button.text() == "Kuyruğa ekle ve indir"


@pytest.mark.parametrize("start, end, message", [
    ("0.75", "0.05", "60'tan küçük"),
    ("0.02", "0.30", "aşıyor"),
    ("0.04", "0.04", "en az 1 saniye"),
    ("abc", "0.05", "Geçersiz zaman"),
])
def test_range_errors_block_download(ui, qtbot, server, with_duration, start, end, message):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    page.set_ranged(True)
    page.rights_toggle.setChecked(True)
    page.set_section("0.01", "0.04")
    page.set_section(start, end)
    assert message in page.range_error.text() and page.range_error.isVisibleTo(page)
    assert not page.start_button.isEnabled()
    assert page.selected_section() == (1.0, 4.0)  # geçersiz giriş seçimi bozmaz
    page.range_slider.setRange(1000, 5000)       # geçerli değişiklik hatayı kaldırır
    assert page.range_error.isHidden() and page.start_button.isEnabled()


def test_range_download_end_to_end(ui, qtbot, server, deps, with_duration):
    page = ui.pages["video_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    page.set_ranged(True)
    page.set_section("0.01", "0.04")
    page.rights_toggle.setChecked(True)
    page.start_button.click()
    assert "(00:00:01–00:00:04) kuyruğa eklendi" in page.added_label.text()
    task = ui.ctx.queue.tasks()[0]
    assert (task.section_start, task.section_end) == (1.0, 4.0) and task.prefetched_info is not None
    row = ui.pages["queue"].rows[task.id]
    assert "00:00:01–00:00:04" in row.title.full_text()
    qtbot.waitUntil(lambda: ui.ctx.queue.get(task.id).status.is_finished, timeout=60_000)
    task = ui.ctx.queue.get(task.id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    assert Path(task.output_path).name == "sample (00.00.01-00.00.04).mp4"
    assert duration_of(deps, task.output_path) == pytest.approx(3.0, abs=0.15)
    assert "[00:00:01–00:00:04]" in ui.ctx.history.list()[0].title


def test_audio_page_range(ui, qtbot, server, deps, with_duration):
    ui.show_page("audio_download")
    page = ui.pages["audio_download"]
    analyze(qtbot, page, server.url("sample.mp4"))
    page.set_ranged(True)
    page.set_section("0.02", "0.05")
    page.rights_toggle.setChecked(True)
    page.start_button.click()
    task = ui.ctx.queue.tasks()[0]
    assert task.kind is OperationKind.AUDIO_DOWNLOAD and task.has_section
    qtbot.waitUntil(lambda: ui.ctx.queue.get(task.id).status.is_finished, timeout=60_000)
    task = ui.ctx.queue.get(task.id)
    assert task.output_path.endswith(".mp3") and duration_of(deps, task.output_path) == pytest.approx(3.0, abs=0.15)


def test_auto_analyze_on_paste_or_typing(ui, qtbot, server):
    page = ui.pages["video_download"]
    page.url_edit.setText("https://")          # tamamlanmamış bağlantı analiz başlatmaz
    qtbot.wait(900)
    assert page._request == 0 and page.media is None
    page.url_edit.setText(server.url("sample.mp4"))  # düğmeye basmadan analiz kendiliğinden başlar
    qtbot.waitUntil(lambda: page.media is not None, timeout=30_000)
    assert page.media.url == server.url("sample.mp4")


def test_new_link_supersedes_running_analysis(ui, qtbot, server):
    page = ui.pages["video_download"]
    page.url_edit.setText(server.url("sample.mp4"))
    page.start_analysis()
    page.url_edit.setText(server.url("dash/manifest.mpd"))
    page.start_analysis()                      # eskisi bitmeden yenisi başlar
    qtbot.waitUntil(lambda: page.media is not None and page._request == 0, timeout=30_000)
    qtbot.wait(300)                            # eski analizin geç gelen sonucu yok sayılmalı
    assert page.media.url == server.url("dash/manifest.mpd")


def test_elapsed_time_shown_while_analyzing(ui):
    import time

    page = ui.pages["video_download"]
    page._request = 99
    page._analysis_started = time.monotonic() - 3.2
    page._analysis_tick()
    assert page.analyze_status.text() == "Bağlantı analiz ediliyor… 3 sn"
    page._request = 0
