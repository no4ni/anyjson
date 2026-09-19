import threading
import numpy as np
import cv2
from PyQt5.QtCore import QObject, pyqtSignal

class VideoProcessor(QObject):
    cache_ready_changed = pyqtSignal(bool)

    def __init__(self, use_hw_accel=False):
        super().__init__()
        self.use_hw_accel = use_hw_accel
        self.cap = None
        self.fps = 25.0
        self.total_frames = 0
        self.duration = 0.0
        self.frame_cache = {}       # {time_sec: frame_rgb}
        self.cache_lock = threading.Lock()
        self.cache_ready = False
        self.is_paused = False
        self.current_sec = 0.0
        self._preload_thread = None

    @property
    def is_loaded(self):
        return self.cap is not None and self.cap.isOpened()

    def load_video(self, path):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        
        backends = [cv2.CAP_FFMPEG]
        if self.use_hw_accel:
            backends.append(cv2.CAP_ANY)
            
        opened = False
        for backend in backends:
            cap = cv2.VideoCapture(path, backend)
            if cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_HW_ACCELERATION, 1)
                except Exception:
                    pass
                self.cap = cap
                opened = True
                break
                
        if not opened:
            self.cap = cv2.VideoCapture(path)
            if not self.cap.isOpened():
                print("Не удалось открыть видео")
                return False

        raw_fps = self.cap.get(cv2.CAP_PROP_FPS)
        physical_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # Коррекция аномального FPS
        if raw_fps > 100 or raw_fps <= 0:
            self.fps = 25.0
            if physical_frames > 0 and raw_fps > 100:
                estimated_duration = physical_frames / raw_fps
                self.total_frames = int(estimated_duration * self.fps)
                self.duration = estimated_duration
            else:
                self.total_frames = physical_frames
                self.duration = physical_frames / self.fps
        else:
            self.fps = raw_fps
            self.total_frames = physical_frames
            self.duration = physical_frames / self.fps if physical_frames > 0 else 0.0
            
        self.clear_cache()
        return True

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.clear_cache()

    def clear_cache(self):
        with self.cache_lock:
            self.frame_cache.clear()
        self._set_cache_ready(False)

    def _sanitize_frame(self, frame_rgb):
        """Убирает артефакты декодирования."""
        r_ch = frame_rgb[:, :, 0].astype(np.int16)
        g_ch = frame_rgb[:, :, 1].astype(np.int16)
        b_ch = frame_rgb[:, :, 2].astype(np.int16)
        
        green_anomaly = (g_ch - r_ch > 60) & (g_ch - b_ch > 60)
        if np.any(green_anomaly):
            replacement = ((r_ch + b_ch) // 2).astype(np.uint8)
            frame_rgb[green_anomaly, 0] = replacement[green_anomaly]
            frame_rgb[green_anomaly, 1] = replacement[green_anomaly]
            frame_rgb[green_anomaly, 2] = replacement[green_anomaly]
        return frame_rgb

    def set_paused(self, paused, current_sec):
        self.is_paused = paused
        self.current_sec = current_sec
        if not self.is_loaded or not self.is_paused:
            self._set_cache_ready(False)
            return
        self.clear_cache()
        start_sec = max(0.0, current_sec - 3.0)
        end_sec = min(current_sec + 3.5, self.duration)
        if end_sec <= start_sec:
            self._set_cache_ready(True)
            return
        # FRAMES seek надёжнее MSEC для MKV/VP9: MSEC-seek прыгает
        # к следующему keyframe, который может быть далеко впереди.
        seek_frame = max(0, int(start_sec * self.fps))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
        self.cap.read()  # синхронизация потока
        target_interval = 1.0 / min(self.fps, 30)
        loaded_count = 0
        MAX_CACHE = 300
        while loaded_count < MAX_CACHE:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                break
            actual_msec = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            actual_sec = actual_msec / 1000.0
            if actual_sec > end_sec:
                break
            needs_save = True
            with self.cache_lock:
                for cached_t in list(self.frame_cache.keys()):
                    if abs(cached_t - actual_sec) < target_interval * 0.6:
                        needs_save = False
                        break
            if needs_save:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame_rgb = self._sanitize_frame(frame_rgb)
                frame_scaled = cv2.resize(frame_rgb, (780, 265), interpolation=cv2.INTER_AREA)
                with self.cache_lock:
                    self.frame_cache[actual_sec] = frame_scaled
                loaded_count += 1
        
        # ИСПРАВЛЕНИЕ: разблокируем Play только если реально загрузили кадры
        if loaded_count > 0:
            self._set_cache_ready(True)
        else:
            print(f"[WARN] Не удалось загрузить ни одного кадра для окна [{start_sec}, {end_sec}]")
            self._set_cache_ready(False)  # Play остается заблокированным
            
    def _read_single_frame(self, t):
        """Быстрое чтение одного кадра по времени (для мгновенного отображения)."""
        if not self.is_loaded:
            return None
        seek_frame = max(0, int(t * self.fps))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
        ret, frame = self.cap.read()
        if ret and frame is not None:
            actual_msec = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            actual_sec = actual_msec / 1000.0
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_rgb = self._sanitize_frame(frame_rgb)
            return cv2.resize(frame_rgb, (780, 265), interpolation=cv2.INTER_AREA)
        return None

    def _sequential_preload_window(self, center_sec):
        """Фоновая дозагрузка окна [center_sec - 3.0, center_sec + 0.5]."""
        if not self.is_loaded:
            return

        window_start = max(0.0, center_sec - 3.0)
        window_end = min(center_sec + 0.5, self.duration)
        
        if window_end <= window_start:
            return

        # Seek на начало окна
        seek_frame = max(0, int(window_start * self.fps))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
        self.cap.read()  # Пропуск первого кадра после seek
        
        local_cache = {}
        loaded_count = 0
        MAX_CACHE = 300
        target_interval = 1.0 / min(self.fps, 30)
        
        while loaded_count < MAX_CACHE:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                break
                
            actual_msec = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            actual_sec = actual_msec / 1000.0
            
            if actual_sec > window_end:
                break
                
            # Фильтр дубликатов
            needs_save = True
            for cached_t in list(local_cache.keys()):
                if abs(cached_t - actual_sec) < target_interval * 0.5:
                    needs_save = False
                    break
                    
            if needs_save:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame_rgb = self._sanitize_frame(frame_rgb)
                frame_scaled = cv2.resize(frame_rgb, (780, 265), interpolation=cv2.INTER_AREA)
                local_cache[actual_sec] = frame_scaled
                loaded_count += 1

        # Merge с глобальным кэшем
        with self.cache_lock:
            keep_from = max(0.0, center_sec - 4.0)
            keys_to_del = [t for t in self.frame_cache if t < keep_from or t > window_end + 1.0]
            for t in keys_to_del:
                del self.frame_cache[t]
            self.frame_cache.update(local_cache)

    def _set_cache_ready(self, ready):
        if self.cache_ready != ready:
            self.cache_ready = ready
            self.cache_ready_changed.emit(ready)

    def get_frame(self, t, target_size=(780, 265)):
        """Получение кадра ТОЛЬКО из кэша."""
        if t < 0:
            t = 0.0
        if not self.is_loaded:
            return np.zeros((target_size[1], target_size[0], 3), dtype=np.uint8)
            
        with self.cache_lock:
            if t in self.frame_cache:
                return self.frame_cache[t]
                
            # Fallback: ближайший кадр
            if self.frame_cache:
                keys = list(self.frame_cache.keys())
                closest = min(keys, key=lambda x: abs(x - t))
                return self.frame_cache[closest]
                
        return np.zeros((target_size[1], target_size[0], 3), dtype=np.uint8)

    def get_frames_for_times(self, times, target_size=(780, 265)):
        frames = []
        for t in times:
            frames.append(self.get_frame(t, target_size))
        return frames