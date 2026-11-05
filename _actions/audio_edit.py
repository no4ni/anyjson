"""Затухание аудио (ffmpeg afade)."""
import subprocess
import time
from pathlib import Path

from _actions import register
from _config import MY_DIR


def _ffmpeg():
    return "ffmpeg"


def _ffprobe():
    return "ffprobe"


def _probe_duration(path):
    try:
        r = subprocess.run(
            [_ffprobe(), "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        d = float((r.stdout or "").strip())
    except ValueError:
        return None
    if d <= 0:
        return None
    return d


_VALID_CURVES = ("tri", "qsin", "esin", "hsin", "log", "exp", "cbr")


@register(["затухание_звука", "затухание_аудио", "фейд_аудио",
           "фейд_звука", "плавный_звук"])
def audio_fade(args: dict) -> str:
    """Плавное появление/затухание аудио (ffmpeg afade).

    Параметры:
      звук          — вход (обязательно)
      вход          — секунды fade in (по умолч. 0)
      выход         — секунды fade out (по умолч. 0)
      начало_выхода — с какой секунды начинать fade out
                      (по умолч. авто = длительность − выход)
      кривая        — tri/qsin/esin/hsin/log/exp/cbr (по умолч. tri)
      сохранить     — куда (иначе MY_DIR/audio_edit/<TS>_fade.<ext>)
      перезаписать  — false: не трогать существующий файл
    """
    звук = args.get("звук") or args.get("аудио") or args.get("файл")
    if not звук:
        return "Ошибка: не указан звук"
    sp = Path(звук)
    if not sp.is_file():
        return "Ошибка: нет звука " + str(sp)

    try:
        fade_in = float(args.get("вход") or 0.0)
    except (TypeError, ValueError):
        return "Ошибка: вход должен быть числом (секунды)"
    if fade_in < 0:
        fade_in = 0.0

    try:
        fade_out = float(args.get("выход") or 0.0)
    except (TypeError, ValueError):
        return "Ошибка: выход должен быть числом (секунды)"
    if fade_out < 0:
        fade_out = 0.0

    if fade_in == 0 and fade_out == 0:
        return "Ошибка: задай «вход» и/или «выход» > 0"

    curve = str(args.get("кривая") or "tri").strip().lower()
    if curve not in _VALID_CURVES:
        return ("Ошибка: кривая должна быть одна из: "
                + ", ".join(_VALID_CURVES))

    start_out_raw = args.get("начало_выхода")
    if start_out_raw is None:
        start_out_raw = args.get("с_секунды")
    start_out = None
    if start_out_raw is not None:
        try:
            start_out = float(start_out_raw)
        except (TypeError, ValueError):
            return "Ошибка: начало_выхода должно быть числом"
        if start_out < 0:
            start_out = 0.0

    if fade_out > 0 and start_out is None:
        dur = _probe_duration(sp)
        if dur is None:
            return ("Ошибка: не удалось определить длительность звука "
                    "(ffprobe). Задай начало_выхода явно.")
        start_out = max(0.0, dur - fade_out)

    out_arg = args.get("сохранить") or args.get("куда")
    overwrite = bool(args.get("перезаписать", False))

    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "audio_edit" / (ts + "_fade" + sp.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    filters = []
    if fade_in > 0:
        filters.append("afade=t=in:st=0:d=" + ("%.3f" % fade_in)
                       + ":curve=" + curve)
    if fade_out > 0:
        filters.append("afade=t=out:st=" + ("%.3f" % start_out)
                       + ":d=" + ("%.3f" % fade_out)
                       + ":curve=" + curve)
    af = ",".join(filters)

    ext = sp.suffix.lower()
    if ext == ".mp3":
        codec = ["-c:a", "libmp3lame", "-b:a", "192k"]
    elif ext == ".wav":
        codec = ["-c:a", "pcm_s16le"]
    elif ext == ".flac":
        codec = ["-c:a", "flac"]
    elif ext == ".ogg":
        codec = ["-c:a", "libvorbis", "-b:a", "192k"]
    elif ext == ".opus":
        codec = ["-c:a", "libopus", "-b:a", "128k"]
    else:
        codec = ["-c:a", "aac", "-b:a", "192k"]

    cmd = ([_ffmpeg(), "-y", "-i", str(sp), "-af", af]
           + codec + [str(out_path)])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=600)
    except FileNotFoundError:
        return "Ошибка: ffmpeg не найден в PATH"
    except subprocess.TimeoutExpired:
        return "Ошибка: таймаут 10 мин"

    if r.returncode != 0:
        err = (r.stderr or "")[-600:]
        return "Ошибка ffmpeg (код " + str(r.returncode) + "):\n" + err

    if not out_path.is_file():
        return "Ошибка: файл не создан: " + str(out_path)

    size = out_path.stat().st_size
    parts = []
    if fade_in > 0:
        parts.append("in " + ("%.2f" % fade_in) + "s")
    if fade_out > 0:
        parts.append("out " + ("%.2f" % fade_out)
                     + "s @" + ("%.2f" % start_out) + "s")
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, "
            + ", ".join(parts) + ", кривая " + curve + ")")