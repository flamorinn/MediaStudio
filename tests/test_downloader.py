"""İndirme motoru testleri — yalnızca yerel test sunucusuyla (internete çıkılmaz)."""

import json
import subprocess
from pathlib import Path

import pytest

from app.core.context import AppContext
from app.core.errors import DependencyMissingError
from app.models import DownloadStatus, OperationKind, OperationStatus
from app.services import ytdlp_support as ys
from app.services.downloader import DownloadManager

K = OperationKind


@pytest.fixture
def engine(qapp, deps, tmp_path):
    ctx = AppContext()
    ctx.dependencies = deps
    ctx.update_settings(download_dir=str(tmp_path / "indirilenler"))
    manager = DownloadManager(ctx)
    ctx.downloads = manager
    yield ctx, manager
    manager.shutdown()
    ctx.history.close()


def wait_done(qtbot, ctx, task_id, timeout=90_000):
    qtbot.waitUntil(lambda: ctx.queue.get(task_id).status.is_finished, timeout=timeout)
    return ctx.queue.get(task_id)


def probe(ffprobe: Path, file: str) -> dict:
    out = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", file],
                         capture_output=True, text=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return json.loads(out)


# ============================================================ saf yardımcılar
def test_safe_options_never_bypass_restrictions():
    opts = ys.base_options()
    for key in ys.FORBIDDEN_OPTIONS:
        assert not opts.get(key)
    assert opts["geo_bypass"] is False and opts["allow_unplayable_formats"] is False
    assert opts["noplaylist"] is True
    with pytest.raises(ValueError):
        ys.base_options(cookiesfrombrowser=("chrome",))


@pytest.mark.parametrize("text, expected", [
    ("ERROR: [x] abc: This video is DRM protected", "DRM"),
    ("Sign in to confirm your age", "giriş, üyelik"),
    ("Private video. Sign in if you've been granted access", "giriş, üyelik"),
    ("The uploader has not made this video available in your country", "bölgenizde"),
    ("Unsupported URL: https://ornek.test", "desteklenmiyor"),
    ("HTTP Error 404: Not Found", "404"),
    ("<urlopen error [Errno 11001] getaddrinfo failed>", "Ağ bağlantısı"),
    ("[Errno 28] No space left on device", "boş alan"),
    ("tamamen bilinmeyen", "İndirme başarısız"),
])
def test_friendly_error(text, expected):
    assert expected in ys.friendly_error(text)


def test_validate_url():
    assert ys.validate_url("  https://ornek.test/v ") == "https://ornek.test/v"
    for bad in ("", "ornek.test/v", "ftp://x/y", "javascript:alert(1)", "file:///C:/a.mp4"):
        with pytest.raises(ys.DownloadFailure):
            ys.validate_url(bad)


def test_check_info_rejects_out_of_scope_content():
    with pytest.raises(ys.DownloadFailure, match="Oynatma listeleri"):
        ys.check_info({"_type": "playlist", "entries": []})
    with pytest.raises(ys.DownloadFailure, match="Canlı"):
        ys.check_info({"is_live": True})
    with pytest.raises(ys.DownloadFailure, match="DRM"):
        ys.check_info({"formats": [{"has_drm": True}, {"has_drm": True}]})
    ys.check_info({"formats": [{"has_drm": True}, {"has_drm": False}]})  # DRM'siz seçenek varsa sorun yok


def test_format_selector():
    assert ys.format_selector(K.AUDIO_DOWNLOAD) == "ba/b"
    assert ys.format_selector(K.VIDEO_DOWNLOAD, "best").startswith("bv*[ext=mp4]+ba[ext=m4a]")
    sel = ys.format_selector(K.VIDEO_DOWNLOAD, "720")
    assert "height<=720" in sel and sel.endswith("/bv*+ba/b")


