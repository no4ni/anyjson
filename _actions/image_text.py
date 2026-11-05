"""Наложить текст на изображение: позиция, шрифт, обводка, поворот, перспектива."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from _actions import register


_FONTS_DIRS = [
    Path(r"C:\Windows\Fonts"),
    Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
]

_FONT_ALIASES = {
    "arial": "arial.ttf",
    "arial_bold": "arialbd.ttf",
    "arial_black": "ariblk.ttf",
    "impact": "impact.ttf",
    "segoe": "segoeui.ttf",
    "segoe_bold": "segoeuib.ttf",
    "calibri": "calibri.ttf",
    "calibri_bold": "calibrib.ttf",
    "times": "times.ttf",
    "times_bold": "timesbd.ttf",
    "verdana": "verdana.ttf",
    "tahoma": "tahoma.ttf",
    "consolas": "consola.ttf",
    "courier": "cour.ttf",
    "comic": "comic.ttf",
    "georgia": "georgia.ttf",
    "trebuchet": "trebuc.ttf",
    "impact_cyr": "impact.ttf",
    "pala": "pala.ttf",
}


def _find_font(name_or_path, size):
    if name_or_path:
        p = Path(str(name_or_path))
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
        key = str(name_or_path).strip().lower()
        fname = _FONT_ALIASES.get(key)
        if not fname and key.endswith(".ttf"):
            fname = key
        if fname:
            for d in _FONTS_DIRS:
                fp = d / fname
                if fp.is_file():
                    try:
                        return ImageFont.truetype(str(fp), size)
                    except Exception:
                        pass
    for fname in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
        for d in _FONTS_DIRS:
            fp = d / fname
            if fp.is_file():
                try:
                    return ImageFont.truetype(str(fp), size)
                except Exception:
                    pass
    return ImageFont.load_default()


def _parse_color(v, default=(255, 255, 255)):
    if v is None:
        return default
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            return (max(0, min(255, int(v[0]))),
                    max(0, min(255, int(v[1]))),
                    max(0, min(255, int(v[2]))))
        except (TypeError, ValueError):
            return default
    if isinstance(v, str):
        t = v.strip().lower()
        named = {
            "белый": (255, 255, 255), "чёрный": (0, 0, 0), "черный": (0, 0, 0),
            "красный": (255, 0, 0), "зелёный": (0, 200, 0), "зеленый": (0, 200, 0),
            "синий": (30, 60, 220), "жёлтый": (255, 220, 0), "желтый": (255, 220, 0),
            "оранжевый": (255, 140, 0), "розовый": (255, 130, 180),
            "фиолетовый": (150, 60, 200), "серый": (128, 128, 128),
            "голубой": (80, 180, 230), "бирюзовый": (60, 200, 200),
        }
        if t in named:
            return named[t]
        if t.startswith("#"):
            h = t[1:]
            if len(h) == 3:
                h = h[0] * 2 + h[1] * 2 + h[2] * 2
            if len(h) == 6:
                try:
                    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                except ValueError:
                    pass
    return default


_POS_ALIASES = {
    "центр": ("center", "center"),
    "по_центру": ("center", "center"),
    "центр_верх": ("center", "top"),
    "по_центру_сверху": ("center", "top"),
    "сверху_центра": ("center", "top"),
    "центр_низ": ("center", "bottom"),
    "по_центру_снизу": ("center", "bottom"),
    "снизу_центра": ("center", "bottom"),
    "слева_верх": ("left", "top"),
    "левый_верх": ("left", "top"),
    "слева_низ": ("left", "bottom"),
    "левый_низ": ("left", "bottom"),
    "справа_верх": ("right", "top"),
    "правый_верх": ("right", "top"),
    "справа_низ": ("right", "bottom"),
    "правый_низ": ("right", "bottom"),
}


def _read_xy(args):
    """Позиция: [x, y] / "строка" / отдельные x, y. Возвращает [x,y], строку или None."""
    pos = args.get("позиция")
    if pos is not None and not (isinstance(pos, str) and pos.strip() == ""):
        return pos
    x = args.get("x")
    y = args.get("y")
    if x is not None and y is not None:
        try:
            return [int(x), int(y)]
        except (TypeError, ValueError):
            return None
    return None


def _resolve_pos(position, canvas_size, text_size, margin_frac=0.03):
    W, H = canvas_size
    tw, th = text_size
    if isinstance(position, (list, tuple)) and len(position) >= 2:
        try:
            return int(position[0]), int(position[1])
        except (TypeError, ValueError):
            pass
    key = str(position or "центр").strip().lower()
    xa, ya = _POS_ALIASES.get(key, ("center", "center"))
    mx = max(4, int(W * margin_frac))
    my = max(4, int(H * margin_frac))
    if xa == "left":
        x = mx
    elif xa == "right":
        x = W - tw - mx
    else:
        x = (W - tw) // 2
    if ya == "top":
        y = my
    elif ya == "bottom":
        y = H - th - my
    else:
        y = (H - th) // 2
    return x, y


def _find_coeffs(src_pts, dst_pts):
    """Коэффициенты для PIL.Image.transform(PERSPECTIVE)."""
    m = []
    for (x, y), (u, v) in zip(src_pts, dst_pts):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(m, dtype=np.float64)
    B = np.array(dst_pts, dtype=np.float64).reshape(8)
    res = np.linalg.solve(A, B)
    return res.tolist()


@register(["написать_текст", "надпись", "текст_на_картинке"])
def add_text(args: dict) -> str:
    """Наложить текст на изображение.

    Параметры:
      путь       — входной файл (обязательно)
      текст      — что написать (обязательно, поддерживает \\n)
      позиция    — [x, y] или строка: центр (по умолч.), центр_верх,
                   центр_низ, слева_верх, слева_низ, справа_верх, справа_низ.
                   Альтернатива: отдельные x и y (числа)
      шрифт      — имя (arial, impact, segoe, consolas, verdana, ...)
                   или путь к .ttf
      размер     — размер шрифта (по умолч. h/12)
      цвет       — цвет текста (#RRGGBB / [r,g,b] / имя)
      обводка    — цвет обводки
      толщина_обводки — 0 по умолч.
      тень       — тень под текстом. Формат "dx,dy,размытие,цвет"
                   (например "2,2,4,#000000AA"). Размытие в px,
                   цвет — #RRGGBB или #RRGGBBAA. Несколько теней —
                   через ';': "2,2,4,#00000080;0,0,12,#FF8800AA".
                   true = "2,2,4" (чёрная, альфа 170). Не задана —
                   без тени.
      прозрачность — 0..1 (по умолч. 1.0)
      поворот    — градусы против часовой (0 по умолч.)
      наклон_x   — тангенс угла (shear по X, 0 по умолч.)
      наклон_y   — тангенс угла (shear по Y, 0 по умолч.)
      перспектива — 4 точки [[x,y],...] углов текстового блока
                    (порядок: LT, RT, RB, LB) в локальных координатах.
                    Позиция смещает блок на холсте.
                    Поворот/наклон игнорируются
      сохранить  — путь к новому файлу. Если задан — только сохраняем,
                   буфер не трогаем. Если не задан — перезаписываем
                   исходник и кладём результат в буфер.

    Примеры:
      {"написать_текст": {"путь": "C:/in.png", "текст": "Привет"}}
      {"написать_текст": {"путь": "C:/in.png", "текст": "Top",
                          "позиция": "центр_верх", "размер": 64,
                          "цвет": "#FFD700", "обводка": "#000000",
                          "толщина_обводки": 3}}
      {"написать_текст": {"путь": "C:/in.png", "текст": "Заголовок",
                          "размер": 96, "тень": "4,4,8,#000000CC"}}
      {"написать_текст": {"путь": "C:/in.png", "текст": "Плакат",
                          "сохранить": "C:/out.png",
                          "перспектива": [[100,100],[600,80],[650,400],[50,450]]}}
    """
    args = args or {}
    src = args.get("путь") or args.get("файл")
    if not src:
        return 'Укажи путь: {"написать_текст": {"путь": "C:/pic.png", "текст": "Привет"}}'
    text = args.get("текст")
    if text is None or str(text) == "":
        return 'Укажи текст: {"написать_текст": {"путь": "...", "текст": "Привет"}}'
    text = str(text)

    _is_jpeg = str(src).lower().endswith((".jpg", ".jpeg"))

    try:
        from _actions.image_effects import _load
        src_p, img = _load(src)
    except Exception as e:
        return "❌ " + str(e)

    W, H = img.size
    is_alpha = img.mode == "RGBA"
    base = img.convert("RGBA")

    try:
        font_size = int(args.get("размер") or max(16, H // 12))
    except (TypeError, ValueError):
        font_size = max(16, H // 12)
    font_size = max(6, min(font_size, 2000))

    font = _find_font(args.get("шрифт"), font_size)

    fg = _parse_color(args.get("цвет"), (255, 255, 255))
    stroke_color = None
    if args.get("обводка") is not None:
        stroke_color = _parse_color(args.get("обводка"), (0, 0, 0))
    try:
        stroke_w = int(args.get("толщина_обводки") or 0)
    except (TypeError, ValueError):
        stroke_w = 0
    stroke_w = max(0, min(stroke_w, 200))

    try:
        alpha_v = float(args.get("прозрачность") if args.get("прозрачность") is not None else 1.0)
    except (TypeError, ValueError):
        alpha_v = 1.0
    alpha_v = max(0.0, min(1.0, alpha_v))
    fg_a = (fg[0], fg[1], fg[2], int(255 * alpha_v))

    # --- разбор тени (до pad — _shadow_pad увеличивает холст) ---
    _shadow_specs = []
    _shadow_raw = args.get("тень")
    if _shadow_raw is not None:
        _sraw = str(_shadow_raw).strip()
        if _sraw.lower() in ("true", "1", "yes", "on", "да", "y", "t"):
            _shadow_specs.append((2.0, 2.0, 4.0, (0, 0, 0, 170)))
        elif _sraw.lower() not in ("false", "0", "no", "off", "нет", "n", "f", ""):
            for _spec in _sraw.split(";"):
                _spec = _spec.strip()
                if not _spec:
                    continue
                _parts = [p.strip() for p in _spec.split(",")]
                try:
                    _sdx = float(_parts[0]) if len(_parts) > 0 and _parts[0] else 2.0
                    _sdy = float(_parts[1]) if len(_parts) > 1 and _parts[1] else 2.0
                    _sbl = float(_parts[2]) if len(_parts) > 2 and _parts[2] else 4.0
                except (TypeError, ValueError):
                    return ("❌ Ошибка: тень в формате 'dx,dy,размытие,цвет' "
                            "через ';', например '2,2,4,#000000AA'")
                _sclr = (0, 0, 0, 170)
                if len(_parts) > 3 and _parts[3]:
                    _pc = _parse_color(_parts[3], (0, 0, 0, 170))
                    if not isinstance(_pc, (tuple, list)) or len(_pc) < 3:
                        _pc = (0, 0, 0, 170)
                    if len(_pc) == 3:
                        _pc = (_pc[0], _pc[1], _pc[2], 255)
                    _sclr = (int(_pc[0]), int(_pc[1]),
                             int(_pc[2]), int(_pc[3]))
                _shadow_specs.append((_sdx, _sdy, _sbl, _sclr))

    _shadow_pad = 0
    for _sdx, _sdy, _sbl, _ in _shadow_specs:
        _shadow_pad = max(
            _shadow_pad,
            int(abs(_sdx)) + int(abs(_sdy)) + int(3 * _sbl) + 4)

    # bbox текста для точного размера слоя
    _tmp = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    _d = ImageDraw.Draw(_tmp)
    try:
        bbox = _d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    except Exception:
        bbox = (0, 0, font_size * max(1, len(text)), font_size)
    pad = stroke_w + 2 + _shadow_pad
    tw = max(1, bbox[2] - bbox[0] + pad * 2)
    th = max(1, bbox[3] - bbox[1] + pad * 2)

    _text_layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ld = ImageDraw.Draw(_text_layer)
    stroke_fill = (stroke_color[0], stroke_color[1], stroke_color[2],
                   int(255 * alpha_v)) if stroke_color else None
    try:
        ld.text((pad - bbox[0], pad - bbox[1]), text, font=font,
                fill=fg_a, stroke_width=stroke_w, stroke_fill=stroke_fill)
    except Exception as e:
        return "❌ Не удалось нарисовать текст: " + str(e)

    if _shadow_specs:
        try:
            from PIL import ImageFilter as _PILFilter
        except ImportError:
            _PILFilter = None
        _combined = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        _text_alpha = _text_layer.getchannel("A")
        for _sdx, _sdy, _sbl, _sclr in _shadow_specs:
            _sh_alpha = _text_alpha
            if _sbl > 0 and _PILFilter is not None:
                _sh_alpha = _sh_alpha.filter(
                    _PILFilter.GaussianBlur(float(_sbl)))
            _sh = Image.new("RGBA", (tw, th), _sclr)
            _sh.putalpha(_sh_alpha)
            _combined.alpha_composite(_sh, (int(_sdx), int(_sdy)))
        _combined.alpha_composite(_text_layer, (0, 0))
        layer = _combined
    else:
        layer = _text_layer

    persp = args.get("перспектива") or args.get("perspective")
    persp_applied = False
    if isinstance(persp, (list, tuple)) and len(persp) == 4:
        # Перспектива — локальные точки углов текстового блока
        # (относительно bbox). Позиция задаёт, куда положить блок.
        try:
            dst_pts = []
            for pt in persp:
                if not (isinstance(pt, (list, tuple)) and len(pt) >= 2):
                    raise ValueError("точка должна быть [x, y]")
                dst_pts.append((float(pt[0]), float(pt[1])))
            xs = [p[0] for p in dst_pts]
            ys = [p[1] for p in dst_pts]
            xmin = int(min(xs)) - 2
            ymin = int(min(ys)) - 2
            xmax = int(max(xs)) + 2
            ymax = int(max(ys)) + 2
            ww = max(1, xmax - xmin)
            wh = max(1, ymax - ymin)
            dst_local = [(p[0] - xmin, p[1] - ymin) for p in dst_pts]
            src_pts = [(0, 0), (tw, 0), (tw, th), (0, th)]
            coeffs = _find_coeffs(src_pts, dst_local)
            warped = layer.transform(
                (ww, wh), Image.PERSPECTIVE, coeffs, Image.BICUBIC
            )
            # Позиция: абсолютная [x,y] или именованная
            _pos = _read_xy(args)
            if isinstance(_pos, (list, tuple)) and len(_pos) >= 2:
                try:
                    px, py = int(_pos[0]), int(_pos[1])
                except (TypeError, ValueError):
                    px, py = _resolve_pos(_pos, (W, H), (ww, wh))
            else:
                px, py = _resolve_pos(_pos, (W, H), (ww, wh))
            base.alpha_composite(warped, (px + xmin, py + ymin))
            persp_applied = True
        except Exception as e:
            return "❌ Перспектива: " + str(e)

    if not persp_applied:
        try:
            angle = float(args.get("поворот") or 0.0)
        except (TypeError, ValueError):
            angle = 0.0
        try:
            shx = float(args.get("наклон_x") or 0.0)
        except (TypeError, ValueError):
            shx = 0.0
        try:
            shy = float(args.get("наклон_y") or 0.0)
        except (TypeError, ValueError):
            shy = 0.0

        if shx != 0.0 or shy != 0.0:
            shear_matrix = (1.0, shx, 0.0, shy, 1.0, 0.0)
            new_w = int(tw + abs(shx) * th) + 4
            new_h = int(th + abs(shy) * tw) + 4
            try:
                layer = layer.transform(
                    (new_w, new_h), Image.AFFINE, shear_matrix,
                    resample=Image.BICUBIC
                )
            except Exception as e:
                return "❌ Наклон: " + str(e)

        if angle != 0.0:
            layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)

        x, y = _resolve_pos(_read_xy(args), (W, H), layer.size)
        base.alpha_composite(layer, (x, y))

    out_img = base.convert("RGB") if _is_jpeg else base
    save_arg = args.get("сохранить")

    if save_arg:
        out_path = Path(save_arg).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_img.save(str(out_path))
        except Exception as e:
            return "❌ Ошибка сохранения: " + str(e)
        return ("✅ " + str(out_path)
                + " (" + str(out_img.size[0]) + "x" + str(out_img.size[1]) + ")")

    try:
        out_img.save(str(src_p))
    except Exception as e:
        return "❌ Ошибка перезаписи: " + str(e)

    try:
        from _actions.clipboard import _pil_to_clipboard
        _pil_to_clipboard(out_img.convert("RGB"))
        return ("[BUF] ✅ " + str(src_p)
                + " (" + str(out_img.size[0]) + "x" + str(out_img.size[1]) + ")")
    except Exception as e:
        return "✅ " + str(src_p) + " (в буфер не попал: " + str(e) + ")"
