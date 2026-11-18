"""Video Kes sayfasının uçtan uca testleri (gerçek pencere + FFmpeg ile üretilen dosyalar)."""

import json
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest

from app.application import create_app
from app.core.context import AppContext
from app.models import OperationKind
from app.models.cut import CutFormat, CutMode, CutStatus
from app.services.cutter import CutJob
from tests.test_cutter import cut_media  # noqa: F401 - oturum fixture'ı

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture
def ui(qtbot, qapp, deps, tmp_path):
    ctx = AppContext()
    ctx.update_settings(download_dir=str(tmp_path / "cikti"))
    _app, ctx, window = create_app([], ctx)
    qtbot.addWidget(window)
    window.resize(1366, 768)
    window.show()
    qtbot.waitExposed(window)
    qtbot.waitUntil(lambda: ctx.dependencies is not None, timeout=30_000)
    window.show_page("cutter")
    qtbot.waitUntil(lambda: not window.stack.animating, timeout=2_000)
    yield window
    CutJob.extra_input_args = ()
    ctx.shutdown()


def load(qtbot, page, path, monkeypatch):
    monkeypatch.setattr("app.ui.pages.cutter_page.QFileDialog.getOpenFileName", lambda *a, **k: (str(path), ""))
    page.change_button.click() if page.loaded_widget.isVisibleTo(page) else page.choose_button.click()
    qtbot.waitUntil(lambda: page.details is not None and page.details.path == str(path), timeout=30_000)
    qtbot.wait(30)  # yerleşim otursun


def wait_previews(qtbot, page):
    qtbot.waitUntil(lambda: page.start_preview.has_frame and page.end_preview.has_frame, timeout=30_000)


def wait_keyframe(qtbot, page):
    qtbot.waitUntil(lambda: page._keyframe_for == page.selection()[0], timeout=30_000)


