"""Скриншоты и изображения в буфер обмена."""
import io
import os
from pathlib import Path

from _actions import register


def _pil_to_clipboard(img):
    """Кладёт PIL.Image в буфер обмена Windows (формат CF_DIB)."""
    import win32clipboard
    from PIL import Image

    # BMP-заголовок: убираем 14 байт BITMAPFILEHEADER, остальное = DIB
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()


@register("скриншот")
def screenshot(args):
    """Добавляет в контекст запрошенный скриншот.

    Параметры:
      что — что запросить, например "рабочий стол"

    Примеры:
      {"скриншот": {"что": "рабочий стол"}}
      {"скриншот": "экран браузера"}
    """
    if isinstance(args, str):
        what = args.strip() or "экран"
    elif isinstance(args, dict):
        what = (args.get("что") or args.get("запрос")
                or args.get("содержимое") or "экран")
    else:
        what = "экран"
    msg = f"📸 АГЕНТ ЗАПРОСИЛ СКРИНШОТ: {what}"
    print(msg)
    try:
        import pyperclip
        pyperclip.copy(msg)
        print("📋 Запрос скопирован в буфер обмена.")
    except Exception:
        pass
    return msg


# (снято с регистрации: алиас прочитать_полностью)
def image_to_clipboard(args):
    """Показать картинку в контексте.

    Параметры:
      путь — путь к изображению (обязательный)

    Пример: {"показать_картинку": {"путь": "C:/pic.png"}}
    """
    from PIL import Image

    path = args.get("путь") or ""
    if not path:
        return 'Укажи путь: {"показать_картинку": {"путь": "C:/pic.png"}}'
    p = Path(path)
    if not p.is_file():
        return f"❌ Файл не найден: {p}"

    try:
        img = Image.open(str(p))
        img.load()
    except Exception as e:
        return f"❌ Не открыть как изображение: {e}"

    try:
        _pil_to_clipboard(img)
    except Exception as e:
        return f"❌ В буфер не лёг: {e}"

    return f"[BUF] 📋 {p.name} ({img.size[0]}×{img.size[1]}) в буфере обмена."