def test_filenames(tmp_path):
    assert ys.safe_stem('a/b:c*d?"e<f>g|h') == "a_b_c_d_'e_f_g_h" or "/" not in ys.safe_stem("a/b")
    assert ys.safe_stem("CON") == "CON_"
    assert ys.safe_stem("") == "medya"
    assert len(ys.safe_stem("x" * 400)) <= ys.MAX_STEM_LENGTH
    assert ys.unique_stem(tmp_path, "Video") == "Video"
    (tmp_path / "Video.webm").write_text("kullanıcı dosyası")
    assert ys.unique_stem(tmp_path, "Video") == "Video (2)"
    (tmp_path / "Video (2).mp4").write_text("x")
    assert ys.unique_stem(tmp_path, "Video") == "Video (3)"


def test_cleanup_only_touches_new_files(tmp_path):
    (tmp_path / "Klip.webm").write_text("kullanıcının dosyası")
    before = ys.files_with_stem(tmp_path, "Klip")
    (tmp_path / "Klip.f1.mp4.part").write_text("yarım")
    (tmp_path / "Klip.mp4.part-Frag3").write_text("parça")
    removed = ys.cleanup_partials(tmp_path, "Klip", before)
    assert sorted(removed) == ["Klip.f1.mp4.part", "Klip.mp4.part-Frag3"]
    assert (tmp_path / "Klip.webm").exists()


# ============================================================ analiz
def test_analyze_direct_file(server):
    info = ys.analyze(server.url("sample.mp4"))
    assert info.title == "sample" and info.extractor == "Generic"


def test_analyze_dash_lists_qualities(server):
    info = ys.analyze(server.url("dash/manifest.mpd"))
    assert info.heights == (360,) and info.has_video and info.has_audio


def test_analyze_errors_are_friendly(server):
    with pytest.raises(ys.DownloadFailure) as err:
        ys.analyze(server.url("yok.mp4"))
    assert "404" in err.value.user_message and "404" in err.value.detail
    with pytest.raises(ys.DownloadFailure, match="Geçerli bir"):
        ys.analyze("bağlantı değil")


