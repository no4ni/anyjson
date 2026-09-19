"""Кроп региона картинки → буфер."""
from pathlib import Path

from _actions import register


@register(["увеличить_часть_изображения", "вырезать_часть", "кроп", "обрезка"])
def zoom_crop(args: dict) -> str:
    """Вырезать регион из картинки и положить в буфер.

    Источник: параметр «путь» (файл) или буфер обмена, если «путь» не указан.

    Параметры:
      x, y  — левый верхний угол региона (пиксели, по умолчанию 0)
      w, h  — ширина и высота региона (обязательные)
      путь  — файл-источник (опц., иначе буфер обмена)

    Примеры:
      {"кроп": {"x": 100, "y": 200, "w": 300, "h": 200}}
      {"кроп": {"путь": "C:/pic.png", "x": 0, "y": 0, "w": 100, "h": 100}}
    """
    from PIL import Image, ImageGrab

    try:
        x = int(args.get("x", 0))
        y = int(args.get("y", 0))
        w = int(args["w"])
        h = int(args["h"])
    except (KeyError, ValueError, TypeError) as e:
        return f"❌ Нужны w, h (обязательно) и x, y (опционально): {e}"

    if w <= 0 or h <= 0:
        return f"❌ w и h должны быть > 0 (получено w={w}, h={h})"

    src_arg = args.get("путь") or args.get("файл") or args.get("path")

    if src_arg:
        src_path = Path(src_arg).expanduser()
        if not src_path.exists():
            return f"❌ Файл не найден: {src_path}"
        try:
            img = Image.open(str(src_path))
        except Exception as e:
            return f"❌ Не удалось открыть картинку: {e}"
    else:
        try:
            img = ImageGrab.grabclipboard()
        except Exception as e:
            return f"❌ Не удалось прочитать буфер: {e}"
        if img is None:
            return "❌ В буфере нет картинки (или формат не поддерживается)"
        if isinstance(img, list):
            return "❌ В буфере список файлов, а не картинка. Укажи «путь»."

    iw, ih = img.size

    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x >= iw or y >= ih:
        return f"❌ Точка ({x},{y}) за пределами картинки {iw}×{ih}"
    if x + w > iw:
        w = iw - x
    if y + h > ih:
        h = ih - y
    if w <= 0 or h <= 0:
        return f"❌ Регион пустой (картинка {iw}×{ih}, точка ({x},{y}))"

    crop = img.crop((x, y, x + w, y + h))

    try:
        from _actions.clipboard import _pil_to_clipboard
        _pil_to_clipboard(crop)
    except Exception as e:
        return f"❌ Не удалось положить в буфер: {e}"

    return (f"[BUF] ✅ кроп {w}×{h} из ({x},{y})"
            f" исходник {iw}×{ih} → буфер")

