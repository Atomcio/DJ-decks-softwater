"""Test stabilności synchronizacji tempo i fazy.

Sprawdza czy phaseOffsetBeats drift < ±0.01 przez 5 minut.
"""

import time
import numpy as np
import threading
import logging
from typing import List, Dict, Any
from pathlib import Path
import sys

# Dodaj ścieżkę do modułów djlite
sys.path.insert(0, str(Path(__file__).parent.parent))

from audio.tempo_phase_sync import get_tempo_phase_sync
from audio.master_clock import get_master_clock
from audio.deck import Deck

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

class MockDeck:
    """Mock deck dla testów stabilności."""
    
    def __init__(self, deck_id: int, sample_rate: int = 48000):
        self.deck_id = deck_id
        self.sample_rate = sample_rate
        self.detected_bpm = 120.0
        self.position = 0.0
        self.tempo_ratio = 1.0
        self.is_playing = True
        
        # Mock time stretch engine
        self.time_stretch_engine = MockTimeStretchEngine()
        
        # Symulacja dryfu czasu
        self.drift_rate = np.random.uniform(-0.001, 0.001)  # Mały losowy drift
        self.start_time = time.time()
        
    def get_beat_position(self) -> float:
        """Symuluj pozycję w beatach z małym dryfem."""
        elapsed = time.time() - self.start_time
        beats_per_second = self.detected_bpm / 60.0
        
        # Dodaj mały drift dla realistyczności
        drift = self.drift_rate * elapsed
        return (elapsed * beats_per_second + drift) % 4.0  # 4-beat loop
        
class MockTimeStretchEngine:
    """Mock time stretch engine."""
    
    def __init__(self):
        self.tempo_ratio = 1.0
        
    def get_tempo(self) -> float:
        return self.tempo_ratio
        
    def set_tempo(self, ratio: float):
        self.tempo_ratio = ratio
        
