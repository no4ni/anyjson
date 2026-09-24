"""Удаление фона: rembg + bbox-обрезка."""
from pathlib import Path

from _actions import register

_MODELS = ("u2net", "u2netp", "u2net_human_seg",
           "isnet-general-use", "silueta")

_sessions = {}


def _get_session(name):
    if name in _sessions:
        return _sessions[name]
    from rembg import new_session
    session = new_session(name)
    _sessions[name] = session
    return session


def _load_image(path):
    from PIL import Image
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("Не найден файл: " + str(p))
    return p, Image.open(str(p)).convert("RGBA")


def _trim_alpha(img, padding=0):
    from PIL import Image
    bbox = img.split()[-1].getbbox()
    if not bbox:
        return img
    x0, y0, x1, y1 = bbox
    W, H = img.size
    x0 = max(0, x0 - padding)
    y0 = max(0, y0 - padding)
    x1 = min(W, x1 + padding)
    y1 = min(H, y1 + padding)
    return img.crop((x0, y0, x1, y1))


@register(["убрать_фон", "удалить_фон", "вырезать_фон", "remove_bg"])
def remove_background(args):
    """Убрать фон у картинки: получить PNG с прозрачностью.

    Параметры:
      путь       — входной файл (обязательно)
      модель     — u2net (по умолч., общая),
                   u2netp (быстрая),
                   u2net_human_seg (для людей),
                   isnet-general-use, silueta
      обрезать   — true: обрезать по границам объекта (по умолч. true)
      отступ     — пиксели вокруг объекта (по умолч. 0)
      сохранить  — путь к PNG (иначе MY_DIR/rembg_out/)
      в_контекст — положить в контекст (по умолч. true)

    Примеры:
      {"убрать_фон": {"путь": "C:/rpg/char.png"}}
      {"убрать_фон": {"путь": "C:/rpg/char.png",
                      "модель": "u2net_human_seg",
                      "обрезать": true, "отступ": 8}}
    """
    path_arg = args.get("путь") or args.get("файл")
    if not path_arg:
        return 'Укажи путь: {"убрать_фон": {"путь": "C:/pic.png"}}'
    try:
        src_path, img = _load_image(path_arg)
    except Exception as e:
        return "❌ " + str(e)

    model = (args.get("модель") or args.get("model") or "u2net").strip()
    if model not in _MODELS:
        return ("❌ Модель '" + model + "' не поддерживается. "
                "Доступно: " + ", ".join(_MODELS))

    try:
        from rembg import remove
        session = _get_session(model)
        out = remove(img, session=session, alpha_matting=False)
    except Exception as e:
        return "❌ rembg: " + str(e)

    if not hasattr(out, "split"):
        from PIL import Image
        out = Image.open(out).convert("RGBA")

    trim = args.get("обрезать", True)
    if isinstance(trim, str):
        trim = trim.strip().lower() not in ("0", "false", "нет", "off", "")
    if trim:
        try:
            pad = int(args.get("отступ") or 0)
        except (TypeError, ValueError):
            pad = 0
        out = _trim_alpha(out, padding=pad)

    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser()
    else:
        from _config import MY_DIR
        out_dir = MY_DIR / "rembg_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        import time as _t
        out_path = out_dir / (src_path.stem + "_no_bg_" +
                              _t.strftime("%H%M%S") + ".png")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    out.save(str(out_path))

    to_buf = args.get("в_контекст")
    if to_buf is None:
        to_buf = args.get("в_буфер", True)
    if isinstance(to_buf, str):
        to_buf = to_buf.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_buf:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(out)
            prefix = "[BUF] "
        except Exception:
            pass

    return (prefix + "Фон убран: " + str(out.size[0]) + "x" + str(out.size[1])
            + " -> " + str(out_path))
