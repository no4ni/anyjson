"""Веб и прокси: прокси-менеджер, Pastebin, Wikipedia, view_page.

Пути (wikiget, canonical_all) — через _tools_core.
"""
import json
import random
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from _actions import _tools_core


# ---------- Прокси ----------
def _test_proxy(proxy_str, timeout=5):
    """True, если прокси отвечает на тестовый HTTP-запрос."""
    try:
        proxies = {"http": proxy_str, "https": proxy_str}
        resp = requests.get("http://httpbin.org/ip",
                            proxies=proxies, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _is_proxy_item(item, ip, port):
    if not isinstance(item, dict):
        return False
    item_ip = item.get("ip")
    item_port = item.get("port")
    return (item_ip == ip and
            (item_port == port or str(item_port) == str(port)))


def _remove_proxy_from_data(data, ip, port):
    modified = False
    if isinstance(data, dict):
        if "value" in data and isinstance(data["value"], list):
            new_list = []
            for item in data["value"]:
                if _is_proxy_item(item, ip, port):
                    modified = True
                    continue
                new_list.append(item)
            data["value"] = new_list
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                modified |= _remove_proxy_from_data(value, ip, port)
    elif isinstance(data, list):
        new_list = []
        for item in data:
            if isinstance(item, dict) and _is_proxy_item(item, ip, port):
                modified = True
                continue
            if isinstance(item, (list, dict)):
                modified |= _remove_proxy_from_data(item, ip, port)
            new_list.append(item)
        data[:] = new_list
    return modified


def get_local_proxies(proxy_dir, count=5, check=False, proxy_type=None):
    """Читает локальные JSON-файлы с прокси (формат {'value': [...]})."""
    proxy_dir = Path(proxy_dir)
    if not proxy_dir.exists():
        return []

    json_files = list(proxy_dir.rglob("*.json"))
    proxies = []
    seen = set()

    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        items = []
        if isinstance(data, dict) and "value" in data:
            items = data["value"]
        elif isinstance(data, list):
            items = data

        for item in items:
            if not isinstance(item, dict):
                continue
            ip = item.get("ip")
            port = item.get("port")
            protocol = item.get("protocol") or (
                "https" if item.get("https") else "http")
            if not ip or not port:
                continue
            key = (ip, port)
            if key not in seen:
                seen.add(key)
                proxies.append(f"{protocol}://{ip}:{port}")

    if check:
        tested = []
        for proxy in proxies:
            if _test_proxy(proxy):
                tested.append(proxy)
        proxies = tested

    if count and len(proxies) > count:
        proxies = random.sample(proxies, count)

    return proxies


def remove_local_proxy(proxy_dir, target_proxy):
    """Физически удаляет прокси (ip:port) из всех JSON-файлов."""
    proxy_dir = Path(proxy_dir)
    if not proxy_dir.exists():
        return 0

    clean = target_proxy.split("://")[-1]
    if ":" not in clean:
        return 0
    ip, port_str = clean.split(":", 1)
    try:
        port = int(port_str)
    except ValueError:
        return 0

    files_removed = 0
    for jf in proxy_dir.rglob("*.json"):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if _remove_proxy_from_data(data, ip, port):
            try:
                with open(jf, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                files_removed += 1
            except Exception:
                pass
    return files_removed


def fetch_free_proxies(max_count=5, timeout=10):
    sources = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=https&timeout=10000&country=all",
    ]
    proxies = []
    for url in sources:
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                lines = resp.text.strip().split("\r\n")
                for line in lines:
                    if ":" in line:
                        ip, port = line.split(":")[:2]
                        proxies.append({
                            "http": f"http://{ip}:{port}",
                            "https": f"http://{ip}:{port}",
                        })
                        if len(proxies) >= max_count:
                            return proxies
        except Exception:
            continue
    return proxies if proxies else [None]


# ---------- Pastebin ----------
def pastebin_post(api_key, text, title="", syntax="text", expiration="1D"):
    # ЗАЩИТА
    try:
        from _actions.tools_leak_guard import scan_for_secrets, format_report
        _hits = scan_for_secrets(text)
        if _hits:
            return format_report(_hits)
    except ImportError:
        pass
    """Публикует текст на Pastebin.com и возвращает URL."""
    if not api_key:
        return "❌ Нет API-ключа Pastebin (api_keys.json → pastebin)."
    try:
        data = {
            "api_dev_key": api_key,
            "api_option": "paste",
            "api_paste_code": text,
            "api_paste_name": title,
            "api_paste_format": syntax,
            "api_paste_expire_date": expiration,
            "api_paste_private": "1",
        }
        resp = requests.post("https://pastebin.com/api/api_post.php",
                             data=data, timeout=30)
        if resp.status_code == 200 and resp.text.startswith("https://pastebin.com/"):
            return resp.text.strip()
        return f"Ошибка Pastebin: {resp.text.strip()}"
    except Exception as e:
        return f"Ошибка запроса к Pastebin: {e}"


# ---------- Wikipedia ----------
def find_canonical(query):
    """Ищет каноничное имя статьи по редиректам."""
    canonical_path = _tools_core.get_canonical_all_path()
    if not canonical_path or not canonical_path.exists():
        return None
    query_lower = query.lower()
    with open(canonical_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            canon_name = parts[0].strip()
            rest = parts[1].strip()
            if canon_name.lower() == query_lower:
                return canon_name
            rest_parts = rest.split()
            if len(rest_parts) >= 2:
                redirects = rest_parts[1:]
                for r in redirects:
                    if r.lower() == query_lower:
                        return canon_name
    return None


def get_wikipedia_article(article_name):
    """Читает статью через wikiget.exe."""
    import subprocess
    import os
    wikiget_path = _tools_core.get_wikiget_path()
    if not wikiget_path or not wikiget_path.exists():
        return "❌ wikiget не найден. Укажите путь к wikiget.exe в config.json."
    try:
        result = subprocess.run(
            [str(wikiget_path), article_name],
            capture_output=True,
            text=False,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = None
        for enc in ["utf-8", "cp866", "cp1251", "latin-1"]:
            try:
                output = result.stdout.decode(enc).strip()
                break
            except UnicodeDecodeError:
                continue
        if output is None:
            output = result.stdout.decode("utf-8", errors="replace").strip()
        if len(output) > 1536:
            return output[:1536] + "\n... (обрезано, используйте 'осмотреть' для полного текста)"
        return output
    except Exception as e:
        return f"❌ Ошибка вызова wikiget: {e}"


# ---------- view_page ----------
def view_page(url, max_length=2048, proxies=None, verify=True):
    # ЗАЩИТА
    try:
        from _actions.tools_leak_guard import scan_for_secrets, format_report
        _hits = scan_for_secrets(url)
        if _hits:
            return format_report(_hits)
    except ImportError:
        pass
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=30,
                            proxies=proxies, verify=verify)
        if resp.status_code != 200:
            return f"Ошибка HTTP {resp.status_code}"
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator="\n", strip=True)
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            link_text = a.get_text(strip=True) or href
            links.append(f"{link_text}: {href}")
        result = text
        if links:
            result += "\n\nСсылки:\n" + "\n".join(links[:50])
        if max_length and len(result) > max_length:
            result = result[:max_length] + "..."
        return result
    except ImportError:
        return "Библиотеки requests или BeautifulSoup не установлены."
    except Exception as e:
        return f"Ошибка загрузки страницы: {e}"
