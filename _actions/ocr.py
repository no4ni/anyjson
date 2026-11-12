"""Распознавание текста с изображений и PDF (Tesseract OCR)."""
import os

from _actions import register

try:
    import pytesseract
    from PIL import Image
except Exception:
    pytesseract = None
    Image = None


def _do_ocr_image(path, lang="rus+eng"):
    """OCR одной картинки. Возвращает (текст, ошибка)."""
    if pytesseract is None or Image is None:
        return "", "Нужны Pillow и pytesseract."
    try:
        img = Image.open(path)
    except Exception as e:
        return "", f"Не открыть изображение: {e}"
    try:
        text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception as e:
        return "", f"OCR ошибка: {e}"
    return text, ""


def _do_ocr_pdf(path, lang="rus+eng", dpi=300):
    """OCR PDF постранично. Возвращает (текст, ошибка)."""
    if pytesseract is None:
        return "", "Нужен pytesseract."
    try:
        from pdf2image import convert_from_path
    except Exception:
        return "", "Нужен pdf2image (pip install pdf2image)."
    try:
        pages = convert_from_path(path, dpi=dpi)
    except Exception as e:
        return "", f"Не открыть PDF: {e}"
    parts = []
    for i, page in enumerate(pages, 1):
        try:
            t = pytesseract.image_to_string(page, lang=lang, config="--psm 3").strip()
        except Exception as e:
            t = f"[Ошибка на странице {i}: {e}]"
        parts.append(t)
    return "\n\n".join(parts), ""


@register(["распознать_текст", "ocr", "распознать_бинарный"])
def ocr_cmd(args):
    """Распознать текст с картинки или PDF (Tesseract OCR).

    Параметры:
      путь      — файл (картинка или PDF) — обязательный
      язык      — язык Tesseract, по умолчанию "rus+eng"
      сохранить — путь к .txt, куда записать распознанный текст
      показывать — сколько первых символов вернуть (по умолчанию 2000)
      dpi       — DPI для PDF (по умолчанию 300)

    Примеры:
      {"распознать_текст": {"путь": "C:/scan.png"}}
      {"распознать_текст": {"путь": "C:/book.pdf", "язык": "rus+eng",
                           "сохранить": "C:/book.txt"}}
    """
    if isinstance(args, str):
        args = {"путь": args}
    path = (args.get("путь") or args.get("файл") or "").strip()
    if not path:
        return "❌ Укажи 'путь' — картинку или PDF."
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"

    lang = args.get("язык") or "rus+eng"
    save_to = (args.get("сохранить") or "").strip()
    show_n = int(args.get("показывать") or 2000)
    dpi = int(args.get("dpi") or 300)

    low = path.lower()
    if low.endswith(".pdf"):
        text, err = _do_ocr_pdf(path, lang=lang, dpi=dpi)
    else:
        text, err = _do_ocr_image(path, lang=lang)

    if err:
        return f"❌ {err}"

    if not text:
        return "⚠️ Текст не найден (возможно, пустая страница или низкое качество)."

    n_chars = len(text)
    out = [f"✅ Распознано: {path} ({n_chars} символов)"]

    if save_to:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(save_to)), exist_ok=True)
            with open(save_to, "w", encoding="utf-8") as f:
                f.write(text)
            out.append(f"💾 Сохранено: {save_to}")
        except Exception as e:
            out.append(f"❌ Не удалось сохранить: {e}")

    out.append("")
    out.append(text[:show_n])
    if n_chars > show_n:
        out.append(f"\n... (показано {show_n} из {n_chars})")

    return "\n".join(out)