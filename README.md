# Media Studio

Windows 10/11 için PySide6 tabanlı medya indirme, dönüştürme ve kesme uygulaması.

> Yalnızca indirme hakkınız olan içerikler içindir. DRM, ücretli içerik veya erişim
> kısıtlamalarını aşma desteklenmez ve eklenmeyecektir.

## Durum

| Aşama | Kapsam | Durum |
|---|---|---|
| 1 | Altyapı, premium arayüz, tüm sayfalar, Dashboard, Ayarlar, tema, FFmpeg/FFprobe/yt-dlp kontrolü, bildirim sistemi, kalıcı ayarlar ve geçmiş, EXE paketleme | Tamam |
| 2 | Video İndir + Ses İndir: analiz, kalite seçimi, akış birleştirme, MP3, kuyruk (eşzamanlılık, iptal, yeniden deneme), geçmiş | Tamam |
| 3 | Dönüştürücü: sürükle-bırak, toplu video/ses → MP3 (128–320 kbps), canlı ilerleme, iptal, geçmiş | Tamam |
| 4 | Video Kes: aralık seçici, kare önizleme, kayıpsız (anahtar kare) / kare hassasiyetinde MP4, MP3 | Tamam |

## Klasör yapısı

```
MediaStudio/
  main.py                    Giriş noktası
  requirements.txt           Çalışma bağımlılıkları
  requirements-dev.txt       + PyInstaller, pytest, pytest-qt
  MediaStudio.spec           PyInstaller tek dosya EXE tanımı
  app/
    application.py           Başlatma: log, tema, global hata yakalayıcı, pencere
    models/                  Saf veri modelleri (Qt'siz): ayarlar, geçmiş, indirme, bildirim
    utils/                   Yollar, Türkçe biçimlendirme, loglama, işletim sistemi yardımcıları
    core/                    Arayüzden bağımsız altyapı (yalnızca QtCore)
      settings_manager.py    JSON ayarlar: atomik yazma, bozuk dosya kurtarma
      history_store.py       SQLite geçmiş: arama, filtre, istatistik
      dependencies.py        FFmpeg / FFprobe / yt-dlp bulma ve doğrulama
      notifications.py       Bildirim merkezi (tüm katmanlar buraya bildirir)
      download_queue.py      İndirme kuyruğu durum modeli
      workers.py             QThreadPool arka plan işleri
      context.py             AppContext: paylaşılan servisler + sinyaller
    services/
      ytdlp_support.py       Güvenli yt-dlp seçenekleri, analiz, format seçimi, hata çevirisi, dosya adları
      downloader.py          DownloadJob (tek indirme) + DownloadManager (kuyruk yürütücüsü)
      ffmpeg_runner.py       Ortak FFmpeg işi: canlı ilerleme, iptal, güvenli (geçici dosya) yazma
      converter.py           FFprobe inceleme + FFmpeg MP3 dönüştürme + ConversionManager
      cutter.py              Medya inceleme, anahtar kare bulma, kare önizleme, kesim + CutManager
    ui/
      theme/                 Tasarım belirteçleri, QSS üretici, Lucide ikonlar, canlı tema yöneticisi
      widgets/               Kart, sidebar, toast, anahtar, responsive ızgara, animasyonlu sayfa yığını
      pages/                 Dashboard, modül sayfaları, kuyruk, geçmiş, ayarlar
      main_window.py
  assets/                    Logo, uygulama ikonu (.ico), lisanslar
  downloads/                 Kaynak koddan çalışırken varsayılan kayıt klasörü
  tests/                     pytest + pytest-qt (169 test; internete çıkmaz)
  tools/generate_icon.py     Logodan çok boyutlu .ico üretir
  vendor/ffmpeg/             (isteğe bağlı) EXE'ye gömülecek ffmpeg.exe / ffprobe.exe
```

