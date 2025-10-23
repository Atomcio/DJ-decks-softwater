"""Spectrum Analyzer widget dla DJ Lite - analiza spektrum w czasie rzeczywistym."""

import numpy as np
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QBrush, QPen
from typing import Optional
import threading


class SpectrumAnalyzer(QWidget):
    """Widget do wyświetlania spektrum audio w czasie rzeczywistym."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Konfiguracja analizy spektrum
        self.fft_size = 512  # Rozmiar FFT
        self.sample_rate = 48000
        self.num_bands = 32  # Liczba pasm do wyświetlenia
        
        # Bufory audio
        self.audio_buffer = np.zeros(self.fft_size, dtype=np.float32)
        self.buffer_lock = threading.Lock()
        
        # Dane spektrum
        self.spectrum_data = np.zeros(self.num_bands, dtype=np.float32)
        self.peak_hold = np.zeros(self.num_bands, dtype=np.float32)
        self.peak_decay = 0.95  # Szybkość opadania peak hold
        
        # Częstotliwości pasm (logarytmiczne)
        self.freq_bands = self._calculate_freq_bands()
        
        # Ustawienia wizualne
        self.setMinimumSize(120, 80)
        self.setMaximumSize(120, 80)
        
        # Timer do odświeżania
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_spectrum)
        self.update_timer.start(50)  # 20 FPS
        
        # Kolory
        self.bg_color = QColor(20, 20, 20)
        self.bar_color = QColor(0, 255, 100)  # Zielony
        self.peak_color = QColor(255, 255, 0)  # Żółty
        self.grid_color = QColor(40, 40, 40)
        
    def _calculate_freq_bands(self):
        """Oblicza częstotliwości pasm w skali logarytmicznej - zakres 40Hz-15kHz."""
        # Zakres częstotliwości: 40 Hz - 15 kHz (szerszy zakres basów, ograniczone wysokie)
        min_freq = 40.0
        max_freq = min(15000.0, self.sample_rate / 2)
        
        # Logarytmiczna skala częstotliwości z większym naciskiem na średnie/wysokie
        freq_bands = np.logspace(
            np.log10(min_freq), 
            np.log10(max_freq), 
            self.num_bands + 1
        )
        
        return freq_bands
    
    def add_audio_data(self, audio_data: np.ndarray):
        """Dodaje nowe dane audio do analizy (stereo -> mono)."""
        if audio_data.size == 0:
            return
            
        # Konwersja do mono jeśli stereo
        if audio_data.ndim == 2:
            mono_data = np.mean(audio_data, axis=1)
        else:
            mono_data = audio_data
            
        with self.buffer_lock:
            # Przesuń bufor i dodaj nowe dane
            samples_to_add = min(len(mono_data), self.fft_size)
            
            if samples_to_add < self.fft_size:
                # Przesuń istniejące dane
                self.audio_buffer[:-samples_to_add] = self.audio_buffer[samples_to_add:]
                self.audio_buffer[-samples_to_add:] = mono_data[-samples_to_add:]
            else:
                # Zastąp cały bufor
                self.audio_buffer = mono_data[-self.fft_size:].copy()
    
    def update_spectrum(self):
        """Aktualizuje dane spektrum i odświeża widget."""
        with self.buffer_lock:
            if np.sum(np.abs(self.audio_buffer)) < 1e-6:
                # Brak sygnału - wyczyść spektrum
                self.spectrum_data.fill(0)
                self.update()
                return
                
            # Zastosuj okno Hanning
            windowed = self.audio_buffer * np.hanning(self.fft_size)
            
            # FFT
            fft_data = np.fft.rfft(windowed)
            magnitude = np.abs(fft_data)
            
            # Konwersja do dB
            magnitude_db = 20 * np.log10(magnitude + 1e-10)
            
            # Mapowanie na pasma częstotliwości
            freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.sample_rate)
            
            for i in range(self.num_bands):
                # Znajdź indeksy dla tego pasma
                freq_low = self.freq_bands[i]
                freq_high = self.freq_bands[i + 1]
                
                mask = (freqs >= freq_low) & (freqs < freq_high)
                if np.any(mask):
                    # Średnia wartość w paśmie
                    band_value = np.mean(magnitude_db[mask])
                    # Normalizacja do zakresu 0-1
                    normalized = max(0, (band_value + 60) / 60)  # -60dB do 0dB
                    self.spectrum_data[i] = min(1.0, normalized)
                else:
                    self.spectrum_data[i] = 0
            
            # Aktualizuj peak hold
            self.peak_hold = np.maximum(self.peak_hold * self.peak_decay, self.spectrum_data)
        
        self.update()
    
    def paintEvent(self, event):
        """Rysuje spektrum analyzer."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Tło
        painter.fillRect(self.rect(), self.bg_color)
        
        # Siatka
        painter.setPen(QPen(self.grid_color, 1))
        width = self.width()
        height = self.height()
        
        # Linie poziome (poziomy dB)
        for i in range(1, 4):
            y = height * i / 4
            painter.drawLine(0, y, width, y)
        
        # Słupki spektrum
        bar_width = width / self.num_bands
        
        for i in range(self.num_bands):
            x = i * bar_width
            
            # Wysokość słupka (odwrócona - 0 na dole)
            bar_height = self.spectrum_data[i] * height
            bar_y = height - bar_height
            
            # Kolor słupka (gradient zielony -> żółty -> czerwony)
            if self.spectrum_data[i] < 0.7:
                # Zielony do żółtego
                ratio = self.spectrum_data[i] / 0.7
                color = QColor(
                    int(ratio * 255),
                    255,
                    0
                )
            else:
                # Żółty do czerwonego
                ratio = (self.spectrum_data[i] - 0.7) / 0.3
                color = QColor(
                    255,
                    int(255 * (1 - ratio)),
                    0
                )
            
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
        
        # Important: End the painter
        painter.end()
    
    def reset(self):
        """Resetuje analyzer."""
        with self.buffer_lock:
            self.audio_buffer.fill(0)
            self.spectrum_data.fill(0)
            self.peak_hold.fill(0)
        self.update()
    
    def set_enabled(self, enabled: bool):
        """Włącza/wyłącza analyzer."""
        if enabled:
            self.update_timer.start(50)
        else:
            self.update_timer.stop()
            self.reset()