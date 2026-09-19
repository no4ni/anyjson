import sys
import cv2
import numpy as np
from PyQt6.QtWidgets import (
	QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
	QPushButton, QLabel, QSpinBox, QFileDialog, QMessageBox
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt


def _imread_unicode(path):
    """cv2.imread не читает пути с не-ASCII (кириллица). Через np.fromfile."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def imread_unicode(path):
	img = _imread_unicode(path)
	if img is not None:
		return img
	try:
		data = np.fromfile(path, dtype=np.uint8)
		img = cv2.imdecode(data, cv2.IMREAD_COLOR)
		return img
	except Exception as e:
		print(f"Ошибка чтения: {e}")
		return None


class MainWindow(QMainWindow):
	def __init__(self):
		super().__init__()
		self.setWindowTitle("Комбинированный сглаживатель + Замена кожи на синий")
		self.setMinimumSize(900, 700)

		self.current_img = None
		self.original_img = None
		self.show_image = True

		main_widget = QWidget()
		self.setCentralWidget(main_widget)
		main_layout = QVBoxLayout(main_widget)

		top_bar = QHBoxLayout()
		top_bar.addWidget(QLabel("Искажение:"))

		self.level_spin = QSpinBox()
		self.level_spin.setRange(0, 100)
		self.level_spin.setSingleStep(1)
		self.level_spin.setValue(9)
		self.level_spin.valueChanged.connect(self.update_parameters)
		top_bar.addWidget(self.level_spin)

		top_bar.addStretch()

		self.load_btn = QPushButton("Загрузить")
		self.load_btn.clicked.connect(self.load_image)
		top_bar.addWidget(self.load_btn)

		# --- Новая кнопка "Вставить" ---
		self.paste_btn = QPushButton("Вставить")
		self.paste_btn.clicked.connect(self.paste_image)
		top_bar.addWidget(self.paste_btn)

		self.apply_btn = QPushButton("Применить всё")
		self.apply_btn.clicked.connect(self.apply_combined)
		self.apply_btn.setEnabled(False)
		top_bar.addWidget(self.apply_btn)

		self.copy_btn = QPushButton("Скопировать")
		self.copy_btn.clicked.connect(self.copy_to_clipboard)
		self.copy_btn.setEnabled(False)
		top_bar.addWidget(self.copy_btn)

		self.toggle_btn = QPushButton("X")
		self.toggle_btn.clicked.connect(self.toggle_image_visibility)
		self.toggle_btn.setEnabled(False)
		top_bar.addWidget(self.toggle_btn)

		main_layout.addLayout(top_bar)

		self.image_label = QLabel("Загрузите изображение")
		self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self.image_label.setStyleSheet("border: 1px solid gray; background-color: #f0f0f0;")
		self.image_label.setMinimumSize(700, 500)
		main_layout.addWidget(self.image_label, stretch=1)

		self.statusBar().showMessage("Готово к работе.")
		self.update_parameters(4)

	def update_parameters(self, level):
		if level <= 0:
			self.stop_threshold = 255
			self.max_iterations = 0
			self.noise_amplitude = 0
			self.lines_count = 0
			self.shift_px = 0
			self.alpha_percent = 0
			self.chess_size = 1
			self.invert_ratio = 0.0
			self.chess_prob = 0.0
			self.skin_alpha = 0.0
			self.replace_threshold = 255
		elif level <= 5:
			t = level / 5.0
			self.stop_threshold = int(255 - (255 - 19) * t)
			self.max_iterations = int(round(13 * t))
			self.noise_amplitude = int(round(1 * t))
			self.lines_count = int(round(6 * t))
			self.shift_px = int(round(4 * t))
			self.alpha_percent = int(round(16 * t))
			self.chess_size = int(round(1 + (17 - 1) * t))
			self.invert_ratio = 0.1 * t
			self.chess_prob = 0.25 * t
			self.skin_alpha = 0.0
			self.replace_threshold = min(255, self.stop_threshold * 2)
		elif level <= 8:
			old_level = level - 5
			self.stop_threshold = max(1, int(19 - (old_level - 1) * 0.5))
			self.replace_threshold = min(255, self.stop_threshold * 2)
			self.max_iterations = min(200, int(13 + (old_level - 1) * 0.5))
			self.noise_amplitude = min(50, int(1 + (old_level - 1) * 0.5))
			self.lines_count = min(100, int(6 + (old_level - 1) * 0.5))
			self.shift_px = min(100, int(4 + (old_level - 1) * 0.5))
			self.alpha_percent = min(100, int(16 + (old_level - 1) * 0.5))
			self.chess_size = min(50, int(17 + (old_level - 1)))
			if old_level <= 8:
				self.invert_ratio = 0.1 + (old_level - 1) * 0.05
			elif old_level <= 18:
				self.invert_ratio = 0.55 + (old_level - 9) * (0.35 / 9.0)
			else:
				self.invert_ratio = 0.5
			if old_level <= 10:
				self.chess_prob = 0.25 + (old_level - 1) * (0.5 - 0.25) / 9.0
			elif old_level <= 20:
				self.chess_prob = 0.5 + (old_level - 11) * (1.0 - 0.5) / 9.0
			else:
				self.chess_prob = 1.0
			self.invert_ratio = min(1.0, max(0.0, self.invert_ratio))
			self.chess_prob = min(1.0, max(0.0, self.chess_prob))
			if old_level <= 1:
				self.skin_alpha = 0.0
			elif old_level < 5:
				self.skin_alpha = (old_level - 1) * 0.0625
			elif old_level < 20:
				self.skin_alpha = 0.25 + (old_level - 5) * 0.05
			else:
				self.skin_alpha = 1.0
		elif 9 <= level <= 17:
			old_level = level - 5
			self.stop_threshold = max(1, int(19 - (old_level - 1) * 0.5))
			multiplier = 2.0 - (level - 9) / 8.0
			self.replace_threshold = min(255, int(self.stop_threshold * multiplier))
			self.max_iterations = min(200, int(13 + (old_level - 1) * 0.5))
			self.noise_amplitude = min(50, int(1 + (old_level - 1) * 0.5))
			self.lines_count = min(100, int(6 + (old_level - 1) * 0.5))
			self.shift_px = min(100, int(4 + (old_level - 1) * 0.5))
			self.alpha_percent = min(100, int(16 + (old_level - 1) * 0.5))
			self.chess_size = min(50, int(17 + (old_level - 1)))
			if old_level <= 8:
				self.invert_ratio = 0.1 + (old_level - 1) * 0.05
			elif old_level <= 18:
				self.invert_ratio = 0.55 + (old_level - 9) * (0.35 / 9.0)
			else:
				self.invert_ratio = 0.5
			if old_level <= 10:
				self.chess_prob = 0.25 + (old_level - 1) * (0.5 - 0.25) / 9.0
			elif old_level <= 20:
				self.chess_prob = 0.5 + (old_level - 11) * (1.0 - 0.5) / 9.0
			else:
				self.chess_prob = 1.0
			self.invert_ratio = min(1.0, max(0.0, self.invert_ratio))
			self.chess_prob = min(1.0, max(0.0, self.chess_prob))
			if old_level <= 1:
				self.skin_alpha = 0.0
			elif old_level < 5:
				self.skin_alpha = (old_level - 1) * 0.0625
			elif old_level < 20:
				self.skin_alpha = 0.25 + (old_level - 5) * 0.05
			else:
				self.skin_alpha = 1.0
		else:  # level > 17
			old_level = level - 5
			self.stop_threshold = max(1, int(19 - (old_level - 1) * 0.5))
			self.replace_threshold = self.stop_threshold
			self.max_iterations = min(200, int(13 + (old_level - 1) * 0.5))
			self.noise_amplitude = min(50, int(1 + (old_level - 1) * 0.5))
			self.lines_count = min(100, int(6 + (old_level - 1) * 0.5))
			self.shift_px = min(100, int(4 + (old_level - 1) * 0.5))
			self.alpha_percent = min(100, int(16 + (old_level - 1) * 0.5))
			self.chess_size = min(50, int(17 + (old_level - 1)))
			if old_level <= 8:
				self.invert_ratio = 0.1 + (old_level - 1) * 0.05
			elif old_level <= 18:
				self.invert_ratio = 0.55 + (old_level - 9) * (0.35 / 9.0)
			else:
				self.invert_ratio = 0.5
			if old_level <= 10:
				self.chess_prob = 0.25 + (old_level - 1) * (0.5 - 0.25) / 9.0
			elif old_level <= 20:
				self.chess_prob = 0.5 + (old_level - 11) * (1.0 - 0.5) / 9.0
			else:
				self.chess_prob = 1.0
			self.invert_ratio = min(1.0, max(0.0, self.invert_ratio))
			self.chess_prob = min(1.0, max(0.0, self.chess_prob))
			if old_level <= 1:
				self.skin_alpha = 0.0
			elif old_level < 5:
				self.skin_alpha = (old_level - 1) * 0.0625
			elif old_level < 20:
				self.skin_alpha = 0.25 + (old_level - 5) * 0.05
			else:
				self.skin_alpha = 1.0

		self.statusBar().showMessage(
			f"Искажение: {level} | "
			f"Пороги: {self.stop_threshold}/{self.replace_threshold} | "
			f"Итераций: {self.max_iterations} | Шум: {self.noise_amplitude} | "
			f"Линий: {self.lines_count} | Сдвиг: {self.shift_px} | "
			f"Прозрачность: {self.alpha_percent}% | Клетка: {self.chess_size}px | "
			f"Инверсия: {int(self.invert_ratio * 100)}% | Вероятность: {int(self.chess_prob * 100)}% | "
			f"Кожа: {int(self.skin_alpha * 100)}%"
		)

	def load_image(self):
		file_path, _ = QFileDialog.getOpenFileName(self, "Выберите изображение", "", "Изображения (*.png *.jpg *.jpeg *.bmp *.tiff *.webp);;Все файлы (*)")
		if file_path:
			img = imread_unicode(file_path)
			if img is None:
				QMessageBox.critical(self, "Ошибка", "Не удалось загрузить изображение.")
				return
			if img.shape[0] > 700:
				scale = 700 / img.shape[0]
				new_width = int(img.shape[1] * scale)
				img = cv2.resize(img, (new_width, 700), interpolation=cv2.INTER_AREA)
			self.original_img = img.copy()
			self.current_img = img.copy()
			self.show_image = True
			self.toggle_btn.setEnabled(True)
			self.display_image(self.current_img)
			self.apply_btn.setEnabled(True)
			self.copy_btn.setEnabled(True)
			self.statusBar().showMessage(f"Загружено: {img.shape[1]}x{img.shape[0]}")
			self.update_parameters(self.level_spin.value())

	# ----- НОВЫЙ МЕТОД: вставка из буфера обмена -----
	def paste_image(self):
		clipboard = QApplication.clipboard()
		mime = clipboard.mimeData()
		if not mime.hasImage():
			QMessageBox.information(self, "Нет изображения", "В буфере обмена нет изображения.")
			return

		qimage = clipboard.image()
		if qimage.isNull():
			QMessageBox.warning(self, "Ошибка", "Не удалось получить изображение из буфера обмена.")
			return

		# Приводим к 24-битному RGB (без альфа-канала)
		qimage = qimage.convertToFormat(QImage.Format.Format_RGB888)
		width = qimage.width()
		height = qimage.height()
		
		# Получаем фактический размер строки с учётом выравнивания
		bytes_per_line = qimage.bytesPerLine()
		
		# Получаем указатель на данные
		ptr = qimage.bits()
		ptr.setsize(height * bytes_per_line)  # задаём правильный размер буфера
		
		# Создаём numpy-массив с учётом stride
		arr = np.frombuffer(ptr, dtype=np.uint8, count=height * bytes_per_line)
		arr = arr.reshape(height, bytes_per_line)
		
		# Если есть паддинг по строкам — отрезаем лишнее
		if bytes_per_line != width * 3:
			arr = arr[:, :width * 3].copy()  # делаем копию, чтобы массив стал непрерывным
		
		# Теперь arr имеет форму (height, width, 3) в формате RGB
		arr = arr.reshape(height, width, 3)
		
		# Конвертируем RGB → BGR для OpenCV
		img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
		
		# Уменьшаем изображение при необходимости (как и в load_image)
		if img.shape[0] > 700:
			scale = 700 / img.shape[0]
			new_width = int(img.shape[1] * scale)
			img = cv2.resize(img, (new_width, 700), interpolation=cv2.INTER_AREA)
		
		self.original_img = img.copy()
		self.current_img = img.copy()
		self.show_image = True
		self.toggle_btn.setEnabled(True)
		self.display_image(self.current_img)
		self.apply_btn.setEnabled(True)
		self.copy_btn.setEnabled(True)
		self.statusBar().showMessage(f"Вставлено изображение: {img.shape[1]}x{img.shape[0]}")
		self.update_parameters(self.level_spin.value())
		
	def toggle_image_visibility(self):
		if self.current_img is None:
			return
		self.show_image = not self.show_image
		if self.show_image:
			self.display_image(self.current_img)
		else:
			self.image_label.clear()
			self.image_label.setText("Цифры не подходят")
			self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

	def smooth_edges_core(self, image, threshold_replace, kernel_size, amplitude):
		current = image.copy()
		h, w = current.shape[:2]
		is_color = len(current.shape) == 3

		if is_color:
			b, g, r = cv2.split(current)
			filtered_b = cv2.GaussianBlur(b, (kernel_size, kernel_size), 0).astype(np.float32)
			filtered_g = cv2.GaussianBlur(g, (kernel_size, kernel_size), 0).astype(np.float32)
			filtered_r = cv2.GaussianBlur(r, (kernel_size, kernel_size), 0).astype(np.float32)
			b_float = b.astype(np.float32)
			g_float = g.astype(np.float32)
			r_float = r.astype(np.float32)
		else:
			filtered = cv2.GaussianBlur(current, (kernel_size, kernel_size), 0).astype(np.float32)

		block_size = 8
		pad_h = (block_size - h % block_size) % block_size
		pad_w = (block_size - w % block_size) % block_size
		H = h + pad_h
		W = w + pad_w

		h_blocks = H // block_size
		w_blocks = W // block_size

		if h_blocks == 0 or w_blocks == 0:
			mask = np.zeros((h, w), dtype=bool)
		else:
			if is_color:
				padded = np.pad(current, ((0, pad_h), (0, pad_w), (0, 0)), mode='constant', constant_values=0)
				valid = np.pad(np.ones((h, w), dtype=bool), ((0, pad_h), (0, pad_w)), mode='constant', constant_values=False)
			else:
				padded = np.pad(current, ((0, pad_h), (0, pad_w)), mode='constant', constant_values=0)
				valid = np.pad(np.ones((h, w), dtype=bool), ((0, pad_h), (0, pad_w)), mode='constant', constant_values=False)

			if is_color:
				padded_r = padded.reshape(h_blocks, block_size, w_blocks, block_size, 3)
				valid_r = valid.reshape(h_blocks, block_size, w_blocks, block_size)
				sums = np.sum(padded_r * valid_r[..., None], axis=(1, 3))
				counts = np.sum(valid_r, axis=(1, 3))
				means = sums / counts[..., None]
				expanded_means = means[:, None, :, None, :]
				diff = np.abs(padded_r - expanded_means)
				max_diff = np.max(diff, axis=(1, 3))
				mask_blocks = np.any(max_diff > threshold_replace, axis=2)
				expanded_mask = mask_blocks[:, None, :, None]
				pad_mask = np.broadcast_to(expanded_mask, (h_blocks, block_size, w_blocks, block_size)).reshape(H, W)
				mask = pad_mask[:h, :w]
			else:
				padded_r = padded.reshape(h_blocks, block_size, w_blocks, block_size)
				valid_r = valid.reshape(h_blocks, block_size, w_blocks, block_size)
				sums = np.sum(padded_r * valid_r, axis=(1, 3))
				counts = np.sum(valid_r, axis=(1, 3))
				means = sums / counts
				expanded_means = means[:, None, :, None]
				diff = np.abs(padded_r - expanded_means)
				max_diff = np.max(diff, axis=(1, 3))
				mask_blocks = max_diff > threshold_replace
				expanded_mask = mask_blocks[:, None, :, None]
				pad_mask = np.broadcast_to(expanded_mask, (h_blocks, block_size, w_blocks, block_size)).reshape(H, W)
				mask = pad_mask[:h, :w]

		if is_color:
			result_b = b_float.copy()
			result_g = g_float.copy()
			result_r = r_float.copy()
			result_b[mask] = filtered_b[mask]
			result_g[mask] = filtered_g[mask]
			result_r[mask] = filtered_r[mask]
			if amplitude > 0:
				noise_b = np.random.randint(-amplitude, amplitude + 1, size=result_b.shape).astype(np.float32)
				noise_g = np.random.randint(-amplitude, amplitude + 1, size=result_g.shape).astype(np.float32)
				noise_r = np.random.randint(-amplitude, amplitude + 1, size=result_r.shape).astype(np.float32)
				result_b[mask] += noise_b[mask]
				result_g[mask] += noise_g[mask]
				result_r[mask] += noise_r[mask]
			current = cv2.merge([np.clip(result_b, 0, 255).astype(np.uint8),
								 np.clip(result_g, 0, 255).astype(np.uint8),
								 np.clip(result_r, 0, 255).astype(np.uint8)])
		else:
			result = current.astype(np.float32).copy()
			result[mask] = filtered[mask]
			if amplitude > 0:
				noise = np.random.randint(-amplitude, amplitude + 1, size=result.shape).astype(np.float32)
				result[mask] += noise[mask]
			current = np.clip(result, 0, 255).astype(np.uint8)

		return current

	def draw_lines(self, img, n):
		if n <= 0:
			return img
		result = img.copy()
		h, w = img.shape[:2]
		is_color = len(img.shape) == 3
		for _ in range(n):
			x1 = np.random.randint(0, w)
			y1 = np.random.randint(0, h)
			x2 = np.random.randint(0, w)
			y2 = np.random.randint(0, h)
			pts = self._line_points(x1, y1, x2, y2)
			if len(pts) == 0:
				continue
			if is_color:
				mean_color = np.mean(result[pts[:, 1], pts[:, 0]], axis=0).astype(np.uint8)
				color = tuple(mean_color.tolist())
			else:
				mean_color = int(np.mean(result[pts[:, 1], pts[:, 0]]))
				color = mean_color
			cv2.line(result, (x1, y1), (x2, y2), color, 8)
		return result

	def _line_points(self, x1, y1, x2, y2):
		dx = abs(x2 - x1)
		dy = -abs(y2 - y1)
		sx = 1 if x1 < x2 else -1
		sy = 1 if y1 < y2 else -1
		err = dx + dy
		points = []
		while True:
			points.append((x1, y1))
			if x1 == x2 and y1 == y2:
				break
			e2 = 2 * err
			if e2 >= dy:
				err += dy
				x1 += sx
			if e2 <= dx:
				err += dx
				y1 += sy
		return np.array(points, dtype=np.int32)

	def max_channel_gradient(self, img):
		if len(img.shape) == 3:
			max_grad = 0
			for ch in range(3):
				channel = img[:, :, ch].astype(np.int16)
				dx = np.abs(np.diff(channel, axis=1))
				dy = np.abs(np.diff(channel, axis=0))
				max_grad = max(max_grad, dx.max(), dy.max())
			return int(max_grad)
		else:
			channel = img.astype(np.int16)
			dx = np.abs(np.diff(channel, axis=1))
			dy = np.abs(np.diff(channel, axis=0))
			return int(max(dx.max(), dy.max()))

	def apply_skin_blue(self, img, alpha):
		if alpha <= 0 or len(img.shape) != 3:
			return img

		b = img[:, :, 0].astype(np.float32)
		g = img[:, :, 1].astype(np.float32)
		r = img[:, :, 2].astype(np.float32)

		Y = 0.299 * r + 0.587 * g + 0.114 * b
		Cr = (r - Y) * 0.713 + 128
		Cb = (b - Y) * 0.564 + 128
		mask_ycrcb = (Cr >= 133) & (Cr <= 173) & (Cb >= 77) & (Cb <= 127)

		maxc = np.maximum(np.maximum(r, g), b)
		minc = np.minimum(np.minimum(r, g), b)
		diff = maxc - minc

		hue = np.zeros_like(r)
		safe_diff = np.where(diff == 0, 1, diff)
		mask_r = (maxc == r)
		mask_g = (maxc == g)
		mask_b = (maxc == b)

		hue[mask_r] = ((g - b) / safe_diff)[mask_r] % 6
		hue[mask_g] = ((b - r) / safe_diff)[mask_g] + 2
		hue[mask_b] = ((r - g) / safe_diff)[mask_b] + 4
		hue = hue * 60

		sat = np.where(maxc == 0, 0, diff / np.where(maxc == 0, 1, maxc))
		val = maxc / 255.0

		mask_hsv = ((hue >= 0) & (hue <= 50) | (hue >= 340)) & (sat >= 0.2) & (val >= 0.2)

		final_mask = mask_ycrcb | mask_hsv

		Y_avg = ((r + g + b) / 3).astype(np.uint8)
		blue_img = img.copy()

		skin_pixels_count = int(final_mask.sum())
		new_pixels = np.zeros((skin_pixels_count, 3), dtype=np.uint8)
		new_pixels[:, 0] = Y_avg[final_mask]

		blue_img[final_mask] = new_pixels

		result = img.astype(np.float32) * (1 - alpha) + blue_img.astype(np.float32) * alpha
		return np.clip(result, 0, 255).astype(np.uint8)

	def apply_combined(self):
		if self.original_img is None:
			return

		current = self.original_img.copy()
		kernel_size = 3
		max_kernel = min(current.shape[0], current.shape[1])
		if max_kernel % 2 == 0:
			max_kernel -= 1

		iterations_done = 0
		lines_drawn = 0
		threshold_stop = self.stop_threshold
		threshold_replace = self.replace_threshold
		max_iter = self.max_iterations
		amplitude = self.noise_amplitude
		lines_total = self.lines_count

		for _ in range(max_iter):
			grad = self.max_channel_gradient(current)
			if grad <= threshold_stop:
				break
			current = self.smooth_edges_core(current, threshold_replace, kernel_size, amplitude)
			iterations_done += 1
			if kernel_size < max_kernel:
				kernel_size += 2
			if lines_drawn < lines_total:
				current = self.draw_lines(current, 1)
				lines_drawn += 1
			QApplication.processEvents()

		shift = self.shift_px
		alpha = self.alpha_percent / 100.0
		if shift > 0:
			h, w = current.shape[:2]
			shift_x = np.random.randint(-shift, shift + 1)
			shift_y = np.random.randint(-shift, shift + 1)
			M = np.float32([[1, 0, shift_x], [0, 1, shift_y]])
			shifted = cv2.warpAffine(current, M, (w, h), borderMode=cv2.BORDER_REFLECT)
			current = cv2.addWeighted(current, 1 - alpha, shifted, alpha, 0)

		chess_size = self.chess_size
		if chess_size > 0:
			h, w = current.shape[:2]
			alpha_c = 1 - 2 * self.invert_ratio
			beta_c = 255 * self.invert_ratio

			for y in range(0, h, chess_size):
				for x in range(0, w, chess_size):
					if (x // chess_size + y // chess_size) % 2 == 0:
						if np.random.rand() < self.chess_prob:
							block = current[y:y+chess_size, x:x+chess_size]
							if block.size == 0:
								continue
							current[y:y+chess_size, x:x+chess_size] = (block * alpha_c + beta_c).astype(np.uint8)

		if self.skin_alpha > 0:
			current = self.apply_skin_blue(current, self.skin_alpha)

		self.current_img = current
		self.display_image(current)
		self.copy_btn.setEnabled(True)

		self.statusBar().showMessage(
			f"Искажение: {self.level_spin.value()} | "
			f"Пороги: {self.stop_threshold}/{self.replace_threshold} | "
			f"Итераций: {iterations_done}/{max_iter}, Линий: {lines_drawn} | "
			f"Сдвиг: {shift}px, Прозрачность: {int(alpha*100)}% | "
			f"Клетка: {chess_size}px | Инверсия: {int(self.invert_ratio * 100)}% | "
			f"Вероятность: {int(self.chess_prob * 100)}% | Кожа: {int(self.skin_alpha * 100)}%"
		)

	def display_image(self, img):
		if img.ndim == 3:
			if img.shape[2] == 4:
				img = img[:, :, :3]
			height, width, channels = img.shape
			bytes_per_line = channels * width
			qimg = QImage(img.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).rgbSwapped()
		else:
			height, width = img.shape
			qimg = QImage(img.data, width, height, width, QImage.Format.Format_Grayscale8)
		pixmap = QPixmap.fromImage(qimg)
		self.image_label.setPixmap(pixmap.scaled(
			self.image_label.size(),
			Qt.AspectRatioMode.KeepAspectRatio,
			Qt.TransformationMode.SmoothTransformation
		))

	def copy_to_clipboard(self):
		if self.current_img is None:
			return
		img = self.current_img
		if img.ndim == 3:
			if img.shape[2] == 4:
				img = img[:, :, :3]
			height, width, channels = img.shape
			bytes_per_line = channels * width
			qimg = QImage(img.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).rgbSwapped()
		else:
			height, width = img.shape
			qimg = QImage(img.data, width, height, width, QImage.Format.Format_Grayscale8)
		pixmap = QPixmap.fromImage(qimg)
		QApplication.clipboard().setPixmap(pixmap)
		self.statusBar().showMessage("Изображение скопировано в буфер обмена.")

	def resizeEvent(self, event):
		if self.current_img is not None and self.show_image:
			self.display_image(self.current_img)
		super().resizeEvent(event)


if __name__ == "__main__":
	app = QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec())