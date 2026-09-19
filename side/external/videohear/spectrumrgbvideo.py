import numpy as np

class MultiChannelTransformer:
	def __init__(self, target_rows=320, target_fps=260.0):
		self.target_rows = target_rows
		self.target_fps = target_fps

	def build_weights(self, freq_centers, freq_widths, points_r, points_g, points_b):
		weights_r = self._compute_weights_from_points(freq_centers, freq_widths, points_r, dtype=np.float32)
		weights_g = self._compute_weights_from_points(freq_centers, freq_widths, points_g, dtype=np.float32)
		weights_b = self._compute_weights_from_points(freq_centers, freq_widths, points_b, dtype=np.float32)
		return weights_r, weights_g, weights_b

	def _compute_weights_from_points(self, freq_centers, freq_widths, points, dtype=np.float32):
		F = len(freq_centers)
		weights = np.zeros((self.target_rows, F), dtype=dtype)
		points = sorted(points, key=lambda x: x[0])
		rows = np.array([p[0] for p in points])
		centers = np.array([p[1] for p in points])
		bws = np.array([p[2] for p in points])
		repeats = np.array([p[3] for p in points])

		for i in range(self.target_rows):
			idx = np.searchsorted(rows, i) - 1
			if idx < 0:
				idx = 0
			if idx >= len(rows) - 1:
				idx = len(rows) - 2

			r1, c1, bw1 = rows[idx], centers[idx], bws[idx]
			r2, c2, bw2 = rows[idx+1], centers[idx+1], bws[idx+1]

			if r2 != r1:
				t = (i - r1) / (r2 - r1)
			else:
				t = 0

			center = c1 + (c2 - c1) * t
			bw = bw1 + (bw2 - bw1) * t

			nearest_idx = np.argmin(np.abs(rows - i))
			repeat = repeats[nearest_idx]

			bw = max(1.0, bw)
			repeat = max(1.0, repeat)

			intervals = []

			if 148 <= i <= 266:
				cur_center = center
				cur_bw = bw
				step_factor = 2.0
				while cur_center < 20000:
					if cur_center < 200:
						amp_scale = 0.0
					elif cur_center <= 250:
						amp_scale = (cur_center - 200) / (250 - 200)
					elif cur_center <= 520:
						amp_scale = 1.0
					elif cur_center <= 4186:
						amp_scale = 1.0 - (cur_center - 520) / (4186 - 520)
					else:
						amp_scale = 0.0

					low = cur_center - cur_bw / 2
					high = cur_center + cur_bw / 2
					if low < 20:
						low = 20
					if high > 20000:
						high = 20000
					if low < high:
						intervals.append((low, high, amp_scale))
					cur_center *= step_factor
					cur_bw = bw
			else:
				low = center - bw / 2
				high = center + bw / 2
				if low < 20:
					low = 20
				if high > 20000:
					high = 20000
				if low < high:
					intervals.append((low, high, 1.0))

			for low, high, amp_scale in intervals:
				for f in range(F):
					f_center = freq_centers[f]
					hw = freq_widths[f] / 2.0
					interval_center = (low + high) / 2.0
					interval_hw = (high - low) / 2.0
					d = abs(f_center - interval_center)
					max_dist = hw + interval_hw
					if d < max_dist:
						weight = 1.0 - (d / max_dist)
						weights[i, f] += weight * amp_scale

			row_sum = weights[i, :].sum()
			if row_sum > 0:
				weights[i, :] /= row_sum

		return weights

	def build_frequency_axes(self, sr, spec_data, idx_200, idx_300, idx_3400):
		window_low = int(sr * 0.5)
		if window_low % 2 != 0:
			window_low += 1
		window_mid_low = int(sr * 0.1)
		if window_mid_low % 2 != 0:
			window_mid_low += 1
		window_mid_high = int(sr * 0.066)
		if window_mid_high % 2 != 0:
			window_mid_high += 1
		window_high = int(sr * 0.005)
		if window_high % 2 != 0:
			window_high += 1

		low_count = idx_200
		mid_low_count = idx_300 - idx_200
		mid_high_count = idx_3400 - idx_300
		high_count = spec_data.shape[0] - idx_3400

		freq_low = np.arange(low_count) * (sr / window_low)
		start_200_mid_low = int(200 / (sr / window_mid_low))
		freq_mid_low = np.arange(start_200_mid_low, start_200_mid_low + mid_low_count) * (sr / window_mid_low)
		start_300_mid_high = int(300 / (sr / window_mid_high))
		freq_mid_high = np.arange(start_300_mid_high, start_300_mid_high + mid_high_count) * (sr / window_mid_high)
		start_3400_high = int(3400 / (sr / window_high))
		freq_high = np.arange(start_3400_high, start_3400_high + high_count) * (sr / window_high)

		freq_centers = np.concatenate([freq_low, freq_mid_low, freq_mid_high, freq_high])

		freq_widths = np.zeros_like(freq_centers)
		if len(freq_centers) > 1:
			freq_widths[0] = freq_centers[1] - freq_centers[0]
			for i in range(1, len(freq_centers) - 1):
				freq_widths[i] = (freq_centers[i+1] - freq_centers[i-1]) / 2
			freq_widths[-1] = freq_centers[-1] - freq_centers[-2]
		else:
			freq_widths[0] = 1.0
		return freq_centers, freq_widths


def get_default_points():
	mid_rows = [148, 158, 167, 177, 187, 197, 207, 217, 226, 236, 246, 256]
	mid_centers = [51.913, 48.999, 46.249, 43.654, 41.203, 38.891, 36.708, 34.648, 32.703, 30.868, 29.135, 27.5]

	points_r = [(0, 170, 300, 20000)] + \
			   [(69, 30, 20.0, 20000)] + \
			   [(138, 310, 20.0, 20000)] + \
			   [(row, center-1, 5.0, 100) for row, center in zip(mid_rows, mid_centers)] + \
			   [(320, 160, 160, 20000)]

	points_g = [(0, 2720, 4800, 20000)] + \
			   [(69, 310, 20.0, 20000)] + \
			   [(138, 5310, 20.0, 20000)] + \
			   [(row, center, 10.0, 100) for row, center in zip(mid_rows, mid_centers)] + \
			   [(320, 320, 360, 20000)]

	points_b = [(0, 12840, 15440, 20000)] + \
			   [(69, 5310, 20.0, 20000)] + \
			   [(138, 19990, 20.0, 20000)] + \
			   [(row, center+1, 20.0, 100) for row, center in zip(mid_rows, mid_centers)] + \
			   [(320, 385, 430, 20000)]

	return points_r, points_g, points_b


def enhance_mid_frequencies(r, g, b):
	r_enh = r.copy()
	g_enh = g.copy()
	b_enh = b.copy()

	r_mid = r_enh[148:266, :].astype(np.float32)
	g_mid = g_enh[148:266, :].astype(np.float32)
	b_mid = b_enh[148:266, :].astype(np.float32)

	product = np.cbrt(r_mid * g_mid * b_mid)
	product[product < 70] = 0
	mask1 = (product > 130) & (product <= 150)
	mask2 = (product > 150) & (product <= 255)
	product[mask1] = 200

	# Диапазон (150, 255] → линейно от 200 до 255
	product[mask2] = 200 + (product[mask2] - 150) * (55 / 105)

	r_enh[148:266, :] = product.astype(np.uint8)
	g_enh[148:266, :] = product.astype(np.uint8)
	b_enh[148:266, :] = product.astype(np.uint8)

	return r_enh, g_enh, b_enh