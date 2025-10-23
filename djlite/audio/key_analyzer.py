"""Analizator klucza muzycznego (music key detection) z chroma features i algorytmem Krumhansl-Schmuckler."""

import numpy as np
import logging
import json
import os
from typing import Optional, Tuple, Dict
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False
    log.warning("librosa nie jest dostępne - analiza klucza będzie ograniczona")


class KeyAnalyzer:
    """Analizator klucza muzycznego z cache i offline analizą."""
    
    # Krumhansl-Schmuckler key profiles (dur/moll)
    MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    
    # Mapowanie pitch class do nazw nut
    PITCH_CLASSES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    PITCH_CLASSES_FLAT = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
    
    # Mapowanie do systemu Camelot
    CAMELOT_MAPPING = {
        # Major keys (B)
        'C major': '8B', 'G major': '9B', 'D major': '10B', 'A major': '11B',
        'E major': '12B', 'B major': '1B', 'F# major': '2B', 'Db major': '3B',
        'Ab major': '4B', 'Eb major': '5B', 'Bb major': '6B', 'F major': '7B',
        # Minor keys (A)
        'A minor': '8A', 'E minor': '9A', 'B minor': '10A', 'F# minor': '11A',
        'C# minor': '12A', 'G# minor': '1A', 'D# minor': '2A', 'Bb minor': '3A',
        'F minor': '4A', 'C minor': '5A', 'G minor': '6A', 'D minor': '7A'
    }
    
    def __init__(self):
        self.sample_rate = 44100
        self.analysis_duration = 90.0  # Analizuj pierwsze 90 sekund
    
    def analyze_key(self, file_path: str) -> Optional[Dict]:
        """Analizuje klucz muzyczny z cache lub wykonuje nową analizę.
        
        Returns:
            Dict z kluczami: 'key_name', 'camelot', 'confidence', 'method'
            lub None jeśli analiza się nie powiodła
        """
        path = Path(file_path)
        
        # Sprawdź cache
        cache_path = str(path) + '.key.json'
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    if self._validate_cache_data(cache_data):
                        log.info(f"Key z cache: {path.name} -> {cache_data['key_name']} ({cache_data['camelot']})")
                        return cache_data
            except Exception as e:
                log.warning(f"Błąd odczytu cache klucza {path.name}: {e}")
        
        # Wykonaj nową analizę
        log.info(f"Rozpoczynam analizę klucza: {path.name}")
        result = self._analyze_key_from_audio(file_path)
        
        if result:
            # Zapisz do cache
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                log.info(f"Key zapisany do cache: {path.name} -> {result['key_name']} ({result['camelot']})")
            except Exception as e:
                log.warning(f"Błąd zapisu cache klucza {path.name}: {e}")
        
        return result
    
    def _validate_cache_data(self, data: Dict) -> bool:
        """Sprawdza czy dane z cache są poprawne."""
        required_keys = ['key_name', 'camelot', 'confidence', 'method']
        return all(key in data for key in required_keys) and isinstance(data['confidence'], (int, float))
    
    def _analyze_key_from_audio(self, file_path: str) -> Optional[Dict]:
        """Wykonuje analizę klucza z pliku audio."""
        try:
            # Załaduj audio
            if LIBROSA_AVAILABLE:
                y, sr = librosa.load(file_path, sr=self.sample_rate, mono=True)
            else:
                # Fallback bez librosa
                return self._analyze_key_fallback(file_path)
            
            # Ogranicz do pierwszych 60-90 sekund
            max_samples = int(self.analysis_duration * sr)
            if len(y) > max_samples:
                y = y[:max_samples]
            
            # Oblicz chroma features
            chroma = self._compute_chroma_features(y, sr)
            if chroma is None:
                return None
            
            # Znajdź najlepszy klucz używając Krumhansl-Schmuckler
            key_name, confidence = self._find_best_key(chroma)
            
            if key_name:
                camelot = self.CAMELOT_MAPPING.get(key_name, '?')
                return {
                    'key_name': key_name,
                    'camelot': camelot,
                    'confidence': float(confidence),
                    'method': 'librosa+krumhansl'
                }
            
        except Exception as e:
            log.error(f"Błąd analizy klucza {file_path}: {e}")
        
        return None
    
    def _compute_chroma_features(self, y: np.ndarray, sr: int) -> Optional[np.ndarray]:
        """Oblicza chroma features z sygnału audio."""
        try:
            # Oblicz chroma z librosa
            chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=512)
            
            # Uśrednij w czasie (pitch class profile)
            chroma_mean = np.mean(chroma, axis=1)
            
            # Normalizacja
            if np.sum(chroma_mean) > 0:
                chroma_mean = chroma_mean / np.sum(chroma_mean)
            
            return chroma_mean
            
        except Exception as e:
            log.error(f"Błąd obliczania chroma features: {e}")
            return None
    
    def _find_best_key(self, chroma: np.ndarray) -> Tuple[Optional[str], float]:
        """Znajduje najlepszy klucz używając algorytmu Krumhansl-Schmuckler."""
        best_correlation = -1.0
        best_key = None
        
        # Testuj wszystkie 24 klucze (12 dur + 12 moll)
        for shift in range(12):
            # Przesuń chroma do różnych tonacji
            shifted_chroma = np.roll(chroma, shift)
            
            # Korelacja z profilem dur
            major_corr = np.corrcoef(shifted_chroma, self.MAJOR_PROFILE)[0, 1]
            if not np.isnan(major_corr) and major_corr > best_correlation:
                best_correlation = major_corr
                root_note = self._get_note_name(shift, prefer_sharps=True)
                best_key = f"{root_note} major"
            
            # Korelacja z profilem moll
            minor_corr = np.corrcoef(shifted_chroma, self.MINOR_PROFILE)[0, 1]
            if not np.isnan(minor_corr) and minor_corr > best_correlation:
                best_correlation = minor_corr
                root_note = self._get_note_name(shift, prefer_sharps=False)
                best_key = f"{root_note} minor"
        
        return best_key, best_correlation
    
    def _get_note_name(self, pitch_class: int, prefer_sharps: bool = True) -> str:
        """Konwertuje pitch class na nazwę nuty."""
        if prefer_sharps:
            return self.PITCH_CLASSES[pitch_class % 12]
        else:
            return self.PITCH_CLASSES_FLAT[pitch_class % 12]
    
    def _analyze_key_fallback(self, file_path: str) -> Optional[Dict]:
        """Fallback analiza bez librosa (uproszczona)."""
        try:
            import soundfile as sf
            
            # Załaduj audio
            y, sr = sf.read(file_path)
            if y.ndim == 2:
                y = np.mean(y, axis=1)  # mono
            
            # Prosty resample do 44.1kHz jeśli potrzeba
            if sr != self.sample_rate:
                from scipy import signal as scipy_signal
                y = scipy_signal.resample(y, int(len(y) * self.sample_rate / sr))
                sr = self.sample_rate
            
            # Ogranicz długość
            max_samples = int(self.analysis_duration * sr)
            if len(y) > max_samples:
                y = y[:max_samples]
            
            # Prosty chroma przez FFT (bez librosa)
            chroma = self._compute_chroma_fft(y, sr)
            if chroma is None:
                return None
            
            # Znajdź klucz
            key_name, confidence = self._find_best_key(chroma)
            
            if key_name:
                camelot = self.CAMELOT_MAPPING.get(key_name, '?')
                return {
                    'key_name': key_name,
                    'camelot': camelot,
                    'confidence': float(confidence),
                    'method': 'fft+krumhansl'
                }
        
        except Exception as e:
            log.error(f"Błąd fallback analizy klucza {file_path}: {e}")
        
        return None
    
    def _compute_chroma_fft(self, y: np.ndarray, sr: int) -> Optional[np.ndarray]:
        """Oblicza chroma features przez FFT (bez librosa)."""
        try:
            # Parametry FFT
            n_fft = 4096
            hop_length = 512
            
            # Inicjalizuj chroma accumulator
            chroma_acc = np.zeros(12)
            n_frames = 0
            
            # Przetwarzaj w blokach
            for i in range(0, len(y) - n_fft, hop_length):
                frame = y[i:i + n_fft]
                
                # FFT
                fft = np.fft.rfft(frame * np.hanning(n_fft))
                magnitude = np.abs(fft)
                
                # Mapowanie częstotliwości do pitch classes
                freqs = np.fft.rfftfreq(n_fft, 1/sr)
                
                for j, freq in enumerate(freqs[1:], 1):  # pomijamy DC
                    if freq > 80 and freq < 2000:  # zakres muzyczny
                        # Konwersja częstotliwości do pitch class
                        midi_note = 69 + 12 * np.log2(freq / 440.0)  # A4 = 440Hz = MIDI 69
                        pitch_class = int(round(midi_note)) % 12
                        chroma_acc[pitch_class] += magnitude[j]
                
                n_frames += 1
            
            if n_frames == 0 or np.sum(chroma_acc) == 0:
                return None
            
            # Normalizacja
            chroma_acc = chroma_acc / np.sum(chroma_acc)
            
            return chroma_acc
            
        except Exception as e:
            log.error(f"Błąd obliczania chroma FFT: {e}")
            return None
    
    def calculate_pitch_shift_cents(self, playback_rate: float) -> float:
        """Oblicza przesunięcie wysokości w centach dla danego playback rate.
        
        Args:
            playback_rate: Stosunek prędkości odtwarzania (1.0 = normalna)
            
        Returns:
            Przesunięcie w centach (1200 centów = 1 oktawa)
        """
        if playback_rate <= 0:
            return 0.0
        
        return 1200.0 * np.log2(playback_rate)
    
    def format_key_display(self, key_data: Dict, playback_rate: float = 1.0, key_lock: bool = True) -> str:
        """Formatuje wyświetlanie klucza dla UI.
        
        Args:
            key_data: Dane klucza z analyze_key()
            playback_rate: Aktualny playback rate
            key_lock: Czy Key Lock jest włączony
            
        Returns:
            Sformatowany string do wyświetlenia
        """
        if not key_data:
            return "KEY: —"
        
        # Obsługa różnych formatów danych (cache vs analiza)
        if 'display' in key_data:
            # Format z cache
            base_display = key_data['display']
        elif 'key_name' in key_data and 'camelot' in key_data:
            # Format z analizy
            base_display = f"KEY: {key_data['key_name']} ({key_data['camelot']})"
        else:
            return "KEY: —"
        
        # Jeśli Key Lock OFF i tempo ≠ 1.0, pokaż shifted key
        if not key_lock and abs(playback_rate - 1.0) > 0.001:
            cents = self.calculate_pitch_shift_cents(playback_rate)
            if abs(cents) > 1.0:  # Pokaż tylko jeśli przesunięcie > 1 cent
                sign = "+" if cents > 0 else ""
                base_display += f"\nKEY (playing): shifted by {sign}{cents:.0f} cents"
        
        return base_display
    
    def analyze_file(self, file_path: str) -> Optional[Dict]:
        """Wrapper dla analyze_key zgodny z interfejsem BatchKeyBpmAnalyzer.
        
        Returns:
            Dict z kluczami: 'status', 'key_info' lub None jeśli błąd
        """
        try:
            result = self.analyze_key(file_path)
            if result:
                return {
                    'status': 'ok',
                    'key_info': {
                        'standard': result['key_name'],
                        'camelot': result['camelot'],
                        'confidence': result['confidence']
                    }
                }
            else:
                return {
                    'status': 'error',
                    'key_info': {}
                }
        except Exception as e:
            log.error(f"Błąd analyze_file dla {file_path}: {e}")
            return {
                'status': 'error',
                'key_info': {}
            }