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
import hashlib
import logging
from pathlib import Path
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal
from .time_stretch import TimeStretchEngine
from .key_analyzer import KeyAnalyzer
from .audio_clock import AudioClock
from .master_clock import get_master_clock
from .tempo_phase_sync import get_tempo_phase_sync
from .cache_manager import (
    BMP_CACHE, AnalysisResult, generate_track_uid, 
    get_bmp, store_bmp, load_from_file_cache, save_to_file_cache
)

@dataclass
class TrackMeta:
    """Metadata utworu z unique ID."""
    uid: str
    path: str
    sr: int
    duration: float
    size: int
    mtime: float

# Logger setup
log = logging.getLogger(__name__)

# Initialize availability flags
AUBIO_AVAILABLE = False
LIBROSA_AVAILABLE = False

try:
    import aubio
    AUBIO_AVAILABLE = True
except ImportError:
    pass

try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    pass


class Deck(QObject):
    """Pojedynczy deck DJ z kontrolą odtwarzania i BPM."""
    
    # Sygnały Qt
    bpmReady = Signal(float)  # emitowany gdy BPM zostanie wykryte
    keyReady = Signal(dict)  # emitowany gdy klucz zostanie wykryty
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
        
        # Ring buffer dla stabilnego audio - zwiększony dla lepszej wydajności
        self.ring_buffer = deque(maxlen=int(48000 * 3.0))  # 3 sekundy bufora
        self.buffer_lock = threading.Lock()
        self.worker_thread = None
        self.should_stop_worker = False
        
        # Kontrola odtwarzania
        self.is_playing = False
        self.is_paused = False
        self.position = 0.0  # pozycja w sekundach
        self.volume = 1.0  # 0.0 - 1.0
        
        # BPM i tempo control (zgodnie ze specyfikacją)
        self.detected_bpm: float = 0.0           # wynik analizy BPM
        self.bmp_confidence: Optional[float] = None  # pewność analizy BPM
        self.bpm_status: str = "none"            # "pending|ok|none"
        self.path: Optional[Path] = None         # ścieżka do pliku jako Path object
        self.bpm_target: Optional[float] = None     # wartość z gałki BPM
        self.rate_target: float = 1.0            # target varispeed (= bpm_target/detected_bpm)
        self.rate_smooth: float = 1.0            # płynnie dociągane tempo
        self.phase: float = 0.0                  # faza dla varispeed
        
        # TempoMap integration
        self.tempo_map: Optional['TempoMap'] = None  # źródło prawdy dla beatgrid
        self.current_uid: Optional[str] = None       # UID utworu dla cache
        
        # SYNC BPM configuration
        self.pitch_range_key: str = '±8'         # aktualny preset zakresu pitch
        self.PITCH_RANGES = {
            '±8':  (0.92, 1.08),
            '±16': (0.84, 1.16),
            '±50': (0.50, 1.50),
        }
        self._last_smooth_t: float = 0.0         # czas ostatniego smooth_rate
        
        # Key detection (analiza klucza muzycznego)
        self.key_detected: Optional[dict] = None  # wynik analizy klucza
        self.key_status: str = "none"             # "pending|ok|none"
        self.key_analyzer = KeyAnalyzer()         # analizator klucza
        
        # Legacy kontrola BPM (do usunięcia)
        self.original_bpm = 120.0
        self.current_bpm = 120.0
        self.pitch_ratio = 1.0  # stosunek prędkości odtwarzania
        
        # Threading
        self._playback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Callback dla audio output
        self.audio_callback: Optional[Callable] = None
        
        # Spectrum analyzer callback
        self.spectrum_callback: Optional[Callable] = None
        
        # Time-stretch engine
        self.time_stretch_engine = TimeStretchEngine(self.sample_rate)
        self.key_lock = False  # Key Lock state
        
        # Nudge/Pitch-bend system
        self.nudge_ratio = 1.0  # aktualny mnożnik nudge
        self.nudge_target = 1.0  # docelowy mnożnik nudge
        self.smoothing_ms = 40  # stała czasowa wygładzania (ms)
        self.nudge_active = False  # czy nudge jest aktywny (trzymany przycisk)
        
        # Informacje o utworze
        self.track_path: Optional[str] = None
        self.track_name: str = ""
        self.duration: float = 0.0
        
        # System ochrony przed wyścigami asynchronicznymi
        self.current_uid: Optional[str] = None
        self.load_token: int = 0
        self.bmp_confidence: Optional[float] = None
        
        # Audio clock dla synchronizacji czasu
        self.clock = AudioClock(sr=self.sample_rate)
        
        # MasterClock jako źródło prawdy dla czasu
        self.master_clock = get_master_clock(self.sample_rate)
        
        # Tempo+Phase Sync system
        self.tempo_phase_sync = get_tempo_phase_sync(self.sample_rate)
        
    def effective_ratio(self) -> float:
        """Zwraca efektywny współczynnik odtwarzania (tempo * nudge)."""
        return self.tempo_ratio * self.nudge_ratio
    
    def set_tempo(self, r: float):
        """Ustawia tempo ratio i aplikuje zmiany."""
        self.tempo_ratio = r
        self._apply_ratio()
    
    def set_nudge_target(self, r: float):
        """Ustawia nudge ratio i aplikuje zmiany."""
        self.nudge_ratio = r
        self._apply_ratio()
    
    def _apply_ratio(self):
        """Aplikuje efektywny ratio do audio engine i innych komponentów."""
        eff = self.effective_ratio()
        
        # Aktualizuj time-stretch engine
        if hasattr(self, 'time_stretch_engine') and self.time_stretch_engine:
            if self.key_lock:
                # Key lock - użyj time-stretch (zachowuje pitch)
                self.time_stretch_engine.set_tempo(eff)
            else:
                # Bez key lock - użyj resampling (zmienia pitch)
                self.time_stretch_engine.set_playback_rate(eff)
        
        # Aktualizuj rate_smooth dla kompatybilności z istniejącym kodem
        self.rate_smooth = eff
        self.rate_target = eff

    def load_track(self, file_path: str) -> bool:
        """Ładuje utwór z pliku audio."""
        try:
            # Ustaw Path object
            self.path = Path(file_path)
            log.info("LOAD %s", self.path.name)
            
            # Generuj unique ID i increment loading token
            new_uid = generate_track_uid(file_path)
            self.current_uid = new_uid
            self.load_token += 1
            current_token = self.load_token
            
            log.info("Track UID: %s, Token: %d", new_uid, current_token)
            
            # Sprawdź cache przed analizą
            cached_result = get_bmp(self.current_uid)
            if not cached_result:
                # Spróbuj załadować z pliku cache
                cached_result = load_from_file_cache(file_path)
                
            if cached_result and cached_result.bmp and cached_result.bmp > 0:
                # Użyj wyniku z cache
                self.detected_bpm = cached_result.bmp
                self.bmp_confidence = cached_result.confidence
                self.bpm_status = "ok"
                log.info("BPM from cache: %.1f (conf: %.2f)", 
                        cached_result.bmp, cached_result.confidence or 1.0)
                # Emit signal dla UI
                self.bpmReady.emit(cached_result.bmp)
            else:
                # Reset BPM status - będzie analizowane
                self.detected_bpm = 0.0
                self.bmp_confidence = None
                self.bpm_status = "pending"
                
            # Sprawdź cache dla klucza
            if cached_result and cached_result.key_display:
                self.key_detected = {"display": cached_result.key_display, "note": cached_result.key_note}
                self.key_status = "ok"
                log.info("Key from cache: %s", cached_result.key_display)
                # Emit signal dla UI
                self.keyReady.emit(self.key_detected)
            else:
                # Reset key status
                self.key_detected = None
                self.key_status = "pending"
            
            self.audio_data, self.sample_rate = sf.read(file_path)
            
            # Konwersja do stereo jeśli mono
            if len(self.audio_data.shape) == 1:
                self.audio_data = np.column_stack((self.audio_data, self.audio_data))
            
            self.channels = self.audio_data.shape[1]
            self.total_frames = len(self.audio_data)
            self.duration = self.total_frames / self.sample_rate
            self.track_path = file_path
            self.track_name = self.path.name
            
            # Reset pozycji
            self.position = 0.0
            self.is_playing = False
            self.is_paused = False
            self.clock.reset()
            
            # Automatyczna normalizacja - oblicz RMS amplitude
            rms = np.sqrt(np.mean(self.audio_data ** 2))
            target_rms = 0.2  # Docelowy poziom RMS (około -14dB)
            
            if rms > 0:
                # Oblicz współczynnik normalizacji
                normalization_factor = target_rms / rms
                # Ograniczenie do rozsądnych wartości (0.1x - 10x)
                normalization_factor = max(0.1, min(10.0, normalization_factor))
                
                # Zastosuj normalizację do audio_data
                self.audio_data = self.audio_data * normalization_factor
                
                log.info("Track %s: RMS=%.4f, normalizacja=%.2fx", self.path.name, rms, normalization_factor)
            
            # Reset tempa do 1.0 (domyślne tempo odtwarzania)
            self.rate_target = 1.0
            self.rate_smooth = 1.0
            self.bpm_target = None
            self.phase = 0.0
            

            
            # Rozpocznij analizę BPM w tle (tylko jeśli nie ma w cache)
            if self.bpm_status == "pending":
                self._start_bmp_analysis()
            
            # Rozpocznij analizę klucza w tle (tylko jeśli nie ma w cache)
            if self.key_status == "pending":
                self._start_key_analysis()
            
            return True
            
        except Exception as e:
            log.error("Błąd ładowania utworu %s: %s", file_path, e)
            return False
    
    def play(self):
        """Rozpoczyna odtwarzanie."""
        if self.audio_data is None:
            return
            
        # Zawsze startuj w tempie 1.0
        self.rate_target = 1.0
        self.rate_smooth = 1.0
        
        # Uruchom AudioClock
        start_samples = int(self.position * self.sample_rate)
        self.clock.play_from_samples(start_samples)
            
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
        self.clock.pause()
    
    def stop(self):
        """Zatrzymuje odtwarzanie i resetuje pozycję."""
        self.is_playing = False
        self.is_paused = False
        self._stop_event.set()
        self.position = 0.0
        self.should_stop_worker = True
        self.clock.reset()
        with self.buffer_lock:
            self.ring_buffer.clear()
    
    def set_position(self, position_seconds: float):
        """Ustawia pozycję odtwarzania w sekundach."""
        if self.audio_data is not None:
            self.position = max(0.0, min(position_seconds, self.duration))
    
    def seek_to(self, position_seconds: float):
        """Mikro-przesunięcie pozycji dla phase correction (nudge-as-phase)."""
        if self.audio_data is not None:
            self.position = max(0.0, min(position_seconds, self.duration))
            # Wyczyść buffer żeby uniknąć artefaktów
            with self.buffer_lock:
                self.ring_buffer.clear()
    
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
            buffer_size = len(self.ring_buffer)
            
            if buffer_size >= chunk_size:
                # Pobierz próbki z bufora
                chunk = np.array([self.ring_buffer.popleft() for _ in range(chunk_size)])
                audio_chunk = chunk.reshape(-1, 2) if chunk.ndim == 1 else chunk
                
                # Log status (tylko co 1000 chunków, żeby nie spamować)
                if hasattr(self, '_chunk_counter'):
                    self._chunk_counter += 1
                else:
                    self._chunk_counter = 1
                    
                if self._chunk_counter % 1000 == 0:
                    remaining_ms = (buffer_size - chunk_size) * 1000 / 48000
                    print(f"Deck {getattr(self, 'name', '?')}: buffer={remaining_ms:.1f}ms, playing={self.is_playing}")
                
                # Przekaż dane do spectrum analyzer jeśli jest podłączony
                if self.spectrum_callback and self.is_playing:
                    try:
                        self.spectrum_callback(audio_chunk)
                    except Exception as e:
                        # Log błąd spectrum analyzer (rzadko)
                        if self._chunk_counter % 5000 == 0:
                            print(f"Spectrum callback error: {e}")
                
                # Zaktualizuj AudioClock
                self.clock.on_audio_callback(chunk_size)
                
                return audio_chunk
            else:
                # Brak próbek - zwróć ciszę (fallback bez powtarzania starych bloków)
                if self.is_playing and hasattr(self, '_underrun_counter'):
                    self._underrun_counter += 1
                    if self._underrun_counter % 100 == 1:  # Log co 100 underrunów
                        print(f"Audio underrun on deck {getattr(self, 'name', '?')}: buffer empty, returning silence")
                elif self.is_playing:
                    self._underrun_counter = 1
                    print(f"Audio underrun on deck {getattr(self, 'name', '?')}: buffer empty, returning silence")
                    
                return np.zeros((chunk_size, 2), dtype=np.float32)
    
    def prepare_playback(self):
        """Przygotowuje odtwarzanie - pre-roll bufora."""
        if self.audio_data is not None and self.is_playing:
            self._fill_ring_buffer(8192)  # Pre-roll 8192 próbek dla lepszej stabilności
    
    def prepare_for_streaming(self):
        """Przygotowuje deck do streamingu - alias dla prepare_playback."""
        self.prepare_playback()
    
    def set_tempo(self, ratio: float):
        """Ustaw tempo (0.5 = połowa prędkości, 2.0 = podwójna prędkość)."""
        ratio = max(0.5, min(2.0, ratio))  # Ograniczenie do 0.5x-2.0x
        self.rate_target = ratio
        with self.tempo_lock:
            self.tempo_ratio = ratio
        # Aktualizuj time-stretch engine
        self.time_stretch_engine.set_tempo(ratio)
    
    def get_tempo(self) -> float:
        """Pobierz aktualne tempo."""
        return self.rate_smooth
    
    # Metoda get_effective_tempo() została zastąpiona przez effective_ratio()
    
    def get_nudge_ratio(self) -> float:
        """Pobierz aktualny mnożnik nudge."""
        return self.nudge_ratio
    
    def get_nudge_percent(self) -> float:
        """Pobierz nudge w procentach (np. 1.04 -> +4.0%)."""
        return (self.nudge_ratio - 1.0) * 100.0
    
    def set_key_lock(self, enabled: bool):
        """Włącz/wyłącz Key Lock."""
        self.key_lock = enabled
        self.time_stretch_engine.set_key_lock(enabled)
    
    def is_key_lock_enabled(self) -> bool:
        """Sprawdź czy Key Lock jest włączony."""
        return self.key_lock
    
    def get_time_stretch_status(self) -> dict:
        """Zwraca status time-stretch engine."""
        return self.time_stretch_engine.get_status_info()
    
    def start_nudge(self, pct: float):
        """Rozpoczyna nudge z podanym procentem (np. +0.04 = +4%, -0.04 = -4%)."""
        self.nudge_target = 1.0 + pct
        self.nudge_active = True
        self.set_nudge_target(self.nudge_target)
    
    def stop_nudge(self):
        """Zatrzymuje nudge - wraca do normalnego tempa."""
        self.nudge_target = 1.0
        self.nudge_active = False
        self.set_nudge_target(1.0)
    
    def enable_tempo_phase_sync(self, master_deck, enabled: bool = True):
        """Włącz/wyłącz zaawansowaną synchronizację tempo i fazy.
        
        Args:
            master_deck: Master deck do synchronizacji
            enabled: Czy włączyć synchronizację
        """
        if enabled and master_deck:
            self.tempo_phase_sync.set_decks(self, master_deck)
            self.tempo_phase_sync.enable_sync(True)
            log.info(f"Tempo+Phase sync enabled for deck {self.deck_id} -> master {master_deck.deck_id}")
        else:
            self.tempo_phase_sync.enable_sync(False)
            log.info(f"Tempo+Phase sync disabled for deck {self.deck_id}")
    
    def get_tempo_phase_sync_state(self) -> dict:
        """Pobierz stan synchronizacji tempo i fazy.
        
        Returns:
            Słownik z informacjami o stanie synchronizacji
        """
        return self.tempo_phase_sync.get_sync_state()
    
    def tap_nudge(self, ms: int):
        """Wykonuje krótkie przesunięcie fazy (tap nudge) w milisekundach."""
        if self.audio_data is None:
            return
        
        samples = int(self.sample_rate * abs(ms) / 1000.0)
        
        if ms > 0:
            # Przesunięcie do przodu
            self.position += samples / self.sample_rate
        else:
            # Przesunięcie do tyłu
            self.position = max(0, self.position - samples / self.sample_rate)
        
        # Upewnij się, że nie przekroczyliśmy końca utworu
        if self.audio_data is not None:
            max_position = len(self.audio_data) / self.sample_rate
            self.position = min(self.position, max_position)
    
    # Metoda _apply_effective_ratio() została zastąpiona przez _apply_ratio()
    
    def phase_follow_tick(self, master_deck, strength: float = 0.15):
        """Sprężynowa korekta fazy względem master decka (nudge-as-phase).
        
        DEPRECATED: Używaj enable_tempo_phase_sync() dla lepszej synchronizacji.
        
        Args:
            master_deck: Master deck do synchronizacji
            strength: Siła korekcji (0.0-1.0), domyślnie 0.15
        """
        if not self.nudge_active or master_deck is None:
            return
            
        # Sprawdź czy używamy nowego systemu Tempo+Phase Sync
        sync_state = self.tempo_phase_sync.get_sync_state()
        if sync_state['enabled']:
            # Nowy system jest aktywny - aktualizuj synchronizację
            self.tempo_phase_sync.update_sync()
            return
            
        if not hasattr(master_deck, 'detected_bpm') or master_deck.detected_bpm <= 0:
            return
            
        if not hasattr(master_deck, 'beat_offset'):
            master_deck.beat_offset = 0.0  # fallback
        if not hasattr(self, 'beat_offset'):
            self.beat_offset = 0.0  # fallback
            
        # Oblicz sekundy na beat dla mastera
        spb = 60.0 / (master_deck.detected_bpm * master_deck.effective_ratio())
        
        # Pobierz aktualne czasy z zegarów
        tM = master_deck.clock.now_seconds()
        tS = self.clock.now_seconds()
        
        # Oblicz pozycje w beatach względem beat_offset
        iM = (tM - master_deck.beat_offset) / spb
        iS = (tS - self.beat_offset) / spb
        
        # Błąd fazy w beatach
        err_beats = iM - iS
        
        # Normalizuj do (-0.5, 0.5] - najbliższa różnica fazy
        err_beats = (err_beats + 0.5) % 1.0 - 0.5
        
        # Oblicz krok korekcji w sekundach
        dt = (err_beats * spb) * strength
        
        # Ogranicz maksymalną korekcję na klatkę (max 5ms)
        max_correction = 0.005  # 5ms
        dt = max(-max_correction, min(max_correction, dt))
        
        # Aplikuj korekcję jeśli znacząca
        if abs(dt) > 1e-4:
            new_position = tS + dt
            self.seek_to(new_position)
    
    def _start_bmp_analysis(self):
        """Rozpoczyna analizę BPM w tle z centralnym cache."""
        if self.audio_data is None or not hasattr(self, 'track_path'):
            return
            
        log.info("BPM start %s", self.path.name)
        
        # Sprawdź centralny cache (już sprawdzony w load_track, ale dla pewności)
        if hasattr(self, 'current_uid') and self.current_uid:
            cached_result = get_bmp(self.current_uid)
            if cached_result and cached_result.bmp and 60 <= cached_result.bmp <= 180:
                log.info("BPM already in cache: %.1f", cached_result.bmp)
                return
        
        # Uruchom analizę w wątku daemon z aktualnym tokenem
        current_token = self.load_token
        analysis_thread = threading.Thread(
            target=self._analyze_bmp_worker,
            args=(self.track_path, self.audio_data.copy(), self.sample_rate, current_token),
            daemon=True
        )
        analysis_thread.start()
    
    def _start_key_analysis(self):
        """Rozpoczyna analizę klucza muzycznego w tle."""
        if self.audio_data is None or not hasattr(self, 'track_path'):
            return
            
        log.info("Key start %s", self.path.name)
        
        # Uruchom analizę w wątku daemon z aktualnym tokenem
        current_token = self.load_token
        key_thread = threading.Thread(
            target=self._analyze_key_worker,
            args=(self.track_path, current_token),
            daemon=True
        )
        key_thread.start()
    
    def _analyze_key_worker(self, file_path: str, token: int):
        """Worker thread dla analizy klucza muzycznego."""
        try:
            # Wykonaj analizę klucza
            key_result = self.key_analyzer.analyze_key(file_path)
            
            # Apply-if-current: sprawdź czy token wciąż aktualny
            if token != self.load_token:
                log.info("Key analysis result dropped: token %d != current %d", token, self.load_token)
                return
                
            if key_result:
                # Wywołaj callback w głównym wątku
                self.on_key_detected(key_result, token)
            else:
                log.warning("Nie udało się wykryć klucza dla %s", self.path.name)
                if token == self.load_token:  # Tylko jeśli wciąż aktualny
                    self.key_status = "none"
                
        except Exception as e:
            log.error("Błąd analizy klucza %s: %s", file_path, e)
            if token == self.load_token:  # Tylko jeśli wciąż aktualny
                self.key_status = "none"
    
    def _analyze_bpm_worker(self, file_path: str, audio_data: np.ndarray, sample_rate: int, token: int):
        """Worker thread dla solidnej analizy BPM zgodnie ze specyfikacją."""
        # Import availability flags
        global AUBIO_AVAILABLE, LIBROSA_AVAILABLE
        
        try:
            import datetime
            
            # === ETAP 1: Wejście audio → mono 44.1 kHz ===
            try:
                if LIBROSA_AVAILABLE:
                    import librosa
                    y, sr = librosa.load(file_path, sr=44100, mono=True)
                else:
                    # Fallback: soundfile + prosty resample
                    import soundfile as sf
                    y, orig_sr = sf.read(file_path)
                    if y.ndim == 2:
                        y = np.mean(y, axis=1)  # downmix do mono
                    if orig_sr != 44100:
                        # Prosty resample z scipy
                        from scipy import signal as scipy_signal
                        y = scipy_signal.resample(y, int(len(y) * 44100 / orig_sr))
                    sr = 44100
            except Exception as e:
                self.analysisFailed.emit(f"Błąd ładowania audio: {str(e)}")
                return
            
            # === ETAP 2: Wstępne oczyszczenie sygnału ===
            # Normalizacja
            y = y / (np.max(np.abs(y)) + 1e-9)
            
            # Delikatny HPF (20–30 Hz) - jeśli mamy scipy
            try:
                from scipy import signal as scipy_signal
                b, a = scipy_signal.butter(1, 30/22050, 'high')
                y = scipy_signal.lfilter(b, a, y)
            except ImportError:
                pass  # pomiń jeśli brak scipy
            
            # === ETAP 3: Wycinanie ciszy na początku/końcu ===
            if LIBROSA_AVAILABLE:
                try:
                    yt, _ = librosa.effects.trim(y, top_db=30)
                    y = yt if len(yt) > 44100 else y  # zostaw oryginał jeśli za krótki
                except Exception:
                    pass  # zostaw oryginał
            
            # === ETAP 4: Stałe analizy ===
            SR = 44100
            HOP = 512
            WIN = 2048
            
            # === ETAP 5: Onset envelope (energia transjentów) ===
            onset_env = None
            if LIBROSA_AVAILABLE:
                try:
                    onset_env = librosa.onset.onset_strength(
                        y=y, sr=SR, hop_length=HOP, aggregate=np.median
                    )
                except Exception as e:
                    print(f"Onset envelope failed: {e}")
            
            # === ETAP 6: Ścieżka 1 - aubio (zoptymalizowany) ===
            aubio_candidates = []
            if AUBIO_AVAILABLE:
                try:
                    # Użyj różnych metod aubio dla lepszej dokładności
                    methods = ["default", "specdiff", "energy", "hfc"]
                    
                    for method in methods:
                        try:
                            tempo_detector = aubio.tempo(method, WIN, HOP, SR)
                            beats = []
                            
                            # Przetwarzaj w blokach hop=512
                            for i in range(0, len(y) - HOP, HOP):
                                block = y[i:i+HOP].astype(np.float32)
                                is_beat = tempo_detector(block)
                                if is_beat:
                                    beat_time = tempo_detector.get_last_s()
                                    if beat_time > 0:
                                        beats.append(beat_time)
                            
                            if len(beats) >= 3:  # wymagaj więcej beatów
                                # Oblicz interwały i BPM
                                intervals = np.diff(beats)
                                if len(intervals) > 0:
                                    # Usuń outliers (interwały poza 2 std dev)
                                    mean_interval = np.mean(intervals)
                                    std_interval = np.std(intervals)
                                    filtered_intervals = intervals[
                                        np.abs(intervals - mean_interval) <= 2 * std_interval
                                    ]
                                    
                                    if len(filtered_intervals) > 0:
                                        median_interval = np.median(filtered_intervals)
                                        if median_interval > 0:
                                            aubio_bpm = 60.0 / median_interval
                                            if 60 <= aubio_bpm <= 180:  # sprawdź zakres od razu
                                                aubio_candidates.append(aubio_bpm)
                                                print(f"Aubio {method}: {aubio_bpm:.1f} BPM")
                        except Exception as method_e:
                            print(f"Aubio method {method} failed: {method_e}")
                            continue
                            
                except Exception as e:
                    print(f"Aubio analysis failed: {e}")
            
            # === ETAP 7: Ścieżka 2 - librosa (fallback) ===
            librosa_candidates = []
            if LIBROSA_AVAILABLE and onset_env is not None:
                try:
                    # Użyj przygotowanego onset_env
                    tempi = librosa.beat.tempo(
                        onset_envelope=onset_env, sr=SR, hop_length=HOP, aggregate=None
                    )
                    if tempi is not None and len(tempi) > 0:
                        librosa_candidates.extend(tempi.flatten())
                        
                    # Dodatkowa metoda: beat_track
                    tempo_bt, _ = librosa.beat.beat_track(
                        y=y, sr=SR, hop_length=HOP, units='time'
                    )
                    if tempo_bt and tempo_bt > 0:
                        librosa_candidates.append(float(tempo_bt))
                        
                except Exception as e:
                    print(f"Librosa analysis failed: {e}")
            
            # === ETAP 8: Korekcja pół/double-tempo do 60-180 ===
            def fold_60_180(v):
                while v < 60:
                    v *= 2
                while v > 180:
                    v /= 2
                return v
            
            # Zbierz wszystkich kandydatów
            all_candidates = []
            for candidate in aubio_candidates + librosa_candidates:
                if candidate and candidate > 0:
                    folded = fold_60_180(float(candidate))
                    if 60 <= folded <= 180:
                        all_candidates.append(folded)
            
            # === ETAP 9: Wybór BPM - trymowana mediana z top-k ===
            final_bpm = None
            confidence = None
            
            if len(all_candidates) >= 1:
                # Weź top 3-5 kandydatów
                candidates = sorted(all_candidates)[:5]
                
                if len(candidates) >= 3:
                    # Odrzuć skrajne 10/90 percentyl
                    p10 = np.percentile(candidates, 10)
                    p90 = np.percentile(candidates, 90)
                    trimmed = [c for c in candidates if p10 <= c <= p90]
                    if trimmed:
                        final_bpm = np.median(trimmed)
                        # Oblicz confidence na podstawie zgodności kandydatów
                        std_dev = np.std(trimmed)
                        confidence = max(0.1, min(1.0, 1.0 - (std_dev / 20.0)))  # im mniejsze odchylenie, tym wyższa confidence
                else:
                    final_bpm = np.median(candidates)
                    # Niższa confidence dla małej liczby kandydatów
                    confidence = 0.5 if len(candidates) == 2 else 0.3
                
                # Zaokrąglij do 0.1
                if final_bpm:
                    final_bpm = round(float(final_bpm), 1)
                    
                # Dodatkowa penalizacja confidence na podstawie liczby kandydatów
                if confidence is not None:
                    candidate_factor = min(1.0, len(all_candidates) / 5.0)  # im więcej kandydatów, tym lepiej
                    confidence *= candidate_factor
            
            # === ETAP 10: Heurystyka "pewności" ===
            if final_bpm is None:
                # Sprawdź czy onset_env ma bardzo niski SNR
                if LIBROSA_AVAILABLE and onset_env is not None:
                    onset_median = np.median(onset_env)
                    if onset_median < 0.01:  # bardzo niski próg
                        self.analysisFailed.emit("Sygnał za słaby dla detekcji BPM")
                        return
                
                self.analysisFailed.emit("Nie udało się wykryć BPM")
                return
            
            # === ETAP 10.5: Normalizacja BPM i sanity-check ===
            # Sprawdź czy wykryty BPM nie jest przypadkiem 0.5x lub 2x rzeczywistego BPM
            if final_bpm is not None:
                # Typowe zakresy BPM dla różnych gatunków:
                # House/Techno: 120-140, Hip-hop: 70-140, Rock: 120-180, Ballady: 60-100
                
                # Jeśli BPM jest bardzo niski (< 80), sprawdź czy 2x nie jest lepsze
                if final_bpm < 80:
                    doubled_bpm = final_bpm * 2
                    if 80 <= doubled_bpm <= 180:
                        print(f"BPM normalization: {final_bpm:.1f} -> {doubled_bpm:.1f} (2x)")
                        final_bpm = doubled_bpm
                        if confidence is not None:
                            confidence *= 0.8  # lekka penalizacja za korekcję
                
                # Jeśli BPM jest bardzo wysoki (> 160), sprawdź czy 0.5x nie jest lepsze
                elif final_bpm > 160:
                    halved_bpm = final_bpm / 2
                    if 60 <= halved_bpm <= 160:
                        print(f"BPM normalization: {final_bpm:.1f} -> {halved_bpm:.1f} (0.5x)")
                        final_bpm = halved_bpm
                        if confidence is not None:
                            confidence *= 0.8  # lekka penalizacja za korekcję
                
                # Ostateczne zaokrąglenie po normalizacji
                final_bpm = round(float(final_bpm), 1)
                
                # Sanity check - odrzuć nierealistyczne wartości
                if final_bpm < 40 or final_bpm > 200:
                    self.analysisFailed.emit(f"BPM poza realistycznym zakresem: {final_bpm:.1f}")
                    return
            
            # Apply-if-current: sprawdź czy token wciąż aktualny
            if token != self.load_token:
                log.info("BPM analysis result dropped: token %d != current %d", token, self.load_token)
                return
                
            # === ETAP 11: Cache przy pliku ===
            cache_path = file_path + '.bmp.json'
            if aubio_candidates:
                method_used = "aubio"
            else:
                method_used = "librosa"
            cache_data = {
                "bpm": final_bpm,
                "method": method_used,
                "sr": SR,
                "ts": datetime.datetime.now().isoformat()
            }
            
            try:
                with open(cache_path, 'w') as f:
                    json.dump(cache_data, f, indent=2)
            except Exception:
                pass  # cache nie krytyczny
            
            # Wywołaj callback z tokenem
            self.on_bpm_detected(final_bpm, confidence, token)
            
        except Exception as e:
            if token == self.load_token:  # Tylko jeśli wciąż aktualny
                self.analysisFailed.emit(f"Błąd analizy BPM: {str(e)}")
    
    def on_bpm_detected(self, bpm: float, confidence: Optional[float] = None, token: Optional[int] = None):
        """Callback po wykryciu BPM - tylko zapisuje wynik, nie zmienia tempa."""
        # Apply-if-current: sprawdź czy token wciąż aktualny
        if token is not None and token != self.load_token:
            log.info("BPM callback dropped: token %d != current %d", token, self.load_token)
            return
            
        # Sprawdź czy UID się zgadza
        if hasattr(self, 'current_uid') and self.current_uid:
            log.info("BPM result for UID %s: %.1f (conf: %.2f)", self.current_uid[:8], bpm or 0, confidence or 0)
        
        self.detected_bpm = bpm
        self.bmp_confidence = confidence
        self.bpm_status = "ok" if bpm and bpm > 0 else "none"
        log.info("BPM result %s -> %s", self.path.name, bpm if bpm else "—")
        print(f"Deck {self.deck_id}: Wykryto BPM: {bpm:.1f}")
        
        # Zapisz do centralnego cache jeśli mamy UID
        if hasattr(self, 'current_uid') and self.current_uid and bpm and bpm > 0:
            result = AnalysisResult(
                uid=self.current_uid,
                bmp=bpm,
                confidence=confidence,
                key_note=None,
                key_display=None,
                method="librosa_bpm"
            )
            store_bmp(result)
            
            # Zapisz też do pliku cache
            if hasattr(self, 'track_path') and self.track_path:
                save_to_file_cache(self.track_path, result)
        
        # Emituj sygnał Qt z głównego wątku
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.bpmReady.emit(bpm))
    
    def on_key_detected(self, key_data: dict, token: Optional[int] = None):
        """Callback po wykryciu klucza muzycznego."""
        # Apply-if-current: sprawdź czy token wciąż aktualny
        if token is not None and token != self.load_token:
            log.info("Key callback dropped: token %d != current %d", token, self.load_token)
            return
            
        # Sprawdź czy UID się zgadza
        if hasattr(self, 'current_uid') and self.current_uid:
            key_display = key_data.get('display', 'None') if key_data else 'None'
            log.info("Key result for UID %s: %s", self.current_uid[:8], key_display)
        
        self.key_detected = key_data
        self.key_status = "ok"
        log.info("Key result %s -> %s", self.path.name, key_data.get('display', 'None'))
        print(f"Deck {self.deck_id}: Wykryto klucz: {key_data.get('display', 'None')}")
        
        # Zapisz do centralnego cache jeśli mamy UID i key
        if hasattr(self, 'current_uid') and self.current_uid and key_data:
            # Sprawdź czy mamy już BPM w cache
            existing_result = get_bmp(self.current_uid)
            if existing_result:
                # Aktualizuj istniejący wynik
                updated_result = AnalysisResult(
                    uid=self.current_uid,
                    bmp=existing_result.bmp,
                    confidence=existing_result.confidence,
                    key_note=key_data.get('note'),
                    key_display=key_data.get('display'),
                    method=existing_result.method
                )
            else:
                # Utwórz nowy wynik tylko z kluczem
                updated_result = AnalysisResult(
                    uid=self.current_uid,
                    bmp=None,
                    confidence=None,
                    key_note=key_data.get('note'),
                    key_display=key_data.get('display'),
                    method="key_analysis"
                )
            
            store_bmp(updated_result)
            
            # Zapisz też do pliku cache
            if hasattr(self, 'track_path') and self.track_path:
                save_to_file_cache(self.track_path, updated_result)
        
        # Emituj sygnał Qt
        self.keyReady.emit(key_data)
    
    def get_key_info(self) -> dict:
        """Zwraca informacje o kluczu z uwzględnieniem pitch shift."""
        if not self.key_detected:
            return {'status': self.key_status, 'display': 'KEY: —'}
        
        # Oblicz aktualny playback rate
        playback_rate = self.rate_smooth if hasattr(self, 'rate_smooth') else 1.0
        
        # Formatuj wyświetlanie
        display = self.key_analyzer.format_key_display(
            self.key_detected, 
            playback_rate, 
            self.key_lock
        )
        
        # Obsługa różnych formatów danych (cache vs analiza)
        key_name = self.key_detected.get('key_name', 'Unknown')
        camelot = self.key_detected.get('camelot', '?')
        confidence = self.key_detected.get('confidence', 0.0)
        
        return {
            'status': self.key_status,
            'key_name': key_name,
            'camelot': camelot,
            'confidence': confidence,
            'display': display,
            'playback_rate': playback_rate,
            'key_lock': self.key_lock
        }
    
    def set_bpm_target(self, bpm: float):
        """Ustawia docelowe BPM z gałki UI."""
        self.bpm_target = bpm
        if self.detected_bpm and self.detected_bpm > 0:
            self.rate_target = float(bpm) / float(self.detected_bpm)
        else:
            # brak detekcji: traktuj gałkę jako mnożnik (bpm=100 -> 1.0)
            self.rate_target = bpm / 100.0
    
    def _smooth_rate(self):
        """Płynne wygładzanie tempa bez klików.
        
        Używa MasterClock dla deterministycznego pozycjonowania.
        """
        # Używaj MasterClock zamiast time.time()
        master_state = self.master_clock.get_state()
        current_time = master_state.monotonic_time
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
    
    def _simple_linear_resample(self, chunk: np.ndarray, ratio: float) -> np.ndarray:
        """Prosty linear resampling - SZYBKI dla real-time."""
        if chunk.size == 0 or abs(ratio - 1.0) < 0.001:
            return chunk
        
        try:
            input_len = len(chunk)
            output_len = int(input_len * ratio)
            
            if output_len <= 0:
                return np.zeros((1, 2), dtype=np.float32)
            
            # Linear interpolation indices
            indices = np.linspace(0, input_len - 1, output_len)
            
            # Dla stereo
            if chunk.ndim == 2 and chunk.shape[1] == 2:
                left = np.interp(indices, np.arange(input_len), chunk[:, 0])
                right = np.interp(indices, np.arange(input_len), chunk[:, 1])
                return np.column_stack((left, right)).astype(np.float32)
            else:
                # Mono
                resampled = np.interp(indices, np.arange(input_len), chunk.flatten())
                return resampled.astype(np.float32)
        except Exception as e:
            print(f"Błąd prostego resamplingu w deck {self.deck_id}: {e}")
            return chunk
    
    def _fill_ring_buffer(self, target_frames: int):
        """Napełnia ring buffer próbkami (worker thread) z tempo kontrolą i nudge."""
        if not self.is_playing or self.audio_data is None:
            return
        
        with self.buffer_lock:
            current_size = len(self.ring_buffer)
            needed_frames = min(target_frames - current_size, self.ring_buffer.maxlen - current_size)
            
            if needed_frames <= 0:
                return
            
            # Wygładzanie nudge (exponential smoothing)
            alpha = min(1.0, needed_frames / self.sample_rate * 1000.0 / self.smoothing_ms)
            self.nudge_ratio += alpha * (self.nudge_target - self.nudge_ratio)
            
            # Oblicz pozycję w próbkach
            start_sample = int(self.position * self.sample_rate)
            
            # Sprawdź czy nie przekroczyliśmy końca utworu
            if start_sample >= len(self.audio_data):
                self.is_playing = False
                return
            
            # Pobierz tempo z lock
            with self.tempo_lock:
                tempo_ratio = self.tempo_ratio
            
            # Oblicz efektywne tempo (tempo_ratio * nudge_ratio)
            effective_ratio = tempo_ratio * self.nudge_ratio
            
            # Oblicz ile próbek potrzebujemy z pliku (z marginesem dla tempo)
            source_frames_needed = int(needed_frames * effective_ratio * 1.1)  # 10% margines
            end_sample = min(start_sample + source_frames_needed, len(self.audio_data))
            chunk = self.audio_data[start_sample:end_sample]
            
            # Zastosuj tempo/pitch-shift
            if self.key_lock:
                # Key Lock ON: używaj time-stretch (tempo bez zmiany pitch)
                if abs(effective_ratio - 1.0) > 0.001:
                    self.time_stretch_engine.set_tempo(effective_ratio)
                    chunk = self.time_stretch_engine.process_audio(chunk)
            else:
                # Key Lock OFF: używaj resampling (tempo + pitch)
                if abs(effective_ratio - 1.0) > 0.001:
                    chunk = self._simple_linear_resample(chunk, 1.0 / effective_ratio)
            
            # Przytnij do needed_frames
            if len(chunk) > needed_frames:
                chunk = chunk[:needed_frames]
            elif len(chunk) < needed_frames:
                # Dopełnij zerami jeśli za krótki
                padding = np.zeros((needed_frames - len(chunk), 2), dtype=np.float32)
                chunk = np.vstack([chunk, padding])
            
            # Zastosuj głośność
            chunk = chunk * self.volume
            
            # Aktualizuj pozycję (z efektywnym tempo)
            time_increment = needed_frames / self.sample_rate
            self.position += time_increment * effective_ratio
            
            # Dodaj do ring bufora - BATCH append dla wydajności
            if len(chunk) > 0:
                # Konwertuj do listy i dodaj batch
                chunk_list = chunk.tolist()
                self.ring_buffer.extend(chunk_list)
    
    def _worker_thread_func(self):
        """Worker thread do napełniania ring bufora - ZOPTYMALIZOWANY.
        
        Używa MasterClock dla deterministycznego pozycjonowania.
        """
        # Używaj MasterClock zamiast time.time()
        master_state = self.master_clock.get_state()
        last_time = master_state.monotonic_time
        
        while not self.should_stop_worker:
            try:
                # Używaj MasterClock zamiast time.time()
                master_state = self.master_clock.get_state()
                current_time = master_state.monotonic_time
                dt = current_time - last_time
                last_time = current_time
                
                if not self.is_playing or self.audio_data is None:
                    # Używaj MasterClock timing zamiast time.sleep()
                    import time
                    time.sleep(0.001)  # Minimalna pauza
                    continue
                
                # Płynne wygładzanie tempa
                self._smooth_rate()
                
                # Sprawdź ile próbek jest w buforze
                with self.buffer_lock:
                    current_buffer_size = len(self.ring_buffer)
                    buffer_capacity = self.ring_buffer.maxlen
                    buffer_fill_ratio = current_buffer_size / buffer_capacity
                
                # Agresywne napełnianie gdy bufor jest mały
                if buffer_fill_ratio < 0.3:  # mniej niż 30% bufora
                    self._fill_ring_buffer(8192)  # duży chunk
                elif buffer_fill_ratio < 0.6:  # mniej niż 60% bufora
                    self._fill_ring_buffer(4096)  # średni chunk
                else:
                    self._fill_ring_buffer(2048)  # mały chunk
                
                # Synchronizuj z MasterClock zamiast time.sleep()
                master_state = self.master_clock.get_state()
                # Krótka pauza tylko jeśli potrzeba
                if buffer_fill_ratio > 0.8:
                    import time
                    time.sleep(0.001)
                
            except Exception as e:
                print(f"Deck {self.deck_id}: Błąd w worker: {e}")
                import time
                time.sleep(0.001)  # Minimalna pauza przy błędzie
    
    def get_current_audio_chunk(self, chunk_size: int) -> np.ndarray:
        """Pobiera aktualny fragment audio do odtwarzania (zachowane dla kompatybilności)."""
        return self.pop_audio_chunk(chunk_size)
    
    def _playback_loop(self):
        """Główna pętla odtwarzania (uruchamiana w osobnym wątku).
        
        Używa MasterClock zamiast time.sleep() dla deterministycznego timing.
        """
        last_master_samples = self.master_clock.get_total_audio_samples()
        
        while not self._stop_event.is_set() and self.is_playing:
            if not self.is_paused:
                # Czekaj na następny blok audio z MasterClock
                current_master_samples = self.master_clock.get_total_audio_samples()
                samples_elapsed = current_master_samples - last_master_samples
                
                if samples_elapsed >= 512:  # ~10ms przy 48kHz
                    last_master_samples = current_master_samples
                    # Pozycja jest aktualizowana przez audio callback
                else:
                    # Krótka pauza jeśli nie ma nowych próbek
                    import time
                    time.sleep(0.001)  # 1ms
            else:
                # Gdy pauzowane, sprawdzaj rzadziej
                import time
                time.sleep(0.01)
    
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
        """Synchronizuje BPM tego decka do master decka z zaawansowaną logiką."""
        # Sprawdź czy oba decki mają wykryte BPM
        if not (hasattr(master_deck, 'detected_bpm') and master_deck.detected_bpm and 
                hasattr(self, 'detected_bpm') and self.detected_bpm):
            raise RuntimeError("No BPM on target deck")
        
        # Oblicz rzeczywiste BPM odtwarzania master decka
        master_effective_tempo = master_deck.effective_ratio() if hasattr(master_deck, 'effective_ratio') else 1.0
        master_playing_bpm = master_deck.detected_bpm * master_effective_tempo
        
        # Oblicz docelowy ratio z korekcją na typowe połowa/podwójne BPM
        try:
            target_ratio, hit_limit = self._compute_sync_ratio(self.detected_bpm, master_playing_bpm)
            
            # Płynna rampa do target_ratio (80ms)
            self._apply_sync_ramp(target_ratio)
            
            print(f"Deck {self.deck_id}: SYNC do Deck {master_deck.deck_id} - target BPM: {master_playing_bpm:.1f} (ratio: {target_ratio:.3f}, limited: {hit_limit})")
            
            return target_ratio, hit_limit
            
        except ValueError as e:
             raise RuntimeError(str(e))
    
    def _choose_multiplier(self, ratio_raw: float) -> float:
        """
        Dostosuj do najbliższego z {0.5, 1.0, 2.0} * ratio_raw,
        żeby zgrać typowe przypadki 64↔128↔256 BPM.
        """
        candidates = [0.5 * ratio_raw, ratio_raw, 2.0 * ratio_raw]
        # wybierz ten najbliższy do 1.0 (najmniejsza zmiana tempa)
        return min(candidates, key=lambda r: abs(r - 1.0))
    
    def _clamp_ratio(self, r: float) -> float:
        """Ogranicza ratio do zakresu pitch."""
        lo, hi = self.PITCH_RANGES[self.pitch_range_key]
        return max(lo, min(hi, r))
    
    def _compute_sync_ratio(self, my_bmp: float, other_bpm: float) -> tuple[float, bool]:
        """Oblicza optymalny sync ratio z korekcją na typowe BPM."""
        if not my_bmp or not other_bpm:
            raise ValueError("BPM missing")
        
        raw = other_bpm / my_bmp
        adjusted = self._choose_multiplier(raw)
        clamped = self._clamp_ratio(adjusted)
        hit_limit = (clamped != adjusted)
        
        # zaokrąglenie do 0.001 (0.1%) dla stabilności UI
        clamped = round(clamped, 3)
        return clamped, hit_limit
    
    def _apply_sync_ramp(self, target_ratio: float):
        """Płynna rampa do target_ratio (80ms)."""
        steps = 8
        start = self.tempo_ratio
        
        for i in range(1, steps + 1):
            val = start + (target_ratio - start) * (i / steps)
            self.set_tempo(val)
            # Krótka pauza między krokami rampy
            time.sleep(0.01)  # 10ms per step = 80ms total
    
    def set_pitch_range(self, range_key: str):
        """Ustawia zakres pitch dla SYNC (±8%, ±16%, ±50%)."""
        if range_key in self.PITCH_RANGES:
            self.pitch_range_key = range_key
    
    def set_spectrum_callback(self, callback: Optional[Callable]):
        """Ustawia callback dla spectrum analyzer."""
        self.spectrum_callback = callback
    
    def get_phase(self) -> float:
        """Zwraca aktualną fazę odtwarzania [0,1]."""
        if self.duration > 0:
            return max(0.0, min(1.0, self.position / self.duration))
        return 0.0
    
    def seek_to_phase(self, phase: float):
        """Ustawia pozycję odtwarzania na podstawie fazy [0,1]."""
        phase = max(0.0, min(1.0, phase))
        if self.duration > 0:
            new_position = phase * self.duration
            self.set_position(new_position)
    
    def seek_to(self, time_sec: float):
        """Hard seek w silniku + soft fade żeby nie strzelało."""
        if self.audio_data is None:
            return self.position
            
        # Ogranicz do dostępnego zakresu
        tgt = max(0.0, min(time_sec, self.duration - 0.001))
        
        # Proste fade-out przez wyczyszczenie ring buffera
        with self.buffer_lock:
            self.ring_buffer.clear()
        
        # Ustaw nową pozycję
        self.position = tgt
        
        # Zaktualizuj AudioClock
        start_samples = int(tgt * self.sample_rate)
        self.clock.play_from_samples(start_samples)
        
        # Reset internal state jeśli potrzeba (dla przyszłych rozszerzeń)
        # W obecnej implementacji position jest głównym wskaźnikiem
        
        return self.position
    
    def get_effective_bpm(self) -> float:
        """Zwraca efektywne BPM z uwzględnieniem tempo_map i rate_smooth."""
        if self.tempo_map and self.tempo_map.is_variable_tempo():
            # Dla zmiennego tempa użyj aktualnej pozycji
            current_bpm = self.tempo_map.get_bpm_at_sample(self.sample_position)
            return current_bpm * self.rate_smooth
        return self.detected_bpm * self.rate_smooth
    
    def samples_to_beats(self, sample_position: int) -> float:
        """Konwertuje pozycję w próbkach na pozycję w beatach.
        
        Args:
            sample_position: Pozycja w próbkach
            
        Returns:
            Pozycja w beatach z uwzględnieniem rate_smooth
        """
        if self.tempo_map:
            # Użyj TempoMap dla precyzyjnej konwersji
            beats = self.tempo_map.samples_to_beats(sample_position)
            # rate_smooth wpływa na tempo odtwarzania
            return beats * self.rate_smooth
        else:
            # Fallback dla prostego BPM
            if self.detected_bpm <= 0:
                return 0.0
            time_sec = sample_position / self.sample_rate
            beats_per_sec = self.detected_bpm / 60.0
            return time_sec * beats_per_sec * self.rate_smooth
    
    def beats_to_samples(self, beat_position: float) -> int:
        """Konwertuje pozycję w beatach na pozycję w próbkach.
        
        Args:
            beat_position: Pozycja w beatach
            
        Returns:
            Pozycja w próbkach z uwzględnieniem rate_smooth
        """
        if self.tempo_map:
            # Uwzględnij rate_smooth
            adjusted_beat_position = beat_position / self.rate_smooth
            return self.tempo_map.beats_to_samples(adjusted_beat_position)
        else:
            # Fallback dla prostego BPM
            if self.detected_bpm <= 0:
                return 0
            adjusted_beat_position = beat_position / self.rate_smooth
            beats_per_sec = self.detected_bpm / 60.0
            time_sec = adjusted_beat_position / beats_per_sec
            return int(time_sec * self.sample_rate)
    
    def set_grid_offset(self, offset_beats: float) -> None:
        """Ustawia ręczną korektę offsetu siatki w beatach.
        
        Args:
            offset_beats: Offset w beatach (może być ujemny)
        """
        if self.tempo_map:
            self.tempo_map.set_grid_offset(offset_beats)
            # Zapisz do metadanych
            if self.current_uid:
                from .tempo_map import TempoMapManager
                TempoMapManager.save_grid_offset(self.current_uid, offset_beats)
            print(f"🎵 Deck {self.deck_id}: Grid offset set to {offset_beats:.3f} beats")
        else:
            print(f"⚠️ Deck {self.deck_id}: No tempo map available for grid offset")
    
    def get_grid_offset(self) -> float:
        """Zwraca aktualny offset siatki w beatach."""
        if self.tempo_map:
            return self.tempo_map.get_grid_offset()
        return 0.0
    
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
            'rate_smooth': getattr(self, 'rate_smooth', 1.0),
            'tempo_ratio': getattr(self, 'tempo_ratio', 1.0),
            'nudge_ratio': getattr(self, 'nudge_ratio', 1.0),
            'nudge_target': getattr(self, 'nudge_target', 1.0),
            'nudge_percent': self.get_nudge_percent(),
            'effective_ratio': self.effective_ratio(),
            'key_lock': getattr(self, 'key_lock', False),
            'key_detected': getattr(self, 'key_detected', None),
            'key_status': getattr(self, 'key_status', 'unknown')
        }