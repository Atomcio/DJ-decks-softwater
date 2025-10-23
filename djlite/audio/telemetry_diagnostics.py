"""Moduł diagnostyczny do logowania telemetrii pozycji i zegarów DJ aplikacji.

Loguje co 100ms:
- audioClock (wysokorozdzielczy, wspólny dla obu decków)
- deckA.samplePosition, deckB.samplePosition (z audio thread)
- deckA.tempoBPM, deckB.tempoBPM (po analizie/po master sync)
- phaseOffsetBeats = (posA - posB) w beatach (z normalizacją do [-0.5, 0.5])
- bufor/latency: outputBufferSize, blockSize, estimatedOutputLatencyMs

Zero zmian w audio ścieżce - tylko odczyt i log.
"""

import threading
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path
import json

# Import MasterClock dla spójnego czasu referencyjnego
from .master_clock import get_master_clock

# High-resolution monotonic clock
try:
    # Windows high-resolution timer
    import ctypes
    from ctypes import wintypes
    
    kernel32 = ctypes.windll.kernel32
    kernel32.QueryPerformanceCounter.restype = wintypes.BOOL
    kernel32.QueryPerformanceCounter.argtypes = [ctypes.POINTER(wintypes.LARGE_INTEGER)]
    kernel32.QueryPerformanceFrequency.restype = wintypes.BOOL
    kernel32.QueryPerformanceFrequency.argtypes = [ctypes.POINTER(wintypes.LARGE_INTEGER)]
    
    _qpc_frequency = wintypes.LARGE_INTEGER()
    kernel32.QueryPerformanceFrequency(ctypes.byref(_qpc_frequency))
    
    def high_res_time() -> float:
        """Zwraca high-resolution monotonic timestamp w sekundach."""
        counter = wintypes.LARGE_INTEGER()
        kernel32.QueryPerformanceCounter(ctypes.byref(counter))
        return counter.value / _qpc_frequency.value
        
except (ImportError, AttributeError):
    # Fallback do time.perf_counter()
    def high_res_time() -> float:
        """Fallback do standardowego high-resolution timer."""
        return time.perf_counter()


@dataclass
class TelemetrySnapshot:
    """Pojedynczy snapshot telemetrii."""
    timestamp: float  # monotonic time z MasterClock
    audio_clock_seconds: float  # czas audio z MasterClock
    
    # Pozycje sampli z audio thread (nie z UI)
    deck_a_sample_position: int
    deck_b_sample_position: int
    
    # Tempo BPM po analizie/sync
    deck_a_tempo_bpm: float
    deck_b_tempo_bpm: float
    
    # Phase offset w beatach [-0.5, 0.5]
    phase_offset_beats: float
    
    # Metryki bufora/latency
    output_buffer_size: int
    block_size: int
    estimated_output_latency_ms: float
    
    # Dodatkowe informacje diagnostyczne
    deck_a_playing: bool
    deck_b_playing: bool
    deck_a_effective_ratio: float
    deck_b_effective_ratio: float


