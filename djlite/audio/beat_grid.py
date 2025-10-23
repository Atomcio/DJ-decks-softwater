"""Centralny beat-grid system dla synchronizacji między deckami.

Zintegrowany z TempoMap jako źródłem prawdy dla beatgrid.
"""

import time
from dataclasses import dataclass
from typing import Optional
from .tempo_map import TempoMap, tempo_map_manager
from .master_clock import get_master_clock


@dataclass
class BeatGrid:
    """Definicja siatki beatów dla utworu - wrapper dla TempoMap."""
    bpm: float              # średnie BPM (dla kompatybilności)
    beat_offset: float      # czas (s) pierwszego beatu względem audio
    beats_per_bar: int = 4
    tempo_map: Optional[TempoMap] = None  # źródło prawdy
    
    def __post_init__(self):
        """Inicjalizacja - tworzy TempoMap jeśli nie istnieje."""
        if self.tempo_map is None:
            # Tworzy prostą tempo map dla stałego BPM
            self.tempo_map = TempoMap.from_constant_bpm(
                bpm=self.bpm,
                sample_rate=48000,  # domyślny sample rate
                beat_offset_seconds=self.beat_offset
            )

    @property
    def sec_per_beat(self) -> float:
        """Zwraca czas jednego beatu w sekundach (średni dla zmiennego tempa)."""
        if self.tempo_map and self.tempo_map.is_variable_tempo():
            return 60.0 / self.tempo_map.get_average_bpm()
        return 60.0 / self.bpm

    def time_of_beat(self, beat_idx: int) -> float:
        """Zwraca czas (w sekundach) dla podanego indeksu beatu."""
        if self.tempo_map:
            sample_pos = self.tempo_map.beats_to_samples(float(beat_idx))
            return sample_pos / self.tempo_map.sample_rate
        return self.beat_offset + beat_idx * self.sec_per_beat

    def beat_at_time(self, time_sec: float) -> float:
        """Zwraca indeks beatu (może być frakcją) dla podanego czasu."""
        if self.tempo_map:
            sample_pos = int(time_sec * self.tempo_map.sample_rate)
            return self.tempo_map.samples_to_beats(sample_pos)
        return (time_sec - self.beat_offset) / self.sec_per_beat

    def bar_at_time(self, time_sec: float) -> float:
        """Zwraca numer taktu (może być frakcją) dla podanego czasu."""
        return self.beat_at_time(time_sec) / self.beats_per_bar
    
    def get_bpm_at_time(self, time_sec: float) -> float:
        """Zwraca BPM w danym czasie (obsługuje zmienne tempo)."""
        if self.tempo_map:
            sample_pos = int(time_sec * self.tempo_map.sample_rate)
            return self.tempo_map.get_bpm_at_sample(sample_pos)
        return self.bpm
    
    def set_grid_offset(self, offset_beats: float) -> None:
        """Ustawia ręczną korektę offsetu siatki."""
        if self.tempo_map:
            self.tempo_map.set_grid_offset(offset_beats)
    
    def get_grid_offset(self) -> float:
        """Zwraca aktualny offset siatki w beatach."""
        if self.tempo_map:
            return self.tempo_map.grid_offset_beats
        return 0.0
    
    def is_variable_tempo(self) -> bool:
        """Sprawdza czy utwór ma zmienne tempo."""
        if self.tempo_map:
            return self.tempo_map.is_variable_tempo()
        return False


