"""Управление Tor."""
import atexit
import os
import socket
import subprocess
import time

from _config import _TOR_PATH, _TORRC
from _utils import log

import re as _re
import threading as _threading

_tor_process = None
_tor_available = False
_last_tor_error = ""
_TOR_BOOT = {"pct": -1, "summary": ""}


def _query_control_port():
    """Опрашивает ControlPort 9051 (если открыт). Возвращает (pct, summary)."""
    try:
        cs = socket.create_connection(("127.0.0.1", 9051), timeout=0.35)
        try:
            cs.sendall(b'AUTHENTICATE ""\r\nGETINFO status/bootstrap-phase\r\nQUIT\r\n')
            raw = cs.recv(1024).decode("utf-8", errors="ignore")
            m_pct = _re.search(r"PROGRESS=(\d+)", raw)
            m_sum = _re.search(r'SUMMARY="([^"]+)"', raw)
            if m_pct:
                return int(m_pct.group(1)), (m_sum.group(1) if m_sum else "")
        finally:
            cs.close()
    except Exception:
        pass
    return -1, ""


def _read_tor_stdout(proc):
    """Читает stdout tor.exe в фоне и обновляет _TOR_BOOT."""
    try:
        for raw_line in iter(proc.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="ignore").strip()
            m = _re.search(r"Bootstrapped (\d+)% \(([^)]+)\):\s*(.+)", line)
            if m:
                _TOR_BOOT["pct"] = int(m.group(1))
                _TOR_BOOT["summary"] = f"{m.group(2)}: {m.group(3)}"
            elif "[err]" in line or "[warn]" in line:
                _TOR_BOOT["summary"] = line[-100:]
    except Exception:
        pass


def get_tor_status():
    """Возвращает (ready_100: bool, pct: int, reason: str)."""
    s = socket.socket()
    s.settimeout(0.4)
    socks_up = False
    try:
        socks_up = (s.connect_ex(('127.0.0.1', 9050)) == 0)
    finally:
        s.close()

    if not socks_up:
        pct = _TOR_BOOT["pct"]
        if _tor_process is not None and _tor_process.poll() is None and pct >= 0:
            return False, pct, f"bootstrap {pct}% ({_TOR_BOOT['summary']})"
        return False, -1, get_tor_error()

    cp_pct, cp_sum = _query_control_port()
    if cp_pct >= 0:
        _TOR_BOOT["pct"] = cp_pct
        if cp_sum:
            _TOR_BOOT["summary"] = cp_sum

    pct = _TOR_BOOT["pct"]
    if pct == 100:
        return True, 100, "bootstrap 100% (Done)"
    if 0 <= pct < 100:
        return False, pct, f"bootstrap {pct}% ({_TOR_BOOT['summary'] or 'цепочка не готова'})"
    return True, 100, "порт 9050 открыт"


def _resolve_tor_paths():
    from pathlib import Path as _P
    exe = _P(str(_TOR_PATH)) if _TOR_PATH else None
    rc = _P(str(_TORRC)) if _TORRC else None
    if exe and exe.is_dir():
        for cand in ("tor2.exe", "tor.exe", "tor"):
            if (exe / cand).is_file():
                exe = exe / cand
                break
    if rc and rc.is_dir():
        if (rc / "torrc").is_file():
            rc = rc / "torrc"
    return (str(exe) if exe else ""), rc


def get_tor_error() -> str:
    return _last_tor_error or 'Tor не запущен. Проверь пути tor_executable / torrc: {"настроить": {}}'


