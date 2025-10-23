"""Główne okno DJ Lite - przezroczyste, zawsze na wierzchu, nakładka na pulpit."""

import sys
import os
from typing import Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QSlider, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QProgressBar, QGroupBox, QSplitter, QDial,
    QTreeWidget, QTreeWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize, QEvent, QUrl, QMimeData
from PySide6.QtGui import QFont, QPalette, QColor, QPainter, QBrush, QTransform, QPixmap, QKeySequence, QShortcut, QDragEnterEvent, QDragMoveEvent, QDropEvent, QDrag

# Import audio components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio.mixer import DJMixer
from audio.telemetry_diagnostics import TelemetryDiagnostics
from audio.click_test import ClickTest
from audio.drift_detector import DriftDetector
from utils.file_browser import MusicLibrary, QuickBrowser
from .batch_key_bpm_window import BatchKeyBpmWindow
from .waveform_mini import WaveformMini
from .central_grid_view import CentralGridView
from .central_wave import CentralWaveView
from .vinyl_widget import VinylWidget
from .drift_hud import DriftHUD
from audio.beat_grid import BeatClock, BeatGrid
from audio.waveform_cache import WaveformCache
# Spectrum analyzer import removed
# Spectrum worker import removed


class TransparentWidget(QWidget):
    """Widget z półprzezroczystym tłem."""
    
    def __init__(self, opacity: float = 0.4):
        super().__init__()
        self.opacity = opacity
        self.setAutoFillBackground(True)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Półprzezroczyste czarne tło
        brush = QBrush(QColor(0, 0, 0, int(255 * self.opacity)))
        painter.fillRect(self.rect(), brush)
        
        # Important: End the painter
        painter.end()
        
        super().paintEvent(event)


class VUMeter(QWidget):
    """Prosty VU meter do wyświetlania poziomu audio."""
    
    def __init__(self, orientation=Qt.Vertical):
        super().__init__()
        self.orientation = orientation
        self.level = 0.0
        self.peak_level = 0.0
        self.setMinimumSize(8, 60)
        self.setMaximumSize(12, 80)
    
    def set_level(self, level: float):
        """Ustawia poziom (0.0 - 1.0)."""
        self.level = max(0.0, min(1.0, level))
        self.peak_level = max(self.peak_level * 0.95, self.level)  # Peak hold
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = self.rect()
        
        # Tło
        painter.fillRect(rect, QColor(20, 20, 20))
        
        if self.orientation == Qt.Vertical:
            # Pionowy VU meter
            level_height = int(rect.height() * self.level)
            level_rect = rect.adjusted(2, rect.height() - level_height, -2, -2)
            
            # Kolor zależny od poziomu
            if self.level < 0.7:
                color = QColor(0, 255, 0)  # Zielony
            elif self.level < 0.9:
                color = QColor(255, 255, 0)  # Żółty
            else:
                color = QColor(255, 0, 0)  # Czerwony
            
            painter.fillRect(level_rect, color)
            
            # Peak indicator
            if self.peak_level > 0.01:
                peak_y = rect.height() - int(rect.height() * self.peak_level)
                painter.fillRect(2, peak_y, rect.width() - 4, 2, QColor(255, 255, 255))
        
        # Important: End the painter
        painter.end()


