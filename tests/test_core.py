"""Çekirdek katman testleri: ayarlar, geçmiş, bağımlılıklar, kuyruk, bildirimler, biçimlendirme."""

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.dependencies import DependencyChecker
from app.core.download_queue import DownloadQueue
from app.core.errors import MediaStudioError
from app.core.history_store import HistoryStore
from app.core.notifications import NotificationCenter
from app.core.settings_manager import SettingsManager
from app.models import DownloadStatus, DownloadTask, NotificationLevel, OperationKind, OperationStatus
from app.models.settings import AppSettings
from app.utils import formatting, paths

K, S = OperationKind, OperationStatus


# ============================================================ yollar
def test_user_data_dir_uses_override(isolated_data_dir):
    assert paths.user_data_dir() == isolated_data_dir
    assert paths.logs_dir().is_dir()


def test_project_root_in_dev():
    assert (paths.project_root() / "app" / "__init__.py").is_file()
    assert paths.asset_path("logo.svg").parent.name == "assets"


# ============================================================ ayarlar
def test_settings_defaults_roundtrip(tmp_path):
    mgr = SettingsManager()
    assert mgr.settings.accent == "aurora" and mgr.settings.audio_bitrate == 192
    assert mgr.download_dir() == tmp_path / "downloads"
    assert mgr.update(audio_bitrate=320, video_quality="1080", accent="violet") == \
        ["audio_bitrate", "video_quality", "accent"]
    again = SettingsManager().settings
    assert (again.audio_bitrate, again.video_quality, again.accent) == (320, "1080", "violet")


def test_settings_update_reports_only_real_changes():
    mgr = SettingsManager()
    assert mgr.update(audio_bitrate=192) == []
    assert mgr.update(audio_bitrate=256) == ["audio_bitrate"]


def test_settings_invalid_values_normalized(isolated_data_dir):
    (isolated_data_dir / "settings.json").write_text(json.dumps({
        "audio_bitrate": 999, "max_concurrent_downloads": 99, "video_quality": "8k",
        "accent": "pembe", "ffmpeg_path": "", "unknown": 1}), encoding="utf-8")
    s = SettingsManager().settings
    assert (s.audio_bitrate, s.max_concurrent_downloads, s.video_quality, s.accent, s.ffmpeg_path) == \
        (192, 5, "best", "aurora", None)


def test_settings_corrupt_file_recovered(isolated_data_dir):
    (isolated_data_dir / "settings.json").write_text("{ bozuk", encoding="utf-8")
    mgr = SettingsManager()
    assert mgr.recovered_from_corruption
    assert mgr.settings == AppSettings().normalized()
    assert (isolated_data_dir / "settings.corrupt.json").exists()


def test_settings_unknown_key_rejected():
    with pytest.raises(KeyError):
        SettingsManager().update(nope=1)


def test_settings_reset_keeps_window_geometry():
    mgr = SettingsManager()
    mgr.update(window_geometry="abc", accent="electric", audio_bitrate=128)
    mgr.reset()
    s = SettingsManager().settings
    assert (s.window_geometry, s.accent, s.audio_bitrate) == ("abc", "aurora", 192)


# ============================================================ geçmiş
@pytest.fixture
def store():
    s = HistoryStore()
    yield s
    s.close()


def test_history_add_list_order(store):
    store.add(kind=K.VIDEO_DOWNLOAD, title="A", status=S.SUCCESS, size_bytes=10)
    store.add(kind=K.CONVERT, title="B", status=S.FAILED, details="hata")
    entries = store.list()
    assert [e.title for e in entries] == ["B", "A"]
    assert entries[0].kind is K.CONVERT and entries[0].status is S.FAILED and entries[0].details == "hata"


def test_history_filter_and_search(store):
    store.add(kind=K.VIDEO_DOWNLOAD, title="Tatil videosu", status=S.SUCCESS)
    store.add(kind=K.AUDIO_DOWNLOAD, title="Podcast", status=S.SUCCESS, source="https://ornek.test/p")
    store.add(kind=K.CUT, title="Klip", status=S.SUCCESS)
    assert [e.title for e in store.list(kind=K.CUT)] == ["Klip"]
    assert [e.title for e in store.list(search="ornek.test")] == ["Podcast"]
    assert [e.title for e in store.list(search="tatil")] == ["Tatil videosu"]  # büyük/küçük harf duyarsız


def test_history_stats(store):
    now = datetime.now()
    store.add(kind=K.VIDEO_DOWNLOAD, title="v", status=S.SUCCESS, size_bytes=1000)
    store.add(kind=K.AUDIO_DOWNLOAD, title="a", status=S.SUCCESS, size_bytes=500)
    store.add(kind=K.AUDIO_DOWNLOAD, title="x", status=S.FAILED, size_bytes=999)
    store.add(kind=K.CONVERT, title="c", status=S.SUCCESS, created_at=now - timedelta(days=30))
    st = store.stats(now)
    assert (st.total, st.completed, st.failed, st.downloaded_files, st.downloaded_bytes, st.last_7_days) == \
        (4, 3, 1, 2, 1500, 3)


