"""Time-stretch i pitch-shift engine z obsługą Key Lock."""

import numpy as np
import threading
import time
from typing import Optional
import logging

log = logging.getLogger(__name__)

try:
    import pyrubberband as pyrb
    PYRUBBERBAND_AVAILABLE = True
except ImportError:
    PYRUBBERBAND_AVAILABLE = False
    log.warning("pyrubberband nie jest dostępny - używam fallback playbackRate")


class TimeStretchEngine:
    """Engine do time-stretch i pitch-shift z obsługą Key Lock."""
    
    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.key_lock_enabled = False
        self.tempo_ratio = 1.0
        self.pitch_ratio = 1.0
        
        # Parametry dla Rubber Band - zoptymalizowane dla deterministyczności
        self.frame_size = 1024  # Większy bufor dla stabilności
        self.hop_size = 512     # overlap 50%
        
        # Buforowanie dla deterministycznego przetwarzania
        self.input_buffer = np.array([], dtype=np.float32)
        self.output_buffer = np.array([], dtype=np.float32)
        self.buffer_size = 4096  # Rozmiar bufora wewnętrznego
        
        # Parametry deterministyczne
        self.deterministic_mode = True
        self.stable_processing = True
        self.min_chunk_size = 256  # Minimalny rozmiar chunka do przetwarzania
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Sprawdź dostępność
        self.high_quality_available = PYRUBBERBAND_AVAILABLE
        
        if not self.high_quality_available:
            log.warning("High-quality time-stretch unavailable; using playbackRate (no Key Lock)")
    
    def set_tempo(self, ratio: float):
        """Ustaw tempo ratio (1.0 = normalna prędkość)."""
        with self.lock:
            self.tempo_ratio = max(0.5, min(2.0, ratio))
    
    def set_key_lock(self, enabled: bool):
        """Włącz/wyłącz Key Lock."""
        with self.lock:
            self.key_lock_enabled = enabled and self.high_quality_available
    
    def get_tempo(self) -> float:
        """Pobierz aktualne tempo."""
        return self.tempo_ratio
    
    def is_key_lock_enabled(self) -> bool:
        """Sprawdź czy Key Lock jest włączony."""
        return self.key_lock_enabled
    
    def is_high_quality_available(self) -> bool:
        """Sprawdź czy high-quality time-stretch jest dostępny."""
        return self.high_quality_available
    
    def process_audio(self, audio_chunk: np.ndarray) -> np.ndarray:
        """Przetwarza chunk audio z time-stretch/pitch-shift.
        
        Args:
            audio_chunk: Audio data (frames, channels) lub (frames,) dla mono
            
        Returns:
            Przetworzony audio chunk
        """
        if audio_chunk is None or len(audio_chunk) == 0:
            return audio_chunk
        
        with self.lock:
            tempo_ratio = self.tempo_ratio
            key_lock = self.key_lock_enabled
        
        # Jeśli tempo = 1.0, zwróć oryginalny chunk
        if abs(tempo_ratio - 1.0) < 0.001:
            return audio_chunk
            
        # Deterministyczne przetwarzanie - użyj buforowania dla stabilności
        if self.deterministic_mode and len(audio_chunk) < self.min_chunk_size:
            return self._process_with_buffering(audio_chunk, tempo_ratio, key_lock)
        
        try:
            if key_lock and self.high_quality_available:
                # Key Lock ON → time-stretch bez zmiany pitch
                return self._process_with_rubberband(audio_chunk, tempo_ratio, 1.0)
            else:
                # Key Lock OFF → prosty linear resample (pitch idzie z tempem)
                return self._simple_resample(audio_chunk, tempo_ratio)
                
        except Exception as e:
            log.error(f"Błąd przetwarzania audio: {e}")
            # Fallback do prostego resample
            return self._simple_resample(audio_chunk, tempo_ratio)
    
    def _process_with_rubberband(self, audio_chunk: np.ndarray, tempo_ratio: float, pitch_ratio: float) -> np.ndarray:
        """Przetwarza audio używając Rubber Band (high quality)."""
        if not PYRUBBERBAND_AVAILABLE:
            return self._simple_resample(audio_chunk, tempo_ratio)
        
        try:
            # Konwertuj do formatu wymaganego przez pyrubberband
            if audio_chunk.ndim == 1:
                # Mono → stereo
                audio_stereo = np.column_stack([audio_chunk, audio_chunk])
            else:
                audio_stereo = audio_chunk
            
            # Upewnij się że mamy float32
            audio_stereo = audio_stereo.astype(np.float32)
            
            # Parametry Rubber Band - zoptymalizowane dla deterministyczności
            options = pyrb.default_options()
            options['time_ratio'] = 1.0 / tempo_ratio  # pyrubberband używa odwrotności
            options['pitch_ratio'] = pitch_ratio
            
            # Deterministyczne ustawienia dla stabilnej synchronizacji
            if self.deterministic_mode:
                options['engine'] = 'finer'  # Najwyższa jakość
                options['realtime'] = False   # Nie real-time dla lepszej jakości
                options['threading'] = False  # Wyłącz threading dla deterministyczności
            
            # Przetwórz audio
            processed = pyrb.time_stretch_and_pitch_shift(
                audio_stereo.T,  # pyrubberband oczekuje (channels, frames)
                self.sample_rate,
                **options
            )
            
            # Konwertuj z powrotem do (frames, channels)
            processed = processed.T
            
            # Jeśli oryginalny był mono, zwróć mono
            if audio_chunk.ndim == 1:
                processed = processed[:, 0]  # weź tylko lewy kanał
            
            return processed.astype(np.float32)
            
        except Exception as e:
            log.error(f"Błąd Rubber Band: {e}")
            return self._simple_resample(audio_chunk, tempo_ratio)
    
    def _simple_resample(self, audio_chunk: np.ndarray, ratio: float) -> np.ndarray:
        """Prosty linear resample (fallback)."""
        try:
            input_len = len(audio_chunk)
            output_len = int(input_len / ratio)
            
            if output_len <= 0:
                return audio_chunk
            
            # Indeksy dla interpolacji
            indices = np.linspace(0, input_len - 1, output_len)
            
            # Dla stereo
            if audio_chunk.ndim == 2 and audio_chunk.shape[1] == 2:
                left = np.interp(indices, np.arange(input_len), audio_chunk[:, 0])
                right = np.interp(indices, np.arange(input_len), audio_chunk[:, 1])
                return np.column_stack((left, right)).astype(np.float32)
            else:
                # Mono
                resampled = np.interp(indices, np.arange(input_len), audio_chunk.flatten())
                return resampled.astype(np.float32)
                
        except Exception as e:
            log.error(f"Błąd prostego resamplingu: {e}")
            return audio_chunk
    
    def _process_with_buffering(self, audio_chunk: np.ndarray, tempo_ratio: float, key_lock: bool) -> np.ndarray:
        """Przetwarza małe chunki audio z buforowaniem dla deterministyczności.
        
        Args:
            audio_chunk: Mały chunk audio
            tempo_ratio: Współczynnik tempo
            key_lock: Czy Key Lock jest włączony
            
        Returns:
            Przetworzony chunk audio
        """
        try:
            # Dodaj chunk do bufora wejściowego
            if audio_chunk.ndim == 1:
                self.input_buffer = np.concatenate([self.input_buffer, audio_chunk])
            else:
                if len(self.input_buffer) == 0:
                    self.input_buffer = audio_chunk.copy()
                else:
                    self.input_buffer = np.vstack([self.input_buffer, audio_chunk])
            
            # Sprawdź czy mamy wystarczająco danych do przetworzenia
            if len(self.input_buffer) < self.min_chunk_size * 2:
                # Za mało danych - zwróć pusty chunk odpowiedniej długości
                expected_output_len = int(len(audio_chunk) / tempo_ratio)
                if audio_chunk.ndim == 1:
                    return np.zeros(expected_output_len, dtype=np.float32)
                else:
                    return np.zeros((expected_output_len, audio_chunk.shape[1]), dtype=np.float32)
            
            # Przetwórz buforowane dane
            if key_lock and self.high_quality_available:
                processed = self._process_with_rubberband(self.input_buffer, tempo_ratio, 1.0)
            else:
                processed = self._simple_resample(self.input_buffer, tempo_ratio)
            
            # Oblicz ile danych zwrócić
            expected_output_len = int(len(audio_chunk) / tempo_ratio)
            
            if len(processed) >= expected_output_len:
                # Mamy wystarczająco danych
                output_chunk = processed[:expected_output_len]
                
                # Zaktualizuj bufory
                remaining_input = int(len(audio_chunk))
                if len(self.input_buffer) > remaining_input:
                    self.input_buffer = self.input_buffer[remaining_input:]
                else:
                    self.input_buffer = np.array([], dtype=np.float32)
                
                return output_chunk
            else:
                # Za mało danych wyjściowych - zwróć co mamy i wyczyść bufor
                self.input_buffer = np.array([], dtype=np.float32)
                return processed
                
        except Exception as e:
            log.error(f"Błąd buforowanego przetwarzania: {e}")
            # Fallback - wyczyść bufory i użyj prostego przetwarzania
            self.input_buffer = np.array([], dtype=np.float32)
            if key_lock and self.high_quality_available:
                return self._process_with_rubberband(audio_chunk, tempo_ratio, 1.0)
            else:
                return self._simple_resample(audio_chunk, tempo_ratio)
    
    def reset_buffers(self):
        """Resetuj bufory wewnętrzne - użyj przy zmianie utworu lub dużych skokach."""
        with self.lock:
            self.input_buffer = np.array([], dtype=np.float32)
            self.output_buffer = np.array([], dtype=np.float32)
    
    def get_status_info(self) -> dict:
        """Zwraca informacje o statusie engine."""
        return {
            'high_quality_available': self.high_quality_available,
            'key_lock_enabled': self.key_lock_enabled,
            'tempo_ratio': self.tempo_ratio,
            'pitch_ratio': self.pitch_ratio,
            'engine_type': 'rubberband' if self.high_quality_available else 'linear_resample'
        }