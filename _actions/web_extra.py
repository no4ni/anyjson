"""Независимые веб-API: новости, погода, энциклопедия, arxiv, stackexchange, wolfram, pastebin."""
import time
import urllib.parse
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from _actions import register
from _config import API_KEYS, ensure_api_key
from _utils import log
from _web import parse_ddgs_output
from _ps import run_with_wait


# ============================================================
#  новости
# ============================================================
@register("новости")
def news(args):
    """Новости по запросу (DuckDuckGo + NewsAPI через Tor).

    Параметры:
      запрос     — тема новостей (обязательный)
      провайдер  — all (по умолч.) / web / api
      количество — сколько результатов (по умолчанию 10)
      язык       — язык новостей для NewsAPI (по умолчанию ru)

    Примеры:
      {"новости": {"запрос": "космос"}}
      {"новости": {"запрос": "AI", "провайдер": "web", "количество": 5}}
    """
    query = args.get("запрос", "")
    mode = args.get("провайдер", "all").lower()
    limit = args.get("количество", 10)
    language = args.get("язык", "ru")
    if not query:
        return "Ошибка: не указан запрос"

    aggregated = {}
    ddg_error = None

    if mode in ("web", "all"):
        # Раньше тут был ddgs_tool.ddgs_tool — модуль не установлен,
        # ветка молча падала в except и «новости» всегда возвращали
        # пустоту. Ходим через штатный веб_поиск (side/web_search.py).
        try:
            from _actions.web_search import web_search as _ws
            raw = _ws({"запрос": "новости " + query,
                       "формат": "json",
                       "мин_релевантность": 0.0})
            import json as _j
            try:
                _data = _j.loads(raw)
                _items = _data.get("search_results", [])[:limit]
            except Exception:
                _items = []
            for item in _items:
                url = item.get("url")
                if not url:
                    continue
                aggregated[url] = {
                    "url": url,
                    "title": item.get("title", "") or url.split("/")[-1],
                    "snippet": item.get("snippet", "") or "[Описание отсутствует]",
                    "source": "DuckDuckGo",
                }
        except Exception as e:
            ddg_error = str(e)
            log(f"Ошибка DDG в новости: {e}")

    if mode in ("api", "all") and API_KEYS.get("newsapi"):
        _newsapi_key = API_KEYS["newsapi"]
        try:
            from _tor import ensure_tor as _et
            _proxies = None
            if _et():
                _proxies = {"http": "socks5h://127.0.0.1:9050",
                            "https": "socks5h://127.0.0.1:9050"}
            _to = datetime.now().date()
            _from = _to - timedelta(days=7)
            _url = "https://newsapi.org/v2/everything"
            _params = {
                "q": query,
                "from": _from.isoformat(),
                "to": _to.isoformat(),
                "language": language,
                "sortBy": "relevancy",
                "pageSize": min(limit, 100),
            }
            _headers = {"X-Api-Key": _newsapi_key}
            _resp = None
            for _attempt in range(3):
                try:
                    _resp = requests.get(_url, params=_params,
                                         headers=_headers,
                                         proxies=_proxies,
                                         timeout=30)
                    break
                except requests.exceptions.RequestException as _e:
                    if _attempt < 2:
                        time.sleep(2 * (_attempt + 1))
                        continue
                    raise
            if _resp is not None and _resp.status_code == 200:
                _data = _resp.json()
                for art in _data.get("articles", []):
                    u = art.get("url")
                    if u and u not in aggregated:
                        aggregated[u] = {
                            "url": u,
                            "title": art.get("title", "") or "Без названия",
                            "snippet": art.get("description", "") or "[Описание отсутствует]",
                            "source": "NewsAPI",
                        }
            elif _resp is not None:
                log(f"NewsAPI HTTP {_resp.status_code}: {_resp.text[:200]}")
        except Exception as e:
            log(f"Ошибка NewsAPI: {e}")

    if not aggregated:
        _hint = ""
        if ddg_error:
            _hint = "\n(DDG: " + ddg_error[:200] + ")"
        return "Новостей не найдено ни в одном источнике." + _hint

    # NewsAPI даёт содержательные новости (заголовок + описание),
    # DDG — только ссылки на источники/разделы. Разделяем вывод:
    # N новостей сверху, N ссылок от DDG — блоком «Подробнее» снизу.
    _news = [v for v in aggregated.values() if v["source"] == "NewsAPI"]
    _ddg = [v for v in aggregated.values() if v["source"] != "NewsAPI"]

    _n_news = min(len(_news), limit)
    _n_ddg = min(len(_ddg), limit)

    lines = []
    if _n_news:
        for i, r in enumerate(_news[:_n_news]):
            lines.append(f"[{i}] {r['title']}")
            _snip = (r.get("snippet") or "").strip()
            if _snip and _snip != "[Описание отсутствует]":
                lines.append(_snip[:300])
            lines.append(f"🔗 {r['url']}")
            lines.append("")
    elif mode != "api":
        lines.append("NewsAPI: свежих новостей не нашлось.")
        lines.append("")

    if _n_ddg:
        lines.append("Подробнее:")
        for r in _ddg[:_n_ddg]:
            _title = r.get("title") or r["url"]
            lines.append(f"  • {_title} — {r['url']}")

    return "\n".join(lines).rstrip()


