"""Блум: яркие пиксели размываются и складываются с исходником (свечение / ореол)."""
from pathlib import Path

from _actions import register


def _load(path):
    from PIL import Image
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("Не найден файл: " + str(p))
    return p, Image.open(str(p))


def _bloom(img, threshold, radius, strength, mode):
    import numpy as np
    from PIL import Image, ImageFilter

    has_alpha = (img.mode in ("RGBA", "LA")
                 or (img.mode == "P" and "transparency" in img.info))
    alpha = img.convert("RGBA").split()[3] if has_alpha else None

    rgb = img.convert("RGB")
    a = np.asarray(rgb, dtype=np.float32)

    # 1. Яркостный порог: оставляем только пиксели ярче threshold.
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    mask = (lum > float(threshold))[..., None]
    bright = np.where(mask, a, 0.0).astype(np.uint8)

    # 2. Размытие яркой карты.
    blurred_img = Image.fromarray(bright, "RGB").filter(
        ImageFilter.GaussianBlur(float(radius))
    )
    blurred = np.asarray(blurred_img, dtype=np.float32)

    # 3. Сложение размытия с оригиналом.
    if mode == "add":
        out = a + float(strength) * blurred
    elif mode == "soft_light":
        base = a / 255.0
        glow = np.clip(blurred / 255.0 * float(strength), 0.0, 1.0)
        out = np.where(
            glow <= 0.5,
            2.0 * base * glow + base * base * (1.0 - 2.0 * glow),
            2.0 * base * (1.0 - glow) + np.sqrt(base) * (2.0 * glow - 1.0),
        ) * 255.0
    else:  # "screen" — по умолчанию, самое мягкое
        base = a / 255.0
        glow = np.clip(blurred / 255.0 * float(strength), 0.0, 1.0)
        out = (1.0 - (1.0 - base) * (1.0 - glow)) * 255.0

    out = np.clip(out, 0, 255).astype(np.uint8)
    result = Image.fromarray(out, "RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


@register(["блум", "bloom", "свечение", "glow", "ореол", "сияние"])
def bloom_image(args):
    """Наложить блум: яркие пиксели размываются и складываются с исходником.

    Вокруг источников света и контрастных бликов появляется мягкий
    светящийся ореол вместо резкой границы.

    Параметры:
      путь       — входной файл (обязательный)
      порог      — яркость 0..255, выше которой пиксель считается
                   источником (по умолч. 200)
      радиус     — радиус размытия в px (по умолч. 15)
      сила       — множитель размытия при сложении, 0..5 (по умолч. 1.0)
      режим      — "screen" (по умолч., мягко), "add" (жёстко,
                   выжигает), "soft_light" (мягкий свет)
                   алиасы: mode, режим
      сохранить  — путь для сохранения; по умолч. MY_DIR/bloom_out/
      в_контекст — положить в контекст (по умолч. true)

    Примеры:
      {"блум": {"путь": "C:/rpg/light.png"}}
      {"блум": {"путь": "C:/rpg/light.png", "порог": 180, "радиус": 25}}
      {"блум": {"путь": "C:/rpg/light.png", "сила": 1.6,
                "режим": "add", "сохранить": "C:/rpg/light_bloom.png"}}
    """
    path_arg = args.get("путь") or args.get("файл")
    if not path_arg:
        return 'Укажи путь: {"блум": {"путь": "C:/pic.png"}}'
    try:
        src_p, img = _load(path_arg)
    except Exception as e:
        return "❌ " + str(e)

    try:
        threshold = int(args.get("порог", 200))
    except (TypeError, ValueError):
        return "❌ порог должен быть числом 0..255"
    threshold = max(0, min(255, threshold))

    try:
        radius = float(args.get("радиус", 15))
    except (TypeError, ValueError):
        return "❌ радиус должен быть числом > 0"
    if radius <= 0:
        return "❌ радиус должен быть > 0"

    try:
        strength = float(args.get("сила", 1.0))
    except (TypeError, ValueError):
        return "❌ сила должна быть числом 0..5"
    strength = max(0.0, min(5.0, strength))

    raw_mode = (args.get("режим") or args.get("mode") or "screen").strip().lower()
    _mmap = {
        "screen": "screen", "экран": "screen", "мягкий": "screen",
        "add": "add", "добавить": "add", "сложение": "add",
        "жёсткий": "add", "жесткий": "add",
        "soft_light": "soft_light", "soft-light": "soft_light",
        "мягкий_свет": "soft_light", "soft": "soft_light",
    }
    mode = _mmap.get(raw_mode)
    if not mode:
        return ("❌ Неизвестный режим: " + raw_mode
                + ". Доступно: screen, add, soft_light")

    try:
        out = _bloom(img, threshold, radius, strength, mode)
    except Exception as e:
        return "❌ " + str(e)

    out_path = None
    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser()
    else:
        try:
            from _config import MY_DIR
            out_dir = MY_DIR / "bloom_out"
            out_dir.mkdir(parents=True, exist_ok=True)
            import time as _t
            out_path = out_dir / (src_p.stem + "_bloom_"
                                  + _t.strftime("%H%M%S")
                                  + src_p.suffix)
        except Exception:
            out_path = None
    if out_path:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        out.save(str(out_path))

    to_ctx = args.get("в_контекст")
    if to_ctx is None:
        to_ctx = args.get("в_буфер", True)
    if isinstance(to_ctx, str):
        to_ctx = to_ctx.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_ctx:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(out)
            prefix = "[BUF] "
        except Exception:
            pass

    _ru = {"screen": "screen", "add": "add", "soft_light": "soft_light"}[mode]
    info = ("🌸 блум (порог=" + str(threshold)
            + ", радиус=" + str(radius)
            + ", сила=" + str(strength)
            + ", " + _ru + "): "
            + str(out.size[0]) + "x" + str(out.size[1]))
    if out_path:
        info += " -> " + str(out_path)
    return prefix + info
