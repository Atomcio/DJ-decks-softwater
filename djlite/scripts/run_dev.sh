#!/bin/bash
# DJ Lite - Skrypt uruchomieniowy dla Linux/macOS
# Uruchamia aplikację DJ Lite w trybie deweloperskim

set -e  # Zatrzymaj przy błędzie

echo "======================================="
echo "DJ Lite - Ultra-prosty odtwarzacz DJ"
echo "======================================="
echo

# Przejdź do katalogu głównego projektu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Katalog projektu: $PROJECT_DIR"
echo

# Sprawdź czy Python jest zainstalowany
if ! command -v python3 &> /dev/null; then
    echo "BŁĄD: Python 3 nie jest zainstalowany"
    echo "Zainstaluj Python 3.10+ używając menedżera pakietów systemu"
    echo "Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "macOS: brew install python3"
    exit 1
fi

# Sprawdź wersję Pythona
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Wersja Python: $PYTHON_VERSION"

if [[ "$(printf '%s\n' "3.10" "$PYTHON_VERSION" | sort -V | head -n1)" != "3.10" ]]; then
    echo "OSTRZEŻENIE: Zalecana wersja Python 3.10+, masz $PYTHON_VERSION"
fi

# Sprawdź czy istnieje plik requirements.txt
if [[ ! -f "requirements.txt" ]]; then
    echo "BŁĄD: Nie znaleziono pliku requirements.txt"
    echo "Upewnij się, że uruchamiasz skrypt z katalogu projektu"
    exit 1
fi

# Sprawdź czy istnieje środowisko wirtualne
if [[ ! -d "venv" ]]; then
    echo "Tworzenie środowiska wirtualnego..."
    python3 -m venv venv
    echo "Środowisko wirtualne utworzone pomyślnie"
    echo
fi

# Aktywuj środowisko wirtualne
echo "Aktywacja środowiska wirtualnego..."
source venv/bin/activate

# Sprawdź czy pip jest aktualny
echo "Aktualizacja pip..."
pip install --upgrade pip

# Zainstaluj/zaktualizuj zależności
echo "Instalacja zależności..."
pip install -r requirements.txt

echo
echo "Zależności zainstalowane pomyślnie"
echo "Uruchamianie DJ Lite..."
echo

# Sprawdź dostępność urządzeń audio (Linux)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if ! command -v aplay &> /dev/null; then
        echo "OSTRZEŻENIE: Nie znaleziono narzędzi audio ALSA"
        echo "Zainstaluj: sudo apt install alsa-utils"
    fi
    
    # Sprawdź czy PulseAudio działa
    if command -v pulseaudio &> /dev/null; then
        if ! pulseaudio --check; then
            echo "OSTRZEŻENIE: PulseAudio nie działa"
            echo "Uruchom: pulseaudio --start"
        fi
    fi
fi

# Uruchom aplikację
python3 app.py

# Sprawdź kod wyjścia
EXIT_CODE=$?
if [[ $EXIT_CODE -ne 0 ]]; then
    echo
    echo "BŁĄD: Aplikacja zakończyła się z kodem $EXIT_CODE"
    echo "Sprawdź komunikaty powyżej"
else
    echo
    echo "DJ Lite zamknięty pomyślnie"
fi

# Dezaktywuj środowisko wirtualne
deactivate

echo
echo "Skrypt zakończony"
exit $EXIT_CODE