class TelemetryDiagnostics:
    """Moduł diagnostyczny do logowania telemetrii bez wpływu na audio ścieżkę."""
    
    def __init__(self, mixer, log_to_file: bool = True, log_to_console: bool = True):
        self.mixer = mixer
        self.log_to_file = log_to_file
        self.log_to_console = log_to_console
        
        # MasterClock jako źródło prawdy dla czasu
        self.master_clock = get_master_clock(mixer.sample_rate if hasattr(mixer, 'sample_rate') else 48000)
        
        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Logging setup
        self._setup_logging()
        
        # Timing
        self._interval_ms = 100  # 100ms interval
        self._start_time = high_res_time()
        
        # Snapshot counter
        self._snapshot_count = 0
        
    def _setup_logging(self):
        """Konfiguruje logging do pliku i konsoli."""
        # File logger
        if self.log_to_file:
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"telemetry_{timestamp}.log"
            
            self.file_logger = logging.getLogger("telemetry_file")
            self.file_logger.setLevel(logging.INFO)
            
            # Remove existing handlers
            for handler in self.file_logger.handlers[:]:
                self.file_logger.removeHandler(handler)
            
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.INFO)
            
            # JSON format for easy parsing
            file_formatter = logging.Formatter('%(message)s')
            file_handler.setFormatter(file_formatter)
            self.file_logger.addHandler(file_handler)
            
            # Prevent propagation to root logger
            self.file_logger.propagate = False
        
        # Console logger
        if self.log_to_console:
            self.console_logger = logging.getLogger("telemetry_console")
            self.console_logger.setLevel(logging.INFO)
            
            # Remove existing handlers
            for handler in self.console_logger.handlers[:]:
                self.console_logger.removeHandler(handler)
            
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # Human-readable format
            console_formatter = logging.Formatter(
                '[TELEMETRY] %(asctime)s - %(message)s',
                datefmt='%H:%M:%S.%f'
            )
            console_handler.setFormatter(console_formatter)
            self.console_logger.addHandler(console_handler)
            
            # Prevent propagation to root logger
            self.console_logger.propagate = False
    
    def _get_sample_position_from_audio_thread(self, deck) -> int:
        """Pobiera pozycję sampli bezpośrednio z audio thread (nie z UI)."""
        try:
            # Pozycja z AudioClock (base_samples + samples_played)
            with deck.clock._lock:
                total_samples = deck.clock._base_samples + deck.clock._samples_played
            return total_samples
        except Exception:
            return 0
    
    def _calculate_phase_offset_beats(self, deck_a, deck_b) -> float:
        """Oblicza phase offset w beatach z normalizacją do [-0.5, 0.5]."""
        try:
            # Pobierz pozycje w sekundach
            pos_a_seconds = deck_a.clock.now_seconds()
            pos_b_seconds = deck_b.clock.now_seconds()
            
            # Pobierz BPM (użyj detected_bpm lub current_bpm)
            bpm_a = deck_a.detected_bpm if deck_a.detected_bpm > 0 else deck_a.current_bpm
            bpm_b = deck_b.detected_bpm if deck_b.detected_bpm > 0 else deck_b.current_bpm
            
            if bpm_a <= 0 or bpm_b <= 0:
                return 0.0
            
            # Użyj średnie BPM dla kalkulacji
            avg_bpm = (bpm_a + bpm_b) / 2.0
            beats_per_second = avg_bpm / 60.0
            
            # Konwertuj pozycje na beaty
            pos_a_beats = pos_a_seconds * beats_per_second
            pos_b_beats = pos_b_seconds * beats_per_second
            
            # Oblicz różnicę
            phase_diff = pos_a_beats - pos_b_beats
            
            # Normalizuj do [-0.5, 0.5]
            phase_diff = phase_diff % 1.0
            if phase_diff > 0.5:
                phase_diff -= 1.0
            
            return phase_diff
            
        except Exception:
            return 0.0
    
    def _get_buffer_metrics(self) -> tuple[int, int, float]:
        """Pobiera metryki bufora/latency."""
        try:
            # Z mixer konfiguracji
            buffer_size = self.mixer.buffer_size
            block_size = buffer_size  # W tym przypadku są takie same
            
            # Estymowana latencja output w ms
            latency_seconds = self.mixer.latency
            latency_ms = latency_seconds * 1000.0
            
            return buffer_size, block_size, latency_ms
            
        except Exception:
            return 0, 0, 0.0
    
    def _capture_snapshot(self) -> TelemetrySnapshot:
        """Przechwytuje pojedynczy snapshot telemetrii."""
        # Czas z MasterClock - deterministyczny i spójny
        master_state = self.master_clock.get_state()
        timestamp = master_state.monotonic_time
        audio_clock_seconds = master_state.audio_time_seconds
        
        # Pozycje sampli z audio thread
        deck_a_sample_pos = self._get_sample_position_from_audio_thread(self.mixer.deck_a)
        deck_b_sample_pos = self._get_sample_position_from_audio_thread(self.mixer.deck_b)
        
        # Tempo BPM po analizie/sync
        deck_a_bpm = self.mixer.deck_a.detected_bpm if self.mixer.deck_a.detected_bpm > 0 else self.mixer.deck_a.current_bpm
        deck_b_bpm = self.mixer.deck_b.detected_bpm if self.mixer.deck_b.detected_bpm > 0 else self.mixer.deck_b.current_bpm
        
        # Phase offset w beatach
        phase_offset = self._calculate_phase_offset_beats(self.mixer.deck_a, self.mixer.deck_b)
        
        # Buffer metrics
        buffer_size, block_size, latency_ms = self._get_buffer_metrics()
        
        # Dodatkowe informacje diagnostyczne
        deck_a_playing = self.mixer.deck_a.is_playing
        deck_b_playing = self.mixer.deck_b.is_playing
        deck_a_ratio = self.mixer.deck_a.effective_ratio()
        deck_b_ratio = self.mixer.deck_b.effective_ratio()
        
        return TelemetrySnapshot(
            timestamp=timestamp,
            audio_clock_seconds=audio_clock_seconds,
            deck_a_sample_position=deck_a_sample_pos,
            deck_b_sample_position=deck_b_sample_pos,
            deck_a_tempo_bpm=deck_a_bpm,
            deck_b_tempo_bpm=deck_b_bpm,
            phase_offset_beats=phase_offset,
            output_buffer_size=buffer_size,
            block_size=block_size,
            estimated_output_latency_ms=latency_ms,
            deck_a_playing=deck_a_playing,
            deck_b_playing=deck_b_playing,
            deck_a_effective_ratio=deck_a_ratio,
            deck_b_effective_ratio=deck_b_ratio
        )
    
    def _log_snapshot(self, snapshot: TelemetrySnapshot):
        """Loguje snapshot do pliku i konsoli."""
        # Relative timestamp od startu
        relative_time = snapshot.timestamp - self._start_time
        
        # JSON dla pliku
        if self.log_to_file:
            json_data = {
                "seq": self._snapshot_count,
                "timestamp": snapshot.timestamp,
                "relative_time": relative_time,
                "audio_clock": snapshot.audio_clock_seconds,
                "deck_a": {
                    "sample_position": snapshot.deck_a_sample_position,
                    "tempo_bpm": snapshot.deck_a_tempo_bpm,
                    "playing": snapshot.deck_a_playing,
                    "effective_ratio": snapshot.deck_a_effective_ratio
                },
                "deck_b": {
                    "sample_position": snapshot.deck_b_sample_position,
                    "tempo_bpm": snapshot.deck_b_tempo_bpm,
                    "playing": snapshot.deck_b_playing,
                    "effective_ratio": snapshot.deck_b_effective_ratio
                },
                "phase_offset_beats": snapshot.phase_offset_beats,
                "buffer": {
                    "output_buffer_size": snapshot.output_buffer_size,
                    "block_size": snapshot.block_size,
                    "estimated_latency_ms": snapshot.estimated_output_latency_ms
                }
            }
            self.file_logger.info(json.dumps(json_data))
        
        # Human-readable dla konsoli
        if self.log_to_console:
            console_msg = (
                f"T+{relative_time:.3f}s | "
                f"AudioClock: {snapshot.audio_clock_seconds:.3f}s | "
                f"A: pos={snapshot.deck_a_sample_position}, bpm={snapshot.deck_a_tempo_bpm:.1f}, ratio={snapshot.deck_a_effective_ratio:.3f} | "
                f"B: pos={snapshot.deck_b_sample_position}, bpm={snapshot.deck_b_tempo_bpm:.1f}, ratio={snapshot.deck_b_effective_ratio:.3f} | "
                f"Phase: {snapshot.phase_offset_beats:.3f} beats | "
                f"Buffer: {snapshot.output_buffer_size}, Latency: {snapshot.estimated_output_latency_ms:.1f}ms"
            )
            self.console_logger.info(console_msg)
    
    def _telemetry_loop(self):
        """Główna pętla telemetrii - działa co 100ms."""
        next_capture_time = high_res_time()
        interval_seconds = self._interval_ms / 1000.0
        
        while self._running:
            try:
                # Przechwytuj snapshot
                snapshot = self._capture_snapshot()
                
                # Loguj
                self._log_snapshot(snapshot)
                
                # Increment counter
                self._snapshot_count += 1
                
                # Oblicz następny czas przechwytywania
                next_capture_time += interval_seconds
                current_time = high_res_time()
                
                # Sleep do następnego przechwytywania
                sleep_time = next_capture_time - current_time
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    # Jeśli jesteśmy spóźnieni, przechwytuj natychmiast
                    next_capture_time = current_time
                    
            except Exception as e:
                if self.log_to_console:
                    self.console_logger.error(f"Błąd w telemetry loop: {e}")
                time.sleep(0.1)  # Krótka pauza przed ponowną próbą
    
    def start(self):
        """Rozpoczyna logowanie telemetrii."""
        with self._lock:
            if self._running:
                return
            
            self._running = True
            self._start_time = high_res_time()
            self._snapshot_count = 0
            
            self._thread = threading.Thread(target=self._telemetry_loop, daemon=True)
            self._thread.start()
            
            if self.log_to_console:
                self.console_logger.info("Telemetria diagnostyczna uruchomiona (100ms interval)")
    
    def stop(self):
        """Zatrzymuje logowanie telemetrii."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            
            if self.log_to_console:
                self.console_logger.info(f"Telemetria diagnostyczna zatrzymana (przechwycono {self._snapshot_count} snapshots)")
    
    def is_running(self) -> bool:
        """Sprawdza czy telemetria jest aktywna."""
        with self._lock:
            return self._running
    
    def get_stats(self) -> Dict[str, Any]:
        """Zwraca statystyki telemetrii."""
        with self._lock:
            runtime = high_res_time() - self._start_time if self._running else 0
            return {
                "running": self._running,
                "snapshot_count": self._snapshot_count,
                "runtime_seconds": runtime,
                "interval_ms": self._interval_ms,
                "log_to_file": self.log_to_file,
                "log_to_console": self.log_to_console
            }