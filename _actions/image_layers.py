"""Композитинг изображений: слои, наложение, blend-режимы."""
from pathlib import Path

from _actions import register


_MODES = ("normal", "multiply", "screen", "overlay", "add", "subtract")
_VIDEO_EXTS = (".webm", ".mp4", ".mov", ".mkv", ".avi", ".gif")


def _is_video_path(path):
    return Path(str(path)).suffix.lower() in _VIDEO_EXTS


def _probe_video_rgba(path):
    import json as _json
    import subprocess as _sp
    from PIL import Image
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("Не найден файл: " + str(p))
    ffprobe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name",
        "-of", "json", str(p),
    ]
    pr = _sp.run(ffprobe_cmd, capture_output=True)
    if pr.returncode != 0:
        raise RuntimeError("ffprobe error: " + pr.stderr.decode("utf-8", errors="replace")[-200:])
    info = _json.loads(pr.stdout.decode("utf-8", errors="replace"))
    streams = info.get("streams") or []
    if not streams:
        raise RuntimeError("В файле нет видеопотока: " + str(p))
    st = streams.__getitem__(0)
    w, h = int(st.get("width", 0)), int(st.get("height", 0))
    codec = str(st.get("codec_name", "")).lower()
    r_str = str(st.get("r_frame_rate", "25/1"))
    if "/" in r_str:
        num, den = r_str.split("/", 1)
        fps = float(num) / max(1.0, float(den))
    else:
        fps = float(r_str or 25.0)
    if fps <= 0 or fps > 120:
        fps = 25.0
    dec_cmd = ["ffmpeg", "-v", "error"]
    if codec == "vp9" or p.suffix.lower() == ".webm":
        dec_cmd += ["-c:v", "libvpx-vp9"]
    elif codec == "vp8":
        dec_cmd += ["-c:v", "libvpx"]
    dec_cmd += ["-i", str(p), "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    dp = _sp.run(dec_cmd, capture_output=True)
    if dp.returncode != 0 or not dp.stdout:
        raise RuntimeError("ffmpeg decode error: " + dp.stderr.decode("utf-8", errors="replace")[-200:])
    frame_bytes = w * h * 4
    n_frames = len(dp.stdout) // frame_bytes
    frames = []
    for idx in range(n_frames):
        chunk = dp.stdout[idx * frame_bytes:(idx + 1) * frame_bytes]
        frames.append(Image.frombytes("RGBA", (w, h), chunk))
    ap_cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(p)]
    ap = _sp.run(ap_cmd, capture_output=True)
    has_audio = bool(ap.stdout.strip())
    return frames, fps, has_audio


def _load_rgba(path):
    from PIL import Image
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("Не найден файл: " + str(p))
    return Image.open(str(p)).convert("RGBA")


def _blend_onto(base, top, pos, alpha, mode):
    from PIL import Image
    if alpha < 1.0:
        r, g, b, a = top.split()
        a = a.point(lambda v: int(v * alpha))
        top = Image.merge("RGBA", (r, g, b, a))
    if mode == "normal":
        base.alpha_composite(top, dest=(int(pos[0]), int(pos[1])))
        return base
    # для остальных режимов — разложить на цвет + альфу
    from PIL import ImageChops
    W, H = base.size
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(top, dest=(int(pos[0]), int(pos[1])))
    br, bg, bb, ba = base.split()
    tr, tg, tb, ta = canvas.split()
    base_rgb = Image.merge("RGB", (br, bg, bb))
    top_rgb = Image.merge("RGB", (tr, tg, tb))
    if mode == "multiply":
        merged = ImageChops.multiply(base_rgb, top_rgb)
    elif mode == "screen":
        merged = ImageChops.screen(base_rgb, top_rgb)
    elif mode == "add":
        merged = ImageChops.add(base_rgb, top_rgb)
    elif mode == "subtract":
        merged = ImageChops.subtract(base_rgb, top_rgb)
    elif mode == "overlay":
        # overlay ~ soft-light inverted; PIL не имеет напрямую
        # эмулируем: screen(base, multiply(base, top))
        merged = ImageChops.screen(
            base_rgb, ImageChops.multiply(base_rgb, top_rgb))
    else:
        merged = base_rgb
    # склеить обратно: RGB + alpha верхнего слоя (там где он непрозрачен)
    mr, mg, mb = merged.split()
    result = Image.merge("RGBA", (mr, mg, mb, ta))
    # и наложить на base
    out = Image.new("RGBA", (W, H))
    out.paste(base, (0, 0))
    out.alpha_composite(result)
    return out


