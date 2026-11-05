"""Автосубтитры: транскрипция видео через faster-whisper (GPU, int8) с прогрессом."""
import os
import subprocess
import sys
import time
from pathlib import Path

from _actions import register
from _actions.subtitles import _write_srt, _write_ass


def _register_nvidia_dlls():
    dll_dirs = []
    for sd in sys.path:
        if not os.path.isdir(sd):
            continue
        nv = os.path.join(sd, "nvidia")
        if not os.path.isdir(nv):
            continue
        for pkg in os.listdir(nv):
            b = os.path.join(nv, pkg, "bin")
            if os.path.isdir(b):
                dll_dirs.append(b)
                try:
                    os.add_dll_directory(b)
                except Exception:
                    pass
    if dll_dirs:
        os.environ["PATH"] = os.pathsep.join(dll_dirs + [os.environ.get("PATH", "")])
    return dll_dirs


def _ffmpeg_exe():
    try:
        from _config import CONFIG
        p = CONFIG.get("paths", {}).get("ffmpeg_exe")
        if p and Path(p).exists():
            return p
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def _ffprobe_exe():
    try:
        from _config import CONFIG
        p = CONFIG.get("paths", {}).get("ffprobe_exe")
        if p and Path(p).exists():
            return p
    except Exception:
        pass
    import shutil
    return shutil.which("ffprobe")


def _duration(video_path):
    ffprobe = _ffprobe_exe()
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return None


def _decode_audio(video_path, sr=16000):
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return None, "ffmpeg не найден"
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", video_path, "-ar", str(sr), "-ac", "1",
             "-f", "s16le", "-"],
            capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return None, "ffmpeg timeout"
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")
        if "does not contain any stream" in err or "matches no streams" in err:
            return None, "❌ В видеофайле не найдена аудиодорожка (распознавать нечего)"
        return None, "ffmpeg error: " + err[-300:]
    import numpy as np
    audio = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, None


def _default_model_dir():
    try:
        from _config import WHISPER_DIR
        return Path(WHISPER_DIR)
    except Exception:
        return Path(r"E:/JerichoSoft/models/whisper-large-v3-turbo")


def _fmt_ts(sec):
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return "{:02d}:{:02d}:{:02d},{:03d}".format(h, m, s, ms)


def _fmt_mmss(sec):
    m, s = divmod(int(sec), 60)
    return "%d:%02d" % (m, s)


def _progress_bar(cur, total, width=28):
    """Однострочный прогресс-бар через \r."""
    if total <= 0:
        return
    pct = min(100, max(0, int(cur / total * 100.0)))
    filled = int(width * pct / 100)
    if filled >= width:
        bar = "=" * width
    else:
        bar = "=" * filled + ">" + " " * (width - filled - 1)
    line = "\r\u23f3 [" + bar + "] " + ("%3d%%" % pct) + "  (" + _fmt_mmss(cur) + " / " + _fmt_mmss(total) + ")"
    sys.stdout.write(line)
    sys.stdout.flush()


def _clear_line():
    sys.stdout.write("\r" + " " * 90 + "\r")
    sys.stdout.flush()


