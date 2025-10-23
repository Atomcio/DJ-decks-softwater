"""Moduł testowy ClickTest - metronom klikający na uderzeniach siatki beatu."""

import time
import threading
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass
from .beat_grid import BeatGrid
import logging

log = logging.getLogger(__name__)

@dataclass
class ClickEvent:
    """Reprezentuje pojedynczy klik metronomu."""
    deck_id: str  # "A" lub "B"
    timestamp: float  # high-resolution timestamp
    beat_index: float  # indeks beatu w siatce
    sample_position: int  # pozycja w próbkach audio
    bpm: float  # aktualne BPM
    channel: int  # kanał audio (0=lewy dla A, 1=prawy dla B)

class ClickGenerator:
    """Generator sygnału kliku dla metronomu."""
    
    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self.click_duration = 0.01  # 10ms
        self.click_frequency = 1000  # 1kHz
        self._generate_click_sample()
    
    def _generate_click_sample(self):
        """Generuje próbkę dźwięku kliku."""
        samples = int(self.click_duration * self.sample_rate)
        t = np.linspace(0, self.click_duration, samples, False)
        
        # Krótki sygnał sinusoidalny z envelope
        sine_wave = np.sin(2 * np.pi * self.click_frequency * t)
        envelope = np.exp(-t * 50)  # Szybko zanikający envelope
        
        self.click_sample = (sine_wave * envelope * 0.3).astype(np.float32)
    
    def generate_click(self, channel: int) -> np.ndarray:
        """Generuje stereo klik dla określonego kanału."""
        stereo_click = np.zeros((len(self.click_sample), 2), dtype=np.float32)
        stereo_click[:, channel] = self.click_sample
        return stereo_click

