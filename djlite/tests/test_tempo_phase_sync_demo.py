"""Demonstracyjny test synchronizacji tempo i fazy.

Krótki test (30 sekund) pokazujący działanie systemu.
"""

import time
import numpy as np
import logging
from pathlib import Path
import sys

# Dodaj ścieżkę do modułów djlite
sys.path.insert(0, str(Path(__file__).parent.parent))

from audio.tempo_phase_sync import get_tempo_phase_sync
from audio.master_clock import get_master_clock

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

class DemoMockDeck:
    """Uproszczony mock deck dla demonstracji."""
    
    def __init__(self, deck_id: int, bpm: float = 120.0):
        self.deck_id = deck_id
        self.detected_bpm = bpm
        self.position = 0.0
        self.tempo_ratio = 1.0
        self.is_playing = True
        self.start_time = time.time()
        
        # Mock time stretch engine
        self.time_stretch_engine = DemoTimeStretchEngine()
        
        # Symulacja różnych BPM dla demonstracji
        if deck_id == 1:  # Master
            self.base_bpm = 120.0
        else:  # Target
            self.base_bpm = 119.5  # Lekko różne BPM
            
    def get_beat_position(self) -> float:
        """Symuluj pozycję w beatach."""
        elapsed = time.time() - self.start_time
        effective_bpm = self.base_bpm * self.tempo_ratio
        beats_per_second = effective_bpm / 60.0
        return (elapsed * beats_per_second) % 4.0  # 4-beat loop
        
class DemoTimeStretchEngine:
    """Mock time stretch engine dla demo."""
    
    def __init__(self):
        self.tempo_ratio = 1.0
        
    def get_tempo(self) -> float:
        return self.tempo_ratio
        
    def set_tempo(self, ratio: float):
        self.tempo_ratio = ratio
        log.info(f"Tempo correction applied: {ratio:.6f} ({(ratio-1)*100:+.3f}%)")
        
def run_demo_test(duration: int = 30):
    """Uruchom demonstracyjny test synchronizacji.
    
    Args:
        duration: Czas trwania testu w sekundach
    """
    print(f"\n🎵 DEMO: Synchronizacja Tempo+Phase Sync ({duration}s)")
    print("="*50)
    
    # Inicjalizacja
    sample_rate = 48000
    master_clock = get_master_clock(sample_rate)
    tempo_phase_sync = get_tempo_phase_sync(sample_rate)
    
    # Stwórz decks z różnymi BPM
    master_deck = DemoMockDeck(1, 120.0)  # Master: 120 BPM
    target_deck = DemoMockDeck(2, 119.5)  # Target: 119.5 BPM (różnica 0.5 BPM)
    
    print(f"Master Deck: {master_deck.base_bpm} BPM")
    print(f"Target Deck: {target_deck.base_bpm} BPM (różnica: {target_deck.base_bpm - master_deck.base_bpm:+.1f} BPM)")
    print("\nWłączam synchronizację...\n")
    
    # Skonfiguruj synchronizację
    tempo_phase_sync.set_decks(target_deck, master_deck)
    tempo_phase_sync.enable_sync(True)
    
    start_time = time.time()
    last_report_time = start_time
    report_interval = 2.0  # Raportuj co 2 sekundy
    
    try:
        while (time.time() - start_time) < duration:
            current_time = time.time()
            
            # Aktualizuj synchronizację
            tempo_phase_sync.update_sync()
            
            # Raportuj stan co kilka sekund
            if current_time - last_report_time >= report_interval:
                elapsed = current_time - start_time
                sync_state = tempo_phase_sync.get_sync_state()
                
                phase_offset = sync_state.get('phase_offset_beats', 0.0)
                tempo_correction = sync_state.get('tempo_correction_factor', 1.0)
                sync_quality = sync_state.get('sync_quality', 'unknown')
                
                print(f"⏱️  {elapsed:5.1f}s | "
                      f"Phase: {phase_offset:+.4f} beats | "
                      f"Tempo: {tempo_correction:.6f} ({(tempo_correction-1)*100:+.3f}%) | "
                      f"Quality: {sync_quality}")
                      
                last_report_time = current_time
                
            time.sleep(0.05)  # 50ms update rate
            
    except KeyboardInterrupt:
        print("\n⏹️  Test przerwany przez użytkownika")
        
    # Podsumowanie
    final_sync_state = tempo_phase_sync.get_sync_state()
    final_phase = final_sync_state.get('phase_offset_beats', 0.0)
    final_tempo = final_sync_state.get('tempo_correction_factor', 1.0)
    final_quality = final_sync_state.get('sync_quality', 'unknown')
    
    print("\n" + "="*50)
    print("📊 PODSUMOWANIE DEMO:")
    print(f"   Końcowy offset fazy: {final_phase:+.4f} beats")
    print(f"   Końcowa korekcja tempo: {final_tempo:.6f} ({(final_tempo-1)*100:+.3f}%)")
    print(f"   Jakość synchronizacji: {final_quality}")
    
    # Ocena wyniku
    if abs(final_phase) < 0.02 and final_quality in ['excellent', 'good']:
        print("   ✅ Synchronizacja UDANA!")
    elif abs(final_phase) < 0.05:
        print("   ⚠️  Synchronizacja CZĘŚCIOWA")
    else:
        print("   ❌ Synchronizacja NIEUDANA")
        
    print("="*50)
    
def main():
    """Główna funkcja demo."""
    print("🎛️  Demonstracja systemu Tempo+Phase Sync")
    print("\nTen test pokazuje jak system synchronizuje dwa decks o różnych BPM.")
    print("Obserwuj jak phase offset i tempo correction zmieniają się w czasie.")
    
    try:
        duration = 30  # 30 sekund demo
        run_demo_test(duration)
        
    except Exception as e:
        log.error(f"Błąd podczas demo: {e}")
        return 1
        
    return 0
    
if __name__ == '__main__':
    exit(main())