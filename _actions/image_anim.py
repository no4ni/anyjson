"""Панорамирование изображения (Ken Burns): плавный зум + сдвиг → mp4."""
import subprocess
import time
from pathlib import Path

from _actions import register
from _config import MY_DIR


def _ffmpeg():
    return "ffmpeg"


_CURVES = ("ease", "linear", "ease_in", "ease_out", "ease_in_out",
           "sine", "cubic")


def _ease(t, curve):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    if curve == "linear":
        return t
    if curve == "ease":
        return t * t * (3.0 - 2.0 * t)
    if curve == "ease_in":
        return t * t
    if curve == "ease_out":
        u = 1.0 - t
        return 1.0 - u * u
    if curve == "ease_in_out":
        if t < 0.5:
            return 2.0 * t * t
        u = -2.0 * t + 2.0
        return 1.0 - (u * u) / 2.0
    if curve == "sine":
        import math
        return 0.5 * (1.0 - math.cos(math.pi * t))
    if curve == "cubic":
        if t < 0.5:
            return 4.0 * t * t * t
        u = -2.0 * t + 2.0
        return 1.0 - (u * u * u) / 2.0
    return t


def _lerp(a, b, t):
    return a + (b - a) * t


def _pick_crf(q):
    if isinstance(q, bool):
        return 18
    if isinstance(q, (int, float)):
        try:
            v = int(q)
            if 0 <= v <= 51:
                return v
        except (TypeError, ValueError):
            pass
    elif isinstance(q, str):
        ql = q.strip().lower()
        if ql in ("высокое", "high", "best"):
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
    return 18


def _even(n):
    n = int(n)
    if n % 2:
        n -= 1
    return max(n, 2)


@register(["панорамирование", "панорама", "кен_бернс",
           "зум_панорама", "эффект_кена_бернса"])
