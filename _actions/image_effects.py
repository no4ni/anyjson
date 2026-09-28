"""Точечные эффекты: цветокоррекция, тень — по одной картинке."""
from pathlib import Path

from _actions import register
from _actions import image_layers as _il


def _load(path):
    from PIL import Image
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("Не найден файл: " + str(p))
    return p, Image.open(str(p)).convert("RGBA")


def _save_and_return(img, src_path, args, tag):
    """Сохранить (опц.) и/или положить в контекст. Вернуть строку."""
    out_path = None
    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        img.save(str(out_path))
    else:
        try:
            from _config import MY_DIR
            out_dir = MY_DIR / "effects_out"
            out_dir.mkdir(parents=True, exist_ok=True)
            import time as _t
            out_path = out_dir / (src_path.stem + "_" + tag + "_"
                                  + _t.strftime("%H%M%S") + ".png")
            img.save(str(out_path))
        except Exception:
            out_path = None

    to_ctx = args.get("в_контекст")
    if to_ctx is None:
        to_ctx = args.get("в_буфер", True)
    if isinstance(to_ctx, str):
        to_ctx = to_ctx.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_ctx:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(img.convert("RGB"))
            prefix = "[BUF] "
        except Exception:
            pass
    info = tag + ": " + str(img.size[0]) + "x" + str(img.size[1])
    if out_path:
        info += " -> " + str(out_path)
    return prefix + info


def _f(args, *keys, default=1.0):
    for k in keys:
        if k in args and args[k] is not None:
            try:
                return float(args[k])
            except (TypeError, ValueError):
                return default
    return default


_NAMED_COLORS = {
    "красный": (255, 0, 0),
    "зелёный": (0, 200, 0),
    "зеленый": (0, 200, 0),
    "синий": (30, 60, 220),
    "голубой": (80, 180, 230),
    "жёлтый": (255, 220, 0),
    "желтый": (255, 220, 0),
    "оранжевый": (255, 140, 0),
    "фиолетовый": (150, 60, 200),
    "розовый": (255, 130, 180),
    "белый": (255, 255, 255),
    "чёрный": (0, 0, 0),
    "черный": (0, 0, 0),
    "серый": (128, 128, 128),
    "коричневый": (140, 90, 50),
    "бирюзовый": (60, 200, 200),
}


