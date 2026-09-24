# web_search.py — агрегатор: DuckDuckGo (ddgs) + Tavily
# v2.0: multi-query, дедуп по URL, ранжирование по релевантности
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
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = s.replace("\u00a0", " ")
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
    # одна строка, до 512 символов
    one = re.sub(r"\s+", " ", text).strip(" -")
    if len(one) > 512:
        one = one[:512].rstrip() + "..."
    return one or "[Описание отсутствует]"


def _clean_title(title: str, url: str) -> str:
    title = _strip_html(title or "")
    if not title:
        title = url.split("/")[-1] or "Без названия"
    return title


def _dns_retry(func, retries=4, delays=(0.5, 1.5, 3.0, 5.0)):
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
            for i, r in enumerate(d.text(query, max_results=max_results)):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                title = _clean_title(r.get("title", ""), url)
                snippet = clean_snippet(r.get("body", "") or r.get("snippet", ""))
                results.append({
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    # позиция в выдаче -> псевдо-score (1.0, 0.5, 0.33, ...)
                    "score": 1.0 / (i + 1),
                    "src": "ddg",
                })
        return results
    except Exception as e:
        print(f"[!] Ошибка DuckDuckGo: {e}", file=sys.stderr)
        return []


def _tor_is_up(host="127.0.0.1", port=9050, timeout=0.4):
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
    if not _tor_is_up():
        return []

    def _do_request():
        import requests
        proxies = {"http": "socks5h://127.0.0.1:9050",
                   "https": "socks5h://127.0.0.1:9050"}
        return requests.post(
            "https://api.tavily.com/search",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            json={
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
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "score": score,
                "src": "tavily",
            })
        return results
    except Exception as e:
        print(f"[!] Ошибка Tavily: {e}", file=sys.stderr)
        return []


def aggregate_results(results_lists: List[List[Dict]]) -> List[Dict]:
    """Дедуп по URL + ранжирование.

    relevance = max(score) + 0.5*(сколько разных запросов нашли этот URL - 1)
              + 0.1*ddg_score (если URL только в DDG, без Tavily)
    """
    agg: Dict[str, Dict] = {}
    for q_idx, results in enumerate(results_lists):
        for item in results:
            url = item.get("url")
            if not url:
                continue
            if url not in agg:
                agg[url] = {
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "sources": set(),
                    "queries": set(),
                    "max_score": 0.0,
                    "ddg_score": 0.0,
                }
            e = agg[url]
            if len(item.get("snippet", "")) > len(e["snippet"]):
                e["snippet"] = item["snippet"]
            if len(item.get("title", "")) > len(e["title"]):
                e["title"] = item["title"]
            src = item.get("src")
            if src:
                e["sources"].add(src)
            e["queries"].add(q_idx)
            sc = item.get("score") or 0.0
            if src == "tavily":
                e["max_score"] = max(e["max_score"], sc)
            elif src == "ddg":
                e["ddg_score"] = max(e["ddg_score"], sc)

    out = []
    for url, e in agg.items():
        n_q = len(e["queries"])
        # Движки равноправны: DDG даёт позицию (1.0, 0.5, 0.33, ...),
        # Tavily — свой score (0..1). Если URL нашли оба —
        # score складываются (кап на 1.0). Если один — берём его score.
        tav = e["max_score"] if "tavily" in e["sources"] else 0.0
        ddg = e["ddg_score"] if "ddg" in e["sources"] else 0.0
        rel = tav + ddg
        # бонус за совпадение в нескольких запросах
        if n_q > 1:
            rel += 0.5 * (n_q - 1)
        if rel > 1.0:
            rel = 1.0
        if rel < 0.0:
            rel = 0.0
        e["relevance"] = round(rel, 4)
        e["queries_count"] = n_q
        e["sources"] = sorted(s for s in e["sources"] if s)
        out.append(e)
    out.sort(key=lambda x: -x["relevance"])
    return out


def search_multi(queries: List[str], tavily_key: str = None,
                 max_results: int = 10, max_workers: int = 2) -> List[Dict]:
    """Прогнать все запросы через DDG+Tavily, объединить, дедуп, ранжировать.

    Запросы идут параллельно, но не больше max_workers одновременно
    (DDG и Tor не любят флуд — 2 безопасно).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    queries = [(q or "").strip() for q in queries if (q or "").strip()]
    if not queries:
        return []

    # Tavily ходит только через Tor. Если Tor не поднят —
    # задачи Tavily не создаём (иначе плодятся пустые потоки).
    _tor_up = _tor_is_up()
    if tavily_key and not _tor_up:
        print("[i] Tor не поднят — Tavily пропущен, только DDG",
              file=sys.stderr)
        tavily_key = None

    # формируем задания: (query, kind)
    tasks = []
    for q in queries:
        tasks.append((q, "ddg"))
        if tavily_key:
            tasks.append((q, "tavily"))

    results_by_task = [None] * len(tasks)

    def _run(idx, q, kind):
        if kind == "ddg":
            return fetch_ddgs(q, max_results)
        return fetch_tavily(q, tavily_key, max_results)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run, i, q, kind): i
            for i, (q, kind) in enumerate(tasks)
        }
        for f in as_completed(futures):
            i = futures[f]
            try:
                results_by_task[i] = f.result()
            except Exception as e:
                print(f"[!] Задача #{i} ({tasks[i]}): {e}", file=sys.stderr)
                results_by_task[i] = []

    lists = [r for r in results_by_task if r]
    return aggregate_results(lists)


def main():
    parser = argparse.ArgumentParser(
        description="Агрегированный веб-поиск (DuckDuckGo + Tavily)"
    )
    parser.add_argument("query", nargs="*",
                        help="Поисковый запрос (один)")
    parser.add_argument("--queries",
                        help='Несколько запросов через "|||": q1|||q2|||q3')
    parser.add_argument("--tavily-key", help="API ключ Tavily")
    parser.add_argument("--max-results", type=int, default=10)
    args = parser.parse_args()

    if args.queries:
        queries = [q.strip() for q in args.queries.split("|||") if q.strip()]
    elif args.query:
        queries = [" ".join(args.query)]
    else:
        parser.error("нужен query или --queries")

    all_results = search_multi(queries, args.tavily_key, args.max_results)

    for item in all_results:
        print(item["title"])
        print(item["snippet"])
        print(f"🔗 {item['url']}")
        srcs = ",".join(item.get("sources") or [])
        print(f"   [{srcs} | rel={item.get('relevance')}]")
        print()


if __name__ == "__main__":
    main()