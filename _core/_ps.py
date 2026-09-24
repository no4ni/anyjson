"""PowerShell runner с сохранением CWD и асинхронным ожиданием."""
import os
import re
import subprocess
import sys
import threading
import time

from _config import BASE_DIR

def _mask(text):
    try:
        from _actions.tools_leak_guard import mask_secrets
        return mask_secrets(text)
    except Exception:
        return text

_ps_cwd = str(BASE_DIR)

# Принудительно включаем UTF-8 в дочернем PowerShell.
_PS_UTF8_PREFIX = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ""$ProgressPreference='SilentlyContinue'; ""$InformationPreference='SilentlyContinue'; ""$WarningPreference='SilentlyContinue'; ")


def _ps_hint_for_command(command: str, killed_reason: str) -> str:
    """Подсказка при таймауте/тишине — с учётом того, ЧТО именно запустили."""
    cmd_low = command.lower()
    recursive_scan = (
        ("get-childitem" in cmd_low and "-recurse" in cmd_low)
        or ("gci" in cmd_low and ("-recurse" in cmd_low or re.search(r"\s-r\b", cmd_low)))
        or re.search(r"\bdir\s+/s\b", cmd_low)
        or re.search(r"\b(findstr|find)\b.*\s/s\b", cmd_low)
        or re.search(r"\btree\b", cmd_low)
    )
    recursive_del = (
        ("remove-item" in cmd_low and "-recurse" in cmd_low)
        or re.search(r"\b(del|erase)\s+/s\b", cmd_low)
        or re.search(r"\b(rd|rmdir)\s+/s\b", cmd_low)
    )
    base = ("\n\n💡 Нужно ждать дольше? Увеличь параметр «таймаут»:\n"
            "   {\"запуск\": {\"команда\": \"...\", \"таймаут\": 600}}")
    if recursive_scan:
        return ("\n\n💡 Для поиска файлов по диску есть готовые команды — "
                "они ходят по индексу и не вешают PowerShell:\n"
                "   {\"найти_файл\": {\"маска\": \"*MEGA*\"}}\n"
                "   {\"найти_файл\": {\"маска\": \"*MEGA*\", \"где\": \"E:/\"}}\n"
                "   {\"анализ_папки\": {\"путь\": \"E:/\"}}\n"
                "   {\"список_файлов\": {\"путь\": \"E:/\"}}\n\n"
                "Либо увеличь «таймаут»:\n"
                "   {\"запуск\": {\"команда\": \"...\", \"таймаут\": 600}}")
    if recursive_del:
        return ("\n\n💡 Для рекурсивной очистки есть готовая команда:\n"
                "   {\"убраться_в\": {\"путь\": \"...\"}}\n\n"
                "Либо увеличь «таймаут»:\n"
                "   {\"запуск\": {\"команда\": \"...\", \"таймаут\": 600}}")
    return base


