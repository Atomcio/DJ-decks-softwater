"""Isolating 3-pasmowy equalizer z filtrami HP/LP/BP i kill funkcją."""

import numpy as np
from scipy import signal
from typing import Tuple, List, Optional
import threading
import time
import logging

log = logging.getLogger(__name__)


class BiquadIIR:
    """Wielokanałowy filtr biquad IIR z zabezpieczeniami."""
    
    def __init__(self, b_coeffs: np.ndarray, a_coeffs: np.ndarray, channels: int = 2):
        """Inicjalizuje filtr biquad.
        
        Args:
            b_coeffs: współczynniki licznika [b0, b1, b2]
            a_coeffs: współczynniki mianownika [a0, a1, a2] (a0 powinno być 1.0)
            channels: liczba kanałów audio (1=mono, 2=stereo)
        """
        # Weryfikacja współczynników
        if not (np.all(np.isfinite(b_coeffs)) and np.all(np.isfinite(a_coeffs))):
            raise ValueError("Współczynniki filtru zawierają NaN/Inf")
        
        # Szybki test stabilności
        if len(a_coeffs) >= 3:
            if abs(a_coeffs[1]) >= 1.999 or abs(a_coeffs[2]) >= 0.999:
                log.warning("Filtr może być niestabilny: a1=%f, a2=%f", a_coeffs[1], a_coeffs[2])
        
        self.b = b_coeffs.copy()
        self.a = a_coeffs.copy()
        self.channels = channels
        
        # Stan filtru dla każdego kanału [kanał, opóźnienie]
        self.x_delay = np.zeros((channels, 2))  # x[n-1], x[n-2]
        self.y_delay = np.zeros((channels, 2))  # y[n-1], y[n-2]
    
    def process(self, audio_block: np.ndarray) -> np.ndarray:
        """Przetwarza blok audio przez filtr - WEKTOROWE PRZETWARZANIE.
        
        Args:
            audio_block: audio w formacie [frames, channels]
            
        Returns:
            przefiltrowane audio w tym samym formacie
        """
        if audio_block.size == 0:
            return audio_block
        
        # Upewnij się, że mamy właściwy kształt
        if audio_block.ndim == 1:
            audio_block = audio_block.reshape(-1, 1)
        
        frames, input_channels = audio_block.shape
        
        # Dopasuj liczbę kanałów jeśli potrzeba
        if input_channels != self.channels:
            if input_channels == 1 and self.channels == 2:
                # Mono -> stereo
                audio_block = np.column_stack([audio_block[:, 0], audio_block[:, 0]])
            elif input_channels == 2 and self.channels == 1:
                # Stereo -> mono
                audio_block = np.mean(audio_block, axis=1, keepdims=True)
            else:
                raise ValueError(f"Niezgodność kanałów: filtr={self.channels}, audio={input_channels}")
        
        output = np.zeros_like(audio_block)
        
        # WEKTOROWE przetwarzanie per kanał - ZERO pętli po próbkach
        for ch in range(self.channels):
            # Użyj scipy.signal.lfilter dla wydajnego przetwarzania
            # Inicjalizuj stan z poprzednich opóźnień
            zi = np.array([self.x_delay[ch, 0], self.x_delay[ch, 1], 
                          self.y_delay[ch, 0], self.y_delay[ch, 1]])
            
            # Filtruj cały kanał jednocześnie
            filtered, zf = signal.lfilter(self.b, self.a, audio_block[:, ch], zi=zi)
            output[:, ch] = filtered
            
            # Aktualizuj stan opóźnień z końcowych wartości
            if len(zf) >= 4:
                self.x_delay[ch, 0] = zf[0]
                self.x_delay[ch, 1] = zf[1] 
                self.y_delay[ch, 0] = zf[2]
                self.y_delay[ch, 1] = zf[3]
        
        return output
    
    def reset(self):
        """Resetuje stan filtru."""
        self.x_delay.fill(0)
        self.y_delay.fill(0)


def biquad_highpass(fs: float, fc: float, Q: float = 0.707) -> Tuple[np.ndarray, np.ndarray]:
    """Tworzy współczynniki filtru high-pass."""
    fs = 48000.0
    fc = np.clip(fc, 20.0, min(20000.0, fs/2.1))
    Q = np.clip(Q, 0.5, 2.0)
    
    w = 2.0 * np.pi * fc / fs
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    alpha = sin_w / (2.0 * Q)
    
    # High-pass coefficients
    b0 = (1.0 + cos_w) / 2.0
    b1 = -(1.0 + cos_w)
    b2 = (1.0 + cos_w) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha
    
    # Normalizacja
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1, a2]) / a0
    
    return b, a