# ============================================================
#  погода
# ============================================================
@register("погода")
def weather(args):
    """Текущая погода в городе (weatherapi.com).

    Параметры:
      город    — название города (обязательный)
      апи_ключ — ключ weatherapi.com (иначе берётся из api_keys.json)

    Примеры:
      {"погода": {"город": "Москва"}}
      {"погода": {"город": "Tokyo"}}
    """
    city = args.get("город", "")
    api_key = args.get("апи_ключ") or API_KEYS.get("weatherapi")
    if not city:
        return "Ошибка: не указан город"
    if not api_key:
        return "Ошибка: требуется api_key"
    try:
        # HTTPS вместо HTTP: ключ не должен ходить открытым текстом.
        # Через Tor (как Tavily) — api.weatherapi.com из РФ напрямую
        # таймаутится. DNS через socks5h (h = hostname на стороне Tor).
        from _tor import ensure_tor as _ensure_tor_w
        url = "https://api.weatherapi.com/v1/current.json"
        _proxies = None
        if _ensure_tor_w():
            _proxies = {"http": "socks5h://127.0.0.1:9050",
                        "https": "socks5h://127.0.0.1:9050"}
        resp = requests.get(url, params={"key": api_key, "q": city, "lang": "ru"},
                            timeout=30, proxies=_proxies)
        data = resp.json()
        if "error" in data:
            return f"Ошибка API: {data['error'].get('message', 'неизвестно')}"
        c = data["current"]
        loc = data["location"]
        return (f"Погода в {loc['name']}, {loc['country']}:\n"
                f"🌡 Температура: {c['temp_c']}°C (ощущается как {c['feelslike_c']}°C)\n"
                f"💧 Влажность: {c['humidity']}%\n"
                f"🌬 Ветер: {c['wind_kph']} км/ч, {c['wind_dir']}\n"
                f"📋 {c['condition']['text']}")
    except Exception as e:
        return f"Ошибка погоды: {e}"


# ============================================================
#  энциклопедия
# ============================================================
@register("энциклопедия")
def wikipedia(args):
    """Статья из Википедии (русская или английская).

    Параметры:
      статья — название статьи (обязательный)

    Примеры:
      {"энциклопедия": {"статья": "Квантовая механика"}}
      {"энциклопедия": {"статья": "Photosynthesis"}}
    """
    article = args.get("статья", "")
    if not article:
        return "❌ Не указана статья."
    from _actions.tools_web import get_wikipedia_article
    return get_wikipedia_article(article)


