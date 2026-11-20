"""Наложить звук на видео (ffmpeg)."""
import os
import subprocess
import sys
import time
from pathlib import Path

from _actions import register
from _config import MY_DIR


def _ffmpeg():
    # Ищем ffmpeg в PATH
    return "ffmpeg"


def _ffprobe():
    # Ищем ffprobe в PATH
    return "ffprobe"


def _probe_duration(path):
    """Длительность медиафайла в секундах или None."""
    try:
        r = subprocess.run(
            [_ffprobe(), "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
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


def _overlay_nabor(args, video, out_arg, replace, obrezat=False):
    """Наложить набор звуков на видео."""
    nabor = args.get("набор") or args.get("sounds")
    if not isinstance(nabor, list) or not nabor:
        return "Ошибка: набор должен быть непустым списком"

    vp = Path(video)
    if not vp.is_file():
        return "Ошибка: нет видео " + str(vp)

    items = []
    for i, it in enumerate(nabor):
        if not isinstance(it, dict):
            return "Ошибка: набор[#" + str(i) + "] должен быть словарём"
        spath = it.get("звук") or it.get("аудио") or it.get("wav") or it.get("mp3")
        if not spath:
            return "Ошибка: набор[#" + str(i) + "] без звука"
        spp = Path(spath)
        if not spp.is_file():
            return "Ошибка: нет файла " + str(spp)
        try:
            d = float(it.get("задержка") or it.get("delay") or 0.0)
        except (TypeError, ValueError):
            return "Ошибка: задержка в наборе[#" + str(i) + "] не число"
        if d < 0:
            d = 0.0
        try:
            vol = float(it.get("громкость") or it.get("volume") or 1.0)
        except (TypeError, ValueError):
            return "Ошибка: громкость в наборе[#" + str(i) + "] не число"
        if vol < 0:
            vol = 0.0
        if vol > 10.0:
            vol = 10.0
        items.append((spp, int(round(d * 1000)), vol))

    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "video_sound" / (ts + vp.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _ffmpeg()

    cmd = [ffmpeg, "-y", "-i", str(vp)]
    for spp, _, _ in items:
        cmd += ["-i", str(spp)]

    filters = []
    labels = []
    if not replace:
        filters.append("[0:a]anull[orig]")
        labels.append("[orig]")

    for i, (spp, dms, vol) in enumerate(items, start=1):
        chain = []
        if dms > 0:
            chain.append("adelay=" + str(dms) + "|" + str(dms))
        if vol != 1.0:
            chain.append("volume=" + str(vol))
        lab = "s" + str(i)
        if chain:
            filters.append("[" + str(i) + ":a]" + ",".join(chain) + "[" + lab + "]")
        else:
            filters.append("[" + str(i) + ":a]anull[" + lab + "]")
        labels.append("[" + lab + "]")

    n_inputs = len(labels)
    filters.append("".join(labels) + "amix=inputs=" + str(n_inputs)
                   + ":duration=longest:normalize=0[aout]")

    cmd += ["-filter_complex", ";".join(filters)]
    cmd += ["-map", "0:v:0", "-map", "[aout]"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]

    # Обрезка: при замене дорожки аудио — только звуки, поэтому
    # -shortest уже даёт обрезку по концу последнего звука. При
    # смешении оригинал тянется на всё видео — режем явным -t.
    if obrezat and not replace:
        target = 0.0
        for spp, dms, _ in items:
            d = _probe_duration(spp)
            if d is None:
                target = None
                break
            target = max(target, dms / 1000.0 + d)
        if target and target > 0:
            cmd += ["-t", ("%.3f" % target)]
        else:
            cmd += ["-shortest"]
    else:
        cmd += ["-shortest"]

    cmd += [str(out_path)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", timeout=600)
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
    mode = "замена" if replace else "смешение"
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, " + mode
            + ", звуков " + str(len(items)) + ")")


@register(["наложить_звук", "наложить_аудио", "подложить_звук"])
def overlay_sound(args: dict) -> str:
    """Приклеить аудио к видео.

    Поддерживаемые форматы звука: WAV, MP3, FLAC, M4A, AAC, OGG,
    OPUS, WMA — всё, что умеет ffmpeg.

    Параметры:
      видео    — путь к видеофайлу (обязательно)
      звук / аудио — путь к аудиофайлу (обязательно)
      путь / сохранить / выход — куда (иначе MY_DIR/video_sound/<TS>.<ext>)
      заменить — true (по умолч.): заменить аудиодорожку;
                  false: смешать с оригинальным звуком
      задержка — сдвиг звука вправо, сек (0 — со старта)
      набор    — список звуков: [{"звук": "путь.mp3",
                  "задержка": 5.0, "громкость": 0.4}, ...].
                  При наборе одиночные звук/задержка игнорируются.
                  громкость — множитель (1.0 = как есть),
                  звуки суммируются без нормализации.
      обрезать — true: обрезать видео по длине звука
                  (для «набор» — по концу последнего звука).
                  При замене дорожки без задержки -shortest уже
                  стоит по умолчанию; флаг включает обрезку при
                  задержке > 0 и в режиме смешения.
                  По умолчанию false.
    """
    video = args.get("видео") or args.get("video")
    out_arg = args.get("путь") or args.get("сохранить") or args.get("выход")
    replace = bool(args.get("заменить", True))
    obrezat = bool(args.get("обрезать", False))

    if args.get("набор") is not None or args.get("sounds") is not None:
        if not video:
            return "Ошибка: не указан видео"
        return _overlay_nabor(args, video, out_arg, replace, obrezat)

    sound = args.get("звук") or args.get("аудио") or args.get("wav") or args.get("mp3")
    try:
        delay_sec = float(args.get("задержка") or args.get("delay") or 0.0)
    except (TypeError, ValueError):
        return "Ошибка: задержка должна быть числом (секунды)"
    if delay_sec < 0:
        delay_sec = 0.0
    delay_ms = int(round(delay_sec * 1000))

    if not video:
        return "Ошибка: не указан видео"
    if not sound:
        return "Ошибка: не указан звук"

    vp = Path(video)
    sp = Path(sound)
    if not vp.is_file():
        return "Ошибка: нет видео " + str(vp)
    if not sp.is_file():
        return "Ошибка: нет звука " + str(sp)

    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "video_sound" / (ts + vp.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _ffmpeg()

    if replace:
        if delay_ms > 0:
            cmd = [ffmpeg, "-y",
                   "-i", str(vp),
                   "-i", str(sp),
                   "-filter_complex", "[1:a]adelay=" + str(delay_ms) + "|" + str(delay_ms) + "[a]",
                   "-map", "0:v:0", "-map", "[a]",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
            if obrezat:
                cmd += ["-shortest"]
            cmd += [str(out_path)]
        else:
            # -shortest: если звук длиннее видео — обрезаем по короткому.
            cmd = [ffmpeg, "-y",
                   "-i", str(vp),
                   "-i", str(sp),
                   "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-shortest",
                   str(out_path)]
    else:
        if delay_ms > 0:
            cmd = [ffmpeg, "-y",
                   "-i", str(vp),
                   "-i", str(sp),
                   "-filter_complex",
                   "[0:a]anull[a0];[1:a]adelay=" + str(delay_ms) + "|" + str(delay_ms) + "[a1];[a0][a1]amix=inputs=2:duration=first[a]",
                   "-map", "0:v:0", "-map", "[a]",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
            if obrezat:
                sdur = _probe_duration(sp)
                if sdur is not None:
                    cmd += ["-t", ("%.3f" % (delay_sec + sdur))]
            cmd += [str(out_path)]
        else:
            cmd = [ffmpeg, "-y",
                   "-i", str(vp),
                   "-i", str(sp),
                   "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first[a]",
                   "-map", "0:v:0", "-map", "[a]",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
            if obrezat:
                sdur = _probe_duration(sp)
                if sdur is not None:
                    cmd += ["-t", ("%.3f" % sdur)]
            cmd += [str(out_path)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", timeout=600)
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
    mode = "замена" if replace else "смешение"
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, " + mode + ")")
