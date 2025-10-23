"""Wybór folderu i indeksowanie utworów audio dla DJ Lite."""

import os
import json
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable
import soundfile as sf
from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import QObject, Signal


class TrackInfo:
    """Informacje o utworze audio."""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.filename = os.path.basename(file_path)
        self.name = os.path.splitext(self.filename)[0]
        self.extension = os.path.splitext(self.filename)[1].lower()
        
        # Informacje audio (ładowane lazy)
        self._duration: Optional[float] = None
        self._sample_rate: Optional[int] = None
        self._channels: Optional[int] = None
        self._loaded = False
        
        # BPM z cache
        self._bpm: Optional[float] = None
        self._bpm_loaded = False
    
    def load_audio_info(self) -> bool:
        """Ładuje informacje o pliku audio."""
        if self._loaded:
            return True
            
        try:
            info = sf.info(self.file_path)
            self._duration = info.duration
            self._sample_rate = info.samplerate
            self._channels = info.channels
            self._loaded = True
            return True
        except Exception as e:
            print(f"Błąd ładowania info dla {self.file_path}: {e}")
            return False
    
    @property
    def duration(self) -> float:
        """Długość utworu w sekundach."""
        if not self._loaded:
            self.load_audio_info()
        return self._duration or 0.0
    
    @property
    def sample_rate(self) -> int:
        """Częstotliwość próbkowania."""
        if not self._loaded:
            self.load_audio_info()
        return self._sample_rate or 44100
    
    @property
    def channels(self) -> Optional[int]:
        """Liczba kanałów audio."""
        if not self._loaded:
            self.load_audio_info()
        return self._channels
    
    @property
    def bpm(self) -> Optional[float]:
        """BPM utworu z cache."""
        if not self._bpm_loaded:
            self.load_bpm_info()
        return self._bpm
    
    def load_bpm_info(self) -> bool:
        """Ładuje BPM z pliku cache."""
        try:
            bpm_cache_path = self.file_path + '.bpm.json'
            if os.path.exists(bpm_cache_path):
                with open(bpm_cache_path, 'r') as f:
                    data = json.load(f)
                    self._bpm = data.get('bpm')
            self._bpm_loaded = True
            return self._bpm is not None
        except Exception as e:
            print(f"Błąd ładowania BPM dla {self.file_path}: {e}")
            self._bpm_loaded = True
            return False
    
    def format_duration(self) -> str:
        """Formatuje długość jako MM:SS."""
        duration = self.duration
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        return f"{minutes:02d}:{seconds:02d}"
    
    def get_info_dict(self) -> Dict:
        """Zwraca informacje jako słownik."""
        return {
            'file_path': self.file_path,
            'filename': self.filename,
            'name': self.name,
            'extension': self.extension,
            'duration': self.duration,
            'duration_formatted': self.format_duration(),
            'sample_rate': self.sample_rate,
            'channels': self.channels
        }


