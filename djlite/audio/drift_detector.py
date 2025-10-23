"""Detektor dryfu - monitoruje phaseOffsetBeats i oblicza drift."""

import time
import threading
from collections import deque
from typing import Optional, Callable
from dataclasses import dataclass
import numpy as np
import logging
from .master_clock import get_master_clock

log = logging.getLogger(__name__)

@dataclass
class DriftSnapshot:
    """Snapshot danych dryfu w określonym momencie."""
    timestamp: float
    phase_offset_beats: float
    moving_average: float
    drift_beats_per_min: float
    sample_count: int

class DriftDetector:
    """Detektor dryfu fazowego między deckami."""
    
    def __init__(self, window_size: int = 50, update_interval: float = 0.1, sample_rate: int = 48000):
        """
        Args:
            window_size: Rozmiar okna dla ruchomej średniej
            update_interval: Interwał aktualizacji w sekundach
            sample_rate: Sample rate dla MasterClock
        """
        self.window_size = window_size
        self.update_interval = update_interval
        self.sample_rate = sample_rate
        
        # MasterClock jako źródło prawdy dla czasu
        self.master_clock = get_master_clock(sample_rate)
        
        # Bufory danych
        self.phase_history = deque(maxlen=window_size)
        self.time_history = deque(maxlen=window_size)
        
        # Aktualne wartości
        self.current_offset = 0.0
        self.moving_average = 0.0
        self.drift_beats_per_min = 0.0
        self.last_update_time = 0.0
        
        # Threading
        self.enabled = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        
        # Źródło danych
        self.telemetry = None
        
        # Callbacks
        self.drift_callback: Optional[Callable[[DriftSnapshot], None]] = None
        
        # Historia snapshots
        self.snapshot_history = deque(maxlen=1000)
        
        # Parametry filtrowania
        self.outlier_threshold = 0.5  # Odrzuć wartości > 0.5 beats
        self.min_samples_for_drift = 10  # Minimum próbek do obliczenia dryfu
    
    def set_telemetry_source(self, telemetry):
        """Ustawia źródło danych telemetrycznych."""
        self.telemetry = telemetry
    
    def start(self):
        """Uruchamia detektor dryfu."""
        if self.enabled:
            return
        
        self.enabled = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._drift_loop, daemon=True)
        self._thread.start()
        
        log.info("Drift detector started")
    
    def stop(self):
        """Zatrzymuje detektor dryfu."""
        if not self.enabled:
            return
        
        self.enabled = False
        self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        
        log.info("Drift detector stopped")
    
    def _drift_loop(self):
        """Główna pętla detektora dryfu."""
        while not self._stop_event.wait(self.update_interval):
            try:
                self._update_drift_calculation()
            except Exception as e:
                log.error(f"Error in drift loop: {e}")
    
    def _update_drift_calculation(self):
        """Aktualizuje obliczenia dryfu."""
        if not self.telemetry:
            return
        
        # Pobierz najnowszy snapshot z telemetrii
        latest_snapshot = self.telemetry.get_latest_snapshot()
        if not latest_snapshot:
            return
        
        # Używaj MasterClock dla spójnego czasu referencyjnego
        master_state = self.master_clock.get_state()
        current_time = master_state.monotonic_time
        phase_offset = latest_snapshot.phase_offset_beats
        
        # Filtruj outliers
        if abs(phase_offset) > self.outlier_threshold:
            log.debug(f"Outlier filtered: {phase_offset:.3f} beats")
            return
        
        with self._lock:
            # Dodaj do historii
            self.phase_history.append(phase_offset)
            self.time_history.append(current_time)
            self.current_offset = phase_offset
            
            # Oblicz ruchomą średnią
            if len(self.phase_history) > 0:
                self.moving_average = np.mean(self.phase_history)
            
            # Oblicz drift (pochodną)
            self.drift_beats_per_min = self._calculate_drift()
            
            # Stwórz snapshot
            snapshot = DriftSnapshot(
                timestamp=current_time,
                phase_offset_beats=phase_offset,
                moving_average=self.moving_average,
                drift_beats_per_min=self.drift_beats_per_min,
                sample_count=len(self.phase_history)
            )
            
            # Dodaj do historii snapshots
            self.snapshot_history.append(snapshot)
            
            # Callback
            if self.drift_callback:
                self.drift_callback(snapshot)
            
            self.last_update_time = current_time
    
    def _calculate_drift(self) -> float:
        """Oblicza drift w beats/min na podstawie historii."""
        if len(self.phase_history) < self.min_samples_for_drift:
            return 0.0
        
        # Użyj regresji liniowej do obliczenia trendu
        times = np.array(list(self.time_history))
        phases = np.array(list(self.phase_history))
        
        # Normalizuj czas do sekund od pierwszego pomiaru
        times = times - times[0]
        
        if len(times) < 2 or times[-1] - times[0] < 1.0:  # Minimum 1 sekunda danych
            return 0.0
        
        try:
            # Regresja liniowa: y = ax + b, gdzie a to slope (beats/sec)
            slope, _ = np.polyfit(times, phases, 1)
            
            # Konwertuj z beats/sec na beats/min
            drift_beats_per_min = slope * 60.0
            
            return drift_beats_per_min
            
        except Exception as e:
            log.debug(f"Error calculating drift: {e}")
            return 0.0
    
    def get_current_status(self) -> dict:
        """Zwraca aktualny status detektora dryfu."""
        with self._lock:
            return {
                'enabled': self.enabled,
                'current_offset_beats': self.current_offset,
                'moving_average_beats': self.moving_average,
                'drift_beats_per_min': self.drift_beats_per_min,
                'sample_count': len(self.phase_history),
                'window_size': self.window_size,
                'last_update': self.last_update_time
            }
    
    def get_hud_data(self) -> dict:
        """Zwraca dane dla HUD w formacie czytelnym dla użytkownika."""
        with self._lock:
            # Formatuj offset
            offset_str = f"{self.current_offset:+.3f}"
            if abs(self.current_offset) < 0.001:
                offset_str = "0.000"
            
            # Formatuj drift
            drift_str = f"{self.drift_beats_per_min:+.2f}"
            if abs(self.drift_beats_per_min) < 0.01:
                drift_str = "0.00"
            
            # Określ status jakości pomiaru
            quality = "poor"
            if len(self.phase_history) >= self.min_samples_for_drift:
                if len(self.phase_history) >= self.window_size * 0.8:
                    quality = "good"
                else:
                    quality = "fair"
            
            return {
                'offset_beats': self.current_offset,
                'offset_str': offset_str,
                'drift_beats_per_min': self.drift_beats_per_min,
                'drift_str': drift_str,
                'moving_average': self.moving_average,
                'sample_count': len(self.phase_history),
                'quality': quality,
                'enabled': self.enabled
            }
    
    def reset(self):
        """Resetuje historię pomiarów."""
        with self._lock:
            self.phase_history.clear()
            self.time_history.clear()
            self.snapshot_history.clear()
            self.current_offset = 0.0
            self.moving_average = 0.0
            self.drift_beats_per_min = 0.0
            
        log.info("Drift detector reset")
    
    def get_history(self, max_samples: int = 100) -> list:
        """Zwraca historię snapshots dla analizy."""
        with self._lock:
            history = list(self.snapshot_history)
            if len(history) > max_samples:
                # Zwróć równomiernie rozłożone próbki
                step = len(history) // max_samples
                history = history[::step]
            return history
    
    def add_manual_measurement(self, phase_offset_beats: float):
        """Dodaje ręczny pomiar phase offset (dla testowania)."""
        current_time = time.perf_counter()
        
        with self._lock:
            self.phase_history.append(phase_offset_beats)
            self.time_history.append(current_time)
            self.current_offset = phase_offset_beats
            
            if len(self.phase_history) > 0:
                self.moving_average = np.mean(self.phase_history)
            
            self.drift_beats_per_min = self._calculate_drift()
            
        log.debug(f"Manual measurement added: {phase_offset_beats:.3f} beats")
    
    def set_window_size(self, size: int):
        """Zmienia rozmiar okna ruchomej średniej."""
        if size < 5:
            size = 5
        elif size > 200:
            size = 200
        
        with self._lock:
            self.window_size = size
            # Utwórz nowe deque z nowym rozmiarem
            old_phase = list(self.phase_history)
            old_time = list(self.time_history)
            
            self.phase_history = deque(old_phase[-size:], maxlen=size)
            self.time_history = deque(old_time[-size:], maxlen=size)
        
        log.info(f"Drift detector window size changed to {size}")
    
    def get_statistics(self) -> dict:
        """Zwraca statystyki detektora dryfu."""
        with self._lock:
            if len(self.phase_history) == 0:
                return {
                    'count': 0,
                    'mean': 0.0,
                    'std': 0.0,
                    'min': 0.0,
                    'max': 0.0,
                    'range': 0.0
                }
            
            phases = np.array(list(self.phase_history))
            
            return {
                'count': len(phases),
                'mean': float(np.mean(phases)),
                'std': float(np.std(phases)),
                'min': float(np.min(phases)),
                'max': float(np.max(phases)),
                'range': float(np.max(phases) - np.min(phases))
            }