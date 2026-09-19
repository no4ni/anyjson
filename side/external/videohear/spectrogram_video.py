import numpy as np
from pydub import AudioSegment
from moviepy import VideoFileClip
import hashlib
import tempfile
import os

class SpectrogramCalculator:
	"""
	Вычисление спектрограммы с четырьмя разными окнами БПФ.
	"""
	def __init__(self, target_fps=200.0):
		self.target_fps = target_fps
		self.sr = 0
		self.samples = None
		self.spec_data = None
		self.total_frames = 0
		self.idx_200 = 0
		self.idx_300 = 0
		self.idx_3400 = 0
		
	def load_video(self, file_path):
		try:
			cache_dir = os.path.join(os.path.dirname(file_path), ".audio_cache")
			os.makedirs(cache_dir, exist_ok=True)
			with open(file_path, 'rb') as f:
				video_hash = hashlib.md5(f.read()).hexdigest()
			audio_cache_path = os.path.join(cache_dir, f"{video_hash}.wav")

			if os.path.exists(audio_cache_path):
				success = self.load_audio(audio_cache_path)
				# Видео не загружаем – возвращаем None для клипа
				return success, audio_cache_path if success else None, None
			else:
				video = VideoFileClip(file_path)
				if video.audio is None:
					raise ValueError("В видео отсутствует аудиодорожка")
				video.audio.write_audiofile(audio_cache_path, nbytes=2, codec='pcm_s16le')
				# НЕ закрываем video – он понадобится для кадров
				success = self.load_audio(audio_cache_path)
				return success, audio_cache_path if success else None, video
		except Exception as e:
			print(f"Ошибка загрузки видео: {e}")
			return False, None, None
		
	def load_audio(self, file_path):
		"""Загружает аудиофайл, возвращает True при успехе."""
		try:
			audio = AudioSegment.from_file(file_path)
			if audio.channels > 1:
				audio = audio.set_channels(1)
			samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
			samples = samples / (2 ** 15)
			self.sr = audio.frame_rate
			self.samples = samples
			return True
		except Exception as e:
			print(f"Ошибка загрузки аудио: {e}")
			return False

	def compute_spectrogram(self, progress_callback=None):
		"""
		Выполняет БПФ с четырьмя временными разрешениями и склеивает результат.
		progress_callback(percent, message) вызывается для обновления статуса.
		Возвращает кортеж (spec_data, idx_200, idx_300, idx_3400, total_frames).
		"""
		if self.samples is None or self.sr == 0:
			raise ValueError("Сначала загрузите аудиофайл методом load_audio()")

		samples = self.samples
		sr = self.sr
		target_fps = self.target_fps

		hop_high_mid = int(sr / target_fps)
		hop_low = hop_high_mid * 4

		# 1. Низкие частоты (20-200 Гц) – 2Гц
		window_low = int(sr * 0.5)
		if window_low % 2 != 0:
			window_low += 1
		cutoff_200 = int(200 / (sr / window_low))
		total_frames_low = (len(samples) - window_low) // hop_low

		# 2. Средние низкие (200-300 Гц) – 10 Гц
		window_mid_low = int(sr * 0.1)
		if window_mid_low % 2 != 0:
			window_mid_low += 1
		start_200_mid_low = int(200 / (sr / window_mid_low))
		end_300_mid_low = int(300 / (sr / window_mid_low))
		total_frames_mid = (len(samples) - window_mid_low) // hop_high_mid

		# 3. Средние высокие (300-3400 Гц) – 15 Гц
		window_mid_high = int(sr * 0.066)
		if window_mid_high % 2 != 0:
			window_mid_high += 1
		start_300_mid_high = int(300 / (sr / window_mid_high))
		end_3400_mid_high = int(3400 / (sr / window_mid_high))

		# 4. Высокие частоты (3400-20000 Гц) – 200 Гц
		window_high = int(sr * 0.005)
		if window_high % 2 != 0:
			window_high += 1
		start_3400_high = int(3400 / (sr / window_high))
		n_high_bins = window_high // 2 + 1

		total_loops = total_frames_low + total_frames_mid * 3
		current_loop = 0

		def update_progress(percent, msg=""):
			if progress_callback:
				progress_callback(percent, msg)

		# Параметры экспоненциального преобразования
		k = 0.02
		scale = 1.0 - np.exp(-k * 255.0)

		# ---- БПФ для низких частот (20-200) ----
		low_slices = np.zeros((cutoff_200, total_frames_low), dtype=np.float32)
		window_low_win = np.hanning(window_low)
		for i in range(total_frames_low):
			start = i * hop_low - window_low // 2
			if start < 0:
				seg = np.zeros(window_low, dtype=np.float32)
				seg[-start:] = samples[:window_low + start]
			else:
				seg = samples[start:start + window_low]
			seg = seg * window_low_win
			spec = np.abs(np.fft.rfft(seg))[:cutoff_200]
			dB = 20 * np.log10(spec + 1e-6)
			dB_clipped = np.clip(dB, -120.0, 80.0)
			val = -np.log1p(-((dB_clipped + 120.0) / 200.0) * scale) / k
			low_slices[:, i] = val
			current_loop += 1
			if current_loop % max(1, total_loops // 100) == 0:
				update_progress(int(current_loop * 100 / total_loops), "Низкие частоты...")

		# ---- БПФ для средних низких (200-300) ----
		mid_low_slices = np.zeros((end_300_mid_low - start_200_mid_low, total_frames_mid), dtype=np.float32)
		window_mid_low_win = np.hanning(window_mid_low)
		for i in range(total_frames_mid):
			start = i * hop_high_mid - window_mid_low // 2
			if start < 0:
				seg = np.zeros(window_mid_low, dtype=np.float32)
				seg[-start:] = samples[:window_mid_low + start]
			else:
				seg = samples[start:start + window_mid_low]
			seg = seg * window_mid_low_win
			spec = np.abs(np.fft.rfft(seg))[start_200_mid_low:end_300_mid_low]
			dB = 20 * np.log10(spec + 1e-6)
			dB_clipped = np.clip(dB, -120.0, 80.0)
			val = -np.log1p(-((dB_clipped + 120.0) / 200.0) * scale) / k
			mid_low_slices[:, i] = val
			current_loop += 1
			if current_loop % max(1, total_loops // 100) == 0:
				update_progress(int(current_loop * 100 / total_loops), "200-300 Гц...")

		# ---- БПФ для средних высоких (300-3400) ----
		mid_high_slices = np.zeros((end_3400_mid_high - start_300_mid_high, total_frames_mid), dtype=np.float32)
		window_mid_high_win = np.hanning(window_mid_high)
		for i in range(total_frames_mid):
			start = i * hop_high_mid - window_mid_high // 2
			if start < 0:
				seg = np.zeros(window_mid_high, dtype=np.float32)
				seg[-start:] = samples[:window_mid_high + start]
			else:
				seg = samples[start:start + window_mid_high]
			seg = seg * window_mid_high_win
			spec = np.abs(np.fft.rfft(seg))[start_300_mid_high:end_3400_mid_high]
			dB = 20 * np.log10(spec + 1e-6)
			dB_clipped = np.clip(dB, -120.0, 80.0)
			val = -np.log1p(-((dB_clipped + 120.0) / 200.0) * scale) / k
			mid_high_slices[:, i] = val
			current_loop += 1
			if current_loop % max(1, total_loops // 100) == 0:
				update_progress(int(current_loop * 100 / total_loops), "300-3400 Гц...")

		# ---- БПФ для высоких частот (3400-20000) ----
		high_slices = np.zeros((n_high_bins - start_3400_high, total_frames_mid), dtype=np.float32)
		window_high_win = np.hanning(window_high)
		for i in range(total_frames_mid):
			start = i * hop_high_mid - window_high // 2
			if start < 0:
				seg = np.zeros(window_high, dtype=np.float32)
				seg[-start:] = samples[:window_high + start]
			else:
				seg = samples[start:start + window_high]
			seg = seg * window_high_win
			spec = np.abs(np.fft.rfft(seg))[start_3400_high:]
			dB = 20 * np.log10(spec + 1e-6)
			dB_clipped = np.clip(dB, -120.0, 80.0)
			val = -np.log1p(-((dB_clipped + 120.0) / 200.0) * scale) / k
			high_slices[:, i] = val
			current_loop += 1
			if current_loop % max(1, total_loops // 100) == 0:
				update_progress(int(current_loop * 100 / total_loops), "3400-20000 Гц...")

		# ---- Интерполяция низких частот до сетки 200 FPS ----
		update_progress(100, "Интерполяция...")
		low_resampled = np.zeros((cutoff_200, total_frames_mid), dtype=np.float32)
		low_times = np.arange(total_frames_low) * hop_low
		target_times = np.arange(total_frames_mid) * hop_high_mid
		for i in range(cutoff_200):
			low_resampled[i, :] = np.interp(target_times, low_times, low_slices[i, :])

		# Сохраняем индексы границ
		self.idx_200 = cutoff_200
		self.idx_300 = cutoff_200 + (end_300_mid_low - start_200_mid_low)
		self.idx_3400 = self.idx_300 + (end_3400_mid_high - start_300_mid_high)

		# Склеиваем 4 диапазона
		self.spec_data = np.vstack((low_resampled, mid_low_slices, mid_high_slices, high_slices))

		# ---- Кусочно-линейная растяжка динамического диапазона ----
		X = self.spec_data
		mask_low = X < 35
		X[mask_low] = (X[mask_low] / 35.0) * 10.0
		mask_high = X > 68
		X[mask_high] = 250.0 + (X[mask_high] - 68.0) * (5.0 / 187.0)
		mask_mid = (X >= 35) & (X <= 68)
		X[mask_mid] = 10.0 + (X[mask_mid] - 35.0) * 4.8

		self.total_frames = total_frames_mid
		update_progress(100, "Готово")
		return self.spec_data, self.idx_200, self.idx_300, self.idx_3400, self.total_frames