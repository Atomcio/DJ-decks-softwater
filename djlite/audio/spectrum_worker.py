import threading
import time
import numpy as np
from collections import deque
from typing import Optional, Callable, Dict, Any
import logging

class SpectrumWorker:
    """Wspólny worker thread dla analiz spektrum obu decków"""
    
    def __init__(self, tick_interval_ms: int = 45):
        self.tick_interval = tick_interval_ms / 1000.0  # Convert to seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
        # Ring buffers dla obu decków
        self.deck_buffers: Dict[str, deque] = {
            'deck_a': deque(maxlen=2048),  # Buffer dla deck A
            'deck_b': deque(maxlen=2048)   # Buffer dla deck B
        }
        
        # Callbacks dla wyników analiz
        self.spectrum_callbacks: Dict[str, Optional[Callable]] = {
            'deck_a': None,
            'deck_b': None
        }
        
        # Parametry analizy
        self.sample_rate = 48000
        self.target_rate = 12000  # Downsample do 12kHz
        self.fft_size = 1024
        self.num_bands = 48
        
        # Precomputed data
        self._setup_precomputed_data()
        
        # EMA smoothing
        self.ema_alpha = 0.3
        self.previous_spectrum: Dict[str, Optional[np.ndarray]] = {
            'deck_a': None,
            'deck_b': None
        }
        
        # Locks dla thread safety
        self.buffer_locks: Dict[str, threading.Lock] = {
            'deck_a': threading.Lock(),
            'deck_b': threading.Lock()
        }
        
        self.logger = logging.getLogger(__name__)
        
    def _setup_precomputed_data(self):
        """Precompute okno Hann i macierz log-binów"""
        # Hann window
        self.hann_window = np.hanning(self.fft_size)
        
        # Log-bin matrix dla 48 pasm
        self._create_log_bin_matrix()
        
        # Downsample ratio
        self.downsample_ratio = self.sample_rate // self.target_rate  # 4
        
    def _create_log_bin_matrix(self):
        """Tworzy macierz do log-binning spektrum"""
        # Częstotliwości dla FFT
        freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.target_rate)
        
        # Logarytmiczne pasma od 40Hz do 6kHz (Nyquist dla 12kHz)
        min_freq = 40.0
        max_freq = 6000.0
        
        # Logarytmiczne granice pasm
        log_min = np.log10(min_freq)
        log_max = np.log10(max_freq)
        band_edges = np.logspace(log_min, log_max, self.num_bands + 1)
        
        # Macierz mapowania
        self.log_bin_matrix = np.zeros((self.num_bands, len(freqs)))
        
        for i in range(self.num_bands):
            # Znajdź indeksy FFT dla tego pasma
            start_freq = band_edges[i]
            end_freq = band_edges[i + 1]
            
            start_idx = np.searchsorted(freqs, start_freq)
            end_idx = np.searchsorted(freqs, end_freq)
            
            if end_idx > start_idx:
                # Równomierne rozłożenie wagi
                weight = 1.0 / (end_idx - start_idx)
                self.log_bin_matrix[i, start_idx:end_idx] = weight
                
    def add_audio_data(self, deck_id: str, audio_data: np.ndarray):
        """Dodaje dane audio do ring buffera (wywołane z audio callback)"""
        if deck_id not in self.deck_buffers:
            return
            
        # Convert stereo to mono if needed
        if len(audio_data.shape) > 1:
            audio_mono = np.mean(audio_data, axis=1)
        else:
            audio_mono = audio_data
            
        # Dodaj do ring buffera (thread-safe)
        with self.buffer_locks[deck_id]:
            self.deck_buffers[deck_id].extend(audio_mono)
            
    def set_spectrum_callback(self, deck_id: str, callback: Callable):
        """Ustawia callback dla wyników spektrum"""
        if deck_id in self.spectrum_callbacks:
            self.spectrum_callbacks[deck_id] = callback
            
    def start(self):
        """Uruchamia worker thread"""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()
        self.logger.info(f"Spectrum worker started with {self.tick_interval*1000:.1f}ms tick")
        
    def stop(self):
        """Zatrzymuje worker thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        self.logger.info("Spectrum worker stopped")
        
    def _worker_loop(self):
        """Główna pętla worker thread"""
        while self.running:
            start_time = time.time()
            
            # Analizuj oba decki
            for deck_id in ['deck_a', 'deck_b']:
                try:
                    self._analyze_deck(deck_id)
                except Exception as e:
                    self.logger.error(f"Error analyzing {deck_id}: {e}")
                    
            # Oblicz czas do następnego tick
            elapsed = time.time() - start_time
            sleep_time = max(0, self.tick_interval - elapsed)
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                self.logger.warning(f"Worker tick overrun: {elapsed*1000:.1f}ms > {self.tick_interval*1000:.1f}ms")
                
    def _analyze_deck(self, deck_id: str):
        """Analizuje spektrum dla jednego decka"""
        callback = self.spectrum_callbacks[deck_id]
        if not callback:
            return
            
        # Pobierz dane z ring buffera
        with self.buffer_locks[deck_id]:
            if len(self.deck_buffers[deck_id]) < self.fft_size * self.downsample_ratio:
                # Nie ma wystarczająco danych
                return
                
            # Skopiuj dane (bez usuwania z buffera)
            buffer_data = list(self.deck_buffers[deck_id])
            
        # Konwertuj do numpy array
        audio_data = np.array(buffer_data[-self.fft_size * self.downsample_ratio:])
        
        # Downsample do 12kHz
        downsampled = audio_data[::self.downsample_ratio]
        
        if len(downsampled) < self.fft_size:
            return
            
        # Weź ostatnie fft_size próbek
        audio_chunk = downsampled[-self.fft_size:]
        
        # Zastosuj okno Hann
        windowed = audio_chunk * self.hann_window
        
        # rFFT
        fft_result = np.fft.rfft(windowed)
        magnitude = np.abs(fft_result)
        
        # Log-binning używając precomputed matrix
        spectrum = np.dot(self.log_bin_matrix, magnitude)
        
        # Konwersja do dB
        spectrum_db = 20 * np.log10(np.maximum(spectrum, 1e-10))
        
        # Normalizacja do 0-1
        min_db = -60.0
        max_db = 0.0
        normalized = np.clip((spectrum_db - min_db) / (max_db - min_db), 0.0, 1.0)
        
        # EMA smoothing
        if self.previous_spectrum[deck_id] is not None:
            smoothed = (self.ema_alpha * normalized + 
                       (1 - self.ema_alpha) * self.previous_spectrum[deck_id])
        else:
            smoothed = normalized
            
        self.previous_spectrum[deck_id] = smoothed
        
        # Wywołaj callback z wynikiem
        try:
            callback(smoothed)
        except Exception as e:
            self.logger.error(f"Error in spectrum callback for {deck_id}: {e}")
            
    def set_quality(self, quality: str):
        """Ustawia jakość spektrum (Low/High)"""
        if quality.lower() == 'low':
            self.num_bands = 32
            self.tick_interval = 0.060  # 60ms
        elif quality.lower() == 'high':
            self.num_bands = 64
            self.tick_interval = 0.040  # 40ms
        else:
            self.num_bands = 48
            self.tick_interval = 0.045  # 45ms (default)
            
        # Przebuduj macierz log-binów
        self._create_log_bin_matrix()
        
        # Reset previous spectrum
        self.previous_spectrum = {'deck_a': None, 'deck_b': None}
        
        self.logger.info(f"Spectrum quality set to {quality}: {self.num_bands} bands, {self.tick_interval*1000:.0f}ms tick")