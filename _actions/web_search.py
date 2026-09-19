"""Веб-поиск, HTTP-запросы, загрузка страниц."""
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from _actions import register
from _config import PUBLIC_TOOLS_DIR, WIKI_TEXTS_DIR, API_KEYS, ensure_api_key
from _utils import log, progress_bar
from _tor import ensure_tor
from _web import (
    parse_ddgs_output, calculate_relevance, fetch_page_content,
    DEFAULT_HEADERS,
)
from _ps import run_with_wait

# --- кэши open/find/search ---
_open_pages_cache = {}
_search_results_cache = {}
_last_search_query = None

# --- кэш HTTP-ответов ---
_http_cache = {}
_HTTP_CACHE_TTL = 300  # сек


def _http_cache_key(method, url, body):
    import hashlib as _hl
    b = ""
    if body is not None:
        if isinstance(body, dict):
            b = json.dumps(body, sort_keys=True, ensure_ascii=False)
        else:
            b = str(body)
    h = _hl.md5(b.encode("utf-8")).hexdigest()[:12]
    return f"{method}:{url}:{h}"


def _with_progress(func, timeout_sec, label="⏳ запрос"):
    """Запустить func в отдельном потоке, рисуя прогресс ожидания в stderr.

    Возвращает результат func или пробрасывает её исключение.
    Прогресс стирается перед возвратом.
    """
    import threading as _th
    import time as _t
    import sys as _sys

    result = [None]
    error = [None]
    done = _th.Event()

    def _run():
        try:
            result[0] = func()
        except BaseException as e:
            error[0] = e
        finally:
            done.set()

    t = _th.Thread(target=_run, daemon=True)
    t.start()

    start = _t.time()
    bar_w = 20
    last_len = 0

    while not done.wait(0.5):
        elapsed = _t.time() - start
        secs = int(elapsed)
        ratio = min(1.0, elapsed / max(1, timeout_sec))
        filled = int(bar_w * ratio)
        if filled >= bar_w:
            bar = "=" * bar_w
        else:
            bar = "=" * filled + ">" + "." * (bar_w - filled - 1)
        line = f"\r{label}: {secs}/{timeout_sec}с [{bar}]"
        try:
            _sys.stderr.write(line)
            _sys.stderr.flush()
            last_len = len(line)
        except Exception:
            pass

    if last_len:
        try:
            _sys.stderr.write("\r" + " " * last_len + "\r")
            _sys.stderr.flush()
        except Exception:
            pass

    if error[0] is not None:
        raise error[0]
    return result[0]


def _proxy_addr_for_hint(proxies):
    """Достать ip:port из dict/строки прокси — для подсказки 'удалить_прокси'."""
    if not proxies:
        return ""
    val = None
    if isinstance(proxies, dict):
        val = proxies.get("https") or proxies.get("http") or ""
    elif isinstance(proxies, str):
        val = proxies
    if not val:
        return ""
    # отрезаем схему socks5:// / socks5h:// / http:// и хвост /
    import re as _re_p
    m = _re_p.search(r"([\d.]+:\d+)", val)
    return m.group(1) if m else ""


def clear_http_cache():
    _http_cache.clear()


def _execute_command(cmd, timeout=None):
    from _ps import run_powershell_with_cwd
    return run_with_wait(run_powershell_with_cwd, (cmd,), timeout=timeout)


def clear_search_cache():
    global _open_pages_cache, _search_results_cache, _last_search_query
    _open_pages_cache.clear()
    _search_results_cache.clear()
    _last_search_query = None


