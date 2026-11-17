"""Окно в мир: кадр с веб-камеры SkylineWebcams → буфер."""
import io
import re
import urllib.request

from PIL import Image

from _actions import register
from _web import DEFAULT_HEADERS


_BASE = "https://www.skylinewebcams.com"

# кэш HTML страниц стран на сессию
_page_cache = {}


_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36")

_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Accept-Encoding": "identity",
}


def _fetch_html(url, timeout=30, retries=3):
    """GET с повторами при обрыве соединения (Skyline иногда рвёт)."""
    import time as _t
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                _t.sleep(1.5 * (attempt + 1))
                continue
            raise last_err


def _extract_cameras(html):
    """Возвращает список (title, img_url, page_url) со страницы страны.

    Через BeautifulSoup — надёжнее регекса.
    """
    from bs4 import BeautifulSoup
    cams = []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        cam_div = a.find("div", class_="cam-light")
        if not cam_div:
            continue
        img = cam_div.find("img", src=True)
        if not img:
            continue
        src = img.get("src", "")
        if "cdn.skylinewebcams.com/live" not in src:
            continue
        title = img.get("alt", "") or ""
        cams.append((title, src, a["href"]))
    return cams


def _fetch_hd_frame(page_url):
    """HD-кадр через HLS-поток Skyline + ffmpeg."""
    import re as _re
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf
    import os as _os

    full_page = page_url if page_url.startswith("http") else (_BASE + "/" + page_url.lstrip("/"))
    try:
        cam_html = _fetch_html(full_page, timeout=30)
    except Exception as e:
        return None, "HD: ошибка загрузки " + full_page + ": " + str(e)

    m = _re.search(r"source\s*:\s*['\"](livee\.m3u8\?a=[^'\"]+)['\"]", cam_html)
    if not m:
        m2 = _re.search(r"source\s*:\s*['\"](live\.m3u8[^'\"]*)['\"]", cam_html)
        if not m2:
            return None, "HD: не найден source (livee.m3u8) на странице"
        stream = m2.group(1)
    else:
        stream = m.group(1).replace("livee.", "live.")

    stream_url = "https://hd-auth.skylinewebcams.com/" + stream

    ff = _sh.which("ffmpeg")
    if not ff:
        return None, "HD: ffmpeg не найден в PATH"

    tmp_fd, tmp_path = _tf.mkstemp(suffix=".jpg", prefix="skyline_hd_")
    _os.close(tmp_fd)
    try:
        cmd = [
            ff, "-y",
            "-user_agent", _BROWSER_UA,
            "-headers", "Referer: " + full_page + "\r\n",
            "-i", stream_url,
            "-frames:v", "1",
            "-q:v", "2",
            tmp_path,
        ]
        r = _sp.run(cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0 or not _os.path.exists(tmp_path) or _os.path.getsize(tmp_path) == 0:
            return None, ("HD: ffmpeg не отдал кадр (код " + str(r.returncode) +
                          "): " + (r.stderr or "")[-300:])
        img = Image.open(tmp_path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.load()
        return img, None
    except Exception as e:
        return None, "HD: " + str(e)
    finally:
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass


@register(["окно_в_мир", "webcam_region"])
def world_window(args):
    """Кадр с первой камеры в стране/регионе SkylineWebcams → буфер.

    Источник: skylinewebcams.com (HTTPS, работает с прямым IP, без Tor).

    Параметры:
      страна    — код страны в URL skyline (italia, deutschland, france,
                  united-states, russia, ellada, ...) — обязательно
      регион    — если задан, берётся страница региона
                  (например "lazio", "veneto", "toscana")
      индекс    — какая по счёту камера (по умолчанию 0 — первая)
      подстрока — фильтр по названию камеры (регистр игнорируется),
                  например "fountain", "beach", "moscow"
      hd        — true: настоящий HD-кадр (720p) через HLS-поток + ffmpeg
                  (по умолчанию — быстрое превью 344x193)
      сохранить — путь для webp (опционально)

    Примеры:
      {"окно_в_мир": {"страна": "italia"}}
      {"окно_в_мир": {"страна": "italia", "регион": "lazio"}}
      {"окно_в_мир": {"страна": "italia", "подстрока": "venice"}}
      {"окно_в_мир": {"страна": "united-states", "подстрока": "beach"}}
    """
    country = args.get("страна")
    if not country:
        return ('Укажи страну: {"окно_в_мир": {"страна": "italia"}}\n'
                'Список стран: https://www.skylinewebcams.com/en/webcam.html')

    country_raw = str(country).strip().lower()
    _country_map = {
        "россия": "russia", "russia": "russia", "rf": "russia", "русь": "russia",
        "италия": "italia", "italy": "italia", "italia": "italia",
        "германия": "deutschland", "germany": "deutschland", "deutschland": "deutschland",
        "франция": "france", "france": "france",
        "испания": "spain", "spain": "spain", "espana": "spain",
        "греция": "ellada", "greece": "ellada", "ellada": "ellada",
        "сша": "united-states", "usa": "united-states", "united states": "united-states", "united-states": "united-states",
        "турция": "turkey", "turkey": "turkey",
        "великобритания": "united-kingdom", "uk": "united-kingdom", "united kingdom": "united-kingdom",
        "нидерланды": "netherlands", "netherlands": "netherlands", "holland": "netherlands",
        "швейцария": "switzerland", "switzerland": "switzerland",
        "австрия": "austria", "austria": "austria",
        "португалия": "portugal", "portugal": "portugal",
        "хорватия": "croatia", "croatia": "croatia",
        "кипр": "cyprus", "cyprus": "cyprus",
        "япония": "japan", "japan": "japan",
        "китай": "china", "china": "china",
        "оаэ": "united-arab-emirates", "uae": "united-arab-emirates",
    }
    country_clean = country_raw.replace(" ", "").replace("-", "")
    if country_clean in _country_map:
        country = _country_map[country_clean]
    else:
        country = country_raw.replace(" ", "-")
    region = args.get("регион")
    if region:
        region = str(region).strip().lower().replace(" ", "-")
        url = _BASE + "/en/webcam/" + country + "/" + region + ".html"
    else:
        url = _BASE + "/en/webcam/" + country + ".html"

    try:
        idx = int(args.get("индекс", 0) or 0)
    except (ValueError, TypeError):
        idx = 0
    if idx < 0:
        idx = 0

    # кэш HTML
    if url in _page_cache:
        html = _page_cache[url]
    else:
        try:
            html = _fetch_html(url, timeout=int(args.get("таймаут", 30) or 30))
        except Exception as e:
            return "Ошибка загрузки " + url + ": " + str(e)
        _page_cache[url] = html

    cams = _extract_cameras(html)
    if not cams:
        return "На странице " + url + " не найдено камер."

    # фильтр по подстроке в названии (регистр игнорируется)
    sub = args.get("подстрока") or args.get("фильтр")
    if sub:
        sub_low = str(sub).strip().lower()
        cams = [c for c in cams if sub_low in (c[0] or "").lower()]
        if not cams:
            return ("По подстроке '" + str(sub) + "' ничего не найдено "
                    "на странице " + url)

    if idx >= len(cams):
        return ("Найдено камер: " + str(len(cams)) + ", индекс " + str(idx) +
                " вне диапазона.")

    title, img_url, page_url = cams[idx]

    hd = args.get("чётко")
    if isinstance(hd, str):
        hd = hd.strip().lower() not in ("0", "false", "нет", "off", "")

    if hd:
        img, err = _fetch_hd_frame(page_url)
        if err:
            return err
    else:
        img_bytes = None
        _last_err = None
        for _attempt in range(3):
            try:
                req = urllib.request.Request(img_url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    img_bytes = r.read()
                break
            except Exception as _e:
                _last_err = _e
                if _attempt < 2:
                    import time as _t
                    _t.sleep(1.0 * (_attempt + 1))
                    continue
        if img_bytes is None:
            return "Ошибка скачивания кадра " + img_url + ": " + str(_last_err)
        try:
            img = Image.open(io.BytesIO(img_bytes))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
        except Exception as e:
            return "Ошибка разбора картинки " + img_url + ": " + str(e)

    save_arg = args.get("сохранить")
    if save_arg:
        try:
            img.save(save_arg)
        except Exception as e:
            return "Ошибка сохранения: " + str(e)

    to_buf = args.get("в_контекст")
    if to_buf is None:
        to_buf = args.get("в_буфер", True)
    if isinstance(to_buf, str):
        to_buf = to_buf.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_buf:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(img)
            prefix = "[BUF] "
        except Exception as e:
            return "Кадр " + str(img.size) + ", но в буфер не попал: " + str(e)

    return (prefix + "OK " + (title or "камера") + " -> " +
            str(img.size[0]) + "x" + str(img.size[1]) + " -> буфер" +
            "  (всего камер: " + str(len(cams)) + ")")
