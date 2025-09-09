"""Główne okno DJ Lite - przezroczyste, zawsze na wierzchu, nakładka na pulpit."""

import sys
import os
from typing import Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QSlider, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QProgressBar, QGroupBox, QSplitter, QDial
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize, QEvent
from PySide6.QtGui import QFont, QPalette, QColor, QPainter, QBrush, QTransform, QPixmap, QKeySequence, QShortcut

# Import audio components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio.mixer import DJMixer
from utils.file_browser import MusicLibrary, QuickBrowser


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
        
        super().paintEvent(event)


class VUMeter(QWidget):
    """Prosty VU meter do wyświetlania poziomu audio."""
    
    def __init__(self, orientation=Qt.Vertical):
        super().__init__()
        self.orientation = orientation
        self.level = 0.0
        self.peak_level = 0.0
        self.setMinimumSize(20, 100)
    
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


class DeckWidget(TransparentWidget):
    """Widget pojedynczego decka z kontrolkami."""
    
    track_loaded = Signal(str)  # file_path
    
    def __init__(self, deck_name: str, mixer: DJMixer):
        super().__init__(opacity=0.7)
        self.deck_name = deck_name
        self.mixer = mixer
        self.deck = mixer.get_deck(deck_name.lower())
        
        self.setup_ui()
        self.setup_connections()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 10, 10, 10)
        
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
        
        # Progress bar pozycji
        self.position_bar = QProgressBar()
        self.position_bar.setMaximum(1000)
        self.position_bar.setTextVisible(False)
        self.position_bar.setMaximumHeight(8)
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
        
        self.rate_label = QLabel("1.00x")
        self.rate_label.setAlignment(Qt.AlignCenter)
        self.rate_label.setStyleSheet("color: white; font-size: 10px; font-weight: bold;")
        bpm_layout.addWidget(self.rate_label)
        
        layout.addLayout(bpm_layout)
        
        # Up-fader (Volume)
        volume_layout = QVBoxLayout()
        volume_layout.addWidget(QLabel("VOLUME"))
        
        self.gain_slider = QSlider(Qt.Vertical)
        self.gain_slider.setRange(0, 200)
        self.gain_slider.setValue(100)
        self.gain_slider.setMaximumHeight(120)
        volume_layout.addWidget(self.gain_slider)
        
        self.gain_label = QLabel("100%")
        self.gain_label.setAlignment(Qt.AlignCenter)
        volume_layout.addWidget(self.gain_label)
        
        layout.addLayout(volume_layout)
        
        # EQ sekcja
        eq_group = QGroupBox("EQ")
        eq_layout = QGridLayout(eq_group)
        
        # High
        eq_layout.addWidget(QLabel("HI"), 0, 0)
        self.eq_high = QSlider(Qt.Vertical)
        self.eq_high.setRange(-100, 100)
        self.eq_high.setValue(0)
        self.eq_high.setMaximumHeight(80)
        eq_layout.addWidget(self.eq_high, 1, 0)
        
        # Mid
        eq_layout.addWidget(QLabel("MID"), 0, 1)
        self.eq_mid = QSlider(Qt.Vertical)
        self.eq_mid.setRange(-100, 100)
        self.eq_mid.setValue(0)
        self.eq_mid.setMaximumHeight(80)
        eq_layout.addWidget(self.eq_mid, 1, 1)
        
        # Low
        eq_layout.addWidget(QLabel("LOW"), 0, 2)
        self.eq_low = QSlider(Qt.Vertical)
        self.eq_low.setRange(-100, 100)
        self.eq_low.setValue(0)
        self.eq_low.setMaximumHeight(80)
        eq_layout.addWidget(self.eq_low, 1, 2)
        
        # VU Meter
        self.vu_meter = VUMeter()
        eq_layout.addWidget(self.vu_meter, 1, 3)
        
        layout.addWidget(eq_group)
        
        # Cue and Sync buttons
        buttons_layout = QHBoxLayout()
        
        self.cue_btn = QPushButton("CUE")
        self.cue_btn.setCheckable(True)
        self.cue_btn.setMaximumHeight(30)
        buttons_layout.addWidget(self.cue_btn)
        
        self.sync_btn = QPushButton("SYNC")
        self.sync_btn.setMaximumHeight(30)
        buttons_layout.addWidget(self.sync_btn)
        
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
        
        self.eq_high.valueChanged.connect(self.update_eq_high)
        self.eq_mid.valueChanged.connect(self.update_eq_mid)
        self.eq_low.valueChanged.connect(self.update_eq_low)
        
        self.cue_btn.toggled.connect(self.toggle_cue)
        self.sync_btn.clicked.connect(self.sync_deck)
        
        # Połącz sygnały z deck
        self.deck.bpmReady.connect(self.on_bpm_detected)
        self.deck.analysisFailed.connect(self.on_bpm_analysis_failed)
    
    def play_track(self):
        self.deck.play()
    
    def pause_track(self):
        self.deck.pause()
    
    def stop_track(self):
        self.deck.stop()
    
    def load_track(self):
        file_path = QuickBrowser.select_audio_file(self)
        if file_path:
            if self.deck.load_track(file_path):
                self.track_info.setText(self.deck.track_name)
                self.track_loaded.emit(file_path)
    
    def update_bpm_target(self, value):
        """Obsługuje zmianę gałki BPM - ustawia target BPM w decku."""
        # Sprawdź czy to nie jest programmatyczne ustawienie
        if hasattr(self, '_bpm_dial_updating') and self._bpm_dial_updating:
            return
            
        bpm_value = value / 10.0  # Konwersja z zakresu 600-2000 na 60.0-200.0
        self.deck.set_bpm_target(bpm_value)
        self.bpm_target_label.setText(f"Target: {bpm_value:.1f}")
    
    def on_bpm_detected(self, bpm: float):
        """Obsługuje sygnał bpmReady z deck."""
        self.bpm_detected_label.setText(f"BPM (detected): {bpm:.1f}")
        
        # Ustaw gałkę na detected BPM bez wywoływania set_bpm_target
        self._bpm_dial_updating = True
        self.bpm_dial.blockSignals(True)
        self.bpm_dial.setValue(int(bpm * 10))
        self.bpm_dial.blockSignals(False)
        self._bpm_dial_updating = False
    
    def on_bpm_analysis_failed(self, error_msg: str):
        """Obsługuje sygnał analysisFailed z deck."""
        self.bpm_detected_label.setText("BPM (detected): Failed")
        print(f"Deck {self.deck_name}: BPM analysis failed: {error_msg}")
    
    def update_bpm_display(self):
        """Aktualizuje wyświetlane informacje o BPM i rate."""
        if hasattr(self.deck, 'detected_bpm') and self.deck.detected_bpm:
            self.bpm_detected_label.setText(f"BPM (detected): {self.deck.detected_bpm:.1f}")
            # Ustaw gałkę na detected BPM, ale nie wywołuj set_bpm_target
            if not hasattr(self, '_bpm_dial_set_programmatically'):
                self._bpm_dial_set_programmatically = True
                self.bpm_dial.setValue(int(self.deck.detected_bpm * 10))
                self._bpm_dial_set_programmatically = False
        
        if hasattr(self.deck, 'rate_smooth'):
            self.rate_label.setText(f"{self.deck.rate_smooth:.2f}x")
    
    def update_gain(self, value):
        gain = value / 100.0
        self.mixer.set_deck_gain(self.deck_name.lower(), gain)
        self.gain_label.setText(f"{value}%")
    
    def update_eq_high(self, value):
        eq_value = value / 100.0
        self.mixer.set_eq(self.deck_name.lower(), 'high', eq_value)
    
    def update_eq_mid(self, value):
        eq_value = value / 100.0
        self.mixer.set_eq(self.deck_name.lower(), 'mid', eq_value)
    
    def update_eq_low(self, value):
        eq_value = value / 100.0
        self.mixer.set_eq(self.deck_name.lower(), 'low', eq_value)
    
    def toggle_cue(self, checked):
        self.mixer.set_cue(self.deck_name.lower(), checked)
    
    def sync_deck(self):
        """Synchronizuje ten deck do drugiego decka (master)."""
        if hasattr(self, 'other_deck') and self.other_deck:
            self.deck.sync_to_deck(self.other_deck)
    
    def set_other_deck(self, other_deck):
        """Ustawia referencję do drugiego decka dla funkcji SYNC."""
        self.other_deck = other_deck
    
    def update_display(self):
        """Aktualizuje wyświetlane informacje."""
        if self.deck.is_loaded():
            # Aktualizuj progress bar
            progress = int(self.deck.get_position_percent() * 1000)
            self.position_bar.setValue(progress)
            
            # Aktualizuj VU meter
            peak_levels = self.mixer.get_peak_levels()
            deck_key = f'deck_{self.deck_name.lower()}_l'
            if deck_key in peak_levels:
                level = max(peak_levels[deck_key], peak_levels.get(f'deck_{self.deck_name.lower()}_r', 0))
                self.vu_meter.set_level(level)
        
        # Aktualizuj informacje o BPM i rate
        self.update_bpm_display()


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
        
        layout.addLayout(master_layout)
        
        # Master VU Meter
        self.master_vu = VUMeter()
        layout.addWidget(self.master_vu)
        
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
    
    def update_crossfader(self, value):
        crossfader_value = value / 100.0
        self.mixer.set_crossfader(crossfader_value)
    
    def update_master_volume(self, value):
        volume = value / 100.0
        self.mixer.set_master_volume(volume)
        self.master_label.setText(f"{value}%")
    
    def update_display(self):
        """Aktualizuje VU meter mastera."""
        peak_levels = self.mixer.get_peak_levels()
        master_level = max(peak_levels.get('master_l', 0), peak_levels.get('master_r', 0))
        self.master_vu.set_level(master_level)


