"""Logika pojedynczego decka DJ - odtwarzanie, BPM, pozycja utworu."""

import threading
import time
import numpy as np
import soundfile as sf
from typing import Optional, Callable
from collections import deque
from scipy import signal
import json
import os
from PySide6.QtCore import QObject, Signal

try:
    import aubio
    AUBIO_AVAILABLE = True
except ImportError:
    AUBIO_AVAILABLE = False
    try:
        import librosa
        LIBROSA_AVAILABLE = True
    except ImportError:
        LIBROSA_AVAILABLE = False


class Deck(QObject):
    """Pojedynczy deck DJ z kontrolą odtwarzania i BPM."""
    
    # Sygnały Qt
    bpmReady = Signal(float)  # emitowany gdy BPM zostanie wykryte
    analysisFailed = Signal(str)  # emitowany gdy analiza się nie powiedzie
    
    def __init__(self, deck_id: int):
        super().__init__()
        self.deck_id = deck_id
        self.audio_data: Optional[np.ndarray] = None
        self.sample_rate: int = 48000  # Zoptymalizowane
        self.channels: int = 2
        
        # Kontrola tempa/BPM
        self.tempo_ratio = 1.0  # 1.0 = normalna prędkość
        self.tempo_lock = threading.Lock()
        
        # Ring buffer dla stabilnego audio
        self.ring_buffer = deque(maxlen=int(48000 * 1.0))  # 1 sekunda bufora
        self.buffer_lock = threading.Lock()
        self.worker_thread = None
        self.should_stop_worker = False
        
        # Kontrola odtwarzania
        self.is_playing = False
        self.is_paused = False
        self.position = 0.0  # pozycja w sekundach
        self.volume = 1.0  # 0.0 - 1.0
        
        # BPM i tempo control
        self.detected_bpm: Optional[float] = None   # wynik analizy
        self.bpm_target: Optional[float] = None     # wartość z gałki BPM
        self.rate_target: float = 1.0            # target varispeed (= bpm_target/detected_bpm)
        self.rate_smooth: float = 1.0            # płynnie dociągane tempo
        self.phase: float = 0.0                  # faza dla varispeed
        self._last_smooth_t: float = 0.0         # czas ostatniego smooth_rate
        
        # Legacy kontrola BPM (do usunięcia)
        self.original_bpm = 120.0
        self.current_bpm = 120.0
        self.pitch_ratio = 1.0  # stosunek prędkości odtwarzania
        
        # Threading
        self._playback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Callback dla audio output
        self.audio_callback: Optional[Callable] = None
        
        # Informacje o utworze
        self.track_path: Optional[str] = None
        self.track_name: str = ""
        self.duration: float = 0.0
        
    def load_track(self, file_path: str) -> bool:
        """Ładuje utwór z pliku audio."""
        try:
            self.audio_data, self.sample_rate = sf.read(file_path)
            
            # Konwersja do stereo jeśli mono
            if len(self.audio_data.shape) == 1:
                self.audio_data = np.column_stack((self.audio_data, self.audio_data))
            
            self.channels = self.audio_data.shape[1]
            self.duration = len(self.audio_data) / self.sample_rate
            self.track_path = file_path
            self.track_name = file_path.split('\\')[-1].split('/')[-1]
            
            # Reset pozycji
            self.position = 0.0
            self.is_playing = False
            self.is_paused = False
            
            # Reset tempa do 1.0
            self.rate_target = 1.0
            self.rate_smooth = 1.0
            self.bpm_target = None
            self.phase = 0.0
            
            # Rozpocznij analizę BPM w tle
            self._start_bpm_analysis()
            
            return True
            
        except Exception as e:
            print(f"Błąd ładowania utworu {file_path}: {e}")
            return False
    
    def play(self):
        """Rozpoczyna odtwarzanie."""
        if self.audio_data is None:
            return
            
        # Zawsze startuj w tempie 1.0
        self.rate_target = 1.0
        self.rate_smooth = 1.0
            
        if self.is_paused:
            self.is_paused = False
            self.is_playing = True
        else:
            self.is_playing = True
            self._stop_event.clear()
            
            # Uruchom worker thread jeśli nie działa
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self.should_stop_worker = False
                self.worker_thread = threading.Thread(target=self._worker_thread_func, daemon=True)
                self.worker_thread.start()
            
            if self._playback_thread is None or not self._playback_thread.is_alive():
                self._playback_thread = threading.Thread(target=self._playback_loop)
                self._playback_thread.daemon = True
                self._playback_thread.start()
    
    def pause(self):
        """Pauzuje odtwarzanie."""
        self.is_paused = True
        self.is_playing = False
    
    def stop(self):
        """Zatrzymuje odtwarzanie i resetuje pozycję."""
        self.is_playing = False
        self.is_paused = False
        self._stop_event.set()
        self.position = 0.0
        self.should_stop_worker = True
        with self.buffer_lock:
            self.ring_buffer.clear()
    
    def set_position(self, position_seconds: float):
        """Ustawia pozycję odtwarzania w sekundach."""
        if self.audio_data is not None:
            self.position = max(0.0, min(position_seconds, self.duration))
    
    def set_bpm(self, new_bpm: float):
        """Ustawia nowe BPM (zmienia prędkość odtwarzania)."""
        if self.original_bpm > 0:
            self.current_bpm = new_bpm
            self.pitch_ratio = new_bpm / self.original_bpm
    
    def set_volume(self, volume: float):
        """Ustawia głośność (0.0 - 1.0)."""
        self.volume = max(0.0, min(1.0, volume))
    
    def pop_audio_chunk(self, chunk_size: int) -> np.ndarray:
        """Pobiera gotowe próbki z ring bufora - SZYBKA operacja dla callbacku."""
        with self.buffer_lock:
            if len(self.ring_buffer) >= chunk_size:
                # Pobierz próbki z bufora
                chunk = np.array([self.ring_buffer.popleft() for _ in range(chunk_size)])
                return chunk.reshape(-1, 2) if chunk.ndim == 1 else chunk
            else:
                # Brak próbek - zwróć ciszę
                return np.zeros((chunk_size, 2), dtype=np.float32)
    
    def prepare_playback(self):
        """Przygotowuje odtwarzanie - pre-roll bufora."""
        if self.audio_data is not None and self.is_playing:
            self._fill_ring_buffer(4096)  # Pre-roll 4096 próbek
    
    def prepare_for_streaming(self):
        """Przygotowuje deck do streamingu - alias dla prepare_playback."""
        self.prepare_playback()
    
    def set_tempo(self, ratio: float):
        """Ustaw tempo (0.5 = połowa prędkości, 2.0 = podwójna prędkość)."""
        ratio = max(0.5, min(2.0, ratio))  # Ograniczenie do 0.5x-2.0x
        self.rate_target = ratio
        with self.tempo_lock:
            self.tempo_ratio = ratio
    
    def get_tempo(self) -> float:
        """Pobierz aktualne tempo."""
        return self.rate_smooth
    
    def _start_bpm_analysis(self):
        """Rozpoczyna analizę BPM w tle z cache."""
        if self.audio_data is None or not hasattr(self, 'track_path'):
            return
            
        # Sprawdź cache
        cache_path = self.track_path + '.bpm.json'
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cache_data = json.load(f)
                    bpm = cache_data.get('bpm')
                    if bpm and 60 <= bpm <= 200:
                        self.on_bpm_detected(bpm)
                        return
            except Exception:
                pass  # cache uszkodzony, rób analizę
        
        # Uruchom analizę w wątku daemon
        analysis_thread = threading.Thread(
            target=self._analyze_bpm_worker,
            args=(self.track_path, self.audio_data.copy(), self.sample_rate),
            daemon=True
        )
        analysis_thread.start()
    
    def _analyze_bpm_worker(self, file_path: str, audio_data: np.ndarray, sample_rate: int):
        """Worker thread dla analizy BPM."""
        try:
            bpm = None
            
            # Konwertuj do mono dla analizy
            if audio_data.ndim == 2:
                mono_audio = np.mean(audio_data, axis=1)
            else:
                mono_audio = audio_data
            
            # Próbuj aubio
            if AUBIO_AVAILABLE:
                try:
                    tempo = aubio.tempo("default", 1024, 512, sample_rate)
                    beats = []
                    
                    # Przetwarzaj w blokach
                    hop_size = 512
                    for i in range(0, len(mono_audio) - hop_size, hop_size):
                        block = mono_audio[i:i+hop_size].astype(np.float32)
                        is_beat = tempo(block)
                        if is_beat:
                            beats.append(tempo.get_last_s())
                    
                    if len(beats) > 1:
                        # Oblicz BPM z interwałów między beatami
                        intervals = np.diff(beats)
                        if len(intervals) > 0:
                            avg_interval = np.median(intervals)
                            if avg_interval > 0:
                                bpm = 60.0 / avg_interval
                except Exception as e:
                    print(f"Aubio analysis failed: {e}")
            
            # Fallback: librosa
            if bpm is None and LIBROSA_AVAILABLE:
                try:
                    import librosa
                    tempo, beats = librosa.beat.beat_track(
                        y=mono_audio, sr=sample_rate, units='time'
                    )
                    if tempo and tempo > 0:
                        bpm = float(tempo)
                except Exception as e:
                    print(f"Librosa analysis failed: {e}")
            
            # Normalizuj BPM do zakresu 60-200
            if bpm:
                while bpm < 60:
                    bpm *= 2
                while bpm > 200:
                    bpm /= 2
                
                if 60 <= bpm <= 200:
                    # Zapisz do cache
                    cache_path = file_path + '.bpm.json'
                    try:
                        with open(cache_path, 'w') as f:
                            json.dump({'bpm': bpm}, f)
                    except Exception:
                        pass  # nie krytyczne
                    
                    # Emituj sygnał
                    self.bpmReady.emit(bpm)
                    return
            
            # Brak wyniku
            self.analysisFailed.emit("Nie udało się wykryć BPM")
            
        except Exception as e:
            self.analysisFailed.emit(f"Błąd analizy BPM: {str(e)}")
    
    def on_bpm_detected(self, bpm: float):
        """Callback po wykryciu BPM - tylko zapisuje wynik, nie zmienia tempa."""
        self.detected_bpm = bpm
        print(f"Deck {self.deck_id}: Wykryto BPM: {bpm:.1f}")
    
    def set_bpm_target(self, bpm: float):
        """Ustawia docelowe BPM z gałki UI."""
        self.bpm_target = bpm
        if self.detected_bpm and self.detected_bpm > 0:
            self.rate_target = float(bpm) / float(self.detected_bpm)
        else:
            # brak detekcji: traktuj gałkę jako mnożnik (bpm=100 -> 1.0)
            self.rate_target = bpm / 100.0
    
    def _smooth_rate(self):
        """Płynne wygładzanie tempa bez klików."""
        current_time = time.time()
        if self._last_smooth_t == 0.0:
            self._last_smooth_t = current_time
            return
        
        dt_sec = current_time - self._last_smooth_t
        self._last_smooth_t = current_time
        
        # stała wygładzania ~30 ms
        tau = 0.03
        alpha = min(1.0, dt_sec / tau)
        self.rate_smooth += alpha * (self.rate_target - self.rate_smooth)
    
    def apply_ramp(self, block: np.ndarray, g0: float, g1: float):
        """Aplikuje mini-rampę przy ostrych zmianach (5-10ms)."""
        n = block.shape[0]
        ramp = np.linspace(g0, g1, n, dtype=np.float32)[:, None]
        block *= ramp
    
    def render_varispeed_block(self, src: np.ndarray, phase: float, rate: float, n_out: int):
        """Renderuje blok z varispeed używając interpolacji liniowej."""
        if src is None or len(src) == 0:
            return np.zeros((n_out, 2), dtype=np.float32), phase
        
        n_src = src.shape[0]
        idx = phase + rate * np.arange(n_out, dtype=np.float32)
        i0 = np.floor(idx).astype(np.int64)
        frac = (idx - i0).astype(np.float32)
        
        i0 = np.clip(i0, 0, n_src - 2)
        i1 = i0 + 1
        
        s0 = src[i0]
        s1 = src[i1]
        out = s0 + (s1 - s0) * frac[:, None]
        
        new_phase = phase + rate * n_out
        return out.astype(np.float32), new_phase
    
    def _render_chunk_with_varispeed(self, size: int) -> Optional[np.ndarray]:
        """Renderuje chunk z nowym varispeed i ciągłą fazą."""
        if self.audio_data is None:
            return None
        
        # Oblicz ile próbek źródłowych potrzebujemy
        src_samples_needed = int(size * self.rate_smooth * 1.5)  # z zapasem
        
        # Pobierz źródłowe próbki
        position_samples = int(self.position * self.sample_rate)
        end_pos = min(position_samples + src_samples_needed, len(self.audio_data))
        if position_samples >= len(self.audio_data):
            return None
        
        src_chunk = self.audio_data[position_samples:end_pos]
        
        # Renderuj z varispeed
        output_chunk, new_phase = self.render_varispeed_block(
            src_chunk, self.phase, self.rate_smooth, size
        )
        
        # Aktualizuj pozycję i fazę
        samples_consumed = int(new_phase - self.phase)
        self.position += samples_consumed / self.sample_rate
        self.phase = new_phase - samples_consumed  # zachowaj ułamkową część
        
        return output_chunk
    
    def _resample_chunk(self, chunk: np.ndarray, tempo_ratio: float) -> np.ndarray:
        """Zmienia tempo audio chunk przez resampling."""
        if chunk.size == 0 or tempo_ratio == 1.0:
            return chunk
        
        try:
            # Dla stereo - przetwarzaj każdy kanał osobno
            if chunk.ndim == 2 and chunk.shape[1] == 2:
                left = signal.resample(chunk[:, 0], int(len(chunk) * tempo_ratio))
                right = signal.resample(chunk[:, 1], int(len(chunk) * tempo_ratio))
                return np.column_stack((left, right)).astype(np.float32)
            else:
                # Mono
                resampled = signal.resample(chunk, int(len(chunk) * tempo_ratio))
                return resampled.astype(np.float32)
        except Exception as e:
            print(f"Błąd resamplingu w deck {self.deck_id}: {e}")
            return chunk
    
    def _fill_ring_buffer(self, target_frames: int):
        """Napełnia ring buffer próbkami (worker thread) z kontrolą tempa."""
        if not self.is_playing or self.audio_data is None:
            return
        
        with self.buffer_lock:
            current_size = len(self.ring_buffer)
            needed_frames = min(target_frames - current_size, self.ring_buffer.maxlen - current_size)
            
            if needed_frames <= 0:
                return
            
            # Oblicz pozycję w próbkach
            start_sample = int(self.position * self.sample_rate)
            
            # Sprawdź czy nie przekroczyliśmy końca utworu
            if start_sample >= len(self.audio_data):
                self.is_playing = False
                return
            
            # Pobierz fragment z uwzględnieniem tempo ratio
            with self.tempo_lock:
                current_tempo = self.tempo_ratio
            
            if current_tempo != 1.0:
                # Użyj scipy do resamplingu
                actual_chunk_size = int(needed_frames / current_tempo)
                end_sample = min(start_sample + actual_chunk_size, len(self.audio_data))
                
                if end_sample > start_sample:
                    chunk = self.audio_data[start_sample:end_sample]
                    
                    # Resample z scipy
                    if len(chunk) > 0:
                        chunk = self._resample_chunk(chunk, current_tempo)
                        # Dopasuj do needed_frames
                        if len(chunk) > needed_frames:
                            chunk = chunk[:needed_frames]
                        elif len(chunk) < needed_frames:
                            padding = np.zeros((needed_frames - len(chunk), 2), dtype=np.float32)
                            chunk = np.vstack([chunk, padding])
                    else:
                        chunk = np.zeros((needed_frames, 2), dtype=np.float32)
                else:
                    chunk = np.zeros((needed_frames, 2), dtype=np.float32)
            else:
                end_sample = min(start_sample + needed_frames, len(self.audio_data))
                chunk = self.audio_data[start_sample:end_sample]
                
                # Dopełnij zerami jeśli za krótki
                if len(chunk) < needed_frames:
                    padding = np.zeros((needed_frames - len(chunk), 2), dtype=np.float32)
                    chunk = np.vstack([chunk, padding])
            
            # Zastosuj głośność
            chunk = chunk * self.volume
            
            # Aktualizuj pozycję
            time_increment = needed_frames / self.sample_rate
            self.position += time_increment
            
            # Dodaj do ring bufora
            for frame in chunk:
                self.ring_buffer.append(frame)
    
    def _worker_thread_func(self):
        """Worker thread do napełniania ring bufora z nową logiką varispeed."""
        last_time = time.time()
        
        while not self.should_stop_worker:
            try:
                current_time = time.time()
                dt = current_time - last_time
                last_time = current_time
                
                if not self.is_playing or self.audio_data is None:
                    time.sleep(0.001)  # 1ms sleep gdy nie gramy
                    continue
                
                # Płynne wygładzanie tempa
                self._smooth_rate()
                
                # Utrzymuj bufor z nowym varispeed
                self._fill_ring_buffer(2048)
                
            except Exception as e:
                print(f"Deck {self.deck_id}: Błąd w worker: {e}")
                time.sleep(0.01)
    
    def get_current_audio_chunk(self, chunk_size: int) -> np.ndarray:
        """Pobiera aktualny fragment audio do odtwarzania (zachowane dla kompatybilności)."""
        return self.pop_audio_chunk(chunk_size)
    
    def _playback_loop(self):
        """Główna pętla odtwarzania (uruchamiana w osobnym wątku)."""
        while not self._stop_event.is_set() and self.is_playing:
            if not self.is_paused:
                # Symulacja odtwarzania - w rzeczywistości audio jest pobierane przez mixer
                time.sleep(0.01)  # 10ms
            else:
                time.sleep(0.1)
    
    def get_position_percent(self) -> float:
        """Zwraca pozycję jako procent (0.0 - 1.0)."""
        if self.duration > 0:
            return min(1.0, self.position / self.duration)
        return 0.0
    
    def get_remaining_time(self) -> float:
        """Zwraca pozostały czas w sekundach."""
        return max(0.0, self.duration - self.position)
    
    def is_loaded(self) -> bool:
        """Sprawdza czy utwór jest załadowany."""
        return self.audio_data is not None
    
    def sync_to_deck(self, master_deck):
        """Synchronizuje BPM tego decka do master decka."""
        if (hasattr(master_deck, 'detected_bpm') and master_deck.detected_bpm and 
            hasattr(self, 'detected_bpm') and self.detected_bpm):
            # Oblicz target BPM na podstawie master decka
            master_bpm = master_deck.detected_bpm
            if hasattr(master_deck, 'bmp_target') and master_deck.bpm_target:
                master_bpm = master_deck.bpm_target
            
            # Ustaw target BPM dla tego decka
            self.set_bpm_target(master_bpm)
    
    def get_info(self) -> dict:
        """Zwraca informacje o aktualnym utworze."""
        return {
            'track_name': self.track_name,
            'duration': self.duration,
            'position': self.position,
            'position_percent': self.get_position_percent(),
            'remaining_time': self.get_remaining_time(),
            'is_playing': self.is_playing,
            'is_paused': self.is_paused,
            'volume': self.volume,
            'current_bpm': self.current_bpm,
            'original_bpm': self.original_bpm,
            'pitch_ratio': self.pitch_ratio,
            'detected_bpm': getattr(self, 'detected_bpm', None),
            'bpm_target': getattr(self, 'bpm_target', None),
            'rate_smooth': getattr(self, 'rate_smooth', 1.0)
        }