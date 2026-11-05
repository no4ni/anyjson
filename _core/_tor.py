"""Управление Tor."""
import atexit
import os
import socket
import subprocess
import time

from _config import _TOR_PATH, _TORRC
from _utils import log

_tor_process = None
_tor_available = False


def start_tor(timeout: float = 15.0) -> bool:
    global _tor_process, _tor_available
    if _tor_process is not None:
        if _tor_process.poll() is None:
            return True
        _tor_process = None

    if not os.path.exists(_TOR_PATH):
        log(f"Tor не найден: {_TOR_PATH}")
        return False

    if not _TORRC.exists():
        log(f"torrc с мостами не найден: {_TORRC}")
        return False

    try:
        _tor_process = subprocess.Popen(
            [_TOR_PATH, "-f", str(_TORRC), "--DisableNetwork", "0"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"Tor запущен, ожидание bootstrap (до {timeout:.0f} сек)...")
        ok = False
        deadline = time.monotonic() + timeout
        while True:
            s = socket.socket()
            s.settimeout(1)
            try:
                if s.connect_ex(('127.0.0.1', 9050)) == 0:
                    ok = True
                    break
            finally:
                s.close()
            if _tor_process.poll() is not None:
                log(f"Tor завершился (код {_tor_process.returncode}) до bootstrap")
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        _tor_available = ok
        log("Tor готов: порт 9050 слушает" if ok else f"Tor не поднялся за {timeout:.0f} сек — порт 9050 не слушает")
        return ok
    except Exception as e:
        log(f"Ошибка запуска Tor: {e}")
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
        with Controller.from_port(port=9051, connect_timeout=3) as c:
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
    if _tor_available:
        return True
    if start_tor():
        _tor_available = True
        return True
    return False


def cleanup_tor():
    global _tor_process
    if _tor_process and _tor_process.poll() is None:
        _tor_process.terminate()


atexit.register(cleanup_tor)
