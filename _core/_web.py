"""\u0412\u0435\u0431-\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0430, \u043f\u0430\u0440\u0441\u0438\u043d\u0433 DDG, \u0440\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u043e\u0441\u0442\u044c."""
import random
import re
import time
from time import sleep
from urllib.parse import urlparse
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

from _utils import log
from _tor import ensure_tor

_last_request_time: Dict[str, float] = {}
_MIN_DELAY = 2.0

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def calculate_relevance(query: str, title: str, snippet: str) -> float:
    if not query:
        return 0.5

    def tokenize(text):
        return set(re.findall(r'[a-zA-Z\u0430-\u044f\u0410-\u042f\u0451\u04010-9]+', text.lower()))

    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.5
    title_tokens = tokenize(title)
    snippet_tokens = tokenize(snippet)
    title_match = len(query_tokens & title_tokens) / len(query_tokens)
    snippet_match = len(query_tokens & snippet_tokens) / len(query_tokens)
    score = title_match * 0.7 + snippet_match * 0.3
    if query.lower() in title.lower():
        score = min(1.0, score + 0.2)
    elif query.lower() in snippet.lower():
        score = min(1.0, score + 0.1)
    return round(score, 2)


def fetch_page_content(url: str, max_length: int = 100_000,
                       use_tor: bool = False, clean_html: bool = True,
                       retries: int = 2) -> str:
    proxies = None
    if use_tor:
        if ensure_tor():
            proxies = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
        else:
            log("Tor \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d, \u0437\u0430\u043f\u0440\u043e\u0441 \u0431\u0435\u0437 \u043f\u0440\u043e\u043a\u0441\u0438")

    domain = urlparse(url).netloc
    now = time.time()
    last = _last_request_time.get(domain, 0)
    if now - last < _MIN_DELAY:
        sleep(_MIN_DELAY - (now - last) + random.uniform(0.5, 1.5))
    _last_request_time[domain] = time.time()

    last_exception = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, proxies=proxies,
                                timeout=15, stream=True)
            resp.raise_for_status()
            content = ""
            for chunk in resp.iter_content(chunk_size=8192, decode_unicode=True):
                if chunk:
                    content += chunk
                    if len(content) > max_length:
                        content = content[:max_length]
                        break
            if resp.encoding:
                try:
                    content = content.encode(resp.encoding).decode(resp.encoding)
                except Exception:
                    content = content.encode('utf-8', errors='replace').decode('utf-8')
            else:
                content = content.encode('utf-8', errors='replace').decode('utf-8')

            if clean_html:
                soup = BeautifulSoup(content, 'html.parser')
                for script in soup(["script", "style"]):
                    script.decompose()
                # Ссылки → markdown [текст](url), иначе URL теряются
                # при get_text и агент видит только голый текст без ссылок.
                for a in soup.find_all('a', href=True):
                    _t = a.get_text(strip=True) or a['href']
                    a.replace_with(f"[{_t}]({a['href']})")
                text = soup.get_text(separator='\n', strip=True)
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                content = '\n'.join(lines)
            return content
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403 and attempt < retries:
                log(f"HTTP 403 \u0434\u043b\u044f {url}, \u043f\u043e\u043f\u044b\u0442\u043a\u0430 {attempt+1}/{retries}, \u0436\u0434\u0451\u043c...")
                sleep(5 * (attempt + 1))
                continue
            return f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 {url}: {e}"
        except Exception as e:
            last_exception = e
            if attempt < retries:
                sleep(2)
                continue
            return f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 {url}: {e}"
    return f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 {url}: {last_exception}"


def parse_ddgs_output(raw: str) -> List[Dict]:
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    results = []
    for block in raw.split('\n\n'):
        if not block.strip():
            continue
        lines = block.strip().split('\n')
        url_line_idx = -1
        url = None
        for i, line in enumerate(lines):
            if 'http://' in line or 'https://' in line:
                start = line.find('http://')
                if start == -1:
                    start = line.find('https://')
                if start != -1:
                    url = line[start:].strip()
                    url_line_idx = i
                    break
        if not url:
            continue
        if url_line_idx > 0:
            title = lines[0].strip()
            snippet = '\n'.join(lines[1:url_line_idx]).strip()
        else:
            title = ""
            snippet = ""
        if not title and len(lines) > 1:
            title = lines[0].strip()
        # парсим строку вида "   [ddg,tavily | rel=0.15]"
        sources = []
        relevance = None
        for _ln in lines:
            _m = re.search(r"\[([^\|\]]+?)\s*\|\s*rel=([\d.]+)\s*\]", _ln)
            if _m:
                sources = [x.strip() for x in _m.group(1).split(",") if x.strip()]
                try:
                    relevance = float(_m.group(2))
                except ValueError:
                    pass
                break
        item = {"url": url, "title": title, "snippet": snippet}
        if sources:
            item["sources"] = sources
        if relevance is not None:
            item["relevance"] = relevance
        results.append(item)
    return results