def _parse_color(v):
    """#RRGGBB / [r,g,b] / имя -> (r, g, b) или None."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            return (max(0, min(255, int(v[0]))),
                    max(0, min(255, int(v[1]))),
                    max(0, min(255, int(v[2]))))
        except (TypeError, ValueError):
            return None
    if isinstance(v, str):
        t = v.strip().lower()
        if t in _NAMED_COLORS:
            return _NAMED_COLORS[t]
        if t.startswith("#"):
            h = t[1:]
            if len(h) == 3:
                h = h[0] * 2 + h[1] * 2 + h[2] * 2
            if len(h) == 6:
                try:
                    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                except ValueError:
                    return None
        try:
            return (int(t), int(t), int(t))
        except ValueError:
            return None
    return None


@register(["цветокоррекция", "цветокор", "цвет", "тонирование"])
def color_correct(args):
    """Цветокоррекция одной картинки.

    Параметры:
      путь          — входной файл (обязательный)
      яркость       — множитель, 1.0 = как есть (алиас: brightness)
      контраст      — множитель, 1.0 = как есть (алиас: contrast)
      насыщенность  — множитель, 1.0 = как есть (алиас: saturation)
      резкость      — множитель, 1.0 = как есть (алиас: sharpness)
      тон           — сдвиг в градусах -180..180, 0 = как есть
                      (алиасы: оттенок, hue)
      плюс_цвет  — примешать цвет: #RRGGBB / [r,g,b] / имя
                      (имена: красный, жёлтый, синий, ...)
      доля         — 0..1, доля цвета в смеси (по умолч. 0.2)
      сохранить     — путь для PNG (иначе MY_DIR/effects_out/)
      в_контекст    — положить в контекст (по умолч. true)

    Примеры:
      {"цветокоррекция": {"путь": "C:/rpg/in.png", "яркость": 1.1,
                          "контраст": 1.15, "тон": -8}}
      {"цветокоррекция": {"путь": "C:/rpg/in.png", "насыщенность": 0.7,
                          "сохранить": "C:/rpg/cold.png"}}
    """
    path_arg = args.get("путь") or args.get("файл")
    if not path_arg:
        return 'Укажи путь: {"цветокоррекция": {"путь": "C:/pic.png"}}'
    try:
        src_p, img = _load(path_arg)
    except Exception as e:
        return "❌ " + str(e)

    has_alpha = img.mode == "RGBA"
    alpha = img.split()[-1] if has_alpha else None

    corrected = _il._apply_color(
        img.convert("RGB"),
        brightness=_f(args, "яркость", "brightness"),
        contrast=_f(args, "контраст", "contrast"),
        saturation=_f(args, "насыщенность", "saturation"),
        sharpness=_f(args, "резкость", "sharpness"),
        hue=_f(args, "тон", "оттенок", "hue", default=0.0),
    )

    # Примесь сплошного цвета (линейное alpha-смешение).
    plus_color = _parse_color(args.get("плюс_цвет") or args.get("plus_color"))
    if plus_color is not None:
        try:
            share = float(args.get("доля", args.get("share", 0.2)) or 0.0)
        except (TypeError, ValueError):
            share = 0.0
        if share < 0.0:
            share = 0.0
        if share > 1.0:
            share = 1.0
        if share > 0.0:
            from PIL import Image
            solid = Image.new("RGB", corrected.size, plus_color)
            corrected = Image.blend(corrected.convert("RGB"), solid, share)

    from PIL import Image
    if has_alpha and alpha is not None:
        corrected = corrected.convert("RGBA")
        corrected.putalpha(alpha)

    return _save_and_return(corrected, src_p, args, "color")


@register(["тень", "drop_shadow", "добавить_тень"])
def add_shadow(args):
    """Добавить тень к одной картинке (drop shadow с наклоном).

    Параметры:
      путь       — входной файл (обязательный)
      смещение   — [dx, dy] или число (по умолч. [4, 4]), алиас: offset
      размытие   — пикселей (по умолч. 8), алиас: blur
      альфа      — прозрачность 0..1 (по умолч. 0.5), алиас: alpha
      цвет       — [r, g, b], по умолч. чёрный, алиас: color
      угол       — наклон в градусах (по умолч. 0), алиас: angle
      магнитуда  — величина наклона в пикселях (по умолч. 0), алиас: magnitude
      сохранить  — путь для PNG (иначе MY_DIR/effects_out/)
      в_контекст — положить в контекст (по умолч. true)

    Примеры:
      {"тень": {"путь": "C:/rpg/char.png"}}
      {"тень": {"путь": "C:/rpg/char.png", "смещение": [8, 12],
                "размытие": 12, "альфа": 0.6,
                "угол": 45, "магнитуда": 30}}
    """
    path_arg = args.get("путь") or args.get("файл")
    if not path_arg:
        return 'Укажи путь: {"тень": {"путь": "C:/pic.png"}}'
    try:
        src_p, img = _load(path_arg)
    except Exception as e:
        return "❌ " + str(e)

    off = args.get("смещение") or args.get("offset") or (4, 4)
    if isinstance(off, (int, float)):
        off = (int(off), int(off))
    if not (isinstance(off, (list, tuple)) and len(off) == 2):
        off = (4, 4)

    blur = _f(args, "размытие", "blur", default=8.0)
    alpha = _f(args, "альфа", "alpha", default=0.5)
    angle = _f(args, "угол", "angle", default=0.0)
    mag = _f(args, "магнитуда", "magnitude", default=0.0)
    col = args.get("цвет") or args.get("color") or (0, 0, 0)
    if not (isinstance(col, (list, tuple)) and len(col) == 3):
        col = (0, 0, 0)

    try:
        result, _shift = _il._apply_shadow(
            img, offset=(int(off[0]), int(off[1])),
            blur=int(blur), alpha=float(alpha),
            color=tuple(int(c) for c in col),
            angle=float(angle), magnitude=float(mag),
        )
    except Exception as e:
        return "❌ Ошибка тени: " + str(e)

    return _save_and_return(result, src_p, args, "shadow")
