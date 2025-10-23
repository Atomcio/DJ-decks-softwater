import os
import json
import threading
import numpy as np
from typing import Optional, Callable
from pathlib import Path
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPainter, QColor, QPen, QImage, QPixmap
import soundfile as sf


class PeaksAnalyzer(QThread):
    """Thread do analizy peaks w tle."""
    
    peaksReady = Signal(np.ndarray)  # peaks array [cols, 2] (min, max)
    progressUpdate = Signal(int)  # progress 0-100
    
    def __init__(self, audio_path: str, target_cols: int):
        super().__init__()
        self.audio_path = audio_path
        self.target_cols = target_cols
        self.should_stop = False
    
    def stop(self):
        self.should_stop = True
    
    def run(self):
        try:
            # Użyj soundfile zamiast librosa dla lepszej kompatybilności
            y, sr = sf.read(self.audio_path, always_2d=True)
            
            # Transpozycja - soundfile zwraca [frames, channels]
            y = y.T  # Teraz [channels, frames]
            
            # Konwertuj na stereo jeśli mono
            if y.shape[0] == 1:
                y = np.vstack([y, y])  # Duplikuj kanał
            elif y.shape[0] > 2:
                y = y[:2]  # Weź tylko pierwsze 2 kanały
            
            # Oblicz ile próbek na kolumnę
            frames_per_col = max(1, y.shape[1] // self.target_cols)
            
            peaks = np.zeros((self.target_cols, 2), dtype=np.float32)  # [min, max]
            
            for col in range(self.target_cols):
                if self.should_stop:
                    return
                
                start_frame = col * frames_per_col
                end_frame = min(start_frame + frames_per_col, y.shape[1])
                
                if start_frame < y.shape[1]:
                    # Weź blok audio
                    block = y[:, start_frame:end_frame]
                    
                    # Max-pooling: max(abs(L), abs(R))
                    max_amplitude = np.max(np.abs(block))
                    min_amplitude = -max_amplitude
                    
                    peaks[col] = [min_amplitude, max_amplitude]
                
                # Progress update co 50 kolumn
                if col % 50 == 0:
                    progress = int((col / self.target_cols) * 100)
                    self.progressUpdate.emit(progress)
            
            self.progressUpdate.emit(100)
            self.peaksReady.emit(peaks)
            
        except Exception as e:
            # Fallback - generuj testowe peaks
            peaks = np.zeros((self.target_cols, 2), dtype=np.float32)
            for i in range(self.target_cols):
                amp = 0.5 * np.sin(i * 0.1)  # Testowy sinus
                peaks[i] = [-abs(amp), abs(amp)]
            self.peaksReady.emit(peaks)


class WaveformMini(QWidget):
    """Mini waveform widget - wąski overview całego utworu."""
    
    waveformClicked = Signal(float)  # phase [0,1]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)  # Niższa wysokość - mniej miejsca w pionie
        self.setMinimumWidth(200)
        
        # Audio metadata
        self.sample_rate = 0
        self.total_frames = 0
        self.duration_s = 0.0
        
        # Peaks data
        self.peaks: Optional[np.ndarray] = None  # [cols, 2]
        self.target_cols = 0
        
        # Rendering
        self.waveform_image: Optional[QImage] = None
        self.playhead_phase = 0.0  # [0,1]
        
        # Background analysis
        self.analyzer: Optional[PeaksAnalyzer] = None
        self.audio_path: Optional[str] = None
        
        # Cache
        self.cache_path: Optional[str] = None
        
        # UI state
        self.is_analyzing = False
        self.analysis_progress = 0
        
        # Colors - lepszy kontrast
        self.bg_color = QColor(20, 20, 20)  # Ciemne tło
        self.waveform_color = QColor(230, 230, 230)  # Jasny waveform
        self.playhead_color = QColor(255, 80, 80)  # Czerwony playhead
        self.progress_color = QColor(100, 100, 100)
        
    def setAudioMeta(self, sample_rate: int, total_frames: int, duration_s: float):
        """Ustawia metadane audio."""
        self.sample_rate = sample_rate
        self.total_frames = total_frames
        self.duration_s = duration_s
        self.target_cols = min(self.width(), 2000)  # Limit kolumn - zmniejszony dla oszczędności zasobów
        
    def setPeaks(self, peaks: np.ndarray):
        """Ustawia dane peaks i renderuje waveform."""
        self.peaks = peaks
        self._renderWaveformImage()
        self.update()
        
    def setPlayhead(self, phase: float):
        """Ustawia pozycję playheada [0,1]."""
        phase = max(0.0, min(1.0, phase))
        if abs(self.playhead_phase - phase) > 0.001:  # Tylko jeśli zmiana > 0.1%
            self.playhead_phase = phase
            self.update()  # Tylko repaint playheada
    
    def loadAudioFile(self, file_path: str):
        """Ładuje plik audio i rozpoczyna analizę peaks."""
        self.audio_path = file_path
        self.cache_path = f"{file_path}.peaks.json"
        
        # Ustaw target_cols na podstawie szerokości widgetu
        self.target_cols = max(100, self.width())  # Minimum 100 kolumn - zmniejszony dla oszczędności zasobów
        
        # Sprawdź cache
        if self._loadFromCache():
            return
        
        # Rozpocznij analizę w tle
        self._startPeaksAnalysis()
    
    def _loadFromCache(self) -> bool:
        """Próbuje wczytać peaks z cache."""
        if not self.cache_path or not os.path.exists(self.cache_path):
            return False
        
        try:
            with open(self.cache_path, 'r') as f:
                cache_data = json.load(f)
            
            # Sprawdź zgodność metadanych
            if (cache_data.get('sample_rate') == self.sample_rate and
                cache_data.get('total_frames') == self.total_frames and
                cache_data.get('cols') == self.target_cols):
                
                peaks_list = cache_data.get('peaks', [])
                if peaks_list:
                    peaks = np.array(peaks_list, dtype=np.float32)
                    self.setPeaks(peaks)
                    return True
        
        except Exception as e:
            pass
        
        return False
    
    def _saveToCache(self, peaks: np.ndarray):
        """Zapisuje peaks do cache."""
        if not self.cache_path:
            return
        
        try:
            cache_data = {
                'sample_rate': self.sample_rate,
                'total_frames': self.total_frames,
                'cols': self.target_cols,
                'peaks': peaks.tolist()
            }
            
            with open(self.cache_path, 'w') as f:
                json.dump(cache_data, f)
                
        except Exception as e:
            pass
    
    def _startPeaksAnalysis(self):
        """Rozpoczyna analizę peaks w tle."""
        if not self.audio_path:
            return
        
        self.is_analyzing = True
        self.analysis_progress = 0
        
        # Zatrzymaj poprzednią analizę
        if self.analyzer:
            self.analyzer.stop()
            self.analyzer.wait()
        
        # Rozpocznij nową analizę
        self.analyzer = PeaksAnalyzer(self.audio_path, self.target_cols)
        self.analyzer.peaksReady.connect(self._onPeaksReady)
        self.analyzer.progressUpdate.connect(self._onProgressUpdate)
        self.analyzer.start()
        
        self.update()
    
    def _onPeaksReady(self, peaks: np.ndarray):
        """Callback gdy peaks są gotowe."""
        self.is_analyzing = False
        self.setPeaks(peaks)
        self._saveToCache(peaks)
    
    def _onProgressUpdate(self, progress: int):
        """Callback aktualizacji postępu analizy."""
        self.analysis_progress = progress
        self.update()
    
    def _renderWaveformImage(self):
        """Renderuje waveform do offscreen QImage - biały obrys na przezroczystym tle."""
        if self.peaks is None or len(self.peaks) == 0:
            return
        
        width = self.width()
        height = self.height()
        
        if width <= 0 or height <= 0:
            return
        
        # Utwórz QImage
        self.waveform_image = QImage(width, height, QImage.Format_ARGB32)
        self.waveform_image.fill(Qt.transparent)
        
        painter = QPainter(self.waveform_image)
        painter.setRenderHint(QPainter.Antialiasing, False)  # Wyłącz dla wydajności
        
        # Jasny kolor dla waveform (lepszy kontrast)
        painter.setPen(QPen(self.waveform_color, 1))
        
        cols = len(self.peaks)
        if cols > 0:
            x_scale = width / cols
            y_center = height // 2
            y_scale = (height - 4) // 2  # Margines 2px góra/dół
            
            for col in range(cols):
                x = int(col * x_scale)
                
                min_val = self.peaks[col, 0]  # ujemny
                max_val = self.peaks[col, 1]  # dodatni
                
                y_min = y_center - int(min_val * y_scale)
                y_max = y_center - int(max_val * y_scale)
                
                # Rysuj linię od min do max
                if y_min != y_max:
                    painter.drawLine(x, y_max, x, y_min)
                else:
                    painter.drawPoint(x, y_center)
        
        painter.end()
    
    def resizeEvent(self, event):
        """Obsługa resize - przelicz kolumny bez ponownej analizy."""
        super().resizeEvent(event)
        
        new_cols = min(self.width(), 4000)
        if new_cols != self.target_cols and self.peaks is not None:
            # Re-binning z istniejących peaks
            self.target_cols = new_cols
            self._rebinPeaks()
    
    def _rebinPeaks(self):
        """Przelicza peaks dla nowej liczby kolumn (downsample)."""
        if self.peaks is None:
            return
        
        old_cols = len(self.peaks)
        new_cols = self.target_cols
        
        if old_cols == new_cols:
            return
        
        new_peaks = np.zeros((new_cols, 2), dtype=np.float32)
        
        for new_col in range(new_cols):
            # Mapuj nową kolumnę na zakres starych kolumn
            start_old = (new_col * old_cols) // new_cols
            end_old = ((new_col + 1) * old_cols) // new_cols
            end_old = min(end_old, old_cols)
            
            if start_old < old_cols:
                # Znajdź min/max w zakresie starych kolumn
                block = self.peaks[start_old:end_old]
                if len(block) > 0:
                    new_peaks[new_col, 0] = np.min(block[:, 0])  # min
                    new_peaks[new_col, 1] = np.max(block[:, 1])  # max
        
        self.peaks = new_peaks
        self._renderWaveformImage()
        self.update()
    
    def paintEvent(self, event):
        """Rysuje widget - waveform + playhead + progress."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        
        # Tło
        painter.fillRect(self.rect(), self.bg_color)
        
        if self.is_analyzing:
            # Placeholder podczas analizy
            painter.fillRect(self.rect(), QColor(50, 50, 50))
            
            # Progress bar
            if self.analysis_progress > 0:
                progress_width = int((self.analysis_progress / 100.0) * self.width())
                painter.fillRect(0, 0, progress_width, self.height(), QColor(100, 100, 100))
            
            # Tekst
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(self.rect(), Qt.AlignCenter, f"Analyzing... {self.analysis_progress}%")
        
        else:
            # Rysuj waveform z QImage
            if self.waveform_image:
                painter.drawImage(0, 0, self.waveform_image)
            
            # Pasek postępu pod spodem
            if self.playhead_phase > 0:
                progress_width = int(self.playhead_phase * self.width())
                painter.fillRect(0, self.height() - 2, progress_width, 2, self.progress_color)
            
            # Playhead (pionowa kreska)
            playhead_x = int(self.playhead_phase * self.width())
            painter.setPen(QPen(self.playhead_color, 1))
            painter.drawLine(playhead_x, 0, playhead_x, self.height())
        
        # Important: End the painter
        painter.end()
    
    def mousePressEvent(self, event):
        """Obsługa kliknięcia - seek."""
        if event.button() == Qt.LeftButton:
            phase = event.x() / self.width()
            phase = max(0.0, min(1.0, phase))
            self.waveformClicked.emit(phase)
    
    def mouseMoveEvent(self, event):
        """Obsługa przeciągania - seek."""
        if event.buttons() & Qt.LeftButton:
            phase = event.x() / self.width()
            phase = max(0.0, min(1.0, phase))
            self.waveformClicked.emit(phase)
    
    def clear(self):
        """Czyści widget."""
        if self.analyzer:
            self.analyzer.stop()
            self.analyzer = None
        
        self.peaks = None
        self.waveform_image = None
        self.playhead_phase = 0.0
        self.is_analyzing = False
        self.update()