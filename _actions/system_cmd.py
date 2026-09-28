"""Системные команды-однословники (искз, ...).

Счётчики сбрасываются любой другой командой.
"""
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

_COUNTERS = {}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def reset():
    _COUNTERS.clear()


def _read_clipboard_file():
    """Путь из буфера: CF_HDROP (файл) или CF_DIB → PNG."""
    import io
    try:
        import win32clipboard
    except ImportError:
        return None

    data_dib = None
    hdrop = None
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                hdrop = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
                data_dib = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None

    if hdrop:
        if isinstance(hdrop, (tuple, list)) and hdrop:
            p = str(hdrop[0])
        else:
            p = str(hdrop)
        if p and os.path.isfile(p):
            return p

    if data_dib:
        try:
            from PIL import Image
            bmp_header = b"BM" + struct.pack("<IHHI", 14 + len(data_dib), 0, 0, 14)
            img = Image.open(io.BytesIO(bmp_header + data_dib))
            img.load()
            fd, tmp = tempfile.mkstemp(suffix=".png", prefix="iskz_")
            os.close(fd)
            img.convert("RGB").save(tmp, "PNG")
            return tmp
        except Exception:
            pass

    return None


def _do_iskz(n):
    """Взять из буфера картинку/файл, исказить силой n, положить обратно."""
    from _actions.mask import distort_image

    src = _read_clipboard_file()
    if not src:
        return "❌ В буфере нет файла или картинки."

    try:
        res = distort_image({"путь": src, "сила": n, "буфер": True})
    except Exception as e:
        return "❌ Искажение упало: " + str(e)

    # если distort_image вернул [BUF] — ок, если нет — добавим подсказку
    return "Искз #" + str(n) + ": " + res


def handle(word):
    """Вернёт результат или None, если слово не системное."""
    w = (word or "").strip().lower()
    if w == "искз":
        _COUNTERS["искз"] = _COUNTERS.get("искз", 4) + 1
        return _do_iskz(_COUNTERS["искз"])
    return None
