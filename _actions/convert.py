"""Универсальная конвертация: видео, аудио, изображения, .lotty.

Маршрутизация по расширениям входа/выхода (см. convert_cmd).
Старые команды конвертировать_лотти, лотти_в_видео, кадр,
слушать_аудио, слушать_видео — остаются и используются внутри.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from _actions import register

try:
    from _config import ffmpeg_exe as _FFMPEG_FROM_CFG
except Exception:
    _FFMPEG_FROM_CFG = None

try:
    from _config import ffprobe_exe as _FFPROBE_FROM_CFG
except Exception:
    _FFPROBE_FROM_CFG = None


_VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
    ".wmv", ".m4v", ".mpg", ".mpeg", ".3gp", ".ts", ".mts", ".m2ts",
})
_AUDIO_EXTS = frozenset({
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a",
    ".opus", ".wma", ".aiff", ".aif", ".ac3", ".dts", ".mka",
})
_IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif",
    ".tif", ".tiff", ".ico", ".avif", ".heic",
})
_SVG_HTML_EXTS = frozenset({".svg", ".html", ".htm"})
_LOTTIE_EXTS = frozenset({".lotty"})

_VIDEO_CRF_MAP = {
    "копия":   None,
    "высокое": 18,
    "среднее": 23,
    "низкое":  28,
}

_AUDIO_BITRATE_MAP = {
    "высокое": "320k",
    "среднее": "192k",
    "низкое":  "128k",
}

_A_CODEC_MAP = {
    ".mp3":  ["-c:a", "libmp3lame"],
    ".wav":  ["-c:a", "pcm_s16le"],
    ".ogg":  ["-c:a", "libvorbis", "-q:a", "5"],
    ".flac": ["-c:a", "flac"],
    ".aac":  ["-c:a", "aac"],
    ".m4a":  ["-c:a", "aac"],
    ".opus": ["-c:a", "libopus"],
    ".wma":  ["-c:a", "wmav2"],
    ".aiff": ["-c:a", "pcm_s16be"],
    ".aif":  ["-c:a", "pcm_s16be"],
    ".ac3":  ["-c:a", "ac3"],
    ".dts":  ["-c:a", "dca"],
    ".mka":  ["-c:a", "copy"],
}

_IMAGE_QUALITY_MAP = {"высокое": 2, "среднее": 5, "низкое": 10}


def _ffmpeg():
    if _FFMPEG_FROM_CFG:
        p = Path(str(_FFMPEG_FROM_CFG))
        if p.is_file():
            return str(p)
    return "ffmpeg"


def _ffprobe():
    if _FFPROBE_FROM_CFG:
        p = Path(str(_FFPROBE_FROM_CFG))
        if p.is_file():
            return str(p)
    return "ffprobe"


def _duration(path):
    """Длительность в секундах через ffprobe, или None."""
    try:
        r = subprocess.run(
            [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        pass
    return None


def _fmt_time(sec):
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_size(n):
    if n > 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} МБ"
    if n > 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n} Б"


def _classify(path):
    suf = Path(str(path)).suffix.lower()
    if suf in _VIDEO_EXTS:
        return "video"
    if suf in _AUDIO_EXTS:
        return "audio"
    if suf in _IMAGE_EXTS:
        return "image"
    if suf in _SVG_HTML_EXTS:
        return "svg_html"
    if suf in _LOTTIE_EXTS:
        return "lotty"
    return None


def _default_dst_ext(kind_src, src):
    if kind_src == "video":
        return ".mp4"
    if kind_src == "audio":
        return ".mp3"
    if kind_src == "image":
        return ".jpg" if src.suffix.lower() == ".png" else ".png"
    if kind_src == "svg_html":
        return ".lotty"
    if kind_src == "lotty":
        return ".mp4"
    return ""


def _prepare_paths(args):
    """Разобрать args, вернуть (src, dst, kind_src, kind_dst).

    Бросает ValueError с готовым текстом для пользователя.
    """
    path = args.get("путь") or args.get("файл") or ""
    if not path:
        raise ValueError("❌ Укажи «путь» — входной файл.")
    src = Path(str(path)).expanduser()
    if not src.is_file():
        raise ValueError(f"❌ Файл не найден: {src}")
    if not src.suffix:
        raise ValueError(f"❌ Не могу определить формат: {src.name}")
    kind_src = _classify(src)
    if not kind_src:
        raise ValueError(
            f"❌ Неизвестный формат входа: {src.suffix}\n"
            f"   Поддержка: видео/аудио/картинки/svg/html/.lotty"
        )

    out_arg = args.get("сохранить")
    if out_arg:
        dst = Path(str(out_arg)).expanduser()
    else:
        dst = src.with_suffix(_default_dst_ext(kind_src, src))

    if not dst.suffix:
        raise ValueError(f"❌ Не могу определить формат выхода: {dst.name}")
    kind_dst = _classify(dst)
    if not kind_dst:
        raise ValueError(
            f"❌ Неизвестный формат выхода: {dst.suffix}\n"
            f"   Поддержка: видео/аудио/картинки/.lotty"
        )

    if src.resolve() == dst.resolve():
        raise ValueError(f"❌ Вход и выход совпадают: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)

    overwrite = bool(args.get("перезаписать"))
    if dst.exists() and not overwrite:
        raise ValueError(
            f"❌ Файл уже существует: {dst}\n"
            f"   Перезаписать: добавь \"перезаписать\": true\n"
            f"   Или укажи другое имя через \"сохранить\"."
        )
    return src, dst, kind_src, kind_dst


def _ensure_ffmpeg():
    ff = _ffmpeg()
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run([ff, "-version"], capture_output=True,
                       timeout=10, creationflags=_flags)
    except FileNotFoundError:
        raise ValueError(f"❌ ffmpeg не найден: {ff}")
    except Exception as e:
        raise ValueError(f"❌ ffmpeg не запускается: {e}")
    return ff


def _audio_codec_args(quality, ext):
    if quality == "копия":
        return ["-c:a", "copy"]
    base = list(_A_CODEC_MAP.get(ext, ["-c:a", "aac"]))
    bitrate = _AUDIO_BITRATE_MAP.get(quality)
    codec_name = base[1] if len(base) >= 2 else ""
    lossless = codec_name in ("flac", "pcm_s16le", "pcm_s16be", "copy")
    if bitrate and not lossless:
        base += ["-b:a", bitrate]
    return base


def _image_quality_args(quality, ext):
    q = 3
    if quality is not None and str(quality).strip() != "":
        k = str(quality).strip().lower()
        if k in _IMAGE_QUALITY_MAP:
            q = _IMAGE_QUALITY_MAP[k]
        elif k in ("копия", "copy"):
            return []
        else:
            try:
                q = int(k)
            except (TypeError, ValueError):
                q = 3
    if ext in (".jpg", ".jpeg"):
        return ["-q:v", str(q)]
    if ext == ".webp":
        return ["-quality", str(max(10, min(100, 100 - (q - 2) * 4)))]
    if ext == ".png":
        return ["-compression_level", str(max(0, min(9, q - 1)))]
    return []


def _run_ffmpeg_with_progress(cmd, total_sec, timeout=7200):
    """Запускает ffmpeg с -progress pipe:1, рисует однострочный прогресс.

    stderr читается в отдельном потоке — иначе на длинном прогоне
    буфер пайпа переполнится и процесс встанет.
    """
    out = cmd[-1]
    cmd = cmd[:-1] + ["-progress", "pipe:1", "-nostats",
                      "-loglevel", "error", out]

    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=_flags,
    )

    stderr_buf = []

    def _reader_err():
        try:
            for line in proc.stderr:
                stderr_buf.append(line)
        except Exception:
            pass

    t_err = threading.Thread(target=_reader_err, daemon=True)
    t_err.start()

    last_shown = ""
    deadline = time.time() + timeout if timeout else None
    try:
        for line in proc.stdout:
            if deadline and time.time() > deadline:
                proc.kill()
                break
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    ms = int(line.split("=", 1)[1])
                    sec = ms / 1_000_000.0
                    if total_sec and total_sec > 0:
                        pct = min(100.0, sec / total_sec * 100)
                        msg = (f"\r⏳ {pct:5.1f}% — {_fmt_time(sec)} / "
                               f"{_fmt_time(total_sec)}")
                    else:
                        msg = f"\r⏳ {_fmt_time(sec)}"
                    if msg != last_shown:
                        sys.stdout.write(msg + "    ")
                        sys.stdout.flush()
                        last_shown = msg
                except (ValueError, IndexError):
                    pass
    finally:
        if last_shown:
            sys.stdout.write("\r" + " " * 90 + "\r")
            sys.stdout.flush()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        t_err.join(timeout=2)

    return proc.returncode, "".join(stderr_buf)


def _convert_video_impl(args, src, dst):
    quality = (args.get("качество") or "копия").strip().lower()
    if quality not in _VIDEO_CRF_MAP:
        try:
            crf_int = int(quality)
            if 0 <= crf_int <= 51:
                crf = crf_int
                quality = f"crf{crf_int}"
            else:
                return (f"❌ Неизвестное качество: {quality}\n"
                        f"   Допустимо: копия, высокое, среднее, низкое, 0..51")
        except (TypeError, ValueError):
            return (f"❌ Неизвестное качество: {quality}\n"
                    f"   Допустимо: копия, высокое, среднее, низкое")
    else:
        crf = _VIDEO_CRF_MAP[quality]

    ff = _ensure_ffmpeg()
    total = _duration(src)

    base = [ff, "-y", "-i", str(src)]
    if quality == "копия":
        cmd = base + [
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dst),
        ]
    else:
        cmd = base + [
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(dst),
        ]

    _t0 = time.time()
    try:
        rc, stderr = _run_ffmpeg_with_progress(cmd, total, timeout=7200)
    except FileNotFoundError:
        return f"❌ ffmpeg не найден: {ff}"
    except Exception as e:
        return f"❌ Ошибка запуска ffmpeg: {e}"
    _dt = time.time() - _t0

    if rc != 0:
        err = (stderr or "")[-800:]
        hint = ""
        if quality == "копия":
            hint = ("\n\n💡 MP4 не принимает некоторые кодеки (VP9, AV1, "
                    "MPEG-2, DTS, AC3, FLAC, Vorbis). Попробуй:\n"
                    "   {\"конвертировать\": {\"путь\": \"...\", "
                    "\"качество\": \"среднее\"}}")
        return (f"❌ ffmpeg упал (код {rc}) за {_dt:.1f} сек.\n"
                f"{err}{hint}")

    if not dst.is_file():
        return f"❌ ffmpeg отчитался об успехе, но файла нет: {dst}"

    size = dst.stat().st_size
    mode = ("перепаковка" if quality == "копия"
            else f"перекодирование ({quality}, CRF {crf})")
    return (f"✅ {dst}\n"
            f"   {_fmt_size(size)}, {mode}, {_dt:.1f} сек")


def _convert_video_to_audio(args, src, dst):
    ff = _ensure_ffmpeg()
    quality = (args.get("качество") or "среднее").strip().lower()
    codec_args = _audio_codec_args(quality, dst.suffix.lower())
    cmd = [ff, "-y", "-i", str(src), "-vn"] + codec_args + [str(dst)]
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=7200, creationflags=_flags)
    except Exception as e:
        return f"❌ Ошибка запуска ffmpeg: {e}"
    if r.returncode != 0:
        return f"❌ ffmpeg: {(r.stderr or '')[-800:]}"
    if not dst.is_file():
        return f"❌ ffmpeg отработал, но файла нет: {dst}"
    return f"✅ {dst}\n   {_fmt_size(dst.stat().st_size)}"


def _convert_video_to_image(args, src, dst):
    ff = _ensure_ffmpeg()
    try:
        sec = float(args.get("секунда", args.get("время", 0)) or 0)
    except (TypeError, ValueError):
        sec = 0.0
    q = _image_quality_args(args.get("качество"), dst.suffix.lower())
    cmd = [ff, "-y", "-ss", str(sec), "-i", str(src),
           "-frames:v", "1"] + q + [str(dst)]
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=300, creationflags=_flags)
    except Exception as e:
        return f"❌ Ошибка запуска ffmpeg: {e}"
    if r.returncode != 0:
        return f"❌ ffmpeg: {(r.stderr or '')[-800:]}"
    if not dst.is_file():
        return f"❌ Кадр не создан"
    return f"✅ Кадр: {dst}\n   {_fmt_size(dst.stat().st_size)}"


def _convert_audio(args, src, dst):
    ff = _ensure_ffmpeg()
    quality = (args.get("качество") or "среднее").strip().lower()
    codec_args = _audio_codec_args(quality, dst.suffix.lower())
    cmd = [ff, "-y", "-i", str(src)] + codec_args + [str(dst)]
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=7200, creationflags=_flags)
    except Exception as e:
        return f"❌ Ошибка запуска ffmpeg: {e}"
    if r.returncode != 0:
        return f"❌ ffmpeg: {(r.stderr or '')[-800:]}"
    if not dst.is_file():
        return f"❌ ffmpeg отработал, но файла нет: {dst}"
    return f"✅ {dst}\n   {_fmt_size(dst.stat().st_size)}"


def _convert_audio_to_image(args, src, dst):
    ff = _ensure_ffmpeg()
    try:
        w = int(args.get("ширина", 1024))
    except (TypeError, ValueError):
        w = 1024
    try:
        h = int(args.get("высота", 512))
    except (TypeError, ValueError):
        h = 512
    cmd = [ff, "-y", "-i", str(src),
           "-lavfi", f"showspectrumpic=s={w}x{h}:legend=1",
           str(dst)]
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=600, creationflags=_flags)
    except Exception as e:
        return f"❌ Ошибка запуска ffmpeg: {e}"
    if r.returncode != 0:
        return f"❌ ffmpeg: {(r.stderr or '')[-800:]}"
    if not dst.is_file():
        return f"❌ Спектрограмма не создана"
    return f"✅ Спектрограмма: {dst}\n   {_fmt_size(dst.stat().st_size)}"


def _convert_image(args, src, dst):
    ff = _ensure_ffmpeg()
    q = _image_quality_args(args.get("качество"), dst.suffix.lower())
    cmd = [ff, "-y", "-i", str(src)] + q + [str(dst)]
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=300, creationflags=_flags)
    except Exception as e:
        return f"❌ Ошибка запуска ffmpeg: {e}"
    if r.returncode != 0:
        return f"❌ ffmpeg: {(r.stderr or '')[-800:]}"
    if not dst.is_file():
        return f"❌ Файл не создан"
    return f"✅ {dst}\n   {_fmt_size(dst.stat().st_size)}"


def _convert_svg_to_lottie(args, src, dst):
    from _actions.lottie import convert_lottie
    l_args = {"вход": str(src), "выход": str(dst)}
    if args.get("ширина") is not None:
        l_args["ширина"] = args["ширина"]
    if args.get("высота") is not None:
        l_args["высота"] = args["высота"]
    if args.get("таймаут") is not None:
        l_args["таймаут"] = args["таймаут"]
    return convert_lottie(l_args)


def _convert_lotty_to_video(args, src, dst, want_frame):
    from _actions.lottie import lottie_to_video
    l_args = {"вход": str(src), "выход": str(dst)}
    if args.get("fps") is not None:
        l_args["fps"] = args["fps"]
    if args.get("ширина") is not None:
        l_args["ширина"] = args["ширина"]
    if args.get("высота") is not None:
        l_args["высота"] = args["высота"]
    if args.get("масштаб") is not None:
        l_args["масштаб"] = args["масштаб"]
    if args.get("фон") is not None:
        l_args["фон"] = args["фон"]
    if args.get("размытие") is not None:
        l_args["размытие"] = args["размытие"]
    if args.get("кадры_маски") is not None:
        l_args["кадры_маски"] = args["кадры_маски"]
    if args.get("потоков_маски") is not None:
        l_args["потоков_маски"] = args["потоков_маски"]
    if args.get("кэш_альфы") is not None:
        l_args["кэш_альфы"] = args["кэш_альфы"]
    if args.get("кэш_текстур") is not None:
        l_args["кэш_текстур"] = args["кэш_текстур"]
    if args.get("номер_кадра") is not None:
        l_args["номер_кадра"] = args["номер_кадра"]
    if args.get("масштаб_кадра") is not None:
        l_args["масштаб_кадра"] = args["масштаб_кадра"]
    if args.get("таймаут") is not None:
        l_args["таймаут"] = args["таймаут"]
    if args.get("отладка") is not None:
        l_args["отладка"] = args["отладка"]
    q = args.get("качество")
    if q is not None and str(q).strip() != "":
        qlow = str(q).strip().lower()
        qmap = {"высокое": 4, "среднее": 8, "низкое": 15}
        l_args["качество"] = qmap.get(qlow, q)
    if want_frame:
        l_args["кадр"] = True
    return lottie_to_video(l_args)


@register(["конвертировать", "конвертация", "перекодировать"])
def convert_cmd(args: dict) -> str:
    """Конвертировать что угодно во что угодно (по расширениям).

    Поддерживаемые пары:
      видео     → видео    mkv/mp4/avi/mov/webm/... (перепаковка или перекодирование)
      видео     → аудио    извлечь звук (mp4 → mp3 и т.п.)
      видео     → картинка кадр на секунде (по умолч. 0)
      аудио     → аудио    mp3/wav/ogg/flac/aac/m4a/opus/...
      аудио     → картинка спектрограмма
      картинка  → картинка png ↔ jpg ↔ webp ↔ bmp ↔ tiff
      svg/html  → .lotty
      .lotty    → .mp4     видео
      .lotty    → .png     кадр (по расширению или "кадр": true)

    Параметры:
      путь              — входной файл (обязательно), алиас: файл
      сохранить         — куда. По умолчанию рядом, расширение по типу:
                          видео → .mp4, аудио → .mp3,
                          картинка → .jpg (если png) / .png (иначе),
                          svg/html → .lotty, lotty → .mp4
      качество          — необязательно, смысл по ветке:
                          видео:   копия (по умолч.) / высокое (CRF 18) /
                                   среднее (23) / низкое (28) / число 0..51
                          аудио:   копия / высокое (320k) / среднее (192k) /
                                   низкое (128k)
                          картинки: высокое / среднее / низкое / число (JPEG q)
                          лотти:   высокое / среднее / низкое / CRF-число
      перезаписать      — false (по умолч.): не трогать существующий файл
      удалить_исходник  — false (по умолч.)
      секунда           — только video → image: какой кадр (по умолч. 0),
                          алиас: время
      ширина / высота   — только audio → image (спектрограмма)
                          и svg/html → lotty (canvas)
      кадр              — только .lotty: true — PNG-кадр вместо MP4.
                          Выход принудительно меняется на .png даже
                          при «сохранить»: «...mp4».
      fps, масштаб, фон, размытие, номер_кадра, кадры_маски,
      потоков_маски, кэш_альфы, кэш_текстур, масштаб_кадра, таймаут,
      отладка           — только .lotty → mp4/png: пробрасываются
                          в лотти_в_видео без изменений

    Примеры:
      {"конвертировать": {"путь": "E:/video.mkv"}}
      {"конвертировать": {"путь": "E:/video.mkv",
                          "сохранить": "E:/out.mp4"}}
      {"конвертировать": {"путь": "E:/video.mp4",
                          "сохранить": "E:/audio.mp3"}}
      {"конвертировать": {"путь": "E:/video.mp4",
                          "сохранить": "E:/frame.png", "секунда": 12.5}}
      {"конвертировать": {"путь": "E:/art.svg",
                          "сохранить": "E:/art.lotty"}}
      {"конвертировать": {"путь": "E:/art.lotty",
                          "сохранить": "E:/art.mp4"}}
      {"конвертировать": {"путь": "E:/art.lotty",
                          "сохранить": "E:/preview.png", "кадр": true}}
    """
    try:
        src, dst, kind_src, kind_dst = _prepare_paths(args)
    except ValueError as e:
        return str(e)

    # .lotty: «кадр: true» = PNG-кадр, даже если сохранить оканчивается на .mp4
    if kind_src == "lotty" and bool(args.get("кадр")) and kind_dst != "image":
        dst = dst.with_suffix(".png")
        if dst.exists() and not bool(args.get("перезаписать")):
            return (f"❌ Файл уже существует: {dst}\n"
                    f"   Перезаписать: добавь \"перезаписать\": true\n"
                    f"   Или укажи другое имя через \"сохранить\".")
        kind_dst = "image"

    try:
        if kind_src == "video" and kind_dst == "video":
            result = _convert_video_impl(args, src, dst)
        elif kind_src == "video" and kind_dst == "audio":
            result = _convert_video_to_audio(args, src, dst)
        elif kind_src == "video" and kind_dst == "image":
            result = _convert_video_to_image(args, src, dst)
        elif kind_src == "audio" and kind_dst == "audio":
            result = _convert_audio(args, src, dst)
        elif kind_src == "audio" and kind_dst == "image":
            result = _convert_audio_to_image(args, src, dst)
        elif kind_src == "image" and kind_dst == "image":
            result = _convert_image(args, src, dst)
        elif kind_src == "svg_html" and kind_dst == "lotty":
            result = _convert_svg_to_lottie(args, src, dst)
        elif kind_src == "lotty" and kind_dst == "video":
            result = _convert_lotty_to_video(args, src, dst, want_frame=False)
        elif kind_src == "lotty" and kind_dst == "image":
            result = _convert_lotty_to_video(args, src, dst, want_frame=True)
        else:
            result = (f"❌ Пара не поддерживается: "
                      f"{src.suffix} → {dst.suffix}\n"
                      f"   Видео→видео, видео→аудио, видео→картинка,\n"
                      f"   аудио→аудио, аудио→картинка, картинка→картинка,\n"
                      f"   svg/html→.lotty, .lotty→.mp4, .lotty→.png")
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"❌ Ошибка конвертации: {e}"

    if (isinstance(result, str) and result.startswith("✅")
            and bool(args.get("удалить_исходник"))):
        try:
            src.unlink()
            result += f"\n   Исходник удалён: {src.name}"
        except OSError as e:
            result += f"\n   ⚠️ Не удалить исходник: {e}"
    return result