class BeatClock:
    """
    MASTER clock – bazuje na real-time i aktualnym 'tempo_ratio' MASTER-a.
    Źródło czasu i BPM dla całego systemu beat-grid.
    Zintegrowany z TempoMap dla precyzyjnej synchronizacji.
    """
    
    def __init__(self, grid: Optional[BeatGrid] = None, sample_rate: int = 48000):
        self.grid = grid or BeatGrid(bpm=120.0, beat_offset=0.0)
        self._start_rt = 0.0            # real-time start (z MasterClock)
        self._start_pos = 0.0           # s (pozycja odtwarzania na starcie)
        self._tempo_ratio = 1.0
        self._playing = False
        self._sample_rate = sample_rate
        
        # MasterClock jako źródło prawdy dla czasu
        self.master_clock = get_master_clock(sample_rate)

    def play(self, start_pos_sec: float):
        """Rozpoczyna odtwarzanie od podanej pozycji."""
        # Używaj MasterClock zamiast time.perf_counter()
        master_state = self.master_clock.get_state()
        self._start_rt = master_state.monotonic_time
        self._start_pos = start_pos_sec
        self._playing = True

    def pause(self, pos_now_sec: float):
        """Pauzuje odtwarzanie na podanej pozycji."""
        self._start_pos = pos_now_sec
        self._playing = False

    def stop(self):
        """Zatrzymuje odtwarzanie i resetuje pozycję."""
        self._start_pos = 0.0
        self._playing = False

    def set_tempo_ratio(self, ratio: float):
        """Ustawia współczynnik tempa (1.0 = normalne tempo)."""
        if self._playing:
            # Zachowaj aktualną pozycję przy zmianie tempa
            current_pos = self.now_sec()
            # Używaj MasterClock zamiast time.perf_counter()
            master_state = self.master_clock.get_state()
            self._start_rt = master_state.monotonic_time
            self._start_pos = current_pos
        self._tempo_ratio = ratio

    def set_grid(self, grid: BeatGrid):
        """Ustawia nową siatkę beatów."""
        self.grid = grid

    def update_bpm(self, new_bpm: float):
        """Aktualizuje BPM w aktualnej siatce."""
        self.grid.bpm = new_bpm
    
    def update_bpm_with_phase_preservation(self, new_bpm: float):
        """Aktualizuje BPM zachowując fazę - beat_offset zostaje przeliczony
        tak, aby aktualny czas (t_now) nadal wypadał w tym samym beat_f."""
        if not self._playing or self.grid.bpm <= 0:
            # Jeśli nie gramy lub brak BPM, po prostu ustaw nowy BPM
            self.grid.bpm = new_bpm
            return
            
        t_now = self.now_sec()
        old_beat_f = self.grid.beat_at_time(t_now)  # Aktualna pozycja w beatach
        
        # Ustaw nowy BPM
        self.grid.bpm = new_bpm
        
        # Przelicz beat_offset tak, aby t_now nadal wypadał w old_beat_f
        # old_beat_f = (t_now - new_beat_offset) / new_sec_per_beat
        # new_beat_offset = t_now - old_beat_f * new_sec_per_beat
        new_sec_per_beat = 60.0 / new_bpm
        new_beat_offset = t_now - old_beat_f * new_sec_per_beat
        self.grid.beat_offset = new_beat_offset
        
        print(f"🎵 BPM updated with phase preservation: {new_bpm:.1f} BPM, beat_offset={new_beat_offset:.3f}s")

    def now_sec(self) -> float:
        """Zwraca aktualną pozycję w sekundach.
        
        Używa MasterClock dla deterministycznego pozycjonowania.
        """
        if not self._playing:
            return self._start_pos
        # Używaj MasterClock zamiast time.perf_counter()
        master_state = self.master_clock.get_state()
        dt = master_state.monotonic_time - self._start_rt
        return self._start_pos + dt * self._tempo_ratio

    def current_beat_index(self) -> float:
        """Zwraca aktualny indeks beatu (może być frakcją)."""
        t = self.now_sec() - self.grid.beat_offset
        return t / self.grid.sec_per_beat

    def current_bar_index(self) -> float:
        """Zwraca aktualny indeks taktu (może być frakcją)."""
        return self.current_beat_index() / self.grid.beats_per_bar

    def is_playing(self) -> bool:
        """Sprawdza czy clock jest w trybie odtwarzania."""
        return self._playing

    def get_tempo_ratio(self) -> float:
        """Zwraca aktualny współczynnik tempa."""
        return self._tempo_ratio

    def get_effective_bpm(self) -> float:
        """Zwraca efektywne BPM (bazowe BPM * tempo_ratio)."""
        if self.grid.tempo_map and self.grid.tempo_map.is_variable_tempo():
            # Dla zmiennego tempa zwróć BPM w aktualnej pozycji
            current_time = self.now_sec()
            current_bpm = self.grid.get_bpm_at_time(current_time)
            return current_bpm * self._tempo_ratio
        return self.grid.bpm * self._tempo_ratio
    
    def samples_to_beats(self, sample_position: int) -> float:
        """Konwertuje pozycję w próbkach na pozycję w beatach.
        
        Args:
            sample_position: Pozycja w próbkach
            
        Returns:
            Pozycja w beatach z uwzględnieniem tempo_ratio
        """
        if self.grid.tempo_map:
            # Użyj TempoMap dla precyzyjnej konwersji
            beats = self.grid.tempo_map.samples_to_beats(sample_position)
            # Uwzględnij tempo_ratio (wpływa na pozycję w czasie)
            return beats * self._tempo_ratio
        else:
            # Fallback dla prostego BPM
            time_sec = sample_position / self._sample_rate
            return self.grid.beat_at_time(time_sec) * self._tempo_ratio
    
    def beats_to_samples(self, beat_position: float) -> int:
        """Konwertuje pozycję w beatach na pozycję w próbkach.
        
        Args:
            beat_position: Pozycja w beatach
            
        Returns:
            Pozycja w próbkach z uwzględnieniem tempo_ratio
        """
        if self.grid.tempo_map:
            # Uwzględnij tempo_ratio
            adjusted_beat_position = beat_position / self._tempo_ratio
            return self.grid.tempo_map.beats_to_samples(adjusted_beat_position)
        else:
            # Fallback dla prostego BPM
            adjusted_beat_position = beat_position / self._tempo_ratio
            time_sec = self.grid.time_of_beat(int(adjusted_beat_position))
            return int(time_sec * self._sample_rate)
    
    def set_grid_offset(self, offset_beats: float) -> None:
        """Ustawia ręczną korektę offsetu siatki w beatach.
        
        Args:
            offset_beats: Offset w beatach (może być ujemny)
        """
        self.grid.set_grid_offset(offset_beats)
        print(f"🎵 BeatClock: Grid offset set to {offset_beats:.3f} beats")
    
    def get_grid_offset(self) -> float:
        """Zwraca aktualny offset siatki w beatach."""
        return self.grid.get_grid_offset()
    
    def set_sample_rate(self, sample_rate: int) -> None:
        """Ustawia sample rate dla konwersji."""
        self._sample_rate = sample_rate
        if self.grid.tempo_map:
            self.grid.tempo_map.sample_rate = sample_rate
    
    def update_tempo_map(self, tempo_map: TempoMap) -> None:
        """Aktualizuje tempo map w grid."""
        if self.grid.tempo_map is None:
            # Tworzy nowy BeatGrid z TempoMap
            avg_bpm = tempo_map.get_average_bpm()
            self.grid = BeatGrid(
                bpm=avg_bpm,
                beat_offset=0.0,
                beats_per_bar=tempo_map.beats_per_bar,
                tempo_map=tempo_map
            )
        else:
            # Aktualizuje istniejący
            self.grid.tempo_map = tempo_map
            self.grid.bpm = tempo_map.get_average_bpm()
        
        print(f"🎵 BeatClock: TempoMap updated, variable tempo: {tempo_map.is_variable_tempo()}")