def biquad_lowpass(fs: float, fc: float, Q: float = 0.707) -> Tuple[np.ndarray, np.ndarray]:
    """Tworzy współczynniki filtru low-pass."""
    fs = 48000.0
    fc = np.clip(fc, 20.0, min(20000.0, fs/2.1))
    Q = np.clip(Q, 0.5, 2.0)
    
    w = 2.0 * np.pi * fc / fs
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    alpha = sin_w / (2.0 * Q)
    
    # Low-pass coefficients
    b0 = (1.0 - cos_w) / 2.0
    b1 = 1.0 - cos_w
    b2 = (1.0 - cos_w) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha
    
    # Normalizacja
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1, a2]) / a0
    
    return b, a


def biquad_bandpass(fs: float, fc: float, Q: float = 0.707) -> Tuple[np.ndarray, np.ndarray]:
    """Tworzy współczynniki filtru band-pass."""
    fs = 48000.0
    fc = np.clip(fc, 20.0, min(20000.0, fs/2.1))
    Q = np.clip(Q, 0.5, 2.0)
    
    w = 2.0 * np.pi * fc / fs
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    alpha = sin_w / (2.0 * Q)
    
    # Band-pass coefficients
    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha
    
    # Normalizacja
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1, a2]) / a0
    
    return b, a


def biquad_peaking(fs: float, fc: float, Q: float = 0.707, gain_db: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """Tworzy współczynniki peaking filtru EQ.
    
    Args:
        fs: częstotliwość próbkowania
        fc: częstotliwość środkowa
        Q: jakość filtru (0.5-1.2) - ograniczone dla stabilności
        gain_db: wzmocnienie w dB (-18 do +12) - ograniczone dla DJ
    """
    fs = 48000.0
    fc = np.clip(fc, 20.0, min(20000.0, fs/2.1))
    Q = np.clip(Q, 0.5, 1.2)  # Ograniczone zgodnie z wymaganiami
    gain_db = np.clip(gain_db, -18.0, 12.0)  # Ograniczone zgodnie z wymaganiami
    
    # Konwersja dB na amplitudę
    A = 10.0 ** (gain_db / 40.0)  # sqrt(10^(gain_db/20))
    
    w = 2.0 * np.pi * fc / fs
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    alpha = sin_w / (2.0 * Q)
    
    # Peaking EQ coefficients
    b0 = 1.0 + alpha * A
    b1 = -2.0 * cos_w
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha / A
    
    # Normalizacja
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1, a2]) / a0
    
    return b, a


def biquad_low_shelf(fs: float, fc: float, gain_db: float = 0.0, Q: float = 0.707) -> Tuple[np.ndarray, np.ndarray]:
    """Tworzy współczynniki low shelf filtru dla DJ EQ.
    
    Args:
        fs: częstotliwość próbkowania
        fc: częstotliwość graniczna (250Hz dla DJ)
        gain_db: wzmocnienie w dB (-18 do +12)
        Q: jakość filtru (0.5-1.2)
    """
    fs = 48000.0
    fc = np.clip(fc, 20.0, min(20000.0, fs/2.1))
    Q = np.clip(Q, 0.5, 1.2)
    gain_db = np.clip(gain_db, -18.0, 12.0)
    
    # Konwersja dB na amplitudę
    A = 10.0 ** (gain_db / 40.0)
    
    w = 2.0 * np.pi * fc / fs
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    S = 1.0  # Shelf slope
    alpha = sin_w / 2.0 * np.sqrt((A + 1.0/A) * (1.0/S - 1.0) + 2.0)
    
    # Low shelf coefficients
    b0 = A * ((A + 1.0) - (A - 1.0) * cos_w + 2.0 * np.sqrt(A) * alpha)
    b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w)
    b2 = A * ((A + 1.0) - (A - 1.0) * cos_w - 2.0 * np.sqrt(A) * alpha)
    a0 = (A + 1.0) + (A - 1.0) * cos_w + 2.0 * np.sqrt(A) * alpha
    a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w)
    a2 = (A + 1.0) + (A - 1.0) * cos_w - 2.0 * np.sqrt(A) * alpha
    
    # Normalizacja
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1, a2]) / a0
    
    return b, a


