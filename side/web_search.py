# web_search.py — мульти-поисковик: Bing, DDG HTML/Lite, SearXNG, AnySearch, Exa, Ollama, Tavily
import sys
import io
import os
import re
import json
import base64
import html as _html
import socket
import argparse
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

def _enable_ipv4_fallback():
    try:
        import socket, urllib3.util.connection as _uconn
        _orig_gai = _uconn.allowed_gai_family
        def _prefer_ipv4():
            return socket.AF_INET
        _uconn.allowed_gai_family = _prefer_ipv4
    except Exception:
        pass


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"}


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def clean_snippet(text: str) -> str:
    if not text:
        return "[Описание отсутствует]"
    one = _strip_html(text)
    one = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", one)
    one = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 - \2", one)
    one = re.sub(r"\s+", " ", one).strip(" -")
    if len(one) > 512:
        one = one[:512].rstrip() + "..."
    return one or "[Описание отсутствует]"


def _clean_title(title: str, url: str) -> str:
    t = _strip_html(title or "")
    return t or url.split("/")[-1] or "Без названия"


def _extract_bing_url(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    decoded = _html.unescape(href)
    try:
        parsed = urllib.parse.urlparse(decoded)
    except Exception:
        return decoded
    if not parsed.hostname or not parsed.hostname.endswith("bing.com") or not parsed.path.startswith("/ck/a"):
        return decoded
    u_vals = urllib.parse.parse_qs(parsed.query).get("u", [])
    wrapped = u_vals[0] if u_vals else ""
    if not wrapped.startswith("a1"):
        return decoded
    b64 = wrapped[2:].replace("-", "+").replace("_", "/")
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    try:
        target = base64.b64decode(b64).decode("utf-8", errors="strict")
        return target if target.startswith(("http://", "https://")) else decoded
    except Exception:
        return decoded


def _extract_ddg_url(rel: Optional[str]) -> Optional[str]:
    if not rel:
        return None
    dec = _html.unescape(rel)
    m = re.search(r"uddg=([^&]+)", dec)
    if m:
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            return m.group(1)
    return f"https:{dec}" if dec.startswith("//") else dec


def fetch_bing(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    r = requests.get("https://www.bing.com/search", params={"q": query, "mkt": "ru-RU"}, headers=HEADERS, timeout=12)
    r.raise_for_status()
    blocks = re.findall(r'<li class="b_algo"[\s\S]*?</li>', r.text)
    out = []
    for i, b in enumerate(blocks):
        hm = re.search(r'<a[^>]*href="(https?://[^"]+)"', b)
        tm = re.search(r'<h2[^>]*>[\s\S]*?<a[^>]*>(.*?)</a>[\s\S]*?</h2>', b)
        sm = re.search(r'<p[^>]*>([\s\S]*?)</p>', b)
        url = _extract_bing_url(hm.group(1) if hm else None)
        if not url:
            continue
        out.append({
            "url": url,
            "title": _clean_title(tm.group(1) if tm else "", url),
            "snippet": clean_snippet(sm.group(1) if sm else ""),
            "score": 1.0 / (i + 1),
            "src": "bing",
        })
        if len(out) >= max_results:
            break
    return out


def fetch_ddg_html(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    r = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=HEADERS, timeout=12)
    r.raise_for_status()
    if re.search(r"anomaly|captcha|unusual traffic|robot check", r.text[:4000], re.I):
        raise RuntimeError("DDG anti-bot")
    blocks = re.findall(r'<div class="result results_links[\s\S]*?</div>\s*</div>\s*</div>', r.text)
    out = []
    for i, b in enumerate(blocks):
        um = re.search(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"', b)
        tm = re.search(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', b)
        sm = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', b)
        url = _extract_ddg_url(um.group(1) if um else None)
        if not url:
            continue
        out.append({
            "url": url,
            "title": _clean_title(tm.group(1) if tm else "", url),
            "snippet": clean_snippet(sm.group(1) if sm else ""),
            "score": 1.0 / (i + 1),
            "src": "ddg_html",
        })
        if len(out) >= max_results:
            break
    return out


def fetch_ddg_lite(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    r = requests.get("https://lite.duckduckgo.com/lite/", params={"q": query}, headers=HEADERS, timeout=12)
    r.raise_for_status()
    if re.search(r"anomaly|captcha|unusual traffic|robot check", r.text[:4000], re.I):
        raise RuntimeError("DDG Lite anti-bot")
    links = re.findall(r'<a[^>]*class=[\'"]result-link[\'"][^>]*>[\s\S]*?</a>', r.text)
    snips = re.findall(r'class=[\'"]result-snippet[\'"][^>]*>([\s\S]*?)</td>', r.text)
    out = []
    for i, tag in enumerate(links):
        hm = re.search(r'href="([^"]*)"', tag)
        tm = re.search(r'class=[\'"]result-link[\'"][^>]*>(.*?)</a>', tag)
        url = _extract_ddg_url(hm.group(1) if hm else None)
        if not url:
            continue
        out.append({
            "url": url,
            "title": _clean_title(tm.group(1) if tm else "", url),
            "snippet": clean_snippet(snips[i] if i < len(snips) else ""),
            "score": 1.0 / (i + 1),
            "src": "ddg_lite",
        })
        if len(out) >= max_results:
            break
    return out


SEARXNG_INSTANCES = [
    "https://opnxng.com",
    "https://priv.au",
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://search.inetol.net",
    "https://paulgo.io",
]


def fetch_searxng(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    for base in SEARXNG_INSTANCES:
        try:
            r = requests.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=8,
            )
            if not r.ok:
                continue
            out = []
            for i, item in enumerate(r.json().get("results", [])):
                url = item.get("url")
                if not url:
                    continue
                out.append({
                    "url": url,
                    "title": _clean_title(item.get("title", ""), url),
                    "snippet": clean_snippet(item.get("content", "")),
                    "score": 1.0 / (i + 1),
                    "src": "searxng",
                })
                if len(out) >= max_results:
                    break
            if out:
                return out
        except Exception:
            continue
    return []


def fetch_anysearch(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    r = requests.post(
        "https://api.anysearch.com/v1/search",
        json={"query": query, "max_results": max_results},
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        timeout=12,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        return []
    out = []
    for i, item in enumerate((data.get("data") or {}).get("results", [])):
        url = item.get("url")
        if not url:
            continue
        out.append({
            "url": url,
            "title": _clean_title(item.get("title", ""), url),
            "snippet": clean_snippet(item.get("snippet", "")),
            "score": 1.0 / (i + 1),
            "src": "anysearch",
        })
        if len(out) >= max_results:
            break
    return out


def fetch_exa_mcp(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    r = requests.post(
        "https://mcp.exa.ai/mcp",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "web_search_exa", "arguments": {"query": query, "numResults": max_results}}},
        timeout=18,
    )
    r.raise_for_status()
    payload = None
    for line in r.text.splitlines():
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
                break
            except Exception:
                pass
    if not payload or payload.get("error"):
        return []
    blocks_text = "\n".join(b.get("text", "") for b in (payload.get("result") or {}).get("content", []) if b.get("type") == "text")
    out = []
    for i, blk in enumerate(re.split(r"\n(?=Title:)", blocks_text)):
        um = re.search(r"^URL: (\S+)", blk, re.M)
        if not um:
            continue
        url = um.group(1)
        tm = re.search(r"^Title: (.+)", blk, re.M)
        hl = blk.split("Highlights:", 1).strip() if "Highlights:" in blk else ""
        out.append({"url": url, "title": _clean_title(tm.group(1) if tm else "", url),
                    "snippet": clean_snippet(hl), "score": 1.0 / (i + 1), "src": "exa"})
        if len(out) >= max_results:
            break
    return out


def fetch_ddgs(query: str, max_results: int = 10) -> List[Dict]:
    from ddgs import DDGS
    out = []
    with DDGS() as d:
        for i, r in enumerate(d.text(query, max_results=max_results)):
            url = r.get("href") or r.get("url") or ""
            if url:
                out.append({"url": url, "title": _clean_title(r.get("title", ""), url),
                            "snippet": clean_snippet(r.get("body", "") or r.get("snippet", "")),
                            "score": 1.0 / (i + 1), "src": "ddgs"})
    return out


def _tor_is_up(host="127.0.0.1", port=9050, timeout=0.4) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def fetch_ollama(query: str, api_key: str, max_results: int = 10) -> List[Dict]:
    if not api_key:
        return []
    import requests
    r = requests.post("https://ollama.com/api/web_search",
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                      json={"query": query, "max_results": min(max_results, 10)}, timeout=20)
    r.raise_for_status()
    out = []
    for i, item in enumerate(r.json().get("results", [])):
        url = item.get("url")
        if url:
            out.append({"url": url, "title": _clean_title(item.get("title", ""), url),
                        "snippet": clean_snippet(item.get("content", "") or item.get("snippet", "")),
                        "score": 1.0 / (i + 1), "src": "ollama"})
    return out


def fetch_tavily(query: str, api_key: str, max_results: int = 10) -> List[Dict]:
    if not api_key or not _tor_is_up():
        return []
    import requests
    proxies = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
    r = requests.post("https://api.tavily.com/search",
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                      json={"query": query, "max_results": max_results, "include_answer": False},
                      proxies=proxies, timeout=25)
    r.raise_for_status()
    out = []
    for item in r.json().get("results", []):
        url = item.get("url")
        if url:
            out.append({"url": url, "title": _clean_title(item.get("title", ""), url),
                        "snippet": clean_snippet(item.get("content", "")),
                        "score": float(item.get("score") or 0.5), "src": "tavily"})
    return out


def fetch_github(query: str, max_results: int = 10) -> List[Dict]:
    import requests
    r = requests.get("https://api.github.com/search/repositories",
                     params={"q": query, "per_page": min(max_results, 10)},
                     headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}, timeout=12)
    r.raise_for_status()
    out = []
    for i, it in enumerate(r.json().get("items", [])):
        url = it.get("html_url")
        if url:
            stars = f" ★{it['stargazers_count']}" if it.get("stargazers_count") else ""
            out.append({"url": url, "title": _clean_title(it.get("full_name") or it.get("name", ""), url),
                        "snippet": clean_snippet((it.get("description") or "") + stars),
                        "score": 1.0 / (i + 1), "src": "github"})
    return out


FREE_ENGINES = {"bing": fetch_bing, "anysearch": fetch_anysearch, "ddgs": fetch_ddgs,
                "github": fetch_github, "ddg_html": fetch_ddg_html, "ddg_lite": fetch_ddg_lite,
                "searxng": fetch_searxng, "exa": fetch_exa_mcp}
DEFAULT_FALLBACK_ORDER = ["bing", "anysearch", "ddgs"]


def _search_one_query(q: str, engine: str, tavily_key: str, ollama_key: str, max_results: int) -> List[Dict]:
    if engine != "auto":
        if engine == "ollama":
            return fetch_ollama(q, ollama_key, max_results)
        if engine == "tavily":
            return fetch_tavily(q, tavily_key, max_results)
        fn = FREE_ENGINES.get(engine)
        return fn(q, max_results) if fn else []
    collected: List[Dict] = []
    for name in DEFAULT_FALLBACK_ORDER:
        try:
            res = FREE_ENGINES[name](q, max_results)
            if res:
                collected.extend(res)
                break
        except Exception as e:
            if "getaddrinfo" in str(e) or "NameResolutionError" in str(e):
                _enable_ipv4_fallback()
                try:
                    res = FREE_ENGINES[name](q, max_results)
                    if res:
                        collected.extend(res)
                        break
                except Exception:
                    pass
            print(f"[!] {name}: {e}", file=sys.stderr)
    for fn, key, tag in ((fetch_ollama, ollama_key, "ollama"), (fetch_tavily, tavily_key, "tavily")):
        if key:
            try:
                collected.extend(fn(q, key, max_results))
            except Exception as e:
                if "402" not in str(e):
                    print(f"[!] {tag}: {e}", file=sys.stderr)
    return collected


def aggregate_results(lists: List[List[Dict]]) -> List[Dict]:
    merged: Dict[str, Dict] = {}
    for items in lists:
        for it in items:
            url = it.get("url")
            if not url:
                continue
            if url in merged:
                ex = merged[url]
                ex["score"] = min(1.0, ex["score"] + it.get("score", 0.2))
                if it["snippet"] not in ex["snippet"]:
                    ex["snippet"] += " | " + it["snippet"]
                if len(it["title"]) > len(ex["title"]):
                    ex["title"] = it["title"]
                if it["src"] not in ex["sources"]:
                    ex["sources"].append(it["src"])
            else:
                merged[url] = {"url": url, "title": it["title"], "snippet": it["snippet"],
                               "sources": [it["src"]], "score": it.get("score", 0.5)}
    out = list(merged.values())
    for x in out:
        x["relevance"] = round(min(1.0, x["score"]), 3)
    out.sort(key=lambda x: x["relevance"], reverse=True)
    return out


def main():
    p = argparse.ArgumentParser(description="Мульти-поиск с авто-фолбэком")
    p.add_argument("query", nargs="*")
    p.add_argument("--queries")
    p.add_argument("--engine", default="auto")
    p.add_argument("--tavily-key", default="")
    p.add_argument("--ollama-key", default="")
    p.add_argument("--max-results", type=int, default=10)
    args = p.parse_args()
    queries = [q.strip() for q in args.queries.split("|||") if q.strip()] if args.queries else ([" ".join(args.query)] if args.query else [])
    if not queries:
        p.error("нужен query или --queries")
    res_by_q: List[List[Dict]] = [[] for _ in queries]
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(queries)))) as pool:
        futs = {pool.submit(_search_one_query, q, args.engine, args.tavily_key, args.ollama_key, args.max_results): i for i, q in enumerate(queries)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                res_by_q[i] = f.result()
            except Exception as e:
                print(f"[!] Запрос #{i}: {e}", file=sys.stderr)
    for item in aggregate_results(res_by_q):
        print(item["title"])
        print(item["snippet"])
        print(f"🔗 {item['url']}")
        print(f"   [{','.join(item['sources'])} | rel={item['relevance']}]")
        print()


if __name__ == "__main__":
    main()