class DeckWidget(TransparentWidget):
    """Widget pojedynczego decka z kontrolkami."""
    
    track_loaded = Signal(str)  # file_path
    deck_clicked = Signal(str)  # deck_name
    
    def __init__(self, deck_name: str, mixer: DJMixer):
        super().__init__(opacity=0.7)
        self.deck_name = deck_name
        self.deck_id = deck_name.lower()  # Dodaj deck_id dla OptimizedSpectrumAnalyzer
        self.mixer = mixer
        self.deck = mixer.get_deck(deck_name.lower())
        
        # Ustaw stałą szerokość i style karty decku
        self.setFixedWidth(280)
        self.setStyleSheet("""
            DeckWidget {
                background-color: rgba(20, 20, 20, 180);
                border: 1px solid rgba(255, 255, 255, 25);
                border-radius: 12px;
                margin: 6px;
            }
        """)
        
        self.setup_ui()
        self.setup_connections()
        
        # Włącz obsługę drag-and-drop
        self.setAcceptDrops(True)
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(6, 6, 6, 6)  # Delikatny padding wewnątrz
        
        # Nagłówek decka
        header = QLabel(f"DECK {self.deck_name}")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 5px;
                background-color: rgba(50, 50, 50, 150);
                border-radius: 3px;
            }
        """)
        layout.addWidget(header)
        
        # Informacje o utworze
        self.track_info = QLabel("Brak utworu")
        self.track_info.setAlignment(Qt.AlignCenter)
        self.track_info.setStyleSheet("color: white; font-size: 11px;")
        layout.addWidget(self.track_info)
        
        # Vinyl jog wheel
        vinyl_container = QHBoxLayout()
        vinyl_container.addStretch()
        self.vinyl_widget = VinylWidget(deck=self.deck)
        vinyl_container.addWidget(self.vinyl_widget)
        vinyl_container.addStretch()
        vinyl_widget_container = QWidget()
        vinyl_widget_container.setLayout(vinyl_container)
        layout.addWidget(vinyl_widget_container)
        
        # Mini waveform overview
        self.waveform_mini = WaveformMini()
        self.waveform_mini.waveformClicked.connect(self.on_waveform_seek)
        # Ograniczenie szerokości waveform do 96% z marginesem
        self.waveform_mini.setStyleSheet("""
            WaveformMini {
                margin: 0px 6px;
                max-width: 96%;
            }
        """)
        layout.addWidget(self.waveform_mini)
        
        # Progress bar pozycji (backup)
        self.position_bar = QProgressBar()
        self.position_bar.setMaximum(1000)
        self.position_bar.setTextVisible(False)
        self.position_bar.setMaximumHeight(8)
        self.position_bar.hide()  # Ukryj, używamy waveform_mini
        layout.addWidget(self.position_bar)
        
        # Kontrolki odtwarzania
        controls_layout = QHBoxLayout()
        
        self.play_btn = QPushButton("▶")
        self.play_btn.setMaximumSize(40, 30)
        self.pause_btn = QPushButton("⏸")
        self.pause_btn.setMaximumSize(40, 30)
        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setMaximumSize(40, 30)
        self.load_btn = QPushButton("📁")
        self.load_btn.setMaximumSize(40, 30)
        
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.pause_btn)
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addWidget(self.load_btn)
        controls_layout.addStretch()
        
        layout.addLayout(controls_layout)
        
        # BPM Control
        bpm_layout = QVBoxLayout()
        
        # BPM detected label
        self.bpm_detected_label = QLabel("BPM (detected): —")
        self.bpm_detected_label.setAlignment(Qt.AlignCenter)
        self.bpm_detected_label.setStyleSheet("color: white; font-size: 11px;")
        bpm_layout.addWidget(self.bpm_detected_label)
        
        # BPM Dial
        self.bpm_dial = QDial()
        self.bpm_dial.setRange(600, 2000)  # 60.0 - 200.0 BPM (x10 for precision)
        self.bpm_dial.setValue(1200)  # 120.0 BPM
        self.bpm_dial.setMaximumSize(80, 80)
        self.bmp_dial_container = QHBoxLayout()
        self.bmp_dial_container.addStretch()
        self.bmp_dial_container.addWidget(self.bpm_dial)
        self.bmp_dial_container.addStretch()
        bmp_dial_widget = QWidget()
        bmp_dial_widget.setLayout(self.bmp_dial_container)
        bpm_layout.addWidget(bmp_dial_widget)
        
        # BPM target and rate labels
        self.bpm_target_label = QLabel("Target: —")
        self.bpm_target_label.setAlignment(Qt.AlignCenter)
        self.bpm_target_label.setStyleSheet("color: white; font-size: 10px;")
        bpm_layout.addWidget(self.bpm_target_label)
        
        # Rzeczywiste BPM odtwarzania
        self.bpm_playing_label = QLabel("Playing: —")
        self.bpm_playing_label.setAlignment(Qt.AlignCenter)
        self.bpm_playing_label.setStyleSheet("color: #00ff00; font-size: 10px; font-weight: bold;")
        bpm_layout.addWidget(self.bpm_playing_label)
        
        self.rate_label = QLabel("1.00x")
        self.rate_label.setAlignment(Qt.AlignCenter)
        self.rate_label.setStyleSheet("color: white; font-size: 10px; font-weight: bold;")
        bpm_layout.addWidget(self.rate_label)
        
        # Key detection display
        self.key_label = QLabel("🎵 KEY: —")
        self.key_label.setAlignment(Qt.AlignCenter)
        self.key_label.setStyleSheet("color: white; font-size: 10px; font-weight: bold;")
        bpm_layout.addWidget(self.key_label)
        
        layout.addLayout(bpm_layout)
        
        # Volume i VU Meter w jednym rzędzie
        volume_vu_layout = QHBoxLayout()
        
        # Up-fader (Volume)
        volume_layout = QVBoxLayout()
        volume_layout.addWidget(QLabel("VOLUME"))
        
        self.gain_slider = QSlider(Qt.Vertical)
        self.gain_slider.setRange(0, 100)
        self.gain_slider.setValue(50)  # 50 = 0dB (unity gain)
        self.gain_slider.setMaximumHeight(120)
        volume_layout.addWidget(self.gain_slider)
        
        self.gain_label = QLabel("0.0dB")
        self.gain_label.setAlignment(Qt.AlignCenter)
        volume_layout.addWidget(self.gain_label)
        
        volume_vu_layout.addLayout(volume_layout)
        
        # VU Meter z boku
        vu_layout = QVBoxLayout()
        vu_label = QLabel("LVL")
        vu_label.setAlignment(Qt.AlignCenter)
        vu_label.setStyleSheet("color: white; font-size: 9px;")
        vu_layout.addWidget(vu_label)
        
        self.vu_meter = VUMeter()
        vu_layout.addWidget(self.vu_meter)
        
        volume_vu_layout.addLayout(vu_layout)
        
        layout.addLayout(volume_vu_layout)
        
        # Tempo Control
        tempo_layout = QVBoxLayout()
        
        # Tempo label
        tempo_header_layout = QHBoxLayout()
        tempo_label = QLabel("Tempo ±%")
        tempo_label.setAlignment(Qt.AlignLeft)
        tempo_label.setStyleSheet("color: white; font-size: 10px; font-weight: bold;")
        tempo_header_layout.addWidget(tempo_label)
        
        # Reset tempo button
        self.tempo_reset_btn = QPushButton("1.0")
        self.tempo_reset_btn.setMaximumSize(30, 20)
        self.tempo_reset_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(100, 100, 100, 200);
                font-size: 9px;
                padding: 2px;
            }
        """)
        tempo_header_layout.addWidget(self.tempo_reset_btn)
        tempo_layout.addLayout(tempo_header_layout)
        
        # Range preset buttons
        range_layout = QHBoxLayout()
        self.range_8_btn = QPushButton("±8%")
        self.range_16_btn = QPushButton("±16%")
        self.range_50_btn = QPushButton("±50%")
        
        # Style range buttons
        range_btn_style = """
            QPushButton {
                background-color: rgba(80, 80, 80, 200);
                font-size: 8px;
                padding: 1px;
                min-width: 25px;
                max-height: 18px;
            }
            QPushButton:checked {
                background-color: rgba(0, 150, 255, 200);
            }
        """
        
        for btn in [self.range_8_btn, self.range_16_btn, self.range_50_btn]:
            btn.setCheckable(True)
            btn.setStyleSheet(range_btn_style)
            range_layout.addWidget(btn)
        
        # Domyślnie ±16% aktywny
        self.range_16_btn.setChecked(True)
        self.current_range = 16
        
        tempo_layout.addLayout(range_layout)
        
        # Tempo fader and value in horizontal layout
        tempo_control_layout = QHBoxLayout()
        
        # Tempo fader (vertical)
        self.tempo_slider = QSlider(Qt.Vertical)
        self.tempo_slider.setRange(840, 1160)  # ±16% domyślnie (x1000 for precision)
        self.tempo_slider.setValue(1000)  # 1.0 = unity
        self.tempo_slider.setMaximumHeight(80)
        self.tempo_slider.setMaximumWidth(20)
        self.tempo_slider.setStyleSheet("""
            QSlider::groove:vertical {
                border: 1px solid #666;
                width: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #333, stop:1 #555);
                margin: 0 2px;
                border-radius: 2px;
            }
            QSlider::handle:vertical {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #fff, stop:1 #ccc);
                border: 1px solid #444;
                height: 12px;
                margin: 0 -3px;
                border-radius: 2px;
            }
        """)
        tempo_control_layout.addWidget(self.tempo_slider)
        
        # Tempo value and key lock
        tempo_info_layout = QVBoxLayout()
        
        self.tempo_value_label = QLabel("+0.0%")
        self.tempo_value_label.setAlignment(Qt.AlignCenter)
        self.tempo_value_label.setStyleSheet("color: white; font-size: 9px; font-weight: bold;")
        tempo_info_layout.addWidget(self.tempo_value_label)
        
        # Key Lock button
        self.key_lock_btn = QPushButton("KEY\nLOCK")
        self.key_lock_btn.setCheckable(True)
        self.key_lock_btn.setMaximumSize(40, 30)
        self.key_lock_btn.setStyleSheet("""
            QPushButton {
                font-size: 8px;
                padding: 2px;
            }
            QPushButton:checked {
                background-color: rgba(255, 165, 0, 200);
            }
        """)
        tempo_info_layout.addWidget(self.key_lock_btn)
        
        tempo_control_layout.addLayout(tempo_info_layout)
        tempo_layout.addLayout(tempo_control_layout)
        
        layout.addLayout(tempo_layout)
        
        # Nudge/Pitch-bend controls
        nudge_layout = QVBoxLayout()
        
        # Nudge header with range selection
        nudge_header_layout = QHBoxLayout()
        nudge_label = QLabel("Nudge")
        nudge_label.setAlignment(Qt.AlignLeft)
        nudge_label.setStyleSheet("color: white; font-size: 10px; font-weight: bold;")
        nudge_header_layout.addWidget(nudge_label)
        
        # Nudge range buttons
        self.nudge_range_2_btn = QPushButton("±2%")
        self.nudge_range_4_btn = QPushButton("±4%")
        self.nudge_range_6_btn = QPushButton("±6%")
        
        nudge_range_style = """
            QPushButton {
                background-color: rgba(80, 80, 80, 200);
                font-size: 8px;
                padding: 1px;
                min-width: 20px;
                max-height: 16px;
            }
            QPushButton:checked {
                background-color: rgba(255, 165, 0, 200);
            }
        """
        
        for btn in [self.nudge_range_2_btn, self.nudge_range_4_btn, self.nudge_range_6_btn]:
            btn.setCheckable(True)
            btn.setStyleSheet(nudge_range_style)
            nudge_header_layout.addWidget(btn)
        
        # Domyślnie ±4% aktywny
        self.nudge_range_4_btn.setChecked(True)
        self.current_nudge_range = 4
        
        nudge_layout.addLayout(nudge_header_layout)
        
        # Nudge buttons
        nudge_buttons_layout = QHBoxLayout()
        
        self.nudge_minus_btn = QPushButton("⟵ Nudge")
        self.nudge_plus_btn = QPushButton("Nudge ⟶")
        
        nudge_btn_style = """
            QPushButton {
                background-color: rgba(100, 100, 100, 200);
                font-size: 9px;
                padding: 4px;
                min-height: 25px;
            }
            QPushButton:pressed {
                background-color: rgba(255, 165, 0, 200);
            }
        """
        
        self.nudge_minus_btn.setStyleSheet(nudge_btn_style)
        self.nudge_plus_btn.setStyleSheet(nudge_btn_style)
        
        nudge_buttons_layout.addWidget(self.nudge_minus_btn)
        nudge_buttons_layout.addWidget(self.nudge_plus_btn)
        
        nudge_layout.addLayout(nudge_buttons_layout)
        
        # Nudge status display
        self.nudge_status_label = QLabel("Nudge: 0%")
        self.nudge_status_label.setAlignment(Qt.AlignCenter)
        self.nudge_status_label.setStyleSheet("color: white; font-size: 9px;")
        nudge_layout.addWidget(self.nudge_status_label)
        
        layout.addLayout(nudge_layout)
        
        # Cue and Sync buttons
        buttons_layout = QHBoxLayout()
        
        self.cue_btn = QPushButton("CUE")
        self.cue_btn.setCheckable(True)
        self.cue_btn.setMaximumHeight(30)
        buttons_layout.addWidget(self.cue_btn)
        
        self.sync_btn = QPushButton("SYNC")
        self.sync_btn.setMaximumHeight(30)
        buttons_layout.addWidget(self.sync_btn)
        
        # Range limit icon (initially hidden)
        self.range_limit_icon = QPushButton("⚠︎")
        self.range_limit_icon.setMaximumSize(25, 25)
        self.range_limit_icon.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 165, 0, 200);
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
            }
        """)
        self.range_limit_icon.setToolTip("SYNC hit pitch range limit")
        self.range_limit_icon.hide()
        buttons_layout.addWidget(self.range_limit_icon)
        
        layout.addLayout(buttons_layout)
        
        # Stylowanie
        self.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #999999;
                height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #B1B1B1, stop:1 #c4c4c4);
                margin: 2px 0;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #b4b4b4, stop:1 #8f8f8f);
                border: 1px solid #5c5c5c;
                width: 18px;
                margin: -2px 0;
                border-radius: 3px;
            }
            QSlider::groove:vertical {
                border: 1px solid #999999;
                width: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #B1B1B1, stop:1 #c4c4c4);
                margin: 0 2px;
                border-radius: 3px;
            }
            QSlider::handle:vertical {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #b4b4b4, stop:1 #8f8f8f);
                border: 1px solid #5c5c5c;
                height: 18px;
                margin: 0 -2px;
                border-radius: 3px;
            }
            QPushButton {
                background-color: rgba(70, 70, 70, 200);
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: rgba(90, 90, 90, 200);
            }
            QPushButton:pressed {
                background-color: rgba(50, 50, 50, 200);
            }
            QPushButton:checked {
                background-color: rgba(0, 150, 0, 200);
            }
            QLabel {
                color: white;
                font-size: 10px;
            }
            QGroupBox {
                color: white;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 3px;
                margin-top: 10px;
                padding-top: 5px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
    
    def setup_connections(self):
        """Łączy sygnały z slotami."""
        self.play_btn.clicked.connect(self.play_track)
        self.pause_btn.clicked.connect(self.pause_track)
        self.stop_btn.clicked.connect(self.stop_track)
        self.load_btn.clicked.connect(self.load_track)
        
        self.bpm_dial.valueChanged.connect(self.update_bpm_target)
        self.gain_slider.valueChanged.connect(self.update_gain)
        
        # EQ connections removed
        
        self.cue_btn.toggled.connect(self.toggle_cue)
        self.sync_btn.clicked.connect(self.sync_deck)
        
        # Tempo controls
        self.tempo_slider.valueChanged.connect(self.update_tempo)
        self.tempo_reset_btn.clicked.connect(self.reset_tempo)
        self.key_lock_btn.toggled.connect(self.toggle_key_lock)
        
        # Range preset buttons
        self.range_8_btn.toggled.connect(lambda checked: self.set_tempo_range(8) if checked else None)
        self.range_16_btn.toggled.connect(lambda checked: self.set_tempo_range(16) if checked else None)
        self.range_50_btn.toggled.connect(lambda checked: self.set_tempo_range(50) if checked else None)
        
        # Połącz range buttons z pitch range dla SYNC
        self.range_8_btn.toggled.connect(lambda checked: self.deck.set_pitch_range('±8') if checked else None)
        self.range_16_btn.toggled.connect(lambda checked: self.deck.set_pitch_range('±16') if checked else None)
        self.range_50_btn.toggled.connect(lambda checked: self.deck.set_pitch_range('±50') if checked else None)
        
        # Nudge controls
        self.nudge_range_2_btn.toggled.connect(lambda checked: self.set_nudge_range(2) if checked else None)
        self.nudge_range_4_btn.toggled.connect(lambda checked: self.set_nudge_range(4) if checked else None)
        self.nudge_range_6_btn.toggled.connect(lambda checked: self.set_nudge_range(6) if checked else None)
        
        # Nudge buttons - press/release events
        self.nudge_minus_btn.pressed.connect(self.start_nudge_minus)
        self.nudge_minus_btn.released.connect(self.stop_nudge)
        self.nudge_plus_btn.pressed.connect(self.start_nudge_plus)
        self.nudge_plus_btn.released.connect(self.stop_nudge)
        
        # Nudge buttons - click events for tap functionality
        self.nudge_minus_btn.clicked.connect(self.tap_nudge_minus)
        self.nudge_plus_btn.clicked.connect(self.tap_nudge_plus)
        
        # Połącz sygnały z deck
        self.deck.bpmReady.connect(self.on_bpm_detected)
        self.deck.analysisFailed.connect(self.on_bpm_analysis_failed)
        self.deck.keyReady.connect(self.on_key_detected)
        
        # Połącz vinyl widget z przyciskami odtwarzania
        self.play_btn.clicked.connect(self.vinyl_widget.start_rotation)
        self.pause_btn.clicked.connect(self.vinyl_widget.stop_rotation)
        self.stop_btn.clicked.connect(self.vinyl_widget.stop_rotation)
        
        # Sprawdź status time-stretch przy inicjalizacji
        self.update_key_lock_status()
        
        # Spectrum worker connections będą ustawione później w MainWindow
    
    def play_track(self):
        self.deck.play()
        
        # Auto-master functionality
        main_window = self.parent()
        if hasattr(main_window, 'master_deck') and hasattr(main_window, 'get_master_deck_object'):
            current_master = main_window.get_master_deck_object()
            # Jeśli master nie gra lub jest None → przejmij mastera automatycznie
            if current_master is None or not current_master.is_playing():
                main_window.set_master(self.deck_name)
                print(f"Auto-master: Deck {self.deck_name} became master (current master not playing)")
    
    def pause_track(self):
        self.deck.pause()
    
    def stop_track(self):
        self.deck.stop()
    
    def load_track(self):
        file_path = QuickBrowser.select_audio_file(self)
        if file_path:
            # UI kontrakt: reset BPM przy double-click
            self.deck.detected_bpm = 0.0
            self.set_bpm_label("…")
            self.set_bpm_knob_enabled(False)
            
            if self.deck.load_track(file_path):
                self.track_info.setText(self.deck.track_name)
                self.track_loaded.emit(file_path)
                # Deck automatycznie rozpocznie analizę BPM w tle
    

    def update_bpm_target(self, value):
        """Obsługuje zmianę gałki BPM - ustawia target BPM w decku."""
        # Sprawdź czy to nie jest programmatyczne ustawienie
        if hasattr(self, '_bpm_dial_updating') and self._bpm_dial_updating:
            return
            
        bpm_value = value / 10.0  # Konwersja z zakresu 600-2000 na 60.0-200.0
        self.deck.set_bpm_target(bpm_value)
        self.bpm_target_label.setText(f"Target: {bpm_value:.1f}")
    
    def set_bpm_label(self, text: str):
        """Ustawia tekst etykiety BPM."""
        self.bpm_detected_label.setText(f"BPM (detected): {text}")
    
    def set_bpm_knob_enabled(self, enabled: bool):
        """Włącza/wyłącza gałkę BPM."""
        self.bpm_dial.setEnabled(enabled)
    
    def on_bpm_result(self, bpm: float):
        """Callback dla wyniku analizy BPM."""
        if bpm is not None and bpm > 0:
            self.deck.detected_bpm = bpm
            
            # Sprawdź confidence z deck
            confidence = getattr(self.deck, 'bmp_confidence', None)
            
            if confidence is not None and confidence < 0.5:
                # Niska confidence - pokaż BPM z oznaczeniem niepewności
                self.set_bpm_label(f"{bpm:.1f}?")
            else:
                # Normalna confidence lub brak informacji o confidence
                self.set_bpm_label(f"{bpm:.1f}")
                
            self.set_bpm_knob_enabled(True)
            
            # Ustaw gałkę na detected BPM bez wywoływania set_bpm_target
            self._bpm_dial_updating = True
            self.bpm_dial.blockSignals(True)
            self.bpm_dial.setValue(int(bpm * 10))
            self.bpm_dial.blockSignals(False)
            self._bpm_dial_updating = False
        else:
            self.set_bpm_label("Failed")
            self.set_bpm_knob_enabled(False)
    
    def on_bpm_detected(self, bpm: float):
        """Obsługuje sygnał bpmReady z deck."""
        print(f"Deck {self.deck_name}: on_bpm_detected called with BPM: {bpm:.1f}")
        
        # Sprawdź confidence z deck
        confidence = getattr(self.deck, 'bmp_confidence', None)
        
        if confidence is not None and confidence < 0.5:
            # Niska confidence - pokaż BPM z oznaczeniem niepewności
            self.bpm_detected_label.setText(f"BPM (detected): {bpm:.1f}?")
        else:
            # Normalna confidence lub brak informacji o confidence
            self.bpm_detected_label.setText(f"BPM (detected): {bpm:.1f}")
        
        # Ustaw gałkę na detected BPM bez wywoływania set_bpm_target
        self._bpm_dial_updating = True
        self.bpm_dial.blockSignals(True)
        self.bpm_dial.setValue(int(bpm * 10))
        self.bpm_dial.blockSignals(False)
        self._bpm_dial_updating = False
        
        # Integracja z CentralGridView - powiadom o zmianie BPM
        # Jeśli to master deck, zaktualizuj BeatClock z zachowaniem fazy
        if hasattr(self.parent(), 'master_deck') and self.parent().master_deck == self.deck_name:
            if hasattr(self.parent(), 'central_grid') and self.parent().central_grid:
                # Oblicz efektywne BPM (detected_bpm * effective_ratio)
                effective_bpm = bpm * self.deck.effective_ratio()
                self.parent().central_grid.on_bpm_changed(self.deck_name, effective_bpm, preserve_phase=True)
            
            # Zsynchronizuj BeatClock
            if hasattr(self.parent(), 'sync_beat_clock_with_master'):
                self.parent().sync_beat_clock_with_master()
    
    def on_bpm_analysis_failed(self, error_msg: str):
        """Obsługuje sygnał analysisFailed z deck."""
        self.bpm_detected_label.setText("BPM (detected): Failed")
        print(f"Deck {self.deck_name}: BPM analysis failed: {error_msg}")
    
    def on_key_detected(self, key_data: dict):
        """Obsługuje sygnał keyReady z deck."""
        if key_data and 'display' in key_data:
            self.key_label.setText(f"🎵 {key_data['display']}")
        else:
            self.key_label.setText("🎵 KEY: —")
        print(f"Deck {self.deck_name}: Key detected: {key_data}")
    
    def update_bpm_display(self):
        """Aktualizuje wyświetlane informacje o BPM i rate."""
        if hasattr(self.deck, 'detected_bpm') and self.deck.detected_bpm:
            self.bpm_detected_label.setText(f"BPM (detected): {self.deck.detected_bpm:.1f}")
            
            # Oblicz rzeczywiste BPM odtwarzania
            effective_ratio = self.deck.effective_ratio() if hasattr(self.deck, 'effective_ratio') else 1.0
            playing_bpm = self.deck.detected_bpm * effective_ratio
            self.bpm_playing_label.setText(f"Playing: {playing_bpm:.1f}")
            
            # Aktualizuj target BPM jeśli jest ustawiony
            if hasattr(self.deck, 'bpm_target') and self.deck.bpm_target:
                self.bpm_target_label.setText(f"Target: {self.deck.bpm_target:.1f}")
            else:
                self.bpm_target_label.setText("Target: —")
        else:
            self.bpm_playing_label.setText("Playing: —")
            self.bpm_target_label.setText("Target: —")
        
        if hasattr(self.deck, 'rate_smooth'):
            self.rate_label.setText(f"{self.deck.rate_smooth:.2f}x")
        else:
            self.rate_label.setText("1.00x")
    
    def update_key_display(self):
        """Aktualizuje wyświetlanie klucza z uwzględnieniem pitch shift."""
        if hasattr(self.deck, 'get_key_info'):
            key_info = self.deck.get_key_info()
            if key_info and key_info['status'] == 'ok':
                self.key_label.setText(f"🎵 {key_info['display']}")
            else:
                self.key_label.setText("🎵 KEY: —")
        else:
            self.key_label.setText("🎵 KEY: —")
    
    def update_nudge_display(self):
        """Aktualizuje wyświetlanie statusu nudge."""
        nudge_percent = self.deck.get_nudge_percent()
        if abs(nudge_percent) < 0.1:
            self.nudge_status_label.setText("Nudge: 0%")
        else:
            sign = "+" if nudge_percent > 0 else ""
            self.nudge_status_label.setText(f"Nudge: {sign}{nudge_percent:.1f}%")
    
    def update_gain(self, value):
        # Konwersja z zakresu 0..100 na logarytmiczną skalę dB
        # 0 = -∞dB (cisza), 50 = 0dB (unity), 100 = +12dB (boost)
        if value == 0:
            gain = 0.0  # Cisza
            db_value = float('-inf')
        else:
            # Logarytmiczna skala: 0-50 = -24dB do 0dB, 50-100 = 0dB do +12dB
            if value <= 50:
                db_value = (value - 50) * 24.0 / 50.0  # -24dB do 0dB
            else:
                db_value = (value - 50) * 12.0 / 50.0  # 0dB do +12dB
            gain = 10.0 ** (db_value / 20.0)  # Konwersja dB na amplitudę
        
        self.mixer.set_deck_gain(self.deck_name.lower(), gain)
        
        # Aktualizacja etykiety z wartością dB
        if value == 0:
            self.gain_label.setText("-∞dB")
        else:
            self.gain_label.setText(f"{db_value:+.1f}dB")
    
    # EQ methods removed
    
    def toggle_cue(self, checked):
        self.mixer.set_cue(self.deck_name.lower(), checked)
    
    def sync_deck(self):
        """Synchronizuje ten deck do drugiego decka (master)."""
        if not hasattr(self, 'other_deck') or not self.other_deck:
            return
            
        try:
            ratio, limited = self.deck.sync_to_deck(self.other_deck)
            
            # Pokaż toast z wynikiem
            other_deck_name = "B" if self.deck_name == "A" else "A"  # Prosta logika
            message = f"Synced {self.deck_name} to {other_deck_name}: {((ratio-1)*100):+.1f}%"
            if limited:
                message += " (range limit)"
                self.show_range_limit_icon(True)
            else:
                self.show_range_limit_icon(False)
                
            self.show_toast(message)
            
        except Exception as e:
            self.show_toast(str(e))
    
    # Reset EQ method removed
    
    def set_other_deck(self, other_deck):
        """Ustawia referencję do drugiego decka dla funkcji SYNC."""
        self.other_deck = other_deck
    
    def update_display(self):
        """Aktualizuje wyświetlane informacje."""
        if self.deck.is_loaded():
            # Aktualizuj progress bar
            progress = int(self.deck.get_position_percent() * 1000)
            self.position_bar.setValue(progress)
            
            # Aktualizuj waveform playhead
            phase = self.deck.get_phase()
            self.waveform_mini.setPlayhead(phase)
        
        # Aktualizuj VU meter zawsze (nawet gdy deck nie jest loaded)
        peak_levels = self.mixer.get_peak_levels()
        deck_key = f'deck_{self.deck_name.lower()}_l'
        if deck_key in peak_levels:
            level = max(peak_levels[deck_key], peak_levels.get(f'deck_{self.deck_name.lower()}_r', 0))
            self.vu_meter.set_level(level)
        
        # Aktualizuj informacje o BPM i rate
        self.update_bpm_display()
        self.update_key_display()
    
    def mousePressEvent(self, event):
        """Obsługuje kliknięcie na deck - ustawia jako aktywny."""
        if event.button() == Qt.LeftButton:
            self.deck_clicked.emit(self.deck_name)
        super().mousePressEvent(event)
        self.update_nudge_display()
    
    def update_tempo(self, value):
        """Obsługuje zmianę tempo fader."""
        # Konwersja z zakresu slidera na tempo ratio
        tempo_ratio = value / 1000.0
        
        # Dead-zone snap do 1.00 gdy blisko (±0.001)
        if abs(tempo_ratio - 1.0) <= 0.001:
            tempo_ratio = 1.0
            self.tempo_slider.setValue(1000)  # Snap slider do środka
        
        # Aktualizuj label z procentową wartością
        percent = (tempo_ratio - 1.0) * 100
        if percent >= 0:
            self.tempo_value_label.setText(f"+{percent:.1f}%")
        else:
            self.tempo_value_label.setText(f"{percent:.1f}%")
        
        # Ustaw tempo w deck state i engine
        if hasattr(self.deck, 'deck_state'):
            self.deck.deck_state['tempoRatio'] = tempo_ratio
        
        # Wywołaj engine.setTempo jeśli istnieje
        if hasattr(self.deck, 'engine') and hasattr(self.deck.engine, 'setTempo'):
            self.deck.engine.setTempo(tempo_ratio)
        elif hasattr(self.deck, 'set_tempo'):
            self.deck.set_tempo(tempo_ratio)
            
        # Integracja z CentralGridView - powiadom o zmianie tempo
        if hasattr(self.parent(), 'central_grid') and self.parent().central_grid:
            self.parent().central_grid.on_tempo_changed(self.deck_name, tempo_ratio)
        
        # Jeśli to master deck, zsynchronizuj BeatClock
        if hasattr(self.parent(), 'master_deck') and self.parent().master_deck == self.deck_name:
            if hasattr(self.parent(), 'sync_beat_clock_with_master'):
                self.parent().sync_beat_clock_with_master()
    
    def reset_tempo(self):
        """Resetuje tempo do 1.0 (unity)."""
        self.tempo_slider.setValue(1000)  # Automatycznie wywoła update_tempo
    
    def set_tempo_range(self, range_percent):
        """Ustawia zakres tempo slidera (±8%, ±16%, ±50%)."""
        # Odznacz inne przyciski
        for btn in [self.range_8_btn, self.range_16_btn, self.range_50_btn]:
            if btn != self.sender():
                btn.setChecked(False)
        
        # Zapisz aktualną wartość tempo
        current_value = self.tempo_slider.value()
        current_ratio = current_value / 1000.0
        
        # Ustaw nowy zakres slidera
        self.current_range = range_percent
        min_ratio = 1.0 - (range_percent / 100.0)
        max_ratio = 1.0 + (range_percent / 100.0)
        
        # Konwertuj na wartości slidera (x1000)
        min_value = int(min_ratio * 1000)
        max_value = int(max_ratio * 1000)
        
        self.tempo_slider.setRange(min_value, max_value)
        
        # Zachowaj aktualną wartość tempo jeśli mieści się w nowym zakresie
        if min_ratio <= current_ratio <= max_ratio:
            self.tempo_slider.setValue(current_value)
        else:
            # Jeśli nie mieści się, ustaw na środek (1.0)
            self.tempo_slider.setValue(1000)
    
    def toggle_key_lock(self, checked):
        """Obsługuje toggle Key Lock."""
        # Sprawdź czy high-quality time-stretch jest dostępny
        status = self.deck.get_time_stretch_status()
        
        if not status['high_quality_available'] and checked:
            # Jeśli próbujemy włączyć Key Lock ale nie ma high-quality
            self.key_lock_btn.setChecked(False)
            return
        
        # Ustaw Key Lock w deck
        self.deck.set_key_lock(checked)
        
        # Aktualizuj UI status
        self.update_key_lock_status()
    
    def update_key_lock_status(self):
        """Aktualizuje status Key Lock button na podstawie dostępności time-stretch."""
        status = self.deck.get_time_stretch_status()
        
        if not status['high_quality_available']:
            # Brak high-quality time-stretch - disable Key Lock
            self.key_lock_btn.setEnabled(False)
            self.key_lock_btn.setToolTip("High-quality time-stretch unavailable; using playbackRate (no Key Lock)")
            self.key_lock_btn.setStyleSheet("""
                QPushButton {
                    background-color: #444;
                    color: #888;
                    border: 1px solid #666;
                    border-radius: 3px;
                    font-size: 8px;
                    font-weight: bold;
                }
            """)
        else:
            # High-quality dostępny - enable Key Lock
            self.key_lock_btn.setEnabled(True)
            self.key_lock_btn.setToolTip("Toggle Key Lock (preserve pitch when changing tempo)")
            # Przywróć oryginalny styl
            self.key_lock_btn.setStyleSheet("""
                QPushButton {
                    background-color: #333;
                    color: white;
                    border: 1px solid #666;
                    border-radius: 3px;
                    font-size: 8px;
                    font-weight: bold;
                }
                QPushButton:checked {
                    background-color: #0a84ff;
                    border-color: #0a84ff;
                }
                QPushButton:hover {
                    background-color: #555;
                }
            """)
    
    def on_waveform_seek(self, phase: float):
        """Obsługuje kliknięcie w waveform - seek do pozycji."""
        if self.deck.is_loaded():
            self.deck.seek_to_phase(phase)
    
    def set_nudge_range(self, range_percent: int):
        """Ustawia zakres nudge (±2%, ±4%, ±6%)."""
        # Odznacz inne przyciski
        for btn in [self.nudge_range_2_btn, self.nudge_range_4_btn, self.nudge_range_6_btn]:
            if btn != self.sender():
                btn.setChecked(False)
        
        self.current_nudge_range = range_percent
    
    def start_nudge_minus(self):
        """Rozpoczyna nudge w dół (zwalnianie)."""
        pct = -self.current_nudge_range / 100.0  # Konwersja na ułamek dziesiętny
        self.deck.start_nudge(pct)
        self._notify_nudge_change()
    
    def start_nudge_plus(self):
        """Rozpoczyna nudge w górę (przyspieszanie)."""
        pct = self.current_nudge_range / 100.0  # Konwersja na ułamek dziesiętny
        self.deck.start_nudge(pct)
        self._notify_nudge_change()
    
    def stop_nudge(self):
        """Zatrzymuje nudge."""
        self.deck.stop_nudge()
        self._notify_nudge_change()
        
    def _notify_nudge_change(self):
        """Powiadamia CentralGridView o zmianie nudge."""
        # Integracja z CentralGridView - powiadom o zmianie nudge
        if hasattr(self.parent(), 'central_grid') and self.parent().central_grid:
            nudge_ratio = self.deck.get_nudge_ratio() if hasattr(self.deck, 'get_nudge_ratio') else 1.0
            self.parent().central_grid.on_nudge_changed(self.deck_name, nudge_ratio)
        
        # Jeśli to master deck, zsynchronizuj BeatClock
        if hasattr(self.parent(), 'master_deck') and self.parent().master_deck == self.deck_name:
            if hasattr(self.parent(), 'sync_beat_clock_with_master'):
                self.parent().sync_beat_clock_with_master()
    
    def tap_nudge_minus(self):
        """Krótkie przesunięcie fazy w tył (10ms)."""
        self.deck.tap_nudge(-10)
    
    def tap_nudge_plus(self):
        """Krótkie przesunięcie fazy w przód (10ms)."""
        self.deck.tap_nudge(10)
    
    def show_toast(self, message: str):
        """Pokazuje toast notification."""
        # Znajdź główne okno
        main_window = self.window()
        if hasattr(main_window, 'show_toast'):
            main_window.show_toast(message)
        else:
            print(f"Toast: {message}")  # Fallback do konsoli
    
    def show_range_limit_icon(self, show: bool):
        """Pokazuje/ukrywa ikonę range limit."""
        if show:
            self.range_limit_icon.show()
            # Auto-hide po 3 sekundach
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, self.range_limit_icon.hide)
        else:
            self.range_limit_icon.hide()
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Obsługuje wejście przeciąganego pliku nad deck."""
        if event.mimeData().hasUrls():
            # Sprawdź czy to plik audio
            urls = event.mimeData().urls()
            if urls and len(urls) == 1:
                file_path = urls[0].toLocalFile()
                if self._is_audio_file(file_path):
                    event.acceptProposedAction()
                    # Wizualny feedback - podświetl deck
                    self.setStyleSheet(self.styleSheet() + """
                        DeckWidget {
                            border: 2px solid #00ff00;
                            background-color: rgba(0, 255, 0, 30);
                        }
                    """)
                    return
        event.ignore()
    
    def dragMoveEvent(self, event: QDragMoveEvent):
        """Obsługuje ruch przeciąganego pliku nad deck."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and len(urls) == 1:
                file_path = urls[0].toLocalFile()
                if self._is_audio_file(file_path):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dragLeaveEvent(self, event):
        """Obsługuje opuszczenie obszaru decka przez przeciągany plik."""
        # Usuń wizualny feedback
        self.setStyleSheet("""
            DeckWidget {
                background-color: rgba(20, 20, 20, 180);
                border: 1px solid rgba(255, 255, 255, 25);
                border-radius: 12px;
                margin: 6px;
            }
        """)
        super().dragLeaveEvent(event)
    
    def dropEvent(self, event: QDropEvent):
        """Obsługuje upuszczenie pliku na deck."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and len(urls) == 1:
                file_path = urls[0].toLocalFile()
                if self._is_audio_file(file_path):
                    # Załaduj utwór do tego konkretnego decka
                    self._load_track_to_this_deck(file_path)
                    event.acceptProposedAction()
                    
                    # Usuń wizualny feedback
                    self.setStyleSheet("""
                        DeckWidget {
                            background-color: rgba(20, 20, 20, 180);
                            border: 1px solid rgba(255, 255, 255, 25);
                            border-radius: 12px;
                            margin: 6px;
                        }
                    """)
                    return
        event.ignore()
    
    def _is_audio_file(self, file_path: str) -> bool:
        """Sprawdza czy plik jest plikiem audio."""
        audio_extensions = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma'}
        return any(file_path.lower().endswith(ext) for ext in audio_extensions)
    
    def _load_track_to_this_deck(self, file_path: str):
        """Ładuje utwór bezpośrednio do tego decka."""
        try:
            # Reset BPM przy ładowaniu
            self.deck.detected_bpm = 0.0
            self.set_bpm_label("…")
            self.set_bpm_knob_enabled(False)
            
            if self.deck.load_track(file_path):
                self.track_info.setText(self.deck.track_name)
                
                # Inicjalizuj waveform z metadanymi
                self.waveform_mini.setAudioMeta(
                    self.deck.sample_rate, self.deck.total_frames, self.deck.duration
                )
                self.waveform_mini.loadAudioFile(file_path)
                
                # Powiadom główne okno o załadowaniu
                main_window = self.window()
                if hasattr(main_window, 'create_waveform_cache_for_deck'):
                    main_window.create_waveform_cache_for_deck(self.deck_name, file_path)
                
                print(f"Załadowano utwór do Deck {self.deck_name}: {self.deck.track_name}")
            else:
                print(f"Błąd ładowania utworu do Deck {self.deck_name}")
                
        except Exception as e:
            print(f"Błąd podczas ładowania utworu do Deck {self.deck_name}: {e}")


