#!/usr/bin/env python3
"""CLI для antiviolate9 — искажение без GUI."""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import os as _os_font
_os_font.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).parent))
from antiviolate9 import MainWindow, imread_unicode


def _imwrite_unicode(path, img):
    """cv2.imwrite не пишет пути с не-ASCII. Через cv2.imencode + tofile."""
    try:
        ext = "." + str(path).rsplit(".", 1)[-1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            ext = ".png"
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        buf.tofile(str(path))
        return True
    except Exception:
        return False


def process(input_path, output_path, level):
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.update_parameters(level)

    img = imread_unicode(input_path)
    if img is None:
        raise RuntimeError(f"Не прочитать: {input_path}")

    if img.shape[0] > 700:
        scale = 700 / img.shape[0]
        new_w = int(img.shape[1] * scale)
        img = cv2.resize(img, (new_w, 700), interpolation=cv2.INTER_AREA)

    current = img.copy()
    kernel_size = 3
    max_kernel = min(current.shape[0], current.shape[1])
    if max_kernel % 2 == 0:
        max_kernel -= 1

    iterations_done = 0
    lines_drawn = 0

    for _ in range(w.max_iterations):
        grad = w.max_channel_gradient(current)
        if grad <= w.stop_threshold:
            break
        current = w.smooth_edges_core(current, w.replace_threshold, kernel_size, w.noise_amplitude)
        iterations_done += 1
        if kernel_size < max_kernel:
            kernel_size += 2
        if lines_drawn < w.lines_count:
            current = w.draw_lines(current, 1)
            lines_drawn += 1

    shift = w.shift_px
    alpha = w.alpha_percent / 100.0
    if shift > 0:
        h, wd = current.shape[:2]
        sx = np.random.randint(-shift, shift + 1)
        sy = np.random.randint(-shift, shift + 1)
        M = np.float32([[1, 0, sx], [0, 1, sy]])
        shifted = cv2.warpAffine(current, M, (wd, h), borderMode=cv2.BORDER_REFLECT)
        current = cv2.addWeighted(current, 1 - alpha, shifted, alpha, 0)

    chess_size = w.chess_size
    if chess_size > 0:
        h, wd = current.shape[:2]
        alpha_c = 1 - 2 * w.invert_ratio
        beta_c = 255 * w.invert_ratio
        for y in range(0, h, chess_size):
            for x in range(0, wd, chess_size):
                if (x // chess_size + y // chess_size) % 2 == 0:
                    if np.random.rand() < w.chess_prob:
                        block = current[y:y+chess_size, x:x+chess_size]
                        if block.size == 0:
                            continue
                        current[y:y+chess_size, x:x+chess_size] = (
                            block * alpha_c + beta_c
                        ).astype(np.uint8)

    if w.skin_alpha > 0:
        current = w.apply_skin_blue(current, w.skin_alpha)

    _imwrite_unicode(output_path, current)
    return output_path, iterations_done, lines_drawn


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--level", type=int, default=5)
    args = p.parse_args()

    try:
        out, iters, lines = process(args.input, args.output, args.level)
    except Exception as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: {out}")
    print(f"level={args.level}, iterations={iters}, lines={lines}")


if __name__ == "__main__":
    main()