def start_tor(timeout: float = 15.0) -> bool:
    global _tor_process, _tor_available, _last_tor_error
    ready, pct, reason = get_tor_status()
    if ready:
        _tor_available = True
        _last_tor_error = ""
        return True
    if _tor_process is not None:
        if _tor_process.poll() is None:
            _tor_available = False
            _last_tor_error = f"Tor застрял на {reason}"
            return False
        _tor_process = None

    tor_exe, torrc_path = _resolve_tor_paths()
    if not tor_exe or not os.path.isfile(tor_exe):
        _last_tor_error = f"не найден файл Tor ({tor_exe or 'не задан'}). Укажи путь через {{\"настроить\": {{}}}}"
        log(f"Tor не найден: {tor_exe}")
        return False

    if not torrc_path or not torrc_path.is_file():
        _last_tor_error = f"не найден файл torrc ({torrc_path or 'не задан'}). Укажи путь через {{\"настроить\": {{}}}}"
        log(f"torrc не найден: {torrc_path}")
        return False

    try:
        _TOR_BOOT["pct"] = 0
        _TOR_BOOT["summary"] = "запуск tor.exe"
        _tor_process = subprocess.Popen(
            [tor_exe, "-f", str(torrc_path), "--DisableNetwork", "0"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        _threading.Thread(target=_read_tor_stdout, args=(_tor_process,), daemon=True).start()
        import sys as _sys
        _tty = (
            not os.environ.get("ANYJSON_DAEMON")
            and not os.environ.get("ANYJSON_WEB")
            and getattr(_sys.stdout, "isatty", lambda: False)()
        )
        log(f"Tor запущен, ожидание bootstrap (до {timeout:.0f} сек)...")

        ok = False
        t_start = time.monotonic()
        eff_timeout = max(timeout, 18.0)
        deadline = t_start + eff_timeout
        while True:
            ready_now, cur_pct, cur_reason = get_tor_status()
            if ready_now:
                ok = True
                break

            if _tor_process.poll() is not None:
                _last_tor_error = f"Tor завершился с кодом {_tor_process.returncode} на {max(0, _TOR_BOOT['pct'])}% ({_TOR_BOOT['summary']})"
                log(_last_tor_error)
                break
            now_m = time.monotonic()
            if now_m >= deadline:
                p_now = max(0, _TOR_BOOT["pct"])
                _last_tor_error = f"Tor не прошёл bootstrap за {eff_timeout:.0f}с (остановился на {p_now}%: {_TOR_BOOT['summary'] or 'проверь мосты в torrc'})"
                break
            if _tty:
                el = now_m - t_start
                p_show = max(0, _TOR_BOOT["pct"])
                frac = p_show / 100.0
                filled = min(12, int(frac * 12))
                bar = "█" * filled + "░" * (12 - filled)
                try:
                    _sys.stdout.write(f"\r\033[93m⏳ Tor bootstrap [{bar}] {p_show}% {el:.1f}/{eff_timeout:.0f}с\033[0m   ")
                    _sys.stdout.flush()
                except Exception:
                    pass
            time.sleep(0.4)
        if _tty:
            try:
                _sys.stdout.write("\r" + (" " * 60) + "\r")
                _sys.stdout.flush()
            except Exception:
                pass
        _tor_available = ok
        if ok:
            _last_tor_error = ""
            log("Tor готов: порт 9050 слушает")
        else:
            log(_last_tor_error)
        return ok
    except Exception as e:
        _last_tor_error = f"ошибка запуска Tor: {e}"
        log(_last_tor_error)
        _tor_available = False
        return False


def restart_tor() -> str:
    global _tor_process, _tor_available
    if _tor_process is not None:
        try:
            _tor_process.terminate()
            _tor_process.wait(timeout=5)
        except Exception:
            try:
                _tor_process.kill()
            except Exception:
                pass
        _tor_process = None
        _tor_available = False
        log("Tor остановлен.")
    if start_tor():
        return "✅ Tor перезапущен и готов."
    return "❌ Не удалось перезапустить Tor."


def new_circuit(wait_sec: float = 2.0) -> bool:
    """Запросить у Tor новую цепочку (новый exit). Требует ControlPort 9051.

    Возвращает True, если NEWNYM отправлен, False — если ротация недоступна.
    """
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as c:
            c.authenticate()
            c.signal(Signal.NEWNYM)
        if wait_sec > 0:
            time.sleep(wait_sec)
        return True
    except ImportError:
        log("stem не установлен — ротация недоступна (pip install stem)")
        return False
    except Exception as e:
        log(f"NEWNYM не удался: {e}")
        return False


def ensure_tor() -> bool:
    global _tor_available
    ready, _, _ = get_tor_status()
    if ready:
        _tor_available = True
        return True
    _tor_available = False
    if start_tor():
        _tor_available = True
        return True
    return False


def cleanup_tor():
    global _tor_process
    if _tor_process and _tor_process.poll() is None:
        _tor_process.terminate()


atexit.register(cleanup_tor)