class MixerWidget(TransparentWidget):
    """Widget głównego miksera z crossfaderem."""
    
    def __init__(self, mixer: DJMixer):
        super().__init__(opacity=0.8)
        self.mixer = mixer
        self.setup_ui()
        self.setup_connections()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Nagłówek
        header = QLabel("MIXER")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 16px;
                padding: 8px;
                background-color: rgba(50, 50, 50, 150);
                border-radius: 3px;
            }
        """)
        layout.addWidget(header)
        
        # Crossfader
        crossfader_layout = QVBoxLayout()
        crossfader_layout.addWidget(QLabel("CROSSFADER"))
        
        self.crossfader = QSlider(Qt.Horizontal)
        self.crossfader.setRange(-100, 100)
        self.crossfader.setValue(0)
        crossfader_layout.addWidget(self.crossfader)
        
        # Etykiety A/B
        labels_layout = QHBoxLayout()
        labels_layout.addWidget(QLabel("A"))
        labels_layout.addStretch()
        labels_layout.addWidget(QLabel("B"))
        crossfader_layout.addLayout(labels_layout)
        
        layout.addLayout(crossfader_layout)
        
        # Master Volume i VU Meter w jednym rzędzie
        master_vu_layout = QHBoxLayout()
        
        # Master Volume
        master_layout = QVBoxLayout()
        master_layout.addWidget(QLabel("MASTER"))
        
        self.master_volume = QSlider(Qt.Vertical)
        self.master_volume.setRange(0, 100)
        self.master_volume.setValue(80)
        self.master_volume.setMaximumHeight(100)
        master_layout.addWidget(self.master_volume)
        
        self.master_label = QLabel("80%")
        self.master_label.setAlignment(Qt.AlignCenter)
        master_layout.addWidget(self.master_label)
        
        master_vu_layout.addLayout(master_layout)
        
        # Master VU Meter z boku
        master_vu_meter_layout = QVBoxLayout()
        master_vu_label = QLabel("LVL")
        master_vu_label.setAlignment(Qt.AlignCenter)
        master_vu_label.setStyleSheet("color: white; font-size: 9px;")
        master_vu_meter_layout.addWidget(master_vu_label)
        
        self.master_vu = VUMeter()
        master_vu_meter_layout.addWidget(self.master_vu)
        
        master_vu_layout.addLayout(master_vu_meter_layout)
        
        layout.addLayout(master_vu_layout)
        
        # Spectrum Quality removed
        
        # Global EQ Reset removed
        
        # Stylowanie
        self.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 11px;
                text-align: center;
            }
            QSlider::groove:horizontal {
                border: 1px solid #999999;
                height: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #B1B1B1, stop:1 #c4c4c4);
                margin: 2px 0;
                border-radius: 6px;
            }
            QSlider::handle:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #b4b4b4, stop:1 #8f8f8f);
                border: 1px solid #5c5c5c;
                width: 24px;
                margin: -2px 0;
                border-radius: 12px;
            }
        """)
    
    def setup_connections(self):
        self.crossfader.valueChanged.connect(self.update_crossfader)
        self.master_volume.valueChanged.connect(self.update_master_volume)
        # Spectrum quality connection removed
        # Global EQ reset connection removed
    
    def update_crossfader(self, value):
        crossfader_value = value / 100.0
        self.mixer.set_crossfader(crossfader_value)
    
    def update_master_volume(self, value):
        volume = value / 100.0
        self.mixer.set_master_volume(volume)
        self.master_label.setText(f"{value}%")
        
    # Spectrum quality update method removed
    
    # Reset all EQ method removed
    
    def update_display(self):
        """Aktualizuje VU meter mastera."""
        peak_levels = self.mixer.get_peak_levels()
        master_level = max(peak_levels.get('master_l', 0), peak_levels.get('master_r', 0))
        self.master_vu.set_level(master_level)


