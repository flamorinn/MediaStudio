"""Dönüştürücü sayfasının uçtan uca testleri (gerçek pencere + FFmpeg ile üretilen dosyalar)."""

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from app.application import create_app
from app.core.context import AppContext
from app.models import ConversionStatus, OperationKind
from app.services.converter import ConversionJob
from tests.test_converter import media  # noqa: F401 - oturum fixture'ı


@pytest.fixture
def work(media, tmp_path):  # noqa: F811
    target = tmp_path / "kaynak"
    shutil.copytree(media, target)
    return target


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
    window.show_page("converter")
    qtbot.waitUntil(lambda: not window.stack.animating, timeout=2_000)
    yield window
    ConversionJob.extra_input_args = ()
    ctx.shutdown()


def page_of(ui):
    return ui.pages["converter"]


def wait_ready(qtbot, page):
    qtbot.waitUntil(lambda: all(i.status is not ConversionStatus.PROBING for i in page.manager.list()), timeout=30_000)


def drop(page, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    enter = QDragEnterEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    page.dragEnterEvent(enter)
    accepted = enter.isAccepted()
    highlighted = page.drop_zone.property("active")
    if accepted:
        page.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                  Qt.KeyboardModifier.NoModifier))
    return accepted, highlighted


def test_full_flow_with_file_dialog(ui, qtbot, work, monkeypatch, deps):
    page = page_of(ui)
    assert page.stack.currentWidget() is page.empty and not page.start_button.isEnabled()
    monkeypatch.setattr("app.ui.pages.converter_page.QFileDialog.getOpenFileNames",
                        lambda *a, **k: ([str(work / "clip.mp4"), str(work / "kayit.wav"), str(work / "sessiz.mp4")], ""))
    page.drop_zone.add_files_button.click()
    assert len(page.rows) == 3 and page.stack.currentWidget() is page.list_widget
    wait_ready(qtbot, page)
    assert page.start_button.text() == "Dönüştürmeyi başlat (2 dosya)"
    silent = next(r for r in page.rows.values() if page.manager.items[r.item_id].name == "sessiz.mp4")
    assert silent.pill.text() == "Desteklenmiyor" and "ses akışı yok" in silent.meta.full_text()
    assert "3 dosya" in page.summary.text() and "toplam" in page.summary.text()

    page.bitrate_combo.setCurrentIndex(page.bitrate_combo.findData(320))
    page.start_button.click()
    qtbot.waitUntil(lambda: not page.manager.has_active(), timeout=60_000)
    done = [i for i in page.manager.list() if i.status is ConversionStatus.COMPLETED]
    assert sorted(Path(i.output_path).name for i in done) == ["clip.mp3", "kayit.mp3"]
    assert all(i.bitrate == 320 and Path(i.output_path).parent == Path(ui.ctx.settings.download_dir) for i in done)
    row = page.rows[done[0].id]
    assert row.open_button.isVisibleTo(row) and row.remove_button.isVisibleTo(row)
    assert not page.start_button.isEnabled()
    assert ui.pages["history"].table.rowCount() == 2
    assert ui.ctx.history.list()[0].kind is OperationKind.CONVERT
    assert ui.toasts.toasts[0].note.title == "Dönüştürme tamamlandı"


def test_drag_and_drop_files_and_folder(ui, qtbot, work):
    page = page_of(ui)
    accepted, highlighted = drop(page, [work / "notlar.txt"])
    assert not accepted and not highlighted  # desteklenmeyen dosya kabul edilmez
    accepted, highlighted = drop(page, [work / "clip.mp4"])
    assert accepted and highlighted and not page.drop_zone.property("active")
    assert [i.name for i in page.manager.list()] == ["clip.mp4"]
    drop(page, [work])  # klasör bırakma: içindeki desteklenen dosyalar eklenir, tekrar edenler atlanır
    names = sorted(i.name for i in page.manager.list())
    assert names == sorted(["clip.mp4", "bozuk.mp4", "clip.mkv", "kayit.wav", "sessiz.mp4", "Şarkı ğüşiöç.mp4"])
    assert "zaten listede" in ui.toasts.toasts[0].note.message
    wait_ready(qtbot, page)


def test_add_folder_button(ui, qtbot, work, tmp_path, monkeypatch):
    page = page_of(ui)
    empty = tmp_path / "bos"
    empty.mkdir()
    monkeypatch.setattr("app.ui.pages.converter_page.QFileDialog.getExistingDirectory", lambda *a, **k: str(empty))
    page.drop_zone.add_folder_button.click()
    assert page.manager.list() == [] and ui.toasts.toasts[0].note.title == "Desteklenen dosya bulunamadı"
    monkeypatch.setattr("app.ui.pages.converter_page.QFileDialog.getExistingDirectory", lambda *a, **k: str(work))
    page.drop_zone.add_folder_button.click()
    assert len(page.manager.list()) == 6
    wait_ready(qtbot, page)


