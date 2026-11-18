import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Her test kendi geçici veri klasörünü kullanır; gerçek %APPDATA% ayarlarına dokunulmaz."""
    data = tmp_path / "appdata"
    data.mkdir()
    downloads = tmp_path / "downloads"
    monkeypatch.setenv("MEDIASTUDIO_DATA_DIR", str(data))
    monkeypatch.setattr("app.utils.paths.default_download_dir", lambda: downloads)
    monkeypatch.setattr("app.core.settings_manager.default_download_dir", lambda: downloads)
    monkeypatch.setattr("app.ui.pages.settings_page.default_download_dir", lambda: downloads, raising=False)
    return data


@pytest.fixture(scope="session")
def deps():
    from app.core.dependencies import DependencyChecker

    report = DependencyChecker().detect()
    assert report.media_ready and report.ytdlp, "Testler FFmpeg, FFprobe ve yt-dlp gerektirir"
    return report


@pytest.fixture(scope="session")
def server(tmp_path_factory, deps):
    """Yerel test medya sunucusu (internete çıkılmaz)."""
    from tests.media_server import MediaServer, build_media

    root = build_media(tmp_path_factory.mktemp("media"), str(deps.ffmpeg.path))
    with MediaServer(root) as srv:
        yield srv
