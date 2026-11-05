"""Поиск картинок через DuckDuckGo (пакет ddgs)."""
import os
import time
from pathlib import Path

from _actions import register
from _config import MY_DIR
import _core._web as _web


_DDG_SIZE = {"small", "medium", "large", "wallpaper"}
_DDG_TYPE = {"photo", "clipart", "gif", "transparent", "line"}
_DDG_COLOR = {"color", "monochrome", "red", "orange", "yellow", "green",
              "blue", "purple", "pink", "brown", "black", "gray", "white"}
_DDG_LAYOUT = {"square", "tall", "wide"}
_DDG_LICENSE = {"any", "public", "share", "modify", "sharecommercially",
                "modifycommercially"}


def _norm(v, allowed, default=None):
    if not v:
        return default
    s = str(v).strip()
    return s if s.lower() in allowed else default


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "y", "t",
                                     "да")
    return bool(v)


def _ext_from_url(url):
    from urllib.parse import urlparse
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
               ".tiff", ".tif", ".svg", ".ico", ".avif", ".heic"):
        return ext
    return ".jpg"


def _download(url, dest, timeout=30):
    try:
        headers = _web.DEFAULT_HEADERS
    except Exception:
        headers = {}
    try:
        import requests
        r = requests.get(url, headers=headers, timeout=timeout,
                         stream=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)
        return True, None
    except Exception as e:
        return False, str(e)


