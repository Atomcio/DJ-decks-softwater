"""Tempo Map - spójne źródło prawdy dla beatgrid i synchronizacji.

Implementuje format tempo map: lista (czas_w_sample, beatIndex, localBPM)
Obsługuje zarówno stałe jak i zmienne tempo w utworach.
"""

import json
import logging
import threading
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict, Any
from pathlib import Path
import numpy as np

log = logging.getLogger(__name__)

@dataclass
class TempoSegment:
    """Pojedynczy segment tempo map."""
    sample_position: int    # pozycja w próbkach
    beat_index: float      # indeks beatu (może być frakcją)
    local_bpm: float       # BPM w tym segmencie
    confidence: float = 1.0  # pewność analizy (0.0-1.0)
    
    def __post_init__(self):
        """Walidacja danych segmentu."""
        if self.sample_position < 0:
            raise ValueError("sample_position must be >= 0")
        if self.beat_index < 0:
            raise ValueError("beat_index must be >= 0")
        if self.local_bpm <= 0:
            raise ValueError("local_bpm must be > 0")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

@dataclass
class TempoMap:
    """Mapa tempa dla utworu - źródło prawdy dla beatgrid."""
    segments: List[TempoSegment]
    sample_rate: int
    grid_offset_beats: float = 0.0  # ręczna korekta offsetu w beatach
    beats_per_bar: int = 4
    uid: Optional[str] = None  # UID utworu dla cache
    
    def __post_init__(self):
        """Walidacja i sortowanie segmentów."""
        if not self.segments:
            raise ValueError("TempoMap must have at least one segment")
        
        # Sortuj segmenty według sample_position
        self.segments.sort(key=lambda s: s.sample_position)
        
        # Sprawdź czy pierwszy segment zaczyna się od 0
        if self.segments[0].sample_position != 0:
            # Dodaj segment początkowy z pierwszym BPM
            first_bpm = self.segments[0].local_bpm
            self.segments.insert(0, TempoSegment(
                sample_position=0,
                beat_index=0.0,
                local_bpm=first_bpm,
                confidence=self.segments[0].confidence
            ))
    
    @classmethod
    def from_constant_bpm(cls, bpm: float, sample_rate: int, 
                         beat_offset_seconds: float = 0.0, 
                         uid: Optional[str] = None) -> 'TempoMap':
        """Tworzy tempo map dla stałego BPM."""
        beat_offset_samples = int(beat_offset_seconds * sample_rate)
        
        segment = TempoSegment(
            sample_position=beat_offset_samples,
            beat_index=0.0,
            local_bpm=bpm,
            confidence=1.0
        )
        
        return cls(
            segments=[segment],
            sample_rate=sample_rate,
            uid=uid
        )
    
    @classmethod
    def from_variable_bpm(cls, bpm_changes: List[Tuple[float, float]], 
                         sample_rate: int, uid: Optional[str] = None) -> 'TempoMap':
        """Tworzy tempo map dla zmiennego BPM.
        
        Args:
            bpm_changes: Lista (time_seconds, bpm) dla zmian tempa
            sample_rate: Częstotliwość próbkowania
            uid: UID utworu
        """
        if not bpm_changes:
            raise ValueError("bpm_changes cannot be empty")
        
        segments = []
        current_beat_index = 0.0
        
        for i, (time_sec, bpm) in enumerate(bmp_changes):
            sample_pos = int(time_sec * sample_rate)
            
            # Oblicz beat_index na podstawie poprzednich segmentów
            if i > 0:
                prev_time, prev_bpm = bpm_changes[i-1]
                time_diff = time_sec - prev_time
                beats_elapsed = (time_diff * prev_bpm) / 60.0
                current_beat_index += beats_elapsed
            
            segments.append(TempoSegment(
                sample_position=sample_pos,
                beat_index=current_beat_index,
                local_bpm=bpm,
                confidence=0.8  # domyślna confidence dla zmiennego tempa
            ))
        
        return cls(
            segments=segments,
            sample_rate=sample_rate,
            uid=uid
        )
    
    def samples_to_beats(self, sample_position: int) -> float:
        """Konwertuje pozycję w próbkach na pozycję w beatach.
        
        Args:
            sample_position: Pozycja w próbkach
            
        Returns:
            Pozycja w beatach (z uwzględnieniem grid_offset_beats)
        """
        if sample_position < 0:
            return 0.0
        
        # Znajdź odpowiedni segment
        segment = self._find_segment_for_sample(sample_position)
        if not segment:
            return 0.0
        
        # Oblicz beats od początku segmentu
        samples_from_segment_start = sample_position - segment.sample_position
        seconds_from_segment_start = samples_from_segment_start / self.sample_rate
        beats_from_segment_start = (seconds_from_segment_start * segment.local_bpm) / 60.0
        
        # Dodaj beat_index segmentu i grid_offset
        total_beats = segment.beat_index + beats_from_segment_start + self.grid_offset_beats
        
        return max(0.0, total_beats)
    
    def beats_to_samples(self, beat_position: float) -> int:
        """Konwertuje pozycję w beatach na pozycję w próbkach.
        
        Args:
            beat_position: Pozycja w beatach
            
        Returns:
            Pozycja w próbkach
        """
        if beat_position <= 0:
            return 0
        
        # Uwzględnij grid_offset
        adjusted_beat_position = beat_position - self.grid_offset_beats
        if adjusted_beat_position <= 0:
            return 0
        
        # Znajdź segment dla tej pozycji w beatach
        segment = self._find_segment_for_beat(adjusted_beat_position)
        if not segment:
            return 0
        
        # Oblicz samples od początku segmentu
        beats_from_segment_start = adjusted_beat_position - segment.beat_index
        seconds_from_segment_start = (beats_from_segment_start * 60.0) / segment.local_bpm
        samples_from_segment_start = int(seconds_from_segment_start * self.sample_rate)
        
        return segment.sample_position + samples_from_segment_start
    
    def get_bpm_at_sample(self, sample_position: int) -> float:
        """Zwraca BPM w danej pozycji próbek."""
        segment = self._find_segment_for_sample(sample_position)
        return segment.local_bpm if segment else 120.0
    
    def get_bpm_at_beat(self, beat_position: float) -> float:
        """Zwraca BPM w danej pozycji beatów."""
        adjusted_beat_position = beat_position - self.grid_offset_beats
        segment = self._find_segment_for_beat(adjusted_beat_position)
        return segment.local_bpm if segment else 120.0
    
    def set_grid_offset(self, offset_beats: float) -> None:
        """Ustawia ręczną korektę offsetu siatki w beatach.
        
        Args:
            offset_beats: Offset w beatach (może być ujemny)
        """
        self.grid_offset_beats = offset_beats
        log.info(f"Grid offset set to {offset_beats:.3f} beats")
    
    def get_average_bpm(self) -> float:
        """Zwraca średnie BPM dla całego utworu."""
        if len(self.segments) == 1:
            return self.segments[0].local_bpm
        
        # Oblicz średnią ważoną czasem trwania segmentów
        total_weighted_bpm = 0.0
        total_duration = 0.0
        
        for i, segment in enumerate(self.segments[:-1]):
            next_segment = self.segments[i + 1]
            duration_samples = next_segment.sample_position - segment.sample_position
            duration_seconds = duration_samples / self.sample_rate
            
            total_weighted_bpm += segment.local_bpm * duration_seconds
            total_duration += duration_seconds
        
        # Dodaj ostatni segment (zakładamy że trwa do końca)
        if total_duration > 0:
            return total_weighted_bpm / total_duration
        else:
            return self.segments[0].local_bpm
    
    def is_variable_tempo(self) -> bool:
        """Sprawdza czy utwór ma zmienne tempo."""
        if len(self.segments) <= 1:
            return False
        
        first_bpm = self.segments[0].local_bpm
        tolerance = 0.1  # 0.1 BPM tolerancja
        
        return any(abs(seg.local_bpm - first_bpm) > tolerance for seg in self.segments[1:])
    
    def _find_segment_for_sample(self, sample_position: int) -> Optional[TempoSegment]:
        """Znajduje segment dla danej pozycji w próbkach."""
        if not self.segments:
            return None
        
        # Znajdź ostatni segment przed lub na pozycji
        for i in range(len(self.segments) - 1, -1, -1):
            if self.segments[i].sample_position <= sample_position:
                return self.segments[i]
        
        return self.segments[0]
    
    def _find_segment_for_beat(self, beat_position: float) -> Optional[TempoSegment]:
        """Znajduje segment dla danej pozycji w beatach."""
        if not self.segments:
            return None
        
        # Znajdź ostatni segment przed lub na pozycji
        for i in range(len(self.segments) - 1, -1, -1):
            if self.segments[i].beat_index <= beat_position:
                return self.segments[i]
        
        return self.segments[0]
    
    def to_dict(self) -> Dict[str, Any]:
        """Konwertuje tempo map do słownika dla serializacji."""
        return {
            'segments': [asdict(seg) for seg in self.segments],
            'sample_rate': self.sample_rate,
            'grid_offset_beats': self.grid_offset_beats,
            'beats_per_bar': self.beats_per_bar,
            'uid': self.uid,
            'version': '1.0'
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TempoMap':
        """Tworzy tempo map ze słownika."""
        segments = [TempoSegment(**seg_data) for seg_data in data['segments']]
        
        return cls(
            segments=segments,
            sample_rate=data['sample_rate'],
            grid_offset_beats=data.get('grid_offset_beats', 0.0),
            beats_per_bar=data.get('beats_per_bar', 4),
            uid=data.get('uid')
        )
    
    def save_to_file(self, file_path: str) -> None:
        """Zapisuje tempo map do pliku JSON."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2)
            log.info(f"TempoMap saved to {file_path}")
        except Exception as e:
            log.error(f"Failed to save TempoMap to {file_path}: {e}")
            raise
    
    @classmethod
    def load_from_file(cls, file_path: str) -> Optional['TempoMap']:
        """Ładuje tempo map z pliku JSON."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            tempo_map = cls.from_dict(data)
            log.info(f"TempoMap loaded from {file_path}")
            return tempo_map
            
        except Exception as e:
            log.warning(f"Failed to load TempoMap from {file_path}: {e}")
            return None


class TempoMapManager:
    """Manager dla tempo map z cache i thread safety."""
    
    def __init__(self):
        self._cache: Dict[str, TempoMap] = {}
        self._lock = threading.RLock()
    
    def get_tempo_map(self, uid: str) -> Optional[TempoMap]:
        """Pobiera tempo map z cache."""
        with self._lock:
            return self._cache.get(uid)
    
    def store_tempo_map(self, tempo_map: TempoMap) -> None:
        """Zapisuje tempo map do cache."""
        if not tempo_map.uid:
            log.warning("TempoMap without UID cannot be cached")
            return
        
        with self._lock:
            self._cache[tempo_map.uid] = tempo_map
            log.info(f"TempoMap cached for UID {tempo_map.uid[:8]}")
    
    def create_from_bpm_analysis(self, uid: str, bpm: float, sample_rate: int, 
                               beat_offset_seconds: float = 0.0) -> TempoMap:
        """Tworzy tempo map z wyniku analizy BPM."""
        tempo_map = TempoMap.from_constant_bpm(
            bpm=bpm,
            sample_rate=sample_rate,
            beat_offset_seconds=beat_offset_seconds,
            uid=uid
        )
        
        self.store_tempo_map(tempo_map)
        return tempo_map
    
    def load_or_create_from_file(self, file_path: str, uid: str, 
                                sample_rate: int) -> Optional[TempoMap]:
        """Ładuje tempo map z pliku cache lub tworzy nową."""
        # Sprawdź cache w pamięci
        cached = self.get_tempo_map(uid)
        if cached:
            return cached
        
        # Sprawdź plik cache
        cache_path = file_path + '.tempo_map.json'
        if Path(cache_path).exists():
            tempo_map = TempoMap.load_from_file(cache_path)
            if tempo_map:
                tempo_map.uid = uid  # Upewnij się że UID jest ustawione
                self.store_tempo_map(tempo_map)
                return tempo_map
        
        return None
    
    def save_to_file_cache(self, tempo_map: TempoMap, audio_file_path: str) -> None:
        """Zapisuje tempo map do pliku cache."""
        if not tempo_map.uid:
            log.warning("Cannot save TempoMap without UID")
            return
        
        cache_path = audio_file_path + '.tempo_map.json'
        tempo_map.save_to_file(cache_path)
    
    def clear_cache(self) -> None:
        """Czyści cache tempo map."""
        with self._lock:
            self._cache.clear()
            log.info("TempoMap cache cleared")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Zwraca statystyki cache."""
        with self._lock:
            total = len(self._cache)
            variable_tempo = sum(1 for tm in self._cache.values() if tm.is_variable_tempo())
            
            return {
                'total_tempo_maps': total,
                'variable_tempo_tracks': variable_tempo,
                'constant_tempo_tracks': total - variable_tempo
            }

# Globalny manager tempo map
tempo_map_manager = TempoMapManager()