# ============================================================
#  arxiv / наука
# ============================================================
@register(["архив", "наука"])
def arxiv(args):
    """Поиск научных статей на arXiv.org.

    Алиас: наука.

    Параметры:
      запрос      — поисковый запрос (обязательный)
      количество  — сколько результатов вернуть (по умолчанию 5)
      категория   — категория arXiv (например cs.AI, math.CO)

    Примеры:
      {"архив": {"запрос": "large language models", "количество": 5}}
      {"наука": {"запрос": "quantum computing", "категория": "quant-ph"}}
    """
    query = args.get("запрос", "")
    if not query:
        # если передан путь/файл — вероятно, имели в виду листинг архива
        _path = args.get("путь") or args.get("файл")
        if _path:
            _win = str(_path).replace("/", "\\")
            return (
                "⚠️ \"архив\" — это поиск на arXiv.org, ему нужен \"запрос\".\n"
                "Возможно, вы имели в виду листинг архива:\n"
                "```text\n"
                f'{{"архив_список": {{"путь": "{_win}"}}}}\n'
                f'{{"архив_список": {{"путь": "{_win}", "количество": 50}}}}\n'
                "```"
            )
        return "Ошибка: не указан запрос"
    try:
        from langchain_community.tools.arxiv import ArxivQueryRun
        tool = ArxivQueryRun()
        for attempt in range(3):
            try:
                result = run_with_wait(lambda: tool.run(query), timeout=15)
                return result[:4000]
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                return f"Ошибка arXiv: {e}"
        return "Ошибка arXiv: превышено число попыток"
    except ImportError:
        return "Не установлен langchain_community."
    except Exception as e:
        return f"Ошибка arXiv: {e}"


# ============================================================
#  stackoverflow / багфикс
# ============================================================
@register("багфикс")
def stackoverflow(args):
    """Поиск решения на StackExchange по тексту ошибки.

    Ходит напрямую в StackExchange API (без ключа, публичный лимит).
    Возвращает: топ-3 вопроса + ссылки + принятый ответ (если есть).

    Параметры:
      запрос   — текст ошибки или вопрос (обязательный)
      сайт     — сайт SE (по умолч. stackoverflow; можно askubuntu, superuser)
      ответов  — сколько ответов показать для лучшего вопроса (по умолч. 1)

    Примеры:
      {"багфикс": {"запрос": "TypeError NoneType is not subscriptable"}}
      {"багфикс": {"запрос": "python sort dict by value", "сайт": "stackoverflow"}}
    """
    import urllib.parse as _up_so
    query = args.get("запрос", "")
    if not query:
        return "Ошибка: не указан запрос"
    site = (args.get("сайт") or "stackoverflow").strip()
    try:
        n_answers = int(args.get("ответов", 1) or 1)
    except (TypeError, ValueError):
        n_answers = 1
    n_answers = max(0, min(n_answers, 3))

    try:
        from _tor import ensure_tor as _et
        _proxies = None
        if _et():
            _proxies = {"http": "socks5h://127.0.0.1:9050",
                        "https": "socks5h://127.0.0.1:9050"}
    except Exception:
        _proxies = None

    _base = "https://api.stackexchange.com/2.3"
    _params = {
        "order": "desc",
        "sort": "relevance",
        "q": query,
        "site": site,
        "pagesize": 5,
        "filter": "withbody",
    }
    try:
        r = requests.get(_base + "/search/advanced",
                         params=_params, proxies=_proxies, timeout=30)
        if r.status_code != 200:
            return "Ошибка StackExchange API: HTTP " + str(r.status_code)
        data = r.json()
    except Exception as e:
        return "Ошибка StackExchange: " + str(e)

    items = data.get("items", [])
    if not items:
        return "Ничего не найдено на " + site + " по запросу: " + query

    import re as _re_so
    import html as _html_so

    def _clean_html(s):
        if not s:
            return ""
        # <p> → перевод строки, <code>...</code> → как есть, остальное вырезаем
        s = _re_so.sub(r"</p>", "\n", s, flags=_re_so.IGNORECASE)
        s = _re_so.sub(r"<br\s*/?>", "\n", s, flags=_re_so.IGNORECASE)
        s = _re_so.sub(r"<[^>]+>", "", s)
        s = _html_so.unescape(s)
        s = _re_so.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    lines = []
    for it in items[:3]:
        title = _html_so.unescape(it.get("title", "?"))
        link = it.get("link", "")
        score = it.get("score", 0)
        ans_count = it.get("answer_count", 0)
        accepted = it.get("accepted_answer_id")
        is_answered = it.get("is_answered")
        _flags = []
        if accepted:
            _flags.append("✅ принят")
        if is_answered:
            _flags.append("есть ответ")
        _flag_s = (" [" + ", ".join(_flags) + "]") if _flags else ""
        lines.append("📌 " + title + _flag_s)
        lines.append("   ↑" + str(score) + " · ответов: " + str(ans_count))
        lines.append("   " + link)

        # принятый ответ
        if n_answers > 0 and accepted:
            try:
                ra = requests.get(
                    _base + "/answers/" + str(accepted),
                    params={"site": site, "filter": "withbody"},
                    proxies=_proxies, timeout=30)
                if ra.status_code == 200:
                    _adata = ra.json()
                    _aitems = _adata.get("items", [])
                    if _aitems:
                        _abody = _clean_html(_aitems[0].get("body", ""))
                        if _abody:
                            lines.append("")
                            lines.append("   ─── Принятый ответ ───")
                            for _ln in _abody.split("\n")[:20]:
                                lines.append("   " + _ln)
            except Exception:
                pass
        lines.append("")

    return "\n".join(lines).rstrip()


