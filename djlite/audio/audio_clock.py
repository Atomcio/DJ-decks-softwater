from dataclasses import dataclass
import threading
from .master_clock import get_master_clock

@dataclass
class AudioClock:
    """AudioClock dla pojedynczego decka - używa MasterClock jako referencji."""
    sr: int
    _samples_played: int = 0          # próbki odtworzone przez ten deck
    _lock: threading.Lock = threading.Lock()
    _paused: bool = True
    _base_samples: int = 0            # pozycja startowa w pliku (w próbkach)
    _start_master_samples: int = 0    # pozycja w MasterClock gdy rozpoczęto odtwarzanie

    def __post_init__(self):
        """Inicjalizacja po utworzeniu dataclass."""
        self._master_clock = get_master_clock(self.sr)

    def reset(self):
        """Resetuje clock do stanu początkowego."""
        with self._lock:
            self._samples_played = 0
            self._base_samples = 0
            self._paused = True
            self._start_master_samples = 0

    def on_audio_callback(self, frames: int):
        """Aktualizuje pozycję po przetworzeniu audio.
        
        UWAGA: MasterClock jest aktualizowany przez mixer, nie przez poszczególne decki.
        """
        if self._paused:
            return
        with self._lock:
            self._samples_played += frames

    def play_from_samples(self, start_samples: int):
        """Rozpoczyna odtwarzanie od określonej pozycji w pliku."""
        with self._lock:
            self._base_samples = start_samples
            self._samples_played = 0
            self._paused = False
            # Zapamiętaj pozycję w MasterClock
            self._start_master_samples = self._master_clock.get_total_audio_samples()

    def pause(self):
        """Pauzuje odtwarzanie."""
        with self._lock:
            self._paused = True

    def now_seconds(self) -> float:
        """Zwraca aktualną pozycję w pliku w sekundach.
        
        Używa MasterClock jako referencji dla deterministycznego pozycjonowania.
        """
        with self._lock:
            if self._paused:
                # Gdy pauzowane, zwróć ostatnią znaną pozycję
                total = self._base_samples + self._samples_played
            else:
                # Gdy odtwarzane, synchronizuj z MasterClock
                master_samples_elapsed = self._master_clock.get_total_audio_samples() - self._start_master_samples
                total = self._base_samples + master_samples_elapsed
            
            return total / self.sr
    
    def get_file_position_samples(self) -> int:
        """Zwraca aktualną pozycję w pliku w próbkach."""
        with self._lock:
            if self._paused:
                return self._base_samples + self._samples_played
            else:
                master_samples_elapsed = self._master_clock.get_total_audio_samples() - self._start_master_samples
                return self._base_samples + master_samples_elapsed