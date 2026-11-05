"""Обрезка и склейка видео (ffmpeg)."""
import os
import subprocess
import tempfile
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


def _probe_resolution(path):
    try:
        r = subprocess.run(
            [_ffprobe(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of",
             "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    s = (r.stdout or "").strip()
    if not s or "x" not in s:
        return None
    try:
        w, h = s.split("x", 1)
        return int(w), int(h)
    except ValueError:
        return None


def _probe_fps(path):
    """Вернуть fps первого видеопотока (float) или None."""
    try:
        ffprobe = _ffprobe()
        cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=r_frame_rate",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split("/")
            if len(parts) == 2:
                return float(parts[0]) / float(parts[1])
            return float(r.stdout.strip())
    except Exception:
        pass
    return None


def _pick_crf(q):
    """качество -> CRF для libx264 (0..51)."""
    if isinstance(q, bool):
        return 23
    if isinstance(q, (int, float)):
        try:
            v = int(q)
            if 0 <= v <= 51:
                return v
        except (TypeError, ValueError):
            pass
    elif isinstance(q, str):
        ql = q.strip().lower()
        if ql in ("высокое", "высокое_качество", "high", "best"):
            return 18
        if ql in ("среднее", "medium", "normal"):
            return 23
        if ql in ("низкое", "low", "fast"):
            return 28
        try:
            v = int(ql)
            if 0 <= v <= 51:
                return v
        except ValueError:
            pass
    return 23


@register(["обрезать_видео", "обрезка_видео", "обрезать_ролик", "вырезать_отрезок"])
def trim_video(args: dict) -> str:
    """Обрезать видео по времени.

    Параметры:
      видео    — путь к видеофайлу (обязательно)
      начало   — секунда начала (по умолч. 0)
      конец    — секунда конца (обязательно, если нет длительности)
      длительность — длина в секундах (альтернатива «конец»)
      сохранить — куда (иначе MY_DIR/video_edit/<TS>_trim.<ext>)
      быстро   — true: -c copy (без перекодирования; резка по
                  ключевым кадрам — неточно); по умолч. false (точно)
      качество — высокое/среднее/низкое или CRF 0..51
                  (только при перекодировании; по умолч. среднее = 23)
      перезаписать — false: не трогать существующий файл
      без_звука — true: удалить аудиодорожку (флаг -an)
    """
    video = args.get("видео") or args.get("video") or args.get("файл") or args.get("вход")
    if not video:
        return "Ошибка: не указан видео"

    vp = Path(video)
    if not vp.is_file():
        return "Ошибка: нет видео " + str(vp)

    try:
        start = float(args.get("начало") or args.get("старт")
                      or args.get("от") or 0.0)
    except (TypeError, ValueError):
        return "Ошибка: начало должно быть числом (секунды)"
    if start < 0:
        start = 0.0

    end_raw = args.get("конец") or args.get("по") or args.get("до")
    dur_raw = (args.get("длительность") or args.get("длина")
               or args.get("продолжительность"))

    end = None
    if end_raw is not None:
        try:
            end = float(end_raw)
        except (TypeError, ValueError):
            return "Ошибка: конец должен быть числом (секунды)"

    dur = None
    if dur_raw is not None:
        try:
            dur = float(dur_raw)
        except (TypeError, ValueError):
            return "Ошибка: длительность должна быть числом (секунды)"

    if end is None and dur is None:
        return "Ошибка: нужен «конец» или «длительность»"
    if dur is not None:
        if dur <= 0:
            return "Ошибка: длительность должна быть > 0"
        end = start + dur
    if end is not None and end <= start:
        return "Ошибка: конец должен быть больше начала"

    out_arg = args.get("сохранить") or args.get("куда") or args.get("выход")
    fast = bool(args.get("быстро") or args.get("копия")
                or args.get("копировать"))
    crf = _pick_crf(args.get("качество", 23))
    overwrite = bool(args.get("перезаписать", False))
    no_audio = bool(args.get("без_звука") or args.get("без_аудио") or args.get("удалить_звук"))

    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "video_edit" / (ts + "_trim" + vp.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    duration = end - start
    ffmpeg = _ffmpeg()

    if fast:
        # -ss до -i: быстрый seek по ключевым кадрам.
        cmd = [ffmpeg, "-y",
               "-ss", ("%.3f" % start),
               "-i", str(vp),
               "-t", ("%.3f" % duration),
               "-c", "copy"]
        if no_audio:
            cmd.append("-an")
        cmd.append(str(out_path))
    else:
        # -ss после -i: точная покадровая обрезка с перекодированием.
        cmd = [ffmpeg, "-y",
               "-i", str(vp),
               "-ss", ("%.3f" % start),
               "-t", ("%.3f" % duration),
               "-c:v", "libx264", "-crf", str(crf), "-preset", "medium"]
        if no_audio:
            cmd.append("-an")
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        cmd.append(str(out_path))

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
    except FileNotFoundError:
        return "Ошибка: ffmpeg не найден в PATH"
    except subprocess.TimeoutExpired:
        return "Ошибка: таймаут 30 мин"

    if r.returncode != 0:
        err = (r.stderr or "")[-600:]
        return "Ошибка ffmpeg (код " + str(r.returncode) + "):\n" + err

    if not out_path.is_file():
        return "Ошибка: файл не создан: " + str(out_path)

    size = out_path.stat().st_size
    if fast:
        mode = "копия (может быть неточно по ключевым кадрам)"
    else:
        mode = "перекодирование, CRF " + str(crf)
    no_audio_note = ", без звука" if no_audio else ""
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, "
            + ("%.2f" % start) + ".." + ("%.2f" % end)
            + " сек, " + mode + no_audio_note + ")")


@register(["склеить_видео", "склейка_видео", "склеить", "соединить_видео"])
def concat_video(args: dict) -> str:
    """Склеить несколько видеофайлов в один (в порядке списка).

    Параметры:
      видео    — список путей (>=2) или строка через «;»
      сохранить — куда (иначе MY_DIR/video_edit/<TS>_concat.mp4)
      перекодировать — true: concat-фильтр, перекодирование.
                  По умолч. false: -c copy — быстро, но нужны
                  одинаковые кодек/разрешение/fps, иначе ошибка
                  → перекодировать: true
      gpu      — true: аппаратное декодирование/масштабирование/
                  кодирование через NVIDIA (NVDEC + scale_cuda +
                  h264_nvenc/hevc_nvenc). Только при
                  перекодировать: true. При недоступности NVENC
                  или scale_cuda автоматически откатывается на CPU.
                  По умолч. false.
      кодек    — h264 (по умолч.) или hevc (только для gpu).
      качество — CRF/CQ 0..51 (по умолч. 23). Меньше — лучше.
      перезаписать — false: не трогать существующий файл
    """
    vlist = (args.get("видео") or args.get("файлы") or args.get("список")
             or args.get("видеофайлы") or args.get("вход"))
    if isinstance(vlist, str):
        vlist = [p.strip() for p in vlist.split(";") if p.strip()]
    if not isinstance(vlist, list) or len(vlist) < 2:
        return "Ошибка: «видео» должен быть списком из >=2 путей"

    paths = []
    for i, p in enumerate(vlist):
        pp = Path(p)
        if not pp.is_file():
            return "Ошибка: нет файла #" + str(i + 1) + ": " + str(pp)
        paths.append(pp)

    out_arg = args.get("сохранить") or args.get("куда") or args.get("выход")
    reencode = bool(args.get("перекодировать", False))
    overwrite = bool(args.get("перезаписать", False))
    gpu = bool(args.get("gpu", True)) and reencode
    codec = str(args.get("кодек", "h264")).lower()
    if codec not in ("h264", "hevc"):
        codec = "h264"
    try:
        quality = int(args.get("качество", 23))
    except (TypeError, ValueError):
        quality = 23
    quality = max(0, min(51, quality))

    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = (MY_DIR / "video_edit"
                    / (ts + "_concat" + paths[0].suffix))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    ffmpeg = _ffmpeg()

    if not reencode:
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False,
                    encoding="utf-8") as tmp:
                tmp_name = tmp.name
                for pp in paths:
                    p_posix = pp.as_posix()
                    if "'" in p_posix:
                        p_posix = p_posix.replace("'", "'\\''")
                    tmp.write("file '" + p_posix + "'\n")
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0",
                   "-i", tmp_name, "-c", "copy", str(out_path)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=1800)
            except FileNotFoundError:
                return "Ошибка: ffmpeg не найден в PATH"
            except subprocess.TimeoutExpired:
                return "Ошибка: таймаут 30 мин"
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
    else:
        wh = _probe_resolution(paths[0])
        fps_val = _probe_fps(paths[0]) or 30.0
        fps_str = "fps=" + str(round(fps_val, 3))
        n = len(paths)

        def build_cmd(use_gpu: bool):
            parts = []
            for i in range(n):
                # Выравнивание fps и частоты дискретизации аудио
                parts.append("[" + str(i) + ":a]aresample=48000:async=1[a" + str(i) + "]")
                if wh:
                    if use_gpu:
                        parts.append(
                            "[" + str(i) + ":v]scale_cuda="
                            + str(wh[0]) + ":" + str(wh[1])
                            + ",hwdownload,format=nv12," + fps_str + ",setsar=1[v" + str(i) + "]")
                    else:
                        parts.append(
                            "[" + str(i) + ":v]scale="
                            + str(wh[0]) + ":" + str(wh[1])
                            + "," + fps_str + ",setsar=1[v" + str(i) + "]")
                else:
                    if use_gpu:
                        parts.append(
                            "[" + str(i) + ":v]hwdownload,format=nv12," + fps_str + ",setsar=1[v" + str(i) + "]")
                    else:
                        parts.append(
                            "[" + str(i) + ":v]" + fps_str + ",setsar=1[v" + str(i) + "]")
            cat_in = "".join(
                "[v" + str(i) + "][a" + str(i) + "]" for i in range(n))
            parts.append(cat_in + "concat=n=" + str(n) + ":v=1:a=1[v][a]")
            fc = ";".join(parts)
            cmd = [ffmpeg, "-y"]
            for pp in paths:
                if use_gpu:
                    cmd += ["-hwaccel", "cuda",
                            "-hwaccel_output_format", "cuda",
                            "-i", str(pp)]
                else:
                    cmd += ["-i", str(pp)]
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
            if use_gpu:
                enc = "hevc_nvenc" if codec == "hevc" else "h264_nvenc"
                cmd += ["-c:v", enc, "-preset", "slow",
                        "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
            else:
                cmd += ["-c:v", "libx264", "-crf", str(quality),
                        "-preset", "medium"]
            cmd += ["-c:a", "aac", "-b:a", "192k", str(out_path)]
            return cmd

        use_gpu = False
        if gpu:
            try:
                enc = subprocess.run(
                    [ffmpeg, "-hide_banner", "-encoders"],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30)
                filt = subprocess.run(
                    [ffmpeg, "-hide_banner", "-filters"],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30)
                has_nvenc = ("h264_nvenc" in (enc.stdout or "")
                             or "hevc_nvenc" in (enc.stdout or ""))
                has_scale = "scale_cuda" in (filt.stdout or "")
                use_gpu = has_nvenc and has_scale
            except Exception:
                use_gpu = False

        cmd = build_cmd(use_gpu)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=3600)
        except FileNotFoundError:
            return "Ошибка: ffmpeg не найден в PATH"
        except subprocess.TimeoutExpired:
            return "Ошибка: таймаут 60 мин"

        if r.returncode != 0 and use_gpu:
            cmd = build_cmd(False)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=3600)
            except FileNotFoundError:
                return "Ошибка: ffmpeg не найден в PATH"
            except subprocess.TimeoutExpired:
                return "Ошибка: таймаут 60 мин"
            if r.returncode == 0:
                use_gpu = False

    if r.returncode != 0:
        err = (r.stderr or "")[-600:]
        hint = ""
        if not reencode:
            hint = ("\n\n💡 Быстрая склейка требует одинаковых "
                    "кодеков/разрешения/fps. Попробуй с "
                    "\"перекодировать\": true.")
        return ("Ошибка ffmpeg (код " + str(r.returncode) + "):\n"
                + err + hint)

    if not out_path.is_file():
        return "Ошибка: файл не создан: " + str(out_path)

    size = out_path.stat().st_size
    mode = "перекодирование" if reencode else "копия"
    if reencode and gpu and use_gpu:
        mode += ", GPU"
    elif reencode and gpu and not use_gpu:
        mode += ", GPU→CPU (фолбэк)"
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, склеено "
            + str(len(paths)) + " файлов, " + mode + ")")
