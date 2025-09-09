"""Prosty 3-pasmowy equalizer (Hi/Mid/Low) używający filtrów IIR biquad."""

import numpy as np
from scipy import signal
from typing import Tuple
import threading


class BiquadFilter:
    """Pojedynczy filtr biquad (IIR drugiego rzędu)."""
    
    def __init__(self, b_coeffs: np.ndarray, a_coeffs: np.ndarray):
        self.b = b_coeffs  # współczynniki licznika
        self.a = a_coeffs  # współczynniki mianownika
        
        # Stan filtru (opóźnienia)
        self.x_delay = np.zeros(2)  # x[n-1], x[n-2]
        self.y_delay = np.zeros(2)  # y[n-1], y[n-2]
    
    def process(self, x: float) -> float:
        """Przetwarza pojedynczą próbkę."""
        # Równanie różnicowe filtru biquad:
        # y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] - a1*y[n-1] - a2*y[n-2]
        
        y = (self.b[0] * x + 
             self.b[1] * self.x_delay[0] + 
             self.b[2] * self.x_delay[1] - 
             self.a[1] * self.y_delay[0] - 
             self.a[2] * self.y_delay[1])
        
        # Aktualizuj opóźnienia
        self.x_delay[1] = self.x_delay[0]
        self.x_delay[0] = x
        self.y_delay[1] = self.y_delay[0]
        self.y_delay[0] = y
        
        return y
    
    def process_block(self, audio_block: np.ndarray) -> np.ndarray:
        """Przetwarza blok audio (stereo)."""
        output = np.zeros_like(audio_block)
        
        for i in range(len(audio_block)):
            # Przetwarzaj każdy kanał osobno
            for ch in range(audio_block.shape[1]):
                output[i, ch] = self.process(audio_block[i, ch])
        
        return output
    
    def reset(self):
        """Resetuje stan filtru."""
        self.x_delay.fill(0)
        self.y_delay.fill(0)