def _apply_shadow(img, offset=(4, 4), blur=8, alpha=0.5,
                  color=(0, 0, 0), angle=0.0, magnitude=0.0):
    """Drop shadow с опциональным наклоном (shear).

    Возвращает (img, (pad_l, pad_t)) — расширенный холст и сдвиг,
    на который оригинал сдвинулся относительно него.
    Пользователь задаёт позицию левого-верхнего угла оригинала,
    а функция возвращает компенсацию для этой позиции.
    """
    from PIL import Image, ImageFilter
    import math

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    ox, oy = int(offset[0]), int(offset[1])

    sk = int(abs(magnitude)) + 10 if magnitude else 0
    pad_l = max(0, -ox) + blur * 2 + sk
    pad_t = max(0, -oy) + blur * 2 + sk
    pad_r = max(0, ox) + blur * 2 + sk
    pad_b = max(0, oy) + blur * 2 + sk
    new_w = w + pad_l + pad_r
    new_h = h + pad_t + pad_b

    a = Image.new("L", (new_w, new_h), 0)
    a.paste(img.split()[-1], (pad_l + ox, pad_t + oy))

    if magnitude and angle:
        ang = math.radians(angle)
        k = math.tan(ang) * (magnitude / max(1, h))
        cy = new_h / 2.0
        a = a.transform(
            (new_w, new_h), Image.AFFINE,
            (1, k, -k * cy, 0, 1, 0),
            resample=Image.BILINEAR,
        )

    if blur > 0:
        a = a.filter(ImageFilter.GaussianBlur(blur))
    if alpha < 1.0:
        a = a.point(lambda v: int(v * alpha))

    sr = Image.new("L", (new_w, new_h), int(color[0]))
    sg = Image.new("L", (new_w, new_h), int(color[1]))
    sb = Image.new("L", (new_w, new_h), int(color[2]))
    shadow_rgba = Image.merge("RGBA", (sr, sg, sb, a))

    out = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    out.alpha_composite(shadow_rgba, dest=(0, 0))
    out.alpha_composite(img, dest=(pad_l, pad_t))
    return out, (pad_l, pad_t)


def _apply_reflection(img, height_frac=0.4, alpha=0.5, blur=2,
                      fade=True, gap=0):
    """Добавить отражение вниз — имитация мокрого асфальта.

    Отражение строится от НИЖНЕЙ ГРАНИЦЫ НЕПРОЗРАЧНОГО (bbox по альфе),
    а не от края файла. Прозрачный padding игнорируется.
    """
    from PIL import Image, ImageFilter
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size

    a_orig = img.split()[-1]
    bbox = a_orig.getbbox()
    if not bbox:
        return img  # пусто, отражать нечего
    _x0, _y0, _x1, _y1 = bbox
    obj_h = _y1 - _y0
    if obj_h <= 0:
        return img

    rh = max(1, int(obj_h * float(height_frac)))
    # берём нижние rh пикселей объекта
    y_start = max(0, _y1 - rh)
    refl_src = img.crop((0, y_start, w, _y1))
    refl = refl_src.transpose(Image.FLIP_TOP_BOTTOM)

    if blur > 0:
        refl = refl.filter(ImageFilter.GaussianBlur(blur))

    if fade:
        a = refl.split()[-1]
        grad = Image.new("L", (1, rh))
        for y in range(rh):
            v = int(255 * (1.0 - y / max(1, rh - 1)))
            grad.putpixel((0, y), v)
        grad = grad.resize((w, rh), Image.BILINEAR)
        a = Image.composite(a, Image.new("L", (w, rh), 0), grad)
        r, g, b, _ = refl.split()
        refl = Image.merge("RGBA", (r, g, b, a))

    if alpha < 1.0:
        r, g, b, a = refl.split()
        a = a.point(lambda v: int(v * alpha))
        refl = Image.merge("RGBA", (r, g, b, a))

    # новая высота — либо h, либо столько, чтобы отражение влезло
    new_h = max(h, _y1 + gap + rh)
    out = Image.new("RGBA", (w, new_h), (0, 0, 0, 0))
    out.alpha_composite(img, dest=(0, 0))
    out.alpha_composite(refl, dest=(0, _y1 + gap))
    return out