class MusicLibrary(QObject):
    """Biblioteka muzyki z indeksowaniem i wyszukiwaniem."""
    
    # Sygnały Qt
    scan_progress = Signal(int, int)  # current, total
    scan_finished = Signal(int)  # total_tracks
    track_added = Signal(object)  # TrackInfo
    
    # Obsługiwane formaty audio
    SUPPORTED_FORMATS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma'}
    
    def __init__(self):
        super().__init__()
        self.tracks: List[TrackInfo] = []
        self.current_folder: Optional[str] = None
        self.is_scanning = False
        self._scan_thread: Optional[threading.Thread] = None
    
    def select_folder(self, parent_widget=None) -> Optional[str]:
        """Otwiera dialog wyboru folderu."""
        folder = QFileDialog.getExistingDirectory(
            parent_widget,
            "Wybierz folder z muzyką",
            self.current_folder or os.path.expanduser("~/Music")
        )
        
        if folder:
            self.current_folder = folder
            return folder
        return None
    
    def scan_folder(self, folder_path: str, recursive: bool = True) -> bool:
        """Skanuje folder w poszukiwaniu plików audio."""
        if self.is_scanning:
            print("Skanowanie już w toku")
            return False
        
        if not os.path.exists(folder_path):
            print(f"Folder nie istnieje: {folder_path}")
            return False
        
        self.current_folder = folder_path
        self.is_scanning = True
        
        # Uruchom skanowanie w osobnym wątku
        self._scan_thread = threading.Thread(
            target=self._scan_folder_thread,
            args=(folder_path, recursive)
        )
        self._scan_thread.daemon = True
        self._scan_thread.start()
        
        return True
    
    def _scan_folder_thread(self, folder_path: str, recursive: bool):
        """Skanuje folder w osobnym wątku."""
        try:
            # Znajdź wszystkie pliki audio
            audio_files = self._find_audio_files(folder_path, recursive)
            total_files = len(audio_files)
            
            print(f"Znaleziono {total_files} plików audio w {folder_path}")
            
            # Wyczyść poprzednie wyniki
            self.tracks.clear()
            
            # Przetwórz każdy plik
            for i, file_path in enumerate(audio_files):
                try:
                    track = TrackInfo(file_path)
                    self.tracks.append(track)
                    
                    # Emituj sygnały postępu
                    self.scan_progress.emit(i + 1, total_files)
                    self.track_added.emit(track)
                    
                except Exception as e:
                    print(f"Błąd przetwarzania {file_path}: {e}")
                
                # Sprawdź czy nie przerwano skanowania
                if not self.is_scanning:
                    break
            
            print(f"Skanowanie zakończone: {len(self.tracks)} utworów")
            self.scan_finished.emit(len(self.tracks))
            
        except Exception as e:
            print(f"Błąd skanowania: {e}")
        finally:
            self.is_scanning = False
    
    def _find_audio_files(self, folder_path: str, recursive: bool) -> List[str]:
        """Znajduje wszystkie pliki audio w folderze."""
        audio_files = []
        
        try:
            if recursive:
                # Rekurencyjne przeszukiwanie
                for root, dirs, files in os.walk(folder_path):
                    for file in files:
                        if self._is_audio_file(file):
                            audio_files.append(os.path.join(root, file))
            else:
                # Tylko główny folder
                for file in os.listdir(folder_path):
                    file_path = os.path.join(folder_path, file)
                    if os.path.isfile(file_path) and self._is_audio_file(file):
                        audio_files.append(file_path)
        
        except Exception as e:
            print(f"Błąd przeszukiwania folderu {folder_path}: {e}")
        
        return sorted(audio_files)
    
    def _is_audio_file(self, filename: str) -> bool:
        """Sprawdza czy plik ma obsługiwane rozszerzenie audio."""
        extension = os.path.splitext(filename)[1].lower()
        return extension in self.SUPPORTED_FORMATS
    
    def stop_scanning(self):
        """Zatrzymuje skanowanie."""
        self.is_scanning = False
    
    def search_tracks(self, query: str) -> List[TrackInfo]:
        """Wyszukuje utwory po nazwie."""
        if not query:
            return self.tracks.copy()
        
        query_lower = query.lower()
        results = []
        
        for track in self.tracks:
            if (query_lower in track.name.lower() or 
                query_lower in track.filename.lower()):
                results.append(track)
        
        return results
    
    def get_tracks_by_extension(self, extension: str) -> List[TrackInfo]:
        """Zwraca utwory o określonym rozszerzeniu."""
        extension = extension.lower()
        if not extension.startswith('.'):
            extension = '.' + extension
        
        return [track for track in self.tracks if track.extension == extension]
    
    def get_track_by_path(self, file_path: str) -> Optional[TrackInfo]:
        """Znajduje utwór po ścieżce pliku."""
        for track in self.tracks:
            if track.file_path == file_path:
                return track
        return None
    
    def get_tracks_count(self) -> int:
        """Zwraca liczbę utworów w bibliotece."""
        return len(self.tracks)
    
    def get_total_duration(self) -> float:
        """Zwraca całkowitą długość wszystkich utworów."""
        total = 0.0
        for track in self.tracks:
            total += track.duration
        return total
    
    def get_library_stats(self) -> Dict:
        """Zwraca statystyki biblioteki."""
        if not self.tracks:
            return {
                'total_tracks': 0,
                'total_duration': 0.0,
                'formats': {},
                'current_folder': self.current_folder
            }
        
        # Zlicz formaty
        formats = {}
        for track in self.tracks:
            ext = track.extension
            formats[ext] = formats.get(ext, 0) + 1
        
        return {
            'total_tracks': len(self.tracks),
            'total_duration': self.get_total_duration(),
            'formats': formats,
            'current_folder': self.current_folder
        }


class QuickBrowser:
    """Uproszczony browser plików dla szybkiego dostępu."""
    
    @staticmethod
    def select_audio_file(parent_widget=None, start_dir: str = None) -> Optional[str]:
        """Otwiera dialog wyboru pojedynczego pliku audio."""
        if start_dir is None:
            start_dir = os.path.expanduser("~/Music")
        
        file_filter = "Pliki audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma);;Wszystkie pliki (*.*)"
        
        file_path, _ = QFileDialog.getOpenFileName(
            parent_widget,
            "Wybierz plik audio",
            start_dir,
            file_filter
        )
        
        return file_path if file_path else None
    
    @staticmethod
    def get_recent_folders() -> List[str]:
        """Zwraca listę ostatnio używanych folderów (placeholder)."""
        # TODO: Implementacja zapisywania/ładowania ostatnich folderów
        common_music_dirs = [
            os.path.expanduser("~/Music"),
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Downloads")
        ]
        
        return [d for d in common_music_dirs if os.path.exists(d)]