class DraggableTreeWidget(QTreeWidget):
    """QTreeWidget z obsługą drag-and-drop dla utworów."""
    
    # Sygnały dla ładowania na konkretny deck
    load_to_deck_a = Signal(str)  # file_path
    load_to_deck_b = Signal(str)  # file_path
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragOnly)
    
    def startDrag(self, supportedActions):
        """Rozpoczyna operację drag-and-drop."""
        item = self.currentItem()
        if item:
            file_path = item.data(0, Qt.UserRole)
            if file_path:
                drag = QDrag(self)
                mimeData = QMimeData()
                
                # Dodaj URL pliku do MIME data
                url = QUrl.fromLocalFile(file_path)
                mimeData.setUrls([url])
                
                # Dodaj tekst jako fallback
                mimeData.setText(file_path)
                
                drag.setMimeData(mimeData)
                
                # Wykonaj drag
                drag.exec_(Qt.CopyAction)
    
    def mousePressEvent(self, event):
        """Obsługuje kliknięcia - lewy na deck A, prawy na deck B."""
        item = self.itemAt(event.pos())
        if item:
            file_path = item.data(0, Qt.UserRole)
            if file_path:
                if event.button() == Qt.LeftButton:
                    # Lewy przycisk = deck A
                    self.load_to_deck_a.emit(file_path)
                elif event.button() == Qt.RightButton:
                    # Prawy przycisk = deck B
                    self.load_to_deck_b.emit(file_path)
                    return  # Nie wywołuj super() dla prawego przycisku
        
        super().mousePressEvent(event)


