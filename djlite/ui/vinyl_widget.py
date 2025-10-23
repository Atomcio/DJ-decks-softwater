from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont
import math


class VinylWidget(QWidget):
    """Widget animowanej jogi vinylowej z znacznikiem obrotu."""
    
    def __init__(self, deck=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 120)
        self.deck = deck  # Referencja do deck dla synchronizacji
        
        # Stan animacji
        self.is_rotating = False
        self.rotation_angle = 0.0
        self.base_rpm = 33.33  # RPM standardowego vinylu
        
        # Timer dla animacji
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.update_rotation)
        self.animation_timer.setInterval(16)  # ~60 FPS
        
    def start_rotation(self):
        """Rozpoczyna obrót jogi."""
        self.is_rotating = True
        self.animation_timer.start()
        
    def stop_rotation(self):
        """Zatrzymuje obrót jogi."""
        self.is_rotating = False
        self.animation_timer.stop()
        
    def set_deck(self, deck):
        """Ustawia referencję do deck dla synchronizacji z odtwarzaniem."""
        self.deck = deck
        
    def update_rotation(self):
        """Aktualizuje kąt obrotu zgodnie z pozycją utworu."""
        if self.is_rotating and self.deck and self.deck.is_playing:
            # Oblicz prędkość obrotu na podstawie tempa decka
            tempo_ratio = getattr(self.deck, 'rate_smooth', 1.0)
            rpm = self.base_rpm * tempo_ratio
            
            # Konwersja RPM na stopnie na klatkę (16ms)
            degrees_per_frame = (rpm * 360) / (60 * 1000 / 16)
            
            self.rotation_angle += degrees_per_frame
            if self.rotation_angle >= 360:
                self.rotation_angle -= 360
            elif self.rotation_angle < 0:
                self.rotation_angle += 360
                
            self.update()
        
    def paintEvent(self, event):
        """Rysuje jogę vinylową z animowanym znacznikiem."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        center_x = self.width() // 2
        center_y = self.height() // 2
        radius = min(center_x, center_y) - 5
        
        # Rysuj główny dysk vinylowy
        self._draw_vinyl_disc(painter, center_x, center_y, radius)
        
        # Rysuj etykietę w centrum
        self._draw_center_label(painter, center_x, center_y, radius * 0.3)
        
        # Rysuj znacznik obrotu
        self._draw_rotation_marker(painter, center_x, center_y, radius)
        
        # Rysuj otwór w centrum
        self._draw_center_hole(painter, center_x, center_y)
        
        # Important: End the painter
        painter.end()
        
    def _draw_vinyl_disc(self, painter, center_x, center_y, radius):
        """Rysuje główny dysk vinylowy z gradientem."""
        # Gradient dla realistycznego wyglądu
        gradient = QRadialGradient(center_x, center_y, radius)
        gradient.setColorAt(0.0, QColor(40, 40, 40))
        gradient.setColorAt(0.7, QColor(20, 20, 20))
        gradient.setColorAt(1.0, QColor(10, 10, 10))
        
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
        
        # Rysuj koncentryczne kręgi (imitacja rowków)
        painter.setPen(QPen(QColor(30, 30, 30), 1))
        for i in range(3, 8):
            circle_radius = radius * (i / 10.0)
            painter.drawEllipse(center_x - circle_radius, center_y - circle_radius, 
                              circle_radius * 2, circle_radius * 2)
                              
    def _draw_center_label(self, painter, center_x, center_y, radius):
        """Rysuje czerwoną etykietę w centrum."""
        # Gradient dla etykiety
        gradient = QRadialGradient(center_x, center_y, radius)
        gradient.setColorAt(0.0, QColor(220, 70, 70))
        gradient.setColorAt(0.8, QColor(180, 50, 50))
        gradient.setColorAt(1.0, QColor(140, 30, 30))
        
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(100, 20, 20), 1))
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
        
    def _draw_rotation_marker(self, painter, center_x, center_y, radius):
        """Rysuje znacznik obrotu (biała kreska)."""
        painter.setPen(QPen(self._marker_color, 3))
        
        # Oblicz pozycję znacznika na podstawie kąta obrotu
        angle_rad = math.radians(self.rotation_angle)
        marker_start_radius = radius * 0.35
        marker_end_radius = radius * 0.85
        
        start_x = center_x + marker_start_radius * math.cos(angle_rad)
        start_y = center_y + marker_start_radius * math.sin(angle_rad)
        end_x = center_x + marker_end_radius * math.cos(angle_rad)
        end_y = center_y + marker_end_radius * math.sin(angle_rad)
        
        painter.drawLine(start_x, start_y, end_x, end_y)
        
        # Dodaj mały punkt na końcu znacznika
        painter.setBrush(QBrush(self._marker_color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(end_x - 2, end_y - 2, 4, 4)
        
    def _draw_center_hole(self, painter, center_x, center_y):
        """Rysuje otwór w centrum płyty."""
        hole_radius = 8
        painter.setBrush(QBrush(QColor(0, 0, 0)))
        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.drawEllipse(center_x - hole_radius, center_y - hole_radius, 
                          hole_radius * 2, hole_radius * 2)
        
    def get_rotation_angle(self):
        """Zwraca aktualny kąt obrotu."""
        return self.rotation_angle