class StabilityTest:
    """Test stabilności synchronizacji tempo i fazy."""
    
    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.master_clock = get_master_clock(sample_rate)
        self.tempo_phase_sync = get_tempo_phase_sync(sample_rate)
        
        # Decks
        self.master_deck = MockDeck(1, sample_rate)
        self.target_deck = MockDeck(2, sample_rate)
        
        # Dane testowe
        self.phase_history: List[float] = []
        self.tempo_history: List[float] = []
        self.sync_quality_history: List[str] = []
        self.timestamps: List[float] = []
        
        # Parametry testu
        self.test_duration = 300  # 5 minut
        self.sample_interval = 0.1  # 100ms
        self.max_allowed_drift = 0.01  # ±0.01 beat
        
        # Kontrola testu
        self.test_running = False
        self.test_thread = None
        
    def setup_sync(self):
        """Skonfiguruj synchronizację między deckami."""
        self.tempo_phase_sync.set_decks(self.target_deck, self.master_deck)
        self.tempo_phase_sync.enable_sync(True)
        log.info("Synchronizacja skonfigurowana")
        
    def run_stability_test(self) -> Dict[str, Any]:
        """Uruchom test stabilności przez 5 minut.
        
        Returns:
            Wyniki testu stabilności
        """
        log.info(f"Rozpoczynam test stabilności na {self.test_duration} sekund")
        
        self.setup_sync()
        self.test_running = True
        
        start_time = time.time()
        next_sample_time = start_time + self.sample_interval
        
        try:
            while self.test_running and (time.time() - start_time) < self.test_duration:
                current_time = time.time()
                
                if current_time >= next_sample_time:
                    self._sample_sync_state(current_time - start_time)
                    next_sample_time += self.sample_interval
                    
                # Aktualizuj synchronizację
                self.tempo_phase_sync.update_sync()
                
                # Krótka pauza
                time.sleep(0.01)
                
        except KeyboardInterrupt:
            log.info("Test przerwany przez użytkownika")
        finally:
            self.test_running = False
            
        return self._analyze_results()
        
    def _sample_sync_state(self, elapsed_time: float):
        """Pobierz próbkę stanu synchronizacji."""
        try:
            sync_state = self.tempo_phase_sync.get_sync_state()
            
            self.timestamps.append(elapsed_time)
            self.phase_history.append(sync_state.get('phase_offset_beats', 0.0))
            self.tempo_history.append(sync_state.get('tempo_correction_factor', 1.0))
            self.sync_quality_history.append(sync_state.get('sync_quality', 'unknown'))
            
            # Log co 30 sekund
            if len(self.timestamps) % 300 == 0:  # co 30s przy 100ms interval
                log.info(f"Test: {elapsed_time:.1f}s, Phase offset: {self.phase_history[-1]:.4f}, "
                        f"Tempo correction: {self.tempo_history[-1]:.6f}, "
                        f"Quality: {self.sync_quality_history[-1]}")
                        
        except Exception as e:
            log.error(f"Błąd podczas próbkowania: {e}")
            
    def _analyze_results(self) -> Dict[str, Any]:
        """Analizuj wyniki testu stabilności."""
        if not self.phase_history:
            return {'error': 'Brak danych do analizy'}
            
        phase_array = np.array(self.phase_history)
        tempo_array = np.array(self.tempo_history)
        
        # Analiza dryfu fazy
        phase_drift = np.max(phase_array) - np.min(phase_array)
        phase_std = np.std(phase_array)
        phase_mean = np.mean(np.abs(phase_array))
        
        # Analiza stabilności tempo
        tempo_drift = np.max(tempo_array) - np.min(tempo_array)
        tempo_std = np.std(tempo_array)
        tempo_mean = np.mean(tempo_array)
        
        # Analiza jakości sync
        quality_counts = {}
        for quality in self.sync_quality_history:
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
            
        # Test stabilności
        stability_passed = phase_drift <= self.max_allowed_drift
        
        results = {
            'test_duration': len(self.timestamps) * self.sample_interval,
            'samples_count': len(self.timestamps),
            'stability_test_passed': stability_passed,
            'max_allowed_drift': self.max_allowed_drift,
            
            'phase_analysis': {
                'drift_range': phase_drift,
                'std_deviation': phase_std,
                'mean_absolute_error': phase_mean,
                'max_offset': np.max(np.abs(phase_array)),
                'within_tolerance': stability_passed
            },
            
            'tempo_analysis': {
                'correction_range': tempo_drift,
                'std_deviation': tempo_std,
                'mean_correction': tempo_mean,
                'max_correction': np.max(np.abs(tempo_array - 1.0))
            },
            
            'sync_quality_distribution': quality_counts,
            
            'performance_metrics': {
                'excellent_percentage': quality_counts.get('excellent', 0) / len(self.sync_quality_history) * 100,
                'good_or_better_percentage': (quality_counts.get('excellent', 0) + quality_counts.get('good', 0)) / len(self.sync_quality_history) * 100
            }
        }
        
        return results
        
    def print_results(self, results: Dict[str, Any]):
        """Wydrukuj wyniki testu w czytelnej formie."""
        print("\n" + "="*60)
        print("WYNIKI TESTU STABILNOŚCI TEMPO+PHASE SYNC")
        print("="*60)
        
        if 'error' in results:
            print(f"BŁĄD: {results['error']}")
            return
            
        print(f"Czas trwania testu: {results['test_duration']:.1f} sekund")
        print(f"Liczba próbek: {results['samples_count']}")
        print(f"Maksymalny dozwolony drift: ±{results['max_allowed_drift']} beat")
        
        print("\nANALIZA FAZY:")
        phase = results['phase_analysis']
        print(f"  Zakres dryfu: {phase['drift_range']:.6f} beat")
        print(f"  Odchylenie standardowe: {phase['std_deviation']:.6f} beat")
        print(f"  Średni błąd bezwzględny: {phase['mean_absolute_error']:.6f} beat")
        print(f"  Maksymalny offset: {phase['max_offset']:.6f} beat")
        
        print("\nANALIZA TEMPO:")
        tempo = results['tempo_analysis']
        print(f"  Zakres korekcji: {tempo['correction_range']:.6f}")
        print(f"  Odchylenie standardowe: {tempo['std_deviation']:.6f}")
        print(f"  Średnia korekcja: {tempo['mean_correction']:.6f}")
        print(f"  Maksymalna korekcja: {tempo['max_correction']:.6f}")
        
        print("\nJAKOŚĆ SYNCHRONIZACJI:")
        for quality, count in results['sync_quality_distribution'].items():
            percentage = count / results['samples_count'] * 100
            print(f"  {quality}: {count} próbek ({percentage:.1f}%)")
            
        print("\nMETRYKI WYDAJNOŚCI:")
        perf = results['performance_metrics']
        print(f"  Doskonała jakość: {perf['excellent_percentage']:.1f}%")
        print(f"  Dobra lub lepsza: {perf['good_or_better_percentage']:.1f}%")
        
        print("\nWYNIK TESTU STABILNOŚCI:")
        if results['stability_test_passed']:
            print("  ✅ ZALICZONY - Drift fazy mieści się w tolerancji")
        else:
            print("  ❌ NIEZALICZONY - Drift fazy przekracza tolerancję")
            
        print("="*60)
        
def main():
    """Uruchom test stabilności."""
    print("Test stabilności synchronizacji Tempo+Phase Sync")
    print("Sprawdza czy phaseOffsetBeats drift < ±0.01 przez 5 minut")
    print("\nNaciśnij Ctrl+C aby przerwać test wcześniej\n")
    
    test = StabilityTest()
    
    try:
        results = test.run_stability_test()
        test.print_results(results)
        
        # Zapisz wyniki do pliku
        import json
        results_file = Path(__file__).parent / "stability_test_results.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nWyniki zapisane do: {results_file}")
        
    except Exception as e:
        log.error(f"Błąd podczas testu: {e}")
        return 1
        
    return 0 if results.get('stability_test_passed', False) else 1
    
if __name__ == '__main__':
    exit(main())