class PlaylistWidget(TransparentWidget):
    """Widget listy utworów."""
    
    track_selected = Signal(str)  # file_path
    load_deck_a = Signal(str)  # file_path
    load_deck_b = Signal(str)  # file_path
    
    def __init__(self):
        super().__init__(opacity=0.7)
        self.music_library = MusicLibrary()
        self.setup_ui()
        self.setup_connections()
        
        # Włącz drag-and-drop
        self.track_list.setDragEnabled(True)
        self.track_list.setDragDropMode(QTreeWidget.DragOnly)
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Nagłówek
        header = QLabel("PLAYLIST")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 12px;
                padding: 5px;
                background-color: rgba(50, 50, 50, 150);
                border-radius: 3px;
            }
        """)
        layout.addWidget(header)
        
        # Przycisk wyboru folderu
        self.folder_btn = QPushButton("📁 Wybierz folder")
        layout.addWidget(self.folder_btn)
        
        # Lista utworów z dwiema kolumnami
        self.track_list = DraggableTreeWidget()
        self.track_list.setMaximumHeight(200)
        self.track_list.setColumnCount(2)
        self.track_list.setHeaderLabels(["Track", "BPM"])
        self.track_list.setColumnWidth(1, 80)  # Prawa kolumna BPM
        self.track_list.header().setStretchLastSection(False)
        self.track_list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.track_list.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.track_list.setRootIsDecorated(False)  # Bez strzałek rozwijania
        self.track_list.setSortingEnabled(True)  # Włącz sortowanie po kliknięciu nagłówka
        layout.addWidget(self.track_list)
        
        # Stylowanie
        self.setStyleSheet("""
            QPushButton {
                background-color: rgba(70, 70, 70, 200);
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: rgba(90, 90, 90, 200);
            }
            QTreeWidget {
                background-color: rgba(30, 30, 30, 200);
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                alternate-background-color: rgba(40, 40, 40, 100);
            }
            QTreeWidget::item {
                padding: 3px;
                border-bottom: 1px solid #444;
            }
            QTreeWidget::item:selected {
                background-color: rgba(0, 120, 215, 150);
            }
            QTreeWidget::item:hover {
                background-color: rgba(70, 70, 70, 100);
            }
            QHeaderView::section {
                background-color: rgba(50, 50, 50, 200);
                color: white;
                padding: 4px;
                border: 1px solid #555;
                font-weight: bold;
            }
            QHeaderView::section:hover {
                background-color: rgba(70, 70, 70, 200);
            }
        """)
    
    def setup_connections(self):
        self.folder_btn.clicked.connect(self.select_folder)
        self.track_list.itemDoubleClicked.connect(self.on_track_double_clicked)
        self.music_library.scan_finished.connect(self.on_scan_finished)
        self.music_library.track_added.connect(self.on_track_added)
        
        # Połączenia dla ładowania na konkretne decki
        self.track_list.load_to_deck_a.connect(self.load_to_deck_a)
        self.track_list.load_to_deck_b.connect(self.load_to_deck_b)
    
    def select_folder(self):
        folder = self.music_library.select_folder(self)
        if folder:
            self.track_list.clear()
            self.music_library.scan_folder(folder)
            # Okno analizy batch zostanie otwarte po zakończeniu skanowania
    
    def on_scan_finished(self, count):
        print(f"Skanowanie zakończone: {count} utworów")
        
        # Automatycznie otwórz okno analizy batch jeśli znaleziono utwory
        if count > 0 and self.music_library.tracks:
            batch_window = BatchKeyBpmWindow(self.music_library.tracks, self)
            # Połącz sygnały do odświeżania listy
            batch_window.track_analyzed.connect(self.on_track_bpm_updated)
            batch_window.analysis_finished.connect(self.on_batch_analysis_finished)
            batch_window.exec()  # Modal dialog
    
    def on_track_added(self, track_info):
        # Utwórz element drzewa z dwiema kolumnami
        item = QTreeWidgetItem()
        item.setText(0, track_info.name)  # Kolumna "Track"
        
        # Kolumna "BPM"
        if track_info.bpm:
            item.setText(1, f"{track_info.bpm:.1f}")
        else:
            item.setText(1, "—")
        
        # Wyrównanie do prawej dla kolumny BPM
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        
        item.setData(0, Qt.UserRole, track_info.file_path)
        item.setData(0, Qt.UserRole + 1, track_info)  # Przechowaj TrackInfo
        self.track_list.addTopLevelItem(item)
    

    def on_track_double_clicked(self, item, column):
        file_path = item.data(0, Qt.UserRole)
        if file_path:
            self.track_selected.emit(file_path)
    
    def on_track_bpm_updated(self, file_path: str, bpm: float, key: str):
        """Aktualizuje BPM w liście utworów po analizie."""
        # Znajdź element w liście
        for i in range(self.track_list.topLevelItemCount()):
            item = self.track_list.topLevelItem(i)
            if item.data(0, Qt.UserRole) == file_path:
                # Aktualizuj BPM w kolumnie
                if bpm > 0:
                    item.setText(1, f"{bpm:.1f}")
                else:
                    item.setText(1, "—")
                
                # Aktualizuj TrackInfo w cache
                track_info = item.data(0, Qt.UserRole + 1)
                if track_info:
                    track_info._bpm = bpm if bpm > 0 else None
                    track_info._bpm_loaded = True
                break
    
    def on_batch_analysis_finished(self, results: dict):
        """Obsługuje zakończenie analizy batch."""
        analyzed_count = len([r for r in results.values() if r.get('bpm', 0) > 0])
        print(f"Analiza batch zakończona: {analyzed_count}/{len(results)} utworów przeanalizowanych")
    
    def load_to_deck_a(self, file_path: str):
        """Ładuje utwór na deck A."""
        print(f"Ładowanie na deck A: {file_path}")
        self.load_deck_a.emit(file_path)
    
    def load_to_deck_b(self, file_path: str):
        """Ładuje utwór na deck B."""
        print(f"Ładowanie na deck B: {file_path}")
        self.load_deck_b.emit(file_path)


class DJLiteMainWindow(QMainWindow):
    """Główne okno DJ Lite - przezroczyste, zawsze na wierzchu."""
    
    def __init__(self):
        super().__init__()
        self.mixer = DJMixer()
        
        # Master deck management
        self.master_deck = "A"  # domyślnie deck A jako master
        self.center_last_bpm = None  # Ostatnie BPM dla smooth transition
        
        # Beat Clock system
        self.beat_clock = BeatClock()
        self.waveform_caches = {}  # Cache waveform dla każdego decka
        
        # Telemetria diagnostyczna
        self.telemetry = TelemetryDiagnostics(
            mixer=self.mixer,
            log_to_file=True,
            log_to_console=True
        )
        
        # ClickTest - metronom testowy
        self.click_test = ClickTest(
            mixer=self.mixer
        )
        
        # Detektor dryfu
        self.drift_detector = DriftDetector()
        
        # Spectrum worker removed
        
        self.setup_window()
        self.setup_ui()
        self.setup_connections()
        self.setup_timer()
        
        # Uruchom audio
        self.mixer.start_audio()
        
        # Uruchom telemetrię diagnostyczną
        self.telemetry.start()
        
        # Uruchom ClickTest
        self.click_test.start()
        
        # Uruchom detektor dryfu
        self.drift_detector.start()
        
        # Spectrum worker start removed
    
    def setup_connections(self):
        """Konfiguruje połączenia sygnałów."""
        # Connections simplified
    
    def setup_window(self):
        """Konfiguruje właściwości okna."""
        self.setWindowTitle("DJ Lite")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | 
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        
        # Przezroczystość
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.95)
        
        # Obsługa focus dla always-on-top ale w tle
        self.installEventFilter(self)
        self.setFocusPolicy(Qt.StrongFocus)
        
        # Rozmiar i pozycja
        self.resize(800, 600)
        
        # Możliwość przesuwania okna
        self.drag_position = None
    
    def setup_ui(self):
        """Tworzy interfejs użytkownika."""
        central_widget = TransparentWidget(opacity=0.1)
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(8)  # Gap między deckami i środkowym panelem
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Lewy panel - Deck A
        self.deck_a_widget = DeckWidget("A", self.mixer)
        main_layout.addWidget(self.deck_a_widget)
        
        # Środkowy panel - Mixer + Central Grid + Playlist
        center_layout = QVBoxLayout()
        
        self.mixer_widget = MixerWidget(self.mixer)
        center_layout.addWidget(self.mixer_widget)
        
        # Central Wave View - waveform z beat grid
        self.central_wave = CentralWaveView(
            source_deck=self.mixer.deck_a,  # domyślnie deck A
            master_deck=self.mixer.deck_a   # domyślnie deck A jako master
        )
        # Ustaw referencje do obu decków
        self.central_wave.set_deck_references(self.mixer.deck_a, self.mixer.deck_b)
        self.central_wave.setMinimumHeight(140)
        self.central_wave.setMaximumHeight(200)
        center_layout.addWidget(self.central_wave)
        
        # Zachowaj stary central_grid dla kompatybilności
        self.central_grid = self.central_wave
        
        # Kontrolki zoom i reset
        controls_layout = QHBoxLayout()
        
        # Zoom controls
        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("Zoom:"))
        
        self.zoom_out_btn = QPushButton("-")
        self.zoom_out_btn.clicked.connect(self.central_wave.zoom_out)
        zoom_layout.addWidget(self.zoom_out_btn)
        
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.clicked.connect(self.central_wave.zoom_in)
        zoom_layout.addWidget(self.zoom_in_btn)
        
        self.reset_view_btn = QPushButton("Reset")
        self.reset_view_btn.clicked.connect(self.central_wave.reset_view)
        zoom_layout.addWidget(self.reset_view_btn)
        
        controls_layout.addLayout(zoom_layout)
        center_layout.addLayout(controls_layout)
        
        self.playlist_widget = PlaylistWidget()
        center_layout.addWidget(self.playlist_widget)
        
        # Przycisk zamknięcia
        self.close_btn = QPushButton("✕")
        self.close_btn.setMaximumSize(30, 30)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(200, 50, 50, 200);
                color: white;
                border: none;
                border-radius: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 70, 70, 200);
            }
        """)
        center_layout.addWidget(self.close_btn)
        
        main_layout.addLayout(center_layout)
        
        # Prawy panel - Deck B
        self.deck_b_widget = DeckWidget("B", self.mixer)
        main_layout.addWidget(self.deck_b_widget)
        
        # Panel HUD - Drift Detector i ClickTest
        self.drift_hud = DriftHUD()
        self.drift_hud.set_drift_detector(self.drift_detector)
        self.drift_hud.set_click_test(self.click_test)
        self.drift_hud.set_deck_references(self.mixer.deck_a, self.mixer.deck_b)
        self.drift_hud.setMaximumWidth(200)
        main_layout.addWidget(self.drift_hud)
        
        # Ustaw referencje między deckami dla funkcji SYNC
        self.deck_a_widget.set_other_deck(self.mixer.deck_b)
        self.deck_b_widget.set_other_deck(self.mixer.deck_a)
        
        # Połącz sygnały
        self.playlist_widget.track_selected.connect(self.load_track_to_deck)
        self.playlist_widget.load_deck_a.connect(self.load_track_to_deck_a)
        self.playlist_widget.load_deck_b.connect(self.load_track_to_deck_b)
    
    def setup_timer(self):
        """Konfiguruje timer do aktualizacji UI."""
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start(50)  # 20 FPS
        
        # Timer do animacji płyt (60 FPS)
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.update_turntable_animation)
        self.animation_timer.start(16)  # ~60 FPS
        
        # Kąty obrotu płyt
        self.rotation_a = 0.0
        self.rotation_b = 0.0
        
        # Skróty klawiszowe
        self.setup_keyboard_shortcuts()
    
    def update_ui(self):
        """Aktualizuje elementy UI."""
        self.deck_a_widget.update_display()
        self.deck_b_widget.update_display()
        self.mixer_widget.update_display()
        
        # BeatClock synchronization removed
    
    def update_turntable_animation(self):
        """Aktualizuje animację obracających się płyt i phase correction (60 FPS)."""
        # Phase correction (nudge-as-phase) - tylko dla slave decka względem mastera
        self._update_phase_correction()
        
        # Deck A
        if self.mixer.deck_a.is_playing:
            tempo_a = self.mixer.deck_a.get_tempo() if hasattr(self.mixer.deck_a, 'get_tempo') else 1.0
            self.rotation_a += 2.0 * tempo_a  # Prędkość obrotu zależna od tempa
            if self.rotation_a >= 360:
                self.rotation_a -= 360
            self._rotate_deck_visual(self.deck_a_widget, self.rotation_a)
        
        # Deck B
        if self.mixer.deck_b.is_playing:
            tempo_b = self.mixer.deck_b.get_tempo() if hasattr(self.mixer.deck_b, 'get_tempo') else 1.0
            self.rotation_b += 2.0 * tempo_b  # Prędkość obrotu zależna od tempa
            if self.rotation_b >= 360:
                self.rotation_b -= 360
            self._rotate_deck_visual(self.deck_b_widget, self.rotation_b)
    
    def _rotate_deck_visual(self, deck_widget, angle: float):
        """Dodaje wizualny efekt obrotu do decka."""
        # Prosta animacja przez zmianę opacity lub inne efekty wizualne
        # Można rozszerzyć o bardziej zaawansowane animacje
        pass
    
    def _update_phase_correction(self):
        """Aktualizuje phase correction (nudge-as-phase) dla slave decka względem mastera."""
        if not hasattr(self, 'master_deck') or not self.master_deck:
            return
            
        # Określ master i slave deck
        if self.master_deck == 'A':
            master_deck = self.mixer.deck_a
            slave_deck = self.mixer.deck_b
        elif self.master_deck == 'B':
            master_deck = self.mixer.deck_b
            slave_deck = self.mixer.deck_a
        else:
            return
            
        # Wywołaj phase correction tylko jeśli slave ma aktywny nudge
        if hasattr(slave_deck, 'phase_follow_tick') and hasattr(slave_deck, 'nudge_active'):
            if slave_deck.nudge_active:
                slave_deck.phase_follow_tick(master_deck, strength=0.01)
    
    def setup_keyboard_shortcuts(self):
        """Konfiguruje skróty klawiszowe."""
        # Space - Start/Stop deck A
        self.shortcut_space = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.shortcut_space.activated.connect(lambda: self.toggle_deck_play('A'))
        
        # Enter - Start/Stop deck B
        self.shortcut_enter = QShortcut(QKeySequence(Qt.Key_Return), self)
        self.shortcut_enter.activated.connect(lambda: self.toggle_deck_play('B'))
        
        # [ - Tempo A w dół
        self.shortcut_bracket_left = QShortcut(QKeySequence(Qt.Key_BracketLeft), self)
        self.shortcut_bracket_left.activated.connect(lambda: self.adjust_tempo('A', -0.05))
        
        # ] - Tempo A w górę
        self.shortcut_bracket_right = QShortcut(QKeySequence(Qt.Key_BracketRight), self)
        self.shortcut_bracket_right.activated.connect(lambda: self.adjust_tempo('A', 0.05))
        
        # ; - Tempo B w dół
        self.shortcut_semicolon = QShortcut(QKeySequence(Qt.Key_Semicolon), self)
        self.shortcut_semicolon.activated.connect(lambda: self.adjust_tempo('B', -0.05))
        
        # ' - Tempo B w górę
        self.shortcut_apostrophe = QShortcut(QKeySequence(Qt.Key_Apostrophe), self)
        self.shortcut_apostrophe.activated.connect(lambda: self.adjust_tempo('B', 0.05))
        
        # Nudge shortcuts będą obsługiwane przez keyPressEvent/keyReleaseEvent
        # żeby umożliwić trzymanie i puszczanie klawiszy
        self.nudge_keys_pressed = set()  # Śledzenie wciśniętych klawiszy nudge
        
        # EQ reset shortcuts removed
    
    def toggle_deck_play(self, deck: str):
        """Przełącza play/pause dla decka przez skrót klawiszowy."""
        if deck == 'A':
            if self.mixer.deck_a.is_playing():
                self.mixer.deck_a.stop()
                self.deck_a_widget.play_button.setText("▶")
            else:
                self.mixer.deck_a.play()
                self.deck_a_widget.play_button.setText("⏸")
        elif deck == 'B':
            if self.mixer.deck_b.is_playing():
                self.mixer.deck_b.stop()
                self.deck_b_widget.play_button.setText("▶")
            else:
                self.mixer.deck_b.play()
                self.deck_b_widget.play_button.setText("⏸")
    
    def adjust_tempo(self, deck: str, delta: float):
        """Dostosowuje tempo decka o zadaną wartość."""
        if deck == 'A':
            current_tempo = self.mixer.deck_a.get_tempo() if hasattr(self.mixer.deck_a, 'get_tempo') else 1.0
            new_tempo = max(0.5, min(2.0, current_tempo + delta))
            self.mixer.deck_a.set_tempo(new_tempo)
            self.deck_a_widget.tempo_slider.setValue(int(new_tempo * 100))
        elif deck == 'B':
            current_tempo = self.mixer.deck_b.get_tempo() if hasattr(self.mixer.deck_b, 'get_tempo') else 1.0
            new_tempo = max(0.5, min(2.0, current_tempo + delta))
            self.mixer.deck_b.set_tempo(new_tempo)
            self.deck_b_widget.tempo_slider.setValue(int(new_tempo * 100))
    

    

    
    def set_master(self, deck_name: str):
        """Ustawia master deck i aktualizuje widok."""
        if deck_name not in ["A", "B"]:
            return
            
        old_master = self.master_deck
        self.master_deck = deck_name
        
        # Aktualizuj CentralWaveView
        if hasattr(self, 'central_wave') and self.central_wave:
            master_deck_obj = self.get_master_deck_object()
            self.central_wave.set_master_deck(master_deck_obj)
            
            # Opcjonalnie przelicz beat_offset żeby faza na playhead nie skoczyła
            if master_deck_obj and hasattr(master_deck_obj, 'position') and hasattr(master_deck_obj, 'detected_bpm'):
                t = master_deck_obj.position
                if hasattr(self, 'center_last_bpm') and self.center_last_bpm and master_deck_obj.detected_bpm:
                    old_bpm = self.center_last_bpm
                    new_bpm = master_deck_obj.detected_bpm
                    if old_bpm > 0 and new_bpm > 0:
                        idx = (t - getattr(master_deck_obj, 'beat_offset', 0.0)) / (60.0/old_bpm)
                        master_deck_obj.beat_offset = t - idx * (60.0/new_bpm)
                        
            self.central_wave.update()
            
        print(f"Master deck changed: {old_master} -> {deck_name}")
        
    def get_master_deck_object(self):
        """Zwraca obiekt master deck."""
        if self.master_deck == "A":
            return self.mixer.deck_a
        elif self.master_deck == "B":
            return self.mixer.deck_b
        return None
    
    def sync_beat_clock_with_master(self):
        """Synchronizuje BeatClock z master deck przez CentralGridView."""
        if not self.master_deck:
            return
            
        master = self.get_master_deck_object()
        if not master or not master.is_loaded():
            return
        
        # Użyj nowej metody sync_with_master_deck z CentralGridView
        if hasattr(self, 'central_grid') and self.central_grid:
            self.central_grid.sync_with_master_deck(master)
        else:
            # Fallback - stara metoda jeśli central_grid nie jest dostępny
            if hasattr(master, 'detected_bpm') and master.detected_bpm > 0:
                effective_bpm = master.detected_bpm * master.effective_ratio()
                
                if not hasattr(self.beat_clock, 'grid') or self.beat_clock.grid is None:
                    self.beat_clock.grid = BeatGrid(bpm=effective_bpm)
                else:
                    self.beat_clock.grid.bpm = effective_bpm
                
                self.beat_clock.set_tempo_ratio(master.effective_ratio())
                print(f"BeatClock synced (fallback): BPM={effective_bpm:.1f}, effective_ratio={master.effective_ratio():.3f}")
    


    
    def create_waveform_cache_for_deck(self, deck_name: str, file_path: str):
        """Tworzy waveform cache dla decka."""
        try:
            import soundfile as sf
            audio_data, sample_rate = sf.read(file_path)
            
            # Konwersja do mono jeśli stereo
            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)
            
            # Utwórz cache z większym block_size dla oszczędności zasobów
            wf_cache = WaveformCache(audio_data, sample_rate, block_size=512)
            
            deck_key = f"deck_{deck_name.lower()}"
            self.waveform_caches[deck_key] = wf_cache
            # Dodaj również pod file_path dla łatwiejszego dostępu
            self.waveform_caches[file_path] = wf_cache
            
            # Waveform cache created for deck
            
            # Ustaw waveform cache w CentralWaveView dla odpowiedniego decka
            if deck_name == "A":
                self.central_wave.set_waveform_cache(wf_cache)
            elif deck_name == "B":
                self.central_wave.set_waveform_cache_b(wf_cache)
                
            print(f"Waveform cache created for Deck {deck_name}: {wf_cache.get_info()}")
            
        except Exception as e:
            print(f"Failed to create waveform cache for Deck {deck_name}: {e}")
    
    def load_track_to_deck(self, file_path: str):
        """Ładuje utwór do aktywnego decka (placeholder)."""
        # Prosta logika - ładuj do decka A jeśli pusty, inaczej do B
        if not self.mixer.deck_a.is_loaded():
            # UI kontrakt: reset BPM przy ładowaniu
            self.mixer.deck_a.detected_bpm = 0.0
            self.deck_a_widget.set_bpm_label("…")
            self.deck_a_widget.set_bpm_knob_enabled(False)
            
            if self.mixer.deck_a.load_track(file_path):
                self.deck_a_widget.track_info.setText(self.mixer.deck_a.track_name)
                
                # Inicjalizuj waveform z metadanymi
                deck = self.mixer.deck_a
                self.deck_a_widget.waveform_mini.setAudioMeta(
                    deck.sample_rate, deck.total_frames, deck.duration
                )
                self.deck_a_widget.waveform_mini.loadAudioFile(file_path)
                
                # Utwórz waveform cache dla central grid
                self.create_waveform_cache_for_deck("A", file_path)
                
                # Deck automatycznie rozpocznie analizę BPM w tle
        else:
            # UI kontrakt: reset BPM przy ładowaniu
            self.mixer.deck_b.detected_bpm = 0.0
            self.deck_b_widget.set_bpm_label("…")
            self.deck_b_widget.set_bpm_knob_enabled(False)
            
            if self.mixer.deck_b.load_track(file_path):
                self.deck_b_widget.track_info.setText(self.mixer.deck_b.track_name)
                
                # Inicjalizuj waveform z metadanymi
                deck = self.mixer.deck_b
                self.deck_b_widget.waveform_mini.setAudioMeta(
                    deck.sample_rate, deck.total_frames, deck.duration
                )
                self.deck_b_widget.waveform_mini.loadAudioFile(file_path)
                
                # Utwórz waveform cache dla central grid
                self.create_waveform_cache_for_deck("B", file_path)
                
                # Deck automatycznie rozpocznie analizę BPM w tle
    
    def load_track_to_deck_a(self, file_path: str):
        """Ładuje utwór bezpośrednio na deck A."""
        # UI kontrakt: reset BPM przy ładowaniu
        self.mixer.deck_a.detected_bpm = 0.0
        self.deck_a_widget.set_bpm_label("…")
        self.deck_a_widget.set_bpm_knob_enabled(False)
        
        if self.mixer.deck_a.load_track(file_path):
            self.deck_a_widget.track_info.setText(self.mixer.deck_a.track_name)
            
            # Inicjalizuj waveform z metadanymi
            deck = self.mixer.deck_a
            self.deck_a_widget.waveform_mini.setAudioMeta(
                deck.sample_rate, deck.total_frames, deck.duration
            )
            self.deck_a_widget.waveform_mini.loadAudioFile(file_path)
            
            # Utwórz waveform cache dla central grid
            self.create_waveform_cache_for_deck("A", file_path)
            
            print(f"Załadowano utwór na Deck A: {self.mixer.deck_a.track_name}")

    def load_track_to_deck_b(self, file_path: str):
        """Ładuje utwór bezpośrednio na deck B."""
        # UI kontrakt: reset BPM przy ładowaniu
        self.mixer.deck_b.detected_bpm = 0.0
        self.deck_b_widget.set_bpm_label("…")
        self.deck_b_widget.set_bpm_knob_enabled(False)
        
        if self.mixer.deck_b.load_track(file_path):
            self.deck_b_widget.track_info.setText(self.mixer.deck_b.track_name)
            
            # Inicjalizuj waveform z metadanymi
            deck = self.mixer.deck_b
            self.deck_b_widget.waveform_mini.setAudioMeta(
                deck.sample_rate, deck.total_frames, deck.duration
            )
            self.deck_b_widget.waveform_mini.loadAudioFile(file_path)
            
            # Utwórz waveform cache dla central grid
            self.create_waveform_cache_for_deck("B", file_path)
            
            print(f"Załadowano utwór na Deck B: {self.mixer.deck_b.track_name}")
    
    def mousePressEvent(self, event):
        """Rozpoczyna przeciąganie okna."""
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        """Przeciąga okno."""
        if event.buttons() == Qt.LeftButton and self.drag_position is not None:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
    
    def keyPressEvent(self, event):
        """Obsługuje wciśnięcie klawiszy - nudge shortcuts."""
        key = event.key()
        
        # A - Nudge A minus (deck A)
        if key == Qt.Key_A and key not in self.nudge_keys_pressed:
            self.nudge_keys_pressed.add(key)
            self.deck_a_widget.start_nudge_minus()
        
        # S - Nudge A plus (deck A)
        elif key == Qt.Key_S and key not in self.nudge_keys_pressed:
            self.nudge_keys_pressed.add(key)
            self.deck_a_widget.start_nudge_plus()
        
        super().keyPressEvent(event)
    
    def keyReleaseEvent(self, event):
        """Obsługuje puszczenie klawiszy - zatrzymanie nudge."""
        key = event.key()
        
        # Zatrzymaj nudge gdy puszczono A lub S
        if key in [Qt.Key_A, Qt.Key_S] and key in self.nudge_keys_pressed:
            self.nudge_keys_pressed.discard(key)
            self.deck_a_widget.stop_nudge()
        
        super().keyReleaseEvent(event)
    
    def show_toast(self, message: str):
        """Pokazuje toast notification w głównym oknie."""
        # Prosta implementacja toast - można rozszerzyć o bardziej zaawansowane UI
        print(f"🔔 SYNC: {message}")
        
        # Opcjonalnie: można dodać wizualny toast widget
        # toast_widget = QLabel(message)
        # toast_widget.setStyleSheet("background: rgba(0,0,0,200); color: white; padding: 10px; border-radius: 5px;")
        # toast_widget.show()
        # QTimer.singleShot(3000, toast_widget.hide)
    
    def eventFilter(self, obj, event):
        """Obsługuje zdarzenia okna dla lepszego zachowania always-on-top."""
        if obj == self:
            if event.type() == QEvent.WindowDeactivate:
                # Gdy okno traci focus, pozostaje on-top ale nie przeszkadza
                self.setWindowOpacity(0.7)  # Zmniejsz przezroczystość
            elif event.type() == QEvent.WindowActivate:
                # Gdy okno odzyskuje focus
                self.setWindowOpacity(0.95)  # Przywróć normalną przezroczystość
        return super().eventFilter(obj, event)
    
    def closeEvent(self, event):
        """Cleanup przy zamykaniu."""
        self.update_timer.stop()
        self.animation_timer.stop()
        
        # Zatrzymaj telemetrię diagnostyczną
        if hasattr(self, 'telemetry'):
            self.telemetry.stop()
        
        # Zatrzymaj ClickTest
        if hasattr(self, 'click_test'):
            self.click_test.stop()
        
        # Zatrzymaj detektor dryfu
        if hasattr(self, 'drift_detector'):
            self.drift_detector.stop()
        
        # Spectrum worker stop removed
            
        self.mixer.stop_audio()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DJLiteMainWindow()
    window.show()
    sys.exit(app.exec())