@echo off
REM DJ Lite - Skrypt uruchomieniowy dla Windows
REM Uruchamia aplikację DJ Lite w trybie deweloperskim

echo =======================================
echo DJ Lite - Ultra-prosty odtwarzacz DJ
echo =======================================
echo.

REM Przejdź do katalogu głównego projektu
cd /d "%~dp0.."

REM Sprawdź czy Python jest zainstalowany
python --version >nul 2>&1
if errorlevel 1 (
    echo BŁĄD: Python nie jest zainstalowany lub nie jest dostępny w PATH
    echo Zainstaluj Python 3.10+ z https://python.org
    pause
    exit /b 1
)

REM Sprawdź czy istnieje plik requirements.txt
if not exist "requirements.txt" (
    echo BŁĄD: Nie znaleziono pliku requirements.txt
    echo Upewnij się, że uruchamiasz skrypt z katalogu projektu
    pause
    exit /b 1
)

REM Sprawdź czy istnieje środowisko wirtualne
if not exist "venv" (
    echo Tworzenie środowiska wirtualnego...
    python -m venv venv
    if errorlevel 1 (
        echo BŁĄD: Nie udało się utworzyć środowiska wirtualnego
        pause
        exit /b 1
    )
    echo Środowisko wirtualne utworzone pomyślnie
    echo.
)

REM Aktywuj środowisko wirtualne
echo Aktywacja środowiska wirtualnego...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo BŁĄD: Nie udało się aktywować środowiska wirtualnego
    pause
    exit /b 1
)

REM Zainstaluj/zaktualizuj zależności
echo Instalacja zależności...
pip install -r requirements.txt
if errorlevel 1 (
    echo BŁĄD: Nie udało się zainstalować zależności
    echo Sprawdź połączenie internetowe i spróbuj ponownie
    pause
    exit /b 1
)

echo.
echo Zależności zainstalowane pomyślnie
echo Uruchamianie DJ Lite...
echo.

REM Uruchom aplikację
python app.py

REM Sprawdź kod wyjścia
if errorlevel 1 (
    echo.
    echo BŁĄD: Aplikacja zakończyła się z błędem
    echo Sprawdź komunikaty powyżej
    pause
) else (
    echo.
    echo DJ Lite zamknięty pomyślnie
)

REM Dezaktywuj środowisko wirtualne
deactivate

echo.
echo Naciśnij dowolny klawisz aby zamknąć...
pause >nul