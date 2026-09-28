import sys
import os
import json
import hashlib
import gc
import glob
import pygame
import numpy as np
import cv2
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
							 QHBoxLayout, QPushButton, QFileDialog, QLabel,
							 QLineEdit, QSplitter, QInputDialog, QMessageBox)
from PyQt5.QtCore import Qt, QRect, QTimer
from PyQt5.QtGui import QPainter, QImage, QPixmap, QFont, QFontMetrics, QColor, QPen
import io
from PyQt5.QtCore import QBuffer, QIODevice
from PIL import Image
import re
import win32clipboard
import pysubs2

def _get_etalons_dir():
    """Папка эталонов: env ANYJSON_ETALONS_DIR, иначе локальная."""
    env = os.environ.get("ANYJSON_ETALONS_DIR")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "etalons")

import os as _os_progress
_PROGRESS_TO_STDERR = _os_progress.environ.get("QT_QPA_PLATFORM") == "offscreen"

from spectrogram_video import SpectrogramCalculator
from spectrumrgbvideo import MultiChannelTransformer, get_default_points, enhance_mid_frequencies
from video8 import VideoProcessor

# Нормализованные границы для 12 нот (от 0 до 1)
# Соответствуют пиксельным интервалам при высоте 163 пикселя
NOTE_BOUNDARIES_NORM = [
	(0/163, 13/163),
	(14/163, 27/163),
	(28/163, 39/163),
	(40/163, 52/163),
	(53/163, 66/163),
	(67/163, 78/163),
	(79/163, 92/163),
	(93/163, 107/163),
	(108/163, 122/163),
	(123/163, 136/163),
	(137/163, 150/163),
	(151/163, 162/163)
]


# ============================================================
#  Загрузка субтитров (.srt / .vtt / .webvtt)
# ============================================================
def clean_subtitle_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    return text


def subtitle_score(text):
    t = clean_subtitle_text(text).strip()
    if t == "":
        return 0
    if all(ch in "-\u2014\u2013_ \n" for ch in t):
        return 1
    return 2


def find_subtitle_file(video_path):
    base = os.path.splitext(video_path)[0]
    for ext in (".srt", ".SRT", ".vtt", ".VTT", ".webvtt", ".WEBVTT"):
        p = base + ext
        if os.path.exists(p):
            return p
    return None


