"""İş mantığı servisleri.

Mevcut modüller:
    ytdlp_support.py  - Güvenli yt-dlp seçenekleri, analiz, format seçimi, hata çevirisi, dosya adları
    downloader.py     - Aşama 2: DownloadJob + DownloadManager (kuyruk yürütücüsü)
    converter.py      - Aşama 3: FFprobe inceleme + FFmpeg ile toplu MP3 dönüştürme
    cutter.py         - Aşama 4: aralık kesme (kayıpsız akış kopyalama / kare hassasiyetinde / MP3)
    ffmpeg_runner.py  - Dönüştürücü ve kesicinin ortak FFmpeg iş sınıfı

Kural: servisler arayüzü import etmez; ilerlemeyi sinyallerle/kuyruk modeliyle bildirir,
sonuçları HistoryStore'a yazar, hataları MediaStudioError olarak yükseltir.
"""