# ============================================================ indirme
def test_video_download_direct(qtbot, engine, server, deps):
    ctx, manager = engine
    seen = []
    ctx.queue.task_updated.connect(lambda tid: seen.append(ctx.queue.get(tid).progress))
    task = manager.enqueue(server.url("sample.mp4"), K.VIDEO_DOWNLOAD)
    assert task.status is DownloadStatus.RUNNING
    task = wait_done(qtbot, ctx, task.id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    out = Path(task.output_path)
    assert out.is_file() and out.suffix == ".mp4" and out.parent == Path(ctx.settings.download_dir)
    assert task.progress == 1.0 and any(p is not None and 0 < p for p in seen)
    entry = ctx.history.list()[0]
    assert entry.status is OperationStatus.SUCCESS and entry.output_path == str(out)
    assert entry.size_bytes == out.stat().st_size and entry.source == server.url("sample.mp4")


def test_video_download_merges_separate_streams(qtbot, engine, server, deps):
    ctx, manager = engine
    phases = []
    ctx.queue.task_updated.connect(lambda tid: phases.append(ctx.queue.get(tid).phase))
    task = wait_done(qtbot, ctx, manager.enqueue(server.url("dash/manifest.mpd"), K.VIDEO_DOWNLOAD).id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    streams = {s["codec_type"] for s in probe(deps.ffprobe.path, task.output_path)["streams"]}
    assert streams == {"video", "audio"}  # tek MP4 içinde birleşti
    assert any("birleştiriliyor" in p for p in phases)
    assert any("(1/2)" in p for p in phases) and any("(2/2)" in p for p in phases)
    leftovers = [p.name for p in Path(task.output_dir).iterdir() if p.name != Path(task.output_path).name]
    assert leftovers == []  # geçici parça dosyası kalmadı


@pytest.mark.parametrize("bitrate", [128, 320])
def test_audio_download_to_mp3(qtbot, engine, server, deps, bitrate):
    ctx, manager = engine
    task = wait_done(qtbot, ctx, manager.enqueue(server.url("sample.mp4"), K.AUDIO_DOWNLOAD,
                                                 audio_bitrate=bitrate).id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    info = probe(deps.ffprobe.path, task.output_path)
    assert task.output_path.endswith(".mp3")
    assert [s["codec_name"] for s in info["streams"] if s["codec_type"] == "audio"] == ["mp3"]
    assert abs(int(info["format"]["bit_rate"]) - bitrate * 1000) < bitrate * 1000 * 0.15
    assert ctx.history.list()[0].kind is K.AUDIO_DOWNLOAD


def test_duplicate_titles_get_numbered(qtbot, engine, server):
    ctx, manager = engine
    first = wait_done(qtbot, ctx, manager.enqueue(server.url("sample.mp4"), K.VIDEO_DOWNLOAD).id)
    second = wait_done(qtbot, ctx, manager.enqueue(server.url("sample.mp4"), K.VIDEO_DOWNLOAD).id)
    assert Path(first.output_path).name == "sample.mp4"
    assert Path(second.output_path).name == "sample (2).mp4"


def test_failed_download_and_retry(qtbot, engine, server):
    ctx, manager = engine
    task = wait_done(qtbot, ctx, manager.enqueue(server.url("yok.mp4"), K.VIDEO_DOWNLOAD).id)
    assert task.status is DownloadStatus.FAILED and "404" in task.error and task.error_details
    assert ctx.history.list()[0].status is OperationStatus.FAILED
    assert ctx.notifications.recent[-1].title == "İndirme başarısız"
    assert manager.retry(task.id)
    assert ctx.queue.get(task.id).status is DownloadStatus.RUNNING
    assert wait_done(qtbot, ctx, task.id).status is DownloadStatus.FAILED
    assert not manager.retry(9999)


def test_cancel_running_download_cleans_up(qtbot, engine, server):
    ctx, manager = engine
    task = manager.enqueue(server.url("slow/big.mp4"), K.VIDEO_DOWNLOAD)
    qtbot.waitUntil(lambda: (ctx.queue.get(task.id).progress or 0) > 0.05, timeout=30_000)
    assert manager.cancel(task.id)
    task = wait_done(qtbot, ctx, task.id)
    assert task.status is DownloadStatus.CANCELLED
    assert list(Path(task.output_dir).iterdir()) == []  # yarım dosya kalmadı
    assert ctx.history.list()[0].status is OperationStatus.CANCELLED


def test_concurrency_limit_and_queue_cancel(qtbot, engine, server):
    ctx, manager = engine
    ctx.update_settings(max_concurrent_downloads=1)
    a = manager.enqueue(server.url("slow/big.mp4"), K.VIDEO_DOWNLOAD)
    b = manager.enqueue(server.url("sample.mp4"), K.VIDEO_DOWNLOAD)
    c = manager.enqueue(server.url("sample.mp4"), K.AUDIO_DOWNLOAD)
    assert ctx.queue.get(b.id).status is DownloadStatus.QUEUED and ctx.queue.active_count() == 1
    assert manager.cancel(c.id)  # sıradaki indirme hemen iptal edilir
    assert ctx.queue.get(c.id).status is DownloadStatus.CANCELLED
    max_active = []
    ctx.queue.task_updated.connect(lambda _tid: max_active.append(ctx.queue.active_count()))
    manager.cancel(a.id)
    assert wait_done(qtbot, ctx, b.id).status is DownloadStatus.COMPLETED
    assert max(max_active) <= 1


def test_enqueue_requires_dependencies(engine, server):
    ctx, manager = engine
    ctx.dependencies = type(ctx.dependencies)(ffmpeg=None, ffprobe=None, ytdlp=ctx.dependencies.ytdlp)
    with pytest.raises(DependencyMissingError, match="FFmpeg"):
        manager.enqueue(server.url("sample.mp4"), K.AUDIO_DOWNLOAD)
    with pytest.raises(ys.DownloadFailure):
        manager.enqueue("geçersiz", K.VIDEO_DOWNLOAD)
    assert ctx.queue.tasks() == []