def test_history_delete_clear_persist(store):
    e = store.add(kind=K.CUT, title="x", status=S.SUCCESS)
    store.add(kind=K.CUT, title="y", status=S.SUCCESS)
    assert store.delete(e.id) and not store.delete(e.id)
    assert HistoryStore().count() == 1
    store.clear()
    assert store.count() == 0


def test_history_rejects_invalid_values(store):
    with pytest.raises(ValueError):
        store.add(kind="hack", title="x", status=S.SUCCESS)
    with pytest.raises(ValueError):
        store.add(kind=K.CUT, title="x", status="garip")


# ============================================================ bağımlılıklar
@pytest.fixture(scope="module")
def real_report():
    return DependencyChecker().detect()


def test_dependencies_found_on_this_machine(real_report):
    assert real_report.ffmpeg and real_report.ffmpeg.path.is_file()
    assert real_report.ffprobe and real_report.ffprobe.path.is_file()
    assert real_report.ytdlp and real_report.ytdlp.version
    assert real_report.all_ready


def test_invalid_custom_ffmpeg_falls_back(tmp_path):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_text("program değil")
    report = DependencyChecker().detect(str(fake))
    assert report.custom_path_rejected and report.ffmpeg.source != "custom"


def test_custom_ffmpeg_folder_accepted(real_report):
    report = DependencyChecker().detect(str(real_report.ffmpeg.path.parent))
    assert report.ffmpeg.source == "custom" and not report.custom_path_rejected
    assert report.ffprobe is not None


def test_bundled_ffmpeg_preferred(tmp_path, monkeypatch, real_report):
    bundle = tmp_path / "bundle"
    (bundle / "bin").mkdir(parents=True)
    shutil.copy2(real_report.ffmpeg.path, bundle / "bin" / real_report.ffmpeg.path.name)
    shutil.copy2(real_report.ffprobe.path, bundle / "bin" / real_report.ffprobe.path.name)
    monkeypatch.setattr("app.core.dependencies.project_root", lambda: bundle)
    report = DependencyChecker().detect()
    assert report.ffmpeg.source == "bundled" and report.ffprobe.source == "bundled"


def test_ffmpeg_missing(monkeypatch, tmp_path):
    checker = DependencyChecker()
    monkeypatch.setattr(checker, "_automatic_candidates", lambda: [("path", tmp_path / "yok.exe")])
    report = checker.detect()
    assert report.ffmpeg is None and report.ffprobe is None and not report.media_ready
    assert str(tmp_path / "yok.exe") in report.tried


def test_probe_rejects_other_programs():
    assert DependencyChecker().probe_version(Path(sys.executable)) is None


# ============================================================ kuyruk
def test_queue_lifecycle(qtbot):
    q = DownloadQueue()
    with qtbot.waitSignal(q.changed):
        a = q.add(DownloadTask("https://ornek.test/1", K.VIDEO_DOWNLOAD, "Bir"))
    b = q.add(DownloadTask("https://ornek.test/2", K.AUDIO_DOWNLOAD))
    assert b.display_title == "https://ornek.test/2"
    q.update(a.id, status=DownloadStatus.RUNNING, progress=0.4)
    assert q.active_count() == 1 and q.pending_count() == 1
    assert not q.remove(a.id)            # aktif indirme kaldırılamaz
    assert q.remove(b.id)
    q.update(a.id, status=DownloadStatus.COMPLETED)
    assert q.clear_finished() == 1 and q.tasks() == []
    with pytest.raises(AttributeError):
        q.update(q.add(DownloadTask("u", K.CUT)).id, nope=1)


# ============================================================ bildirimler
def test_notification_center(qtbot):
    center = NotificationCenter()
    with qtbot.waitSignal(center.posted) as blocker:
        center.warning("Başlık", "Mesaj", "ayrıntı")
    note = blocker.args[0]
    assert (note.level, note.title, note.details) == (NotificationLevel.WARNING, "Başlık", "ayrıntı")
    friendly = center.exception(MediaStudioError("Anlaşılır mesaj", detail="teknik"))
    assert friendly.message == "Anlaşılır mesaj" and friendly.details == "teknik"
    assert center.exception(ValueError()).message == "ValueError"
    assert len(center.recent) == 3


# ============================================================ biçimlendirme
def test_formatting():
    assert formatting.human_size(0) == "0 B"
    assert formatting.human_size(1536) == "1,5 KB"
    assert formatting.human_duration(3725) == "1:02:05"
    assert formatting.human_duration(None) == "—"
    now = datetime(2026, 11, 18, 15, 0)
    assert formatting.relative_time("2026-11-18T14:59:30", now) == "az önce"
    assert formatting.relative_time("2026-11-18T14:30:00", now) == "30 dk önce"
    assert formatting.relative_time("2026-11-17T09:05:00", now) == "dün 09:05"
    assert formatting.long_date(now) == "18 Kasım 2026, Çarşamba"
    assert formatting.greeting(now) == "İyi günler"
