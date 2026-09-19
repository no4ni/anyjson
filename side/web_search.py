# web_search.py — агрегатор: DuckDuckGo (ddgs) + Tavily
import sys
import io
import re
import html as _html
import argparse
from typing import List, Dict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _strip_html(s: str) -> str:
    """Убрать HTML-теги (заменить на пробел) и развернуть entity."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = s.replace("\u00a0", " ")  # NBSP -> обычный пробел
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def clean_snippet(text: str) -> str:
    if not text:
        return "[Описание отсутствует]"
    text = _strip_html(text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 - \2\n", text)
    text = re.sub(r"\(\)|\[\]|\"", "", text)
    text = re.sub(r"\.{2,}", " ", text)
    text = re.sub(r"\s*-\s*\n\s*-\s*", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    lines = [line.strip(" -") for line in text.split("\n") if line.strip()]
    cleaned = "\n".join(lines)
    return cleaned or "[Описание отсутствует]"


def _clean_title(title: str, url: str) -> str:
    title = _strip_html(title or "")
    if not title:
        title = url.split("/")[-1] or "Без названия"
    return title


def _dns_retry(func, retries=4, delays=(0.5, 1.5, 3.0, 5.0)):
    """Повторить запрос, если DNS/сеть временно отвалились."""
    import time as _t
    last = None
    for i in range(retries):
        try:
            return func()
        except Exception as e:
            msg = str(e).lower()
            transient = (
                "getaddrinfo" in msg
                or "nameresolution" in msg
                or "max retries" in msg
                or "connection reset" in msg
                or "timed out" in msg
                or "timeout" in msg
            )
            last = e
            if not transient or i == retries - 1:
                raise
            _t.sleep(delays[i] if i < len(delays) else 5.0)
    raise last


def fetch_ddgs(query: str, max_results: int = 10) -> List[Dict]:
    try:
        from ddgs import DDGS
    except ImportError as e:
        print(f"[!] Не удалось импортировать ddgs: {e}", file=sys.stderr)
        return []
    try:
        results = []
        with DDGS() as d:
            for r in d.text(query, max_results=max_results):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                title = _clean_title(r.get("title", ""), url)
                snippet = clean_snippet(r.get("body", "") or r.get("snippet", ""))
                results.append({"url": url, "title": title, "snippet": snippet})
        return results
    except Exception as e:
        print(f"[!] Ошибка DuckDuckGo: {e}", file=sys.stderr)
        return []


def _tor_is_up(host="127.0.0.1", port=9050, timeout=0.4):
    """Проверить, слушает ли уже Tor. НЕ запускает его."""
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def fetch_tavily(query: str, api_key: str, max_results: int = 10) -> List[Dict]:
    if not api_key:
        return []

    # Tavily блокирует РФ — ходим только через Tor.
    # Tor НЕ поднимаем сами: если он уже запущен — используем,
    # иначе тихо пропускаем Tavily (DDG даст результаты).
    if not _tor_is_up():
        return []

    def _do_request():
        import requests
        proxies = {"http": "socks5h://127.0.0.1:9050",
                   "https": "socks5h://127.0.0.1:9050"}
        return requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
            proxies=proxies,
            timeout=30,
        )

    try:
        resp = _dns_retry(_do_request)
        data = resp.json()
        results = []
        for item in data.get("results", []):
            url = item.get("url")
            if not url:
                continue
            title = _clean_title(item.get("title", ""), url)
            snippet = clean_snippet(item.get("content", ""))
            results.append({"url": url, "title": title, "snippet": snippet})
        return results
    except Exception as e:
        print(f"[!] Ошибка Tavily: {e}", file=sys.stderr)
        return []


def aggregate_results(results_lists: List[List[Dict]]) -> List[Dict]:
    aggregated = {}
    for results in results_lists:
        for item in results:
            url = item.get("url")
            if not url:
                continue
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            if url in aggregated:
                existing = aggregated[url]
                if len(snippet) > len(existing["snippet"]):
                    existing["snippet"] = snippet
                if len(title) > len(existing["title"]):
                    existing["title"] = title
            else:
                aggregated[url] = {"url": url, "title": title, "snippet": snippet}
    return list(aggregated.values())


def main():
    parser = argparse.ArgumentParser(
        description="Агрегированный веб-поиск (DuckDuckGo + Tavily)"
    )
    parser.add_argument("query", nargs="+", help="Поисковый запрос")
    parser.add_argument("--tavily-key", help="API ключ Tavily")
    parser.add_argument("--max-results", type=int, default=10)
    args = parser.parse_args()

    query = " ".join(args.query)
    ddgs_results = fetch_ddgs(query, args.max_results)
    tavily_results = []
    if args.tavily_key:
        tavily_results = fetch_tavily(query, args.tavily_key, args.max_results)
    all_results = aggregate_results([ddgs_results, tavily_results])

    for item in all_results:
        print(item["title"])
        print(item["snippet"])
        print(f"🔗 {item['url']}")
        print()


if __name__ == "__main__":
    main()