class ThreeBandEQ:
    """3-pasmowy equalizer (Low, Mid, High) z kontrolą gain dla każdego pasma."""
    
    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        
        # Częstotliwości graniczne
        self.low_freq = 250.0    # Hz - granica low/mid
        self.high_freq = 4000.0  # Hz - granica mid/high
        
        # Gain dla każdego pasma (-20dB do +20dB)
        self.low_gain = 0.0   # dB
        self.mid_gain = 0.0   # dB  
        self.high_gain = 0.0  # dB
        
        # Cache dla współczynników filtrów
        self._filter_cache = {}
        self._cache_valid = False
        
        # Filtry dla każdego kanału (stereo)
        self._create_filters()
    
    def _create_filters(self):
        """Tworzy filtry dla każdego pasma."""
        nyquist = self.sample_rate / 2
        
        # Low pass filter (pasmo niskie)
        low_norm_freq = self.low_freq / nyquist
        b_low, a_low = signal.butter(2, low_norm_freq, btype='low')
        
        # Band pass filter (pasmo średnie)
        mid_low_norm = self.low_freq / nyquist
        mid_high_norm = self.high_freq / nyquist
        b_mid, a_mid = signal.butter(2, [mid_low_norm, mid_high_norm], btype='band')
        
        # High pass filter (pasmo wysokie)
        high_norm_freq = self.high_freq / nyquist
        b_high, a_high = signal.butter(2, high_norm_freq, btype='high')
        
        # Utwórz filtry dla lewego i prawego kanału
        self.low_filter_L = BiquadFilter(b_low, a_low)
        self.low_filter_R = BiquadFilter(b_low, a_low)
        
        self.mid_filter_L = BiquadFilter(b_mid, a_mid)
        self.mid_filter_R = BiquadFilter(b_mid, a_mid)
        
        self.high_filter_L = BiquadFilter(b_high, a_high)
        self.high_filter_R = BiquadFilter(b_high, a_high)
    
    def set_low_gain(self, gain_db: float):
        """Ustawia wzmocnienie pasma niskiego (-20 do +20 dB)."""
        new_gain = max(-20.0, min(20.0, gain_db))
        if new_gain != self.low_gain:
            self.low_gain = new_gain
            self._cache_valid = False
    
    def set_mid_gain(self, gain_db: float):
        """Ustawia wzmocnienie pasma średniego (-20 do +20 dB)."""
        new_gain = max(-20.0, min(20.0, gain_db))
        if new_gain != self.mid_gain:
            self.mid_gain = new_gain
            self._cache_valid = False
    
    def set_high_gain(self, gain_db: float):
        """Ustawia wzmocnienie pasma wysokiego (-20 do +20 dB)."""
        new_gain = max(-20.0, min(20.0, gain_db))
        if new_gain != self.high_gain:
            self.high_gain = new_gain
            self._cache_valid = False
    
    def _db_to_linear(self, db: float) -> float:
        """Konwertuje dB na współczynnik liniowy."""
        return 10.0 ** (db / 20.0)
    
    def process(self, audio_block: np.ndarray) -> np.ndarray:
        """Przetwarza blok audio przez equalizer - zoptymalizowane."""
        if len(audio_block.shape) != 2 or audio_block.shape[1] != 2:
            raise ValueError("Audio musi być stereo (shape: [samples, 2])")
        
        # Sprawdź czy wszystkie gainy są zero (bypass)
        if self.low_gain == 0.0 and self.mid_gain == 0.0 and self.high_gain == 0.0:
            return audio_block
        
        # Przefiltruj każde pasmo
        low_output = np.zeros_like(audio_block)
        mid_output = np.zeros_like(audio_block)
        high_output = np.zeros_like(audio_block)
        
        # Przetwarzaj próbka po próbce dla każdego kanału
        for i in range(len(audio_block)):
            # Kanał lewy (0)
            low_output[i, 0] = self.low_filter_L.process(audio_block[i, 0])
            mid_output[i, 0] = self.mid_filter_L.process(audio_block[i, 0])
            high_output[i, 0] = self.high_filter_L.process(audio_block[i, 0])
            
            # Kanał prawy (1)
            low_output[i, 1] = self.low_filter_R.process(audio_block[i, 1])
            mid_output[i, 1] = self.mid_filter_R.process(audio_block[i, 1])
            high_output[i, 1] = self.high_filter_R.process(audio_block[i, 1])
        
        # Zastosuj wzmocnienia
        low_gain_linear = self._db_to_linear(self.low_gain)
        mid_gain_linear = self._db_to_linear(self.mid_gain)
        high_gain_linear = self._db_to_linear(self.high_gain)
        
        # Sumuj wszystkie pasma z odpowiednimi wzmocnieniami
        output = (low_output * low_gain_linear + 
                 mid_output * mid_gain_linear + 
                 high_output * high_gain_linear)
        
        return output
    
    def reset(self):
        """Resetuje wszystkie filtry."""
        self.low_filter_L.reset()
        self.low_filter_R.reset()
        self.mid_filter_L.reset()
        self.mid_filter_R.reset()
        self.high_filter_L.reset()
        self.high_filter_R.reset()
    
    def get_gains(self) -> Tuple[float, float, float]:
        """Zwraca aktualne wzmocnienia (low, mid, high) w dB."""
        return (self.low_gain, self.mid_gain, self.high_gain)
    
    def set_gains(self, low_db: float, mid_db: float, high_db: float):
        """Ustawia wszystkie wzmocnienia jednocześnie."""
        self.set_low_gain(low_db)
        self.set_mid_gain(mid_db)
        self.set_high_gain(high_db)
    
    def bypass(self, audio_block: np.ndarray) -> np.ndarray:
        """Przepuszcza audio bez przetwarzania (bypass EQ)."""
        return audio_block.copy()


class SimpleEQ:
    """Uproszczona wersja EQ z kontrolkami -1.0 do +1.0 (zamiast dB)."""
    
    def __init__(self, sample_rate: int = 48000):
        self.eq = ThreeBandEQ(sample_rate)
        self.enabled = True
        self.lock = threading.Lock()
    
    def set_low(self, value: float):
        """Ustawia niskie częstotliwości (-1.0 do +1.0)."""
        db_value = value * 15.0  # mapowanie na ±15dB
        with self.lock:
            self.eq.set_low_gain(db_value)
    
    def set_mid(self, value: float):
        """Ustawia średnie częstotliwości (-1.0 do +1.0)."""
        db_value = value * 15.0  # mapowanie na ±15dB
        with self.lock:
            self.eq.set_mid_gain(db_value)
    
    def set_high(self, value: float):
        """Ustawia wysokie częstotliwości (-1.0 do +1.0)."""
        db_value = value * 15.0  # mapowanie na ±15dB
        with self.lock:
            self.eq.set_high_gain(db_value)
    
    def process(self, audio_block: np.ndarray) -> np.ndarray:
        """Przetwarza audio przez EQ (jeśli włączony) - SZYBKA operacja dla callbacku."""
        if audio_block.size == 0:
            return audio_block
        
        if self.enabled:
            with self.lock:
                return self.eq.process(audio_block)
        else:
            return self.eq.bypass(audio_block)
    
    def toggle_enabled(self):
        """Przełącza włączenie/wyłączenie EQ."""
        with self.lock:
            self.enabled = not self.enabled
    
    def reset(self):
        """Resetuje EQ do ustawień neutralnych."""
        with self.lock:
            self.eq.set_gains(0.0, 0.0, 0.0)
            self.eq.reset()