# ============================================================
#  веб_поиск / search / search_web
# ============================================================
@register(["веб_поиск", "поиск_информации"])
def web_search(args):
    """Поиск в интернете (DuckDuckGo + Tavily).

    Параметры:
      запрос          — что искать (обязательный); несколько через "||"
      формат          — "text" (по умолчанию) или "json"
      мин_релевантность — порог отсечения 0..1 (по умолчанию 0.1)

    Алиас: поиск_информации.

    Примеры:
      {"веб_поиск": {"запрос": "python asyncio"}}
      {"поиск_информации": {"запрос": "AGI|LLM"}}
    """
    query_str = args.get("запрос") or args.get("queries") or args.get("query") or ""
    output_format = args.get("формат", "text")
    if not query_str:
        return "Ошибка: не указан запрос (поле 'запрос' или 'queries')"

    queries = [q.strip() for q in query_str.split('||') if q.strip()]
    if not queries:
        return "Ошибка: пустой запрос"

    clear_search_cache()
    global _search_results_cache, _last_search_query
    _last_search_query = query_str

    aggregated = {}
    import subprocess as _sp
    import sys as _sys_w
    for q in queries:
        web_search_script = PUBLIC_TOOLS_DIR / "web_search.py"
        if not web_search_script.exists():
            log(f"web_search.py не найден: {web_search_script}")
            continue
        # если ключа нет — спросим и сохраним (только из главного потока)
        tavily_key = ensure_api_key("tavily")
        _args = [_sys_w.executable, str(web_search_script), q]
        if tavily_key:
            _args += ["--tavily-key", tavily_key]
        try:
            # ПРЯМОЙ subprocess — PowerShell схлопывает \n в пробелы,
            # из-за этого парсер не может разделить результаты
            _r = _sp.run(
                _args, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            raw_result = _r.stdout or ""
            if _r.stderr:
                log(f"web_search.py stderr '{q}': {_r.stderr[:500]}")
        except Exception as e:
            log(f"Ошибка запуска web_search.py для '{q}': {e}")
            continue

        items = parse_ddgs_output(raw_result)
        if not items:
            continue

        for item in items:
            url = item.get("url")
            if not url:
                continue
            title = item.get("title", "") or url.split('/')[-1] or "Без названия"
            snippet = item.get("snippet", "") or "[Описание отсутствует]"
            score = calculate_relevance(q, title, snippet)
            if url in aggregated:
                ex = aggregated[url]
                ex["relevance_score"] = min(1.0, ex["relevance_score"] + score)
                if snippet not in ex["snippet"]:
                    ex["snippet"] += " | " + snippet
                if len(title) > len(ex["title"]):
                    ex["title"] = title
            else:
                aggregated[url] = {"url": url, "title": title,
                                   "snippet": snippet, "relevance_score": score}

    all_structured = list(aggregated.values())
    all_structured.sort(key=lambda x: x["relevance_score"], reverse=True)
    min_relevance = args.get("мин_релевантность", 0.1)
    all_structured = [i for i in all_structured if i["relevance_score"] >= min_relevance]

    _search_results_cache.clear()
    for idx, item in enumerate(all_structured):
        item["id"] = idx
        item["cursor"] = idx
        _search_results_cache[idx] = item

    if output_format == "json":
        return json.dumps({"search_results": all_structured}, ensure_ascii=False, indent=2)
    if not all_structured:
        return "По вашему запросу ничего не найдено."
    lines = []
    for item in all_structured:
        score = item.get('relevance_score', 0)
        lines.append(f"[id: {item['id']} | релевантность: {score:.2f}] {item['title']}")
        if item['snippet']:
            lines.append(item['snippet'])
        lines.append(f"🔗 {item['url']}")
        lines.append("")
    return "\n".join(lines).strip()


# ============================================================
#  запрос / веб_запрос
# ============================================================
@register(["запрос", "веб_запрос"])
def http_request(args):
    """HTTP-запрос: GET / POST / PUT / DELETE.

    Параметры:
      url           — адрес (обязательный)
      метод         — GET (по умолчанию), POST, PUT, DELETE
      заголовки     — свои HTTP-заголовки (объект)
      тело          — тело для POST/PUT (объект для JSON или строка)
      сохранить_как — путь для сохранения ответа в файл
      прокси        — прокси-URL (socks5://... или http://...)
      авто_прокси   — true: через Tor (если прокси не задан)
      таймаут       — секунд (по умолчанию 30)
      verify_ssl    — проверять сертификат (по умолчанию true)
      страница      — номер страницы вывода (по умолчанию 1)
      размер        — символов на страницу (по умолчанию 2000)
      обновить      — true: игнорировать кэш (TTL 5 мин)

    Алиас: веб_запрос.

    Примеры:
      {"запрос": {"url": "https://api.example.com/data"}}
      {"запрос": {"url": "https://httpbin.org/post", "метод": "POST",
                  "тело": {"ключ": "значение"}}}
      {"запрос": {"url": "https://cdn.example.com/big.zip",
                  "сохранить_как": "E:/tmp/big.zip"}}
    """
    method = args.get("метод", "GET").upper()
    url = args.get("url", "")
    custom_headers = args.get("заголовки", {})
    body = args.get("тело", None)
    save_path = args.get("сохранить_как", None)
    proxy = args.get("прокси") or args.get("proxy") or args.get("прокси_url")
    # прокси может быть строкой "socks5://host:port" или объектом
    if isinstance(proxy, str):
        proxy = {"http": proxy, "https": proxy}
    elif proxy is not None and not isinstance(proxy, dict):
        return f"Ошибка: неверный формат 'прокси' ({type(proxy).__name__})"
    auto_proxy = args.get("авто_прокси", False)
    timeout_sec = args.get("таймаут", 30)
    if not isinstance(timeout_sec, (int, float)):
        timeout_sec = 30

    if not url:
        return "Ошибка: не указан URL"

    if auto_proxy and not proxy and ensure_tor():
        proxy = {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}

    headers = {**DEFAULT_HEADERS, **custom_headers}
    proxies = proxy if proxy else None
    verify = args.get("verify_ssl", True)

    try:
        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            kw = dict(headers=headers, proxies=proxies, verify=verify,
                      stream=True, timeout=timeout_sec)
            if method == "GET":
                resp = requests.get(url, **kw)
            elif method == "POST":
                if isinstance(body, dict):
                    headers.setdefault("Content-Type", "application/json")
                    resp = requests.post(url, json=body, **kw)
                else:
                    resp = requests.post(url, data=body, **kw)
            elif method == "PUT":
                if isinstance(body, dict):
                    headers.setdefault("Content-Type", "application/json")
                    resp = requests.put(url, json=body, **kw)
                else:
                    resp = requests.put(url, data=body, **kw)
            elif method == "DELETE":
                resp = requests.delete(url, **kw)
            else:
                return f"Неподдерживаемый метод: {method}"
            resp.raise_for_status()
            total = int(resp.headers.get('content-length', 0))
            done = 0
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if total > 0:
                            pct = done / total * 100
                            sys.stdout.write(f"\r{progress_bar(pct)} ({done/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB)")
                            sys.stdout.flush()
            print()
            return f"Файл сохранён: {save_path} ({os.path.getsize(save_path)} байт)"

        # --- кэш: не делать повторный запрос в течение TTL ---
        cache_key = _http_cache_key(method, url, body)
        now = time.time()
        entry = _http_cache.get(cache_key)
        fresh = bool(args.get("обновить") or args.get("refresh"))
        if entry and not fresh and (now - entry["ts"]) < _HTTP_CACHE_TTL:
            status_code = entry["status"]
            body_text = entry["body"]
            content_type = entry["content_type"]
            from_cache = True
        else:
            kw = dict(headers=headers, proxies=proxies, verify=verify,
                      timeout=timeout_sec)

            def _do_request():
                if method == "GET":
                    return requests.get(url, **kw)
                if method == "POST":
                    if isinstance(body, dict):
                        headers.setdefault("Content-Type", "application/json")
                        return requests.post(url, json=body, **kw)
                    return requests.post(url, data=body, **kw)
                if method == "PUT":
                    if isinstance(body, dict):
                        headers.setdefault("Content-Type", "application/json")
                        return requests.put(url, json=body, **kw)
                    return requests.put(url, data=body, **kw)
                if method == "DELETE":
                    return requests.delete(url, **kw)
                raise ValueError(f"Неподдерживаемый метод: {method}")

            _label = "⏳ запрос через прокси" if proxies else "⏳ запрос"
            try:
                resp = _with_progress(_do_request, timeout_sec, label=_label)
            except ValueError as _ve:
                return str(_ve)
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                try:
                    body_text = json.dumps(resp.json(),
                                           ensure_ascii=False, indent=2)
                except Exception:
                    body_text = resp.text
            else:
                body_text = resp.text
            _http_cache[cache_key] = {
                "ts": now,
                "status": status_code,
                "content_type": content_type,
                "body": body_text,
            }
            from_cache = False

        # --- постраничный вывод ---
        size = args.get("размер") or args.get("лимит") or 2000
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 2000
        if size <= 0:
            size = 2000

        total = len(body_text)
        total_pages = max(1, (total + size - 1) // size)
        page = args.get("страница", 1)
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * size
        end = min(start + size, total)
        chunk = body_text[start:end]

        cache_mark = " [кэш]" if from_cache else ""
        result = f"Статус: {status_code}{cache_mark}\n"
        result += (f"Страница {page}/{total_pages} "
                   f"(символов {start+1}-{end} из {total})\n\n")
        result += chunk
        if page < total_pages:
            result += (f"\n\n➡️ Дальше: добавь \"страница\": {page+1}")
        if page > 1:
            result += (f"\n⬅️ Назад: добавь \"страница\": {page-1}")
        return result
    except requests.exceptions.Timeout:
        _msg = f"Ошибка: превышен таймаут ({timeout_sec} сек)"
        if proxies:
            _addr = _proxy_addr_for_hint(proxies)
            if _addr:
                _msg += ('\n\n💡 Прокси, возможно, мёртв. Удалить его из хранилища:\n'
                         '```text\n'
                         f'{{"удалить_прокси": {{"прокси": "{_addr}"}}}}\n'
                         '```')
        return _msg
    except Exception as e:
        _msg = f"Ошибка запроса: {e}"
        if proxies and ("proxy" in str(e).lower()
                        or "socks" in str(e).lower()
                        or "connect" in str(e).lower()):
            _addr = _proxy_addr_for_hint(proxies)
            if _addr:
                _msg += ('\n\n💡 Прокси, возможно, мёртв. Удалить его из хранилища:\n'
                         '```text\n'
                         f'{{"удалить_прокси": {{"прокси": "{_addr}"}}}}\n'
                         '```')
        return _msg


# ============================================================
#  веб_страница / страница / open
# ============================================================
@register(["страница", "веб_страница"])
def web_page(args):
    """Загрузить веб-страницу и вернуть её текст.

    Параметры:
      адрес        — URL (обязательный), алиас: url
      авто_прокси  — true: через Tor
      verify_ssl   — проверять сертификат (по умолчанию true)
      сессия       — true: с куками и редиректами (по умолчанию true)
      редиректы    — следовать редиректам (по умолчанию true)
      view_source  — true: вернуть HTML без очистки

    Спец-режимы:
      open_list — открыть несколько URL/ID из кэша поиска
      find_list — искать подстроку в ранее открытых страницах

    Алиас: веб_страница.

    Примеры:
      {"страница": {"адрес": "https://example.com"}}
      {"страница": {"url": "https://ru.wikipedia.org/wiki/Python"}}
    """
    from urllib.parse import urlparse, unquote
    from _actions.tools_web import find_canonical

    open_list = args.get("open_list")
    find_list = args.get("find_list")
    url = args.get("адрес", "") or args.get("url", "")
    auto_proxy = args.get("авто_прокси", False)
    verify = args.get("verify_ssl", True)
    use_session = args.get("сессия", True)
    allow_redirects = args.get("редиректы", True)
    view_source = args.get("view_source", False)

    proxies = None
    _p = args.get("прокси") or args.get("proxy")
    if isinstance(_p, str):
        _p = {"http": _p, "https": _p}
    if isinstance(_p, dict):
        proxies = _p
    elif auto_proxy and ensure_tor():
        proxies = {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}

    def _fetch(target_url, proxies=None, verify=True):
        clean_html = True
        if use_session:
            session = requests.Session()
            session.max_redirects = 10
            session.headers.update({
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Accept": DEFAULT_HEADERS["Accept"],
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            })
            try:
                resp = session.get(target_url, proxies=proxies, verify=verify,
                                   allow_redirects=allow_redirects, timeout=30)
                resp.raise_for_status()
                content = resp.text if resp.encoding else resp.content.decode('utf-8', errors='replace')
                if clean_html:
                    soup = BeautifulSoup(content, 'html.parser')
                    for script in soup(["script", "style"]):
                        script.decompose()
                    for a in soup.find_all('a', href=True):
                        a.replace_with(f"[{a.get_text(strip=True)}]({a['href']})")
                    text = soup.get_text(separator='\n', strip=True)
                else:
                    text = content
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                return '\n'.join(lines), resp.url, dict(resp.cookies)
            except Exception as e:
                return f"Ошибка загрузки {target_url}: {e}", target_url, {}
        else:
            content = fetch_page_content(target_url, use_tor=auto_proxy, clean_html=not view_source)
            if content.startswith("Ошибка"):
                return content, target_url, {}
            if clean_html:
                soup = BeautifulSoup(content, 'html.parser')
                for script in soup(["script", "style"]):
                    script.decompose()
                for a in soup.find_all('a', href=True):
                    a.replace_with(f"[{a.get_text(strip=True)}]({a['href']})")
                text = soup.get_text(separator='\n', strip=True)
                content = '\n'.join(l.strip() for l in text.splitlines() if l.strip())
            return content, target_url, {}

    # --- open_list ---
    if open_list is not None:
        results = []
        for idx, item in enumerate(open_list):
            target_id = item.get("id")
            cursor = item.get("cursor", -1)
            loc = item.get("loc", 0)
            num_lines = item.get("num_lines", -1)
            item_view_source = item.get("view_source", False)

            if cursor == -1 and isinstance(target_id, str) and target_id.startswith("http"):
                cursor = idx

            if isinstance(target_id, str) and target_id.startswith("http"):
                url_to_fetch = target_id
            elif target_id is not None and target_id != -1:
                cached = _search_results_cache.get(target_id)
                if cached:
                    url_to_fetch = cached["url"]
                else:
                    results.append({"error": f"ID {target_id} не найден в кэше поиска"})
                    continue
            else:
                results.append({"error": "Не указан id или url"})
                continue

            page_key = cursor if cursor != -1 else url_to_fetch

            if page_key in _open_pages_cache:
                page = _open_pages_cache[page_key]
                content = page["content"]
                final_url = page["url"]
                source_type = page.get("source", "http")
            else:
                source_type = "http"
                parsed_url = urlparse(url_to_fetch)
                if parsed_url.netloc == "ru.wikipedia.org":
                    _proxies = args.get("прокси") or args.get("proxies") or proxies
                    _verify = args.get("verify_ssl", True)
                    content, final_url, cookies = _fetch(url_to_fetch, proxies=_proxies, verify=_verify)
                    if content.startswith("Ошибка загрузки") and "403" in content:
                        path_parts = parsed_url.path.split('/')
                        if len(path_parts) >= 3 and path_parts[1] == "wiki":
                            article_slug = unquote(path_parts[2]).replace('_', ' ')
                            canon = find_canonical(article_slug)
                            filepath = WIKI_TEXTS_DIR / f"{canon}.txt" if canon else WIKI_TEXTS_DIR / f"{article_slug}.txt"
                            if filepath.exists():
                                try:
                                    with open(filepath, "r", encoding="utf-8") as f:
                                        content = f.read()
                                    final_url = url_to_fetch
                                    cookies = {}
                                    source_type = "local"
                                except Exception as e:
                                    content = f"Ошибка чтения локальной статьи: {e}"
                            else:
                                content = f"Ошибка: локальная копия не найдена"
                else:
                    _proxies = args.get("прокси") or args.get("proxies") or proxies
                    _verify = args.get("verify_ssl", True)
                    content, final_url, cookies = _fetch(url_to_fetch, proxies=_proxies, verify=_verify)

                if content.startswith("Ошибка"):
                    results.append({"error": content, "url": url_to_fetch})
                    continue

                lines = content.splitlines()
                _open_pages_cache[page_key] = {
                    "url": final_url, "content": content, "lines": lines,
                    "fetched_at": time.time(), "cookies": cookies, "source": source_type,
                }

            lines = _open_pages_cache[page_key]["lines"]
            total_lines = len(lines)
            if loc < 0:
                loc = 0
            if loc >= total_lines:
                content_lines = []
            else:
                end = total_lines if num_lines == -1 else min(loc + num_lines, total_lines)
                content_lines = lines[loc:end]

            results.append({
                "id": target_id if isinstance(target_id, str) else str(target_id),
                "cursor": page_key,
                "url": final_url,
                "total_lines": total_lines,
                "loc": loc,
                "num_lines": len(content_lines),
                "content_lines": content_lines if not item_view_source else lines,
                "source": source_type,
            })
        return json.dumps({"open_results": results}, ensure_ascii=False, indent=2)

    # --- find_list ---
    if find_list is not None:
        results = []
        for item in find_list:
            pattern = item.get("pattern", "")
            cursor = item.get("cursor", -1)
            if not pattern:
                results.append({"error": "Не указан pattern"})
                continue
            if cursor == -1:
                results.append({"error": "Не указан cursor"})
                continue
            page = _open_pages_cache.get(cursor)
            if not page:
                results.append({"error": f"Страница с cursor={cursor} не открыта"})
                continue
            lines = page["lines"]
            matches = []
            for line_num, line in enumerate(lines):
                if pattern in line:
                    pos = line.find(pattern)
                    matches.append({"line": line_num, "start_char": pos,
                                    "end_char": pos + len(pattern), "context": line.strip()})
            results.append({"cursor": cursor, "pattern": pattern,
                            "matches": matches, "count": len(matches)})
        return json.dumps({"find_results": results}, ensure_ascii=False, indent=2)

    # --- простой режим ---
    if not url:
        return "Ошибка: не указан адрес"
    content = fetch_page_content(url, use_tor=auto_proxy, clean_html=not view_source)
    if content.startswith("Ошибка"):
        return content
    lines = content.splitlines()
    _open_pages_cache[url] = {
        "url": url, "content": content, "lines": lines,
        "fetched_at": time.time(), "cookies": {}, "source": "http",
    }
    if not content.strip():
        return fetch_page_content(url, use_tor=auto_proxy, clean_html=False)
    return content[:2048]
