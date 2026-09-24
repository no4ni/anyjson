"""Зеркалирование изображения: горизонталь / вертикаль."""
from pathlib import Path

from _actions import register


def _load(path):
    from PIL import Image
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("Не найден файл: " + str(p))
    return p, Image.open(str(p))


@register(["зеркало", "отразить", "зеркалить", "mirror"])
def mirror_image(args):
    """Отразить картинку по горизонтали или вертикали.

    Параметры:
      путь       — входной файл (обязательный)
      ось        — "горизонталь" (по умолч., зеркало влево-вправо),
                   "вертикаль" (вверх-вниз),
                   "обе" (сначала гориз., потом верт.)
                   алиасы: axis, направление
      сохранить  — путь для сохранения; по умолч. MY_DIR/mirror_out/
      в_контекст — положить в контекст (по умолч. true)

    Примеры:
      {"зеркало": {"путь": "C:/rpg/char.png"}}
      {"зеркало": {"путь": "C:/rpg/char.png", "ось": "вертикаль"}}
      {"зеркало": {"путь": "C:/rpg/char.png", "ось": "обе",
                   "сохранить": "C:/rpg/char_flip.png"}}
    """
    path_arg = args.get("путь") or args.get("файл")
    if not path_arg:
        return 'Укажи путь: {"зеркало": {"путь": "C:/pic.png"}}'
    try:
        src_p, img = _load(path_arg)
    except Exception as e:
        return "❌ " + str(e)

    raw = (args.get("ось") or args.get("axis")
           or args.get("направление") or "горизонталь").strip().lower()
    _map = {
        "горизонталь": "h", "гориз": "h", "h": "h",
        "horizontal": "h", "left-right": "h", "lr": "h",
        "вертикаль": "v", "верт": "v", "v": "v",
        "vertical": "v", "up-down": "v", "ud": "v",
        "обе": "both", "both": "both", "всё": "both",
    }
    axis = _map.get(raw)
    if not axis:
        return ("❌ Неизвестная ось: " + raw
                + ". Доступно: горизонталь, вертикаль, обе")

    from PIL import Image
    if axis == "h":
        out = img.transpose(Image.FLIP_LEFT_RIGHT)
    elif axis == "v":
        out = img.transpose(Image.FLIP_TOP_BOTTOM)
    else:
        out = img.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)

    out_path = None
    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser()
    else:
        try:
            from _config import MY_DIR
            out_dir = MY_DIR / "mirror_out"
            out_dir.mkdir(parents=True, exist_ok=True)
            import time as _t
            _tag = {"h": "h", "v": "v", "both": "hv"}[axis]
            out_path = out_dir / (src_p.stem + "_mirror_" + _tag
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

    _ru = {"h": "горизонталь", "v": "вертикаль", "both": "обе"}[axis]
    info = ("Зеркало (" + _ru + "): "
            + str(out.size[0]) + "x" + str(out.size[1]))
    if out_path:
        info += " -> " + str(out_path)
    return prefix + info
