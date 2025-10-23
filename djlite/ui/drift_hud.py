"""HUD widget dla wyświetlania informacji o drift i phase offset."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPalette, QColor
from typing import Optional

class DriftHUD(QWidget):
    """Widget HUD wyświetlający informacje o drift i phase offset."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drift_detector = None
        self.click_test = None
        
        self.setup_ui()
        self.setup_timer()
        
        # Kolory dla różnych stanów
        self.colors = {
            'good': QColor(0, 255, 0),      # Zielony
            'warning': QColor(255, 255, 0),  # Żółty
            'error': QColor(255, 0, 0),      # Czerwony
            'inactive': QColor(128, 128, 128) # Szary
        }
    
    def setup_ui(self):
        """Konfiguruje interfejs użytkownika."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # Grupa Drift Detector
        drift_group = QGroupBox("Drift Detector")
        drift_layout = QVBoxLayout(drift_group)
        
        # Offset display
        offset_layout = QHBoxLayout()
        offset_layout.addWidget(QLabel("Offset:"))
        self.offset_label = QLabel("0.000 beats")
        self.offset_label.setFont(QFont("Consolas", 12, QFont.Bold))
        self.offset_label.setAlignment(Qt.AlignRight)
        offset_layout.addWidget(self.offset_label)
        drift_layout.addLayout(offset_layout)
        
        # Drift display
        drift_layout_h = QHBoxLayout()
        drift_layout_h.addWidget(QLabel("Drift:"))
        self.drift_label = QLabel("0.00 beats/min")
        self.drift_label.setFont(QFont("Consolas", 12, QFont.Bold))
        self.drift_label.setAlignment(Qt.AlignRight)
        drift_layout_h.addWidget(self.drift_label)
        drift_layout.addLayout(drift_layout_h)
        
        # Status info
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Samples:"))
        self.samples_label = QLabel("0")
        self.samples_label.setFont(QFont("Consolas", 10))
        status_layout.addWidget(self.samples_label)
        
        status_layout.addWidget(QLabel("Quality:"))
        self.quality_label = QLabel("Poor")
        self.quality_label.setFont(QFont("Consolas", 10))
        status_layout.addWidget(self.quality_label)
        drift_layout.addLayout(status_layout)
        
        # Reset button
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self.reset_drift)
        drift_layout.addWidget(self.reset_button)
        
        layout.addWidget(drift_group)
        
        # Grupa ClickTest
        click_group = QGroupBox("ClickTest")
        click_layout = QVBoxLayout(click_group)
        
        # Enable/disable buttons
        button_layout = QHBoxLayout()
        self.deck_a_button = QPushButton("Deck A")
        self.deck_a_button.setCheckable(True)
        self.deck_a_button.clicked.connect(lambda: self.toggle_deck_click('A'))
        button_layout.addWidget(self.deck_a_button)
        
        self.deck_b_button = QPushButton("Deck B")
        self.deck_b_button.setCheckable(True)
        self.deck_b_button.clicked.connect(lambda: self.toggle_deck_click('B'))
        button_layout.addWidget(self.deck_b_button)
        click_layout.addLayout(button_layout)
        
        # Click timing info
        timing_layout = QVBoxLayout()
        
        time_diff_layout = QHBoxLayout()
        time_diff_layout.addWidget(QLabel("Time diff:"))
        self.time_diff_label = QLabel("0.0 ms")
        self.time_diff_label.setFont(QFont("Consolas", 10))
        self.time_diff_label.setAlignment(Qt.AlignRight)
        time_diff_layout.addWidget(self.time_diff_label)
        timing_layout.addLayout(time_diff_layout)
        
        beat_diff_layout = QHBoxLayout()
        beat_diff_layout.addWidget(QLabel("Beat diff:"))
        self.beat_diff_label = QLabel("0.000 beats")
        self.beat_diff_label.setFont(QFont("Consolas", 10))
        self.beat_diff_label.setAlignment(Qt.AlignRight)
        beat_diff_layout.addWidget(self.beat_diff_label)
        timing_layout.addLayout(beat_diff_layout)
        
        click_layout.addLayout(timing_layout)
        
        layout.addWidget(click_group)
        
        # Grupa Grid Offset
        grid_group = QGroupBox("Grid Offset")
        grid_layout = QVBoxLayout(grid_group)
        
        # Deck selection
        deck_select_layout = QHBoxLayout()
        deck_select_layout.addWidget(QLabel("Deck:"))
        self.grid_deck_a_button = QPushButton("A")
        self.grid_deck_a_button.setCheckable(True)
        self.grid_deck_a_button.setChecked(True)
        self.grid_deck_a_button.clicked.connect(lambda: self.select_grid_deck('A'))
        deck_select_layout.addWidget(self.grid_deck_a_button)
        
        self.grid_deck_b_button = QPushButton("B")
        self.grid_deck_b_button.setCheckable(True)
        self.grid_deck_b_button.clicked.connect(lambda: self.select_grid_deck('B'))
        deck_select_layout.addWidget(self.grid_deck_b_button)
        grid_layout.addLayout(deck_select_layout)
        
        # Current offset display
        offset_display_layout = QHBoxLayout()
        offset_display_layout.addWidget(QLabel("Offset:"))
        self.grid_offset_label = QLabel("0.000 beats")
        self.grid_offset_label.setFont(QFont("Consolas", 12, QFont.Bold))
        self.grid_offset_label.setAlignment(Qt.AlignRight)
        offset_display_layout.addWidget(self.grid_offset_label)
        grid_layout.addLayout(offset_display_layout)
        
        # Adjustment buttons
        adjust_layout = QHBoxLayout()
        
        self.offset_minus_button = QPushButton("-0.1")
        self.offset_minus_button.clicked.connect(lambda: self.adjust_grid_offset(-0.1))
        adjust_layout.addWidget(self.offset_minus_button)
        
        self.offset_fine_minus_button = QPushButton("-0.01")
        self.offset_fine_minus_button.clicked.connect(lambda: self.adjust_grid_offset(-0.01))
        adjust_layout.addWidget(self.offset_fine_minus_button)
        
        self.offset_reset_button = QPushButton("Reset")
        self.offset_reset_button.clicked.connect(lambda: self.set_grid_offset(0.0))
        adjust_layout.addWidget(self.offset_reset_button)
        
        self.offset_fine_plus_button = QPushButton("+0.01")
        self.offset_fine_plus_button.clicked.connect(lambda: self.adjust_grid_offset(0.01))
        adjust_layout.addWidget(self.offset_fine_plus_button)
        
        self.offset_plus_button = QPushButton("+0.1")
        self.offset_plus_button.clicked.connect(lambda: self.adjust_grid_offset(0.1))
        adjust_layout.addWidget(self.offset_plus_button)
        
        grid_layout.addLayout(adjust_layout)
        
        layout.addWidget(grid_group)
        
        # Stretch na końcu
        layout.addStretch()
        
        # Inicjalizuj wybrany deck
        self.selected_deck = 'A'
        self.deck_references = {'A': None, 'B': None}
    
    def setup_timer(self):
        """Konfiguruje timer do aktualizacji HUD."""
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_display)
        self.update_timer.start(100)  # Aktualizuj co 100ms
    
    def set_drift_detector(self, drift_detector):
        """Ustawia referencję do detektora dryfu."""
        self.drift_detector = drift_detector
    
    def set_click_test(self, click_test):
        """Ustawia referencję do ClickTest."""
        self.click_test = click_test
        if click_test:
            # Ustaw callback dla timing data
            click_test.timing_callback = self.update_click_timing
    
    def update_display(self):
        """Aktualizuje wyświetlane informacje."""
        self.update_drift_display()
        self.update_click_display()
        self.update_grid_offset_display()
    
    def update_drift_display(self):
        """Aktualizuje wyświetlanie informacji o drift."""
        if not self.drift_detector:
            self.offset_label.setText("N/A")
            self.drift_label.setText("N/A")
            self.samples_label.setText("0")
            self.quality_label.setText("N/A")
            return
        
        hud_data = self.drift_detector.get_hud_data()
        
        # Aktualizuj tekst
        self.offset_label.setText(f"{hud_data['offset_str']} beats")
        self.drift_label.setText(f"{hud_data['drift_str']} beats/min")
        self.samples_label.setText(str(hud_data['sample_count']))
        self.quality_label.setText(hud_data['quality'].title())
        
        # Aktualizuj kolory na podstawie wartości
        offset_color = self.get_offset_color(abs(hud_data['offset_beats']))
        drift_color = self.get_drift_color(abs(hud_data['drift_beats_per_min']))
        quality_color = self.get_quality_color(hud_data['quality'])
        
        self.offset_label.setStyleSheet(f"color: {offset_color.name()};")
        self.drift_label.setStyleSheet(f"color: {drift_color.name()};")
        self.quality_label.setStyleSheet(f"color: {quality_color.name()};")
    
    def update_click_display(self):
        """Aktualizuje wyświetlanie informacji o ClickTest."""
        if not self.click_test:
            self.deck_a_button.setEnabled(False)
            self.deck_b_button.setEnabled(False)
            return
        
        status = self.click_test.get_status()
        
        # Aktualizuj stan przycisków
        self.deck_a_button.setEnabled(True)
        self.deck_b_button.setEnabled(True)
        self.deck_a_button.setChecked(status['deck_a_enabled'])
        self.deck_b_button.setChecked(status['deck_b_enabled'])
        
        # Aktualizuj style przycisków
        self.deck_a_button.setStyleSheet(
            "background-color: lightgreen;" if status['deck_a_enabled'] else ""
        )
        self.deck_b_button.setStyleSheet(
            "background-color: lightgreen;" if status['deck_b_enabled'] else ""
        )
    
    def update_click_timing(self, timing_data: dict):
        """Callback do aktualizacji informacji o timing kliknięć."""
        time_diff = timing_data.get('time_diff_ms', 0.0)
        beat_diff = timing_data.get('beat_diff', 0.0)
        
        self.time_diff_label.setText(f"{time_diff:+.1f} ms")
        self.beat_diff_label.setText(f"{beat_diff:+.3f} beats")
        
        # Koloruj na podstawie różnicy
        time_color = self.get_timing_color(abs(time_diff))
        beat_color = self.get_timing_color(abs(beat_diff) * 100)  # Skaluj beat diff
        
        self.time_diff_label.setStyleSheet(f"color: {time_color.name()};")
        self.beat_diff_label.setStyleSheet(f"color: {beat_color.name()};")
    
    def get_offset_color(self, abs_offset: float) -> QColor:
        """Zwraca kolor dla wartości offset."""
        if abs_offset < 0.01:
            return self.colors['good']
        elif abs_offset < 0.05:
            return self.colors['warning']
        else:
            return self.colors['error']
    
    def get_drift_color(self, abs_drift: float) -> QColor:
        """Zwraca kolor dla wartości drift."""
        if abs_drift < 0.1:
            return self.colors['good']
        elif abs_drift < 0.5:
            return self.colors['warning']
        else:
            return self.colors['error']
    
    def get_quality_color(self, quality: str) -> QColor:
        """Zwraca kolor dla jakości pomiaru."""
        if quality == "good":
            return self.colors['good']
        elif quality == "fair":
            return self.colors['warning']
        else:
            return self.colors['error']
    
    def set_deck_references(self, deck_a, deck_b):
        """Ustawia referencje do decków dla Grid Offset."""
        self.deck_references['A'] = deck_a
        self.deck_references['B'] = deck_b
    
    def select_grid_deck(self, deck_id: str):
        """Wybiera deck do kontroli Grid Offset."""
        self.selected_deck = deck_id
        
        # Aktualizuj stan przycisków
        self.grid_deck_a_button.setChecked(deck_id == 'A')
        self.grid_deck_b_button.setChecked(deck_id == 'B')
        
        # Natychmiast aktualizuj wyświetlanie
        self.update_grid_offset_display()
        
        print(f"🎵 Grid Offset: Selected deck {deck_id}")
    
    def get_selected_deck(self):
        """Zwraca aktualnie wybrany deck."""
        return self.deck_references.get(self.selected_deck)
    
    def update_grid_offset_display(self):
        """Aktualizuje wyświetlanie Grid Offset."""
        deck = self.get_selected_deck()
        if deck and hasattr(deck, 'get_grid_offset'):
            offset = deck.get_grid_offset()
            self.grid_offset_label.setText(f"{offset:+.3f} beats")
            
            # Koloruj na podstawie wartości offset
            color = self.get_offset_color(abs(offset))
            self.grid_offset_label.setStyleSheet(f"color: {color.name()};")
        else:
            self.grid_offset_label.setText("N/A")
            self.grid_offset_label.setStyleSheet("color: gray;")
    
    def adjust_grid_offset(self, delta: float):
        """Dostosowuje Grid Offset o podaną wartość."""
        deck = self.get_selected_deck()
        if deck and hasattr(deck, 'get_grid_offset') and hasattr(deck, 'set_grid_offset'):
            current_offset = deck.get_grid_offset()
            new_offset = current_offset + delta
            deck.set_grid_offset(new_offset)
            
            # Natychmiast aktualizuj wyświetlanie
            self.update_grid_offset_display()
            
            print(f"🎵 Grid Offset: Deck {self.selected_deck} adjusted by {delta:+.3f} beats (now {new_offset:+.3f})")
        else:
            print(f"⚠️ Grid Offset: Cannot adjust - deck {self.selected_deck} not available")
    
    def set_grid_offset(self, offset: float):
        """Ustawia Grid Offset na konkretną wartość."""
        deck = self.get_selected_deck()
        if deck and hasattr(deck, 'set_grid_offset'):
            deck.set_grid_offset(offset)
            
            # Natychmiast aktualizuj wyświetlanie
            self.update_grid_offset_display()
            
            print(f"🎵 Grid Offset: Deck {self.selected_deck} set to {offset:+.3f} beats")
        else:
            print(f"⚠️ Grid Offset: Cannot set - deck {self.selected_deck} not available")
    
    def get_timing_color(self, abs_value: float) -> QColor:
        """Zwraca kolor dla wartości timing."""
        if abs_value < 5.0:  # < 5ms lub < 0.05 beats
            return self.colors['good']
        elif abs_value < 20.0:  # < 20ms lub < 0.2 beats
            return self.colors['warning']
        else:
            return self.colors['error']
    
    def toggle_deck_click(self, deck_id: str):
        """Przełącza stan ClickTest dla określonego decka."""
        if not self.click_test:
            return
        
        if deck_id == 'A':
            enabled = self.deck_a_button.isChecked()
        else:
            enabled = self.deck_b_button.isChecked()
        
        self.click_test.enable_deck(deck_id, enabled)
    
    def reset_drift(self):
        """Resetuje detektor dryfu."""
        if self.drift_detector:
            self.drift_detector.reset()
    
    def closeEvent(self, event):
        """Cleanup przy zamykaniu."""
        if hasattr(self, 'update_timer'):
            self.update_timer.stop()
        super().closeEvent(event)