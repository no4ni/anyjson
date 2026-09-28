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