@register(["автосубтитры", "распознать_видео", "видео_в_текст", "транскрибировать"])
def auto_subtitles(args: dict) -> str:
    """Транскрипция видео через faster-whisper (GPU int8) -> SRT.

    Параметры:
      видео     — путь к видео/аудио (обязательно), алиас: путь, файл
      выход     — куда сохранить .srt (по умолч. рядом с видео)
      язык      — код языка (по умолч. "ru")
      модель    — путь к папке модели (по умолч. Private/models/whisper-large-v3-turbo)
      формат    — srt (по умолч.) / ass / txt
      качество  — int8 (по умолч.), int8_float32, float32

    Примеры:
      {"автосубтитры": {"видео": "D:/video.mp4"}}
      {"автосубтитры": {"видео": "D:/video.mp4", "язык": "ru", "формат": "txt"}}
    """
    args = args or {}
    video = args.get("видео") or args.get("путь") or args.get("файл")
    if not video:
        return "Ошибка: нужен путь к видео (параметр 'видео')"
    vp = Path(video)
    if not vp.exists():
        return "Файл не найден: " + str(vp)

    lang = str(args.get("язык") or "ru")
    fmt = str(args.get("формат") or "srt").strip().lower()
    if fmt not in ("srt", "ass", "txt"):
        return "Ошибка: формат должен быть srt, ass или txt"
    compute = str(args.get("качество") or "int8")
    model_dir = args.get("модель") or str(_default_model_dir())
    if not Path(model_dir).is_dir():
        return "Папка модели не найдена: " + str(model_dir)
    # Принцип "никаких тихих операций": прогресс всегда идёт в консоль.
    # Параметр "тихо" убран; передача его больше не приглушает вывод
    # (и вызовет "неизвестные параметры" в _check_unknown_params).
    quiet = False

    out_arg = args.get("выход")
    if out_arg:
        out_path = Path(out_arg)
    else:
        out_path = vp.with_suffix("." + fmt)

    _register_nvidia_dlls()
    dur = _duration(str(vp)) or 0.0

    # 1. Декодируем
    if not quiet:
        print("\u23f3 декодирую аудио через ffmpeg...", flush=True)
    t0 = time.time()
    audio, err = _decode_audio(str(vp))
    if err:
        return "Ошибка декодирования: " + err
    if audio is None or len(audio) == 0:
        return "Ошибка: пустое аудио"
    dec_sec = time.time() - t0
    audio_dur = len(audio) / 16000.0
    if not quiet:
        print("\u2705 аудио: %.1f сек (декодировано за %.1f сек)" % (audio_dur, dec_sec), flush=True)

    # 2. Модель
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        return "Ошибка импорта faster-whisper: " + str(e)

    if not quiet:
        print("\u23f3 загружаю модель (%s, cuda)..." % compute, flush=True)
    t1 = time.time()
    try:
        model = WhisperModel(model_dir, device="cuda", compute_type=compute)
    except Exception as e:
        return "Ошибка загрузки модели: " + str(e)
    load_sec = time.time() - t1
    if not quiet:
        print("\u2705 модель загружена за %.1f сек" % load_sec, flush=True)

    # 3. Транскрипция с прогрессом
    if not quiet:
        print("\u23f3 распознаю речь (язык: %s). Первый сегмент может появиться через 10-20 сек..." % lang, flush=True)
    t2 = time.time()
    try:
        task = str(args.get("задача") or "transcribe").strip().lower()
        if task != "transcribe":
            return "Ошибка: задача 'translate' не поддерживается (перевод отключён)"
        segments, info = model.transcribe(
            audio, language=lang, task="transcribe", beam_size=5, vad_filter=True)
        det_lang = getattr(info, "language", None) or "?"
        det_prob = getattr(info, "language_probability", None)
        # info.duration — точнее, чем len(audio)
        try:
            total_dur = float(info.duration) if info and info.duration else audio_dur
        except Exception:
            total_dur = audio_dur
        items = []
        last_pct = -1
        for seg in segments:
            text = seg.text.strip()
            if text:
                items.append((seg.start, seg.end, text))
            if not quiet and total_dur > 0:
                cur = float(seg.end)
                pct = min(100, max(0, int(cur / total_dur * 100.0)))
                if pct != last_pct:
                    _progress_bar(cur, total_dur)
                    last_pct = pct
        if not quiet:
            _clear_line()
    except Exception as e:
        if not quiet:
            _clear_line()
        return "Ошибка транскрипции: " + str(e)
    tr_sec = time.time() - t2

    if not items:
        return "Не удалось распознать речь (0 сегментов)"

    # 4. Сохранение
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "srt":
            _write_srt(items, out_path)
        elif fmt == "ass":
            _write_ass(items, out_path)
        else:
            lines = []
            for s, e, t in items:
                lines.append("[%s -> %s] %s" % (_fmt_ts(s), _fmt_ts(e), t))
            out_path.write_text(chr(10).join(lines), encoding="utf-8")
    except Exception as e:
        return "Ошибка записи: " + str(e)

    size = out_path.stat().st_size
    rt = audio_dur / max(tr_sec, 0.001)
    det_str = str(det_lang) + ((" (%.2f)" % det_prob) if isinstance(det_prob, (int, float)) else "")
    warn = ""
    if str(det_lang).lower() != lang.lower():
        warn = chr(10) + "  ⚠️ модель определила язык: " + det_str + ", ожидался: " + lang
    return (
        "Готово: " + str(out_path) + chr(10)
        + "  сегментов: " + str(len(items)) + ", размер: " + str(size) + " байт, формат: " + fmt + chr(10)
        + "  длительность: " + _fmt_mmss(audio_dur)
        + ", распознавание: %.1f сек (%.1fx)" % (tr_sec, rt) + chr(10)
        + "  декодирование: %.1f сек, модель: %.1f сек" % (dec_sec, load_sec) + chr(10)
        + "  язык: " + det_str
        + warn
    )
