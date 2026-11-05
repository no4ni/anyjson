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

    Возвращает rc (int, 0 = успех), False при Ctrl+C, None при исключении.
    """
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        return None
    args = [
        curl, "-L", "-4", "--retry", "3", "--retry-delay", "2",
        "-C", "-",  # докачка с места обрыва
        "--connect-timeout", "30",
        "--max-time", str(timeout),
        "-A", ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36"),
        "-o", str(dst_p),
        url,
    ]
    if use_tor:
        args[1:1] = ["--socks5-hostname", "127.0.0.1:9050"]
    try:
        # прогресс curl идёт в stderr, наследуем его — пользователь видит живой бар
        rc = subprocess.call(args)
    except KeyboardInterrupt:
        sys.stderr.write("\n⏹ Скачивание прервано (Ctrl+C)\n")
        return False
    except Exception as e:
        sys.stderr.write(f"\n[!] curl error: {e}\n")
        return None
    return rc


def _download_requests(url, dst_p, use_tor, timeout):
    """Fallback: requests с периодическим выводом прогресса в stderr."""
    try:
        import requests
    except ImportError:
        return None, "Не установлен пакет requests."

    proxies = None
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
                         stream=True, timeout=timeout)
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
        return done, None
    except KeyboardInterrupt:
        sys.stderr.write("\n⏹ Скачивание прервано (Ctrl+C)\n")
        return None, "прервано пользователем"
    except Exception as e:
        return None, "Ошибка скачивания: " + type(e).__name__ + ": " + str(e)


@register(["скачать", "скачай"])
def download_file(args):
    """Скачать файл по URL и сохранить на диск.

    Параметры:
      адрес   — URL файла (обязательно), алиасы: url, ссылка
      путь    — куда сохранить (обязательно), алиасы: сохранить_как, куда
      тор     — true: скачать через Tor (по умолч. false)
      таймаут — секунд (по умолч. 300, для curl — max-time)

    Примеры:
      {"скачать": {"адрес": "https://example.com/file.zip",
                   "путь": "E:/Jericho/downloads/file.zip"}}
      {"скачать": {"url": "https://...", "путь": "E:/tmp/file.bin", "тор": true}}

    Куда сохранять: корень C:\\ и системные папки (Program Files,
    Windows, ProgramData) требуют прав администратора — запись туда
    упадёт с PermissionError. Используй E:/, D:/, папку профиля
    (C:/Users/<ты>/...) или MY_DIR агента.
    """
    url = (args.get("адрес") or args.get("url")
           or args.get("ссылка") or args.get("target") or "").strip()
    if not url:
        return 'Укажи адрес: {"скачать": {"адрес": "https://...", "путь": "C:/file.bin"}}'
    if not (url.startswith("http://") or url.startswith("https://")):
        return "URL должен начинаться с http:// или https://"

    dst = (args.get("путь") or args.get("сохранить_как")
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

    use_tor = args.get("тор") or args.get("tor")
    if isinstance(use_tor, str):
        use_tor = use_tor.strip().lower() not in ("0", "false", "нет", "off", "")

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

    tor_s = " (через Tor)" if use_tor else ""

    # 1. curl — живой прогресс
    curl_result = _download_curl(url, dst_p, use_tor, timeout)
    if curl_result is False:
        return "⏹ Скачивание прервано (Ctrl+C)."
    if curl_result == 0 and dst_p.exists():
        size = dst_p.stat().st_size
        return ("✅ Скачано: " + _fmt_size(size) + tor_s
                + " -> " + str(dst_p))

    # 2. fallback — requests
    done, err = _download_requests(url, dst_p, use_tor, timeout)
    if err:
        return err
    return ("✅ Скачано: " + _fmt_size(done) + tor_s
            + " -> " + str(dst_p))
