"""Zoptymalizowany Spectrum Analyzer - zero FFT w audio callbacku, worker thread, precomputed matrices."""

import numpy as np
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QImage, QPixmap
from typing import Optional, Tuple
import threading
import time
from collections import deque
from enum import Enum


class SpectrumQuality(Enum):
    """Jakość analizy spektrum."""
    LOW = "low"      # 32 pasma, tick 60ms
    HIGH = "high"    # 64 pasma, tick 40ms


class SpectrumWorker(QThread):
    """Worker thread do analizy spektrum - wspólny dla obu decków."""
    
    spectrum_updated = Signal(str, np.ndarray)  # deck_id, spectrum_data
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.quality = SpectrumQuality.HIGH
        
        # Ring buffery dla obu decków
        self.ring_buffers = {
            'deck_a': deque(maxlen=8192),  # ~170ms przy 48kHz
            'deck_b': deque(maxlen=8192)
        }
        self.buffer_locks = {
            'deck_a': threading.Lock(),
            'deck_b': threading.Lock()
        }
        
        # Precomputed matrices i okna
        self._setup_precomputed_data()
        
    def _setup_precomputed_data(self):
        """Precompute wszystkich stałych - zero alokacji w pętli."""
        # Parametry analizy
        self.sample_rate = 48000
        self.target_rate = 12000  # Downsampling do 12kHz
        self.fft_size = 1024
        self.downsample_factor = self.sample_rate // self.target_rate  # 4
        
        # Okno Hann - precomputed
        self.hann_window = np.hanning(self.fft_size).astype(np.float32)
        
        # Częstotliwości FFT
        self.fft_freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.target_rate)
        
        # Log-binning matrices dla różnych jakości
        self._setup_log_binning_matrices()
        
        # Bufory robocze - prealokowane
        self.work_buffer = np.zeros(self.fft_size, dtype=np.float32)
        self.fft_buffer = np.zeros(self.fft_size // 2 + 1, dtype=np.complex64)
        
    def _setup_log_binning_matrices(self):
        """Precompute macierzy log-binning dla różnych jakości."""
        self.log_matrices = {}
        
        for quality in SpectrumQuality:
            if quality == SpectrumQuality.LOW:
                num_bands = 32
            else:  # HIGH
                num_bands = 64
                
            # Zakres częstotliwości: 40Hz - 6kHz (Nyquist dla 12kHz)
            min_freq = 40.0
            max_freq = min(6000.0, self.target_rate / 2)
            
            # Logarytmiczne pasma
            freq_bands = np.logspace(
                np.log10(min_freq), 
                np.log10(max_freq), 
                num_bands + 1
            )
            
            # Macierz mapowania FFT bins -> log bands
            matrix = np.zeros((num_bands, len(self.fft_freqs)), dtype=np.float32)
            
            for i in range(num_bands):
                freq_low = freq_bands[i]
                freq_high = freq_bands[i + 1]
                
                # Znajdź bins w tym paśmie
                mask = (self.fft_freqs >= freq_low) & (self.fft_freqs < freq_high)
                bin_count = np.sum(mask)
                
                if bin_count > 0:
                    matrix[i, mask] = 1.0 / bin_count  # Średnia
                    
            self.log_matrices[quality] = matrix
    
    def set_quality(self, quality: SpectrumQuality):
        """Ustawia jakość analizy."""
        self.quality = quality
        
    def add_audio_data(self, deck_id: str, audio_data: np.ndarray):
        """Dodaje audio data do ring buffera - SZYBKA operacja dla callbacku."""
        if audio_data.size == 0:
            return
            
        # Konwersja do mono
        if audio_data.ndim == 2:
            mono_data = np.mean(audio_data, axis=1)
        else:
            mono_data = audio_data
            
        # Downsampling przez prostą decymację (szybkie)
        downsampled = mono_data[::self.downsample_factor]
        
        # Dodaj do ring buffera
        with self.buffer_locks[deck_id]:
            self.ring_buffers[deck_id].extend(downsampled)
    
    def run(self):
        """Główna pętla worker thread."""
        self.running = True
        
        # Tick rate zależny od jakości
        if self.quality == SpectrumQuality.LOW:
            tick_ms = 60  # 60ms
        else:
            tick_ms = 40  # 40ms
            
        while self.running:
            start_time = time.perf_counter()
            
            # Analizuj oba decki
            for deck_id in ['deck_a', 'deck_b']:
                spectrum = self._analyze_deck(deck_id)
                if spectrum is not None:
                    self.spectrum_updated.emit(deck_id, spectrum)
            
            # Kontrola tick rate
            elapsed = (time.perf_counter() - start_time) * 1000
            sleep_time = max(0, tick_ms - elapsed)
            
            if sleep_time > 0:
                self.msleep(int(sleep_time))
                
    def _analyze_deck(self, deck_id: str) -> Optional[np.ndarray]:
        """Analizuje spektrum dla jednego decka."""
        # Pobierz dane z ring buffera
        with self.buffer_locks[deck_id]:
            if len(self.ring_buffers[deck_id]) < self.fft_size:
                return None  # Za mało danych
                
            # Skopiuj dane do work buffera
            data_list = list(self.ring_buffers[deck_id])[-self.fft_size:]
            self.work_buffer[:] = data_list
        
        # Zastosuj okno Hann
        windowed = self.work_buffer * self.hann_window
        
        # rFFT
        fft_result = np.fft.rfft(windowed)
        magnitude = np.abs(fft_result)
        
        # Konwersja do dB
        magnitude_db = 20 * np.log10(magnitude + 1e-10)
        
        # Log-binning przez precomputed matrix
        matrix = self.log_matrices[self.quality]
        spectrum_db = matrix @ magnitude_db
        
        # Normalizacja do 0-1 (-60dB do 0dB)
        spectrum_normalized = np.clip((spectrum_db + 60) / 60, 0, 1)
        
        return spectrum_normalized.astype(np.float32)
    
    def stop(self):
        """Zatrzymuje worker thread."""
        self.running = False
        self.wait()


class OptimizedSpectrumAnalyzer(QWidget):
    """Zoptymalizowany widget spektrum z offscreen rendering i EMA smoothing."""
    
    def __init__(self, deck_id: str, parent=None):
        super().__init__(parent)
        
        self.deck_id = deck_id
        self.quality = SpectrumQuality.HIGH
        
        # Dane spektrum z EMA smoothing
        self.spectrum_data = None
        self.smoothed_spectrum = None
        self.peak_hold = None
        self.ema_alpha = 0.3  # Współczynnik EMA
        self.peak_decay = 0.95
        
        # Offscreen rendering
        self.offscreen_image = None
        self.needs_redraw = True
        
        # Ustawienia wizualne
        self.setMinimumSize(120, 80)
        self.setMaximumSize(120, 80)
        
        # Timer UI - max 20 FPS
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start(50)  # 20 FPS
        
        # Kolory
        self.bg_color = QColor(20, 20, 20)
        self.bar_color = QColor(0, 255, 100)
        self.peak_color = QColor(255, 255, 0)
        self.grid_color = QColor(40, 40, 40)
        
    def set_quality(self, quality: SpectrumQuality):
        """Ustawia jakość analizy."""
        self.quality = quality
        
        # Reset danych przy zmianie jakości
        if quality == SpectrumQuality.LOW:
            num_bands = 32
        else:
            num_bands = 64
            
        self.spectrum_data = np.zeros(num_bands, dtype=np.float32)
        self.smoothed_spectrum = np.zeros(num_bands, dtype=np.float32)
        self.peak_hold = np.zeros(num_bands, dtype=np.float32)
        self.needs_redraw = True
        
    def update_spectrum_data(self, spectrum: np.ndarray):
        """Aktualizuje dane spektrum z EMA smoothing."""
        if self.smoothed_spectrum is None:
            # Pierwsza aktualizacja
            self.spectrum_data = spectrum.copy()
            self.smoothed_spectrum = spectrum.copy()
            self.peak_hold = spectrum.copy()
        else:
            # EMA smoothing
            self.spectrum_data = spectrum
            self.smoothed_spectrum = (
                self.ema_alpha * spectrum + 
                (1 - self.ema_alpha) * self.smoothed_spectrum
            )
            
            # Peak hold
            self.peak_hold = np.maximum(
                self.peak_hold * self.peak_decay, 
                self.smoothed_spectrum
            )
            
        self.needs_redraw = True
        
    def update_spectrum_data(self, spectrum: np.ndarray):
        """Aktualizuje dane spektrum z workera (thread-safe)"""
        if spectrum is None or len(spectrum) == 0:
            return
            
        # Kopiuj dane spektrum
        self.spectrum_data = spectrum.copy()
        
        # EMA smoothing
        if self.smoothed_spectrum is None:
            self.smoothed_spectrum = self.spectrum_data.copy()
        else:
            self.smoothed_spectrum = (
                self.ema_alpha * self.spectrum_data + 
                (1 - self.ema_alpha) * self.smoothed_spectrum
            )
        
        # Aktualizuj peak hold
        if self.peak_hold is None:
            self.peak_hold = self.smoothed_spectrum.copy()
        else:
            self.peak_hold = np.maximum(self.peak_hold, self.smoothed_spectrum)
            self.peak_hold *= self.peak_decay
        
        self.needs_redraw = True
        
    def update_ui(self):
        """Aktualizuje UI - max 20 FPS."""
        if self.needs_redraw:
            self._render_offscreen()
            self.update()
            self.needs_redraw = False
            
    def _render_offscreen(self):
        """Renderuje spektrum do offscreen QImage."""
        if self.smoothed_spectrum is None:
            return
            
        width = self.width()
        height = self.height()
        
        if (self.offscreen_image is None or 
            self.offscreen_image.size() != self.size()):
            self.offscreen_image = QImage(
                width, height, QImage.Format_RGB32
            )
            
        # Malowanie do offscreen image
        painter = QPainter(self.offscreen_image)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Tło
        painter.fillRect(0, 0, width, height, self.bg_color)
        
        # Siatka
        painter.setPen(QPen(self.grid_color, 1))
        for i in range(1, 4):
            y = height * i / 4
            painter.drawLine(0, y, width, y)
        
        # Słupki spektrum
        num_bands = len(self.smoothed_spectrum)
        bar_width = width / num_bands
        
        for i in range(num_bands):
            x = i * bar_width
            
            # Wysokość słupka
            bar_height = self.smoothed_spectrum[i] * height
            bar_y = height - bar_height
            
            # Kolor gradientowy
            if self.smoothed_spectrum[i] < 0.7:
                ratio = self.smoothed_spectrum[i] / 0.7
                color = QColor(int(ratio * 255), 255, 0)
            else:
                ratio = (self.smoothed_spectrum[i] - 0.7) / 0.3
                color = QColor(255, int(255 * (1 - ratio)), 0)
            
            painter.fillRect(
                int(x + 1), int(bar_y), 
                int(bar_width - 2), int(bar_height),
                QBrush(color)
            )
            
            # Peak hold
            peak_y = height - (self.peak_hold[i] * height)
            painter.setPen(QPen(self.peak_color, 2))
            painter.drawLine(
                int(x + 1), int(peak_y),
                int(x + bar_width - 1), int(peak_y)
            )
            
        painter.end()
        
    def paintEvent(self, event):
        """Maluje widget używając offscreen image."""
        if self.offscreen_image is not None:
            painter = QPainter(self)
            painter.drawImage(0, 0, self.offscreen_image)
            painter.end()
        else:
            # Fallback - czarne tło
            painter = QPainter(self)
            painter.fillRect(self.rect(), self.bg_color)
            painter.end()
            
    def reset(self):
        """Resetuje analyzer."""
        if self.smoothed_spectrum is not None:
            self.smoothed_spectrum.fill(0)
            self.peak_hold.fill(0)
            self.needs_redraw = True


# Globalny worker thread - singleton
_spectrum_worker = None

def get_spectrum_worker() -> SpectrumWorker:
    """Zwraca globalny worker thread (singleton)."""
    global _spectrum_worker
    if _spectrum_worker is None:
        _spectrum_worker = SpectrumWorker()
        _spectrum_worker.start()
    return _spectrum_worker

def cleanup_spectrum_worker():
    """Czyści globalny worker thread."""
    global _spectrum_worker
    if _spectrum_worker is not None:
        _spectrum_worker.stop()
        _spectrum_worker = None