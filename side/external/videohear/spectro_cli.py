#!/usr/bin/env python3
"""CLI: скриншот полного layout плеера (спектрограмма + видео + подписи) -> буфер.
Использование:
    python spectro_cli.py VIDEO --секунда 30 [--сохранить out.png] [--размер 1366x768]

Работает через offscreen-режим Qt: запускает настоящий AudioPlayer,
устанавливает позицию, обновляет layout, делает grab() окна.
"""
import os
import sys
import io
import argparse

# offscreen Qt + dummy audio — ДО импорта PyQt5 / pygame
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass

from PyQt5.QtWidgets import QApplication, QFileDialog
from PyQt5.QtGui import QFont
from PyQt5.QtCore import QBuffer, QIODevice
import pygame
from PIL import Image

try:
    import win32clipboard
    import win32con
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

# импорт плеера
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import videohear2


def qpixmap_to_pil(pixmap):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    buf.close()
    img = Image.open(io.BytesIO(bytes(buf.data())))
    return img.convert("RGB")


def copy_image_to_clipboard(pil_image):
    if pil_image is None:
        return False
    if not HAS_PYWIN32:
        return False
    try:
        buf = io.BytesIO()
        pil_image.convert("RGB").save(buf, "BMP")
        data = buf.getvalue()[14:]
        buf.close()
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        print(f"[clip] fail: {e}", file=sys.stderr)
        return False


def parse_size(s):
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:
        return 1366, 768


def main():
    p = argparse.ArgumentParser(description="Скриншот layout плеера -> буфер.")
    p.add_argument("video", help="путь к видеофайлу")
    p.add_argument("--секунда", "--sec", dest="sec", type=float, required=True)
    p.add_argument("--сохранить", "--save", dest="save", default=None)
    p.add_argument("--размер", "--size", dest="size", default="1366x768")
    p.add_argument("--no-clip", "--no_clip", dest="no_clip", action="store_true",
                   help="не класть в буфер обмена")
    args = p.parse_args()

    video = args.video
    if not os.path.isfile(video):
        print(f"ERR: файл не найден: {video}")
        return 1

    sec = args.sec
    W, H = parse_size(args.size)
    print(f"REQ: {W}x{H} @ {sec:.2f}s")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Arial", 10))

    # подмена диалога — load_file получит наш путь
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **kw: (video, ""))

    player = videohear2.AudioPlayer()
    player.setFixedSize(W, H)
    player.resize(W, H)
    player.show()
    QApplication.processEvents()

    # загрузка: посчитает спектрограмму (если нет в кэше — долго)
    player.load_file()
    QApplication.processEvents()

    # фиксируем момент
    player.play_offset = 0.0
    player.current_sec = sec
    player.is_paused = True

    # подменяем pygame — чтобы update_spectrogram_window не заблокировался
    pygame.mixer.music.get_pos = lambda: int(sec * 1000)
    pygame.mixer.music.get_busy = lambda: True

    # прогружаем окно кадров видео
    if player.video_processor.is_loaded:
        try:
            player.video_processor.set_paused(True, sec)
        except Exception as e:
            print(f"[warn] video_processor.set_paused: {e}", file=sys.stderr)

    # обновляем весь layout
    player.update_spectrogram_window()
    QApplication.processEvents()
    player.repaint()
    QApplication.processEvents()

    # скриншот — обрезаем заголовок (30px) и строку кнопок (~42px)
    TOP_CROP = 30
    BOTTOM_CROP = 42
    pixmap = player.grab()
    if pixmap.isNull():
        print("ERR: grab вернул пусто")
        return 2
    W_full = pixmap.width()
    H_full = pixmap.height()
    crop_h = max(1, H_full - TOP_CROP - BOTTOM_CROP)
    pixmap = pixmap.copy(0, TOP_CROP, W_full, crop_h)
    img = qpixmap_to_pil(pixmap)
    print(f"SIZE: {img.size[0]}x{img.size[1]}")

    if args.no_clip:
        print(f"OK: {img.size[0]}x{img.size[1]}")
    elif copy_image_to_clipboard(img):
        print(f"CLIP: {img.size[0]}x{img.size[1]} -> буфер обмена")
    else:
        print("WARN: не положить в буфер")

    if args.save:
        try:
            img.save(args.save)
            print(f"SAVE: {args.save}")
        except Exception as e:
            print(f"WARN: не сохранить: {e}")

    player.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