def biquad_high_shelf(fs: float, fc: float, gain_db: float = 0.0, Q: float = 0.707) -> Tuple[np.ndarray, np.ndarray]:
    """Tworzy współczynniki high shelf filtru dla DJ EQ.
    
    Args:
        fs: częstotliwość próbkowania
        fc: częstotliwość graniczna (4000Hz dla DJ)
        gain_db: wzmocnienie w dB (-18 do +12)
        Q: jakość filtru (0.5-1.2)
    """
    fs = 48000.0
    fc = np.clip(fc, 20.0, min(20000.0, fs/2.1))
    Q = np.clip(Q, 0.5, 1.2)
    gain_db = np.clip(gain_db, -18.0, 12.0)
    
    # Konwersja dB na amplitudę
    A = 10.0 ** (gain_db / 40.0)
    
    w = 2.0 * np.pi * fc / fs
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    S = 1.0  # Shelf slope
    alpha = sin_w / 2.0 * np.sqrt((A + 1.0/A) * (1.0/S - 1.0) + 2.0)
    
    # High shelf coefficients
    b0 = A * ((A + 1.0) + (A - 1.0) * cos_w + 2.0 * np.sqrt(A) * alpha)
    b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w)
    b2 = A * ((A + 1.0) + (A - 1.0) * cos_w - 2.0 * np.sqrt(A) * alpha)
    a0 = (A + 1.0) - (A - 1.0) * cos_w + 2.0 * np.sqrt(A) * alpha
    a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cos_w)
    a2 = (A + 1.0) - (A - 1.0) * cos_w - 2.0 * np.sqrt(A) * alpha
    
    # Normalizacja
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1, a2]) / a0
    
    return b, a


class FilterChain:
    """Immutable łańcuch filtrów dla RCU."""
    def __init__(self, low_filter=None, mid_filter=None, high_filter=None):
        self.low_filter = low_filter
        self.mid_filter = mid_filter
        self.high_filter = high_filter
        self.is_bypass = (low_filter is None and mid_filter is None and high_filter is None)


