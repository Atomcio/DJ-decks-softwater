# DJ Lite

**Ultra-prosty odtwarzacz DJ z dwoma deckami**

Minimalistyczna aplikacja DJ z przezroczystym interfejsem, zawsze na wierzchu, w stylu "nakładka na pulpit".

## ✨ Funkcje

- **Dwa niezależne decki** z kontrolą odtwarzania
- **Crossfader** do miksowania między deckami
- **Regulacja BPM** w czasie rzeczywistym
- **3-pasmowy EQ** (Hi/Mid/Low) dla każdego decka
- **VU Metry** do monitorowania poziomu audio
- **Przeglądarka plików** z automatycznym indeksowaniem
- **Przezroczysty interfejs** zawsze na wierzchu
- **Obsługa formatów**: WAV, MP3, FLAC, OGG

## 🛠️ Wymagania

- **Python 3.10+**
- **System operacyjny**: Windows, Linux, macOS
- **Urządzenie audio** z wyjściem stereo

## 📦 Instalacja

### Automatyczna (zalecana)

**Windows:**
```bash
cd djlite
scripts\run_dev.bat
```

**Linux/macOS:**
```bash
cd djlite
chmod +x scripts/run_dev.sh
./scripts/run_dev.sh
```

### Manualna

1. **Sklonuj/pobierz projekt**
```bash
git clone <repository-url>
cd djlite
```

2. **Utwórz środowisko wirtualne**
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate
```

3. **Zainstaluj zależności**
```bash
pip install -r requirements.txt
```

4. **Uruchom aplikację**
```bash
python app.py
```

## 🎵 Użytkowanie

### Podstawowe sterowanie

1. **Wybór muzyki**:
   - Kliknij "Browse" w sekcji PLAYLIST
   - Wybierz folder z plikami audio
   - Poczekaj na indeksowanie

2. **Ładowanie utworów**:
   - Kliknij dwukrotnie utwór z listy
   - Zostanie załadowany do aktywnego decka
   - Przełączaj między deckami przyciskami A/B

3. **Odtwarzanie**:
   - **Play/Pause**: ▶️/⏸️
   - **BPM**: Suwak do zmiany tempa
   - **Gain**: Głośność decka
   - **EQ**: Hi/Mid/Low dla każdego decka

4. **Miksowanie**:
   - **Crossfader**: Przełączanie między deckami
   - **Master**: Główna głośność wyjścia

### Skróty klawiszowe

- **Spacja**: Play/Pause aktywnego decka
- **A/B**: Przełączanie między deckami
- **Esc**: Zamknij aplikację

### Przeciąganie okna

- Przeciągnij okno lewym przyciskiem myszy
- Okno pozostaje zawsze na wierzchu
- Użyj przycisku ✕ aby zamknąć

## 🏗️ Struktura projektu

```
djlite/
├── app.py                 # Główny plik startowy
├── requirements.txt       # Zależności Python
├── README.md             # Ta dokumentacja
├── audio/                # Moduły audio
│   ├── deck.py           # Logika pojedynczego decka
│   ├── mixer.py          # Miksowanie dwóch decków
│   └── eq.py             # 3-pasmowy equalizer
├── ui/                   # Interfejs użytkownika
│   ├── main_window.py    # Główne okno aplikacji
│   ├── styles.qss        # Style CSS dla Qt
│   └── assets/           # Zasoby graficzne
├── utils/                # Narzędzia pomocnicze
│   └── file_browser.py   # Przeglądarka plików audio
└── scripts/              # Skrypty uruchomieniowe
    ├── run_dev.bat       # Windows
    └── run_dev.sh        # Linux/macOS
```

## 🔧 Technologie

- **GUI**: PySide6 (Qt6)
- **Audio**: sounddevice, soundfile
- **DSP**: numpy, scipy
- **Threading**: Python threading
- **Formaty audio**: WAV, MP3, FLAC, OGG

## ⚙️ Konfiguracja audio

### Windows
- Aplikacja automatycznie wykrywa urządzenia audio
- Zalecane: ASIO4ALL dla niskiej latencji

### Linux
- Wymagane: ALSA lub PulseAudio
- Instalacja: `sudo apt install alsa-utils pulseaudio`

### macOS
- Używa Core Audio (wbudowane)
- Brak dodatkowej konfiguracji

## 🐛 Rozwiązywanie problemów

### Brak dźwięku
1. Sprawdź czy urządzenie audio jest podłączone
2. Sprawdź ustawienia głośności systemu
3. Uruchom ponownie aplikację

### Błędy importu
```bash
# Zainstaluj ponownie zależności
pip install --upgrade -r requirements.txt
```

### Wysokie użycie CPU
- Zmniejsz jakość audio w ustawieniach systemu
- Zamknij inne aplikacje audio

### Aplikacja się nie uruchamia
1. Sprawdź wersję Python: `python --version`
2. Sprawdź czy wszystkie zależności są zainstalowane
3. Uruchom z terminala aby zobaczyć błędy

## 📝 Dziennik zmian

### v1.0.0
- Pierwsza wersja
- Podstawowe funkcje DJ
- Przezroczysty interfejs
- Obsługa dwóch decków
- 3-pasmowy EQ
- Crossfader
- Przeglądarka plików

## 🚀 Przyszłe funkcje

- **Pitch shifting** z Rubber Band Library
- **Efekty audio** (reverb, delay, filter)
- **Nagrywanie mixów**
- **Synchronizacja BPM**
- **Waveform display**
- **Cue points**
- **Loop funkcje**

## 📄 Licencja

Projekt open-source. Używaj i modyfikuj według potrzeb.

## 🤝 Wsparcie

W przypadku problemów:
1. Sprawdź sekcję "Rozwiązywanie problemów"
2. Uruchom aplikację z terminala aby zobaczyć szczegółowe błędy
3. Sprawdź czy wszystkie zależności są zainstalowane

---

**DJ Lite** - Prosty, ale potężny odtwarzacz DJ dla każdego! 🎧