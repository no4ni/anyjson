"""Кроп региона картинки -> буфер или файл."""
from pathlib import Path

from _actions import register


def _crop_region(args):
    """Общий код: открыть источник, вырезать регион.
    Возвращает (img, err_str). img=None если ошибка.
    """
    from PIL import Image, ImageGrab

    try:
        x = int(args.get("левый_край", 0) or 0)
        y = int(args.get("верхний_край", 0) or 0)
        w = int(args.get("ширина") or 0)
        h = int(args.get("высота") or 0)
    except (KeyError, ValueError, TypeError) as e:
        return None, ("❌ Нужны ширина, высота (обязательно) и "
                      "левый_край, верхний_край (опционально): " + str(e))

    if w <= 0 or h <= 0:
        return None, ("❌ ширина и высота должны быть > 0 "
                      "(получено ширина=" + str(w) + ", высота=" + str(h) + ")")

    src_arg = args.get("путь")

    if src_arg:
        src_path = Path(src_arg).expanduser()
        if not src_path.exists():
            return None, "❌ Файл не найден: " + str(src_path)
        try:
            img = Image.open(str(src_path))
        except Exception as e:
            return None, "❌ Не удалось открыть картинку: " + str(e)
    else:
        try:
            img = ImageGrab.grabclipboard()
        except Exception as e:
            return None, "❌ Не удалось прочитать буфер: " + str(e)
        if img is None:
            return None, "❌ В буфере нет картинки (или формат не поддерживается)"
        if isinstance(img, list):
            return None, "❌ В буфере список файлов, а не картинка. Укажи «путь»."

    iw, ih = img.size

    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x >= iw or y >= ih:
        return None, ("❌ Точка (" + str(x) + "," + str(y)
                      + ") за пределами картинки " + str(iw) + "x" + str(ih))
    if x + w > iw:
        w = iw - x
    if y + h > ih:
        h = ih - y
    if w <= 0 or h <= 0:
        return None, ("❌ Регион пустой (картинка " + str(iw) + "x" + str(ih)
                      + ", точка (" + str(x) + "," + str(y) + "))")

    crop = img.crop((x, y, x + w, y + h))
    return (crop, (x, y, w, h, iw, ih)), None


@register(["рассмотреть_поближе", "увеличить_часть_изображения"])
def zoom_crop(args: dict) -> str:
    """Вырезать регион из картинки и показать его в контексте.

    Источник: параметр «путь» (файл).

    Параметры:
      путь          — файл-источник (обязательный)
      левый_край, верхний_край — угол региона (пиксели, по умолч. 0)
      ширина, высота — размер региона (обязательные)
      сохранить     — опц. путь для файла (формат по расширению: .png/.jpg/.webp/.bmp)
      в_контекст    — положить в контекст (по умолч. true)

    Пример:
      {"рассмотреть_поближе": {"путь": "C:/pic.png",
                               "левый_край": 0, "верхний_край": 0,
                               "ширина": 200, "высота": 200}}
    """
    result, err = _crop_region(args)
    if err:
        return err
    crop, meta = result
    x, y, w, h, iw, ih = meta

    out_path = None
    _alpha_warn = ""
    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _ext = out_path.suffix.lower()
        _no_alpha = (".jpg", ".jpeg", ".bmp")
        if _ext in _no_alpha and crop.mode in ("RGBA", "LA", "PA"):
            # конвертируем на белый фон — предсказуемо, не чёрный
            from PIL import Image as _Img2
            bg = _Img2.new("RGB", crop.size, (255, 255, 255))
            if crop.mode != "RGBA":
                crop = crop.convert("RGBA")
            bg.paste(crop, mask=crop.split()[-1])
            _save_img = bg
            _alpha_warn = " (альфа потеряна — фон белый)"
        else:
            _save_img = crop
        try:
            _save_img.save(str(out_path))
        except Exception as e:
            return "❌ Не удалось сохранить: " + str(e)

    to_ctx = args.get("в_контекст")
    if to_ctx is None:
        to_ctx = args.get("в_буфер", True)
    if isinstance(to_ctx, str):
        to_ctx = to_ctx.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_ctx:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(crop)
            prefix = "[BUF] "
        except Exception as e:
            return ("❌ Не удалось положить в контекст: " + str(e)
                    + ((" (сохранено в " + str(out_path) + ")") if out_path else ""))

    info = ("кроп " + str(w) + "x" + str(h)
            + " из (" + str(x) + "," + str(y) + ")"
            + " исходник " + str(iw) + "x" + str(ih))
    if out_path:
        info += " -> " + str(out_path)
    if _alpha_warn:
        info += _alpha_warn
    return prefix + info


@register(["кроп", "вырезать_часть", "обрезка", "вырезать"])
def crop_to_file(args: dict) -> str:
    """Вырезать регион из картинки и сохранить в файл.

    Источник: параметр «путь» (файл).

    Параметры:
      путь          — файл-источник (обязательный)
      левый_край, верхний_край — угол региона (пиксели, по умолч. 0)
      ширина, высота — размер региона (обязательные)
      сохранить     — путь для файла (обязательный). Формат — по расширению (.png/.jpg/.webp/.bmp). При .jpg/.bmp и наличии альфы прозрачность заменится белым фоном.
      в_контекст    — положить в контекст (по умолч. true)

    Пример:
      {"кроп": {"путь": "C:/pic.png",
                "левый_край": 100, "верхний_край": 200,
                "ширина": 300, "высота": 200,
                "сохранить": "C:/rpg/crop_01.png"}}
    """
    if not (args.get("сохранить") or args.get("куда")):
        return ('Укажи "сохранить": {"кроп": {"путь": "C:/pic.png", '
                '"ширина": 100, "высота": 100, '
                '"сохранить": "C:/rpg/crop_01.png"}}')
    save_arg = args.get("сохранить") or args.get("куда")
    args = dict(args)
    args["сохранить"] = save_arg
    if "в_контекст" not in args and "в_буфер" not in args:
        args["в_контекст"] = True
    return zoom_crop(args)
