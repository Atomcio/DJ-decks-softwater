#!/usr/bin/env python3
"""DJ Lite - Ultra-prosty odtwarzacz DJ z dwoma deckami.

Główny plik startowy aplikacji.
Uruchamia GUI i inicjalizuje system audio.
"""

import sys
import os
import traceback
import logging
from pathlib import Path

# Konfiguracja loggingu
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Dodaj ścieżkę do modułów
app_dir = Path(__file__).parent
sys.path.insert(0, str(app_dir))

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QIcon, QFont
except ImportError as e:
    print(f"Błąd importu PySide6: {e}")
    print("Zainstaluj wymagane biblioteki: pip install -r requirements.txt")
    sys.exit(1)

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    import scipy
except ImportError as e:
    print(f"Błąd importu bibliotek audio: {e}")
    print("Zainstaluj wymagane biblioteki: pip install -r requirements.txt")
    sys.exit(1)

try:
    from ui.main_window import DJLiteMainWindow
except ImportError as e:
    print(f"Błąd importu UI: {e}")
    traceback.print_exc()
    sys.exit(1)


class DJLiteApp:
    """Główna klasa aplikacji DJ Lite."""
    
    def __init__(self):
        self.app = None
        self.main_window = None
        self.setup_application()
    
    def setup_application(self):
        """Konfiguruje aplikację Qt."""
        # Ustawienia aplikacji
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("DJ Lite")
        self.app.setApplicationVersion("1.0.0")
        self.app.setOrganizationName("DJ Lite")
        
        # Ustaw domyślną czcionkę
        font = QFont("Arial", 9)
        self.app.setFont(font)
        
        # Obsługa zamykania aplikacji
        self.app.aboutToQuit.connect(self.cleanup)
    
    def check_audio_devices(self) -> bool:
        """Sprawdza dostępność urządzeń audio."""
        try:
            devices = sd.query_devices()
            if len(devices) == 0:
                self.show_error("Brak urządzeń audio", 
                               "Nie znaleziono żadnych urządzeń audio w systemie.")
                return False
            
            # Sprawdź czy jest dostępne urządzenie wyjściowe
            output_devices = [d for d in devices if d['max_output_channels'] > 0]
            if len(output_devices) == 0:
                self.show_error("Brak urządzeń wyjściowych", 
                               "Nie znaleziono urządzeń audio z wyjściem.")
                return False
            
            print(f"Znaleziono {len(output_devices)} urządzeń wyjściowych audio")
            return True
            
        except Exception as e:
            self.show_error("Błąd audio", f"Błąd sprawdzania urządzeń audio: {e}")
            return False
    
    def show_error(self, title: str, message: str):
        """Wyświetla okno błędu."""
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec()
    
    def show_info(self, title: str, message: str):
        """Wyświetla okno informacyjne."""
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec()
    
    def create_main_window(self) -> bool:
        """Tworzy główne okno aplikacji."""
        try:
            self.main_window = DJLiteMainWindow()
            
            # Wyśrodkuj okno na ekranie
            screen = self.app.primaryScreen().geometry()
            window_size = self.main_window.size()
            x = (screen.width() - window_size.width()) // 2
            y = (screen.height() - window_size.height()) // 2
            self.main_window.move(x, y)
            
            return True
            
        except Exception as e:
            self.show_error("Błąd inicjalizacji", 
                           f"Nie udało się utworzyć głównego okna: {e}")
            traceback.print_exc()
            return False
    
    def run(self) -> int:
        """Uruchamia aplikację."""
        print("=== DJ Lite - Uruchamianie ===")
        print(f"Python: {sys.version}")
        print(f"Katalog aplikacji: {app_dir}")
        
        # Sprawdź urządzenia audio
        if not self.check_audio_devices():
            return 1
        
        # Utwórz główne okno
        if not self.create_main_window():
            return 1
        
        # Pokaż okno
        self.main_window.show()
        
        # Wyświetl informacje o uruchomieniu
        print("DJ Lite uruchomiony pomyślnie!")
        print("Sterowanie:")
        print("- Przeciągnij okno lewym przyciskiem myszy")
        print("- Użyj przycisku ✕ aby zamknąć")
        print("- Wybierz folder z muzyką w sekcji PLAYLIST")
        print("- Kliknij dwukrotnie utwór aby załadować do decka")
        
        # Uruchom pętlę aplikacji
        try:
            return self.app.exec()
        except KeyboardInterrupt:
            print("\nPrzerwano przez użytkownika")
            return 0
        except Exception as e:
            self.show_error("Błąd aplikacji", f"Nieoczekiwany błąd: {e}")
            traceback.print_exc()
            return 1
    
    def cleanup(self):
        """Czyści zasoby przed zamknięciem."""
        print("Zamykanie DJ Lite...")
        
        if self.main_window:
            try:
                # Zatrzymaj audio mixer
                if hasattr(self.main_window, 'mixer'):
                    self.main_window.mixer.stop_audio()
                    print("Audio mixer zatrzymany")
            except Exception as e:
                print(f"Błąd zatrzymywania audio: {e}")
        
        print("DJ Lite zamknięty")


def check_dependencies():
    """Sprawdza czy wszystkie wymagane biblioteki są zainstalowane."""
    required_modules = {
        'PySide6': 'PySide6',
        'sounddevice': 'sounddevice',
        'soundfile': 'soundfile', 
        'numpy': 'numpy',
        'scipy': 'scipy'
    }
    
    missing = []
    
    for module_name, package_name in required_modules.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)
    
    if missing:
        print("Brakujące biblioteki:")
        for package in missing:
            print(f"  - {package}")
        print("\nZainstaluj je poleceniem:")
        print(f"pip install {' '.join(missing)}")
        print("\nlub:")
        print("pip install -r requirements.txt")
        return False
    
    return True


def main():
    """Główna funkcja aplikacji."""
    print("DJ Lite v1.0.0")
    print("Ultra-prosty odtwarzacz DJ z dwoma deckami")
    print("" + "="*50)
    
    # Sprawdź zależności
    if not check_dependencies():
        return 1
    
    # Utwórz i uruchom aplikację
    try:
        app = DJLiteApp()
        return app.run()
    except Exception as e:
        print(f"Krytyczny błąd: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())