class ClickTest:
    """Tryb testowy z metronomem dla synchronizacji decków."""
    
    def __init__(self, mixer, sample_rate: int = 44100):
        self.mixer = mixer
        self.sample_rate = sample_rate
        self.click_generator = ClickGenerator(sample_rate)
        
        # Stan testowania
        self.enabled = False
        self.deck_a_enabled = False
        self.deck_b_enabled = False
        
        # Śledzenie kliknięć
        self.last_click_a: Optional[ClickEvent] = None
        self.last_click_b: Optional[ClickEvent] = None
        self.click_history = []  # Historia kliknięć dla analizy
        
        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        
        # Callback dla logowania
        self.click_callback: Optional[Callable[[ClickEvent], None]] = None
        self.timing_callback: Optional[Callable[[dict], None]] = None
        
        # Bufory audio dla kliknięć
        self.click_buffer_size = int(0.1 * sample_rate)  # 100ms buffer
        self.click_buffer = np.zeros((self.click_buffer_size, 2), dtype=np.float32)
        self.buffer_position = 0
    
    def enable_deck(self, deck_id: str, enabled: bool):
        """Włącza/wyłącza metronom dla określonego decka."""
        with self._lock:
            if deck_id.upper() == "A":
                self.deck_a_enabled = enabled
            elif deck_id.upper() == "B":
                self.deck_b_enabled = enabled
            
            log.info(f"ClickTest deck {deck_id}: {'enabled' if enabled else 'disabled'}")
    
    def start(self):
        """Uruchamia tryb testowy ClickTest."""
        if self.enabled:
            return
        
        self.enabled = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._click_loop, daemon=True)
        self._thread.start()
        
        log.info("ClickTest started")
    
    def stop(self):
        """Zatrzymuje tryb testowy ClickTest."""
        if not self.enabled:
            return
        
        self.enabled = False
        self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        
        log.info("ClickTest stopped")
    
    def _click_loop(self):
        """Główna pętla generowania kliknięć."""
        last_beat_a = -1
        last_beat_b = -1
        
        while not self._stop_event.wait(0.001):  # 1ms precision
            try:
                current_time = time.perf_counter()
                
                # Sprawdź deck A
                if self.deck_a_enabled:
                    deck_a = self.mixer.deck_a
                    if deck_a.is_playing and deck_a.detected_bpm > 0:
                        beat_index = self._calculate_beat_index(deck_a)
                        current_beat = int(beat_index)
                        
                        if current_beat != last_beat_a and current_beat >= 0:
                            self._generate_click_event("A", deck_a, beat_index, current_time, 0)
                            last_beat_a = current_beat
                
                # Sprawdź deck B
                if self.deck_b_enabled:
                    deck_b = self.mixer.deck_b
                    if deck_b.is_playing and deck_b.detected_bpm > 0:
                        beat_index = self._calculate_beat_index(deck_b)
                        current_beat = int(beat_index)
                        
                        if current_beat != last_beat_b and current_beat >= 0:
                            self._generate_click_event("B", deck_b, beat_index, current_time, 1)
                            last_beat_b = current_beat
                
                # Analiza różnic czasowych
                self._analyze_timing_differences()
                
            except Exception as e:
                log.error(f"Error in click loop: {e}")
    
    def _calculate_beat_index(self, deck) -> float:
        """Oblicza aktualny indeks beatu dla decka."""
        if not deck.detected_bpm or deck.detected_bpm <= 0:
            return -1
        
        # Pozycja w sekundach
        position_sec = deck.audio_clock.now_seconds()
        
        # Oblicz beat index na podstawie BPM i pozycji
        sec_per_beat = 60.0 / (deck.detected_bpm * deck.effective_ratio())
        beat_index = position_sec / sec_per_beat
        
        return beat_index
    
    def _generate_click_event(self, deck_id: str, deck, beat_index: float, timestamp: float, channel: int):
        """Generuje event kliku i dodaje do bufora audio."""
        # Oblicz pozycję w próbkach
        sample_position = int(deck.audio_clock.now_seconds() * self.sample_rate)
        
        # Stwórz event kliku
        click_event = ClickEvent(
            deck_id=deck_id,
            timestamp=timestamp,
            beat_index=beat_index,
            sample_position=sample_position,
            bpm=deck.detected_bpm * deck.effective_ratio(),
            channel=channel
        )
        
        # Zapisz w historii
        with self._lock:
            if deck_id == "A":
                self.last_click_a = click_event
            else:
                self.last_click_b = click_event
            
            self.click_history.append(click_event)
            # Ogranicz historię do ostatnich 100 kliknięć
            if len(self.click_history) > 100:
                self.click_history.pop(0)
        
        # Generuj dźwięk kliku
        self._add_click_to_buffer(channel)
        
        # Callback dla logowania
        if self.click_callback:
            self.click_callback(click_event)
        
        log.debug(f"Click generated: Deck {deck_id}, beat {beat_index:.2f}, BPM {click_event.bpm:.1f}")
    
    def _add_click_to_buffer(self, channel: int):
        """Dodaje klik do bufora audio."""
        click_audio = self.click_generator.generate_click(channel)
        
        # Dodaj do bufora cyklicznego
        end_pos = self.buffer_position + len(click_audio)
        if end_pos <= len(self.click_buffer):
            self.click_buffer[self.buffer_position:end_pos] += click_audio
        else:
            # Wrap around
            first_part = len(self.click_buffer) - self.buffer_position
            self.click_buffer[self.buffer_position:] += click_audio[:first_part]
            self.click_buffer[:end_pos - len(self.click_buffer)] += click_audio[first_part:]
    
    def _analyze_timing_differences(self):
        """Analizuje różnice czasowe między klikami decków."""
        with self._lock:
            if self.last_click_a and self.last_click_b:
                # Oblicz różnicę w czasie
                time_diff_ms = (self.last_click_a.timestamp - self.last_click_b.timestamp) * 1000
                
                # Oblicz różnicę w beatach
                if self.last_click_a.bpm > 0 and self.last_click_b.bpm > 0:
                    avg_bpm = (self.last_click_a.bpm + self.last_click_b.bpm) / 2
                    ms_per_beat = 60000 / avg_bpm
                    beat_diff = time_diff_ms / ms_per_beat
                    
                    timing_data = {
                        'time_diff_ms': time_diff_ms,
                        'beat_diff': beat_diff,
                        'deck_a_bpm': self.last_click_a.bpm,
                        'deck_b_bpm': self.last_click_b.bpm,
                        'deck_a_beat': self.last_click_a.beat_index,
                        'deck_b_beat': self.last_click_b.beat_index,
                        'timestamp': time.perf_counter()
                    }
                    
                    if self.timing_callback:
                        self.timing_callback(timing_data)
    
    def get_click_audio(self, chunk_size: int) -> np.ndarray:
        """Pobiera chunk audio z klikami dla miksera."""
        if not self.enabled:
            return np.zeros((chunk_size, 2), dtype=np.float32)
        
        # Pobierz chunk z bufora cyklicznego
        audio_chunk = np.zeros((chunk_size, 2), dtype=np.float32)
        
        if chunk_size <= len(self.click_buffer):
            end_pos = self.buffer_position + chunk_size
            if end_pos <= len(self.click_buffer):
                audio_chunk = self.click_buffer[self.buffer_position:end_pos].copy()
            else:
                # Wrap around
                first_part = len(self.click_buffer) - self.buffer_position
                audio_chunk[:first_part] = self.click_buffer[self.buffer_position:].copy()
                audio_chunk[first_part:] = self.click_buffer[:chunk_size - first_part].copy()
            
            # Wyczyść wykorzystany fragment bufora
            if end_pos <= len(self.click_buffer):
                self.click_buffer[self.buffer_position:end_pos] = 0
            else:
                self.click_buffer[self.buffer_position:] = 0
                self.click_buffer[:end_pos - len(self.click_buffer)] = 0
            
            # Aktualizuj pozycję bufora
            self.buffer_position = end_pos % len(self.click_buffer)
        
        return audio_chunk
    
    def get_status(self) -> dict:
        """Zwraca status trybu testowego."""
        with self._lock:
            return {
                'enabled': self.enabled,
                'deck_a_enabled': self.deck_a_enabled,
                'deck_b_enabled': self.deck_b_enabled,
                'last_click_a': self.last_click_a.__dict__ if self.last_click_a else None,
                'last_click_b': self.last_click_b.__dict__ if self.last_click_b else None,
                'click_history_count': len(self.click_history)
            }