def run_powershell_with_cwd(command: str, timeout: int = 30, silence_timeout: float = None) -> str:
    global _ps_cwd
    command = command.strip()

    # Pre-flight: рекурсивный обход/удаление через PowerShell — не наш путь.
    _cl = command.lower()
    _recursive_scan = (
        ("get-childitem" in _cl and "-recurse" in _cl)
        or ("gci" in _cl and ("-recurse" in _cl or re.search(r"\s-r\b", _cl)))
        or re.search(r"\bdir\s+/s\b", _cl)
        or re.search(r"\b(findstr|find)\b.*\s/s\b", _cl)
        or re.search(r"\btree\b", _cl)
    )
    _recursive_del = (
        ("remove-item" in _cl and "-recurse" in _cl)
        or re.search(r"\b(del|erase)\s+/s\b", _cl)
        or re.search(r"\b(rd|rmdir)\s+/s\b", _cl)
    )
    if _recursive_scan:
        return ("❌ Рекурсивный обход диска через PowerShell не выполняю.\n"
                "   Готовые команды:\n"
                "     {\"найти_файл\": {\"маска\": \"*MEGA*\"}}\n"
                "     {\"найти_файл\": {\"маска\": \"*MEGA*\", \"где\": \"E:/\"}}\n"
                "     {\"анализ_папки\": {\"путь\": \"E:/\"}}\n"
                "     {\"список_файлов\": {\"путь\": \"E:/\"}}")
    if _recursive_del:
        return ("❌ Рекурсивное удаление через PowerShell не выполняю.\n"
                "   Готовая команда:\n"
                "     {\"убраться_в\": {\"путь\": \"...\"}}")

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
                _tail_result = run_powershell_with_cwd(_rest_part, timeout, silence_timeout)
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

        # silence_timeout = min(timeout/2, 300), если не задан явно
        if silence_timeout is None:
            silence_timeout = min(timeout / 2.0, 300.0)

        import threading as _thr
        out_buf = []
        err_buf = []
        buf_lock = _thr.Lock()
        activity = [time.time()]

        def _reader(stream, buf):
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    with buf_lock:
                        buf.append(chunk)
                    activity[0] = time.time()
            except Exception:
                pass

        t_out = _thr.Thread(target=_reader, args=(proc.stdout, out_buf), daemon=True)
        t_err = _thr.Thread(target=_reader, args=(proc.stderr, err_buf), daemon=True)
        t_out.start()
        t_err.start()

        start = time.time()
        killed_reason = None
        _last_shown = ""
        while True:
            if proc.poll() is not None:
                break
            now = time.time()
            _silent = now - activity[0]
            _elapsed = now - start
            if timeout and _elapsed > timeout:
                killed_reason = f"Таймаут {timeout} сек"
                break
            if silence_timeout and _silent > silence_timeout:
                killed_reason = f"Тишина {int(silence_timeout)} сек"
                break
            # прогресс: что раньше наступит — таймаут или тишина
            if silence_timeout and timeout:
                if (int(timeout) - int(_elapsed)) < (int(silence_timeout) - int(_silent)):
                    _msg = f"\r⏳ Таймаут {int(_elapsed)}/{int(timeout)} сек"
                else:
                    _msg = f"\r⏳ Тишина {int(_silent)}/{int(silence_timeout)} сек"
            elif silence_timeout:
                _msg = f"\r⏳ Тишина {int(_silent)}/{int(silence_timeout)} сек"
            elif timeout:
                _msg = f"\r⏳ {int(_elapsed)}/{int(timeout)} сек"
            else:
                _msg = ""
            if _msg and _msg != _last_shown:
                sys.stdout.write(_msg + "    ")
                sys.stdout.flush()
                _last_shown = _msg
            time.sleep(0.3)
        if _last_shown:
            sys.stdout.write("\r" + " " * 90 + "\r")
            sys.stdout.flush()

        if killed_reason:
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
                proc.wait(timeout=5)
            except Exception:
                pass

        t_out.join(timeout=2)
        t_err.join(timeout=2)

        with buf_lock:
            stdout_b = b"".join(out_buf)
            stderr_b = b"".join(err_buf)

        if killed_reason:
            partial = (_decode_ps(stdout_b) + "\n"
                       + _decode_ps(stderr_b, prefer_utf8=False)).strip()
            hint = _ps_hint_for_command(command, killed_reason)
            partial = _mask(partial)
            return (f"⏱ {killed_reason} — процесс и его потомки убиты.\n"
                    f"Успело вывести:\n{partial[:1024] or '(пусто)'}{hint}")

        out_str = _decode_ps(stdout_b, prefer_utf8=True)
        # stderr: сначала UTF-8 (PS-ошибки от уже запущенного процесса),
        # fallback — cp866 (парсинг-ошибки до префикса)
        err_str = _decode_ps(stderr_b, prefer_utf8=True)

        # PowerShell 5.1 сериализует streams 3-6 (Progress/Information/Warning)
        # в CLIXML и кидает их в stderr. $InformationPreference='SilentlyContinue'
        # не всегда помогает (особенно от вложенных модулей). Отрезаем.
        import re as _re_clx
        err_str = _re_clx.sub(r"\n?#<\s*CLIXML.*", "", err_str, flags=_re_clx.S).rstrip()
        # В stdout тоже может попасть CLIXML — вычищаем и оттуда
        out_str = _re_clx.sub(r"\n?#<\s*CLIXML.*", "", out_str, flags=_re_clx.S).rstrip()

        output = out_str
        if err_str.strip():
            output += ("\n" + err_str) if output.strip() else err_str
        # Финальная обрезка CLIXML (на всякий случай — на всём output)
        import re as _re_clx2
        output = _re_clx2.sub(r"#<\s*CLIXML.*", "", output, flags=_re_clx2.S).rstrip()
        # Сохраняем переводы строк (важно для веб-поиска, файлов и т.п.).
        # Схлопываем только пробелы/табы и лишние пустые строки.
        output = re.sub(r"[ \t]+", " ", output)
        output = re.sub(r" *\n *", "\n", output)
        output = re.sub(r"\n{3,}", "\n\n", output)
        output = output.strip()
        if len(output) > 2048:
            output = output[:1024] + "\n... [обрезано] ...\n" + output[-1024:]
        output = _mask(output)
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
