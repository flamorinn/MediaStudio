"""Zaman aralığı indirme ve analiz bilgisinin yeniden kullanımı (yerel test sunucusuyla, internetsiz)."""

import dataclasses
import json
import subprocess
import time
from pathlib import Path

import pytest

from app.core.context import AppContext
from app.models import DownloadStatus, OperationKind
from app.services import ytdlp_support as ys
from app.services.downloader import DownloadManager
from app.utils.timecode import parse_flexible_timecode

K = OperationKind
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def probe(deps, path):
    out = subprocess.run([str(deps.ffprobe.path), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True, creationflags=NO_WINDOW).stdout
    return json.loads(out)


# ============================================================ zaman kodu
@pytest.mark.parametrize("text, seconds", [
    ("5.37", 337), ("6,48", 408), ("1.05.37", 3937), ("5:37", 337), ("337", 337), ("00:05:37.250", 337.25),
    ("0.02", 2), ("12.5", 12.5),
])
def test_flexible_timecode(text, seconds):
    assert parse_flexible_timecode(text) == pytest.approx(seconds)


@pytest.mark.parametrize("bad", ["5.75", "abc", "", "1:75"])
def test_flexible_timecode_rejects(bad):
    with pytest.raises(ValueError):
        parse_flexible_timecode(bad)


# ============================================================ yardımcılar
def test_range_prefers_segmented_streams():
    video = ys.format_selector(K.VIDEO_DOWNLOAD, "1080", ranged=True)
    assert video.startswith("bv*[protocol^=m3u8][vcodec^=avc1][height<=1080]+ba")
    assert video.endswith(ys.format_selector(K.VIDEO_DOWNLOAD, "1080"))  # HLS yoksa normal seçici
    assert ys.format_selector(K.AUDIO_DOWNLOAD, ranged=True) == "ba[protocol^=m3u8]/ba/b"
    assert "m3u8" not in ys.format_selector(K.VIDEO_DOWNLOAD, "best")  # tam indirmede değişiklik yok


def test_validate_section():
    ys.validate_section(10, 20, 60)
    ys.validate_section(10, 20, None)  # süre bilinmiyorsa üst sınır denetlenmez
    for start, end in ((-1, 5), (5, 5.5), (8, 4), (10, 70)):
        with pytest.raises(ys.DownloadFailure):
            ys.validate_section(start, end, 60)


def test_expected_section_bytes():
    info = {"requested_formats": [{"tbr": 2000}, {"tbr": 128}]}
    assert ys.expected_section_bytes(info, 10, 20) == int(2128 * 1000 / 8 * 10)
    assert ys.expected_section_bytes({}, 0, 10) == 0


def test_expected_bytes_uses_average_for_hls():
    """HLS tepe bit hızı yerine aynı çözünürlükteki normal akışın ortalaması kullanılır."""
    hls = {"format_id": "312", "protocol": "m3u8_native", "height": 1080, "vcodec": "avc1.64002A", "tbr": 7418}
    dash = {"format_id": "299", "protocol": "https", "height": 1080, "vcodec": "avc1.64002a", "tbr": 3248}
    audio = {"format_id": "251", "protocol": "https", "vcodec": "none", "tbr": 128}
    info = {"requested_formats": [hls, audio], "formats": [hls, dash, audio]}
    assert ys.expected_section_bytes(info, 0, 71) == int((3248 + 128) * 1000 / 8 * 71)
    lone = {"requested_formats": [hls], "formats": [hls]}  # eşdeğer yoksa tepe değerin yarısı
    assert ys.expected_section_bytes(lone, 0, 10) == int(7418 * 0.5 * 1000 / 8 * 10)


# ============================================================ aralık indirme
def test_video_range_from_direct_file(qtbot, engine, server, deps):
    ctx, manager = engine
    media = ys.analyze(server.url("sample.mp4"))
    task = wait_done(qtbot, ctx, manager.enqueue(media.url, K.VIDEO_DOWNLOAD, section=(1.5, 4.5), media=media).id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    out = Path(task.output_path)
    assert out.name == "sample (00.00.01-00.00.04).mp4"
    meta = probe(deps, out)
    assert float(meta["format"]["duration"]) == pytest.approx(3.0, abs=0.15)
    assert {s["codec_type"] for s in meta["streams"]} == {"video", "audio"}
    assert "[00:00:01–00:00:04]" in ctx.history.list()[0].title
    assert [p.name for p in out.parent.iterdir()] == [out.name]  # geçici parça kalmadı


def test_video_range_merges_separate_streams(qtbot, engine, server, deps):
    ctx, manager = engine
    media = ys.analyze(server.url("dash/manifest.mpd"))
    task = wait_done(qtbot, ctx, manager.enqueue(media.url, K.VIDEO_DOWNLOAD, section=(1.0, 5.0), media=media).id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    meta = probe(deps, task.output_path)
    assert {s["codec_type"] for s in meta["streams"]} == {"video", "audio"}
    assert float(meta["format"]["duration"]) == pytest.approx(4.0, abs=0.2)


def test_audio_range_to_mp3(qtbot, engine, server, deps):
    ctx, manager = engine
    media = ys.analyze(server.url("sample.mp4"))
    task = wait_done(qtbot, ctx, manager.enqueue(media.url, K.AUDIO_DOWNLOAD, section=(2.0, 5.0), media=media,
                                                 audio_bitrate=256).id)
    assert task.status is DownloadStatus.COMPLETED, task.error_details
    meta = probe(deps, task.output_path)
    assert [s["codec_name"] for s in meta["streams"]] == ["mp3"]
    assert float(meta["format"]["duration"]) == pytest.approx(3.0, abs=0.15)
    assert task.output_path.endswith("sample (00.00.02-00.00.05).mp3")


def test_analysis_is_reused_not_repeated(qtbot, engine, server, monkeypatch):
    """Analiz edilmiş bağlantı indirilirken yt-dlp bilgiyi yeniden çekmemeli."""
    import yt_dlp

    ctx, manager = engine
    media = ys.analyze(server.url("sample.mp4"))
    assert media.raw is not None and media.analyzed_at > 0
    calls = []
    original = yt_dlp.YoutubeDL.extract_info
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info",
                        lambda self, *a, **k: calls.append(a) or original(self, *a, **k))
    task = wait_done(qtbot, ctx, manager.enqueue(media.url, K.VIDEO_DOWNLOAD, media=media).id)
    assert task.status is DownloadStatus.COMPLETED and calls == []
    # Analiz bilgisi yoksa (ör. eski veya farklı bağlantı) bilgi bir kez çekilir
    task = wait_done(qtbot, ctx, manager.enqueue(media.url, K.VIDEO_DOWNLOAD).id)
    assert task.status is DownloadStatus.COMPLETED and len(calls) == 1


def test_stale_analysis_is_refreshed(qtbot, engine, server, monkeypatch):
    import yt_dlp

    ctx, manager = engine
    media = ys.analyze(server.url("sample.mp4"))
    object.__setattr__(media, "analyzed_at", time.time() - ys.PREFETCH_MAX_AGE - 5)
    calls = []
    original = yt_dlp.YoutubeDL.extract_info
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info",
                        lambda self, *a, **k: calls.append(a) or original(self, *a, **k))
    task = wait_done(qtbot, ctx, manager.enqueue(media.url, K.VIDEO_DOWNLOAD, media=media).id)
    assert task.status is DownloadStatus.COMPLETED and len(calls) == 1


def test_invalid_section_rejected_at_enqueue(engine, server):
    ctx, manager = engine
    media = dataclasses.replace(ys.analyze(server.url("sample.mp4")), duration=6.0)  # süresi bilinen içerik
    for section in ((4, 3), (1, 1.5), (2, 60)):
        with pytest.raises(ys.DownloadFailure):
            manager.enqueue(media.url, K.VIDEO_DOWNLOAD, section=section, media=media)
    assert ctx.queue.tasks() == []


def test_cancel_range_download_stops_ffmpeg(qtbot, engine, server, monkeypatch):
    """Aralık indirmede ilerleme kancası yoktur; iptal FFmpeg sürecini durdurarak hemen çalışmalı."""
    from tests import media_server

    monkeypatch.setattr(media_server._Handler, "slow_chunk", 4 * 1024)   # ~20 KB/sn: indirme saniyeler sürer
    monkeypatch.setattr(media_server._Handler, "slow_delay", 0.2)
    ctx, manager = engine
    media = ys.analyze(server.url("slow/big.mp4"))
    task = manager.enqueue(media.url, K.VIDEO_DOWNLOAD, section=(1.0, 4.0), media=media)
    qtbot.waitUntil(lambda: ctx.queue.get(task.id).phase.startswith("Aralık"), timeout=20_000)
    qtbot.wait(800)
    t0 = time.monotonic()
    assert manager.cancel(task.id)
    task = wait_done(qtbot, ctx, task.id, timeout=10_000)
    assert task.status is DownloadStatus.CANCELLED
    assert time.monotonic() - t0 < 5
    assert list(Path(task.output_dir).iterdir()) == []
