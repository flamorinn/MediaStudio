"""MP3 dönüştürücü motor testleri (FFmpeg ile üretilen gerçek dosyalarla)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.context import AppContext
from app.core.errors import DependencyMissingError
from app.models import ConversionStatus, OperationKind, OperationStatus
from app.services import converter as cv
from app.services.converter import ConversionJob, ConversionManager
from app.utils.files import temp_path_for, unique_output

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ffmpeg_run(ffmpeg, *args):
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *args], check=True,
                   capture_output=True, creationflags=NO_WINDOW)


@pytest.fixture(scope="session")
def media(tmp_path_factory, deps):
    root = tmp_path_factory.mktemp("convert_src")
    f = deps.ffmpeg.path
    av = ["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100"]
    ffmpeg_run(f, *av, "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-metadata", "title=Deneme Başlık", "-metadata", "artist=Media Studio", "-shortest", root / "clip.mp4")
    ffmpeg_run(f, *av, "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "libvorbis", "-shortest",
               root / "clip.mkv")
    ffmpeg_run(f, "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25", "-t", "3", "-c:v", "libx264",
               "-preset", "ultrafast", root / "sessiz.mp4")
    ffmpeg_run(f, "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=22050", "-t", "4", root / "kayit.wav")
    shutil.copy2(root / "clip.mp4", root / "Şarkı ğüşiöç.mp4")
    (root / "bozuk.mp4").write_bytes(os.urandom(4096))
    (root / "notlar.txt").write_text("metin")
    return root


@pytest.fixture
def work(media, tmp_path):
    """Her test kaynak dosyaların kendi kopyasıyla çalışır."""
    target = tmp_path / "kaynak"
    shutil.copytree(media, target)
    return target


@pytest.fixture
def conv(qapp, deps, tmp_path):
    ctx = AppContext()
    ctx.dependencies = deps
    ctx.update_settings(download_dir=str(tmp_path / "cikti"))
    manager = ConversionManager(ctx)
    ctx.conversions = manager
    yield ctx, manager
    ConversionJob.extra_input_args = ()
    manager.shutdown()
    ctx.history.close()


def probe(ffprobe, path):
    out = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", check=True, creationflags=NO_WINDOW).stdout
    return json.loads(out)


def wait_probed(qtbot, manager, items):
    qtbot.waitUntil(lambda: all(manager.items[i.id].status is not ConversionStatus.PROBING for i in items),
                    timeout=30_000)


def wait_idle(qtbot, manager, timeout=60_000):
    qtbot.waitUntil(lambda: not manager.has_active(), timeout=timeout)


# ============================================================ yardımcılar
def test_unique_output_and_temp(tmp_path):
    assert unique_output(tmp_path, "a", "mp3") == tmp_path / "a.mp3"
    (tmp_path / "a.mp3").write_text("x")
    assert unique_output(tmp_path, "a", "mp3") == tmp_path / "a (2).mp3"
    (tmp_path / "a (2).converting.mp3").write_text("yarım")  # başka bir dönüştürmenin geçici dosyası
    assert unique_output(tmp_path, "a", "mp3") == tmp_path / "a (3).mp3"
    assert unique_output(tmp_path, "a", "mp3", {str(tmp_path / "a (3).mp3")}) == tmp_path / "a (4).mp3"
    assert temp_path_for(tmp_path / "x.mp3").name == "x.converting.mp3"


def test_supported_extensions():
    assert cv.is_supported("a.MKV") and cv.is_supported("b.wav") and not cv.is_supported("c.txt")


def test_command_maps_only_audio_and_keeps_tags():
    cmd = cv.build_command("ffmpeg", "in.mp4", "out.mp3", 256)
    assert cmd[cmd.index("-b:a") + 1] == "256k" and cmd[cmd.index("-map") + 1] == "0:a:0"
    assert "-vn" in cmd and "-map_metadata" in cmd and cmd[-1] == "out.mp3"


@pytest.mark.parametrize("text, expected", [
    ("Stream map '0:a:0' matches no streams.", "ses akışı yok"),
    ("bozuk.mp4: Invalid data found when processing input", "bozuk"),
    ("Permission denied", "yazılamıyor"),
    ("garip", "başarısız"),
])
def test_friendly_errors(text, expected):
    assert expected in cv.friendly_error(text)


def test_probe(deps, media):
    info = cv.probe(str(deps.ffprobe.path), str(media / "clip.mp4"))
    assert info.has_audio and info.has_video and 5.5 < info.duration < 6.5
    assert not cv.probe(str(deps.ffprobe.path), str(media / "sessiz.mp4")).has_audio
    assert not cv.probe(str(deps.ffprobe.path), str(media / "kayit.wav")).has_video
    with pytest.raises(cv.ConversionFailure):
        cv.probe(str(deps.ffprobe.path), str(media / "bozuk.mp4"))


# ============================================================ ekleme ve inceleme
def test_add_files_probes_and_filters(qtbot, conv, work):
    ctx, manager = conv
    added, skipped = manager.add_files([work / "clip.mp4", work / "sessiz.mp4", work / "bozuk.mp4",
                                        work / "notlar.txt", work / "clip.mp4", work])
    assert len(added) == 3
    assert sorted(r for _p, r in skipped) == ["desteklenmeyen biçim", "klasör", "zaten listede"]
    wait_probed(qtbot, manager, added)
    clip, silent, broken = (manager.items[i.id] for i in added)
    assert clip.status is ConversionStatus.READY and 5.5 < clip.duration < 6.5 and clip.has_video
    assert silent.status is ConversionStatus.INVALID and "ses akışı yok" in silent.error
    assert broken.status is ConversionStatus.INVALID and broken.error_details


def test_add_folder(qtbot, conv, work):
    _ctx, manager = conv
    added, _ = manager.add_folder(work)
    assert sorted(Path(i.source).name for i in added) == sorted(
        ["bozuk.mp4", "clip.mkv", "clip.mp4", "kayit.wav", "sessiz.mp4", "Şarkı ğüşiöç.mp4"])
    wait_probed(qtbot, manager, added)


# ============================================================ dönüştürme
@pytest.mark.parametrize("bitrate", [128, 192, 256, 320])
def test_convert_bitrates(qtbot, conv, work, deps, bitrate):
    ctx, manager = conv
    added, _ = manager.add_files([work / "clip.mp4"])
    wait_probed(qtbot, manager, added)
    progress = []
    manager.item_updated.connect(lambda i: progress.append(manager.items[i].progress))
    assert manager.start(bitrate) == 1
    wait_idle(qtbot, manager)
    item = manager.items[added[0].id]
    assert item.status is ConversionStatus.COMPLETED, item.error_details
    out = Path(item.output_path)
    assert out.name == "clip.mp3" and out.parent == Path(ctx.settings.download_dir)
    info = probe(deps.ffprobe.path, out)
    assert [s["codec_name"] for s in info["streams"]] == ["mp3"]  # görüntü yok, yalnızca MP3 ses
    assert abs(int(info["format"]["bit_rate"]) - bitrate * 1000) < bitrate * 1000 * 0.1
    assert info["format"]["tags"].get("title") == "Deneme Başlık"  # etiketler korundu
    assert 5.5 < float(info["format"]["duration"]) < 6.6
    assert progress[-1] == 1.0
    assert not list(out.parent.glob("*.converting.*"))


def test_batch_mixed_with_history(qtbot, conv, work):
    ctx, manager = conv
    added, _ = manager.add_files([work / n for n in ("clip.mp4", "clip.mkv", "kayit.wav", "Şarkı ğüşiöç.mp4",
                                                     "sessiz.mp4")])
    wait_probed(qtbot, manager, added)
    assert manager.start(192) == 4  # sessiz video başlatılmaz
    wait_idle(qtbot, manager)
    outs = sorted(Path(manager.items[i.id].output_path).name for i in added
                  if manager.items[i.id].status is ConversionStatus.COMPLETED)
    # clip.mp4 ve clip.mkv aynı ada sahip: ikincisi numaralandırılır (aynı anda başlasalar bile)
    assert outs == sorted(["Şarkı ğüşiöç.mp3", "clip (2).mp3", "clip.mp3", "kayit.mp3"])
    entries = ctx.history.list()
    assert len(entries) == 4 and all(e.kind is OperationKind.CONVERT and e.status is OperationStatus.SUCCESS
                                     for e in entries)
    assert ctx.notifications.recent[-1].title == "Dönüştürme tamamlandı"
    assert "4 dosya" in ctx.notifications.recent[-1].message


def test_output_next_to_source_and_custom_dir(qtbot, conv, work, tmp_path):
    ctx, manager = conv
    added, _ = manager.add_files([work / "kayit.wav"])
    wait_probed(qtbot, manager, added)
    manager.start(128, mode="source")
    wait_idle(qtbot, manager)
    assert Path(manager.items[added[0].id].output_path) == work / "kayit.mp3"
    manager.retry(added[0].id)  # tamamlanmış öğe yeniden denenemez
    assert manager.items[added[0].id].status is ConversionStatus.COMPLETED
    manager.clear(finished_only=True)
    added, _ = manager.add_files([work / "kayit.wav"])
    wait_probed(qtbot, manager, added)
    manager.start(128, custom_dir=str(tmp_path / "özel"))
    wait_idle(qtbot, manager)
    assert Path(manager.items[added[0].id].output_path) == tmp_path / "özel" / "kayit.mp3"


def test_mp3_source_is_not_overwritten(qtbot, conv, work):
    ctx, manager = conv
    first, _ = manager.add_files([work / "kayit.wav"])
    wait_probed(qtbot, manager, first)
    manager.start(128, mode="source")
    wait_idle(qtbot, manager)
    original = (work / "kayit.mp3").read_bytes()
    again, _ = manager.add_files([work / "kayit.mp3"])
    wait_probed(qtbot, manager, again)
    manager.start(320, mode="source", item_ids=[again[0].id])
    wait_idle(qtbot, manager)
    assert Path(manager.items[again[0].id].output_path).name == "kayit (2).mp3"
    assert (work / "kayit.mp3").read_bytes() == original


def test_cancel_running_conversion(qtbot, conv, work):
    ctx, manager = conv
    ConversionJob.extra_input_args = ("-re",)  # gerçek zamanlı okuma: dönüştürme ~6 sn sürer
    added, _ = manager.add_files([work / "clip.mp4"])
    wait_probed(qtbot, manager, added)
    manager.start(192)
    qtbot.waitUntil(lambda: manager.items[added[0].id].progress > 0.05, timeout=20_000)
    item = manager.items[added[0].id]
    assert item.status is ConversionStatus.CONVERTING and item.speed
    assert manager.cancel(item.id)
    wait_idle(qtbot, manager)
    assert item.status is ConversionStatus.CANCELLED
    assert list(Path(ctx.settings.download_dir).iterdir()) == []  # geçici dosya silindi
    assert ctx.history.list()[0].status is OperationStatus.CANCELLED
    ConversionJob.extra_input_args = ()
    assert manager.retry(item.id)
    wait_idle(qtbot, manager)
    assert item.status is ConversionStatus.COMPLETED


def test_parallel_limit_and_queue_cancel(qtbot, conv, work):
    ctx, manager = conv
    ConversionJob.extra_input_args = ("-re",)
    added, _ = manager.add_files([work / n for n in ("clip.mp4", "clip.mkv", "kayit.wav")])
    wait_probed(qtbot, manager, added)
    manager.start(128)
    states = [manager.items[i.id].status for i in added]
    assert states.count(ConversionStatus.CONVERTING) == cv.MAX_PARALLEL == 2
    assert states[2] is ConversionStatus.QUEUED
    assert manager.cancel(added[2].id) and manager.items[added[2].id].status is ConversionStatus.CANCELLED
    assert not manager.remove(added[0].id)  # çalışan öğe kaldırılamaz
    assert manager.cancel_all() == 2
    wait_idle(qtbot, manager)
    assert all(manager.items[i.id].status is ConversionStatus.CANCELLED for i in added)
    assert manager.clear() == 3 and manager.list() == []


def test_failure_during_conversion(qtbot, conv, work):
    ctx, manager = conv
    added, _ = manager.add_files([work / "clip.mp4"])
    wait_probed(qtbot, manager, added)
    (work / "clip.mp4").unlink()  # kaynak, inceleme sonrası silindi
    manager.start(192)
    wait_idle(qtbot, manager)
    item = manager.items[added[0].id]
    assert item.status is ConversionStatus.FAILED and "bulunamadı" in item.error and item.error_details
    assert ctx.history.list()[0].status is OperationStatus.FAILED
    assert ctx.notifications.recent[-1].title == "Dönüştürme başarısız"


def test_requires_ffmpeg(conv, work):
    ctx, manager = conv
    ctx.dependencies = type(ctx.dependencies)(ffmpeg=None, ffprobe=None, ytdlp=ctx.dependencies.ytdlp)
    with pytest.raises(DependencyMissingError, match="FFmpeg"):
        manager.add_files([work / "clip.mp4"])