class PlaylistWidget(TransparentWidget):
    """Widget listy utworów."""
    
    track_selected = Signal(str)  # file_path
    
    def __init__(self):
        super().__init__(opacity=0.7)
        self.music_library = MusicLibrary()
        self.setup_ui()
        self.setup_connections()
    
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
        
        # Lista utworów
        self.track_list = QListWidget()
        self.track_list.setMaximumHeight(200)
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
            QListWidget {
                background-color: rgba(30, 30, 30, 200);
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
            }
            QListWidget::item {
                padding: 3px;
                border-bottom: 1px solid #444;
            }
            QListWidget::item:selected {
                background-color: rgba(0, 120, 215, 150);
            }
            QListWidget::item:hover {
                background-color: rgba(70, 70, 70, 100);
            }
        """)
    
    def setup_connections(self):
        self.folder_btn.clicked.connect(self.select_folder)
        self.track_list.itemDoubleClicked.connect(self.on_track_double_clicked)
        self.music_library.scan_finished.connect(self.on_scan_finished)
        self.music_library.track_added.connect(self.on_track_added)
    
    def select_folder(self):
        folder = self.music_library.select_folder(self)
        if folder:
            self.track_list.clear()
            self.music_library.scan_folder(folder)
    
    def on_scan_finished(self, count):
        print(f"Skanowanie zakończone: {count} utworów")
    
    def on_track_added(self, track_info):
        item = QListWidgetItem(track_info.name)
        item.setData(Qt.UserRole, track_info.file_path)
        self.track_list.addItem(item)
    
    def on_track_double_clicked(self, item):
        file_path = item.data(Qt.UserRole)
        if file_path:
            self.track_selected.emit(file_path)


class DJLiteMainWindow(QMainWindow):
    """Główne okno DJ Lite - przezroczyste, zawsze na wierzchu."""
    
    def __init__(self):
        super().__init__()
        self.mixer = DJMixer()
        self.setup_window()
        self.setup_ui()
        self.setup_timer()
        
        # Uruchom audio
        self.mixer.start_audio()
    
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
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Lewy panel - Deck A
        self.deck_a_widget = DeckWidget("A", self.mixer)
        main_layout.addWidget(self.deck_a_widget)
        
        # Środkowy panel - Mixer + Playlist
        center_layout = QVBoxLayout()
        
        self.mixer_widget = MixerWidget(self.mixer)
        center_layout.addWidget(self.mixer_widget)
        
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
        
        # Ustaw referencje między deckami dla funkcji SYNC
        self.deck_a_widget.set_other_deck(self.mixer.deck_b)
        self.deck_b_widget.set_other_deck(self.mixer.deck_a)
        
        # Połącz sygnały
        self.playlist_widget.track_selected.connect(self.load_track_to_deck)
    
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
    
    def update_turntable_animation(self):
        """Aktualizuje animację obracających się płyt."""
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
    
    def load_track_to_deck(self, file_path: str):
        """Ładuje utwór do aktywnego decka (placeholder)."""
        # Prosta logika - ładuj do decka A jeśli pusty, inaczej do B
        if not self.mixer.deck_a.is_loaded():
            if self.mixer.deck_a.load_track(file_path):
                self.deck_a_widget.track_info.setText(self.mixer.deck_a.track_name)
        else:
            if self.mixer.deck_b.load_track(file_path):
                self.deck_b_widget.track_info.setText(self.mixer.deck_b.track_name)
    
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
        self.mixer.stop_audio()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DJLiteMainWindow()
    window.show()
    sys.exit(app.exec())