def duration_of(deps, path):
    out = subprocess.run([str(deps.ffprobe.path), "-v", "error", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, creationflags=NO_WINDOW).stdout
    return float(json.loads(out)["format"]["duration"])


def test_initial_state(ui):
    page = ui.pages["cutter"]
    assert page.picker.isVisibleTo(page) and not page.loaded_widget.isVisibleTo(page)
    assert page.choose_button.isEnabled()
    assert not page.range_card.isVisibleTo(page) and not page.options_card.isVisibleTo(page)
    assert page.exports_stack.currentWidget() is page.exports_empty


def test_load_select_and_lossless_export(ui, qtbot, cut_media, deps, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    assert page.loaded_widget.isVisibleTo(page) and not page.picker.isVisibleTo(page)
    assert page.file_name.full_text() == "film.mp4"
    assert "320×180" in page.file_meta.text() and "H.264 / AAC" in page.file_meta.text()
    assert page.range_card.isVisibleTo(page) and page.options_card.isVisibleTo(page)
    assert page.start_edit.text() == "00:00:00.000" and page.end_edit.text().startswith("00:00:20")
    wait_previews(qtbot, page)

    page.set_selection(5, 9)
    assert page.selection() == (5.0, 9.0) and page.length_label.text() == "00:00:04.000"
    assert page.slider.values() == (5000, 9000)
    wait_keyframe(qtbot, page)
    assert page.keyframe == pytest.approx(4.0, abs=0.02)
    assert "00:00:04.000" in page.keyframe_note.text() and "1,00 sn önce" in page.keyframe_note.text()
    wait_previews(qtbot, page)
    assert "00:00:05.000" in page.start_preview.caption.text()

    page.export_button.click()
    qtbot.waitUntil(lambda: not page.manager.has_active(), timeout=60_000)
    item = page.manager.list()[0]
    assert item.status is CutStatus.COMPLETED and item.mode is CutMode.LOSSLESS
    assert item.effective_start == pytest.approx(4.0, abs=0.02)
    assert duration_of(deps, item.output_path) == pytest.approx(5.0, abs=0.15)
    row = page.rows[item.id]
    assert row.open_button.isVisibleTo(row) and "→" in row.meta.full_text()
    assert ui.ctx.history.list()[0].kind is OperationKind.CUT
    assert ui.toasts.toasts[0].note.title == "Kesim tamamlandı"


def test_keyframe_aligned_start_note(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    page.set_selection(6, 8)
    wait_keyframe(qtbot, page)
    assert "denk geliyor" in page.keyframe_note.text()
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(CutMode.PRECISE))
    assert "tam seçilen kareden" in page.keyframe_note.text()


def test_precise_and_mp3_exports(ui, qtbot, cut_media, deps, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    page.set_selection(5, 9)
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(CutMode.PRECISE))
    page.export_button.click()
    page.format_combo.setCurrentIndex(page.format_combo.findData(CutFormat.MP3))
    assert page.bitrate_row_widget.isVisibleTo(page) and not page.mode_row_widget.isVisibleTo(page)
    page.bitrate_combo.setCurrentIndex(page.bitrate_combo.findData(256))
    page.export_button.click()
    qtbot.waitUntil(lambda: not page.manager.has_active(), timeout=90_000)
    precise, mp3 = page.manager.list()
    assert precise.status is CutStatus.COMPLETED and mp3.status is CutStatus.COMPLETED
    assert duration_of(deps, precise.output_path) == pytest.approx(4.0, abs=0.1)
    assert mp3.output_path.endswith(".mp3") and mp3.bitrate == 256
    assert duration_of(deps, mp3.output_path) == pytest.approx(4.0, abs=0.1)


def test_slider_mouse_and_keyboard(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    slider = page.slider
    track = slider._track()
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton,
                     pos=QPointF(track.left() + track.width() * 0.25, slider.height() / 2).toPoint())
    lo, hi = slider.values()
    assert 4500 < lo < 5500 and hi > 19900  # başlangıç tutamacı tıklanan yere geldi, bitiş yerinde
    assert page.start_edit.text().startswith("00:00:0")
    slider.setFocus()
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert slider.values()[0] == lo + 100
    QTest.keyClick(slider, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert slider.values()[0] == lo + 1100
    QTest.keyClick(slider, Qt.Key.Key_Space)  # bitiş tutamacına geç
    QTest.keyClick(slider, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert slider.values()[1] == hi - 1000
    assert page.end_edit.text() == "00:00:" + f"{(hi - 1000) / 1000:06.3f}"


def test_slider_keeps_minimum_gap(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    page.slider.setRange(5000, 5000)
    lo, hi = page.slider.values()
    assert hi - lo >= 100


@pytest.mark.parametrize("start, end, message", [
    ("abc", "00:00:05", "Geçersiz zaman"),
    ("00:00:05", "00:00:05", "en az 0,1"),
    ("00:00:01", "00:00:30", "aşıyor"),
    ("00:00:01", "00:75", "60'tan küçük"),
])
def test_invalid_timecodes(ui, qtbot, cut_media, monkeypatch, start, end, message):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    page.set_selection(2, 4)
    page.start_edit.setText(start)
    page.end_edit.setText(end)
    page.end_edit.editingFinished.emit()
    assert message in page.range_error.text() and page.range_error.isVisibleTo(page)
    assert not page.export_button.isEnabled()
    assert page.selection() == (2.0, 4.0)  # geçersiz giriş seçimi bozmaz
    page.slider.setRange(3000, 6000)        # geçerli bir değişiklik hatayı temizler
    assert page.range_error.isHidden() and page.export_button.isEnabled()


def test_audio_only_source(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "ses.wav", monkeypatch)
    model = page.format_combo.model()
    assert not model.item(page.format_combo.findData(CutFormat.MP4)).isEnabled()
    assert page.current_format() is CutFormat.MP3
    assert not page.start_preview.isVisibleTo(page)  # görüntü yok, önizleme gizli


def test_incompatible_codecs_force_precise(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "klip.webm", monkeypatch)
    assert not page.mode_combo.model().item(page.mode_combo.findData(CutMode.LOSSLESS)).isEnabled()
    assert page.current_mode() is CutMode.PRECISE
    assert "kayıpsız kopyalanamıyor" in page.keyframe_note.text()


def test_drag_and_drop_single_file(ui, qtbot, cut_media, tmp_path):
    page = ui.pages["cutter"]

    def drop(paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        enter = QDragEnterEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
        page.dragEnterEvent(enter)
        if enter.isAccepted():
            page.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier))
        return enter.isAccepted()

    (tmp_path / "not.txt").write_text("x")
    assert not drop([tmp_path / "not.txt"])
    assert not drop([cut_media / "film.mp4", cut_media / "ses.wav"])  # tek dosya kabul edilir
    assert drop([cut_media / "film.mp4"])
    qtbot.waitUntil(lambda: page.details is not None, timeout=30_000)
    assert not page.picker.property("active")


def test_broken_file_shows_error(ui, qtbot, tmp_path, monkeypatch):
    page = ui.pages["cutter"]
    broken = tmp_path / "bozuk.mp4"
    broken.write_bytes(b"\0" * 2048)
    monkeypatch.setattr("app.ui.pages.cutter_page.QFileDialog.getOpenFileName", lambda *a, **k: (str(broken), ""))
    page.choose_button.click()
    qtbot.waitUntil(lambda: page._probe_request == 0, timeout=30_000)
    assert page.details is None and "okunamadı" in page.source_status.text()


def test_export_row_cancel_retry_remove(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    CutJob.extra_input_args = ("-re",)
    page.set_selection(0, 12)
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(CutMode.PRECISE))
    page.export_button.click()
    item = page.manager.list()[0]
    qtbot.waitUntil(lambda: page.manager.items[item.id].progress > 0.05, timeout=20_000)
    row = page.rows[item.id]
    assert row.cancel_button.isVisibleTo(row) and "hız" in row.meta.full_text()
    row.cancel_button.click()
    qtbot.waitUntil(lambda: page.manager.items[item.id].status is CutStatus.CANCELLED, timeout=10_000)
    assert row.retry_button.isVisibleTo(row) and row.remove_button.isVisibleTo(row)
    CutJob.extra_input_args = ()
    row.retry_button.click()
    qtbot.waitUntil(lambda: page.manager.items[item.id].status is CutStatus.COMPLETED, timeout=60_000)
    revealed = []
    monkeypatch.setattr("app.ui.pages.cutter_page.reveal_in_explorer", lambda p: revealed.append(p) or True)
    row.open_button.click()
    assert revealed == [page.manager.items[item.id].output_path]
    page.clear_button.click()
    assert page.manager.list() == [] and page.exports_stack.currentWidget() is page.exports_empty


def test_output_modes(ui, qtbot, cut_media, tmp_path, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    page.output_combo.setCurrentIndex(page.output_combo.findData("source"))
    page.output_combo.activated.emit(page.output_combo.currentIndex())
    assert ui.ctx.settings.cut_output_mode == "source"
    assert page.output_dir() == str(cut_media)
    target = tmp_path / "klipler"
    monkeypatch.setattr("app.ui.pages.cutter_page.QFileDialog.getExistingDirectory", lambda *a, **k: str(target))
    page.output_combo.setCurrentIndex(page.output_combo.findData("custom"))
    page.output_combo.activated.emit(page.output_combo.currentIndex())
    assert page.output_dir() == str(target) and ui.ctx.settings.cut_output_mode == "source"
    page.set_selection(2, 3)
    page.format_combo.setCurrentIndex(page.format_combo.findData(CutFormat.MP3))
    page.export_button.click()
    qtbot.waitUntil(lambda: not page.manager.has_active(), timeout=30_000)
    assert Path(page.manager.list()[0].output_path).parent == target


def test_close_asks_while_cutting(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    CutJob.extra_input_args = ("-re",)
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(CutMode.PRECISE))
    page.export_button.click()
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: False)
    assert not ui.close()
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: True)
    assert ui.close()
    qtbot.waitUntil(lambda: page.manager.list()[0].status is CutStatus.CANCELLED, timeout=10_000)


def test_loaded_page_fits_small_window(ui, qtbot, cut_media, monkeypatch):
    page = ui.pages["cutter"]
    load(qtbot, page, cut_media / "film.mp4", monkeypatch)
    for size in ((1024, 640), (1366, 768)):
        ui.resize(*size)
        qtbot.wait(50)
        assert page.body_widget.minimumSizeHint().width() <= page.width(), size
        qtbot.waitUntil(lambda: page.scroll.widget().width() <= page.scroll.viewport().width(), timeout=1000)
        assert page.scroll.horizontalScrollBar().maximum() == 0
