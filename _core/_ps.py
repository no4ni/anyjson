"""PowerShell runner с сохранением CWD и асинхронным ожиданием."""
import os
import re
import subprocess
import sys
import threading
import time

from _config import BASE_DIR

_ps_cwd = str(BASE_DIR)

# Принудительно включаем UTF-8 в дочернем PowerShell.
_PS_UTF8_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def run_powershell_with_cwd(command: str, timeout: int = 30) -> str:
    global _ps_cwd
    command = command.strip()

    # cd <путь> [; ...]  — берём только путь, остальное выполняем после
    cd_match = re.match(r'^\s*cd\s+(.+)$', command, re.IGNORECASE | re.DOTALL)
    if cd_match:
        _rest_raw = cd_match.group(1).strip()
        # отрезаем всё после && ; || — это следующая команда
        _rest_part = ""
        _cut = len(_rest_raw)
        for _sep in ("&&", "||", ";"):
            _i = _rest_raw.find(_sep)
            if _i != -1 and _i < _cut:
                _cut = _i
        if _cut < len(_rest_raw):
            _rest_part = _rest_raw[_cut:].lstrip("&|; ").strip()
            _rest_raw = _rest_raw[:_cut].strip()

        # убираем cmd-стиль /d (в PowerShell его нет)
        if _rest_raw.lower().startswith("/d "):
            _rest_raw = _rest_raw[3:].strip()
        elif _rest_raw.lower() == "/d":
            return "⚠️ cd /d без пути."

        # убираем обрамляющие кавычки
        new_dir = _rest_raw.strip('"').strip("'")

        from pathlib import Path
        new_path = Path(new_dir)
        if not new_path.is_absolute():
            new_path = Path(_ps_cwd) / new_path
        try:
            new_path = new_path.resolve()
            if not new_path.exists():
                return f"❌ Папка не найдена: {new_path}"
            if not new_path.is_dir():
                return f"❌ Это не папка: {new_path}"
            _ps_cwd = str(new_path)
            _msg = f"Текущая директория изменена на: {_ps_cwd}"
            if _rest_part:
                # выполняем остаток с новым cwd
                _tail_result = run_powershell_with_cwd(_rest_part, timeout)
                _msg += f"\n\n$ {_rest_part}\n{_tail_result}"
            return _msg
        except Exception as e:
            return f"Ошибка смены директории: {e}"

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["SSH_ASKPASS"] = "echo"

    proc = None
    try:
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command",
             _PS_UTF8_PREFIX + command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # раздельно, чтобы декодировать по-разному
            stdin=subprocess.DEVNULL,
            cwd=_ps_cwd,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        def _decode_ps(data, prefer_utf8=True):
            """Декодирует вывод PowerShell. stdout — UTF-8, stderr — cp866/cp1251."""
            if not data:
                return ""
            order = ("utf-8", "cp866", "cp1251", "cp1252") if prefer_utf8 \
                    else ("cp866", "cp1251", "utf-8", "cp1252")
            for enc in order:
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    continue
            return data.decode("utf-8", errors="replace")

        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            try:
                stdout_b, stderr_b = proc.communicate(timeout=5)
            except Exception:
                stdout_b, stderr_b = b"", b""
            partial = (_decode_ps(stdout_b) + "\n" + _decode_ps(stderr_b, prefer_utf8=False)).strip()
            return (f"⏱ Таймаут {timeout} сек — процесс и его потомки убиты.\n"
                    f"Успело вывести:\n{partial[:1024] or '(пусто)'}")

        out_str = _decode_ps(stdout_b, prefer_utf8=True)
        # stderr: сначала UTF-8 (PS-ошибки от уже запущенного процесса),
        # fallback — cp866 (парсинг-ошибки до префикса)
        err_str = _decode_ps(stderr_b, prefer_utf8=True)
        output = out_str
        if err_str.strip():
            output += ("\n" + err_str) if output.strip() else err_str
        # Сохраняем переводы строк (важно для веб-поиска, файлов и т.п.).
        # Схлопываем только пробелы/табы и лишние пустые строки.
        output = re.sub(r"[ \t]+", " ", output)
        output = re.sub(r" *\n *", "\n", output)
        output = re.sub(r"\n{3,}", "\n\n", output)
        output = output.strip()
        if len(output) > 2048:
            output = output[:1024] + "\n... [обрезано] ...\n" + output[-1024:]
        return output if output else "Команда выполнена без вывода."

    except Exception as e:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return f"Ошибка выполнения: {e}"


def run_with_wait(func, args=(), timeout=None):
    result = [None]
    error = [None]
    done = threading.Event()
    start_time = time.time()
    warned = False

    def wrapper():
        try:
            result[0] = func(*args)
        except Exception as e:
            error[0] = e
        finally:
            done.set()

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()

    while not done.wait(1):
        elapsed = time.time() - start_time
        if not warned and elapsed > 5:
            sys.stdout.write("\n⏳ Иди пока воду набери...\n")
            sys.stdout.flush()
            warned = True
        if timeout and elapsed > timeout:
            return f"Операция превысила таймаут ({timeout} сек)"

    if error[0]:
        raise error[0]
    return result[0]
