"""Asynchroniczny analizator BPM z daemon thread i callback system."""

import threading
import logging
from pathlib import Path
from typing import Optional, Callable

log = logging.getLogger(__name__)

# Import Deck class without circular dependency
try:
    from .deck import Deck
except ImportError:
    # Fallback if there are import issues
    Deck = None

class BpmAnalyzer:
    """Asynchroniczny analizator BPM zgodny z UI kontraktem."""
    
    def __init__(self):
        self._current_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
    
    def start(self, path: Path, callback: Callable[[Optional[float]], None]):
        """Rozpoczyna analizę BPM w daemon thread.
        
        Args:
            path: Ścieżka do pliku audio
            callback: Funkcja callback(bpm_or_none) wywoływana po zakończeniu
        """
        # Zatrzymaj poprzednią analizę jeśli trwa
        self.stop()
        
        log.info("BPM start %s", path.name)
        
        # Uruchom nowy wątek daemon
        self._stop_event.clear()
        self._current_thread = threading.Thread(
            target=self._analyze_worker,
            args=(path, callback),
            daemon=True
        )
        self._current_thread.start()
    
    def stop(self):
        """Zatrzymuje bieżącą analizę."""
        if self._current_thread and self._current_thread.is_alive():
            self._stop_event.set()
            self._current_thread.join(timeout=1.0)
    
    def _analyze_worker(self, path: Path, callback: Callable[[Optional[float]], None]):
        """Worker thread dla analizy BPM."""
        try:
            # Sprawdź czy Deck jest dostępny
            if Deck is None:
                log.error("Deck class not available")
                callback(None)
                return
                
            # Utwórz tymczasowy deck do analizy
            temp_deck = Deck(deck_id=-1)  # -1 oznacza tymczasowy
            
            # Załaduj plik
            if not temp_deck.load_track(str(path)):
                log.error("Nie udało się załadować %s", path.name)
                callback(None)
                return
            
            # Poczekaj na wynik analizy BPM
            result_bpm = None
            
            def on_bpm_ready(bpm: float):
                nonlocal result_bpm
                result_bpm = bpm
            
            def on_analysis_failed(error: str):
                nonlocal result_bpm
                log.error("Analiza BPM failed for %s: %s", path.name, error)
                result_bpm = None
            
            # Podłącz sygnały
            temp_deck.bpmReady.connect(on_bpm_ready)
            temp_deck.analysisFailed.connect(on_analysis_failed)
            
            # Czekaj na wynik (max 30 sekund)
            for _ in range(300):  # 30s * 10 checks/s
                if self._stop_event.is_set():
                    return
                
                if result_bpm is not None or temp_deck.bpm_status in ["ok", "none"]:
                    break
                    
                threading.Event().wait(0.1)  # 100ms
            
            # Pobierz ostateczny wynik
            final_bpm = temp_deck.detected_bpm if temp_deck.detected_bpm > 0 else None
            
            log.info("BPM result %s -> %s", path.name, final_bpm if final_bpm else "—")
            
            # Wywołaj callback
            if not self._stop_event.is_set():
                callback(final_bpm)
                
        except Exception as e:
            log.error("Błąd analizy BPM dla %s: %s", path.name, e)
            if not self._stop_event.is_set():
                callback(None)