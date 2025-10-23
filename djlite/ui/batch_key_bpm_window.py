"""Okno analizy batch BPM i klucza dla całego folderu muzyki."""

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
from audio.batch_key_bpm_analyzer import BatchKeyBpmAnalyzer
from utils.file_browser import TrackInfo


class BatchKeyBpmWindow(QDialog):
    """Okno analizy BPM i klucza dla całego folderu."""
    
    # Sygnały
    analysis_finished = Signal(dict)  # Wszystkie analizy zakończone
    track_analyzed = Signal(str, float, str)  # file_path, bpm, key
    
    def __init__(self, tracks: List[TrackInfo], parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.analyzer = BatchKeyBpmAnalyzer(self)
        self.is_analyzing = False
        self.current_index = 0  # Aktualny indeks analizowanego pliku
        self.results = {}  # file_path -> {'bpm': float, 'key': str}
        
        self.setup_ui()
        self.setup_connections()
        
        # Auto-start analizy
        QTimer.singleShot(500, self.start_analysis)
    
    def setup_ui(self):
        """Konfiguruje interfejs użytkownika."""
        self.setWindowTitle(f"Analiza BPM i Klucza - {len(self.tracks)} utworów")
        self.setModal(True)
        self.resize(800, 600)
        
        # Layout główny
        layout = QVBoxLayout(self)
        
        # Nagłówek
        header_layout = QHBoxLayout()
        
        title_label = QLabel("🎵 Analiza BPM i Klucza Muzycznego")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # Przycisk pauzy
        self.pause_btn = QPushButton("⏸️ Pauza")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setFixedSize(100, 30)
        header_layout.addWidget(self.pause_btn)
        
        # Przycisk zamknięcia
        self.close_btn = QPushButton("❌ Zamknij")
        self.close_btn.setFixedSize(100, 30)
        header_layout.addWidget(self.close_btn)
        
        layout.addLayout(header_layout)
        
        # Progress bar
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(QLabel("Postęp:"))
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(len(self.tracks))
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        layout.addLayout(progress_layout)
        
        # Status
        self.status_label = QLabel("Przygotowywanie analizy...")
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: #2b2b2b;
                color: #ffffff;
                padding: 8px;
                border-radius: 4px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.status_label)
        
        # Lista wyników
        results_label = QLabel("📊 Wyniki Analizy:")
        results_label.setFont(QFont("Arial", 12, QFont.Bold))
        layout.addWidget(results_label)
        
        self.results_list = QListWidget()
        self.results_list.setStyleSheet("""
            QListWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 4px;
                border-bottom: 1px solid #333333;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
            }
        """)
        layout.addWidget(self.results_list)
        
        # Log
        log_label = QLabel("📝 Log Analizy:")
        log_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(150)
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #cccccc;
                border: 1px solid #444444;
                border-radius: 4px;
                font-family: 'Consolas', monospace;
                font-size: 10px;
            }
        """)
        layout.addWidget(self.log_text)
        
        # Stylowanie okna
        self.setStyleSheet("""
            QDialog {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
            QPushButton:disabled {
                background-color: #666666;
                color: #999999;
            }
            QProgressBar {
                border: 1px solid #444444;
                border-radius: 4px;
                text-align: center;
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #0078d4;
                border-radius: 3px;
            }
        """)
    
    def setup_connections(self):
        """Konfiguruje połączenia sygnałów."""
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.close_btn.clicked.connect(self.close_analysis)
        
        # Połączenia z analyzer
        self.analyzer.progress.connect(self.on_file_progress)
        self.analyzer.finished_all.connect(self.on_analysis_finished)
    
    def close_analysis(self):
        """Zamyka okno analizy."""
        if self.is_analyzing:
            self.analyzer.request_stop()
            self.analyzer.wait(2000)  # Czekaj max 2 sekundy
        
        self.accept()
    
    def finish_analysis(self):
        """Kończy analizę i przygotowuje wyniki."""
        self.is_analyzing = False
        self.pause_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        
        self.status_label.setText(f"✅ Analiza zakończona - {len(self.results)} utworów przeanalizowanych")
        self.log_message("=== ANALIZA ZAKOŃCZONA ===")
        
        # Emituj sygnał z wynikami
        self.analysis_finished.emit(self.results)
    
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
        
        self.log_message("Rozpoczynanie analizy BPM i klucza...")
        
        # Przygotuj listę ścieżek
        file_paths = [track.file_path for track in self.tracks]
        self.analyzer.set_files(file_paths)
        self.analyzer.set_timeout(45)  # 45 sekund timeout per plik (więcej czasu na klucz)
        
        # Uruchom batch analyzer
        self.analyzer.start()
    
    def on_file_progress(self, file_idx: int, success: bool, bpm: float, key: str, message: str):
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
        self.results[track.file_path] = {
            'bpm': bpm if success else 0.0,
            'key': key if success else "—"
        }
        
        # Dodaj do listy wyników
        if success and bpm > 0:
            result_text = f"{track.name:<40} - {bpm:6.1f} BPM - {key}"
            color = Qt.white
        else:
            result_text = f"{track.name:<40} - FAILED"
            color = Qt.red
        
        item = QListWidgetItem(result_text)
        item.setForeground(color)
        self.results_list.addItem(item)
        self.results_list.scrollToBottom()
        
        # Log message
        self.log_message(f"[{file_idx + 1}/{len(self.tracks)}] {track.name}: {message}")
        
        # Emituj sygnał
        self.track_analyzed.emit(track.file_path, bpm if success else 0.0, key if success else "—")
    
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
    
    def closeEvent(self, event):
        """Obsługuje zamknięcie okna."""
        if self.is_analyzing:
            self.analyzer.request_stop()
            self.analyzer.wait(2000)  # Czekaj max 2 sekundy
        
        event.accept()