def _apply_color(img, brightness=1.0, contrast=1.0,
                 saturation=1.0, sharpness=1.0, hue=0.0):
    """Цветокоррекция: яркость, контраст, насыщенность, резкость, тон.

    Все множители 1.0 = как есть. Тон — сдвиг в градусах (-180..180).
    """
    from PIL import Image, ImageEnhance

    if not isinstance(img, Image.Image):
        return img
    if img.mode != "RGB":
        img = img.convert("RGB")

    if hue and abs(float(hue)) > 0.01:
        hsv = img.convert("HSV")
        h, s_, v = hsv.split()
        shift = int(round(float(hue) / 360.0 * 255.0))
        h = h.point(lambda x: (x + shift) % 256)
        img = Image.merge("HSV", (h, s_, v)).convert("RGB")

    if abs(float(brightness) - 1.0) > 0.001:
        img = ImageEnhance.Brightness(img).enhance(float(brightness))
    if abs(float(contrast) - 1.0) > 0.001:
        img = ImageEnhance.Contrast(img).enhance(float(contrast))
    if abs(float(saturation) - 1.0) > 0.001:
        img = ImageEnhance.Color(img).enhance(float(saturation))
    if abs(float(sharpness) - 1.0) > 0.001:
        img = ImageEnhance.Sharpness(img).enhance(float(sharpness))
    return img