def extract_subtitle_via_ffmpeg(video_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return None
    tmp_srt = os.path.join(os.path.dirname(video_path), "_temp_subs.srt")
    cmd = ["ffmpeg", "-i", video_path, "-map", "0:s:0",
           "-c:s", "srt", "-y", tmp_srt]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return tmp_srt
    except Exception:
        return None


def load_subtitles(video_path):
    sub_path = find_subtitle_file(video_path) or extract_subtitle_via_ffmpeg(video_path)
    if not sub_path:
        return [], None
    try:
        subs = list(pysubs2.load(sub_path, encoding="utf-8-sig"))
    except Exception:
        try:
            subs = list(pysubs2.load(sub_path, encoding="utf-8-sig", format_="vtt"))
        except Exception:
            subs = []
    return subs, sub_path


class SpectrogramWidget(QWidget):
	def __init__(self, parent=None):
		super().__init__(parent)
		self.setMinimumSize(800, 400)
		self.note_boundaries_norm = []   # список кортежей (start, end) в [0,1]
		self.img_width = 780
		self.img_height = 320
		self.image = self.create_blank_image(self.img_width, self.img_height)
		self.time_start = 0.0
		self.time_end = 3.0
		self.row_labels = []
		self.left_axis_pixmap = None
		self.subtitle_text = ""
		self.tembr_list = []
		self.speech_list = []
		self.line_y_pixels = None
		self.line_valid = None

		# Новые атрибуты для динамической левой шкалы
		self.note_rows = []			 # список из 12 строк (уровней)
		self.stable_flags = [False] * 12  # булев массив

	def create_blank_image(self, w, h):
		data = np.zeros((h, w, 3), dtype=np.uint8)
		data = np.ascontiguousarray(data)
		return QImage(data.tobytes(), w, h, w * 3, QImage.Format_RGB888)

	def set_spectrogram_rgb(self, rgb_data, time_start=None, time_end=None, row_labels=None):
		if rgb_data is None or rgb_data.shape != (self.img_height, self.img_width, 3):
			self.image = self.create_blank_image(self.img_width, self.img_height)
			self.update()
			return
		data = np.clip(rgb_data, 0, 255).astype(np.uint8)
		data = np.ascontiguousarray(data)
		self.image = QImage(data.tobytes(), self.img_width, self.img_height,
							self.img_width * 3, QImage.Format_RGB888)
		if time_start is not None:
			self.time_start = time_start
		if time_end is not None:
			self.time_end = time_end
		if row_labels is not None:
			self.row_labels = row_labels
		self.update()

	def set_subtitle(self, text):
		self.subtitle_text = text
		self.update()

	def set_etalon_data(self, tembr_list, speech_list):
		self.tembr_list = tembr_list
		self.speech_list = speech_list
		self.update()

	def set_line_data(self, y_pixels, valid_mask):
		self.line_y_pixels = y_pixels
		self.line_valid = valid_mask
		self.update()

	def set_stable_flags(self, flags):
		self.stable_flags = flags
		self.update()

	def clear(self):
		self.image = self.create_blank_image(self.img_width, self.img_height)
		self.time_start = 0.0
		self.time_end = 3.0
		self.row_labels = []
		self.subtitle_text = ""
		self.tembr_list = []
		self.speech_list = []
		self.line_y_pixels = None
		self.line_valid = None
		self.stable_flags = [False] * 12
		self.update()

	def paintEvent(self, event):
		painter = QPainter(self)
		painter.fillRect(self.rect(), Qt.black)

		LEFT_MARGIN = 70
		BOTTOM_MARGIN = 25
		image_rect = QRect(LEFT_MARGIN, 0,
						   self.width() - LEFT_MARGIN,
						   self.height() - BOTTOM_MARGIN)

		if self.image is not None:
			painter.setRenderHint(QPainter.SmoothPixmapTransform)
			painter.drawImage(image_rect, self.image)

		if self.line_y_pixels is not None:
			try:
				img_h = self.img_height
				w = image_rect.width()
				h = image_rect.height()
				left = image_rect.left()
				top = image_rect.top()

				x_coords = np.linspace(left, left + w, len(self.line_y_pixels))
				y_coords = top + (self.line_y_pixels / img_h) * h

				painter.setPen(QPen(Qt.white, 5))
				for i in range(len(x_coords) - 1):
					if self.line_valid[i] and self.line_valid[i+1]:
						painter.drawLine(int(x_coords[i]), int(y_coords[i]),
										 int(x_coords[i+1]), int(y_coords[i+1]))
			except Exception as e:
				print(f"Ошибка отрисовки готовой линии: {e}")

		painter.setPen(Qt.white)
		duration = self.time_end - self.time_start
		if duration > 0 and self.image is not None:
			num_ticks = 6
			w = image_rect.width()
			left = image_rect.left()
			for i in range(num_ticks):
				t = self.time_start + (duration * i / (num_ticks - 1))
				x = left + (w * i / (num_ticks - 1))
				if i == 0:
					x += 40
				if i == num_ticks - 1:
					x -= 40
				painter.drawText(int(x) - 15, self.height() - 5, f"{t:.1f}")
			painter.drawText(self.width() - 40, self.height() - 20, 40, 20,
							 Qt.AlignRight | Qt.AlignVCenter, "сек.")

		# --------------------------------------------------------------
		# Динамическая левая шкала (только стабильные ноты)
		# --------------------------------------------------------------
		if self.left_axis_pixmap is None:
			script_dir = os.path.dirname(os.path.abspath(__file__))
			pixmap_path = os.path.join(script_dir, "leftaxisnotes.png")
			if os.path.exists(pixmap_path):
				self.left_axis_pixmap = QPixmap(pixmap_path)

		# Если есть картинка, рисуем левую часть (0-23) всегда
		if (self.left_axis_pixmap is not None and not self.left_axis_pixmap.isNull()):
			scale = self.height() / 850.0
			y_orig = int(365 * scale)
			h_orig = int(280 * scale)
			pix_width = self.left_axis_pixmap.width()
			pix_height = self.left_axis_pixmap.height()

			# 1) Всегда рисуем левую часть (первые 23 пикселя исходной картинки)
			painter.drawPixmap(
				0, y_orig, 23, h_orig,
				self.left_axis_pixmap,
				0, 0, 23, pix_height
			)

			# 2) Для стабильных нот рисуем фрагменты в правой части (24-70)
			if pix_width > 23:
				src_x_start = 23
				src_width = pix_width - 23
				dest_x = 24
				dest_width = LEFT_MARGIN - 24
				for idx, flag in enumerate(self.stable_flags):
					if flag and idx < len(self.note_boundaries_norm):
						start_norm, end_norm = self.note_boundaries_norm[idx]
						src_y0 = start_norm * pix_height
						src_y1 = end_norm * pix_height
						dest_y0 = y_orig + start_norm * h_orig
						dest_y1 = y_orig + end_norm * h_orig
						# Ограничения, чтобы не вылезать за границы
						src_y0 = max(0, min(pix_height, src_y0))
						src_y1 = max(0, min(pix_height, src_y1))
						dest_y0 = max(y_orig, min(y_orig + h_orig, dest_y0))
						dest_y1 = max(y_orig, min(y_orig + h_orig, dest_y1))
						if src_y1 > src_y0 and dest_y1 > dest_y0:
							painter.drawPixmap(
								dest_x, int(dest_y0), dest_width, int(dest_y1 - dest_y0),
								self.left_axis_pixmap,
								src_x_start, int(src_y0), src_width, int(src_y1 - src_y0)
							)

		# --------------------------------------------------------------
		# Подписи тембра и речи (остаются без изменений)
		# --------------------------------------------------------------
		def draw_wrapped_text(painter, text, x, y, max_width, line_height):
			if not text:
				return 0
			fm = painter.fontMetrics()
			words = text.split()
			lines = []
			current_line = []
			for word in words:
				if fm.horizontalAdvance(word) > max_width:
					if current_line:
						lines.append(' '.join(current_line))
						current_line = []
					chars = list(word)
					temp_line = []
					for ch in chars:
						test_line = ''.join(temp_line + [ch])
						if fm.horizontalAdvance(test_line) <= max_width:
							temp_line.append(ch)
						else:
							if temp_line:
								lines.append(''.join(temp_line))
							temp_line = [ch]
					if temp_line:
						lines.append(''.join(temp_line))
				else:
					test_line = ' '.join(current_line + [word])
					if fm.horizontalAdvance(test_line) <= max_width:
						current_line.append(word)
					else:
						if current_line:
							lines.append(' '.join(current_line))
						current_line = [word]
			if current_line:
				lines.append(' '.join(current_line))

			for i, line in enumerate(lines):
				painter.drawText(x, y + i * line_height, line)
			return len(lines)

		font = QFont("Arial", 10, QFont.Bold)
		painter.setFont(font)
		max_text_width = LEFT_MARGIN
		line_height = painter.fontMetrics().height() + 2

		def draw_etalon_list(etalon_list, start_x, start_y, max_width, line_height):
			if not etalon_list:
				return
			n = len(etalon_list)
			if n == 1:
				colors = [Qt.white]
			else:
				min_d = etalon_list[0]['dist']
				max_d = etalon_list[-1]['dist']
				if max_d == min_d:
					colors = [Qt.white] * n
				else:
					colors = []
					for item in etalon_list:
						ratio = (item['dist'] - min_d) / (max_d - min_d)
						brightness = int(255 * (1 - ratio))
						colors.append(QColor(brightness, brightness, brightness))

			current_y = start_y
			for idx, item in enumerate(etalon_list):
				painter.setPen(colors[idx])
				num_lines = draw_wrapped_text(painter, item['name'],
											  start_x, current_y,
											  max_width, line_height)
				current_y += num_lines * line_height

		painter.setPen(Qt.white)
		tembr_y = image_rect.top() + 15
		painter.drawText(5, tembr_y, "Тембр:")
		if self.tembr_list:
			draw_etalon_list(self.tembr_list, 2, tembr_y + line_height + 2, max_text_width, line_height)

		speech_y = image_rect.top() + int(0.8 * image_rect.height()) + 15
		painter.setPen(Qt.white)
		painter.drawText(5, speech_y, "Речь:")
		if self.speech_list:
			draw_etalon_list(self.speech_list, 2, speech_y + line_height + 2, max_text_width, line_height)

		if self.subtitle_text:
			painter.setPen(Qt.black)
			painter.setFont(QFont("Arial", 18))

			margin = 10
			duration = self.time_end - self.time_start
			image_width = self.width() - LEFT_MARGIN
			if duration > 0:
				offset_pixels = int(image_width * (0.0 - self.time_start) / duration)
			else:
				offset_pixels = 0
			if offset_pixels < 0:
				offset_pixels = 0

			x_start = LEFT_MARGIN + offset_pixels
			width_remaining = image_width - offset_pixels
			if width_remaining < 1:
				width_remaining = 1

			rect = QRect(x_start + margin, 0, width_remaining - 2 * margin, self.height() - BOTTOM_MARGIN - 5)

			fm2 = painter.fontMetrics()
			words = self.subtitle_text.split()

			if len(words) == 1:
				painter.drawText(rect, Qt.AlignCenter | Qt.AlignBottom, self.subtitle_text)
			else:
				letters_width = sum(fm2.horizontalAdvance(word) for word in words)
				gaps = len(words) - 1
				avail_width = rect.width()
				gap_width = (avail_width - letters_width) / gaps if avail_width > letters_width else fm2.horizontalAdvance(' ')
				x = rect.left()
				y = rect.bottom() - 5

				for i, word in enumerate(words):
					painter.drawText(int(x), int(y), word)
					x += fm2.horizontalAdvance(word)
					if i < len(words) - 1:
						x += gap_width

class AudioPlayer(QMainWindow):
	# Константы для определения стабильности
	WINDOW_WIDTH = 25   # количество столбцов для проверки
	TOLERANCE = 3	   # допустимое отклонение в пикселях

	def __init__(self):
		super().__init__()
		self.setWindowTitle("Видеослух")
		pygame.mixer.init()
		self.playback_audio_path = None

		self.video_processor = VideoProcessor(use_hw_accel=True)
		self.video_processor.cache_ready_changed.connect(self.on_cache_ready)

		central = QWidget()
		self.setCentralWidget(central)
		layout = QVBoxLayout(central)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(0)

		self.spec_widget = SpectrogramWidget()

		self.video_label = QLabel()
		self.video_label.setAlignment(Qt.AlignCenter)
		self.video_label.setStyleSheet("background-color: black;")
		self.video_label.setMinimumHeight(100)

		screen = QApplication.primaryScreen()
		size = screen.size()
		width = size.width()
		height = size.height()
		half_height = int(height * 0.5)

		splitter = QSplitter(Qt.Vertical)
		splitter.addWidget(self.spec_widget)
		splitter.addWidget(self.video_label)
		splitter.setSizes([int(half_height * 0.25), int(half_height * 0.75)])
		splitter.setHandleWidth(0)
		splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

		layout.addWidget(splitter, 1)

		controls = QHBoxLayout()
		controls.setContentsMargins(5, 5, 5, 5)
		controls.setSpacing(10)

		self.load_btn = QPushButton("Загрузить файл")
		self.play_btn = QPushButton("Play")
		self.play_btn.setEnabled(False)
		self.pause_btn = QPushButton("Pause")
		self.again_btn = QPushButton("Again")
		# НОВЫЕ КНОПКИ
		self.btn_add_tembr = QPushButton("+Тембр")
		self.btn_add_speech = QPushButton("+Речь")
		self.btn_add_tembr.clicked.connect(self.add_tembr_etalon)
		self.btn_add_speech.clicked.connect(self.add_speech_etalon)

		self.sub_edit = QLineEdit()
		self.sub_edit.setPlaceholderText("Введите субтитр или секунды")
		self.sub_edit.returnPressed.connect(self.process_input)
		self.file_label = QLabel("Файл не выбран")

		controls.addWidget(self.load_btn)
		controls.addWidget(self.play_btn)
		controls.addWidget(self.pause_btn)
		controls.addWidget(self.again_btn)
		controls.addWidget(self.btn_add_tembr)
		controls.addWidget(self.btn_add_speech)
		controls.addWidget(self.sub_edit)
		controls.addWidget(self.file_label)
		layout.addLayout(controls)

		self.load_btn.clicked.connect(self.load_file)
		self.play_btn.clicked.connect(self.play_audio)
		self.pause_btn.clicked.connect(self.pause_audio_manual)
		self.again_btn.clicked.connect(self.again_audio)

		self.audio_file = None
		self.calculator = SpectrogramCalculator(target_fps=200.0)
		self.transformer = MultiChannelTransformer(target_rows=320, target_fps=260.0)

		self.spec_r = None
		self.spec_g = None
		self.spec_b = None
		self.total_frames_260 = 0
		self.rgb_slice = None
		self.row_labels = [0, 148, 158, 167, 177, 187, 197, 207, 217, 226, 236, 246, 256, 320]

		self.is_paused = False
		self.render_timer = QTimer()
		self.render_timer.timeout.connect(self.update_spectrogram_window)

		self.pause_timer = QTimer()
		self.pause_timer.timeout.connect(self.check_pause)
		self.last_interval = 0

		self.subtitles = {}
		self.file_subs = []
		self.current_subtitle = ""

		self.play_offset = 0.0
		self.current_sec = 0.0

		self.points_r, self.points_g, self.points_b = get_default_points()

		self.setWindowFlags(Qt.FramelessWindowHint)
		self.setGeometry(0, 0, width, height - 40)
		self.setFixedSize(width, height - 40)

		title_bar = QWidget()
		title_bar.setFixedHeight(30)
		title_bar.setStyleSheet("background-color: #2b2b2b;")
		title_layout = QHBoxLayout(title_bar)
		title_layout.setContentsMargins(10, 0, 10, 0)
		self.title_label = QLabel("Видеослух")
		self.title_label.setStyleSheet("color: white; font-weight: bold; font-size: 14px;")
		title_layout.addWidget(self.title_label)
		title_layout.addStretch()
		layout.insertWidget(0, title_bar)

		self.etalons = []
		self.etalon_threshold = 800000.0
		self.load_etalons()

		# Атрибуты для сохранения текущего среза при создании эталона
		self._current_rgb_slice = None
		self._current_pad_left = 0
		self._current_valid_width = 0

	# ---------- ДОБАВЛЕННЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С ЭТАЛОНАМИ ----------
	def get_vertical_color_extremes(self, image_array, n_bins=100):
		"""
		image_array: np.array (H, W, 3) в формате RGB (0..255)
		Возвращает (min_arr, max_arr) формы (n_bins, 3)
		"""
		H, W, _ = image_array.shape
		bin_edges = np.linspace(0, H, n_bins + 1, dtype=int)
		min_colors = []
		max_colors = []
		last_min = None
		last_max = None

		for i in range(n_bins):
			start = bin_edges[i]
			end = bin_edges[i+1]
			if start == end:
				if last_min is not None:
					min_colors.append(last_min)
					max_colors.append(last_max)
				else:
					first = image_array[0, 0, :].tolist()
					min_colors.append(first)
					max_colors.append(first)
				continue

			band = image_array[start:end, :, :]
			min_vals = np.min(band, axis=(0, 1))
			max_vals = np.max(band, axis=(0, 1))
			min_colors.append(min_vals.tolist())
			max_colors.append(max_vals.tolist())
			last_min = min_vals.tolist()
			last_max = max_vals.tolist()

		return np.array(min_colors, dtype=np.float32), np.array(max_colors, dtype=np.float32)

	def add_etalon(self, region):
		"""region: 'tembr' или 'speech'"""
		if self._current_rgb_slice is None:
			QMessageBox.warning(self, "Ошибка", "Нет текущего изображения спектрограммы.")
			return

		rgb = self._current_rgb_slice
		pad_left = self._current_pad_left
		valid_width = self._current_valid_width

		if valid_width <= 0:
			QMessageBox.warning(self, "Ошибка", "Нет валидных данных для сохранения.")
			return

		# Вырезаем валидную область по горизонтали
		end_col = pad_left + valid_width
		valid_rgb = rgb[:, pad_left:end_col, :]

		# Определяем строки для региона
		if region == 'tembr':
			row_start, row_end = 0, 148
		else:  # speech
			row_start, row_end = 256, 320

		# Обрезаем по вертикали
		region_rgb = valid_rgb[row_start:row_end, :, :]
		if region_rgb.shape[0] == 0:
			QMessageBox.warning(self, "Ошибка", f"Область {region} пуста.")
			return

		# Вычисляем min/max по 100 бинам
		min_arr, max_arr = self.get_vertical_color_extremes(region_rgb, n_bins=100)

		# Папка для эталонов региона
		base_dir = os.path.join(_get_etalons_dir(), region)
		os.makedirs(base_dir, exist_ok=True)

		# Цикл для получения уникального имени
		while True:
			name, ok = QInputDialog.getText(self, f"Новый эталон ({region})",
											"Введите имя эталона (без расширения):")
			if not ok or not name.strip():
				return  # отмена
			name = name.strip()
			file_path = os.path.join(base_dir, f"{name}.json")
			if not os.path.exists(file_path):
				break
			# Если файл уже существует, спросим заменить или ввести другое
			reply = QMessageBox.question(self, "Файл существует",
										 f"Файл '{name}.json' уже существует.\nЗаменить?",
										 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
			if reply == QMessageBox.Cancel:
				return
			if reply == QMessageBox.Yes:
				# Перезаписываем (удаляем старый)
				os.remove(file_path)
				break
			# Если No – продолжаем цикл, запрашиваем новое имя

		# Сохраняем JSON
		data = {
			"min": min_arr.tolist(),
			"max": max_arr.tolist()
		}
		with open(file_path, 'w', encoding='utf-8') as f:
			json.dump(data, f, indent=2, ensure_ascii=False)

		# Перезагружаем эталоны, чтобы они сразу использовались
		self.load_etalons()
		QMessageBox.information(self, "Успешно", f"Эталон '{name}' сохранён в {file_path}")

	def add_tembr_etalon(self):
		self.add_etalon('tembr')

	def add_speech_etalon(self):
		self.add_etalon('speech')

	# ---------- ОСТАЛЬНЫЕ МЕТОДЫ (без изменений) ----------
	def capture_window_to_clipboard(self):
		self.repaint()
		pixmap = self.grab()
		if pixmap.isNull():
			return
		cropped = pixmap.copy(0, 0, pixmap.width(), max(1, pixmap.height() - 33))
		
		# QPixmap → BMP в память
		buffer = QBuffer()
		buffer.open(QIODevice.WriteOnly)
		cropped.save(buffer, "BMP")
		buffer.close()
		
		# BMP → PIL → DIB (без заголовка файла)
		img = Image.open(io.BytesIO(buffer.data()))
		output = io.BytesIO()
		img.convert("RGB").save(output, "BMP")
		dib_data = output.getvalue()[14:]  # отрезаем BITMAPFILEHEADER
		
		# Прямая запись в системный клипборд
		win32clipboard.OpenClipboard()
		win32clipboard.EmptyClipboard()
		win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
		win32clipboard.CloseClipboard()

	def load_etalons(self):
		base_dir = _get_etalons_dir()
		if not os.path.exists(base_dir):
			print("Папка etalons не найдена")
			return
			
		self.etalons = []

		for region in ["speech", "tembr"]:
			region_dir = os.path.join(base_dir, region)
			if not os.path.isdir(region_dir):
				continue
			json_files = glob.glob(os.path.join(region_dir, "*.json"))
			for fpath in json_files:
				try:
					with open(fpath, 'r', encoding='utf-8') as f:
						data = json.load(f)
					min_arr = np.array(data["min"], dtype=np.float32)
					max_arr = np.array(data["max"], dtype=np.float32)
					if min_arr.shape != (100, 3) or max_arr.shape != (100, 3):
						print(f"Неверный формат в {fpath}, пропускаем")
						continue
					name = os.path.splitext(os.path.basename(fpath))[0]
					self.etalons.append({
						"name": name,
						"region": region,
						"min": min_arr,
						"max": max_arr
					})
				except Exception as e:
					print(f"Ошибка загрузки {fpath}: {e}")
					
		# Динамический порог: обратно пропорционален количеству эталонов
		BASE_THRESHOLD = 800000.0
		REF_COUNT = 40
		self.etalon_threshold = BASE_THRESHOLD * (REF_COUNT / max(1, len(self.etalons)))

	def compute_etalon_lists(self, rgb_slice, pad_left=0, valid_width=None):
		if rgb_slice is None or self.etalons is None:
			return [], []

		if valid_width is None:
			valid_width = rgb_slice.shape[1]

		end_idx = pad_left + valid_width
		valid_slice = rgb_slice[:, pad_left:end_idx, :]

		if valid_slice.shape[1] == 0:
			return [], []

		min_over_time = valid_slice.min(axis=1)
		max_over_time = valid_slice.max(axis=1)

		results_tembr = []
		results_speech = []

		for region in ["tembr", "speech"]:
			if region == "tembr":
				row_start, row_end = 0, 148
			else:
				row_start, row_end = 256, 320

			min_rows = min_over_time[row_start:row_end, :]
			max_rows = max_over_time[row_start:row_end, :]
			n_rows = row_end - row_start

			old_idx = np.linspace(0, n_rows - 1, n_rows)
			new_idx = np.linspace(0, n_rows - 1, 100)
			min_resampled = np.zeros((100, 3), dtype=np.float32)
			max_resampled = np.zeros((100, 3), dtype=np.float32)
			for c in range(3):
				min_resampled[:, c] = np.interp(new_idx, old_idx, min_rows[:, c])
				max_resampled[:, c] = np.interp(new_idx, old_idx, max_rows[:, c])

			for etalon in self.etalons:
				if etalon["region"] != region:
					continue
				mn = etalon["min"]
				mx = etalon["max"]
				dist_channels = np.zeros(3, dtype=np.float32)
				for c in range(3):
					dist_c = 0.0
					for i in range(100):
						our_min = min_resampled[i, c]
						our_max = max_resampled[i, c]
						et_min = mn[i, c]
						et_max = mx[i, c]
						dist_c += (our_min - et_min) ** 2 + (our_max - et_max) ** 2
					dist_channels[c] = dist_c
				max_dist = np.mean(dist_channels)
				if max_dist < self.etalon_threshold:
					if region == "tembr":
						results_tembr.append({"name": etalon["name"], "dist": max_dist})
					else:
						results_speech.append({"name": etalon["name"], "dist": max_dist})

		results_tembr.sort(key=lambda x: x["dist"])
		results_speech.sort(key=lambda x: x["dist"])

		if len(results_tembr) > 3:
			results_tembr = results_tembr[:3]

		return results_tembr, results_speech

	def process_input(self):
		text = self.sub_edit.text().strip()
		if not text:
			interval = self.get_current_interval()
			if interval in self.subtitles:
				del self.subtitles[interval]
				self.current_subtitle = ""
				self.spec_widget.set_subtitle("")
				self.sub_edit.clear()
				self.save_subtitles()
			else:
				self.sub_edit.clear()
			return

		try:
			seconds = float(text)
			if seconds < 0:
				seconds = 0.0
			if self.audio_file is not None and self.total_frames_260 > 0:
				duration = self.total_frames_260 / self.transformer.target_fps
				if seconds > duration:
					seconds = duration
				self.go_to_time(seconds)
				return
			else:
				self.sub_edit.clear()
				return
		except ValueError:
			self.save_subtitle(text)

	def get_current_interval(self):
		if self.is_paused and self.last_interval >= 0:
			return self.last_interval
		if pygame.mixer.music.get_busy():
			pos_ms = pygame.mixer.music.get_pos()
			if pos_ms >= 0:
				pos_sec = self.play_offset + pos_ms / 1000.0
				return int(pos_sec) // 3
		return self.last_interval if self.last_interval >= 0 else 0

	def save_subtitle(self, text):
		interval = self.get_current_interval()
		if not text:
			if interval in self.subtitles:
				del self.subtitles[interval]
				self.current_subtitle = ""
				self.spec_widget.set_subtitle("")
				self.sub_edit.clear()
				self.save_subtitles()
			return
		self.subtitles[interval] = text
		self.current_subtitle = text
		self.spec_widget.set_subtitle(text)
		self.sub_edit.setText(text)
		self.save_subtitles()

	def save_subtitles(self):
		if self.audio_file:
			cache_dir = self.get_cache_dir(self.audio_file)
			sub_file = os.path.join(cache_dir, 'subtitles.json')
			with open(sub_file, 'w', encoding='utf-8') as f:
				json.dump(self.subtitles, f, ensure_ascii=False, indent=2)

	def go_to_time(self, seconds):
		if self.audio_file is None:
			return
		pygame.mixer.music.stop()
		self.render_timer.stop()
		self.pause_timer.stop()
		self.is_paused = True
		self.play_offset = seconds
		self.current_sec = seconds
		self.video_processor.clear_cache()
		self.video_processor.set_paused(True, self.current_sec)
		self.play_btn.setEnabled(False)
		self.update_spectrogram_window()
		try:
			if self.playback_audio_path is None:
				print("Нет аудио для загрузки")
				return
			pygame.mixer.music.load(self.playback_audio_path)
		except Exception as e:
			print(f"Ошибка загрузки аудио: {e}")
		self._set_status(f"Пауза на {seconds:.1f} сек")

	def again_audio(self):
		if self.audio_file is None:
			return
		new_offset = max(0.0, self.current_sec - 3.0)
		self.play_offset = new_offset
		pygame.mixer.music.stop()
		self.render_timer.stop()
		self.pause_timer.stop()
		self.is_paused = False
		self.video_processor.set_paused(False, self.current_sec)
		self.play_btn.setEnabled(False)
		try:
			if self.playback_audio_path is None:
				print("Нет аудио для воспроизведения")
				return
			pygame.mixer.music.load(self.playback_audio_path)
			pygame.mixer.music.play(start=self.play_offset)
			self.render_timer.start(50)
			self.pause_timer.start(100)
			self.last_interval = 0
			self.update_spectrogram_window()
		except Exception as e:
			print(f"Ошибка при повторном воспроизведении: {e}")

	def get_cache_dir(self, audio_path):
		file_hash = hashlib.md5(audio_path.encode()).hexdigest()
		cache_root = os.path.join(os.path.dirname(__file__), "cache")
		return os.path.join(cache_root, file_hash)

	def get_weights_dir(self, sr):
		weights_root = os.path.join(os.path.dirname(__file__), "cache", "weights")
		return os.path.join(weights_root, f"sr_{int(sr)}_rows_{self.transformer.target_rows}")

	def load_from_cache(self, cache_dir):
		try:
			spec_r = np.load(os.path.join(cache_dir, 'spec_r.npy'))
			spec_g = np.load(os.path.join(cache_dir, 'spec_g.npy'))
			spec_b = np.load(os.path.join(cache_dir, 'spec_b.npy'))
			with open(os.path.join(cache_dir, 'meta.json'), 'r') as f:
				meta = json.load(f)
			self.spec_r, self.spec_g, self.spec_b = spec_r, spec_g, spec_b
			self.total_frames_260 = meta['total_frames_260']
			duration_sec = meta['duration_sec']
			audio_path = meta.get('audio_path', None)
			return True, duration_sec, audio_path
		except Exception:
			return False, None, None

	def save_to_cache(self, cache_dir, duration_sec):
		os.makedirs(cache_dir, exist_ok=True)
		np.save(os.path.join(cache_dir, 'spec_r.npy'), self.spec_r)
		np.save(os.path.join(cache_dir, 'spec_g.npy'), self.spec_g)
		np.save(os.path.join(cache_dir, 'spec_b.npy'), self.spec_b)
		meta = {
			'total_frames_260': self.total_frames_260,
			'duration_sec': duration_sec,
			'audio_path': self.playback_audio_path
		}
		with open(os.path.join(cache_dir, 'meta.json'), 'w') as f:
			json.dump(meta, f)
		if self.subtitles:
			with open(os.path.join(cache_dir, 'subtitles.json'), 'w', encoding='utf-8') as f:
				json.dump(self.subtitles, f, ensure_ascii=False, indent=2)

	def _set_status(self, text):
		"""Обновить file_label; в offscreen-режиме (spectro_cli) —
		продублировать в stderr, чтобы anyjson видел прогресс."""
		try:
			self._set_status(text)
		except Exception:
			pass
		if _PROGRESS_TO_STDERR:
			try:
				_s = str(text)
				if getattr(self, "_last_status", None) != _s:
					self._last_status = _s
					sys.stderr.write(chr(0x1F3BC) + " " + _s + chr(10))
					sys.stderr.flush()
			except Exception:
				pass

	def load_file(self):
		path, _ = QFileDialog.getOpenFileName(
			self, "Выберите аудиофайл", "", "Audio/Video Files (*.wav *.mp3 *.flac *.mp4 *.avi *.mkv *.mov *.flv *.webm)"
		)
		if not path:
			return
		self.title_label.setText(f"Видеослух - {os.path.basename(path)}")

		self.video_processor.close()
		self.video_label.clear()
		self.play_btn.setEnabled(False)

		self.audio_file = path

		# загрузка файловых субтитров
		try:
		    subs, sub_path = load_subtitles(path)
		    self.file_subs = subs
		    if sub_path:
		        print(f"[subs] {len(subs)} replik iz {os.path.basename(sub_path)}")
		    else:
		        print("[subs] file not found, rabotaem s ruchnymi")
		except Exception as e:
		    print(f"[subs] error: {e}")
		    self.file_subs = []
		self.stop_audio()
		QApplication.processEvents()

		cache_dir = self.get_cache_dir(path)

		sub_file = os.path.join(cache_dir, 'subtitles.json')
		if os.path.exists(sub_file):
			with open(sub_file, 'r', encoding='utf-8') as f:
				self.subtitles = json.load(f)
				self.subtitles = {int(k): v for k, v in self.subtitles.items()}
		else:
			self.subtitles.clear()
		self.current_subtitle = ""
		self.spec_widget.set_subtitle("")
		self.sub_edit.clear()
		self.play_offset = 0.0
		self.current_sec = 0.0

		loaded_from_cache, duration_sec, audio_path = self.load_from_cache(cache_dir)

		# Устанавливаем границы нот для отрисовки (на основе NOTE_BOUNDARIES_NORM)
		self.spec_widget.note_boundaries_norm = NOTE_BOUNDARIES_NORM

		if loaded_from_cache:
			self.playback_audio_path = audio_path if audio_path else path
			self._set_status(f"Загружено из кэша ({duration_sec:.1f} сек)")
			mins = int(duration_sec // 60)
			secs = int(duration_sec % 60)
			if duration_sec >= 3600:
				hours = int(duration_sec // 3600)
				mins = int((duration_sec % 3600) // 60)
				self._set_status(f"Конец на {hours:02d}:{mins:02d}:{secs:02d}")
			else:
				self._set_status(f"Конец на {mins:02d}:{secs:02d}")
			self.spec_widget.clear()
			self.rgb_slice = None
			if 0 in self.subtitles:
				self.sub_edit.setText(self.subtitles[0])
			ext = os.path.splitext(path)[1].lower()
			if ext in ['.mp4', '.avi', '.mkv', '.mov', '.flv', '.webm']:
				if self.video_processor.load_video(path):
					self.video_processor.set_paused(True, self.current_sec)
			else:
				self.play_btn.setEnabled(True)
			return

		self._set_status("Вычисление спектрограммы...")
		QApplication.processEvents()

		ext = os.path.splitext(path)[1].lower()
		if ext in ['.mp4', '.avi', '.mkv', '.mov', '.flv', '.webm']:
			success, audio_path, video_clip = self.calculator.load_video(path)
			if not success:
				self._set_status("Ошибка загрузки видео/аудио")
				return
			self.playback_audio_path = audio_path
			self.video_processor.load_video(path)
			self.video_processor.set_paused(True, self.current_sec)
			self.update_spectrogram_window()
		else:
			success = self.calculator.load_audio(path)
			if not success:
				self._set_status("Ошибка загрузки аудио")
				return
			self.playback_audio_path = path
			self.video_processor.close()
			self.play_btn.setEnabled(True)

		sr = self.calculator.sr

		# Адаптивный STFT fps:
		#   <= 15 мин  -> 200 fps
		#   >= 2 часа  -> 100 fps (минимум)
		#   между ними — линейная интерполяция 200 -> 100
		_sec_total = len(self.calculator.samples) / sr
		_FPS_MAX = 200.0
		_FPS_MIN = 100.0
		_T_FAST = 15 * 60.0   # до 15 мин — максимум
		_T_SLOW = 2 * 3600.0  # 2 часа — минимум
		if _sec_total <= _T_FAST:
			_stft_fps = _FPS_MAX
		elif _sec_total >= _T_SLOW:
			_stft_fps = _FPS_MIN
		else:
			_t = (_sec_total - _T_FAST) / (_T_SLOW - _T_FAST)
			_stft_fps = _FPS_MAX - (_FPS_MAX - _FPS_MIN) * _t
		self.calculator.target_fps = float(_stft_fps)
		self._set_status(f"STFT {_stft_fps:.0f} fps (длительность {_sec_total:.0f} с)")
		QApplication.processEvents()

		def progress(pct, msg=""):
			self._set_status(f"{msg} {pct}%")
			QApplication.processEvents()

		try:
			spec_data, idx_200, idx_300, idx_3400, total_frames_orig = self.calculator.compute_spectrogram(progress_callback=progress)
		except Exception as e:
			self._set_status(f"Ошибка: {e}")
			return

		spec_data = spec_data.astype(np.float32, copy=False)

		freq_centers, freq_widths = self.transformer.build_frequency_axes(
			sr, spec_data, idx_200, idx_300, idx_3400
		)

		duration_sec = len(self.calculator.samples) / sr
		self.total_frames_260 = int(duration_sec * self.transformer.target_fps)

		weights_dir = self.get_weights_dir(sr)
		weights_r_path = os.path.join(weights_dir, 'weights_r.npy')
		weights_g_path = os.path.join(weights_dir, 'weights_g.npy')
		weights_b_path = os.path.join(weights_dir, 'weights_b.npy')

		if all(os.path.exists(p) for p in [weights_r_path, weights_g_path, weights_b_path]):
			self._set_status("Загрузка весов из кэша (по частоте дискретизации)...")
			QApplication.processEvents()
			weights_r = np.load(weights_r_path).astype(np.float32)
			weights_g = np.load(weights_g_path).astype(np.float32)
			weights_b = np.load(weights_b_path).astype(np.float32)
		else:
			self._set_status("Построение весовых матриц...")
			QApplication.processEvents()
			weights_r, weights_g, weights_b = self.transformer.build_weights(
				freq_centers, freq_widths,
				self.points_r, self.points_g, self.points_b
			)
			os.makedirs(weights_dir, exist_ok=True)
			np.save(weights_r_path, weights_r)
			np.save(weights_g_path, weights_g)
			np.save(weights_b_path, weights_b)

		self._set_status("Преобразование в RGB... 0%")
		QApplication.processEvents()

		F, T_orig = spec_data.shape
		time_orig = np.linspace(0, duration_sec, T_orig, dtype=np.float32)
		time_target = np.linspace(0, duration_sec, self.total_frames_260, dtype=np.float32)
		spec_interp = np.zeros((F, self.total_frames_260), dtype=np.float32)
		for f in range(F):
			spec_interp[f, :] = np.interp(time_target, time_orig, spec_data[f, :])
		self._set_status("Преобразование в RGB... 25%")
		QApplication.processEvents()

		r = weights_r @ spec_interp
		self._set_status("Преобразование в RGB... 50%")
		QApplication.processEvents()

		g = weights_g @ spec_interp
		self._set_status("Преобразование в RGB... 75%")
		QApplication.processEvents()

		b = weights_b @ spec_interp
		self._set_status("Преобразование в RGB... 100%")
		QApplication.processEvents()

		del spec_interp, spec_data
		gc.collect()

		self.spec_r = np.clip(r, 0, 255).astype(np.uint8)
		self.spec_g = np.clip(g, 0, 255).astype(np.uint8)
		self.spec_b = np.clip(b, 0, 255).astype(np.uint8)

		del r, g, b
		gc.collect()

		self.save_to_cache(cache_dir, duration_sec)

		self.spec_widget.clear()
		self.rgb_slice = None
		self.is_paused = False

		mins = int(duration_sec // 60)
		secs = int(duration_sec % 60)
		if duration_sec >= 3600:
			hours = int(duration_sec // 3600)
			mins = int((duration_sec % 3600) // 60)
			self._set_status(f"Конец на {hours:02d}:{mins:02d}:{secs:02d}")
		else:
			self._set_status(f"Конец на {mins:02d}:{secs:02d}")

		if 0 in self.subtitles:
			self.sub_edit.setText(self.subtitles[0])

	def on_cache_ready(self, ready):
		if self.video_processor.is_loaded:
			self.play_btn.setEnabled(ready)

	def get_active_file_sub(self, sec):
		if not self.file_subs:
			return ""
		ms = sec * 1000.0
		active = [s for s in self.file_subs if s.start <= ms <= s.end]
		if not active:
			return ""
		best = max(active, key=lambda s: subtitle_score(s.text))
		text = clean_subtitle_text(best.text)
		if subtitle_score(text) <= 1:
			return ""
		text = re.sub(r"\s+", " ", text.replace("\\N", " ").replace("\n", " ")).strip()
		return text

	def update_spectrogram_window(self):
		if self.spec_r is None:
			return

		if not pygame.mixer.music.get_busy() and not self.is_paused:
			return

		pos_ms = pygame.mixer.music.get_pos()
		if pos_ms < 0:
			return

		self.current_sec = self.play_offset + pos_ms / 1000.0

		current_frame = int(self.current_sec * self.transformer.target_fps)
		if current_frame < 0:
			current_frame = 0
		if current_frame > self.total_frames_260:
			current_frame = self.total_frames_260

		frames_window = int(3.0 * self.transformer.target_fps)
		start_frame = current_frame - frames_window

		if start_frame < 0:
			pad = -start_frame
			if current_frame > 0:
				existing_r = self.spec_r[:, :current_frame]
				existing_g = self.spec_g[:, :current_frame]
				existing_b = self.spec_b[:, :current_frame]
			else:
				existing_r = np.zeros((self.spec_r.shape[0], 0), dtype=np.uint8)
				existing_g = np.zeros((self.spec_g.shape[0], 0), dtype=np.uint8)
				existing_b = np.zeros((self.spec_b.shape[0], 0), dtype=np.uint8)
			pad_arr = np.zeros((self.spec_r.shape[0], pad), dtype=np.uint8)
			slice_r = np.hstack((pad_arr, existing_r))
			slice_g = np.hstack((pad_arr, existing_g))
			slice_b = np.hstack((pad_arr, existing_b))

			pad_left = pad
			valid_width = current_frame
		else:
			slice_r = self.spec_r[:, start_frame:current_frame]
			slice_g = self.spec_g[:, start_frame:current_frame]
			slice_b = self.spec_b[:, start_frame:current_frame]

			pad_left = 0
			valid_width = slice_r.shape[1]

		if slice_r.shape[1] < frames_window:
			pad_right = frames_window - slice_r.shape[1]
			pad_arr = np.zeros((self.spec_r.shape[0], pad_right), dtype=np.uint8)
			slice_r = np.hstack((slice_r, pad_arr))
			slice_g = np.hstack((slice_g, pad_arr))
			slice_b = np.hstack((slice_b, pad_arr))

		slice_r, slice_g, slice_b = enhance_mid_frequencies(slice_r, slice_g, slice_b)

		# ====== Расчёт линии ======
		try:
			region = slice_r[148:266, :]
			step = 4
			region_ds = region[:, ::step]
			max_vals_ds = np.max(region_ds, axis=0)
			THRESHOLD = 15
			valid_mask_ds = max_vals_ds > THRESHOLD

			reshaped = region.reshape(region.shape[0], -1, step)
			min_per_block = np.min(reshaped, axis=(0, 2))
			overexposed = min_per_block > 125

			overexposed_to_disable = np.zeros_like(overexposed, dtype=bool)
			diff = np.diff(np.concatenate(([0], overexposed.astype(int), [0])))
			starts = np.where(diff == 1)[0]
			ends = np.where(diff == -1)[0]
			for s, e in zip(starts, ends):
				if (e - s) >= 3:
					overexposed_to_disable[s:e] = True

			max_indices_ds = np.full(region_ds.shape[1], np.nan, dtype=np.float64)
			valid_cols = np.where(valid_mask_ds)[0]
			for x in valid_cols:
				col = region_ds[:, x]
				max_col_val = max_vals_ds[x]
				if overexposed_to_disable[x]:
					valid_mask_ds[x] = False
					max_indices_ds[x] = np.nan
					continue
				mask = col > (0.8 * max_col_val)
				true_idx = np.where(mask)[0]
				if len(true_idx) == 0:
					continue
				breaks = np.where(np.diff(true_idx) != 1)[0] + 1
				clusters = np.split(true_idx, breaks)
				best_power = -1
				best_center = 0
				for cl in clusters:
					if len(cl) == 0:
						continue
					seg_power = np.sum(col[cl])
					if seg_power > best_power:
						best_power = seg_power
						best_center = int(np.mean(cl))
				max_indices_ds[x] = best_center + 148

			radius_ds = 3
			smoothed_indices_ds = np.full_like(max_indices_ds, np.nan)
			n_cols_ds = len(max_indices_ds)
			for i in range(n_cols_ds):
				start = max(0, i - radius_ds)
				end = min(n_cols_ds, i + radius_ds + 1)
				window_vals = max_indices_ds[start:end]
				window_valid = valid_mask_ds[start:end]
				if np.any(window_valid):
					smoothed_indices_ds[i] = np.nanmean(window_vals)

			x_old = np.arange(0, 780, step)
			x_new = np.arange(780)
			max_indices = np.interp(x_new, x_old, smoothed_indices_ds)
			valid_mask = np.interp(x_new, x_old, valid_mask_ds.astype(float)) > 0.5
			valid_mask = valid_mask & ~np.isnan(max_indices)

			self.spec_widget.set_line_data(max_indices, valid_mask)
		except Exception as e:
			print(f"Ошибка расчёта линии: {e}")

		# ====== Определение стабильных нот (12 уровней) ======
		stable_flags = [False] * 12
		# Идеальные непрерывные границы интервалов (полуоткрытые)
		# [148,156), [156,166), [166,174), [174,183), [183,192), [192,201),
		# [201,211), [211,219), [219,230), [230,240), [240,250), [250,267]
		BOUNDARIES = [148, 156, 166, 174, 183, 192, 201, 211, 219, 230, 240, 250, 267]

		if valid_mask.sum() > 0:
			x_valid = np.where(valid_mask)[0]
			y_valid = max_indices[x_valid]
			n = len(x_valid)

			if n >= self.WINDOW_WIDTH:
				for i in range(n - self.WINDOW_WIDTH + 1):
					window_y = y_valid[i:i + self.WINDOW_WIDTH]
					mean_y = np.mean(window_y)
					if np.all(np.abs(window_y - mean_y) <= self.TOLERANCE):
						for idx in range(12):
							if BOUNDARIES[idx] <= mean_y < BOUNDARIES[idx + 1]:
								stable_flags[idx] = True
								break

		self.spec_widget.set_stable_flags(stable_flags)

		# ====== Время и подписи ======
		time_start = self.current_sec - 3.0
		time_end = self.current_sec

		rgb = np.stack([slice_r, slice_g, slice_b], axis=2)
		self.rgb_slice = rgb
		self.spec_widget.set_spectrogram_rgb(rgb, time_start, time_end, self.row_labels)

		# СОХРАНЯЕМ ТЕКУЩИЙ СРЕЗ ДЛЯ ВОЗМОЖНОГО СОЗДАНИЯ ЭТАЛОНА
		self._current_rgb_slice = rgb
		self._current_pad_left = pad_left
		self._current_valid_width = valid_width

		tembr_list, speech_list = self.compute_etalon_lists(self.rgb_slice, pad_left=pad_left, valid_width=valid_width)
		self.spec_widget.set_etalon_data(tembr_list, speech_list)

		# file subs v prioritete
		file_text = self.get_active_file_sub(self.current_sec)
		if file_text:
		    self.current_subtitle = file_text
		    self.spec_widget.set_subtitle(file_text)
		else:
		    interval = int(self.current_sec) // 3
		    prev_interval = max(0, interval + 1)
		    self.current_subtitle = self.subtitles.get(prev_interval, "")
		    self.spec_widget.set_subtitle(self.current_subtitle)
		    self.sub_edit.setText(self.current_subtitle)

		# =========================================================
		# ОТОБРАЖЕНИЕ ВИДЕО: ВЕКТОРНЫЙ БЛЕНДИНГ (БЕЗ ЛАГОВ)
		# =========================================================
		if self.video_processor.is_loaded:
			try:
				label_size = self.video_label.size()
				W_actual = label_size.width()
				H_actual = label_size.height()

				if W_actual <= 0 or H_actual <= 0:
					W_actual, H_actual = 780, 200

				W_INTERNAL = 780
				H_TARGET = 265

				fps = self.video_processor.fps
				x_indices = np.arange(W_INTERNAL, dtype=np.float32)
				t_start = self.current_sec - 3.0
				t_map = t_start + 3.0 * (x_indices / (W_INTERNAL - 1))

				frame_pos = t_map * fps
				idx_left = np.floor(frame_pos).astype(np.int32)
				idx_right = idx_left + 1

				alpha = (frame_pos - idx_left).astype(np.float32)

				max_f = self.video_processor.total_frames - 1
				np.clip(idx_left, 0, max_f, out=idx_left)
				np.clip(idx_right, 0, max_f, out=idx_right)

				unique_idxs = np.unique(np.concatenate((idx_left, idx_right)))
				idx_map = {val: i for i, val in enumerate(unique_idxs)}

				frames_list = []
				for f_idx in unique_idxs:
					t_sec = f_idx / fps
					f = self.video_processor.get_frame(t_sec, (W_INTERNAL, H_TARGET))
					frames_list.append(f)

				if not frames_list:
					self.video_label.clear()
				else:
					stack = np.array(frames_list, dtype=np.uint8)
					local_left = np.array([idx_map[i] for i in idx_left], dtype=np.int32)
					local_right = np.array([idx_map[i] for i in idx_right], dtype=np.int32)

					output_buf = np.zeros((H_TARGET, W_INTERNAL, 3), dtype=np.float32)

					for i, f_idx in enumerate(unique_idxs):
						frame_data = stack[i]
						mask_l = (idx_left == f_idx)
						mask_r = (idx_right == f_idx)

						if np.any(mask_l):
							cols = np.where(mask_l)[0]
							w = (1.0 - alpha[cols]).reshape(1, -1, 1)
							chunk = frame_data[:, cols, :].astype(np.float32)
							chunk *= w
							output_buf[:, cols, :] += chunk

						if np.any(mask_r):
							cols = np.where(mask_r)[0]
							w = alpha[cols].reshape(1, -1, 1)
							chunk = frame_data[:, cols, :].astype(np.float32)
							chunk *= w
							output_buf[:, cols, :] += chunk

					np.clip(output_buf, 0, 255, out=output_buf)
					output_small = output_buf.astype(np.uint8)

					interp = cv2.INTER_LINEAR
					output_full = cv2.resize(output_small, (W_actual, H_actual), interpolation=interp)

					qimage = QImage(output_full.data, W_actual, H_actual, W_actual * 3, QImage.Format_RGB888)
					self.video_label.setPixmap(QPixmap.fromImage(qimage.copy()))

			except Exception as e:
				print(f"Ошибка видео: {e}")
				import traceback
				traceback.print_exc()
				self.video_label.setPixmap(QPixmap())
		else:
			self.video_label.clear()

	def play_audio(self):
		if self.audio_file is None:
			return

		if self.is_paused:
			pygame.mixer.music.unpause()
			self.is_paused = False
			self.video_processor.set_paused(False, self.current_sec)
			self.render_timer.start(50)
			self.pause_timer.start(100)
			self.update_spectrogram_window()
			return

		if pygame.mixer.music.get_busy():
			return

		try:
			if self.playback_audio_path is None:
				print("Нет аудио для воспроизведения")
				return
			pygame.mixer.music.load(self.playback_audio_path)
			pygame.mixer.music.play(start=self.play_offset)
			self.render_timer.start(50)
			self.pause_timer.start(100)
			self.is_paused = False
			self.last_interval = 0
			self.video_processor.set_paused(False, self.current_sec)
			self.play_btn.setEnabled(False)
			self.update_spectrogram_window()
		except Exception as e:
			print(f"Ошибка воспроизведения: {e}")

	def pause_audio_manual(self):
		self.pause_audio()
		self.play_btn.setEnabled(True)

	def pause_audio(self):
		if not pygame.mixer.music.get_busy():
			return
		pygame.mixer.music.pause()
		self.is_paused = True
		self.render_timer.stop()
		self.pause_timer.stop()
		# обновить субтитр на актуальный
		pos_ms2 = pygame.mixer.music.get_pos()
		if pos_ms2 >= 0:
		    self.current_sec = self.play_offset + pos_ms2 / 1000.0
		file_text = self.get_active_file_sub(self.current_sec)
		if file_text:
		    self.spec_widget.set_subtitle(file_text)
		
		# СКРИНШОТ СНАЧАЛА — до set_paused, без processEvents
		self.capture_window_to_clipboard()
		
		# Потом запускаем кэш
		if self.video_processor.is_loaded:
			self.video_processor.set_paused(True, self.current_sec)
		else:
			self.play_btn.setEnabled(True)

	def stop_audio(self):
		self.video_processor.close()
		self.video_label.clear()

		pygame.mixer.music.stop()
		self.render_timer.stop()
		self.pause_timer.stop()
		self.is_paused = False
		self.spec_widget.clear()
		self.rgb_slice = None
		self.spec_widget.set_subtitle("")
		self.play_offset = 0.0
		self.current_sec = 0.0
		self.sub_edit.clear()
		self.playback_audio_path = None
		self.play_btn.setEnabled(False)

	def check_pause(self):
		if not pygame.mixer.music.get_busy() or self.is_paused:
			return
		pos_ms = pygame.mixer.music.get_pos()
		if pos_ms < 0:
			return
		pos_sec = self.play_offset + pos_ms / 1000.0
		self.current_sec = pos_sec
		interval = int(pos_sec) // 3
		if interval > self.last_interval and interval > 0:
			self.pause_audio()
			self.load_etalons()
			self.last_interval = interval
			self.show_subtitle_for_interval(interval)

	def show_subtitle_for_interval(self, interval):
		file_text = self.get_active_file_sub(self.current_sec)
		if file_text:
		    self.current_subtitle = file_text
		    self.spec_widget.set_subtitle(file_text)
		    return
		if interval in self.subtitles:
			self.current_subtitle = self.subtitles[interval]
			self.spec_widget.set_subtitle(self.current_subtitle)
			self.sub_edit.setText(self.current_subtitle)
		else:
			self.current_subtitle = ""
			self.spec_widget.set_subtitle("")
			self.sub_edit.clear()

	def closeEvent(self, event):
		self.video_processor.close()
		super().closeEvent(event)


if __name__ == '__main__':
	app = QApplication(sys.argv)
	player = AudioPlayer()
	player.show()
	sys.exit(app.exec_())
