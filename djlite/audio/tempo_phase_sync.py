"""Tempo+Phase Sync - wyrównanie tempo przez resampling/pitch shift.

System synchronizacji tempo i fazy między deckami używający:
- MasterClock jako źródło prawdy dla czasu
- TimeStretchEngine dla korekcji tempo
- PLL (Phase-Locked Loop) dla stabilnej blokady fazy
"""

import numpy as np
import threading
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass
import logging
from .master_clock import get_master_clock
from .time_stretch import TimeStretchEngine

log = logging.getLogger(__name__)

@dataclass
class SyncState:
    """Stan synchronizacji tempo i fazy."""
    phase_offset_beats: float = 0.0
    tempo_correction_factor: float = 1.0
    sync_enabled: bool = False
    sync_quality: str = "poor"  # poor, fair, good, excellent
    last_update_time: float = 0.0
    
class TempoPhaseSync:
    """System synchronizacji tempo i fazy między deckami.
    
    Używa kombinacji:
    - Tempo correction przez TimeStretchEngine
    - Phase correction przez micro-adjustments
    - PLL dla stabilnej blokady
    """
    
    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.master_clock = get_master_clock(sample_rate)
        
        # Stan synchronizacji
        self.sync_state = SyncState()
        self.target_deck = None
        self.master_deck = None
        
        # Parametry PLL (proporcjonalno-całkująco-różniczkujący)
        # Zoptymalizowane dla stabilnej synchronizacji fazy
        self.kp = 1.2   # Proporcjonalny - szybka reakcja na błąd
        self.ki = 0.15  # Całkujący - eliminacja błędu stałego
        self.kd = 0.08  # Różniczkujący - tłumienie oscylacji
        
        # Parametry adaptacyjne PLL
        self.adaptive_gain = True
        self.min_kp = 0.5
        self.max_kp = 2.0
        
        # Historia błędów dla PID
        self.error_history = []
        self.integral_error = 0.0
        self.last_error = 0.0
        
        # Limity korekcji z histerezą
        self.max_tempo_correction = 0.005  # ±0.5% maksymalna korekcja
        self.max_phase_correction = 0.1    # ±0.1 beat
        
        # Histereza dla tempo correction
        self.tempo_hysteresis_threshold = 0.001  # 0.1% próg włączenia korekcji
        self.tempo_hysteresis_release = 0.0005   # 0.05% próg wyłączenia korekcji
        self.tempo_correction_active = False
        
        # Ograniczenia adaptacyjne
        self.min_tempo_correction = 0.0001  # Minimalna korekcja (0.01%)
        self.tempo_correction_ramp_rate = 0.95  # Szybkość narastania/opadania korekcji
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Timing
        self.update_interval = 0.05  # 50ms
        self.last_update = 0.0
        
        log.info("TempoPhaseSync initialized")
    
    def set_decks(self, target_deck, master_deck):
        """Ustaw decki do synchronizacji.
        
        Args:
            target_deck: Deck który ma być synchronizowany
            master_deck: Deck master (źródło synchronizacji)
        """
        with self.lock:
            self.target_deck = target_deck
            self.master_deck = master_deck
            self.reset_sync_state()
            
        log.info(f"Sync decks set: target={getattr(target_deck, 'deck_id', 'unknown')}, master={getattr(master_deck, 'deck_id', 'unknown')}")
    
    def enable_sync(self, enabled: bool):
        """Włącz/wyłącz synchronizację."""
        with self.lock:
            self.sync_state.sync_enabled = enabled
            if enabled:
                self.reset_sync_state()
            
        log.info(f"Tempo+Phase sync {'enabled' if enabled else 'disabled'}")
    
    def reset_sync_state(self):
        """Resetuj stan synchronizacji."""
        self.sync_state.phase_offset_beats = 0.0
        self.sync_state.tempo_correction_factor = 1.0
        self.sync_state.sync_quality = "poor"
        self.error_history.clear()
        self.integral_error = 0.0
        self.last_error = 0.0
        
        master_state = self.master_clock.get_state()
        self.sync_state.last_update_time = master_state.monotonic_time
    
    def update_sync(self) -> bool:
        """Aktualizuj synchronizację tempo i fazy.
        
        Returns:
            True jeśli synchronizacja została zaktualizowana
        """
        if not self._should_update():
            return False
            
        with self.lock:
            if not self.sync_state.sync_enabled or not self.target_deck or not self.master_deck:
                return False
                
            # Pobierz aktualny stan
            phase_error = self._calculate_phase_error()
            if phase_error is None:
                return False
                
            # Aktualizuj PLL
            tempo_correction = self._update_pll(phase_error)
            
            # Aplikuj korekcję tempo
            self._apply_tempo_correction(tempo_correction)
            
            # Aktualizuj stan
            master_state = self.master_clock.get_state()
            self.sync_state.last_update_time = master_state.monotonic_time
            self.sync_state.phase_offset_beats = phase_error
            
            # Oceń jakość synchronizacji
            self._update_sync_quality()
            
            return True
    
    def _should_update(self) -> bool:
        """Sprawdź czy należy zaktualizować synchronizację."""
        master_state = self.master_clock.get_state()
        dt = master_state.monotonic_time - self.last_update
        
        if dt >= self.update_interval:
            self.last_update = master_state.monotonic_time
            return True
        return False
    
    def _calculate_phase_error(self) -> Optional[float]:
        """Oblicz błąd fazy między deckami w beatach.
        
        Returns:
            Błąd fazy w beatach lub None jeśli nie można obliczyć
        """
        try:
            # Sprawdź czy decki mają wymagane atrybuty
            if not all(hasattr(deck, attr) for deck in [self.target_deck, self.master_deck] 
                      for attr in ['clock', 'detected_bpm', 'effective_ratio']):
                return None
                
            if self.master_deck.detected_bpm <= 0:
                return None
                
            # Pobierz pozycje z zegarów (używają MasterClock)
            master_time = self.master_deck.clock.now_seconds()
            target_time = self.target_deck.clock.now_seconds()
            
            # Oblicz sekundy na beat dla mastera
            master_bpm = self.master_deck.detected_bpm * self.master_deck.effective_ratio()
            spb = 60.0 / max(1e-6, master_bpm)
            
            # Pozycje w beatach względem beat_offset
            master_offset = getattr(self.master_deck, 'beat_offset', 0.0)
            target_offset = getattr(self.target_deck, 'beat_offset', 0.0)
            
            master_beat = (master_time - master_offset) / spb
            target_beat = (target_time - target_offset) / spb
            
            # Błąd fazy w beatach
            phase_error = master_beat - target_beat
            
            # Normalizuj do (-0.5, 0.5] - najbliższa różnica fazy
            phase_error = (phase_error + 0.5) % 1.0 - 0.5
            
            return phase_error
            
        except Exception as e:
            log.error(f"Error calculating phase error: {e}")
            return None
    
    def _update_pll(self, phase_error: float) -> float:
        """Aktualizuj PLL i oblicz korekcję tempo.
        
        Args:
            phase_error: Błąd fazy w beatach
            
        Returns:
            Korekcja tempo (mnożnik)
        """
        # Dodaj do historii błędów
        self.error_history.append(phase_error)
        if len(self.error_history) > 100:  # Zwiększona historia dla lepszej analizy
            self.error_history.pop(0)
            
        # Adaptacyjne dostrojenie wzmocnienia
        if self.adaptive_gain and len(self.error_history) > 10:
            self._adapt_pll_gains()
            
        # Oblicz składniki PID z ulepszoną logiką
        proportional = self.kp * phase_error
        
        # Integral z anti-windup i resetem przy dużych błędach
        if abs(phase_error) > 0.5:  # Reset integral przy dużych błędach
            self.integral_error *= 0.5
        else:
            self.integral_error += phase_error
            
        # Zaawansowany anti-windup z ograniczeniem dynamicznym
        max_integral = 5.0 / max(self.ki, 0.01)  # Dynamiczne ograniczenie
        self.integral_error = max(-max_integral, min(max_integral, self.integral_error))
        integral = self.ki * self.integral_error
        
        # Derivative z filtrem dla redukcji szumu
        raw_derivative = phase_error - self.last_error
        # Prosty filtr dolnoprzepustowy dla derivative
        if hasattr(self, '_filtered_derivative'):
            self._filtered_derivative = 0.7 * self._filtered_derivative + 0.3 * raw_derivative
        else:
            self._filtered_derivative = raw_derivative
            
        derivative = self.kd * self._filtered_derivative
        self.last_error = phase_error
        
        # Suma PID z nieliniową charakterystyką
        pid_output = proportional + integral + derivative
        
        # Nieliniowa funkcja korekcji dla lepszej stabilności
        if abs(pid_output) < 0.01:
            # Martwa strefa dla małych błędów
            pid_output *= 0.5
        elif abs(pid_output) > 0.1:
            # Ograniczenie dla dużych korekcji
            pid_output = 0.1 * np.sign(pid_output) + 0.5 * (pid_output - 0.1 * np.sign(pid_output))
        
        # Konwertuj na korekcję tempo
        tempo_correction = 1.0 + pid_output * 0.01  # Skalowanie do małych korekcji
        
        # Zastosuj histerezę dla tempo correction
        tempo_correction = self._apply_tempo_hysteresis(tempo_correction)
        
        # Dynamiczne ograniczenie korekcji na podstawie jakości sync
        max_corr = self._get_adaptive_tempo_limit()
            
        min_correction = 1.0 - max_corr
        max_correction = 1.0 + max_corr
        tempo_correction = max(min_correction, min(max_correction, tempo_correction))
        
        return tempo_correction
    
    def _apply_tempo_hysteresis(self, tempo_correction: float) -> float:
        """Zastosuj histerezę dla tempo correction aby uniknąć oscylacji.
        
        Args:
            tempo_correction: Surowa korekcja tempo
            
        Returns:
            Korekcja tempo z zastosowaną histerezą
        """
        correction_magnitude = abs(tempo_correction - 1.0)
        
        # Logika włączania/wyłączania korekcji z histerezą
        if not self.tempo_correction_active:
            # Korekcja nieaktywna - sprawdź czy włączyć
            if correction_magnitude > self.tempo_hysteresis_threshold:
                self.tempo_correction_active = True
            else:
                return 1.0  # Brak korekcji
        else:
            # Korekcja aktywna - sprawdź czy wyłączyć
            if correction_magnitude < self.tempo_hysteresis_release:
                self.tempo_correction_active = False
                return 1.0  # Wyłącz korekcję
        
        # Zastosuj ramping dla płynnych zmian
        if hasattr(self, '_last_tempo_correction'):
            # Płynne przejście między korekcjami
            tempo_correction = (self._last_tempo_correction * self.tempo_correction_ramp_rate + 
                              tempo_correction * (1.0 - self.tempo_correction_ramp_rate))
        
        self._last_tempo_correction = tempo_correction
        
        # Zastosuj minimalny próg korekcji
        if correction_magnitude < self.min_tempo_correction:
            return 1.0
            
        return tempo_correction
    
    def _get_adaptive_tempo_limit(self) -> float:
        """Oblicz adaptacyjny limit tempo correction na podstawie jakości sync.
        
        Returns:
            Maksymalny dozwolony tempo correction
        """
        if hasattr(self.sync_state, 'sync_quality'):
            if self.sync_state.sync_quality == "excellent":
                return 0.0005  # 0.05% dla doskonałego sync
            elif self.sync_state.sync_quality == "good":
                return 0.001   # 0.1% dla dobrego sync
            elif self.sync_state.sync_quality == "fair":
                return 0.002   # 0.2% dla przeciętnego sync
            else:
                return self.max_tempo_correction  # Pełny zakres dla słabego sync
        else:
            return self.max_tempo_correction
    
    def _adapt_pll_gains(self):
        """Adaptacyjne dostrojenie parametrów PLL na podstawie historii błędów."""
        if len(self.error_history) < 20:
            return
            
        recent_errors = self.error_history[-20:]
        error_variance = np.var(recent_errors)
        mean_abs_error = np.mean(np.abs(recent_errors))
        
        # Dostrojenie Kp na podstawie wariancji błędu
        if error_variance > 0.01:  # Duża wariancja = zmniejsz Kp
            self.kp = max(self.min_kp, self.kp * 0.95)
        elif error_variance < 0.001 and mean_abs_error > 0.02:  # Mała wariancja ale duży błąd = zwiększ Kp
            self.kp = min(self.max_kp, self.kp * 1.05)
            
        # Dostrojenie Ki na podstawie średniego błędu
        if mean_abs_error > 0.05:
            self.ki = min(0.25, self.ki * 1.02)  # Zwiększ Ki dla dużych błędów stałych
        elif mean_abs_error < 0.01:
            self.ki = max(0.05, self.ki * 0.98)  # Zmniejsz Ki gdy błąd jest mały
    
    def _apply_tempo_correction(self, tempo_correction: float):
        """Aplikuj korekcję tempo do target deck.
        
        Args:
            tempo_correction: Mnożnik korekcji tempo
        """
        try:
            if hasattr(self.target_deck, 'time_stretch_engine'):
                # Użyj TimeStretchEngine dla precyzyjnej korekcji
                current_tempo = self.target_deck.time_stretch_engine.get_tempo()
                new_tempo = current_tempo * tempo_correction
                self.target_deck.time_stretch_engine.set_tempo(new_tempo)
                
                self.sync_state.tempo_correction_factor = tempo_correction
                
            elif hasattr(self.target_deck, 'tempo_ratio'):
                # Fallback do prostej korekcji tempo_ratio
                self.target_deck.tempo_ratio *= tempo_correction
                self.sync_state.tempo_correction_factor = tempo_correction
                
        except Exception as e:
            log.error(f"Error applying tempo correction: {e}")
    
    def _update_sync_quality(self):
        """Oceń jakość synchronizacji na podstawie historii błędów."""
        if len(self.error_history) < 10:
            self.sync_state.sync_quality = "poor"
            return
            
        # Oblicz średni błąd i odchylenie standardowe
        recent_errors = self.error_history[-10:]
        mean_error = np.mean(np.abs(recent_errors))
        std_error = np.std(recent_errors)
        
        # Oceń jakość
        if mean_error < 0.01 and std_error < 0.005:
            self.sync_state.sync_quality = "excellent"
        elif mean_error < 0.02 and std_error < 0.01:
            self.sync_state.sync_quality = "good"
        elif mean_error < 0.05 and std_error < 0.02:
            self.sync_state.sync_quality = "fair"
        else:
            self.sync_state.sync_quality = "poor"
    
    def get_sync_state(self) -> Dict[str, Any]:
        """Pobierz aktualny stan synchronizacji.
        
        Returns:
            Słownik z informacjami o stanie synchronizacji
        """
        with self.lock:
            return {
                'enabled': self.sync_state.sync_enabled,
                'phase_offset_beats': self.sync_state.phase_offset_beats,
                'tempo_correction_factor': self.sync_state.tempo_correction_factor,
                'sync_quality': self.sync_state.sync_quality,
                'error_history_size': len(self.error_history),
                'integral_error': self.integral_error,
                'last_error': self.last_error,
                'kp': self.kp,
                'ki': self.ki,
                'kd': self.kd
            }
    
    def set_pll_parameters(self, kp: float = None, ki: float = None, kd: float = None):
        """Ustaw parametry PLL.
        
        Args:
            kp: Proportional gain
            ki: Integral gain  
            kd: Derivative gain
        """
        with self.lock:
            if kp is not None:
                self.kp = max(0.0, min(1.0, kp))
            if ki is not None:
                self.ki = max(0.0, min(0.1, ki))
            if kd is not None:
                self.kd = max(0.0, min(0.5, kd))
                
        log.info(f"PLL parameters updated: kp={self.kp}, ki={self.ki}, kd={self.kd}")
    
    def set_correction_limits(self, max_tempo: float = None, max_phase: float = None):
        """Ustaw limity korekcji.
        
        Args:
            max_tempo: Maksymalna korekcja tempo (±%)
            max_phase: Maksymalna korekcja fazy (±beats)
        """
        with self.lock:
            if max_tempo is not None:
                self.max_tempo_correction = max(0.001, min(0.02, max_tempo))
            if max_phase is not None:
                self.max_phase_correction = max(0.01, min(0.5, max_phase))
                
        log.info(f"Correction limits updated: tempo=±{self.max_tempo_correction*100:.1f}%, phase=±{self.max_phase_correction:.2f}beats")

# Singleton instance
_tempo_phase_sync_instance = None
_sync_lock = threading.Lock()

def get_tempo_phase_sync(sample_rate: int = 48000) -> TempoPhaseSync:
    """Pobierz singleton instance TempoPhaseSync.
    
    Args:
        sample_rate: Sample rate audio
        
    Returns:
        TempoPhaseSync instance
    """
    global _tempo_phase_sync_instance
    
    if _tempo_phase_sync_instance is None:
        with _sync_lock:
            if _tempo_phase_sync_instance is None:
                _tempo_phase_sync_instance = TempoPhaseSync(sample_rate)
                
    return _tempo_phase_sync_instance