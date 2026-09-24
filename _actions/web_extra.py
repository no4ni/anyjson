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
    """Команда "новости".

    Параметры: см. реализацию.
    Пример: {"новости": {}}
    """
    query = args.get("запрос", "")
    mode = args.get("провайдер", "all").lower()
    limit = args.get("количество", 10)
    language = args.get("язык", "ru")
    if not query:
        return "Ошибка: не указан запрос"

    aggregated = {}
    ddgs = None
    try:
        from ddgs_tool.ddgs_tool import ddgs as _ddgs
        ddgs = _ddgs
    except Exception:
        pass

    if mode in ("web", "all") and ddgs:
        try:
            search_query = f"новости {query}"
            raw = run_with_wait(ddgs, (search_query,))
            for item in parse_ddgs_output(raw):
                url = item.get("url")
                if not url:
                    continue
                aggregated[url] = {
                    "url": url,
                    "title": item.get("title", "") or url.split('/')[-1],
                    "snippet": item.get("snippet", "") or "[Описание отсутствует]",
                    "source": "DuckDuckGo",
                }
        except Exception as e:
            log(f"Ошибка DuckDuckGo: {e}")

    if mode in ("api", "all") and API_KEYS.get("newsapi"):
        try:
            from newsapi import NewsApiClient
            client = NewsApiClient(api_key=API_KEYS["newsapi"])
            to_date = datetime.now().date()
            from_date = to_date - timedelta(days=7)
            safe_query = quote(query.encode('utf8'))
            resp = client.get_everything(q=safe_query,
                                         from_param=from_date.isoformat(),
                                         to=to_date.isoformat(),
                                         language=language,
                                         sort_by='relevancy',
                                         page_size=min(limit, 100))
            if resp.get('status') == 'ok':
                for art in resp.get('articles', []):
                    url = art.get('url')
                    if url and url not in aggregated:
                        aggregated[url] = {
                            "url": url,
                            "title": art.get('title', '') or 'Без названия',
                            "snippet": art.get('description', '') or '[Описание отсутствует]',
                            "source": "NewsAPI",
                        }
        except Exception as e:
            log(f"Ошибка NewsAPI: {e}")

    if not aggregated:
        return "Новостей не найдено ни в одном источнике."
    results = list(aggregated.values())
    results.sort(key=lambda x: 0 if x['source'] == 'NewsAPI' else 1)
    results = results[:limit]
    lines = []
    for i, r in enumerate(results):
        lines.append(f"[{i}] {r['title']} (источник: {r['source']})")
        lines.append(r['snippet'][:300])
        lines.append(f"🔗 {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


# ============================================================
#  погода
# ============================================================
@register("погода")
def weather(args):
    """Команда "погода".

    Параметры: см. реализацию.
    Пример: {"погода": {}}
    """
    city = args.get("город", "")
    api_key = args.get("апи_ключ") or API_KEYS.get("weatherapi")
    if not city:
        return "Ошибка: не указан город"
    if not api_key:
        return "Ошибка: требуется api_key"
    try:
        url = "http://api.weatherapi.com/v1/current.json"
        resp = requests.get(url, params={"key": api_key, "q": city, "lang": "ru"}, timeout=10)
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
    """Команда "энциклопедия".

    Параметры: см. реализацию.
    Пример: {"энциклопедия": {}}
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
    """Команда "багфикс".

    Параметры: см. реализацию.
    Пример: {"багфикс": {}}
    """
    query = args.get("запрос", "")
    if not query:
        return "Ошибка: не указан запрос"
    try:
        from langchain_community.utilities.stackexchange import StackExchangeAPIWrapper
        from langchain_community.tools.stackexchange.tool import StackExchangeTool
        wrapper = StackExchangeAPIWrapper()
        tool = StackExchangeTool(api_wrapper=wrapper)
        return run_with_wait(lambda: tool.run(query), timeout=15)[:4000]
    except ImportError:
        return "Не установлен langchain_community."
    except Exception as e:
        return f"Ошибка StackExchange: {e}"


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
    """Команда "паста".

    Параметры: см. реализацию.
    Пример: {"паста": {}}
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
