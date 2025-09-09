"""Mixer DJ - miksowanie 2 decków, crossfader, routowanie do wyjścia audio."""

import threading
import time
import numpy as np
import sounddevice as sd
from typing import Optional, List
from .deck import Deck
from .eq import SimpleEQ


class DJMixer:
    """Główny mixer DJ z dwoma deckami i crossfaderem."""
    
    def __init__(self, sample_rate: int = 48000, buffer_size: int = 2048):
        # Konfiguracja audio - zoptymalizowana dla stabilności
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.latency = 0.08  # 80ms latencji
        
        # Dwa decki
        self.deck_a = Deck(deck_id=1)
        self.deck_b = Deck(deck_id=2)
        
        # EQ dla każdego decka
        self.eq_a = SimpleEQ(sample_rate)
        self.eq_b = SimpleEQ(sample_rate)
        
        # Kontrolki miksera
        self.crossfader = 0.0  # -1.0 (deck A) do +1.0 (deck B)
        self.master_volume = 0.8  # główna głośność
        self.cue_mix = 0.0  # mix dla słuchawek (-1.0 = tylko cue, +1.0 = tylko master)
        
        # Kontrolki gain dla każdego decka
        self.gain_a = 1.0
        self.gain_b = 1.0
        
        # Kontrolki cue (słuchawki)
        self.cue_a = False
        self.cue_b = False
        
        # Audio stream
        self.audio_stream: Optional[sd.OutputStream] = None
        self.is_streaming = False
        
        # Threading dla audio callback
        self._audio_lock = threading.Lock()
        
        # Monitoring
        self.peak_levels = {'master_l': 0.0, 'master_r': 0.0, 
                           'deck_a_l': 0.0, 'deck_a_r': 0.0,
                           'deck_b_l': 0.0, 'deck_b_r': 0.0}
        self._last_peak_a = 0.0
        self._last_peak_b = 0.0
    
    def start_audio(self) -> bool:
        """Rozpoczyna stream audio z optymalnymi parametrami."""
        try:
            if self.audio_stream is not None:
                self.stop_audio()
            
            # Pre-roll - przygotuj decki przed startem
            self.deck_a.prepare_for_streaming()
            self.deck_b.prepare_for_streaming()
            
            self.audio_stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=2,
                blocksize=self.buffer_size,
                callback=self._audio_callback,
                dtype=np.float32,
                latency=self.latency
            )
            
            self.audio_stream.start()
            self.is_streaming = True
            print(f"Audio stream started: {self.sample_rate}Hz, buffer: {self.buffer_size}, latencja: {self.latency}s")
            return True
            
        except Exception as e:
            print(f"Błąd uruchamiania audio: {e}")
            return False
    
    def stop_audio(self):
        """Zatrzymuje stream audio."""
        if self.audio_stream is not None:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None
        
        self.is_streaming = False
        print("Audio stream zatrzymany")
    
    def _audio_callback(self, outdata: np.ndarray, frames: int, time, status):
        """Callback audio - TYLKO miksowanie gotowych próbek."""
        # BRAK printów w callbacku dla wydajności!
        
        with self._audio_lock:
            try:
                # Pobierz gotowe próbki z ring bufferów
                audio_a = self.deck_a.pop_audio_chunk(frames)
                audio_b = self.deck_b.pop_audio_chunk(frames)
                
                # Zastosuj tylko gain (EQ usunięte z callbacku)
                audio_a *= self.gain_a
                audio_b *= self.gain_b
                
                # Bardzo uproszczone miksowanie z crossfaderem
                crossfader_pos = (self.crossfader + 1.0) * 0.5  # -1..1 -> 0..1
                mix_a = 1.0 - crossfader_pos
                mix_b = crossfader_pos
                
                mixed_audio = (audio_a * mix_a) + (audio_b * mix_b)
                
                # Zastosuj master volume
                mixed_audio *= self.master_volume
                
                # Bardzo prosty hard clip
                np.clip(mixed_audio, -0.95, 0.95, out=mixed_audio)
                
                # Wypełnij buffer wyjściowy
                if len(mixed_audio) >= frames:
                    outdata[:] = mixed_audio[:frames]
                else:
                    outdata[:len(mixed_audio)] = mixed_audio
                    outdata[len(mixed_audio):] = 0
                
                # Atomowa aktualizacja peak levels (bez VU meters)
                self._last_peak_a = np.max(np.abs(audio_a)) if len(audio_a) > 0 else 0.0
                self._last_peak_b = np.max(np.abs(audio_b)) if len(audio_b) > 0 else 0.0
                
            except Exception:
                # Cisza zamiast błędu
                outdata.fill(0)
    
    def _apply_crossfader(self, audio_a: np.ndarray, audio_b: np.ndarray) -> np.ndarray:
        """Stosuje crossfader do miksowania dwóch decków."""
        # Crossfader: -1.0 = tylko A, 0.0 = 50/50, +1.0 = tylko B
        
        # Oblicz współczynniki miksowania (krzywa logarytmiczna)
        if self.crossfader <= 0:
            # Lewy zakres: A dominuje
            mix_a = 1.0
            mix_b = max(0.0, (self.crossfader + 1.0))
        else:
            # Prawy zakres: B dominuje  
            mix_a = max(0.0, (1.0 - self.crossfader))
            mix_b = 1.0
        
        # Zastosuj krzywe crossfadera (power law dla smooth transition)
        mix_a = mix_a ** 0.5
        mix_b = mix_b ** 0.5
        
        # Miksuj audio
        mixed = audio_a * mix_a + audio_b * mix_b
        
        return mixed
    
    def _soft_limit(self, audio: np.ndarray, threshold: float = 0.95) -> np.ndarray:
        """Soft limiting aby zapobiec clippingowi."""
        # Prosta kompresja/limiting
        peak = np.max(np.abs(audio))
        if peak > threshold:
            ratio = threshold / peak
            audio *= ratio
        
        return audio
    
    def _update_peak_levels(self, audio_a: np.ndarray, audio_b: np.ndarray):
        """Aktualizuje poziomy peak dla VU meters."""
        if len(audio_a) > 0:
            self.peak_levels['deck_a_l'] = float(np.max(np.abs(audio_a[:, 0])))
            self.peak_levels['deck_a_r'] = float(np.max(np.abs(audio_a[:, 1])))
        
        if len(audio_b) > 0:
            self.peak_levels['deck_b_l'] = float(np.max(np.abs(audio_b[:, 0])))
            self.peak_levels['deck_b_r'] = float(np.max(np.abs(audio_b[:, 1])))
    
    def update_vu_meters(self, mixed_audio: np.ndarray):
        """Aktualizuje VU metry na podstawie audio."""
        if len(mixed_audio) > 0:
            peak = np.max(np.abs(mixed_audio))
            self.peak_levels['master'] = peak
            self.peak_levels['A'] = self._last_peak_a
            self.peak_levels['B'] = self._last_peak_b
    
    # Kontrolki crossfadera
    def set_crossfader(self, value: float):
        """Ustawia pozycję crossfadera (-1.0 do +1.0)."""
        self.crossfader = max(-1.0, min(1.0, value))
    
    def get_crossfader(self) -> float:
        """Zwraca aktualną pozycję crossfadera."""
        return self.crossfader
    
    # Kontrolki głośności
    def set_master_volume(self, volume: float):
        """Ustawia główną głośność (0.0 - 1.0)."""
        self.master_volume = max(0.0, min(1.0, volume))
    
    def set_deck_gain(self, deck: str, gain: float):
        """Ustawia gain dla decka ('a' lub 'b')."""
        gain = max(0.0, min(2.0, gain))  # 0-200%
        if deck.lower() == 'a':
            self.gain_a = gain
        elif deck.lower() == 'b':
            self.gain_b = gain
    
    # Kontrolki EQ
    def set_eq(self, deck: str, band: str, value: float):
        """Ustawia EQ dla decka i pasma."""
        eq = self.eq_a if deck.lower() == 'a' else self.eq_b
        value = max(-1.0, min(1.0, value))
        
        if band.lower() == 'low':
            eq.set_low(value)
        elif band.lower() == 'mid':
            eq.set_mid(value)
        elif band.lower() == 'high':
            eq.set_high(value)
    
    # Kontrolki cue (słuchawki)
    def set_cue(self, deck: str, enabled: bool):
        """Włącza/wyłącza cue dla decka."""
        if deck.lower() == 'a':
            self.cue_a = enabled
        elif deck.lower() == 'b':
            self.cue_b = enabled
    
    def set_cue_mix(self, mix: float):
        """Ustawia mix cue/master dla słuchawek (-1.0 do +1.0)."""
        self.cue_mix = max(-1.0, min(1.0, mix))
    
    # Dostęp do decków
    def get_deck(self, deck: str) -> Deck:
        """Zwraca obiekt decka ('a' lub 'b')."""
        if deck.lower() == 'a':
            return self.deck_a
        elif deck.lower() == 'b':
            return self.deck_b
        else:
            raise ValueError("Deck musi być 'a' lub 'b'")
    
    # Monitoring
    def get_peak_levels(self) -> dict:
        """Zwraca aktualne poziomy peak."""
        return self.peak_levels.copy()
    
    def get_mixer_state(self) -> dict:
        """Zwraca pełny stan miksera."""
        return {
            'crossfader': self.crossfader,
            'master_volume': self.master_volume,
            'gain_a': self.gain_a,
            'gain_b': self.gain_b,
            'cue_a': self.cue_a,
            'cue_b': self.cue_b,
            'cue_mix': self.cue_mix,
            'is_streaming': self.is_streaming,
            'sample_rate': self.sample_rate,
            'buffer_size': self.buffer_size,
            'peak_levels': self.peak_levels,
            'deck_a_info': self.deck_a.get_info(),
            'deck_b_info': self.deck_b.get_info()
        }
    
    # Zarządzanie
    def reset_mixer(self):
        """Resetuje mixer do ustawień domyślnych."""
        self.crossfader = 0.0
        self.master_volume = 0.8
        self.gain_a = 1.0
        self.gain_b = 1.0
        self.cue_a = False
        self.cue_b = False
        self.cue_mix = 0.0
        
        # Reset EQ
        self.eq_a.reset()
        self.eq_b.reset()
        
        print("Mixer zresetowany do ustawień domyślnych")
    
    def __del__(self):
        """Cleanup przy usuwaniu obiektu."""
        if self.is_streaming:
            self.stop_audio()