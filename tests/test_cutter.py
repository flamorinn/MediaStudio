"""Video kesme motoru testleri (FFmpeg ile üretilen, anahtar kareleri bilinen dosyalarla)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.context import AppContext
from app.core.errors import DependencyMissingError
from app.models import OperationKind, OperationStatus
from app.models.cut import CutFormat, CutMode, CutStatus
from app.services import cutter as ct
from app.services.cutter import CutJob, CutManager
from app.utils.timecode import filename_timecode, format_timecode, parse_timecode

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ff(ffmpeg, *args):
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *args], check=True,
                   capture_output=True, creationflags=NO_WINDOW)


@pytest.fixture(scope="session")
def cut_media(tmp_path_factory, deps):
    root = tmp_path_factory.mktemp("cut_src")
    f = deps.ffmpeg.path
    av = ["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    # 20 sn, anahtar kare tam 2 sn'de bir (0, 2, 4, ...)
    ff(f, *av, "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
       "-g", "50", "-keyint_min", "50", "-sc_threshold", "0", "-c:a", "aac", "-metadata", "title=Kesim Testi",
       "-shortest", root / "film.mp4")
    ff(f, *av, "-t", "10", "-c:v", "libvpx", "-b:v", "300k", "-c:a", "libvorbis", "-shortest", root / "klip.webm")
    ff(f, "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=44100", "-t", "10", root / "ses.wav")
    ff(f, "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast",
       "-g", "25", root / "sessiz.mp4")
    return root


@pytest.fixture
def cutm(qapp, deps, tmp_path):
    ctx = AppContext()
    ctx.dependencies = deps
    ctx.update_settings(download_dir=str(tmp_path / "cikti"))
    manager = CutManager(ctx)
    ctx.cuts = manager
    yield ctx, manager
    CutJob.extra_input_args = ()
    manager.shutdown()
    ctx.history.close()


def info(ffprobe, path):
    out = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", check=True, creationflags=NO_WINDOW).stdout
    return json.loads(out)


def details(deps, path):
    return ct.probe_details(str(deps.ffprobe.path), str(path))


def run_cut(qtbot, manager, *args, **kwargs):
    item = manager.export(*args, **kwargs)
    qtbot.waitUntil(lambda: not manager.items[item.id].status.is_active, timeout=60_000)
    return manager.items[item.id]


# ============================================================ zaman kodu
@pytest.mark.parametrize("text, seconds", [
    ("83", 83), ("83,25", 83.25), ("1:23", 83), ("01:23.5", 83.5), ("1:02:03.250", 3723.25), ("0:00:00", 0),
])
def test_parse_timecode(text, seconds):
    assert parse_timecode(text) == pytest.approx(seconds)


@pytest.mark.parametrize("bad", ["", "abc", "1:75", "1:61:00", "-5", "1::2"])
def test_parse_timecode_rejects(bad):
    with pytest.raises(ValueError):
        parse_timecode(bad)


def test_format_timecode():
    assert format_timecode(3723.25) == "01:02:03.250"
    assert format_timecode(59.9996) == "00:01:00.000"
    assert format_timecode(5, millis=False) == "00:00:05"
    assert filename_timecode(83) == "00.01.23"


# ============================================================ inceleme
def test_probe_details(deps, cut_media):
    film = details(deps, cut_media / "film.mp4")
    assert film.has_video and film.has_audio and film.video_codec == "h264" and film.audio_codec == "aac"
    assert (film.width, film.height) == (320, 180) and film.fps == pytest.approx(25)
    assert 19.9 < film.duration < 20.2 and film.lossless_mp4_possible
    webm = details(deps, cut_media / "klip.webm")
    assert webm.video_codec == "vp8" and not webm.lossless_mp4_possible  # VP8/Vorbis MP4'e kopyalanamaz
    wav = details(deps, cut_media / "ses.wav")
    assert not wav.has_video and wav.has_audio and not wav.lossless_mp4_possible
    with pytest.raises(ct.CutFailure):
        ct.probe_details(str(deps.ffprobe.path), str(cut_media / "yok.mp4"))


@pytest.mark.parametrize("t, expected", [(0, 0), (1.9, 0), (2.0, 2), (5.0, 4), (9.99, 8), (9.9995, 10), (19.5, 18)])
def test_keyframe_before(deps, cut_media, t, expected):
    assert ct.keyframe_before(str(deps.ffprobe.path), str(cut_media / "film.mp4"), t) == pytest.approx(expected, abs=0.02)


def test_extract_frame(deps, cut_media):
    png = ct.extract_frame(str(deps.ffmpeg.path), str(cut_media / "film.mp4"), 3.0, width=200)
    assert png.startswith(b"\x89PNG")
    assert ct.extract_frame(str(deps.ffmpeg.path), str(cut_media / "ses.wav"), 1.0) == b""  # görüntü yok


def test_validate_range():
    ct.validate_range(1, 2, 10)
    for start, end in ((-1, 2), (5, 5.05), (6, 5), (1, 10.5)):
        with pytest.raises(ct.CutFailure):
            ct.validate_range(start, end, 10)


# ============================================================ kesme
def test_lossless_cut_snaps_to_keyframe(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    item = run_cut(qtbot, manager, d, 5.0, 9.0, CutFormat.MP4, mode=CutMode.LOSSLESS,
                   output_dir=ctx.settings.download_dir)
    assert item.status is CutStatus.COMPLETED, item.error_details
    assert item.effective_start == pytest.approx(4.0, abs=0.02)
    out = Path(item.output_path)
    assert out.name == "film (00.00.05-00.00.09).mp4"
    meta = info(deps.ffprobe.path, out)
    assert {s["codec_name"] for s in meta["streams"]} == {"h264", "aac"}   # yeniden kodlanmadı
    assert float(meta["format"]["duration"]) == pytest.approx(5.0, abs=0.15)  # 4 sn'deki anahtar kareden 9 sn'ye
    assert meta["format"]["tags"].get("title") == "Kesim Testi"


def test_lossless_cut_on_keyframe_is_exact(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    item = run_cut(qtbot, manager, d, 6.0, 10.0, CutFormat.MP4, output_dir=ctx.settings.download_dir)
    assert float(info(deps.ffprobe.path, item.output_path)["format"]["duration"]) == pytest.approx(4.0, abs=0.15)


def test_precise_cut_is_frame_accurate(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    item = run_cut(qtbot, manager, d, 5.0, 9.0, CutFormat.MP4, mode=CutMode.PRECISE,
                   output_dir=ctx.settings.download_dir)
    assert item.status is CutStatus.COMPLETED, item.error_details
    meta = info(deps.ffprobe.path, item.output_path)
    assert float(meta["format"]["duration"]) == pytest.approx(4.0, abs=0.1)
    video = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"


@pytest.mark.parametrize("bitrate", [128, 320])
def test_mp3_cut(qtbot, cutm, cut_media, deps, bitrate):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    item = run_cut(qtbot, manager, d, 3.0, 8.5, CutFormat.MP3, bitrate=bitrate, output_dir=ctx.settings.download_dir)
    meta = info(deps.ffprobe.path, item.output_path)
    assert [s["codec_name"] for s in meta["streams"]] == ["mp3"]
    assert float(meta["format"]["duration"]) == pytest.approx(5.5, abs=0.1)
    assert abs(int(meta["format"]["bit_rate"]) - bitrate * 1000) < bitrate * 1000 * 0.12
    assert item.output_path.endswith("film (00.00.03-00.00.08).mp3")


def test_audio_only_and_video_only_sources(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    wav = details(deps, cut_media / "ses.wav")
    with pytest.raises(ct.CutFailure, match="görüntü akışı yok"):
        manager.export(wav, 1, 3, CutFormat.MP4, output_dir=ctx.settings.download_dir)
    assert run_cut(qtbot, manager, wav, 1, 3, CutFormat.MP3, output_dir=ctx.settings.download_dir).status \
        is CutStatus.COMPLETED
    silent = details(deps, cut_media / "sessiz.mp4")
    with pytest.raises(ct.CutFailure, match="ses akışı yok"):
        manager.export(silent, 1, 3, CutFormat.MP3, output_dir=ctx.settings.download_dir)
    item = run_cut(qtbot, manager, silent, 1, 3, CutFormat.MP4, output_dir=ctx.settings.download_dir)
    assert item.status is CutStatus.COMPLETED  # sesi olmayan video da kesilebilir


def test_incompatible_codecs_require_precise(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    webm = details(deps, cut_media / "klip.webm")
    with pytest.raises(ct.CutFailure, match="Kare hassasiyetinde"):
        manager.export(webm, 1, 4, CutFormat.MP4, mode=CutMode.LOSSLESS, output_dir=ctx.settings.download_dir)
    item = run_cut(qtbot, manager, webm, 1, 4, CutFormat.MP4, mode=CutMode.PRECISE,
                   output_dir=ctx.settings.download_dir)
    assert item.status is CutStatus.COMPLETED, item.error_details
    meta = info(deps.ffprobe.path, item.output_path)
    assert {s["codec_name"] for s in meta["streams"]} == {"h264", "aac"}


def test_invalid_range_rejected(cutm, cut_media, deps):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    for start, end in ((5, 5), (8, 4), (0, 25)):
        with pytest.raises(ct.CutFailure):
            manager.export(d, start, end, CutFormat.MP4, output_dir=ctx.settings.download_dir)
    assert manager.list() == []


def test_multiple_clips_numbered_and_history(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    a = manager.export(d, 2, 4, CutFormat.MP4, output_dir=ctx.settings.download_dir)
    b = manager.export(d, 2, 4, CutFormat.MP4, output_dir=ctx.settings.download_dir)
    assert manager.items[b.id].status is CutStatus.QUEUED  # aynı anda tek kesim
    qtbot.waitUntil(lambda: not manager.has_active(), timeout=60_000)
    names = sorted(Path(manager.items[i.id].output_path).name for i in (a, b))
    assert names == ["film (00.00.02-00.00.04) (2).mp4", "film (00.00.02-00.00.04).mp4"]
    entries = ctx.history.list()
    assert len(entries) == 2 and all(e.kind is OperationKind.CUT and e.status is OperationStatus.SUCCESS for e in entries)
    assert "[00:00:02–00:00:04]" in entries[0].title
    assert ctx.notifications.recent[-1].title == "Kesim tamamlandı"


def test_cancel_and_retry(qtbot, cutm, cut_media, deps):
    ctx, manager = cutm
    CutJob.extra_input_args = ("-re",)  # gerçek zamanlı: kesim saniyeler sürer
    d = details(deps, cut_media / "film.mp4")
    item = manager.export(d, 0, 12, CutFormat.MP4, mode=CutMode.PRECISE, output_dir=ctx.settings.download_dir)
    queued = manager.export(d, 0, 2, CutFormat.MP3, output_dir=ctx.settings.download_dir)
    qtbot.waitUntil(lambda: manager.items[item.id].progress > 0.05, timeout=20_000)
    assert manager.cancel(queued.id) and manager.items[queued.id].status is CutStatus.CANCELLED
    assert not manager.remove(item.id)  # çalışan iş kaldırılamaz
    assert manager.cancel(item.id)
    qtbot.waitUntil(lambda: not manager.has_active(), timeout=20_000)
    assert manager.items[item.id].status is CutStatus.CANCELLED
    assert list(Path(ctx.settings.download_dir).iterdir()) == []  # yarım dosya kalmadı
    CutJob.extra_input_args = ()
    assert manager.retry(item.id)
    qtbot.waitUntil(lambda: not manager.has_active(), timeout=60_000)
    assert manager.items[item.id].status is CutStatus.COMPLETED
    assert manager.clear() == 2 and manager.list() == []


def test_failure_when_source_removed(qtbot, cutm, cut_media, deps, tmp_path):
    ctx, manager = cutm
    copy = tmp_path / "gecici.mp4"
    shutil.copy2(cut_media / "film.mp4", copy)
    d = details(deps, copy)
    copy.unlink()
    item = run_cut(qtbot, manager, d, 1, 3, CutFormat.MP4, mode=CutMode.PRECISE, output_dir=ctx.settings.download_dir)
    assert item.status is CutStatus.FAILED and "bulunamadı" in item.error
    assert ctx.history.list()[0].status is OperationStatus.FAILED


def test_requires_ffmpeg(cutm, cut_media, deps):
    ctx, manager = cutm
    d = details(deps, cut_media / "film.mp4")
    ctx.dependencies = type(ctx.dependencies)(ffmpeg=None, ffprobe=None, ytdlp=ctx.dependencies.ytdlp)
    with pytest.raises(DependencyMissingError):
        manager.export(d, 1, 2, CutFormat.MP4, output_dir=ctx.settings.download_dir)