class IsolatingThreeBandEQ:
    """Isolating 3-pasmowy EQ z RCU (Read-Copy-Update) - ZERO locków w callbacku."""
    
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = 48000  # Zawsze 48kHz
        self.channels = channels
        
        # Częstotliwości graniczne pasm
        self.low_cutoff = 250.0   # Hz - granica low/mid
        self.high_cutoff = 4000.0 # Hz - granica mid/high
        
        # Gain dla każdego pasma (-1.0 = kill, 0.0 = neutral, +1.0 = boost)
        self.low_gain = 0.0
        self.mid_gain = 0.0
        self.high_gain = 0.0
        
        # RCU: aktualny łańcuch filtrów (atomowa referencja)
        self.current_chain = FilterChain()  # Pusty łańcuch na start
        
        # Crossfade state
        self.crossfade_chain = None  # Stary łańcuch podczas crossfade
        self.crossfade_samples = int(0.012 * self.sample_rate)  # 12ms
        self.crossfade_counter = 0
        self.crossfading = False
        
        # Prealokowane bufory dla crossfade (unikamy alokacji w callbacku)
        self.temp_buffer1 = None
        self.temp_buffer2 = None
        
        # Debounce dla przebudowy (poza callbackiem)
        self.last_rebuild_time = 0.0
        self.rebuild_pending = False
        
        # Smoothing gałek (rampa 10-15ms na gain_lin)
        self.target_low_gain = 1.0
        self.target_mid_gain = 1.0
        self.target_high_gain = 1.0
        self.current_low_gain = 1.0
        self.current_mid_gain = 1.0
        self.current_high_gain = 1.0
        self.ramp_samples = int(sample_rate * 0.012)  # 12ms rampa
        
        # Timer dla debounced rebuild (uruchamiany poza callbackiem)
        self._rebuild_timer = None
        
        # Zbuduj początkowy łańcuch
        self._rebuild_filters_immediate()
    
    def _rebuild_filters_immediate(self):
        """Przebudowuje filtry natychmiast (dla inicjalizacji)."""
        try:
            # Buduj nowy łańcuch filtrów
            new_low = None
            new_mid = None
            new_high = None
            
            # LOW SHELF filter dla pasma niskiego (<250Hz)
            if abs(self.low_gain) > 1e-6:
                gain_db = self.low_gain * 15.0  # Skalowanie do zakresu [-18, +12]
                b, a = biquad_low_shelf(self.sample_rate, 250.0, gain_db=gain_db, Q=0.7)
                new_low = BiquadIIR(b, a, self.channels)
            
            # PEAKING filter dla pasma średniego (250Hz-4kHz)
            if abs(self.mid_gain) > 1e-6:
                gain_db = self.mid_gain * 15.0
                b, a = biquad_peaking(self.sample_rate, 1000.0, Q=0.8, gain_db=gain_db)
                new_mid = BiquadIIR(b, a, self.channels)
            
            # HIGH SHELF filter dla pasma wysokiego (>4kHz)
            if abs(self.high_gain) > 1e-6:
                gain_db = self.high_gain * 15.0
                b, a = biquad_high_shelf(self.sample_rate, 4000.0, gain_db=gain_db, Q=0.7)
                new_high = BiquadIIR(b, a, self.channels)
            
            # RCU: atomowa wymiana referencji
            self.current_chain = FilterChain(new_low, new_mid, new_high)
            
        except Exception as e:
            log.error("Błąd przebudowy filtrów EQ: %s", e)
    
    def _rebuild_filters_with_crossfade(self):
        """Przebudowuje filtry z crossfade (wywoływane poza callbackiem)."""
        try:
            # Zachowaj stary łańcuch dla crossfade
            old_chain = self.current_chain
            
            # Buduj nowy łańcuch
            self._rebuild_filters_immediate()
            
            # Uruchom crossfade jeśli stary łańcuch nie był pusty
            if not old_chain.is_bypass:
                self.crossfade_chain = old_chain
                self.crossfade_counter = 0
                self.crossfading = True
            
            self.rebuild_pending = False
            
        except Exception as e:
            log.error("Błąd przebudowy filtrów EQ z crossfade: %s", e)
    
    def _schedule_rebuild(self):
        """Planuje przebudowę z debounce (poza callbackiem)."""
        self.last_rebuild_time = time.perf_counter()
        self.rebuild_pending = True
        
        # W prawdziwej implementacji tutaj byłby timer/thread
        # Na razie robimy rebuild natychmiast dla prostoty
        self._rebuild_filters_with_crossfade()
    
    def set_low_gain(self, gain: float):
        """Ustawia wzmocnienie pasma niskiego (-1.0 = kill, 0.0 = neutral, +1.0 = boost)."""
        self.low_gain = np.clip(gain, -1.0, 1.0)
        self._schedule_rebuild()
    
    def set_mid_gain(self, gain: float):
        """Ustawia wzmocnienie pasma średniego (-1.0 = kill, 0.0 = neutral, +1.0 = boost)."""
        self.mid_gain = np.clip(gain, -1.0, 1.0)
        self._schedule_rebuild()
    
    def set_high_gain(self, gain: float):
        """Ustawia wzmocnienie pasma wysokiego (-1.0 = kill, 0.0 = neutral, +1.0 = boost)."""
        self.high_gain = np.clip(gain, -1.0, 1.0)
        self._schedule_rebuild()
    
    def process(self, audio_block: np.ndarray) -> np.ndarray:
        """Przetwarza audio przez isolating EQ - ZOPTYMALIZOWANE dla audio callback."""
        if audio_block.size == 0:
            return audio_block
        
        # SZYBKI BYPASS: jeśli wszystkie gain są neutralne (0.0)
        if (abs(self.low_gain) < 1e-6 and 
            abs(self.mid_gain) < 1e-6 and 
            abs(self.high_gain) < 1e-6):
            return audio_block  # BRAK KOPII!
        
        # Bypass gdy wszystkie pasma ≈ 0 dB
        if (abs(self.low_gain_db) < 0.1 and 
            abs(self.mid_gain_db) < 0.1 and 
            abs(self.high_gain_db) < 0.1):
            return audio_block.copy()  # Bypass - zwróć kopię
        
        try:
            # RCU: pobierz aktualny łańcuch filtrów (atomowe odczytanie)
            current_chain = self.current_chain
            
            # SZYBKI BYPASS: jeśli łańcuch jest pusty
            if current_chain.is_bypass:
                return audio_block  # BRAK KOPII!
            
            # Rozpocznij od oryginalnego sygnału
            output = audio_block.copy()
            output += 1e-20  # anti-denormal
            
            # Crossfade handling (bez locków)
            if self.crossfading and self.crossfade_counter < self.crossfade_samples:
                # Alokuj bufory jeśli potrzeba
                if self.temp_buffer1 is None or self.temp_buffer1.shape != audio_block.shape:
                    self.temp_buffer1 = np.zeros_like(audio_block)
                    self.temp_buffer2 = np.zeros_like(audio_block)
                
                # Oblicz współczynnik crossfade
                fade_factor = self.crossfade_counter / self.crossfade_samples
                
                # Przetwórz przez stary łańcuch
                self.temp_buffer1[:] = audio_block
                if self.crossfade_chain.low_filter is not None:
                    self.temp_buffer1 = self.crossfade_chain.low_filter.process(self.temp_buffer1)
                if self.crossfade_chain.mid_filter is not None:
                    self.temp_buffer1 = self.crossfade_chain.mid_filter.process(self.temp_buffer1)
                if self.crossfade_chain.high_filter is not None:
                    self.temp_buffer1 = self.crossfade_chain.high_filter.process(self.temp_buffer1)
                
                # Przetwórz przez nowy łańcuch
                self.temp_buffer2[:] = audio_block
                if current_chain.low_filter is not None:
                    self.temp_buffer2 = current_chain.low_filter.process(self.temp_buffer2)
                if current_chain.mid_filter is not None:
                    self.temp_buffer2 = current_chain.mid_filter.process(self.temp_buffer2)
                if current_chain.high_filter is not None:
                    self.temp_buffer2 = current_chain.high_filter.process(self.temp_buffer2)
                
                # Crossfade
                output = self.temp_buffer1 * (1.0 - fade_factor) + self.temp_buffer2 * fade_factor
                
                # Zwiększ licznik
                self.crossfade_counter += audio_block.shape[0]
                
                # Sprawdź czy crossfade się skończył
                if self.crossfade_counter >= self.crossfade_samples:
                    self.crossfading = False
                    self.crossfade_chain = None
            
            else:
                # Normalne przetwarzanie przez aktualny łańcuch
                if current_chain.low_filter is not None:
                    output = current_chain.low_filter.process(output)
                
                if current_chain.mid_filter is not None:
                    output = current_chain.mid_filter.process(output)
                
                if current_chain.high_filter is not None:
                    output = current_chain.high_filter.process(output)
            
            # Aktualizuj smoothing gains
            self._update_smoothing_gains(audio_block.shape[0])
            
            # Strażnik NaN/Inf + soft clip
            output = np.nan_to_num(output, nan=0.0, posinf=1.0, neginf=-1.0)
            np.clip(output, -1.0, 1.0, out=output)
            
            return output
            
        except Exception as e:
            log.error("EQ callback error: %s", e)
            # Zwróć suchy sygnał przy błędzie
            return audio_block
    
    def _update_smoothing_gains(self, num_samples: int):
        """Aktualizuje smoothing gains w kierunku target values."""
        if self.ramp_samples <= 0:
            return
        
        ramp_step = num_samples / self.ramp_samples
        
        # Smooth interpolation do target values
        self.current_low_gain += (self.target_low_gain - self.current_low_gain) * ramp_step
        self.current_mid_gain += (self.target_mid_gain - self.current_mid_gain) * ramp_step
        self.current_high_gain += (self.target_high_gain - self.current_high_gain) * ramp_step
    
    def reset(self):
        """Resetuje wszystkie filtry."""
        # Reset aktualnego łańcucha
        current_chain = self.current_chain
        if current_chain.low_filter:
            current_chain.low_filter.reset()
        if current_chain.mid_filter:
            current_chain.mid_filter.reset()
        if current_chain.high_filter:
            current_chain.high_filter.reset()
        
        # Reset crossfade chain jeśli istnieje
        if self.crossfade_chain:
            if self.crossfade_chain.low_filter:
                self.crossfade_chain.low_filter.reset()
            if self.crossfade_chain.mid_filter:
                self.crossfade_chain.mid_filter.reset()
            if self.crossfade_chain.high_filter:
                self.crossfade_chain.high_filter.reset()
    
    def get_gains(self) -> Tuple[float, float, float]:
        """Zwraca aktualne wzmocnienia (low, mid, high)."""
        return (self.low_gain, self.mid_gain, self.high_gain)
    
    def set_gains(self, low: float, mid: float, high: float):
        """Ustawia wszystkie wzmocnienia jednocześnie."""
        self.low_gain = np.clip(low, -1.0, 1.0)
        self.mid_gain = np.clip(mid, -1.0, 1.0)
        self.high_gain = np.clip(high, -1.0, 1.0)
        
        # Konwertuj na linear gain (przywrócone bezpośrednie działanie)
        self.low_gain = np.clip(low, -1.0, 1.0)
        self.mid_gain = np.clip(mid, -1.0, 1.0)
        self.high_gain = np.clip(high, -1.0, 1.0)
        
        # Ustaw target gains dla smoothing
        self.target_low_gain = self.low_gain
        self.target_mid_gain = self.mid_gain
        self.target_high_gain = self.high_gain
        
        self._schedule_rebuild()


