"""Альфа-канал PNG → маска (градации серого).

Белое = непрозрачно (alpha=255), чёрное = прозрачно (alpha=0),
серое = промежуточная прозрачность.

Параметры:
  путь          — входной .png (обязательно)
  сохранить     — путь к выходному .png. По умолчанию рядом с исходным:
                  <stem>_mask.png
  порог         — 0..255. Если задан, alpha >= порог → 255, иначе 0.
                  Без порога — полупрозрачность сохраняется как серый.
  инвертировать — true: 255 - alpha (применяется ДО порога)
  обводка       — 0..N. Каждый чисто белый пиксель результата
                  «раздувается» квадратом со стороной 2N+1.
                  Серые пиксели не трогаются.
  размытие      — N.N. Gaussian blur краёв маски (feathered-маска).
                  Применяется ПОСЛЕ обводки. Дробные значения —
                  мягче переход.

Примеры:
  {"альфа_в_маску": {"путь": "in.png", "сохранить": "mask.png"}}
  {"альфа_в_маску": {"путь": "in.png", "сохранить": "mask.png",
                      "порог": 128}}
  {"альфа_в_маску": {"путь": "in.png", "сохранить": "mask.png",
                      "инвертировать": true}}
"""
from pathlib import Path

from _actions import register


@register(["альфа_в_маску", "маска_из_альфы", "альфа_маска"])
def alpha_to_mask(args):
    """Превратить альфа-канал PNG в маску (градации серого).

    Белое = непрозрачно, чёрное = прозрачно, серое = полупрозрачность.

    Параметры:
      путь          — входной .png (обязательно)
      сохранить     — путь к выходному .png. По умолчанию:
                      рядом с исходным, <stem>_mask.png
      порог         — 0..255. Если задан, alpha >= порог → 255,
                      иначе 0. Без порога — полупрозрачность
                      сохраняется как серый.
      инвертировать — true: 255 - alpha (применяется ДО порога)
      обводка       — 0..N. Каждый чисто белый пиксель результата
                      «раздувается» квадратом со стороной 2N+1.
                      Серые пиксели не трогаются.
      размытие      — N.N. Gaussian blur краёв маски (feathered-маска).
                      Применяется ПОСЛЕ обводки. Дробные значения —
                      мягче переход.

    Примеры:
      {"альфа_в_маску": {"путь": "in.png", "сохранить": "mask.png"}}
      {"альфа_в_маску": {"путь": "in.png", "сохранить": "mask.png",
                          "порог": 128}}
      {"альфа_в_маску": {"путь": "in.png", "сохранить": "mask.png",
                          "инвертировать": true}}
    """
    try:
        from PIL import Image
    except ImportError:
        return ('❌ PIL не установлен. Поставь:\n'
                '   {"установить_пакет": {"имя": "Pillow"}}')

    src = args.get("путь") or args.get("файл") or args.get("вход")
    if not src:
        return '❌ альфа_в_маску: не указан "путь"'
    p_src = Path(str(src))
    if not p_src.exists():
        return f"❌ Файл не найден: {p_src}"
    if not p_src.is_file():
        return f"❌ Это не файл: {p_src}"

    dst = args.get("сохранить") or args.get("выход")
    if not dst:
        dst = p_src.with_name(p_src.stem + "_mask.png")
    p_dst = Path(str(dst))

    _inv = bool(args.get("инвертировать", False))

    _thr_raw = args.get("порог")
    _thr = None
    if _thr_raw is not None and str(_thr_raw).strip() != "":
        try:
            _thr = int(float(_thr_raw))
        except (TypeError, ValueError):
            return f"❌ порог: ожидалось число 0..255, пришло {_thr_raw!r}"
        _thr = max(0, min(_thr, 255))

    _obv_raw = args.get("обводка") or args.get("толщина")
    _obv = 0
    if _obv_raw is not None and str(_obv_raw).strip() != "":
        try:
            _obv = int(float(_obv_raw))
        except (TypeError, ValueError):
            return f"❌ обводка: ожидалось число, пришло {_obv_raw!r}"
        _obv = max(0, _obv)

    _blur_raw = (args.get("размытие") or args.get("растушёвка")
                 or args.get("feather"))
    _blur = 0.0
    if _blur_raw is not None and str(_blur_raw).strip() != "":
        try:
            _blur = float(_blur_raw)
        except (TypeError, ValueError):
            return f"❌ размытие: ожидалось число, пришло {_blur_raw!r}"
        if _blur < 0:
            _blur = 0.0

    try:
        im = Image.open(p_src)
    except Exception as e:
        return f"❌ Не удалось открыть {p_src}: {e}"

    try:
        if "A" in im.getbands():
            alpha = im.getchannel("A")
        else:
            return (f"❌ У {p_src.name} (режим {im.mode}) нет альфа-канала.\n"
                    f"   Маску можно сделать только из PNG с прозрачностью "
                    f"(RGBA/LA/PA).")
    except Exception as e:
        return f"❌ Не удалось получить альфа-канал: {e}"

    if _inv:
        alpha = alpha.point(lambda v: 255 - v)

    if _thr is not None:
        alpha = alpha.point(lambda v: 255 if v >= _thr else 0)

    mask = alpha.convert("L")

    if _obv > 0:
        # Квадратное расширение только чистых белых пикселей.
        # MaxFilter (C-реализация) быстрее numpy-цикла в разы;
        # размер окна должен быть нечётным.
        from PIL import ImageFilter as _IF
        from PIL import Image as _Im
        import numpy as _np
        _size = 2 * _obv + 1
        _bin = mask.point(lambda v: 255 if v == 255 else 0)
        _dil = _bin.filter(_IF.MaxFilter(_size))
        _arr = _np.array(mask, dtype=_np.uint8)
        _dil_arr = _np.array(_dil, dtype=_np.uint8)
        _arr = _np.maximum(_arr, _dil_arr)
        mask = _Im.fromarray(_arr)

    if _blur > 0:
        # Feathered-маска: мягкие края. GaussianBlur — C-реализация,
        # принимает float radius. Применяется после порога/обводки,
        # к финальной маске.
        from PIL import ImageFilter as _IF2
        mask = mask.filter(_IF2.GaussianBlur(_blur))

    p_dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        mask.save(str(p_dst), format="PNG")
    except Exception as e:
        return f"❌ Не удалось сохранить {p_dst}: {e}"

    hist = mask.histogram()
    total = sum(hist) or 1
    pct_white = 100.0 * hist[255] / total
    pct_black = 100.0 * hist[0] / total
    pct_mid = 100.0 - pct_white - pct_black

    out = [f"✅ Маска: {p_dst}",
           f"   {mask.size[0]}x{mask.size[1]} — "
           f"белых {pct_white:.1f}%, чёрных {pct_black:.1f}%, "
           f"промежуточных {pct_mid:.1f}%"]
    if _thr is not None:
        out.append(f"   порог: {_thr}")
    if _inv:
        out.append("   инвертировано")
    if _obv > 0:
        out.append(f"   обводка: +{_obv}px (квадрат {2 * _obv + 1}x{2 * _obv + 1})")
    if _blur > 0:
        out.append(f"   размытие краёв: {_blur:g}px")
    return "\n".join(out)