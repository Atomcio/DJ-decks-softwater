"""Centralny system cache dla wyników analizy BPM i klucza."""

import os
import json
import time
import hashlib
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict
import logging

log = logging.getLogger(__name__)

@dataclass
class AnalysisResult:
    """Wynik analizy BPM/klucza z TempoMap."""
    uid: str
    bmp: Optional[float]
    confidence: Optional[float]
    key_note: Optional[str]
    key_display: Optional[str]
    method: str = "unknown"
    timestamp: float = 0.0
    tempo_map: Optional['TempoMap'] = None  # Nowy format tempo map
    grid_offset: float = 0.0  # Ręczna korekta offsetu w beatach
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

# Globalny cache w pamięci - współdzielony między wszystkimi komponentami
BMP_CACHE: Dict[str, AnalysisResult] = {}

def generate_track_uid(file_path: str) -> str:
    """Generuje unique ID dla utworu na podstawie ścieżki, rozmiaru i mtime."""
    try:
        stat = os.stat(file_path)
        # Kombinacja: absolutna ścieżka + rozmiar + mtime
        uid_string = f"{os.path.abspath(file_path)}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.md5(uid_string.encode()).hexdigest()[:16]
    except Exception:
        # Fallback: tylko ścieżka
        return hashlib.md5(os.path.abspath(file_path).encode()).hexdigest()[:16]

def get_bmp(uid: str) -> Optional[AnalysisResult]:
    """Pobiera wynik analizy z cache."""
    return BMP_CACHE.get(uid)

def get_bmp_by_path(file_path: str) -> Optional[AnalysisResult]:
    """Pobiera wynik analizy z cache na podstawie ścieżki pliku."""
    uid = generate_track_uid(file_path)
    return get_bmp(uid)

def store_bmp(result: AnalysisResult) -> None:
    """Zapisuje wynik analizy do cache."""
    if result.bmp and result.bmp > 0:
        BMP_CACHE[result.uid] = result
        log.info("Cache stored: UID %s -> BPM %.1f, Key %s", 
                result.uid[:8], result.bmp or 0, result.key_display or "—")

def store_bmp_by_path(file_path: str, bmp: Optional[float], confidence: Optional[float] = None, 
                     key_note: Optional[str] = None, key_display: Optional[str] = None, 
                     method: str = "unknown") -> str:
    """Zapisuje wynik analizy do cache na podstawie ścieżki pliku.
    
    Returns:
        UID utworu
    """
    uid = generate_track_uid(file_path)
    result = AnalysisResult(
        uid=uid,
        bmp=bmp,
        confidence=confidence,
        key_note=key_note,
        key_display=key_display,
        method=method
    )
    store_bmp(result)
    return uid

def load_from_file_cache(file_path: str) -> Optional[AnalysisResult]:
    """Ładuje wynik z cache pliku (.bmp.json lub .analysis.json)."""
    uid = generate_track_uid(file_path)
    
    # Sprawdź różne formaty cache
    cache_files = [
        file_path + '.bmp.json',
        file_path + '.bpm.json', 
        file_path + '.analysis.json'
    ]
    
    for cache_path in cache_files:
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    
                bmp = cache_data.get('bpm') or cache_data.get('bmp')
                confidence = cache_data.get('confidence')
                key_note = cache_data.get('key')
                key_display = cache_data.get('key_display') or key_note
                method = cache_data.get('method', 'file_cache')
                timestamp = cache_data.get('timestamp', 0)
                
                if bmp and bmp > 0:
                    result = AnalysisResult(
                        uid=uid,
                        bmp=float(bmp),
                        confidence=float(confidence) if confidence else None,
                        key_note=key_note,
                        key_display=key_display,
                        method=method,
                        timestamp=float(timestamp)
                    )
                    # Dodaj do cache w pamięci
                    store_bmp(result)
                    log.info("Loaded from file cache: %s -> BPM %.1f", Path(file_path).name, bmp)
                    return result
                    
            except Exception as e:
                log.warning("Cache read error for %s: %s", cache_path, e)
                continue
    
    return None

def save_to_file_cache(file_path: str, result: AnalysisResult) -> None:
    """Zapisuje wynik do cache pliku (.bmp.json)."""
    cache_path = file_path + '.bmp.json'
    try:
        cache_data = asdict(result)
        # Zmień 'bmp' na 'bpm' dla kompatybilności
        if 'bmp' in cache_data:
            cache_data['bpm'] = cache_data.pop('bmp')
            
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)
        log.debug("Saved to file cache: %s", cache_path)
    except Exception as e:
        log.warning("Cache save error for %s: %s", cache_path, e)

def clear_cache() -> None:
    """Czyści cache w pamięci."""
    global BMP_CACHE
    BMP_CACHE.clear()
    log.info("Cache cleared")

def get_cache_stats() -> Dict[str, Any]:
    """Zwraca statystyki cache."""
    total = len(BMP_CACHE)
    with_bmp = len([r for r in BMP_CACHE.values() if r.bmp and r.bmp > 0])
    with_key = len([r for r in BMP_CACHE.values() if r.key_display])
    
    return {
        'total_entries': total,
        'with_bmp': with_bmp,
        'with_key': with_key,
        'cache_size_mb': len(str(BMP_CACHE)) / 1024 / 1024
    }

def store_tempo_map(uid: str, tempo_map: 'TempoMap') -> None:
    """Zapisuje TempoMap do cache.
    
    Args:
        uid: Unikalny identyfikator utworu
        tempo_map: TempoMap do zapisania
    """
    if uid in BMP_CACHE:
        # Aktualizuj istniejący wpis
        BMP_CACHE[uid].tempo_map = tempo_map
        BMP_CACHE[uid].bmp = tempo_map.get_average_bpm()
    else:
        # Utwórz nowy wpis
        result = AnalysisResult(
            uid=uid,
            bmp=tempo_map.get_average_bpm(),
            confidence=1.0,
            key_note=None,
            key_display=None,
            method="tempo_map",
            timestamp=time.time(),
            tempo_map=tempo_map
        )
        BMP_CACHE[uid] = result
    
    log.info("TempoMap cached for %s", uid[:8])

def get_tempo_map(uid: str) -> Optional['TempoMap']:
    """Pobiera TempoMap z cache.
    
    Args:
        uid: Unikalny identyfikator utworu
        
    Returns:
        TempoMap lub None jeśli nie znaleziono
    """
    if uid in BMP_CACHE:
        return BMP_CACHE[uid].tempo_map
    return None

def store_grid_offset(uid: str, offset_beats: float) -> None:
    """Zapisuje grid offset do cache.
    
    Args:
        uid: Unikalny identyfikator utworu
        offset_beats: Offset w beatach
    """
    if uid in BMP_CACHE:
        BMP_CACHE[uid].grid_offset = offset_beats
        log.info("Grid offset %.3f beats cached for %s", offset_beats, uid[:8])
    else:
        log.warning("Cannot store grid offset: no cache entry for %s", uid[:8])

def get_grid_offset(uid: str) -> float:
    """Pobiera grid offset z cache.
    
    Args:
        uid: Unikalny identyfikator utworu
        
    Returns:
        Grid offset w beatach (domyślnie 0.0)
    """
    if uid in BMP_CACHE:
        return BMP_CACHE[uid].grid_offset
    return 0.0