class SimpleEQ:
    """Wrapper dla IsolatingThreeBandEQ z kontrolkami -1.0 do +1.0."""
    
    def __init__(self, sample_rate: int = 48000):
        self.eq = IsolatingThreeBandEQ(sample_rate, channels=2)
        self.enabled = True
        self.lock = threading.Lock()
    
    def set_low(self, value: float):
        """Ustawia niskie częstotliwości (-1.0 = kill, 0.0 = neutral, +1.0 = boost)."""
        value = np.clip(value, -1.0, 1.0)
        self.eq.set_low_gain(value)
    
    def set_mid(self, value: float):
        """Ustawia średnie częstotliwości (-1.0 = kill, 0.0 = neutral, +1.0 = boost)."""
        value = np.clip(value, -1.0, 1.0)
        self.eq.set_mid_gain(value)
    
    def set_high(self, value: float):
        """Ustawia wysokie częstotliwości (-1.0 = kill, 0.0 = neutral, +1.0 = boost)."""
        value = np.clip(value, -1.0, 1.0)
        self.eq.set_high_gain(value)
    
    def process(self, audio_block: np.ndarray) -> np.ndarray:
        """Przetwarza audio przez EQ (jeśli włączony) - ZOPTYMALIZOWANE."""
        if audio_block.size == 0:
            return audio_block
        
        if self.enabled:
            # EQ processing
            return self.eq.process(audio_block)
        else:
            log.debug("EQ disabled - bypassing")
            return audio_block  # BRAK KOPII gdy EQ wyłączony!
    
    def toggle_enabled(self):
        """Przełącza włączenie/wyłączenie EQ."""
        with self.lock:
            self.enabled = not self.enabled
    
    def reset(self):
        """Resetuje EQ do ustawień neutralnych."""
        with self.lock:
            self.eq.set_gains(0.0, 0.0, 0.0)
            self.eq.reset()
    
    def get_gains(self):
        """Zwraca aktualne wzmocnienia."""
        return self.eq.get_gains()
    
    def set_low(self, value: float):
        """Ustawia wzmocnienie niskich częstotliwości (-1.0 do +1.0)."""
        # Mapuj -1.0..+1.0 na -18..+12 dB
        gain_db = value * 15.0  # -15 do +15, ale ograniczone do -18..+12
        gain_db = max(-18.0, min(12.0, gain_db))
        current_gains = self.eq.get_gains()
        self.eq.set_gains(gain_db, current_gains[1], current_gains[2])
    
    def set_mid(self, value: float):
        """Ustawia wzmocnienie średnich częstotliwości (-1.0 do +1.0)."""
        gain_db = value * 15.0
        gain_db = max(-18.0, min(12.0, gain_db))
        current_gains = self.eq.get_gains()
        self.eq.set_gains(current_gains[0], gain_db, current_gains[2])
    
    def set_high(self, value: float):
        """Ustawia wzmocnienie wysokich częstotliwości (-1.0 do +1.0)."""
        gain_db = value * 15.0
        gain_db = max(-18.0, min(12.0, gain_db))
        current_gains = self.eq.get_gains()
        self.eq.set_gains(current_gains[0], current_gains[1], gain_db)
    
    def reset_eq(self):
        """Resetuje EQ do neutralnych ustawień (0 dB na wszystkich pasmach)."""
        self.eq.set_gains(0.0, 0.0, 0.0)
        self.eq.reset()


# Backward compatibility aliases
ThreeBandEQ = IsolatingThreeBandEQ
StableThreeBandEQ = IsolatingThreeBandEQ
BiquadFilter = BiquadIIR