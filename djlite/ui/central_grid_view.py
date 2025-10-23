from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import QTimer, Qt
from audio.beat_grid import BeatClock
from audio.waveform_cache import WaveformCache


class CentralGridView(QWidget):
    """Centralny widget wyświetlający waveform z beat grid i stałym playhead.
    
    Wyświetla przebieg audio z nałożoną siatką rytmiczną, gdzie:
    - Playhead jest zawsze na środku
    - Waveform i siatka przesuwają się zgodnie z czasem BeatClock
    - Zmiana BPM/tempo natychmiast wpływa na gęstość siatki
    """
    
    def __init__(self, clock: BeatClock, wf: WaveformCache = None, parent=None):
        super().__init__(parent)
        self.clock = clock
        self.wf = wf
        self.px_per_sec = 120.0     # ZOOM: 120 px = 1 s (zrób +/- przyciski do zmiany)
        self.refresh_hz = 60
        
        # Timer do odświeżania widoku
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(int(1000/self.refresh_hz))
        
        self.setMinimumHeight(140)
        
        # Kolory dla różnych elementów
        self.bg_color = QColor(18, 18, 20)
        self.waveform_color = QColor(200, 200, 200, 150)
        self.playhead_color = QColor(255, 255, 255, 200)
        self.beat_color = QColor(120, 180, 255, 110)
        self.bar_color = QColor(120, 220, 255, 170)
        self.text_color = QColor(255, 255, 255, 180)
    
    def set_waveform(self, wf: WaveformCache):
        """Ustawia nowy waveform cache."""
        self.wf = wf
        self.update()
    
    def set_zoom(self, px_per_sec: float):
        """Ustawia zoom (pikseli na sekundę), ograniczony do 20-600."""
        self.px_per_sec = max(20.0, min(600.0, px_per_sec))
        self.update()
    
    def zoom_in(self):
        """Przybliża widok (zwiększa px_per_sec)."""
        self.set_zoom(self.px_per_sec * 1.2)
    
    def zoom_out(self):
        """Oddala widok (zmniejsza px_per_sec)."""
        self.set_zoom(self.px_per_sec / 1.2)
    
    def paintEvent(self, ev):
        """Główna funkcja rysowania widoku."""
        if self.clock is None:
            return
            
        w = self.width()
        h = self.height()
        center_x = w // 2
        
        # Pobierz czas z uwzględnieniem effective_ratio (tempo + nudge)
        base_time = self.clock.now_sec()
        if hasattr(self, 'deck') and self.deck and hasattr(self.deck, 'effective_ratio'):
            t_now = base_time * self.deck.effective_ratio()
        else:
            t_now = base_time
        
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Tło
        p.fillRect(0, 0, w, h, self.bg_color)
        
        # Waveform (jeśli dostępny)
        if self.wf is not None:
            self._paint_waveform(p, center_x, t_now, w, h)
        
        # Beat grid
        self._paint_beat_grid(p, center_x, t_now, w, h)
        
        # Playhead (zawsze na środku)
        pen = QPen(self.playhead_color, 2)
        p.setPen(pen)
        p.drawLine(center_x, 0, center_x, h)
        
        # Znacznik czasu na playhead
        p.setPen(QPen(self.text_color, 1))
        time_text = f"{t_now:.1f}s"
        p.drawText(center_x + 5, 15, time_text)
        
        # Important: End the painter
        p.end()
    
    def _paint_waveform(self, p: QPainter, center_x: int, t_now: float, w: int, h: int):
        """Rysuje waveform jako paski min/max (peak envelope)."""
        # Oblicz zakres czasowy widoczny na ekranie
        sec_span_left = center_x / self.px_per_sec
        sec_span_right = (w - center_x) / self.px_per_sec
        t0 = max(0.0, t_now - sec_span_left)
        t1 = t_now + sec_span_right
        
        # Pobierz zakres bin-ów dla tego czasu
        b0, b1 = self.wf.bins_range_from_time_span(t0, t1)
        if b1 <= b0:
            return
        
        # Skalowanie Y - środek na połowie wysokości
        mid_y = h // 2
        amp = h * 0.42  # Amplituda zajmuje 84% wysokości
        
        # Rysuj paski min/max
        p.setPen(QPen(self.waveform_color, 1))
        sec_per_bin = self.wf.block_size / self.wf.sr
        
        for i in range(b0, min(b1, len(self.wf.min_peaks))):
            t_bin = i * sec_per_bin
            x = center_x + int((t_bin - t_now) * self.px_per_sec)
            
            if 0 <= x < w:
                # Pobierz min/max peaks dla tego bin-a
                min_val = self.wf.min_peaks[i] if i < len(self.wf.min_peaks) else 0.0
                max_val = self.wf.max_peaks[i] if i < len(self.wf.max_peaks) else 0.0
                
                y_min = int(mid_y - amp * min_val)
                y_max = int(mid_y - amp * max_val)
                
                # Rysuj linię od min do max
                p.drawLine(x, y_min, x, y_max)
    
    def _paint_beat_grid(self, p: QPainter, center_x: int, t_now: float, w: int, h: int):
        """Rysuje siatkę rytmiczną (beat grid)."""
        if not hasattr(self.clock, 'grid') or self.clock.grid is None:
            return
            
        grid = self.clock.grid
        spb = grid.sec_per_beat
        
        if spb <= 0:
            return
        
        # Znajdź zakres czasowy do narysowania
        sec_span_left = center_x / self.px_per_sec
        t_left = t_now - sec_span_left
        
        # Indeks beatu przy lewej krawędzi (z marginesem)
        b_idx = int((t_left - grid.beat_offset) / spb) - 1
        b_idx = max(b_idx, -10000)  # Guard przeciwko zbyt dużym wartościom
        
        # Pędzle dla różnych typów linii
        pen_beat = QPen(self.beat_color, 1)      # Cienkie linie (beat)
        pen_bar = QPen(self.bar_color, 2)        # Grube linie (bar co beats_per_bar)
        
        # Rysuj linie beat-ów
        for k in range(2000):  # Maksymalnie 2000 linii
            b_idx += 1
            t_beat = grid.time_of_beat(b_idx)
            x = center_x + int((t_beat - t_now) * self.px_per_sec)
            
            # Jeśli linia jest poza prawą krawędzią, przerwij
            if x > w:
                break
                
            # Jeśli linia jest przed lewą krawędzią, kontynuuj
            if x < 0:
                continue
            
            # Wybierz styl linii - gruba dla początku taktu
            if (b_idx % grid.beats_per_bar) == 0:
                p.setPen(pen_bar)
            else:
                p.setPen(pen_beat)
            
            # Rysuj linię
            p.drawLine(x, 0, x, h)
            
            # Opcjonalnie: podpisy numerów taktów
            if (b_idx % grid.beats_per_bar) == 0 and x > 10:
                bar_num = (b_idx // grid.beats_per_bar) + 1
                p.setPen(QPen(self.text_color, 1))
                p.drawText(x + 4, 12, f"{bar_num}")
    
    def get_time_at_x(self, x: int) -> float:
        """Zwraca czas (w sekundach) dla pozycji X na ekranie."""
        if self.clock is None:
            return 0.0
        center_x = self.width() // 2
        t_now = self.clock.now_sec()
        return t_now + (x - center_x) / self.px_per_sec
    
    def get_x_for_time(self, time: float) -> int:
        """Zwraca pozycję X na ekranie dla danego czasu."""
        if self.clock is None:
            return 0
        center_x = self.width() // 2
        t_now = self.clock.now_sec()
        return center_x + int((time - t_now) * self.px_per_sec)
    
    def on_tempo_changed(self, deck_name: str, tempo_ratio: float):
        """Obsługuje zmianę tempo na decku - aktualizuje BeatClock.
        
        Args:
            deck_name: Nazwa decka ("A" lub "B")
            tempo_ratio: Nowy współczynnik tempo (1.0 = normalne tempo)
        """
        if self.clock is None:
            return
            
        # Aktualizuj tempo_ratio w BeatClock
        self.clock.set_tempo_ratio(tempo_ratio)
        print(f"🎵 CentralGridView: Tempo changed on deck {deck_name} to {tempo_ratio:.3f}")
    
    def on_bpm_changed(self, deck_name: str, new_bpm: float, preserve_phase: bool = True):
        """Obsługuje zmianę BPM na decku MASTER - aktualizuje grid.bpm.
        
        Args:
            deck_name: Nazwa decka ("A" lub "B")
            new_bpm: Nowy BPM
            preserve_phase: Czy zachować fazę (domyślnie True)
        """
        if self.clock is None or not hasattr(self.clock, 'grid') or self.clock.grid is None:
            return
            
        if preserve_phase:
            self.clock.update_bpm_with_phase_preservation(new_bpm)
        else:
            self.clock.update_bpm(new_bpm)
            
        print(f"🎵 CentralGridView: BPM changed on deck {deck_name} to {new_bpm:.1f}")
    
    def on_nudge_changed(self, deck_name: str, nudge_ratio: float):
        """Obsługuje zmianę nudge na decku - aktualizuje efektywne tempo.
        
        Args:
            deck_name: Nazwa decka ("A" lub "B")
            nudge_ratio: Współczynnik nudge (1.0 = brak nudge)
        """
        if self.clock is None:
            return
            
        # Nudge wpływa na efektywne tempo (tempo_ratio * nudge_ratio)
        # Zakładamy, że tempo_ratio jest już ustawione, więc aktualizujemy całkowite tempo
        current_tempo = self.clock.get_tempo_ratio()
        effective_tempo = current_tempo * nudge_ratio
        
        self.clock.set_tempo_ratio(effective_tempo)
        print(f"🎵 CentralGridView: Nudge changed on deck {deck_name}, effective tempo: {effective_tempo:.3f}")
    
    def sync_with_master_deck(self, master_deck):
        """Synchronizuje CentralGridView z master deckiem.
        
        Args:
            master_deck: Obiekt master deck z atrybutami detected_bpm, tempo_ratio, nudge_ratio
        """
        if self.clock is None or master_deck is None:
            return
            
        # Aktualizuj BPM jeśli dostępny - używamy bpm_playing = bpm_detected * effective_ratio
        if hasattr(master_deck, 'detected_bpm') and master_deck.detected_bpm > 0:
            bpm_playing = master_deck.detected_bpm * master_deck.effective_ratio()
            self.on_bpm_changed("MASTER", bpm_playing, preserve_phase=True)
            
        # Aktualizuj tempo ratio
        if hasattr(master_deck, 'effective_ratio'):
            self.on_tempo_changed("MASTER", master_deck.effective_ratio())
            
        print(f"🎵 CentralGridView: Synced with master deck, BPM={getattr(master_deck, 'detected_bpm', 0):.1f}, effective_ratio={master_deck.effective_ratio():.3f}")