# ============================================================
#  wolfram / исчисление
# ============================================================
@register(["вольфрам", "исчисление"])
def wolfram(args):
    """Запрос в Wolfram Alpha: вычисления, факты, единицы, даты.

    Параметры:
      запрос — что посчитать или найти (обязательный)

    Алиас: исчисление.

    Примеры:
      {"вольфрам": {"запрос": "integrate x^2 dx"}}
      {"вольфрам": {"запрос": "100 USD in EUR"}}
      {"исчисление": {"запрос": "distance Earth to Mars"}}
    """
    query = args.get("запрос", "")
    api_key = args.get("апи_ключ") or ensure_api_key(
        "wolfram",
        prompt="🔑 API-ключ 'wolfram' не найден. Введи (Enter — пропустить): ",
    )
    if not query:
        return "Ошибка: не указан запрос"
    if not api_key:
        return "Ошибка: требуется API-ключ Wolfram Alpha"
    try:
        encoded = urllib.parse.quote(query)
        url = f"http://api.wolframalpha.com/v1/result?appid={api_key}&i={encoded}"

        def ask():
            r = requests.get(url, timeout=60)
            if r.status_code != 200:
                return f"Ошибка HTTP {r.status_code}: {r.text[:200]}"
            return r.text.strip()

        return run_with_wait(ask, timeout=70)[:4000]
    except Exception as e:
        return f"Ошибка Wolfram Alpha: {e}"


# ============================================================
#  паста / pastebin
# ============================================================
@register("паста")
def pastebin(args):
    """Выложить текст на pastebin.com и получить ссылку.

    Параметры:
      текст     — содержимое (обязательный), алиасы: содержание, код
      заголовок — название пасты (опционально)
      язык      — подсветка синтаксиса (по умолчанию text)
      срок      — как долго хранить: 10M / 1H / 1D / 1W / 2W / 1M
                  (по умолчанию 1D)

    Примеры:
      {"паста": {"текст": "print(1)"}}
      {"паста": {"текст": "SELECT 1", "язык": "sql", "срок": "1W"}}
    """
    text = args.get("текст") or args.get("содержание") or args.get("код") or ""
    if not text:
        return "❌ Укажите текст."
    from _actions.tools_web import pastebin_post
    return pastebin_post(
        API_KEYS.get("pastebin"),
        text,
        args.get("заголовок", ""),
        args.get("язык", "text"),
        args.get("срок", "1D"),
    )
