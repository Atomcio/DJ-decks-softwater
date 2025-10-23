"""Wspólny master clock dla całego systemu DJ - pojedyncze źródło prawdy dla czasu."""

import threading
import time
from dataclasses import dataclass
from typing import Optional
import logging

log = logging.getLogger(__name__)

# Próba użycia high-resolution timer dla Windows
try:
    import ctypes
    from ctypes import wintypes
    
    # QueryPerformanceCounter dla Windows
    _qpc_frequency = wintypes.LARGE_INTEGER()
    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(_qpc_frequency))
    
    def high_res_time() -> float:
        """High-resolution monotonic timer dla Windows."""
        counter = wintypes.LARGE_INTEGER()
        ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
        return counter.value / _qpc_frequency.value
        
except (ImportError, AttributeError):
    # Fallback do time.perf_counter()
    def high_res_time() -> float:
        """Fallback do standardowego high-resolution timer."""
        return time.perf_counter()


@dataclass
class MasterClockState:
    """Stan master clock w określonym momencie."""
    monotonic_time: float      # czas monotoniczny z high_res_time()
    audio_samples_total: int   # całkowita liczba próbek audio wyprodukowanych
    sample_rate: int          # częstotliwość próbkowania
    is_running: bool          # czy clock jest aktywny
    
    @property
    def audio_time_seconds(self) -> float:
        """Czas audio w sekundach na podstawie próbek."""
        return self.audio_samples_total / self.sample_rate if self.sample_rate > 0 else 0.0


class MasterClock:
    """Wspólny master clock dla całego systemu DJ.
    
    Zapewnia:
    - Monotoniczny czas referencyjny
    - Synchronizację między deckami
    - Eliminację zależności od UI time
    - Deterministyczne pozycjonowanie audio
    """
    
    _instance: Optional['MasterClock'] = None
    _lock = threading.Lock()
    
    def __new__(cls, sample_rate: int = 48000):
        """Singleton pattern - jeden master clock dla całego systemu."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self, sample_rate: int = 48000):
        if self._initialized:
            return
            
        self.sample_rate = sample_rate
        self._audio_samples_total = 0
        self._is_running = False
        self._start_time = 0.0
        self._clock_lock = threading.Lock()
        
        # Kalibracja latency
        self._estimated_latency_samples = 0
        self._latency_ms = 0.0
        
        log.info(f"MasterClock initialized: {sample_rate}Hz")
        self._initialized = True
    
    def start(self, estimated_latency_ms: float = 120.0):
        """Uruchamia master clock.
        
        Args:
            estimated_latency_ms: Szacowana latencja audio w milisekundach
        """
        with self._clock_lock:
            if not self._is_running:
                self._start_time = high_res_time()
                self._audio_samples_total = 0
                self._is_running = True
                self._latency_ms = estimated_latency_ms
                self._estimated_latency_samples = int(estimated_latency_ms * self.sample_rate / 1000.0)
                log.info(f"MasterClock started with {estimated_latency_ms}ms latency")
    
    def stop(self):
        """Zatrzymuje master clock."""
        with self._clock_lock:
            self._is_running = False
            log.info("MasterClock stopped")
    
    def reset(self):
        """Resetuje master clock do stanu początkowego."""
        with self._clock_lock:
            self._audio_samples_total = 0
            self._start_time = high_res_time() if self._is_running else 0.0
            log.info("MasterClock reset")
    
    def on_audio_callback(self, frames_processed: int):
        """Aktualizuje clock po przetworzeniu audio.
        
        MUSI być wywoływane z audio thread po każdym bloku audio.
        
        Args:
            frames_processed: Liczba próbek przetworzonych w tym bloku
        """
        if not self._is_running:
            return
            
        with self._clock_lock:
            self._audio_samples_total += frames_processed
    
    def get_state(self) -> MasterClockState:
        """Zwraca aktualny stan master clock."""
        with self._clock_lock:
            return MasterClockState(
                monotonic_time=high_res_time(),
                audio_samples_total=self._audio_samples_total,
                sample_rate=self.sample_rate,
                is_running=self._is_running
            )
    
    def get_audio_time_seconds(self) -> float:
        """Zwraca aktualny czas audio w sekundach (z kompensacją latency)."""
        with self._clock_lock:
            if not self._is_running:
                return 0.0
            # Kompensacja latency - odejmij szacowaną latencję
            compensated_samples = max(0, self._audio_samples_total - self._estimated_latency_samples)
            return compensated_samples / self.sample_rate
    
    def get_monotonic_time(self) -> float:
        """Zwraca aktualny czas monotoniczny."""
        return high_res_time()
    
    def samples_to_seconds(self, samples: int) -> float:
        """Konwertuje próbki na sekundy."""
        return samples / self.sample_rate
    
    def seconds_to_samples(self, seconds: float) -> int:
        """Konwertuje sekundy na próbki."""
        return int(seconds * self.sample_rate)
    
    def get_total_audio_samples(self) -> int:
        """Zwraca całkowitą liczbę przetworzonych próbek audio."""
        with self._clock_lock:
            return self._audio_samples_total
    
    def is_running(self) -> bool:
        """Sprawdza czy clock jest aktywny."""
        with self._clock_lock:
            return self._is_running
    
    def set_latency_compensation(self, latency_ms: float):
        """Ustawia kompensację latency.
        
        Args:
            latency_ms: Latencja w milisekundach
        """
        with self._clock_lock:
            self._latency_ms = latency_ms
            self._estimated_latency_samples = int(latency_ms * self.sample_rate / 1000.0)
            log.info(f"MasterClock latency compensation set to {latency_ms}ms")
    
    def get_latency_ms(self) -> float:
        """Zwraca aktualną kompensację latency w milisekundach."""
        with self._clock_lock:
            return self._latency_ms


# Globalna instancja master clock
_master_clock: Optional[MasterClock] = None

def get_master_clock(sample_rate: int = 48000) -> MasterClock:
    """Zwraca globalną instancję master clock."""
    global _master_clock
    if _master_clock is None:
        _master_clock = MasterClock(sample_rate)
    return _master_clock


def reset_master_clock():
    """Resetuje globalną instancję master clock (głównie do testów)."""
    global _master_clock
    if _master_clock:
        _master_clock.stop()
    _master_clock = None
    MasterClock._instance = None