def test_output_modes(ui, qtbot, work, tmp_path, monkeypatch):
    page = page_of(ui)
    page.output_combo.setCurrentIndex(page.output_combo.findData("source"))
    page.output_combo.activated.emit(page.output_combo.currentIndex())
    assert ui.ctx.settings.convert_output_mode == "source"
    assert "kendi klasörüne" in page.output_label.full_text()

    # Özel klasör seçimi iptal edilirse önceki seçenek geri gelir
    monkeypatch.setattr("app.ui.pages.converter_page.QFileDialog.getExistingDirectory", lambda *a, **k: "")
    page.output_combo.setCurrentIndex(page.output_combo.findData("custom"))
    page.output_combo.activated.emit(page.output_combo.currentIndex())
    assert page.output_mode() == "source"

    target = tmp_path / "ozel"
    monkeypatch.setattr("app.ui.pages.converter_page.QFileDialog.getExistingDirectory", lambda *a, **k: str(target))
    page.output_combo.setCurrentIndex(page.output_combo.findData("custom"))
    page.output_combo.activated.emit(page.output_combo.currentIndex())
    assert page.output_label.full_text() == str(target)
    assert ui.ctx.settings.convert_output_mode == "source"  # özel klasör kalıcı ayar değildir

    page.add_paths([work / "kayit.wav"])
    wait_ready(qtbot, page)
    page.start_button.click()
    qtbot.waitUntil(lambda: not page.manager.has_active(), timeout=60_000)
    assert Path(page.manager.list()[0].output_path) == target / "kayit.mp3"


def test_row_actions_cancel_retry_remove(ui, qtbot, work, monkeypatch):
    page = page_of(ui)
    ConversionJob.extra_input_args = ("-re",)
    page.add_paths([work / "clip.mp4", work / "bozuk.mp4"])
    wait_ready(qtbot, page)
    clip_id = next(i.id for i in page.manager.list() if i.name == "clip.mp4")
    broken_id = next(i.id for i in page.manager.list() if i.name == "bozuk.mp4")
    broken_row = page.rows[broken_id]
    assert broken_row.details_button.isVisibleTo(broken_row)
    shown = []
    monkeypatch.setattr("app.ui.pages.converter_page.ErrorDetailsDialog.exec", lambda self: shown.append(self) or 0)
    broken_row.details_button.click()
    assert shown and shown[0].details.toPlainText()

    page.start_button.click()
    qtbot.waitUntil(lambda: page.manager.items[clip_id].progress > 0.05, timeout=20_000)
    row = page.rows[clip_id]
    assert row.cancel_button.isVisibleTo(row) and not row.remove_button.isVisibleTo(row)
    assert page.cancel_all_button.isVisibleTo(page) and "hız" in row.meta.full_text()
    row.cancel_button.click()
    qtbot.waitUntil(lambda: page.manager.items[clip_id].status is ConversionStatus.CANCELLED, timeout=10_000)
    assert row.retry_button.isVisibleTo(row)
    ConversionJob.extra_input_args = ()
    row.retry_button.click()
    qtbot.waitUntil(lambda: page.manager.items[clip_id].status is ConversionStatus.COMPLETED, timeout=30_000)

    broken_row.remove_button.click()
    assert broken_id not in page.manager.items
    page.clear_button.click()
    assert page.manager.list() == [] and page.stack.currentWidget() is page.empty


def test_blocked_without_ffmpeg(ui, work):
    page = page_of(ui)
    deps = ui.ctx.dependencies
    ui.ctx.dependencies = type(deps)(ffmpeg=None, ffprobe=None, ytdlp=deps.ytdlp)
    ui.ctx.dependencies_changed.emit(ui.ctx.dependencies)
    assert "FFmpeg ve FFprobe gerekli" in page.blocker.text()
    assert not page.drop_zone.add_files_button.isEnabled()
    page.add_paths([work / "clip.mp4"])
    assert page.manager.list() == [] and ui.toasts.toasts[0].note.title == "Dosyalar eklenemedi"


def test_close_asks_while_converting(ui, qtbot, work, monkeypatch):
    page = page_of(ui)
    ConversionJob.extra_input_args = ("-re",)
    page.add_paths([work / "clip.mp4"])
    wait_ready(qtbot, page)
    page.start_button.click()
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: False)
    assert not ui.close()
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: True)
    assert ui.close()
    qtbot.waitUntil(lambda: page.manager.list()[0].status is ConversionStatus.CANCELLED, timeout=10_000)
    assert not list(Path(ui.ctx.settings.download_dir).glob("*.converting.*"))
