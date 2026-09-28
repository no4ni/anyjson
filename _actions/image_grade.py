"""Цветовой грейд: кривые, S-кривая, тонирование теней/светов, LUT."""
import time
from pathlib import Path

import numpy as np
from PIL import Image

from _actions import register
from _config import MY_DIR


_LUT_CACHE = {}


def _parse_color(v):
    """#RRGGBB / [r,g,b] 0..255 -> (r, g, b) 0..1 или None."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            return (max(0, min(255, int(v[0]))) / 255.0,
                    max(0, min(255, int(v[1]))) / 255.0,
                    max(0, min(255, int(v[2]))) / 255.0)
        except (TypeError, ValueError):
            return None
    if isinstance(v, str):
        t = v.strip().lower()
        if t.startswith("#"):
            h = t[1:]
            if len(h) == 3:
                h = h[0] * 2 + h[1] * 2 + h[2] * 2
            if len(h) == 6:
                try:
                    return (int(h[0:2], 16) / 255.0,
                            int(h[2:4], 16) / 255.0,
                            int(h[4:6], 16) / 255.0)
                except ValueError:
                    return None
    return None


def _load_cube(path):
    """Загрузить .cube 3D LUT. Кешируется в памяти."""
    key = str(path)
    if key in _LUT_CACHE:
        return _LUT_CACHE[key]

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("LUT не найден: " + str(p))

    size = None
    rows = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            up = line.upper()
            if up.startswith("LUT_3D_SIZE"):
                try:
                    size = int(line.split()[-1])
                except ValueError:
                    raise ValueError("Плохой LUT_3D_SIZE: " + line)
                continue
            if up.startswith(("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE")):
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
                except ValueError:
                    continue

    if size is None:
        raise ValueError("В .cube нет LUT_3D_SIZE")
    need = size * size * size
    if len(rows) < need:
        raise ValueError("В .cube мало строк: " + str(len(rows)) + " < " + str(need))

    arr = np.array(rows[:need], dtype=np.float32)
    # .cube ordering: R — самая медленная ось, B — самая быстрая
    arr = arr.reshape(size, size, size, 3)
    _LUT_CACHE[key] = arr
    return arr


def _apply_lut(img_arr, lut):
    """Трилинейная интерполяция по 3D LUT. img_arr (H,W,3) float 0..1."""
    N = lut.shape[0]
    idx = np.clip(img_arr, 0.0, 1.0) * (N - 1)
    lo = np.floor(idx).astype(np.int32)
    hi = np.minimum(lo + 1, N - 1)
    frac = (idx - lo).astype(np.float32)

    out = np.zeros_like(img_arr, dtype=np.float32)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                wx = frac[..., 0] if dx else (1.0 - frac[..., 0])
                wy = frac[..., 1] if dy else (1.0 - frac[..., 1])
                wz = frac[..., 2] if dz else (1.0 - frac[..., 2])
                w = (wx * wy * wz)[..., None]
                ix = hi[..., 0] if dx else lo[..., 0]
                iy = hi[..., 1] if dy else lo[..., 1]
                iz = hi[..., 2] if dz else lo[..., 2]
                out += lut[ix, iy, iz] * w
    return out


def _s_curve(arr, strength):
    """S-кривая (smoothstep). strength ∈ [-1, 1], 0 — линейно."""
    if strength == 0.0:
        return arr
    x = np.clip(arr, 0.0, 1.0)
    y = x * x * (3.0 - 2.0 * x)
    return x + strength * (y - x)


def _tint_range(arr, color, strength, shadows):
    """Тонирование теней (shadows=True) или светов (shadows=False)."""
    luma = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    mask = (1.0 - luma) if shadows else luma
    # smoothstep для плавного перехода
    mask = mask * mask * (3.0 - 2.0 * mask)
    mask = (mask * float(strength))[..., None]
    c = np.array(color, dtype=np.float32)
    return arr * (1.0 - mask) + c * mask


def _apply_curve(arr, points):
    """Кривая по точкам [[x,y],...]. Линейная интерполяция."""
    if not points or not isinstance(points, (list, tuple)):
        return arr
    xs, ys = [], []
    for pt in points:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                xs.append(float(pt[0]))
                ys.append(float(pt[1]))
            except (TypeError, ValueError):
                continue
    if len(xs) < 2:
        return arr
    order = np.argsort(xs)
    xs = np.array(xs, dtype=np.float32)[order]
    ys = np.array(ys, dtype=np.float32)[order]
    return np.interp(arr, xs, ys).astype(np.float32)


def _grade_array(arr, args):
    """Грейд numpy-массива (H,W,3) float 0..1."""
    c_all = args.get("кривые")
    c_r = args.get("кривые_r")
    c_g = args.get("кривые_g")
    c_b = args.get("кривые_b")
    if c_all or c_r or c_g or c_b:
        chans = [arr[..., 0], arr[..., 1], arr[..., 2]]
        if c_all:
            for i in range(3):
                chans[i] = _apply_curve(chans[i], c_all)
        if c_r:
            chans[0] = _apply_curve(chans[0], c_r)
        if c_g:
            chans[1] = _apply_curve(chans[1], c_g)
        if c_b:
            chans[2] = _apply_curve(chans[2], c_b)
        arr = np.stack(chans, axis=-1)

    try:
        s_str = float(args.get("s_кривая", args.get("s_curve", 0.0)) or 0.0)
    except (TypeError, ValueError):
        s_str = 0.0
    s_str = max(-1.0, min(1.0, s_str))
    if s_str != 0.0:
        arr = _s_curve(arr, s_str)

    sh_color = _parse_color(args.get("тени_цвет") or args.get("shadows_color"))
    if sh_color is not None:
        try:
            sh_dose = float(args.get("тени_доля", args.get("shadows_dose", 0.3)) or 0.0)
        except (TypeError, ValueError):
            sh_dose = 0.0
        sh_dose = max(0.0, min(1.0, sh_dose))
        if sh_dose > 0:
            arr = _tint_range(arr, sh_color, sh_dose, shadows=True)

    hl_color = _parse_color(args.get("света_цвет") or args.get("highlights_color"))
    if hl_color is not None:
        try:
            hl_dose = float(args.get("света_доля", args.get("highlights_dose", 0.3)) or 0.0)
        except (TypeError, ValueError):
            hl_dose = 0.0
        hl_dose = max(0.0, min(1.0, hl_dose))
        if hl_dose > 0:
            arr = _tint_range(arr, hl_color, hl_dose, shadows=False)

    lut_path = args.get("lut")
    if lut_path:
        try:
            lut = _load_cube(lut_path)
        except Exception as e:
            return "LUT: " + str(e)
        arr = _apply_lut(arr, lut)

    return np.clip(arr, 0.0, 1.0)


_VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg")


def _is_video(path):
    return Path(path).suffix.lower() in _VIDEO_EXTS


def _fmt_progress(written, total, tag="", width=20):
    if total <= 0:
        return "[" + "." * width + "] " + str(written)
    ratio = min(1.0, written / total)
    filled = int(ratio * width)
    if written >= total:
        bar = "=" * width
    elif filled >= width:
        bar = "=" * (width - 1) + ">"
    else:
        bar = "=" * filled + ">" + "." * (width - filled - 1)
    tail = " " + tag if tag else ""
    return "[" + bar + "] " + str(written) + "/" + str(total) + tail


def _progress(text, end=False):
    import sys as _sys
    if _sys.stderr.isatty():
        _sys.stderr.write("\r" + text + "\x1b[K")
    else:
        _sys.stderr.write(text + "\n")
    _sys.stderr.flush()
    if end:
        _sys.stderr.write("\n")


def _grade_video(args, src_path):
    """Покадровый грейд видео + возврат звука через ffmpeg."""
    import cv2
    import subprocess
    import tempfile
    import os as _os

    src_p = Path(src_path)
    cap = cv2.VideoCapture(str(src_p))
    if not cap.isOpened():
        return "❌ Не открыть видео: " + str(src_p)

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    out_arg = args.get("сохранить") or args.get("путь_выхода")
    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "grading_out" / (ts + src_p.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(suffix=".mp4", prefix="grade_")
    _os.close(fd)
    tmp_p = Path(tmp_name)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(tmp_p), fourcc, fps, (w, h))
    if not vw.isOpened():
        cap.release()
        try: tmp_p.unlink()
        except OSError: pass
        return "❌ Не открыть VideoWriter"

    idx = 0
    _progress(_fmt_progress(0, total, "грейд"))
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            res = _grade_array(rgb, args)
            if isinstance(res, str):
                _progress("", end=True)
                return "❌ " + res
            out_frame = (res * 255.0).astype(np.uint8)
            vw.write(cv2.cvtColor(out_frame, cv2.COLOR_RGB2BGR))
            idx += 1
            _progress(_fmt_progress(idx, total, "грейд"))
    finally:
        cap.release()
        vw.release()
        _progress("", end=True)

    cmd = ["ffmpeg", "-y",
           "-i", str(tmp_p),
           "-i", str(src_p),
           "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "copy", "-c:a", "copy", "-shortest",
           str(out_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except FileNotFoundError:
        try: tmp_p.unlink()
        except OSError: pass
        return "❌ ffmpeg не найден в PATH"
    finally:
        try: tmp_p.unlink()
        except OSError: pass

    if r.returncode != 0:
        err = (r.stderr or "")[-600:]
        return "❌ Ошибка ffmpeg: " + err

    if not out_path.is_file():
        return "❌ Файл не создан: " + str(out_path)

    size = out_path.stat().st_size
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, "
            + str(idx) + " кадров)")


@register(["грейд", "цветогрейд", "color_grade"])
def color_grade(args):
    """Цветовой грейд: кривые, S-кривая, тонирование теней/светов, LUT.

    Порядок: кривые → S-кривая → тени/света → LUT.

    Параметры:
      путь        — входной файл (обязательно)
      кривые      — 4+ точки [[x,y],...], общая кривая для всех каналов
      кривые_r    — то же для R (перебивает общую для R)
      кривые_g    — то же для G
      кривые_b    — то же для B
      s_кривая    — сила S-кривой, -1..1 (0 = выкл.)
      тени_цвет   — цвет тонирования теней (#RRGGBB / [r,g,b] 0..255)
      тени_доля   — сила 0..1 (по умолч. 0.3)
      света_цвет  — цвет тонирования светов
      света_доля  — сила 0..1 (по умолч. 0.3)
      lut         — путь к .cube файлу (3D LUT)
      сохранить   — путь для PNG (иначе MY_DIR/grading_out/)
      в_контекст  — положить в контекст (по умолч. true)

    Примеры:
      {"грейд": {"путь": "C:/in.png", "тени_цвет": "#2050A0",
                  "света_цвет": "#FFD0A0", "s_кривая": 0.4}}
      {"грейд": {"путь": "C:/in.png", "lut": "C:/lut/kodak.cube"}}
      {"грейд": {"путь": "C:/in.png",
                  "кривые": [[0,0],[0.25,0.2],[0.75,0.85],[1,1]]}}
    """
    path_arg = args.get("путь") or args.get("файл")
    if not path_arg:
        return 'Укажи путь: {"грейд": {"путь": "C:/pic.png"}}'

    if _is_video(path_arg):
        return _grade_video(args, path_arg)

    from _actions.image_effects import _load, _save_and_return
    try:
        src_p, img = _load(path_arg)
    except Exception as e:
        return "❌ " + str(e)

    has_alpha = img.mode == "RGBA"
    alpha = img.split()[-1] if has_alpha else None

    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    arr = _grade_array(arr, args)
    if isinstance(arr, str):
        return "❌ " + arr
    out = Image.fromarray((arr * 255.0).astype(np.uint8), mode="RGB")

    if has_alpha and alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)

    return _save_and_return(out, src_p, args, "grade")