def fetch_images_ddgs(query, n, size=None, type_image=None, color=None,
                      layout=None, lic=None, backend=None, use_tor=False):
    """Поиск картинок через ddgs. backend: auto/duckduckgo/bing.
    use_tor: DDGS(proxy=socks5h://127.0.0.1:9050), обходит IP-бан.
    """
    try:
        from ddgs import DDGS
    except ImportError as e:
        return [], "Не удалось импортировать ddgs: " + str(e)
    if use_tor:
        try:
            if not ensure_tor():
                return [], ("Tor не поднят — запусти Tor или убери "
                            "через_tor")
        except Exception as e:
            return [], "Tor: " + str(e)
    kwargs = {"max_results": n}
    if backend:
        kwargs["backend"] = backend
    if size:
        kwargs["size"] = size
    if type_image:
        kwargs["type_image"] = type_image
    if color:
        kwargs["color"] = color
    if layout:
        kwargs["layout"] = layout
    if lic:
        kwargs["license_image"] = lic
    print(f"  [DDG] {query} (tor={use_tor}, backend={backend}) ...", flush=True)
    delays = (3.0, 6.0, 12.0)
    last_err = None
    for attempt in range(len(delays) + 1):
        try:
            if use_tor:
                try:
                    d = DDGS(proxy="socks5h://127.0.0.1:9050")
                except TypeError:
                    d = DDGS()
            else:
                d = DDGS()
            out = []
            with d:
                for r in d.images(query, **kwargs):
                    img = r.get("image") or ""
                    if not img:
                        continue
                    out.append({
                        "title": r.get("title") or "",
                        "image": img,
                        "thumbnail": r.get("thumbnail") or "",
                        "url": r.get("url") or "",
                        "width": r.get("width"),
                        "height": r.get("height"),
                        "source": r.get("source") or "",
                    })
            if out:
                print(f"  [DDG] OK, {len(out)} картинок", flush=True)
                return out, None
            last_err = "No results found"
            print(f"  [DDG] пусто (попытка {attempt+1}/{len(delays)+1})", flush=True)
            if attempt < len(delays):
                sleep(delays[attempt])
        except TypeError as e:
            print(f"  [DDG] ошибка параметров: {e}", flush=True)
            return [], ("Версия ddgs не поддерживает один из параметров: "
                        + str(e))
        except Exception as e:
            msg = str(e).lower()
            last_err = str(e)
            print(f"  [DDG] ошибка: {type(e).__name__}: {str(e)[:100]}", flush=True)
            _transient = (
                "no results" in msg
                or "403" in msg
                or "429" in msg
                or "rate limit" in msg
                or "getaddrinfo" in msg
                or "max retries" in msg
                or "connection reset" in msg
                or "timed out" in msg
                or "timeout" in msg
            )
            if not _transient or attempt == len(delays):
                return [], "Ошибка DuckDuckGo: " + str(e)
            sleep(delays[attempt])
    print(f"  [DDG] окончательно пусто: {last_err}", flush=True)
    return [], "Ошибка DuckDuckGo: " + (last_err or "пустой ответ")


def _debug_http_ddg_images(query, n=3):
    """Прямой HTTP к DDG image endpoint с полным дампом запроса/ответа.

    Показывает, что реально уходит на duckduckgo.com/i.js и что приходит
    (status, headers, первые 500 символов body). Помогает отличить
    rate limit / captcha / смену API от бага в ddgs.
    """
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    out = []
    # 1. Главная страница — получить vqd-токен (без него i.js отдаёт 403)
    try:
        r1 = s.get("https://duckduckgo.com/",
                   params={"q": query, "iax": "images", "ia": "images"},
                   timeout=15)
        out.append("--- GET https://duckduckgo.com/ (vqd) ---")
        out.append("  URL: " + r1.url)
        out.append("  status: " + str(r1.status_code))
        out.append("  headers: " + repr(dict(r1.headers))[:300])
        vqd = None
        m = re.search(r'vqd=["\']?([\d-]+)', r1.text)
        if m:
            vqd = m.group(1)
        out.append("  vqd: " + repr(vqd))
        out.append("  body[:300]: " + repr(r1.text[:300]))
    except Exception as e:
        out.append("--- GET https://duckduckgo.com/ ---")
        out.append("ИСКЛЮЧЕНИЕ " + type(e).__name__ + ": " + str(e)[:300])
        return "\n".join(out)
    # 2. Image endpoint
    params = {"l": "us-en", "o": "json", "q": query,
              "f": ",,,", "p": "1"}
    if vqd:
        params["vqd"] = vqd
    try:
        r2 = s.get("https://duckduckgo.com/i.js", params=params,
                   headers={"Referer": "https://duckduckgo.com/"},
                   timeout=15)
        out.append("--- GET https://duckduckgo.com/i.js ---")
        out.append("  params: " + repr(params))
        out.append("  URL: " + r2.url)
        out.append("  status: " + str(r2.status_code))
        out.append("  headers: " + repr(dict(r2.headers))[:300])
        out.append("  body[:500]: " + repr(r2.text[:500]))
    except Exception as e:
        out.append("--- GET https://duckduckgo.com/i.js ---")
        out.append("ИСКЛЮЧЕНИЕ " + type(e).__name__ + ": " + str(e)[:300])
    return "\n".join(out)


_SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.bus-hit.me",
    "https://searx.tiekoetter.com",
    "https://opnxng.com",
    "https://paulgo.io",
    "https://searxng.site",
    "https://priv.au",
    "https://search.inetol.net",
    "https://searx.work",
    "https://search.hbubli.cc",
]


def fetch_images_searxng(query, n, instance=None, timeout=15, use_tor=False):
    """Поиск картинок через публичный SearXNG (JSON API).

    SearXNG агрегирует Google/Bing/DDG за своим IP — обходит наши
    баны. Публичные инстансы могут тормозить/лежать — перебираем.
    Возвращает (results, err). results — как у fetch_images_ddgs.
    """
    out = []
    seen = set()
    instances = ([instance] if instance else []) + _SEARXNG_INSTANCES
    last_err = None
    proxies = None
    if use_tor:
        proxies = {"http": "socks5h://127.0.0.1:9050",
                   "https": "socks5h://127.0.0.1:9050"}
    
    for base in instances:
        print(f"  [SearXNG] {base} (tor={use_tor}) ...", flush=True)
        try:
            r = requests.get(
                base.rstrip("/") + "/search",
                params={"q": query, "format": "json",
                        "categories": "images", "safesearch": "0",
                        "pageno": "1"},
                headers=DEFAULT_HEADERS, timeout=timeout,
                proxies=proxies,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [SearXNG] {base} ошибка: {type(e).__name__}: {str(e)[:100]}", flush=True)
            last_err = type(e).__name__ + ": " + str(e)[:120]
            continue
        for item in data.get("results", []):
            img = item.get("img_src") or item.get("image") or ""
            if not img or img in seen:
                continue
            seen.add(img)
            w = h = None
            res = item.get("resolution") or ""
            if "x" in res:
                try:
                    _w, _h = res.lower().split("x", 1)
                    w, h = int(_w), int(_h)
                except (ValueError, TypeError):
                    pass
            out.append({
                "title": item.get("title") or "",
                "image": img,
                "thumbnail": item.get("thumbnail_src") or "",
                "url": item.get("url") or "",
                "width": w,
                "height": h,
                "source": "SearXNG",
            })
            if len(out) >= n:
                print(f"  [SearXNG] {base} OK, {len(out)} картинок", flush=True)
                return out[:n], None
        if out:
            print(f"  [SearXNG] {base} OK, {len(out)} картинок", flush=True)
            return out[:n], None
        print(f"  [SearXNG] {base} вернул 0 результатов", flush=True)
        last_err = "пусто на " + base
    print(f"  [SearXNG] все инстансы отказали", flush=True)
    return [], "SearXNG: " + (last_err or "все инстансы недоступны")


def debug_images(query, n=3):
    """Диагностика: сырой repr() от d.images() в режимах + прямой HTTP.

    (a) без kwargs — как d.text() в web_search.py;
    (b) только max_results;
    (c) с safesearch="off";
    (d) прямой HTTP к duckduckgo.com/i.js — полный дамп.
    Возвращает строку с результатом каждого режима.
    """
    try:
        from ddgs import DDGS
    except ImportError as e:
        return "Не удалось импортировать ddgs: " + str(e)
    modes = [
        ("a: без kwargs", {}),
        ("b: только max_results", {"max_results": n}),
        ("c: safesearch=off", {"max_results": n, "safesearch": "off"}),
    ]
    out = []
    for label, kwargs in modes:
        out.append("--- " + label + " ---")
        out.append("  kwargs: " + repr(kwargs))
        try:
            with DDGS() as d:
                raw = list(d.images(query, **kwargs))
            out.append("получено: " + str(len(raw)))
            for i, r in enumerate(raw[:3]):
                out.append("  [" + str(i) + "] keys=" + str(sorted(r.keys())))
                out.append("       image=" + repr(r.get("image", ""))[:200])
            if not raw:
                out.append("  (пусто)")
        except Exception as e:
            out.append("ИСКЛЮЧЕНИЕ " + type(e).__name__ + ": " + str(e)[:300])
    out.append(_debug_http_ddg_images(query, n))
    return "\n".join(out)

def fetch_images_bing(query, n, size=None, type_image=None, color=None,
                      layout=None, lic=None, use_tor=False):
    """Поиск картинок через Bing Images (прямой HTTP).
    
    Извлекает vqd-токен из выдачи search, иначе async отдаёт мусор
    (трендовые картинки вместо запроса).
    """
    import json as _json
    import html as _html
    import re as _re
    
    proxies = None
    if use_tor:
        proxies = {"http": "socks5h://127.0.0.1:9050",
                   "https": "socks5h://127.0.0.1:9050"}
                   
    try:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS)
        if proxies:
            s.proxies.update(proxies)
        s.get("https://www.bing.com/", timeout=15)
    except Exception as e:
        print(f"  [Bing] ошибка инициализации сессии: {type(e).__name__}: {str(e)[:100]}", flush=True)
        s = requests
    out = []
    seen = set()
    diag = []
    vqd = None

    def _add(murl, title="", thumb="", page="", w=None, h=None):
        if not murl or murl in seen:
            return False
        seen.add(murl)
        out.append({
            "title": title,
            "image": murl,
            "thumbnail": thumb,
            "url": page,
            "width": w,
            "height": h,
            "source": "Bing",
        })
        return True

    for endpoint in ("search", "async"):
        print(f"  [Bing] {endpoint} (tor={use_tor}) ...", flush=True)
        first = 1
        count = min(max(n, 10), 35)
        while len(out) < n and first <= 200:
            url = ("https://www.bing.com/images/search" if endpoint == "search"
                   else "https://www.bing.com/images/async")
            params = {"q": query, "first": first, "count": count,
                      "mkt": "en-US", "adlt": "moderate"}
            if endpoint == "async" and vqd:
                params["vqd"] = vqd
                
            try:
                r = s.get(url, params=params, timeout=20, proxies=proxies)
                r.raise_for_status()
            except Exception as e:
                print(f"  [Bing] {endpoint} ошибка: {type(e).__name__}: {str(e)[:100]}", flush=True)
                diag.append(endpoint + ": EXC " + type(e).__name__ + ": " + str(e)[:150])
                break
            
            text = r.text
            
            # Ищем vqd в первом ответе search
            if endpoint == "search" and vqd is None:
                _m = _re.search(r'vqd="([^"]+)"', text) or _re.search(r'vqd=([\d-]+)', text)
                if _m:
                    vqd = _m.group(1)
                    print(f"  [Bing] найден vqd: {vqd}", flush=True)
            
            soup = BeautifulSoup(text, "html.parser")
            items = (soup.select("a.iusc") or soup.select("a.mimg"))
            if not items:
                print(f"  [Bing] {endpoint} пусто (status={r.status_code}, len={len(text)}, a.iusc=0)", flush=True)
                diag.append(endpoint + ": url=" + r.url
                            + " status=" + str(r.status_code)
                            + " len=" + str(len(text))
                            + " a.iusc=" + str(len(soup.select("a.iusc")))
                            + " a.mimg=" + str(len(soup.select("a.mimg")))
                            + " preview=" + repr(text[:200]))
                break
            found = 0
            for a in items:
                m = a.get("m")
                if not m:
                    continue
                try:
                    data = _json.loads(_html.unescape(m))
                except Exception:
                    try:
                        data = _json.loads(m)
                    except Exception:
                        continue
                if _add(data.get("murl") or "",
                        data.get("t") or "",
                        data.get("turl") or "",
                        data.get("purl") or "",
                        data.get("mid") and None or None,
                        None):
                    found += 1
                if len(out) >= n:
                    break
            if found == 0:
                print(f"  [Bing] {endpoint} items={len(items)}, но 0 murl", flush=True)
                diag.append(endpoint + ": items=" + str(len(items)) + " found=0 (все без murl)")
                break
            print(f"  [Bing] {endpoint} OK, +{found} картинок (всего {len(out)})", flush=True)
            first += count
        if len(out) >= n:
            break
    if out:
        print(f"  [Bing] OK, {len(out)} картинок", flush=True)
        return out[:n], None
    print(f"  [Bing] все эндпоинты пусты", flush=True)
    return [], ("DIAG: " + " | ".join(diag) if diag else "DIAG: пусто, без диагностики")
