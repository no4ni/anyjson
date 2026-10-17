"""inline_assets.py — подстановка внешних картинок в Lottie как base64.

В .lotty ассеты могут ссылаться на файлы:
    {"id": "tex", "w": 64, "h": 64, "u": "textures/", "p": "tree.png", "e": 0}

Перед подачей в ThorVG рендерер вызывает inline_assets(doc, base_dir):
читает файлы по u+p и заменяет "e": 0 на "e": 1 + base64 data-URL.
"""
import base64, pathlib


def _mime_of(path):
    ext = pathlib.Path(path).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(ext, "image/png")


def inline_assets(doc, base_dir):
    """Возвращает копию doc, где все внешние ассеты заменены на base64.

    base_dir — папка, откуда загружается .lotty (для относительных путей).
    Ассеты с "e": 1 (уже embedded) не трогаются.
    """
    import copy
    d = copy.deepcopy(doc)
    base_dir = pathlib.Path(base_dir)
    for a in d.get("assets", []):
        if a.get("e", 0) == 1:
            continue
        u = a.get("u", "") or ""
        p = a.get("p", "")
        if not p:
            continue
        # абсолютный или относительный
        if p.startswith("data:"):
            continue
        full = pathlib.Path(p)
        if not full.is_absolute():
            full = base_dir / u / p if u else base_dir / p
        if not full.exists():
            # попробуем base_dir / p (без u)
            alt = base_dir / p
            if alt.exists():
                full = alt
            else:
                print(f"  ⚠ ассет не найден: {full}")
                continue
        data = full.read_bytes()
        b64 = base64.b64encode(data).decode()
        a["p"] = f"data:{_mime_of(str(full))};base64,{b64}"
        a["u"] = ""
        a["e"] = 1
    return d
