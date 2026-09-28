#!/usr/bin/env python3
"""CLI: скриншот спектрограммы hear3secautodetect3 -> буфер."""
import os
import sys
import io
import argparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false;*.debug=false")

import warnings as _warnings
_warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from PyQt5.QtWidgets import QApplication, QFileDialog
from PyQt5.QtGui import QFont
from PyQt5.QtCore import QBuffer, QIODevice
from PIL import Image
import pygame

try:
    import win32clipboard
    import win32con
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

import hear3secautodetect3 as hear


def qpixmap_to_pil(pixmap):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    buf.close()
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def copy_image_to_clipboard(pil_image):
    if pil_image is None or not HAS_PYWIN32:
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
        return 1370, 675


def main():
    p = argparse.ArgumentParser(description="Спектрограмма аудио -> буфер.")
    p.add_argument("audio", help="путь к аудиофайлу")
    p.add_argument("--секунда", "--sec", dest="sec", type=float, required=True)
    p.add_argument("--сохранить", "--save", dest="save", default=None)
    p.add_argument("--размер", "--size", dest="size", default="1370x675")
    p.add_argument("--no-clip", "--no_clip", dest="no_clip", action="store_true",
                   help="не класть в буфер обмена")
    args = p.parse_args()

    audio = args.audio
    if not os.path.isfile(audio):
        print(f"ERR: файл не найден: {audio}")
        return 1

    sec = args.sec
    W, H = parse_size(args.size)

    app = QApplication.instance() or QApplication(sys.argv)

    QFileDialog.getOpenFileName = staticmethod(lambda *a, **kw: (audio, ""))

    player = hear.AudioPlayer()
    # перебиваем frameless-fullscreen из __init__
    player.spec_widget.setFixedSize(W, H)
    player.setFixedSize(W, H + 30 + 42)
    player.show()
    QApplication.processEvents()

    import contextlib as _ctx
    _sink = io.StringIO()
    with _ctx.redirect_stdout(_sink):
        player.load_file()
    QApplication.processEvents()

    if player.spec_r is None:
        print("ERR: спектрограмма не построена")
        return 2

    player.play_offset = 0.0
    player.current_sec = sec
    player.is_paused = True

    pygame.mixer.music.get_pos = lambda: int(sec * 1000)
    pygame.mixer.music.get_busy = lambda: True

    player.update_spectrogram_window()
    QApplication.processEvents()
    player.spec_widget.repaint()
    QApplication.processEvents()

    pixmap = player.spec_widget.grab()
    if pixmap.isNull():
        print("ERR: grab вернул пусто")
        return 3

    img = qpixmap_to_pil(pixmap)

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