**Katman kuralı:** `models` ve `utils` Qt'ye bağlı değildir. `core` ve `services` widget import etmez.
Arayüz durumu yalnızca `AppContext` sinyalleriyle dinler. Uzun işler `Worker` ile arka planda çalışır.

**FFmpeg arama sırası:**
1. Ayarlar'da seçilen yol
2. EXE'ye gömülü kopya
3. PATH
4. winget, Chocolatey, Scoop veya `C:\ffmpeg`

Her aday `-version` ile gerçekten çalıştırılarak doğrulanır.

**Veri konumu:** `%APPDATA%\MediaStudio\` (`settings.json`, `history.db`, `logs\`).
Varsayılan kayıt klasörü kaynak koddan çalışırken `downloads/`, EXE'de `İndirilenler\Media Studio`.

## İndirme motoru (Aşama 2)

- **Akış:** bağlantı → *Analiz et* → kalite ve klasör seçimi → "indirme hakkım var" onayı → kuyruk.
  Kuyruk, Ayarlar'daki eşzamanlı indirme sınırına göre yürütülür.
- **Video:** önce MP4/M4A uyumlu akışlar seçilir; görüntü ve ses ayrıysa FFmpeg ile tek bir MP4'te
  birleştirilir. Seçilen çözünürlük kaynakta yoksa altındaki en yakın kalite, o da yoksa mevcut en iyisi indirilir.
- **Ses:** en iyi ses akışı indirilir, seçilen bit hızında (128/192/256/320 kbps) MP3'e dönüştürülür ve etiketlenir.
- **Dosya adları:** başlıktan güvenli bir ad üretilir; çakışmada `Başlık (2)` biçiminde numaralandırılır.
  İptal veya hata durumunda yalnızca o indirmenin oluşturduğu geçici dosyalar silinir.
- **Kapsam ve güvenlik:**
  - Yalnızca tek içerik indirilir. Oynatma listeleri ve canlı yayınlar reddedilir.
  - DRM'li, giriş/üyelik gerektiren veya bölge kısıtlamalı içerikler açık bir mesajla reddedilir.
  - Tarayıcı çerezleri, oturum bilgisi, coğrafi engel aşma ve oynatılamayan formatları zorlama gibi seçenekler hiç kullanılmaz.
  - `ytdlp_support.base_options` bu seçeneklerden biri eklenirse çalışmayı durdurur.
- **Testler:** `tests/media_server.py`, FFmpeg ile sentetik test içerikleri üretip 127.0.0.1'den sunar:
  doğrudan MP4, ayrı akışlı DASH, yavaş akan dosya ve 404. Testler internete çıkmaz.

## Dönüştürücü (Aşama 3)

- **Ekleme:** dosyalar sürükle-bırak (klasör bırakılabilir), "Dosya ekle…" veya "Klasör ekle…" ile eklenir.
  Desteklenmeyen uzantılar ve listede zaten olan dosyalar atlanır; bunun nedeni bildirimle söylenir.
- **İnceleme:** her dosya FFprobe ile incelenir; süre ve boyut gösterilir. Ses akışı olmayan veya bozuk
  dosyalar "Desteklenmiyor" olarak işaretlenir ve başlatılmaz.
- **Dönüştürme:** yalnızca ilk ses akışı libmp3lame ile seçilen bit hızında (128/192/256/320 kbps) MP3'e
  kodlanır; başlık ve sanatçı etiketleri korunur. Aynı anda en fazla 2 dosya işlenir; ilerleme, hız ve
  kalan süre FFmpeg'in `-progress` çıktısından canlı okunur.
- **Güvenli yazma:** çıktı önce `Ad.converting.mp3` geçici dosyasına yazılır ve yalnızca başarıyla biterse
  `Ad.mp3` olur. İptal veya hatada geçici dosya silinir. Kaynak dosyalara asla dokunulmaz; kaynak zaten
  MP3 olsa bile üzerine yazılmaz. Aynı adlı çıktılar, aynı anda başlasalar bile `Ad (2).mp3` diye numaralanır.
- **Çıktı konumu:** kayıt klasörü, kaynak dosyanın yanı (kalıcı ayar) veya o oturum için özel bir klasör.

## Video Kes (Aşama 4)

- **Kaynak:** dosya seçilir veya sürüklenir. FFprobe ile süre, çözünürlük, FPS ve kodekler okunur.
- **Aralık:** iki tutamaçlı kaydırıcı (fare; klavyede ←/→ 0,1 sn, Shift ile 1 sn, Boşluk tutamaç değiştirir)
  ve `ss:dd:sn.ms` zaman kodu alanları birlikte çalışır. Geçersiz girişte açıklayıcı bir mesaj çıkar ve
  mevcut seçim bozulmaz. Başlangıç ve bitiş kareleri FFmpeg ile çıkarılıp önizlenir; bunun için QtMultimedia
  gerekmez, EXE büyümez.
- **MP4 · Kayıpsız:** akışlar yeniden kodlanmadan kopyalanır; kalite kaybı yoktur ve kesim çok hızlıdır.
  Video yalnızca anahtar kareden başlayabildiği için başlangıç, önceki anahtar kareye çekilir. Bu önceden
  açıkça gösterilir (ör. "00:00:12.000 anındaki anahtar kareden başlar, seçimden 0,50 sn önce").
  Başlangıç hiçbir zaman seçilen andan sonraya kaymaz. Kodekler MP4'e kopyalanamıyorsa (ör. VP8/Vorbis)
  bu mod devre dışıdır.
- **MP4 · Kare hassasiyetinde:** tam seçilen kareden başlar; H.264 CRF 18 + AAC 192k ile yeniden kodlanır.
- **MP3:** seçilen aralığın sesi 128–320 kbps MP3 olarak çıkarılır. Yalnızca ses içeren dosyalar da kesilebilir.
- **Dışa aktarımlar:** birden fazla klip sıraya alınabilir (aynı anda biri işlenir). Canlı ilerleme, iptal,
  yeniden deneme ve klasörde gösterme vardır. Çıktı adı `Ad (00.00.12-00.00.31).mp4` biçimindedir; çakışırsa numaralandırılır.

## Geliştirme

```bash
python -m venv C:\venvs\mediastudio
C:\venvs\mediastudio\Scripts\python -m pip install -r requirements-dev.txt
C:\venvs\mediastudio\Scripts\python main.py
C:\venvs\mediastudio\Scripts\python -m pytest -v
```

> **Önemli:** Sanal ortamı kısa bir yola kurun. Qt eklenti yolu (`...\PySide6\plugins\platforms\qwindows.dll`)
> yaklaşık 250 karakteri aşarsa Qt, pencere açılmadan **sessizce donar**; uzun yol desteği açık olsa bile.
> Bu, derin klasörlerde `.venv` oluşturulduğunda görülür. Paketlenmiş EXE bundan etkilenmez,
> çünkü kendini kısa bir `%TEMP%\_MEI…` yoluna açar.

Kısayollar: `Ctrl+1` … `Ctrl+8` sayfalar arasında geçiş, `F5` sayfayı yeniler.

## EXE oluşturma

```bash
C:\venvs\mediastudio\Scripts\python tools\generate_icon.py
C:\venvs\mediastudio\Scripts\python -m PyInstaller --noconfirm --clean MediaStudio.spec
```

Çıktı: `dist\MediaStudio.exe` (yaklaşık 43 MB). FFmpeg'i EXE içine gömmek için önce `vendor\ffmpeg\`
klasörüne `ffmpeg.exe` ve `ffprobe.exe` kopyalayın. Bu durumda FFmpeg'in GPL/LGPL lisans koşullarına uyun.

## Lisanslar

İkonlar [Lucide](https://lucide.dev) setinden alınmıştır (ISC); bkz. `assets/licenses/lucide.txt`.
