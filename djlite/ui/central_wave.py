from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import QTimer, Qt
import math

class CentralWaveView(QWidget):
    def __init__(self, source_deck, master_deck, px_per_sec=120, parent=None):
        super().__init__(parent)
        self.source_deck = source_deck    # deck A/B do waveformu (dla kompatybilności)
        self.master_deck = master_deck    # deck MASTER do siatki
        self.deck_a = None                # referencja do deck A
        self.deck_b = None                # referencja do deck B
        self.px_per_sec = px_per_sec
        self.waveform_cache = None        # WaveformCache object (deck A)
        self.waveform_cache_b = None      # WaveformCache object (deck B)
        self.scroll_offset_sec = 0.0      # przesunięcie widoku w sekundach
        self._dragging = False            # czy jesteśmy w trybie drag
        self._target_deck = None          # deck który przeciągamy
        self._drag_start_x = None         # pozycja X początku przeciągania
        self._drag_start_time = None      # czas początku przeciągania
        self._last_drag_x = None          # ostatnia pozycja X podczas przeciągania
        self.setMinimumHeight(120)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000//60)        # 60 FPS

    # API do przełączania
    def set_source_deck(self, deck):
        self.source_deck = deck
    
    def set_master_deck(self, deck):
        self.master_deck = deck
    
    def set_waveform_cache(self, waveform_cache):
        """Ustawia cache waveform do wyświetlania (deck A)."""
        self.waveform_cache = waveform_cache
    
    def set_waveform_cache_b(self, waveform_cache):
        """Ustawia cache waveform do wyświetlania (deck B)."""
        self.waveform_cache_b = waveform_cache
    
    def set_deck_references(self, deck_a, deck_b):
        """Ustawia referencje do obu decków."""
        self.deck_a = deck_a
        self.deck_b = deck_b
    
    def _t_ref(self, deck=None) -> float:
        """Zwraca czas referencyjny dla danego decka z uwzględnieniem tempo/nudge."""
        target_deck = deck if deck else self.master_deck
        if target_deck and hasattr(target_deck, 'clock'):
            # Podstawowy czas z zegara audio
            base_time = target_deck.clock.now_seconds()
            # Jeśli deck ma effective_ratio, użyj go do skalowania czasu wyświetlania
            if hasattr(target_deck, 'effective_ratio'):
                # Czas wyświetlania = czas rzeczywisty * effective_ratio
                # To sprawia, że waveform przesuwa się szybciej/wolniej zgodnie z tempo/nudge
                return base_time * target_deck.effective_ratio()
            return base_time
        return 0.0
    
    def _time_from_x(self, x: int) -> float:
        """Konwertuje pozycję X na czas w sekundach względem referencji mastera."""
        cx = self.width() // 2
        t_ref = self._t_ref() + self.scroll_offset_sec
        return t_ref + (x - cx) / self.px_per_sec
    
    def _snap_to_beat(self, t: float) -> float:
        """Zaokrągla czas do najbliższego beatu MASTER'a."""
        md = self.master_deck
        if md is None or not hasattr(md, 'detected_bpm') or md.detected_bpm is None:
            return t
        if QApplication.keyboardModifiers() & Qt.AltModifier:
            return t
        bpm_playing = md.detected_bpm * md.tempo_ratio * md.nudge_ratio
        spb = 60.0 / max(1e-6, bpm_playing)
        offset = getattr(md, "beat_offset", 0.0)
        beat_idx = round((t - offset) / spb)
        return offset + beat_idx * spb
    
    def zoom_in(self):
        self.px_per_sec = min(600, self.px_per_sec*1.25)
    
    def zoom_out(self):
        self.px_per_sec = max(20, self.px_per_sec/1.25)
    
    def reset_view(self):
        """Resetuje przesunięcie widoku do playheadu."""
        self.scroll_offset_sec = 0.0
    
    def wheelEvent(self, ev):
        """Obsługa scroll wheel - przewijanie widoku."""
        delta = ev.angleDelta().y() / 120  # jeden notch
        self.scroll_offset_sec += -delta * (1.0 / self.px_per_sec) * 200  # 200px skoku
        ev.accept()
    
    def _get_target_deck(self, y_pos):
        """Zwraca deck na podstawie pozycji Y kliknięcia."""
        if y_pos < self.height() // 2:
            return self.deck_a  # górna połowa = deck A
        else:
            return self.deck_b  # dolna połowa = deck B
    
    def mousePressEvent(self, ev):
        """Rozpoczęcie vinyl-style drag mode."""
        self._dragging = True
        self._target_deck = self._get_target_deck(ev.pos().y())
        self._drag_start_x = ev.pos().x()
        self._drag_start_time = self._target_deck.position if self._target_deck else 0.0
        self._last_drag_x = ev.pos().x()
        self.setCursor(Qt.ClosedHandCursor)
        ev.accept()
    
    def mouseMoveEvent(self, ev):
        """Vinyl-style dragging - płynne przesuwanie playhead."""
        if self._dragging and self._target_deck:
            # Oblicz różnicę w pikselach od ostatniej pozycji
            dx = ev.pos().x() - self._last_drag_x
            self._last_drag_x = ev.pos().x()
            
            # Konwertuj na czas (im szybciej przeciągamy, tym szybciej się przesuwa)
            # Odwrócony kierunek: prawo = do przodu, lewo = do tyłu
            time_delta = -dx / self.px_per_sec
            
            # Zastosuj przesunięcie do aktualnej pozycji decka
            new_time = self._target_deck.position + time_delta
            new_time = max(0.0, min(new_time, self._target_deck.duration or 0.0))
            
            # Wykonaj seek w czasie rzeczywistym
            self._target_deck.seek_to(new_time)
            self.update()
        ev.accept()
    
    def mouseReleaseEvent(self, ev):
        """Zakończenie vinyl drag."""
        if self._dragging:
            self._dragging = False
            self._target_deck = None
            self._drag_start_x = None
            self._drag_start_time = None
            self._last_drag_x = None
            self.setCursor(Qt.ArrowCursor)
        ev.accept()
    
    def mouseDoubleClickEvent(self, ev):
        """Double click - natychmiastowy seek z snap to beat."""
        target_deck = self._get_target_deck(ev.pos().y())
        if target_deck:
            t = self._snap_to_beat(self._time_from_x(ev.pos().x()))
            target_deck.seek_to(t)
            self.update()
        ev.accept()

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        cx = w//2
        p.fillRect(0, 0, w, h, QColor(18, 18, 20))

        # playhead
        p.setPen(QPen(QColor(255, 255, 255, 220), 2))
        p.drawLine(cx, 0, cx, h)

        # Podczas przeciągania kursor zmienia się na ClosedHand
        # Playhead przesuwa się w czasie rzeczywistym bez ghost preview
        
        # Rysuj waveformy i gridy - każdy deck ze swoim własnym czasem
        if self.waveform_cache and self.deck_a:
            t_ref_a = self._t_ref(self.deck_a) + self.scroll_offset_sec
            self._paint_waveform(p, cx, w, h//2, t_ref_a, "A")
            self._paint_grid(p, cx, w, h, self.deck_a, "A", t_ref_a)
            
        if self.waveform_cache_b and self.deck_b:
            t_ref_b = self._t_ref(self.deck_b) + self.scroll_offset_sec
            self._paint_waveform(p, cx, w, h//2, t_ref_b, "B", h//2)
            self._paint_grid(p, cx, w, h, self.deck_b, "B", t_ref_b)
        
        # Linia separująca między deckami
        p.setPen(QPen(QColor(80, 80, 80), 1))
        p.drawLine(0, h//2, w, h//2)
        
        # Etykiety decków
        p.setPen(QPen(QColor(200, 200, 200), 1))
        p.drawText(5, 15, "DECK A")
        p.drawText(5, h//2 + 15, "DECK B")
        
        # Important: End the painter
        p.end()

    def _paint_waveform(self, p, cx, w, h, t_ref, deck="A", y_offset=0):
        wf = self.waveform_cache if deck == "A" else self.waveform_cache_b
        if not wf:
            return
            
        # Pobierz deck object dla skalowania
        deck_obj = self.deck_a if deck == "A" else self.deck_b
        
        # Skaluj px_per_sec względem effective_ratio dla wizualnego rozciągania/ściskania
        effective_px_per_sec = self.px_per_sec
        if deck_obj and hasattr(deck_obj, 'effective_ratio'):
            effective_px_per_sec = self.px_per_sec * deck_obj.effective_ratio()
            
        sec_per_bin = wf.block / wf.sr
        mid, amp = h//2 + y_offset, h*0.42
        
        # KLUCZOWA ZMIANA: zakres czasowy liczony od t_ref (czas mastera) z effective scaling
        t_left = t_ref - cx/effective_px_per_sec
        t_right = t_ref + (w-cx)/effective_px_per_sec
        # które biny narysować
        i0 = max(0, int(t_left/sec_per_bin)-2)
        i1 = min(len(wf.min_peaks)-1, int(t_right/sec_per_bin)+2)

        # Różne kolory dla różnych decków
        color = QColor(200, 200, 200, 170) if deck == "A" else QColor(255, 150, 150, 170)
        p.setPen(QPen(color, 1))
        
        for i in range(i0, i1):
            t_i = i*sec_per_bin
            # KLUCZOWA ZMIANA: pozycje X liczone od t_ref z effective scaling
            x = cx + int((t_i - t_ref)*effective_px_per_sec)
            if 0 <= x < w:
                y0 = int(mid - amp*wf.min_peaks[i])
                y1 = int(mid - amp*wf.max_peaks[i])
                p.drawLine(x, y0, x, y1)

    def _paint_grid(self, p, cx, w, h, deck, deck_name, t_ref):
        if not deck or not hasattr(deck, 'detected_bpm') or deck.detected_bpm is None:
            return

        # Skaluj px_per_sec względem effective_ratio dla spójności z waveform
        effective_px_per_sec = self.px_per_sec
        if hasattr(deck, 'effective_ratio'):
            effective_px_per_sec = self.px_per_sec * deck.effective_ratio()

        # realny BPM siatki (to, co gra faktycznie) - używaj effective_ratio() dla spójności
        if hasattr(deck, 'effective_ratio'):
            bmp_playing = deck.detected_bpm * deck.effective_ratio()
        else:
            bmp_playing = deck.detected_bpm * deck.tempo_ratio * deck.nudge_ratio
        spb = 60.0 / max(1e-6, bmp_playing)
        offset = getattr(deck, "beat_offset", 0.0)  # s: pierwszy beat
        beats_per_bar = getattr(deck, "beats_per_bar", 4)

        # KLUCZOWA ZMIANA: używamy t_ref (czas mastera) z effective scaling
        t_left = t_ref - cx/effective_px_per_sec
        # indeks beatu przy lewej krawędzi
        b_idx = math.floor((t_left - offset)/spb) - 1

        # Określ obszar rysowania dla danego decka
        if deck_name == "A":
            y_start = 0
            y_end = h // 2
            pen_beat = QPen(QColor(100, 150, 100, 110), 1)  # Zielonkawy dla A
            pen_bar = QPen(QColor(150, 200, 150, 170), 2)
        else:  # deck B
            y_start = h // 2
            y_end = h
            pen_beat = QPen(QColor(150, 100, 100, 110), 1)  # Czerwonawy dla B
            pen_bar = QPen(QColor(200, 150, 150, 170), 2)

        # Grid wyłączony - użytkownik nie chce pionowych linii
        # for k in range(0, 4000):
        #     b = b_idx + k
        #     t_b = offset + b*spb
        #     # KLUCZOWA ZMIANA: pozycje X liczone od t_ref
        #     x = cx + int((t_b - t_ref)*self.px_per_sec)
        #     if x > w:
        #         break
        #     if x < 0:
        #         continue
        #     p.setPen(pen_bar if (b % beats_per_bar) == 0 else pen_beat)
        #     p.drawLine(x, y_start, x, y_end)