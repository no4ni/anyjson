"""Расширение картинки по бокам: зеркалка с градиентной альфой + размытый фон."""
from pathlib import Path

from PIL import Image, ImageFilter

from _actions import register


def _parse_color(v):
    """Строка '#RRGGBB', 'white', 'red', список [r,g,b] или [r,g,b,a]."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        if len(v) == 3:
            return tuple(int(x) for x in v) + (255,)
        if len(v) == 4:
            return tuple(int(x) for x in v)
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 6:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (255,)
        if len(h) == 8:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4, 6))
        return None
    _named = {
        "white": (255, 255, 255, 255),
        "black": (0, 0, 0, 255),
        "red":   (255, 0, 0, 255),
        "green": (0, 128, 0, 255),
        "blue":  (0, 0, 255, 255),
        "gray":  (128, 128, 128, 255),
        "grey":  (128, 128, 128, 255),
    }
    return _named.get(s.lower())


def _alpha_1d(pad, gw, side, curve="linear"):
    """numpy-массив длины pad, uint8, для маски альфы."""
    import numpy as np
    xs = np.arange(pad)
    if gw <= 0:
        return np.full(pad, 255, dtype=np.uint8)
    if gw >= pad:
        if side == "left":
            x_norm = xs / max(1, pad - 1)
        else:
            x_norm = (pad - 1 - xs) / max(1, pad - 1)
    else:
        if side == "left":
            x_norm = np.where(xs < gw, xs / max(1, gw - 1), 1.0)
        else:
            right_start = pad - gw
            x_norm = np.where(xs >= right_start, (pad - 1 - xs) / max(1, gw - 1), 1.0)
    
    if curve == "smooth":
        a = 255 * (x_norm ** 2)
    elif curve == "ease":
        a = 255 * (x_norm * x_norm * (3 - 2 * x_norm))
    else:
        a = 255 * x_norm
        
    return np.clip(np.round(a), 0, 255).astype(np.uint8)


def _make_gradient_mask(pad, height, gw, side, curve="linear"):
    """2D L-маска (height, pad) — по горизонтали градиент."""
    import numpy as np
    arr1d = _alpha_1d(pad, gw, side, curve)
    arr2d = np.tile(arr1d[None, :], (height, 1))
    return Image.fromarray(arr2d, mode="L")


@register(["расширить_картинку", "расширить_изображение", "расширить_поля"])
def extend_image(args: dict) -> str:
    """Расширение картинки по бокам: зеркалка с градиентом + размытый фон.

    Зеркалка берётся фиксированной доли от исходной ширины (по умолч.
    10%) и прижимается к границе с оригиналом. Если отступ больше
    зеркалки (при большой «ширина») — остаток заливается размытым
    фоном.

    Параметры:
      картинка    — вход (обязательно), алиас: изображение, image, путь
      ширина      — итоговая ширина в px. Отступ = (ширина − W) / 2
                    на каждую сторону.
      процент     — сколько добавить с каждой стороны, % от ширины (10),
                    если «ширина» не задана
      зеркалка    — ширина зеркалки, % от W (10). Если отступ больше
                    зеркалки, остаток — размытый фон. 0 — без зеркалки.
      сохранить   — куда (алиас: куда, выход). Иначе рядом:
                    <имя>_ext<ширина|процент>.<ext>
      перезаписать — false: не трогать существующий файл
      размытие    — радиус размытия фона, px. По умолч. auto = 2% от
                    ширины, мин. 12, макс. 120. 0 — фон без размытия.
      градиент    — доля зеркалки, на которой у края альфа-градиент
                    0→255, % (0..100, по умолч. 100).
      кривая      — форма градиента:
                    smooth (по умолч.) — 100→25→0 (квадратичная,
                    мягче у края, лучше прячет шов)
                    linear — 100→50→0 (прямой пандус)
                    ease — S-образная, максимально плавная у обоих краёв
      цвет_фона   — если задан, фон заливается этим цветом вместо
                    растянутого размытого оригинала. Форматы: "#RRGGBB",
                    "white", [r,g,b] или [r,g,b,a].

    Примеры:
      {"расширить_картинку": {"картинка": "in.jpg", "ширина": 1920}}
        → smooth по умолчанию, отступ по (1920−W)/2
      {"расширить_картинку": {"картинка": "in.jpg", "ширина": 1920, "кривая": "linear"}}
        → прямой пандус 100→50→0
      {"расширить_картинку": {"картинка": "in.jpg", "процент": 10, "зеркалка": 15}}
    """
    src = (args.get("картинка") or args.get("изображение")
           or args.get("image") or args.get("путь") or "")
    src = str(src).strip()
    if not src:
        return "Ошибка: не указана картинка"
    src_path = Path(src)
    if not src_path.is_file():
        return "Ошибка: нет картинки " + str(src_path)

    try:
        img = Image.open(str(src_path))
        img.load()
    except Exception as e:
        return "Ошибка открытия: " + str(e)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    W, H = img.size

    _width_arg = args.get("ширина")
    _width_mode = False
    _suffix = ""
    pct = 0.0
    if _width_arg is not None and str(_width_arg).strip() != "":
        try:
            _target_w = int(float(_width_arg))
        except (TypeError, ValueError):
            return "Ошибка: ширина должна быть целым числом"
        if _target_w <= W:
            return ("Ошибка: целевая ширина (" + str(_target_w)
                    + "px) должна быть больше исходной (" + str(W) + "px)")
        _total_pad = _target_w - W
        pad_left = _total_pad // 2
        pad_right = _total_pad - pad_left
        _width_mode = True
        _suffix = "w" + str(_target_w)
    else:
        try:
            pct = float(args.get("процент") or 10)
        except (TypeError, ValueError):
            return "Ошибка: процент должен быть числом"
        if pct <= 0:
            return "Ошибка: процент должен быть > 0"
        _pad = max(1, int(round(W * pct / 100.0)))
        if _pad >= W:
            _pad = W - 1
        if _pad < 1:
            return "Ошибка: не удалось вычислить отступ (слишком мала картинка?)"
        pad_left = _pad
        pad_right = _pad
        _suffix = "p" + str(int(round(pct)))

    if pad_left > W or pad_right > W:
        return ("Ошибка: слишком большой отступ ("
                + str(pad_left) + "/" + str(pad_right)
                + "px при ширине " + str(W)
                + "px). Максимум по " + str(W) + "px на сторону.")

    W_new = W + pad_left + pad_right

    try:
        mirror_pct = float(args.get("зеркалка")
                           if args.get("зеркалка") is not None else 10)
    except (TypeError, ValueError):
        mirror_pct = 10.0
    mirror_pct = max(0.0, min(100.0, mirror_pct))
    _mirror_w = int(round(W * mirror_pct / 100.0))
    mirror_left = min(_mirror_w, pad_left)
    mirror_right = min(_mirror_w, pad_right)

    out_arg = args.get("сохранить") or args.get("куда") or args.get("выход")
    if out_arg:
        out_path = Path(str(out_arg).strip())
    else:
        out_path = src_path.with_name(
            src_path.stem + "_ext" + _suffix + src_path.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overwrite = bool(args.get("перезаписать", False))
    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    blur_arg = args.get("размытие")
    if blur_arg is None or blur_arg == "" or str(blur_arg).lower() == "auto":
        blur_r = max(12, min(120, int(W * 0.02)))
    else:
        try:
            blur_r = int(float(blur_arg))
        except (TypeError, ValueError):
            blur_r = 0
        if blur_r < 0:
            blur_r = 0

    try:
        grad_pct = float(args.get("градиент", 100))
    except (TypeError, ValueError):
        grad_pct = 100.0
    grad_pct = max(0.0, min(100.0, grad_pct))
    
    curve = str(args.get("кривая") or "smooth").strip().lower()

    bg_color = _parse_color(args.get("цвет_фона"))

    if bg_color is not None:
        bg = Image.new("RGBA" if has_alpha else "RGB",
                       (W_new, H),
                       bg_color if has_alpha else bg_color[:3])
    else:
        bg = img.resize((W_new, H), Image.LANCZOS)
        if blur_r > 0:
            bg = bg.filter(ImageFilter.GaussianBlur(blur_r))

    canvas = bg.convert("RGBA")

    if mirror_left > 0 and pad_left > 0:
        _off_l = pad_left - mirror_left
        left_mirror = img.crop((0, 0, mirror_left, H)).transpose(
            Image.FLIP_LEFT_RIGHT)
        _gw_l = int(round(mirror_left * grad_pct / 100.0))
        if _gw_l > mirror_left:
            _gw_l = mirror_left
        left_mask = _make_gradient_mask(mirror_left, H, _gw_l, "left", curve)
        left_rgba = left_mirror.convert("RGBA")
        left_rgba.putalpha(Image.composite(
            left_rgba.getchannel("A"),
            Image.new("L", (mirror_left, H), 0),
            left_mask))
        canvas.alpha_composite(left_rgba, (_off_l, 0))

    if mirror_right > 0 and pad_right > 0:
        _off_r = W_new - pad_right
        right_mirror = img.crop((W - mirror_right, 0, W, H)).transpose(
            Image.FLIP_LEFT_RIGHT)
        _gw_r = int(round(mirror_right * grad_pct / 100.0))
        if _gw_r > mirror_right:
            _gw_r = mirror_right
        right_mask = _make_gradient_mask(mirror_right, H, _gw_r, "right", curve)
        right_rgba = right_mirror.convert("RGBA")
        right_rgba.putalpha(Image.composite(
            right_rgba.getchannel("A"),
            Image.new("L", (mirror_right, H), 0),
            right_mask))
        canvas.alpha_composite(right_rgba, (_off_r, 0))

    canvas.alpha_composite(img.convert("RGBA"), (pad_left, 0))

    out = canvas if has_alpha else canvas.convert("RGB")

    save_kwargs = {}
    if out_path.suffix.lower() in (".jpg", ".jpeg"):
        if out.mode == "RGBA":
            _bg = Image.new("RGB", out.size, (255, 255, 255))
            _bg.paste(out, mask=out.split()[-1])
            out = _bg
        save_kwargs["quality"] = 95
    try:
        out.save(str(out_path), **save_kwargs)
    except Exception as e:
        return "Ошибка сохранения: " + str(e)

    _buf_ok = False
    try:
        from _actions.clipboard import _pil_to_clipboard
        _pil_to_clipboard(out.convert("RGB") if out.mode != "RGB" else out)
        _buf_ok = True
    except Exception:
        _buf_ok = False

    _parts = [str(W_new) + "×" + str(H)]
    if _width_mode:
        _parts.append("ширина " + str(W) + "→" + str(W_new)
                      + " (+" + str(pad_left) + "/+" + str(pad_right) + ")")
    else:
        _parts.append("по бокам +" + str(int(round(pct))) + "% ("
                      + str(pad_left) + "/" + str(pad_right) + "px)")
    if mirror_left == 0 and mirror_right == 0:
        _parts.append("без зеркалки")
    else:
        _parts.append("зеркалка " + str(int(round(mirror_pct))) + "% ("
                      + str(mirror_left) + "/" + str(mirror_right) + "px)")
    _parts.append("градиент " + str(int(grad_pct)) + "%")
    if curve != "linear":
        _parts.append("кривая " + curve)
    if bg_color is not None:
        _parts.append("фон цветом")
    elif blur_r > 0:
        _parts.append("фон размыт r=" + str(blur_r))
    else:
        _parts.append("фон без размытия")
    _summary = ", ".join(_parts)

    if _buf_ok:
        return ("[BUF] 📋 " + out_path.name + " ("
                + str(W_new) + "×" + str(H) + ")"
                + "\n   📁 " + str(out_path)
                + "\n   " + _summary)
    return ("📁 " + str(out_path) + " (" + str(W_new) + "×" + str(H) + ")"
            + "\n   " + _summary)