def image_pan(args: dict) -> str:
    """Панорамирование изображения (Ken Burns): плавный зум и сдвиг.

    Параметры:
      картинка     — вход (обязательно); алиасы: изображение, image, файл
      сохранить    — куда (иначе MY_DIR/image_anim/<TS>_pan.mp4);
                     алиасы: куда, выход, путь
      длительность — секунды (по умолч. 5.0)
      fps          — кадров/сек (по умолч. 30)
      ширина       — ширина кадра (по умолч. по картинке, ≤ 1920)
      высота       — высота кадра (по умолч. по картинке, ≤ 1080)
      зум_начало   — зум в начале, ≥ 1.0 (по умолч. 1.0)
      зум_конец    — зум в конце, ≥ 1.0 (по умолч. 1.15)
      x_начало     — центр по X в начале, 0..1 (по умолч. 0.5)
      y_начало     — центр по Y в начале, 0..1 (по умолч. 0.5)
      x_конец      — центр по X в конце, 0..1 (по умолч. 0.5)
      y_конец      — центр по Y в конце, 0..1 (по умолч. 0.5)
      кривая       — ease / linear / ease_in / ease_out /
                     ease_in_out / sine / cubic (по умолч. ease)
      качество     — высокое/среднее/низкое или CRF 0..51
                     (по умолч. 18)
      перезаписать — false: не трогать существующий файл
    """
    img = (args.get("картинка") or args.get("изображение")
           or args.get("image") or args.get("файл")
           or args.get("вход"))
    if not img:
        return "Ошибка: не указана картинка"
    ip = Path(img)
    if not ip.is_file():
        return "Ошибка: нет картинки " + str(ip)

    try:
        from PIL import Image
    except ImportError:
        return "Ошибка: PIL не установлен"

    try:
        src = Image.open(ip)
        src.load()
    except Exception as e:
        return "Ошибка: не открыть картинку: " + str(e)

    if src.mode == "RGBA":
        bg = Image.new("RGB", src.size, (0, 0, 0))
        bg.paste(src, mask=src.split()[-1])
        src = bg
    elif src.mode not in ("RGB", "L"):
        src = src.convert("RGB")
    elif src.mode == "L":
        src = src.convert("RGB")

    W, H = src.size

    try:
        dur = float(args.get("длительность") or args.get("duration") or 5.0)
    except (TypeError, ValueError):
        return "Ошибка: длительность должна быть числом (сек)"
    if dur <= 0:
        return "Ошибка: длительность должна быть > 0"

    try:
        fps = int(float(args.get("fps") or 30))
    except (TypeError, ValueError):
        return "Ошибка: fps должен быть числом"
    if fps < 1 or fps > 120:
        return "Ошибка: fps должен быть 1..120"

    w_arg = args.get("ширина") or args.get("width")
    h_arg = args.get("высота") or args.get("height")
    if w_arg or h_arg:
        try:
            ow = int(w_arg) if w_arg else 0
            oh = int(h_arg) if h_arg else 0
        except (TypeError, ValueError):
            return "Ошибка: ширина/высота должны быть числами"
        if ow <= 0 and oh <= 0:
            return "Ошибка: ширина/высота должны быть > 0"
        if ow > 0 and oh == 0:
            oh = int(round(ow * H / W))
        elif oh > 0 and ow == 0:
            ow = int(round(oh * W / H))
    else:
        scale = min(1920.0 / W, 1080.0 / H, 1.0)
        ow = int(round(W * scale))
        oh = int(round(H * scale))
    ow = _even(ow)
    oh = _even(oh)

    try:
        z0 = float(args.get("зум_начало", 1.0))
        z1 = float(args.get("зум_конец", 1.15))
    except (TypeError, ValueError):
        return "Ошибка: зум_начало/зум_конец должны быть числами"
    if z0 < 1.0:
        z0 = 1.0
    if z1 < 1.0:
        z1 = 1.0

    def _f(v, default=0.5):
        try:
            x = float(v) if v is not None else default
        except (TypeError, ValueError):
            return None
        return min(max(x, 0.0), 1.0)

    x0 = _f(args.get("x_начало"), 0.5)
    y0 = _f(args.get("y_начало"), 0.5)
    x1 = _f(args.get("x_конец"), 0.5)
    y1 = _f(args.get("y_конец"), 0.5)
    if None in (x0, y0, x1, y1):
        return "Ошибка: x_/y_начало/конец должны быть числами 0..1"

    curve = str(args.get("кривая") or "ease").strip().lower()
    if curve not in _CURVES:
        return ("Ошибка: кривая должна быть одна из: "
                + ", ".join(_CURVES))

    crf = _pick_crf(args.get("качество", 18))
    overwrite = bool(args.get("перезаписать", False))

    out_arg = (args.get("сохранить") or args.get("куда")
               or args.get("выход") or args.get("путь"))
    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "image_anim" / (ts + "_pan.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    src_aspect = W / float(H)
    out_aspect = ow / float(oh)
    if src_aspect > out_aspect:
        base_h = float(H)
        base_w = base_h * out_aspect
    else:
        base_w = float(W)
        base_h = base_w / out_aspect

    total_frames = int(round(dur * fps))
    if total_frames < 1:
        total_frames = 1

    ffmpeg = _ffmpeg()
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", str(ow) + "x" + str(oh), "-r", str(fps),
           "-i", "-",
           "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
           "-pix_fmt", "yuv420p",
           str(out_path)]

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    except FileNotFoundError:
        return "Ошибка: ffmpeg не найден в PATH"

    last_err = b""
    try:
        for i in range(total_frames):
            t_raw = i / float(total_frames - 1) if total_frames > 1 else 0.0
            t = _ease(t_raw, curve)
            z = _lerp(z0, z1, t)
            cx = _lerp(x0, x1, t)
            cy = _lerp(y0, y1, t)

            cw = base_w / z
            ch = base_h / z
            if cw > W:
                cw = float(W)
            if ch > H:
                ch = float(H)

            px = cx * W
            py = cy * H
            left = px - cw / 2.0
            top = py - ch / 2.0
            if left < 0:
                left = 0.0
            if top < 0:
                top = 0.0
            if left + cw > W:
                left = W - cw
            if top + ch > H:
                top = H - ch

            box = (int(round(left)), int(round(top)),
                   int(round(left + cw)), int(round(top + ch)))
            if box[2] <= box[0]:
                box = (box[0], box[1], box[0] + 1, box[3])
            if box[3] <= box[1]:
                box = (box[0], box[1], box[2], box[1] + 1)

            frame = src.crop(box).resize((ow, oh), Image.LANCZOS)
            if frame.mode != "RGB":
                frame = frame.convert("RGB")
            try:
                proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                break
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            last_err = proc.stderr.read() if proc.stderr else b""
        except Exception:
            pass
        rc = proc.wait()

    if rc != 0:
        try:
            if out_path.is_file():
                out_path.unlink()
        except Exception:
            pass
        err = (last_err or b"").decode("utf-8", errors="replace")[-600:]
        return "Ошибка ffmpeg (код " + str(rc) + "):\n" + err

    if not out_path.is_file():
        return "Ошибка: файл не создан: " + str(out_path)

    size = out_path.stat().st_size
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, "
            + str(total_frames) + " кадров, "
            + str(ow) + "x" + str(oh) + ", zoom "
            + ("%.2f" % z0) + "→" + ("%.2f" % z1)
            + ", " + curve + ", CRF " + str(crf) + ")")