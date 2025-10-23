"""Robustny batch analyzer BPM z jedną kolejką QThread."""

import os
import json
import time
import logging
from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import QThread, Signal

try:
    import librosa
    import aubio
    import soundfile as sf
    import scipy.signal
except ImportError as e:
    logging.error(f"Missing audio library: {e}")
    raise


class BatchBpmAnalyzer(QThread):
    """Batch analyzer BPM z robustnym error handling i timeout."""
    
    # Sygnały
    progress = Signal(int, bool, float, str)  # file_idx, success, bpm, message
    finished_all = Signal()  # Wszystkie pliki przetworzone
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_paths: List[str] = []
        self.timeout_seconds = 30
        self.stop_flag = False
        self.pause_flag = False
    
    def set_files(self, file_paths: List[str]):
        """Ustawia listę plików do analizy."""
        self.file_paths = file_paths.copy()
    
    def set_timeout(self, seconds: int):
        """Ustawia timeout per plik."""
        self.timeout_seconds = seconds
    
    def request_stop(self):
        """Żąda zatrzymania analizy."""
        self.stop_flag = True
    
    def pause_analysis(self):
        """Wstrzymuje analizę."""
        self.pause_flag = True
    
    def resume_analysis(self):
        """Wznawia analizę."""
        self.pause_flag = False
    
    def is_paused(self) -> bool:
        """Sprawdza czy analiza jest wstrzymana."""
        return self.pause_flag
    
    def stop(self):
        """Zatrzymuje analizę (alias dla request_stop)."""
        self.request_stop()
    
    def _check_cache(self, file_path: str) -> Optional[float]:
        """Sprawdza cache BPM dla pliku.
        
        Returns:
            BPM z cache lub None jeśli brak cache
        """
        cache_path = file_path + ".bpm.json"
        
        if not os.path.exists(cache_path):
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                bpm = cache_data.get('bpm')
                if bpm is not None and bpm > 0:
                    return float(bpm)
        except Exception as e:
            logging.warning(f"Cache read error for {file_path}: {e}")
        
        return None
    
    def _analyze_bpm_with_timeout(self, file_path: str, timeout_s: float) -> Optional[float]:
        """Analizuje BPM z timeoutem i fallback."""
        path = Path(file_path)
        
        # Sprawdź cache
        cached_bpm = self._check_cache(file_path)
        if cached_bpm is not None:
            logging.info(f"Cache hit: {path.name} -> {cached_bpm:.1f} BPM")
            return cached_bpm
        
        logging.info(f"Start analysis: {path.name}")
        
        # Format guard - szybkie sprawdzenie czy plik się czyta
        try:
            info = sf.info(str(path))
            logging.debug(f"Audio info: {info.samplerate}Hz, {info.duration:.1f}s, {info.channels}ch")
        except RuntimeError as e:
            logging.error(f"Unsupported or corrupted audio: {path.name} - {e}")
            raise ValueError(f"Unsupported or corrupted audio: {path.name}")
        
        start_time = time.monotonic()
        bpm_result = None
        
        try:
            # Próba 1: aubio tempo detection
            bpm_result = self._analyze_with_aubio(path, timeout_s, start_time)
            
            if bpm_result is None:
                 # Próba 2: librosa fallback
                 if time.monotonic() - start_time < timeout_s:
                     bpm_result = self._analyze_with_librosa(path, timeout_s, start_time)
            
        except TimeoutError:
            logging.error(f"Timeout: {path.name}")
            raise
        except Exception as e:
            logging.error(f"Analysis error: {path.name} - {e}")
            raise
        
        # Heurystyka wyniku
        if bpm_result is None or bpm_result <= 0:
            logging.info(f"No BPM detected: {path.name} -> None")
            bpm_result = None
        else:
            logging.info(f"BPM detected: {path.name} -> {bpm_result:.1f} BPM")
            # Zapisz do cache
            self._save_to_cache(file_path, bpm_result)
        
        return bpm_result
    
    def _analyze_with_aubio(self, path: Path, timeout_s: float, start_time: float) -> Optional[float]:
        """Analiza BPM używając aubio z prawidłowym końcem strumienia."""
        try:
            s = aubio.source(str(path), samplerate=44100, hop_size=512)
            o = aubio.tempo("default", 2048, 512, 44100)
            beats = []
            
            while True:
                # Sprawdź timeout i stop flag
                if time.monotonic() - start_time > timeout_s:
                    raise TimeoutError("BPM analyze timeout")
                if self.stop_flag:
                    raise Exception("Analysis stopped by user")
                
                # Sprawdź pauzę
                while self.pause_flag:
                    time.sleep(0.05)
                    if self.stop_flag:
                        raise Exception("Analysis stopped by user")
                
                samples, read = s()
                if o(samples):
                    beats.append(o.get_last_s())
                
                # PRAWIDŁOWY KONIEC STRUMIENIA
                if read < 512:
                    break
            
            # Heurystyka wyniku
            if len(beats) < 2:
                return None
            
            # Oblicz BPM z beats
            if len(beats) > 1:
                intervals = [beats[i+1] - beats[i] for i in range(len(beats)-1)]
                avg_interval = sum(intervals) / len(intervals)
                if avg_interval > 0:
                    return 60.0 / avg_interval
            
            return None
            
        except Exception as e:
            logging.debug(f"Aubio analysis failed for {path.name}: {e}")
            return None
    
    def _analyze_with_librosa(self, path: Path, timeout_s: float, start_time: float) -> Optional[float]:
        """Fallback analiza BPM używając librosa."""
        try:
            # Sprawdź timeout
            if time.monotonic() - start_time > timeout_s:
                raise TimeoutError("BPM analyze timeout")
            
            # Próba załadowania z librosa
            try:
                y, sr = librosa.load(str(path), sr=22050)
            except Exception:
                # Fallback: soundfile + prosty resample
                data, sr_orig = sf.read(str(path))
                if sr_orig != 22050:
                    # Prosty resample (nie idealny, ale działa)
                    import scipy.signal
                    num_samples = int(len(data) * 22050 / sr_orig)
                    y = scipy.signal.resample(data, num_samples)
                    sr = 22050
                else:
                    y = data
                    sr = sr_orig
            
            # Sprawdź timeout ponownie
            if time.monotonic() - start_time > timeout_s:
                raise TimeoutError("BPM analyze timeout")
            
            # Analiza tempo
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            
            if tempo > 0:
                return float(tempo)
            
            return None
            
        except Exception as e:
            logging.debug(f"Librosa analysis failed for {path.name}: {e}")
            return None
    
    def _save_to_cache(self, file_path: str, bpm: float):
        """Zapisuje BPM do cache."""
        cache_path = file_path + ".bpm.json"
        try:
            cache_data = {
                'bpm': bpm,
                'timestamp': time.time()
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f)
        except Exception as e:
            logging.warning(f"Cache save error for {file_path}: {e}")
    
    def run(self):
        """Główna pętla batch analysis."""
        self.stop_flag = False
        
        # Dry-run: pokaż listę plików na starcie
        total_files = len(self.file_paths)
        if total_files > 0:
            logging.info(f"=== BATCH ANALYSIS START ===")
            logging.info(f"Total files: {total_files}")
            
            # Pokaż pierwsze 3 nazwy plików
            for i, file_path in enumerate(self.file_paths[:3]):
                file_name = Path(file_path).name
                logging.info(f"  [{i+1}] {file_name}")
            
            if total_files > 3:
                logging.info(f"  ... and {total_files - 3} more files")
            
            logging.info(f"Timeout per file: {self.timeout_seconds}s")
            logging.info(f"=== STARTING ANALYSIS ===")
        
        for file_idx, file_path in enumerate(self.file_paths):
            # Sprawdź czy zatrzymano
            if self.stop_flag:
                logging.info("Analysis stopped by user")
                break
            
            # Sprawdź pauzę
            while self.pause_flag:
                time.sleep(0.05)
                if self.stop_flag:
                    logging.info("Analysis stopped by user")
                    break
            
            if self.stop_flag:
                break
            
            file_name = Path(file_path).name
            
            try:
                # === SPRAWDŹ CACHE ===
                cached_bpm = self._check_cache(file_path)
                if cached_bpm is not None:
                    # Cache hit
                    logging.info(f"[{file_idx+1}/{total_files}] {file_name}: Cache hit -> {cached_bpm:.1f} BPM")
                    self.progress.emit(
                        file_idx, 
                        True, 
                        cached_bpm, 
                        f"Cache: {cached_bpm:.1f} BPM"
                    )
                    continue
                
                # === ANALIZA Z TIMEOUT ===
                logging.info(f"[{file_idx+1}/{total_files}] {file_name}: Starting analysis...")
                
                try:
                    bpm = self._analyze_bpm_with_timeout(file_path, self.timeout_seconds)
                    
                    # Sukces
                    if bpm is not None and bpm > 0:
                        logging.info(f"[{file_idx+1}/{total_files}] {file_name}: SUCCESS -> {bpm:.1f} BPM")
                        self.progress.emit(
                            file_idx, 
                            True, 
                            bpm, 
                            f"Sukces: {bpm:.1f} BPM"
                        )
                    else:
                        # Brak wyniku
                        logging.info(f"[{file_idx+1}/{total_files}] {file_name}: NO BPM detected")
                        self.progress.emit(
                            file_idx, 
                            False, 
                            0.0, 
                            "Nie wykryto BPM"
                        )
                        
                except TimeoutError:
                    # Timeout
                    logging.error(f"[{file_idx+1}/{total_files}] {file_name}: TIMEOUT ({self.timeout_seconds}s)")
                    self.progress.emit(
                        file_idx, 
                        False, 
                        0.0, 
                        f"Timeout ({self.timeout_seconds}s)"
                    )
                    
                except Exception as e:
                    # Błąd analizy
                    logging.error(f"[{file_idx+1}/{total_files}] {file_name}: ERROR -> {str(e)}")
                    self.progress.emit(
                        file_idx, 
                        False, 
                        0.0, 
                        f"Błąd: {str(e)}"
                    )
                    
            except Exception as e:
                # Błąd ogólny
                logging.error(f"[{file_idx+1}/{total_files}] {file_name}: GENERAL ERROR -> {str(e)}")
                self.progress.emit(
                    file_idx, 
                    False, 
                    0.0, 
                    f"Błąd: {str(e)}"
                )
        
        # Zakończ
        logging.info(f"=== BATCH ANALYSIS FINISHED ===")
        self.finished_all.emit()