@register(["слои", "слой", "склей", "склеить", "композит", "композитинг", "наложить"])
def layers(args):
    """Собрать картинку из слоёв: фон + наложения.

    Параметры:
      низ           — нижний слой (путь к PNG/JPG или видео WEBM/MP4/MOV/GIF; обязательный)
      сверху        — список путей к верхним слоям (картинки или видео, в т.ч. прозрачный WEBM yuva420p)
      fps           — частота кадров для видеорежима (по умолчанию от самого длинного видео или 25)
      позиции       — список [x, y] для каждого верхнего слоя.
                      Это ЦЕНТР слоя (по границам непрозрачного).
                      По умолчанию — центр холста.
      масштабы      — список множителей размера для верхних (1.0 = как есть;
                      0.3 = уменьшить в ~3.3 раза; работает и с числом — тогда
                      одно значение для всех)
      прозрачность  — список альф 0..1 для верхних (по умолчанию 1.0)
      тени          — список теней для верхних. Каждая — объект:
                      {смещение: [dx, dy], размытие: 8, альфа: 0.5,
                       цвет: [r, g, b], угол: 0, магнитуда: 0}.
                      Можно true (тень по умолчанию) или один объект
                      на все слои.
      отражения     — список отражений (мокрый асфальт) для верхних слоёв.
                      Каждый — объект: {высота: 0.4, прозрачность: 0.5,
                      размытие: 2, затухание: true, отступ: 0}.
                      Можно true — отражение по умолчанию.
      цветокоррекции — список цветокоррекций для ВЕРХНИХ слоёв (по объекту
                      на позицию). Каждый — объект {яркость, контраст,
                      насыщенность, резкость, тон} или null (пропустить слой).
                      Можно один объект — применится ко всем верхним.
      цветокоррекция — блок для итоговой картинки:
                      {яркость: 1.0, контраст: 1.0, насыщенность: 1.0,
                       резкость: 1.0, тон: 0}
                      Все множители 1.0 = как есть; тон — сдвиг в градусах
                      (-180..180). Применяется после склейки.
      режим         — normal (по умолч.) / multiply / screen / overlay / add / subtract
      размер        — [ширина, высота] — вписать результат (по умолч. как у нижнего)
      сохранить     — путь к PNG (или к WEBM/MP4 при наличии видеослоёв)
      в_контекст    — положить в контекст (по умолч. true)

    Пример:
      {"слои": {
        "низ": "C:/rpg/bar_bg.png",
        "сверху": ["C:/rpg/kay.png", "C:/rpg/cop.png"],
        "позиции": [[100, 200], [500, 300]],   // ЦЕНТР каждого слоя
        "масштабы": [1.0, 0.3],
        "тени": [
          {смещение: [6, 8], размытие: 12, альфа: 0.6,
           угол: 60, магнитуда: 30},
          true
        ],
        "цветокоррекции": [null, {яркость: 0.6, насыщенность: 0.5}],
        "сохранить": "C:/rpg/scene_001.png"
      }}
    """
    from PIL import Image

    args = dict(args)

    def _extract_path(d):
        if isinstance(d, dict):
            return (d.get("путь") or d.get("файл") or d.get("path")
                    or d.get("src") or d.get("картинка"))
        return None

    # base_path тоже может прийти как dict
    for _k in ("низ", "фон", "base"):
        if isinstance(args.get(_k), dict):
            _bp = _extract_path(args[_k])
            if _bp:
                args[_k] = _bp

    base_path = args.get("низ") or args.get("фон") or args.get("base")
    if not base_path:
        return 'Укажи нижний слой: {"слои": {"низ": "C:/bg.png"}}'

    _raw_over_check = args.get("сверху") or args.get("слои") or []
    if isinstance(_raw_over_check, str):
        _raw_over_check = [_raw_over_check]
    _over_paths_check = []
    if isinstance(_raw_over_check, list):
        for _it in _raw_over_check:
            if isinstance(_it, dict):
                _p = _extract_path(_it)
                if _p:
                    _over_paths_check.append(str(_p))
            elif isinstance(_it, (str, Path)):
                _over_paths_check.append(str(_it))

    if not args.get("_video_frame_mode") and (_is_video_path(base_path) or any(_is_video_path(_p) for _p in _over_paths_check)):
        return _layers_video(args, base_path, _over_paths_check)

    try:
        base = args.get("_base_pil").copy() if args.get("_base_pil") is not None else _load_rgba(base_path)
    except Exception as e:
        return "❌ " + str(e)

    over = args.get("сверху") or args.get("слои") or []
    if isinstance(over, str):
        over = [over]
    if not isinstance(over, list):
        return "❌ 'сверху' должен быть списком путей или объектов."

    # --- Дружелюбная нормализация: слои могут прийти как dict-объекты ---
    # LLM часто передаёт {"сверху": [{"путь": ..., "позиции": ..., ...}]}
    # вместо {"сверху": [path], "позиции": [[...]]}. Поддерживаем оба.
    if any(isinstance(_x, dict) for _x in over):
        _inline_pos, _inline_sc, _inline_al = [], [], []
        _inline_sh, _inline_rf, _inline_cc = [], [], []
        _over_clean = []
        for _i, _it in enumerate(over):
            if isinstance(_it, dict):
                _p = _extract_path(_it)
                if not _p:
                    return ("❌ Слой #" + str(_i + 1)
                            + ": объект без 'путь'/'файл'. Пример: "
                            "{\"путь\": \"C:/x.png\", \"позиции\": [400, 400]}")
                _over_clean.append(str(_p))
                _inline_pos.append(_it.get("позиции", _it.get("позиция")))
                _inline_sc.append(_it.get("масштабы", _it.get("масштаб")))
                _inline_al.append(_it.get("прозрачность", _it.get("альфа")))
                _inline_sh.append(_it.get("тени", _it.get("тень")))
                _inline_rf.append(_it.get("отражения", _it.get("отражение")))
                _inline_cc.append(_it.get("цветокоррекции",
                                          _it.get("цветокоррекция")))
            elif isinstance(_it, (str, Path)):
                _over_clean.append(str(_it))
                _inline_pos.append(None); _inline_sc.append(None)
                _inline_al.append(None); _inline_sh.append(None)
                _inline_rf.append(None); _inline_cc.append(None)
            else:
                return ("❌ Слой #" + str(_i + 1)
                        + ": ожидается путь или объект, получено "
                        + type(_it).__name__)
        over = _over_clean
        args["сверху"] = over

        def _merge_parallel(arg_name, aliases, inline_list, n,
                            coord_field=False):
            val = args.get(arg_name)
            for a in aliases:
                if val is None:
                    val = args.get(a)
            if val is None:
                base_l = list(inline_list)
            elif isinstance(val, dict):
                base_l = [val.get(str(i), val.get(i)) for i in range(n)]
                for i in range(n):
                    if base_l[i] is None:
                        base_l[i] = inline_list[i]
            elif isinstance(val, (list, tuple)):
                base_l = list(val)
                if (coord_field and len(base_l) == 2
                        and all(isinstance(x, (int, float))
                                for x in base_l)):
                    base_l = [base_l]
                base_l = base_l + [None] * max(0, n - len(base_l))
                for i in range(n):
                    if base_l[i] is None:
                        base_l[i] = inline_list[i]
            else:
                base_l = [val] * n
            return base_l

        args["позиции"] = _merge_parallel(
            "позиции", ("позиция", "position"), _inline_pos,
            len(over), coord_field=True)
        args["масштабы"] = _merge_parallel(
            "масштабы", ("масштаб", "scale"), _inline_sc, len(over))
        args["прозрачность"] = _merge_parallel(
            "прозрачность", ("альфа", "alpha"), _inline_al, len(over))
        args["тени"] = _merge_parallel(
            "тени", ("тень", "shadow"), _inline_sh, len(over))
        args["отражения"] = _merge_parallel(
            "отражения", ("отражение", "reflection"), _inline_rf,
            len(over))
        # None в тенях/отражениях = «без тени/отражения».
        # Приводим к False, чтобы валидация ниже не падала.
        args["тени"] = [False if _x is None else _x
                        for _x in args["тени"]]
        args["отражения"] = [False if _x is None else _x
                            for _x in args["отражения"]]
        args["цветокоррекции"] = _merge_parallel(
            "цветокоррекции", ("цветокоррекция_слоёв",),
            _inline_cc, len(over))

    positions = args.get("позиции")
    if positions is None:
        positions = []
    if isinstance(positions, dict):
        positions = [positions.get(str(i)) or positions.get(i)
                     for i in range(len(over))]
    if not isinstance(positions, list):
        return ("❌ 'позиции' должен быть списком [x, y] или словарём "
                "{0: [x, y], 1: [x, y]}. Получено: "
                + type(positions).__name__)

    alphas = args.get("прозрачность")
    if alphas is None:
        alphas = []
    if isinstance(alphas, (int, float)):
        alphas = [float(alphas)] * len(over)
    if not isinstance(alphas, list):
        return ("❌ 'прозрачность' должна быть списком чисел или одним числом. "
                "Получено: " + type(alphas).__name__)

    scales = args.get("масштабы")
    if scales is None:
        scales = args.get("масштаб")
    if scales is None:
        scales = []
    if isinstance(scales, (int, float)):
        scales = [float(scales)] * len(over)
    if not isinstance(scales, list):
        return ("❌ 'масштабы' должны быть списком чисел или одним числом. "
                "Получено: " + type(scales).__name__)

    shadows = args.get("тени")
    if shadows is None:
        shadows = args.get("тень")
    if shadows is None:
        shadows = []
    if isinstance(shadows, (dict, bool)):
        shadows = [shadows] * len(over)
    if not isinstance(shadows, list):
        return ("❌ 'тени' должны быть списком объектов/true или "
                "одним объектом/true. Получено: "
                + type(shadows).__name__)
    for _i, _sh in enumerate(shadows):
        if not isinstance(_sh, (dict, bool)):
            return ("❌ тень #" + str(_i + 1) + " должна быть объектом "
                    "{...} или true. Получено: " + type(_sh).__name__)

    reflections = args.get("отражения")
    if reflections is None:
        reflections = args.get("отражение")
    if reflections is None:
        reflections = []
    if isinstance(reflections, (dict, bool)):
        reflections = [reflections] * len(over)
    if not isinstance(reflections, list):
        return ("❌ 'отражения' должны быть списком объектов/true или "
                "одним объектом/true. Получено: "
                + type(reflections).__name__)
    for _i, _rf in enumerate(reflections):
        if not isinstance(_rf, (dict, bool)):
            return ("❌ отражение #" + str(_i + 1) + " должно быть объектом "
                    "{...} или true. Получено: " + type(_rf).__name__)

    layer_cc = args.get("цветокоррекции")
    if layer_cc is None:
        layer_cc = args.get("цветокоррекция_слоёв")
    if layer_cc is None:
        layer_cc = []
    if isinstance(layer_cc, dict):
        layer_cc = [layer_cc] * len(over)
    if not isinstance(layer_cc, list):
        return ("❌ 'цветокоррекции' должны быть списком объектов или "
                "одним объектом. Получено: " + type(layer_cc).__name__)
    for _i, _lc in enumerate(layer_cc):
        if _lc is not None and not isinstance(_lc, dict):
            return ("❌ цветокоррекция слоя #" + str(_i + 1)
                    + " должна быть объектом {...} или null. Получено: "
                    + type(_lc).__name__)

    mode = (args.get("режим") or args.get("mode") or "normal").lower()
    if mode not in _MODES:
        return ("❌ Неизвестный режим: " + mode
                + ". Доступно: " + ", ".join(_MODES))

    _fx_parts = []
    _over_pils = args.get("_over_pils") or []
    W, H = base.size
    for i, layer_path in enumerate(over):
        try:
            if i < len(_over_pils) and _over_pils[i] is not None:
                top = _over_pils[i].copy()
            else:
                top = _load_rgba(layer_path)
        except Exception as e:
            return "❌ Слой #" + str(i + 1) + ": " + str(e)

        _pos_adjust = (0, 0)

        # масштаб слоя
        try:
            sc = float(scales[i]) if i < len(scales) else 1.0
        except (TypeError, ValueError):
            sc = 1.0
        if sc != 1.0 and sc > 0:
            new_w = max(1, int(top.size[0] * sc))
            new_h = max(1, int(top.size[1] * sc))
            top = top.resize((new_w, new_h), Image.LANCZOS)

        # центр ОРИГИНАЛЬНОГО слоя (для видео — геометрический центр кадра, чтобы не дрожало от bbox альфы)
        if args.get("_video_frame_mode"):
            _center_orig = (top.size[0] / 2.0, top.size[1] / 2.0)
        else:
            try:
                _bbox0 = top.split()[-1].getbbox()
            except Exception:
                _bbox0 = None
            if _bbox0:
                _center_orig = ((_bbox0[0] + _bbox0[2]) / 2.0,
                                (_bbox0[1] + _bbox0[3]) / 2.0)
            else:
                _center_orig = (top.size[0] / 2.0, top.size[1] / 2.0)

        # цветокоррекция слоя (если задана)
        _lc = layer_cc[i] if i < len(layer_cc) else None
        if isinstance(_lc, dict):
            def _lf(key, *aliases, default=1.0):
                for k in (key,) + aliases:
                    if k in _lc:
                        try:
                            return float(_lc[k])
                        except (TypeError, ValueError):
                            return default
                return default
            _lc_b = _lf("яркость", "brightness")
            _lc_c = _lf("контраст", "contrast")
            _lc_s = _lf("насыщенность", "saturation")
            _lc_sh = _lf("резкость", "sharpness")
            _lc_h = _lf("тон", "оттенок", "hue", default=0.0)
            _alpha_ch = top.split()[-1] if top.mode == "RGBA" else None
            top = _apply_color(
                top,
                brightness=_lc_b,
                contrast=_lc_c,
                saturation=_lc_s,
                sharpness=_lc_sh,
                hue=_lc_h,
            )
            if _alpha_ch is not None:
                top = top.convert("RGBA")
                top.putalpha(_alpha_ch)
            _lc_parts = []
            if abs(_lc_b - 1.0) > 0.001:
                _lc_parts.append("ярк=" + format(_lc_b, ".2f"))
            if abs(_lc_c - 1.0) > 0.001:
                _lc_parts.append("контр=" + format(_lc_c, ".2f"))
            if abs(_lc_s - 1.0) > 0.001:
                _lc_parts.append("насыщ=" + format(_lc_s, ".2f"))
            if abs(_lc_sh - 1.0) > 0.001:
                _lc_parts.append("резк=" + format(_lc_sh, ".2f"))
            if abs(_lc_h) > 0.01:
                _lc_parts.append("тон=" + format(_lc_h, "+.0f"))
            if _lc_parts:
                _fx_parts.append("цвет слоя#" + str(i + 1)
                                 + ": " + ", ".join(_lc_parts))

        # тень (если задана) — до наложения
        sh = shadows[i] if i < len(shadows) else None
        if sh:
            if isinstance(sh, bool):
                sh = {}
            if not isinstance(sh, dict):
                sh = {}
            _off = sh.get("смещение") or sh.get("offset") or (4, 4)
            if isinstance(_off, (int, float)):
                _off = (int(_off), int(_off))
            try:
                _blur = int(sh.get("размытие") or sh.get("blur") or 8)
            except (TypeError, ValueError):
                _blur = 8
            try:
                _sa = float(sh.get("альфа") or sh.get("alpha") or 0.5)
            except (TypeError, ValueError):
                _sa = 0.5
            _col = sh.get("цвет") or sh.get("color") or (0, 0, 0)
            if not (isinstance(_col, (list, tuple)) and len(_col) == 3):
                _col = (0, 0, 0)
            try:
                _ang = float(sh.get("угол") or sh.get("angle") or 0)
            except (TypeError, ValueError):
                _ang = 0.0
            try:
                _mag = float(sh.get("магнитуда") or sh.get("magnitude") or 0)
            except (TypeError, ValueError):
                _mag = 0.0
            top, _shift = _apply_shadow(top, offset=_off, blur=_blur,
                                        alpha=_sa, color=tuple(_col),
                                        angle=_ang, magnitude=_mag)
            _pos_adjust = _shift
            _tline = ("тень#" + str(i + 1)
                      + ": смещ=" + str(list(_off))
                      + " разм=" + str(_blur)
                      + " α=" + format(_sa, ".2f"))
            if _ang or _mag:
                _tline += (" накл=" + format(_ang, ".0f") + "°"
                           + " магн=" + format(_mag, ".0f"))
            _fx_parts.append(_tline)

        # отражение (если задано)
        rf = reflections[i] if i < len(reflections) else None
        if rf:
            if isinstance(rf, bool):
                rf = {}
            if not isinstance(rf, dict):
                rf = {}
            try:
                _rh = float(rf.get("высота") or rf.get("height") or 0.4)
            except (TypeError, ValueError):
                _rh = 0.4
            try:
                _ra = float(rf.get("прозрачность") or rf.get("alpha") or 0.5)
            except (TypeError, ValueError):
                _ra = 0.5
            try:
                _rb = float(rf.get("размытие") or rf.get("blur") or 2)
            except (TypeError, ValueError):
                _rb = 2
            _rfade = rf.get("затухание", rf.get("fade", True))
            if isinstance(_rfade, str):
                _rfade = _rfade.strip().lower() not in ("0", "false", "нет", "off", "")
            try:
                _rg = int(rf.get("отступ") or rf.get("gap") or 0)
            except (TypeError, ValueError):
                _rg = 0
            top = _apply_reflection(top, height_frac=_rh, alpha=_ra,
                                    blur=_rb, fade=bool(_rfade), gap=_rg)
            _fx_parts.append("отраж#" + str(i + 1)
                             + ": h=" + format(_rh, ".2f")
                             + " α=" + format(_ra, ".2f")
                             + " разм=" + format(_rb, ".0f"))

        pos = positions[i] if i < len(positions) else None
        if isinstance(pos, dict):
            pos = (pos.get("x", pos.get("0", W // 2)), pos.get("y", pos.get("1", H // 2)))
        if not pos:
            pos = (W // 2, H // 2)
        try:
            alpha = float(alphas[i]) if i < len(alphas) else 1.0
        except (TypeError, ValueError):
            alpha = 1.0
        # pos = ЦЕНТР оригинального слоя. top сейчас, возможно, расширен
        # тенью/отражением — компенсируем _pos_adjust (сдвиг паддинга).
        pos = (int(pos[0] - _center_orig[0] - _pos_adjust[0]),
               int(pos[1] - _center_orig[1] - _pos_adjust[1]))
        base = _blend_onto(base, top, pos, alpha, mode)

    size_arg = args.get("размер")
    if size_arg and isinstance(size_arg, (list, tuple)) and len(size_arg) == 2:
        try:
            base = base.resize((int(size_arg[0]), int(size_arg[1])),
                               Image.LANCZOS)
        except Exception:
            pass

    # цветокоррекция итоговой картинки
    _cc_applied = None
    cc = args.get("цветокоррекция")
    if cc is None:
        cc = args.get("цветокор")
    # терпимость к форме: список из одного dict = сам dict
    if isinstance(cc, list):
        if len(cc) == 1 and isinstance(cc[0], dict):
            cc = cc[0]
        elif len(cc) == 0:
            cc = None
        else:
            return ("❌ 'цветокоррекция' — один блок на итог. "
                    "Передан список из " + str(len(cc))
                    + ". Сделай список из одного объекта, либо один объект, "
                    "либо применяй цветокоррекцию по слоям отдельной "
                    "командой.")
    if cc is not None and not isinstance(cc, dict):
        return ("❌ 'цветокоррекция' должна быть объектом "
                "{яркость: 1.0, контраст: 1.0, ...}. Получено: "
                + type(cc).__name__)
    if isinstance(cc, dict):
        def _f(key, *aliases, default=1.0):
            for k in (key,) + aliases:
                if k in cc:
                    try:
                        return float(cc[k])
                    except (TypeError, ValueError):
                        return default
            return default
        try:
            _cc_b = _f("яркость", "brightness")
            _cc_c = _f("контраст", "contrast")
            _cc_s = _f("насыщенность", "saturation")
            _cc_sh = _f("резкость", "sharpness")
            _cc_h = _f("тон", "оттенок", "hue", default=0.0)
            base = _apply_color(
                base,
                brightness=_cc_b,
                contrast=_cc_c,
                saturation=_cc_s,
                sharpness=_cc_sh,
                hue=_cc_h,
            )
            _parts = []
            if abs(_cc_b - 1.0) > 0.001:
                _parts.append("ярк=" + format(_cc_b, ".2f"))
            if abs(_cc_c - 1.0) > 0.001:
                _parts.append("контр=" + format(_cc_c, ".2f"))
            if abs(_cc_s - 1.0) > 0.001:
                _parts.append("насыщ=" + format(_cc_s, ".2f"))
            if abs(_cc_sh - 1.0) > 0.001:
                _parts.append("резк=" + format(_cc_sh, ".2f"))
            if abs(_cc_h) > 0.01:
                _parts.append("тон=" + format(_cc_h, "+.0f"))
            if _parts:
                _cc_applied = ", ".join(_parts)
        except Exception as e:
            print("color correction error:", e)

    out_path = None
    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser().resolve()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        base.save(str(out_path))

    to_buf = args.get("в_контекст")
    if to_buf is None:
        to_buf = args.get("в_буфер", True)
    if isinstance(to_buf, str):
        to_buf = to_buf.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_buf:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(base.convert("RGB"))
            prefix = "[BUF] "
        except Exception:
            pass
    _total = len(over) + 1  # низ + верхние
    info = (str(base.size[0]) + "x" + str(base.size[1])
            + ", слоёв: " + str(_total))
    if _cc_applied:
        info += " | цвет: " + _cc_applied
    if _fx_parts:
        info += " | " + " ; ".join(_fx_parts)
    if args.get("_return_pil"):
        return base
    if out_path:
        info += " -> " + str(out_path)
    return prefix + "Склеено: " + info

def _layers_video(args, base_path, over_paths):
    import subprocess as _sp
    from _config import MY_DIR

    try:
        if _is_video_path(base_path):
            base_frames, base_fps, base_audio = _probe_video_rgba(base_path)
        else:
            base_frames, base_fps, base_audio = [_load_rgba(base_path)], None, False
    except Exception as e:
        return "❌ " + str(e)

    over_sources = []
    fps = base_fps
    audio_src = str(Path(base_path).expanduser()) if base_audio else None
    for idx, p in enumerate(over_paths):
        try:
            if _is_video_path(p):
                fr, v_fps, v_aud = _probe_video_rgba(p)
                if fps is None:
                    fps = v_fps
                if v_aud:
                    audio_src = str(Path(p).expanduser())
                over_sources.append(fr)
            else:
                over_sources.append([_load_rgba(p)])
        except Exception as e:
            return "❌ Слой #" + str(idx + 1) + ": " + str(e)

    if fps is None or fps <= 0:
        fps = float(args.get("fps") or 25.0)
    else:
        fps = float(args.get("fps") or fps)

    total_frames = max([len(base_frames)] + [len(s) for s in over_sources])
    if total_frames <= 0:
        return "❌ Не удалось прочитать кадры видео."

    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser().resolve()
    else:
        out_path = (Path(MY_DIR) / "layers_out.webm").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    first_base = base_frames.__getitem__(0)
    first_overs = [s.__getitem__(0) for s in over_sources]
    probe_args = dict(args)
    probe_args["_video_frame_mode"] = True
    probe_args["_return_pil"] = True
    probe_args["в_контекст"] = False
    probe_args["сохранить"] = None
    probe_args["_base_pil"] = first_base
    probe_args["_over_pils"] = first_overs
    sample_out = layers(probe_args)
    if isinstance(sample_out, str):
        return sample_out
    out_w, out_h = sample_out.size

    is_webm = out_path.suffix.lower() == ".webm"
    if is_webm:
        ff_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{out_w}x{out_h}",
            "-r", str(fps), "-i", "-",
        ]
        if audio_src:
            ff_cmd += ["-i", audio_src]
        ff_cmd += [
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0", "-deadline", "realtime", "-cpu-used", "4",
            "-b:v", "1800k",
        ]
        if audio_src:
            ff_cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "libopus", "-shortest"]
        ff_cmd.append(str(out_path))
    else:
        ff_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{out_w}x{out_h}",
            "-r", str(fps), "-i", "-",
        ]
        if audio_src:
            ff_cmd += ["-i", audio_src]
        ff_cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
        ]
        if audio_src:
            ff_cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"]
        ff_cmd.append(str(out_path))

    proc = _sp.Popen(ff_cmd, stdin=_sp.PIPE, stderr=_sp.DEVNULL)
    try:
        for fi in range(total_frames):
            if fi == 0:
                comp = sample_out
            else:
                f_args = dict(probe_args)
                f_args["_base_pil"] = base_frames[fi % len(base_frames)]
                f_args["_over_pils"] = [s[fi % len(s)] for s in over_sources]
                comp = layers(f_args)
                if isinstance(comp, str):
                    break
            proc.stdin.write(comp.convert("RGBA").tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        return f"❌ Ошибка кодирования видео ffmpeg (код {proc.returncode})"

    return (
        f"Склеено видео: {out_w}x{out_h}, {total_frames} кадр. @ {fps:.2f} fps, "
        f"слоёв: {len(over_sources) + 1} -> {out_path}"
    )
