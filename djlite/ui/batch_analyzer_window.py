"""Okno analizy batch BPM dla całego folderu muzyki."""

import os
from typing import List, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QListWidget, QListWidgetItem, QTextEdit
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

import sys
import os
from pathlib import Path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio.batch_bpm_analyzer import BatchBpmAnalyzer
from utils.file_browser import TrackInfo


class BatchAnalyzerWindow(QDialog):
    """Okno analizy BPM dla całego folderu."""
    
    # Sygnały
    analysis_finished = Signal(dict)  # Wszystkie analizy zakończone
    track_analyzed = Signal(str, float)  # file_path, bpm
    
    def __init__(self, tracks: List[TrackInfo], parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.analyzer = BatchBpmAnalyzer(self)
        self.is_analyzing = False
        self.current_index = 0  # Aktualny indeks analizowanego pliku
        self.results = {}  # file_path -> bpm
        
        self.setup_ui()
        self.setup_connections()
        
        # Auto-start analizy
        QTimer.singleShot(500, self.start_analysis)
    
    def setup_ui(self):
        """Konfiguruje interfejs użytkownika."""
        self.setWindowTitle("Analiza BPM - Batch Processing")
        self.setFixedSize(500, 400)
        self.setModal(True)
        
        # Ustaw styl okna
        self.setStyleSheet("""
            QDialog {
                background-color: #2b2b2b;
                color: white;
            }
            QLabel {
                color: white;
                font-size: 12px;
            }
            QPushButton {
                background-color: #404040;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:pressed {
                background-color: #353535;
            }
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                text-align: center;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
            QListWidget {
                background-color: #1e1e1e;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 4px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background-color: #404040;
            }
            QTextEdit {
                background-color: #1e1e1e;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                font-family: 'Consolas', monospace;
                font-size: 10px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Nagłówek
        header = QLabel(f"Analizowanie {len(self.tracks)} utworów...")
        header.setFont(QFont("Arial", 14, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, len(self.tracks))
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Przygotowywanie...")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        # Lista wyników
        results_label = QLabel("Wyniki analizy:")
        layout.addWidget(results_label)
        
        self.results_list = QListWidget()
        self.results_list.setMaximumHeight(150)
        layout.addWidget(self.results_list)
        
        # Log area
        log_label = QLabel("Log:")
        layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(80)
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        
        # Przyciski
        buttons_layout = QHBoxLayout()
        
        self.pause_btn = QPushButton("⏸️ Pauza")
        self.pause_btn.setEnabled(False)
        buttons_layout.addWidget(self.pause_btn)
        
        self.stop_btn = QPushButton("⏹️ Stop")
        buttons_layout.addWidget(self.stop_btn)
        
        buttons_layout.addStretch()
        
        self.close_btn = QPushButton("Zamknij")
        self.close_btn.setEnabled(False)
        buttons_layout.addWidget(self.close_btn)
        
        layout.addLayout(buttons_layout)
    
    def setup_connections(self):
        """Łączy sygnały z slotami."""
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_analysis)
        self.close_btn.clicked.connect(self.accept)
        
        # Podłącz sygnały batch analyzer
        self.analyzer.progress.connect(self.on_file_progress)
        self.analyzer.finished_all.connect(self.on_analysis_finished)
    
    def log_message(self, message: str):
        """Dodaje wiadomość do logu."""
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
    
    def start_analysis(self):
        """Rozpoczyna analizę batch."""
        if self.is_analyzing or not self.tracks:
            return
        
        self.is_analyzing = True
        self.pause_btn.setEnabled(True)
        self.close_btn.setEnabled(False)
        
        self.log_message("Rozpoczynanie analizy batch...")
        
        # Przygotuj listę ścieżek
        file_paths = [track.file_path for track in self.tracks]
        self.analyzer.set_files(file_paths)
        self.analyzer.set_timeout(30)  # 30 sekund timeout per plik
        
        # Uruchom batch analyzer
        self.analyzer.start()
    
    def on_file_progress(self, file_idx: int, success: bool, bpm: float, message: str):
        """Obsługuje progress z batch analyzer."""
        if file_idx >= len(self.tracks):
            return
        
        track = self.tracks[file_idx]
        self.current_index = file_idx  # Aktualizuj aktualny indeks
        
        # Aktualizuj progress bar
        self.progress_bar.setValue(file_idx + 1)
        
        # Aktualizuj status
        self.status_label.setText(f"Analizowanie: {track.name}")
        
        # Zapisz wynik
        self.results[track.file_path] = bpm if success else 0.0
        
        # Dodaj do listy wyników
        if success and bpm > 0:
            result_text = f"{track.name} - {bpm:.1f} BPM"
            color = Qt.white
        else:
            result_text = f"{track.name} - FAILED"
            color = Qt.red
        
        item = QListWidgetItem(result_text)
        item.setForeground(color)
        self.results_list.addItem(item)
        self.results_list.scrollToBottom()
        
        # Log message
        self.log_message(f"[{file_idx + 1}/{len(self.tracks)}] {track.name}: {message}")
        
        # Emituj sygnał
        self.track_analyzed.emit(track.file_path, bpm if success else 0.0)
    
    def on_analysis_finished(self):
        """Obsługuje zakończenie całej analizy batch."""
        self.finish_analysis()
    
    def toggle_pause(self):
        """Przełącza pauzę analizy."""
        if not self.analyzer.is_paused():
            # Wstrzymaj
            self.analyzer.pause_analysis()
            self.pause_btn.setText("▶️ Wznów")
            self.status_label.setText("Analiza wstrzymana")
            self.log_message("Analiza wstrzymana")
        else:
            # Wznów
            self.analyzer.resume_analysis()
            self.pause_btn.setText("⏸️ Pauza")
            self.status_label.setText("Wznawianie analizy...")
            self.log_message("Analiza wznowiona")
    
    def stop_analysis(self):
        """Zatrzymuje analizę."""
        self.is_analyzing = False
        self.analyzer.request_stop()
        
        self.status_label.setText("Analiza zatrzymana")
        self.log_message("Analiza zatrzymana przez użytkownika")
        
        self.pause_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
    
    def finish_analysis(self):
        """Kończy analizę i aktualizuje UI."""
        self.is_analyzing = False
        
        # Aktualizuj UI
        self.progress_bar.setValue(len(self.tracks))
        analyzed_count = len([r for r in self.results.values() if r > 0])
        self.status_label.setText(f"Analiza zakończona - {analyzed_count}/{len(self.tracks)} utworów")
        
        self.pause_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        
        self.log_message(f"Analiza zakończona: {analyzed_count}/{len(self.tracks)} utworów")
        
        # Emituj sygnał zakończenia
        self.analysis_finished.emit(self.results)
    
    def get_results(self) -> dict:
        """Zwraca wyniki analizy."""
        return self.results.copy()
    
    def closeEvent(self, event):
        """Obsługuje zamknięcie okna."""
        if self.is_analyzing:
            self.stop_analysis()
        
        self.analyzer.stop()
        event.accept()