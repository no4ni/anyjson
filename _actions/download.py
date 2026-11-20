"""Скачать файл по URL."""
import shutil
import subprocess
import sys
import time
from pathlib import Path

from _actions import register


def _fmt_size(b):
    if b is None:
        return "?"
    if b > 1024 * 1024:
        return format(b / (1024 * 1024), ".1f") + " МБ"
    if b > 1024:
        return format(b / 1024, ".1f") + " КБ"
    return str(b) + " Б"


def _download_curl(url, dst_p, use_tor, timeout):
    """Скачать через curl.exe с живым прогрессом.

    Возвращает (rc, final_url), (False, None) при Ctrl+C, (None, None) при исключении.
    """
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        return None, None
    args = [
        curl, "-L", "-4", "--retry", "1", "--retry-delay", "1",
        "-C", "-",  # докачка с места обрыва
        "--connect-timeout", "15",
        "--max-time", str(timeout),
        "-w", "\n%{url_effective}",
        "-A", ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36"),
        "-o", str(dst_p),
        url,
    ]
    if use_tor:
        args[1:1] = ["--socks5-hostname", "127.0.0.1:9050"]
    try:
        # прогресс curl идёт в stderr (наследуется в консоль), stdout ловим ради %{url_effective}
        p = subprocess.run(args, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        eff = (p.stdout or "").strip().splitlines()
        final_url = eff[-1].strip() if eff else url
        return p.returncode, final_url
    except KeyboardInterrupt:
        sys.stderr.write("\n⏹ Скачивание прервано (Ctrl+C)\n")
        return False, None
    except Exception as e:
        sys.stderr.write(f"\n[!] curl error: {e}\n")
        return None, None


def _download_requests(url, dst_p, use_tor, timeout):
    """Fallback: requests с периодическим выводом прогресса в stderr."""
    try:
        import requests
    except ImportError:
        return None, "Не установлен пакет requests."

    proxies = {"http": None, "https": None}
    if use_tor:
        proxies = {"http": "socks5h://127.0.0.1:9050",
                   "https": "socks5h://127.0.0.1:9050"}

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
    }
    try:
        r = requests.get(url, headers=headers, proxies=proxies,
                         stream=True, timeout=(15, timeout))
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last_shown = 0
        t0 = time.time()
        with open(dst_p, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last_shown >= 1.0:
                    last_shown = now
                    speed = done / max(0.1, now - t0)
                    if total > 0:
                        pct = done / total * 100
                        _w = 20
                        filled = _w * int(pct) // 100
                        bar = "=" * filled + ">" + "." * (_w - filled - 1)
                        line = ("\r📥 " + format(pct, ".1f") + "% ["
                                + bar + "] "
                                + _fmt_size(done) + " / " + _fmt_size(total)
                                + "  " + _fmt_size(int(speed)) + "/с")
                    else:
                        line = ("\r📥 " + _fmt_size(done)
                                + "  " + _fmt_size(int(speed)) + "/с")
                    sys.stderr.write(line)
                    sys.stderr.flush()
        sys.stderr.write("\r" + " " * 100 + "\r")
        sys.stderr.flush()
        return done, None, (r.url or url)
    except KeyboardInterrupt:
        sys.stderr.write("\n⏹ Скачивание прервано (Ctrl+C)\n")
        return None, "прервано пользователем", None
    except Exception as e:
        return None, "Ошибка скачивания: " + type(e).__name__ + ": " + str(e), None


@register(["скачать", "скачай"])
def download_file(args):
    """Скачать файл по URL и сохранить на диск.

    Параметры:
      адрес         — URL файла (обязательно, если не заданы сайт + путь_на_сайте), алиасы: url, ссылка
      сайт          — домен/хост (например "https://aliyun.com"), альтернатива длинному «адрес»
      путь_на_сайте — относительный путь на сайте (например "catalog/aliyun_cu124.html")
      путь          — куда сохранить (обязательно), алиасы: сохранить_в, сохранить_как, куда
      тор           — true: скачать через Tor (по умолч. false)
      таймаут       — секунд (по умолч. 300, для curl — max-time)

    Примеры:
      {"скачать": {"адрес": "https://example.com/file.zip",
                   "путь": "E:/Jericho/downloads/file.zip"}}
      {"скачать": {"сайт": "https://aliyun.com", "путь_на_сайте": "catalog/aliyun_cu124.html", "сохранить_в": "E:/wheels/aliyun_cu124.html"}}
      {"скачать": {"url": "https://...", "путь": "E:/tmp/file.bin", "тор": true}}

    Куда сохранять: корень C:\\ и системные папки (Program Files,
    Windows, ProgramData) требуют прав администратора — запись туда
    упадёт с PermissionError. Используй E:/, D:/, папку профиля
    (C:/Users/<ты>/...) или MY_DIR агента.
    """
    url = (args.get("адрес") or args.get("url")
           or args.get("ссылка") or args.get("target") or "").strip()
    if not url:
        _site = (args.get("сайт") or args.get("site") or "").strip()
        _sub = (args.get("путь_на_сайте") or args.get("path") or "").strip()
        if _site:
            if not (_site.startswith("http://") or _site.startswith("https://")):
                _site = "https://" + _site
            url = (_site.rstrip("/") + "/" + _sub.lstrip("/")) if _sub else _site
    if not url:
        return ('Укажи адрес (или сайт + путь_на_сайте):\n'
                '  {"скачать": {"адрес": "https://...", "путь": "E:/file.bin"}}\n'
                '  {"скачать": {"сайт": "https://aliyun.com", "путь_на_сайте": "catalog/aliyun_cu124.html", "сохранить_в": "E:/wheels/aliyun_cu124.html"}}')
    if not (url.startswith("http://") or url.startswith("https://")):
        return "URL должен начинаться с http:// или https://"

    dst = (args.get("путь") or args.get("сохранить_в") or args.get("сохранить_как")
           or args.get("куда") or "").strip()
    if not dst:
        return 'Укажи путь: {"скачать": {"адрес": "' + url + '", "путь": "C:/file.bin"}}'

    dst_p = Path(dst).expanduser()
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        timeout = int(args.get("таймаут") or 300)
    except (TypeError, ValueError):
        timeout = 300

    _tor_explicit = ("тор" in args) or ("tor" in args)
    use_tor = args.get("тор") if "тор" in args else args.get("tor")
    if isinstance(use_tor, str):
        use_tor = use_tor.strip().lower() not in ("0", "false", "нет", "off", "")
    else:
        use_tor = bool(use_tor)

    # пре-проверка: пробная запись в целевую папку
    # (os.access на Windows не проверяет ACL и всегда True для существующих папок)
    if dst_p.parent.exists():
        import os as _os
        _probe = dst_p.parent / (".__dl_probe_" + str(_os.getpid()))
        try:
            _probe.write_bytes(b"")
        except (PermissionError, OSError) as _e:
            return ("❌ Нет прав на запись в " + str(dst_p.parent)
                    + " (" + type(_e).__name__ + ": " + str(_e) + "). "
                    "Корень C:\\ и системные папки требуют прав админа — "
                    "укажи E:/Jericho/... или C:/Users/<ты>/Downloads/...")
        finally:
            try:
                if _probe.exists():
                    _probe.unlink()
            except Exception:
                pass

    tor_note = ""
    if use_tor:
        try:
            from _tor import ensure_tor
            if not ensure_tor():
                use_tor = False
                tor_note = " (напрямую — Tor недоступен)"
        except Exception:
            use_tor = False
            tor_note = " (напрямую — Tor недоступен)"

    def _fmt_url_info(final_url: str) -> str:
        f_u = (final_url or "").strip()
        if f_u and f_u.rstrip("/") != url.rstrip("/"):
            return f" · {url} → {f_u} (редирект)"
        return f" · {url}"

    def _try_download(via_tor: bool):
        tag = " (через Tor)" if via_tor else tor_note
        curl_res, curl_final = _download_curl(url, dst_p, via_tor, timeout)
        if curl_res is False:
            return "⏹ Скачивание прервано (Ctrl+C).", None
        if curl_res == 0 and dst_p.exists():
            sz = dst_p.stat().st_size
            return "✅ Скачано: " + _fmt_size(sz) + tag + _fmt_url_info(curl_final) + " -> " + str(dst_p), None
        done_b, req_err, req_final = _download_requests(url, dst_p, via_tor, timeout)
        if not req_err:
            return "✅ Скачано: " + _fmt_size(done_b) + tag + _fmt_url_info(req_final) + " -> " + str(dst_p), None
        return None, req_err

    ok_msg, err = _try_download(bool(use_tor))
    if ok_msg:
        return ok_msg

    # Если скачивание через Tor сорвалось (узел/мост недоступен) — пробуем напрямую без Tor
    if use_tor:
        tor_note = " (напрямую после сбоя Tor)"
        ok_direct, err_direct = _try_download(False)
        if ok_direct:
            return ok_direct
        return err + "\nПопытка напрямую без Tor также не удалась: " + str(err_direct)

    # Если прямое скачивание упало (DNS/блокировка) и тор не запрещён явно ("тор": false) — пробуем через Tor
    if not _tor_explicit:
        try:
            from _tor import ensure_tor
            if ensure_tor():
                ok_tor, err_tor = _try_download(True)
                if ok_tor:
                    return ok_tor.replace(" (через Tor)", " (через Tor после сбоя прямого доступа)")
                return err + "\nПопытка через Tor также не удалась: " + str(err_tor)
        except Exception:
            pass

    return err
