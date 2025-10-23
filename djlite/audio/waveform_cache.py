"""WaveformCache - system downsamplingu audio do efektywnego rysowania waveform."""

import numpy as np
from typing import Tuple, Optional
import logging

log = logging.getLogger(__name__)


class WaveformCache:
    """
    Cache dla waveform z downsamplingiem do min/max peaks.
    Optymalizuje rysowanie przez redukcję ilości danych przy zachowaniu kształtu.
    """
    
    def __init__(self, y_mono: np.ndarray, sr: int, block_size: int = 256):
        """
        Args:
            y_mono: Mono audio data (1D numpy array)
            sr: Sample rate
            block_size: Samples per peak bin (dobierz pod zoom level)
        """
        self.sr = sr
        self.y = y_mono.astype(np.float32)
        self.block = block_size  # samples per peak bin
        self.duration = len(self.y) / sr
        
        # Cache dla różnych poziomów zoom
        self.min_peaks: Optional[np.ndarray] = None
        self.max_peaks: Optional[np.ndarray] = None
        
        self._build_peaks()
        
        log.info(f"WaveformCache created: {len(self.y)} samples -> {len(self.min_peaks)} peaks (block={self.block})")

    def _build_peaks(self):
        """Buduje cache min/max peaks dla całego audio."""
        n = len(self.y)
        
        # Pad do wielokrotności block_size
        pad = (-n) % self.block
        if pad:
            self.y = np.pad(self.y, (0, pad), mode='constant', constant_values=0)
            n = len(self.y)
        
        # Reshape do bloków i oblicz min/max
        y2 = self.y.reshape(-1, self.block)
        self.min_peaks = y2.min(axis=1)
        self.max_peaks = y2.max(axis=1)
        
        log.debug(f"Built peaks: {len(self.min_peaks)} bins from {n} samples")

    def sample_to_bin(self, sample_idx: int) -> int:
        """Konwertuje indeks sampla na indeks bin-a w cache."""
        return max(0, min(len(self.min_peaks) - 1, sample_idx // self.block))

    def time_to_bin(self, time_sec: float) -> int:
        """Konwertuje czas na indeks bin-a w cache."""
        sample_idx = int(time_sec * self.sr)
        return self.sample_to_bin(sample_idx)

    def bin_to_time(self, bin_idx: int) -> float:
        """Konwertuje indeks bin-a na czas w sekundach."""
        sample_idx = bin_idx * self.block
        return sample_idx / self.sr

    def bins_range_from_time_span(self, t0: float, t1: float) -> Tuple[int, int]:
        """Zwraca zakres bin-ów dla podanego przedziału czasowego."""
        s0 = int(t0 * self.sr)
        s1 = int(t1 * self.sr)
        bin0 = self.sample_to_bin(s0)
        bin1 = self.sample_to_bin(s1)
        return bin0, bin1

    def get_peaks_for_time_range(self, t0: float, t1: float) -> Tuple[np.ndarray, np.ndarray]:
        """Zwraca min/max peaks dla podanego zakresu czasowego."""
        bin0, bin1 = self.bins_range_from_time_span(t0, t1)
        
        # Zapewnij że bin1 > bin0
        if bin1 <= bin0:
            bin1 = bin0 + 1
            
        # Ogranicz do dostępnych danych
        bin0 = max(0, bin0)
        bin1 = min(len(self.min_peaks), bin1)
        
        return self.min_peaks[bin0:bin1], self.max_peaks[bin0:bin1]

    def get_peaks_for_bins(self, bin0: int, bin1: int) -> Tuple[np.ndarray, np.ndarray]:
        """Zwraca min/max peaks dla podanego zakresu bin-ów."""
        bin0 = max(0, bin0)
        bin1 = min(len(self.min_peaks), bin1)
        
        if bin1 <= bin0:
            return np.array([]), np.array([])
            
        return self.min_peaks[bin0:bin1], self.max_peaks[bin0:bin1]

    def get_rms_for_time_range(self, t0: float, t1: float, num_points: int = 100) -> np.ndarray:
        """Zwraca RMS values dla podanego zakresu czasowego (do spectrum/energy display)."""
        s0 = int(t0 * self.sr)
        s1 = int(t1 * self.sr)
        
        # Ogranicz do dostępnych danych
        s0 = max(0, s0)
        s1 = min(len(self.y), s1)
        
        if s1 <= s0:
            return np.zeros(num_points)
            
        # Pobierz fragment audio
        audio_segment = self.y[s0:s1]
        
        # Podziel na bloki dla RMS
        block_size = max(1, len(audio_segment) // num_points)
        
        if block_size == 1:
            return np.abs(audio_segment[:num_points])
            
        # Pad i reshape
        pad_len = (-len(audio_segment)) % block_size
        if pad_len:
            audio_segment = np.pad(audio_segment, (0, pad_len))
            
        blocks = audio_segment.reshape(-1, block_size)
        rms_values = np.sqrt(np.mean(blocks ** 2, axis=1))
        
        # Ogranicz do num_points
        return rms_values[:num_points]

    def get_info(self) -> dict:
        """Zwraca informacje o cache."""
        return {
            'sample_rate': self.sr,
            'duration': self.duration,
            'total_samples': len(self.y),
            'block_size': self.block,
            'num_peaks': len(self.min_peaks) if self.min_peaks is not None else 0,
            'compression_ratio': len(self.y) / len(self.min_peaks) if self.min_peaks is not None else 0
        }

    def rebuild_with_block_size(self, new_block_size: int):
        """Przebudowuje cache z nowym rozmiarem bloku (dla różnych poziomów zoom)."""
        if new_block_size != self.block:
            self.block = new_block_size
            self._build_peaks()
            log.info(f"WaveformCache rebuilt with block_size={new_block_size}")

    @classmethod
    def from_stereo(cls, y_stereo: np.ndarray, sr: int, block_size: int = 256, channel: str = 'mix'):
        """Tworzy WaveformCache z audio stereo.
        
        Args:
            y_stereo: Stereo audio data (2D numpy array)
            sr: Sample rate
            block_size: Samples per peak bin
            channel: 'left', 'right', 'mix' (default)
        """
        if len(y_stereo.shape) == 1:
            # Już mono
            y_mono = y_stereo
        elif y_stereo.shape[1] == 1:
            # Mono w formacie 2D
            y_mono = y_stereo[:, 0]
        else:
            # Stereo -> mono
            if channel == 'left':
                y_mono = y_stereo[:, 0]
            elif channel == 'right':
                y_mono = y_stereo[:, 1]
            else:  # 'mix'
                y_mono = np.mean(y_stereo, axis=1)
                
        return cls(y_mono, sr, block_size)