@register(["найти_картинку", "поиск_картинок", "поиск_изображений"])
def image_search(args: dict) -> str:
    """Поиск картинок в интернете (DuckDuckGo).

    Параметры:
      запрос       — строка (обязательно), алиас: query
      количество   — сколько результатов (по умолч. 10, макс. 50), алиас: лимит
      сохранить_как — путь к файлу (алиас: файл). Скачивает первую
                     удачную картинку (403/битые — пропускает),
                     кладёт её в контекст и возвращает путь.
                     В пакете (>1 команда: ||| / параллельно /
                     actions) — только сохраняет файл, в контекст
                     не кладёт.
      перезаписать — true: перезаписать существующий файл
      таймаут      — сек на скачивание одной картинки (по умолч. 30)

    Примеры:
      {"найти_картинку": {"запрос": "cat in hat"}}
      {"найти_картинку": {"запрос": "castle", "количество": 5}}
      {"найти_картинку": {"запрос": "anime girl", "сохранить_как": "E:/pics/anime.jpg"}}
    """
    query = (args.get("запрос") or args.get("query") or "").strip()
    if not query:
        return "Ошибка: не указан запрос"
    print(f"[image_search] Ищу: {query} (n={args.get('количество') or 10})", flush=True)
    try:
        n = int(args.get("количество") or args.get("лимит") or 10)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(50, n))

    size = _norm(args.get("размер"), _DDG_SIZE, None)
    type_image = _norm(args.get("тип"), _DDG_TYPE, None)
    color = _norm(args.get("цвет"), _DDG_COLOR, None)
    layout = _norm(args.get("раскладка"), _DDG_LAYOUT, None)
    lic = _norm(args.get("лицензия"), _DDG_LICENSE, None)

    if _as_bool(args.get("отладка"), False):
        return _web.debug_images(query, 3)

    results, err = _web.fetch_images_ddgs(query, n, size, type_image,
                                          color, layout, lic)
    if err:
        return "Поиск картинок временно недоступен (занято, попробуйте позже)."
    if not results:
        return "0 результатов."

    out = []
    download = _as_bool(args.get("скачать"), False)

    # --- сохранить_как: первая картинка -> файл -> буфер (CF_DIB) ---
    save_as_arg = (args.get("сохранить_как") or args.get("файл") or "").strip()
    if save_as_arg:
        try:
            dest = Path(save_as_arg)
        except Exception as e:
            return "Ошибка: некорректный путь сохранения: " + str(e)
        if dest.is_dir():
            return ("Ошибка: сохранить_как указывает на папку: " + str(dest)
                    + " (для папки используй сохранить)")
        if dest.suffix == "":
            return ("Ошибка: сохранить_как без расширения: " + str(dest)
                    + " (укажи .jpg / .png / ...)")
        overwrite_sa = _as_bool(args.get("перезаписать"), False)
        if dest.is_file() and not overwrite_sa:
            return ("Ошибка: файл уже существует: " + str(dest)
                    + " (перезаписать: true)")
        try:
            timeout_sa = int(args.get("таймаут") or 30)
        except (TypeError, ValueError):
            timeout_sa = 30
        timeout_sa = max(5, min(120, timeout_sa))
        # Пробуем результаты по очереди: 403/битый файл/ошибка
        # скачивания — переходим к следующему.
        _attempts = []
        _chosen = None
        _img = None
        for _i, _r in enumerate(results, 1):
            _img_url = _r.get("image") or ""
            if not _img_url:
                continue
            _ok, _dl_err = _download(_img_url, dest, timeout=timeout_sa)
            if not _ok:
                _attempts.append("  " + str(_i) + ". " + str(_dl_err)[:120])
                continue
            try:
                from PIL import Image as _PIL
                with _PIL.open(str(dest)) as _im:
                    _img = _im.copy()
            except Exception as _oe:
                _attempts.append("  " + str(_i) + ". open: "
                                 + type(_oe).__name__ + ": "
                                 + str(_oe)[:120])
                _img = None
                continue
            _chosen = _r
            break
        if _chosen is None or _img is None:
            _tried = "\n".join(_attempts) if _attempts else "  (нет URL)"
            try:
                dest.unlink()
            except Exception:
                pass
            return ("Ошибка: ни одну картинку не удалось скачать "
                    "(" + str(len(results)) + " шт., куда: "
                    + str(dest) + ").\nПопытки:\n" + _tried)
        _title = _chosen["title"] or "(без названия)"
        _note = ""
        if _attempts:
            _note = ("\n   ⚠️ пропущено до успеха: "
                     + str(len(_attempts)))
        # в_контекст:false — не трогать буфер (авто-флаг в пакете).
        _no_clip = False
        for _k in ("в_контекст", "в_буфер"):
            if _k in args:
                _v = args[_k]
                if isinstance(_v, str):
                    _no_clip = _v.strip().lower() in (
                        "0", "false", "нет", "off", "no", "")
                else:
                    _no_clip = _v is False
                break
        if _no_clip:
            return ("📁 Скачано: " + str(dest)
                    + "\n   «" + _title + "» ("
                    + str(_img.size[0]) + "×" + str(_img.size[1]) + ")"
                    + _note)
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(_img)
        except Exception as e:
            return ("Ошибка: файл скачан, но в буфер не лёг: " + str(e)
                    + "\n📁 " + str(dest))
        return ("[BUF] 📋 " + dest.name
                + " (" + str(_img.size[0]) + "×" + str(_img.size[1]) + ")"
                + " — картинка в буфере обмена."
                + "\n   «" + _title + "»"
                + "\n   📁 " + str(dest) + _note)

    if download:
        try:
            timeout = int(args.get("таймаут") or 30)
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(5, min(120, timeout))
        save_arg = (args.get("сохранить") or args.get("куда")
                    or args.get("выход"))
        if save_arg:
            try:
                save_dir = Path(save_arg)
            except Exception as e:
                return "Ошибка: некорректный путь: " + str(e)
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            save_dir = Path(MY_DIR) / "image_search" / ts
        save_dir.mkdir(parents=True, exist_ok=True)
        overwrite = _as_bool(args.get("перезаписать"), False)
        ok_count = 0
        for i, r in enumerate(results, 1):
            ext = _ext_from_url(r["image"])
            dest = save_dir / ("{:03d}{}".format(i, ext))
            if dest.exists() and not overwrite:
                dest = save_dir / ("{:03d}_{}{}".format(
                    i, int(time.time()), ext))
            ok, dl_err = _download(r["image"], dest, timeout=timeout)
            title = r["title"] or "(без названия)"
            if ok:
                ok_count += 1
                out.append("• " + title)
                if r.get("width") and r.get("height"):
                    out.append("   📐 {}x{}".format(r["width"], r["height"]))
                out.append("   📁 " + str(dest))
                if r.get("url"):
                    out.append("   🔗 " + r["url"])
            else:
                _short_err = str(dl_err).split(":")[0] if ":" in str(dl_err) else str(dl_err)[:50]
                out.append("• " + title + " — ❌ " + _short_err)
        header = "🖼 Найдено {}, скачано {} → {}".format(
            len(results), ok_count, save_dir)
        return header + "\n" + "\n".join(out)

    for i, r in enumerate(results, 1):
        _title = r["title"] or "(без названия)"
        if r.get("width") and r.get("height"):
            _title += " — {}x{}".format(r["width"], r["height"])
        if i == 1:
            out.append("1. " + _title)
            out.append("   🖼 " + r["image"])
            if r.get("thumbnail") and r["thumbnail"] != r["image"]:
                out.append("   📷 thumb: " + r["thumbnail"])
            if r.get("url"):
                out.append("   🔗 " + r["url"])
            if r.get("source"):
                out.append("   📍 " + r["source"])
        else:
            out.append("   " + _title)
            out.append("   🖼 " + r["image"])
    return "🖼 Найдено {} картинок:\n".format(len(results)) + "\n".join(out)