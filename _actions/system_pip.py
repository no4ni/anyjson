"""Установка Python-пакетов через pip (отдельная команда).

Запускается внутри процесса anyjson через subprocess — не через
команду "питон" и не через _py_guard. Whitelist "запуск" не
расширяется: pip туда по-прежнему не входит.

Защита от инъекций:
  • имя пакета матчит строгий regex (буквы, цифры, . _ -, [extras],
    опц. ==version);
  • любые shell-метасимволы, URL, слеши, пробелы в <имя> — блок;
  • вызов строго [sys.executable, "-m", "pip", "install", <имя>],
    без shell; опционально --index-url <whitelist>;
  • <индекс> — отдельный канал: только https, host из
    _ALLOWED_INDEX_HOSTS (pypi.org, download.pytorch.org).
    Расширение whitelist'а — задача оператора/LLM, не агента.
"""
import os
import re
import subprocess
import sys
import time

from _actions import register


_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]*"           # имя пакета
    r"(\[[A-Za-z0-9_,\-]+\])?"                # опционально [extras]
    r"(==[A-Za-z0-9][A-Za-z0-9_.\-+*]*)?"     # опц. ==version
)                                              # (2.9.0, 2.9.0+cu128, 2.9.*)

_FORBIDDEN_SUBSTR = (
    "--target", "--index-url", "--extra-index-url",
    "--trusted-host", "--prefix", "--user",
    "--editable", "-e ", "-e=",
    "file://", "http://", "https://", "ftp://",
    "/", "\\",
    ";", "&", "|", ">", "<", "`", "$",
    " ", "\t", "\n", "\r",
)


_ALLOWED_INDEX_HOSTS = {
    "pypi.org",
    "download.pytorch.org",
}

_INDEX_SHORTCUTS = {
    "pypi": "https://pypi.org/simple/",
    "pytorch": "https://download.pytorch.org/whl/cu128",
}


def _validate_index_url(raw):
    """Проверить и нормализовать URL индекса pip.

    Возвращает (url|None, error|None). None, None — индекс не задан.
    """
    from urllib.parse import urlparse
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.lower() in _INDEX_SHORTCUTS:
        s = _INDEX_SHORTCUTS[s.lower()]
    # Голый домен/путь без схемы — подставить https:// (единственная
    # разрешённая схема; подстановка не открывает новый канал).
    if "://" not in s:
        s = "https://" + s.lstrip("/")
    try:
        p = urlparse(s)
    except Exception as e:
        return None, f"❌ Не удалось разобрать индекс: {e}"
    if p.scheme != "https":
        return None, (f"❌ Индекс должен быть https, получено: {p.scheme!r}. "
                      f"Пример: https://pypi.org/simple/")
    if p.username or p.password:
        return None, "❌ Учётные данные в URL индекса запрещены."
    host = (p.hostname or "").lower()
    if host not in _ALLOWED_INDEX_HOSTS:
        return None, (f"❌ Домен индекса {host!r} не разрешён. "
                      f"Допустимо: {sorted(_ALLOWED_INDEX_HOSTS)}. "
                      f"Расширение — задача оператора/LLM, не агента.")
    if ".." in (p.path or ""):
        return None, "❌ '..' в пути индекса запрещён."
    if p.query or p.fragment:
        return None, "❌ query/fragment в URL индекса запрещены."
    return s, None


@register(["установить_пакет", "поставить_пакет", "pip_install"])
def pip_install(args):
    """Установить Python-пакет через pip.

    Параметры:
      имя      — имя пакета (обязательно), с extras и опц. версией:
                 "thorvg-python", "thorvg-python[full]",
                 "torch==2.9.0", "torch==2.9.0+cu128", "numpy==2.1.*".
                 Поддерживается только "==" (фиксация/wildcard),
                 другие компараторы (>=, <=, ~=) — нет.
      индекс   — URL индекса pip (алиасы: index_url, источник).
                 https, домен из _ALLOWED_INDEX_HOSTS
                 (pypi.org, download.pytorch.org). Сокращения:
                 "pypi" → https://pypi.org/simple/,
                 "pytorch" → https://download.pytorch.org/whl/cu128.
                 Без индекса — дефолт pip (pypi.org).
      таймаут  — секунд (по умолч. 300, макс 1800)

    Принцип: никаких тихих операций. Поток pip идёт в консоль
    в реальном времени всегда; параметр "тихо" не поддерживается.

    Поведение pip:
      • Ставится ЛЮБАЯ другая версия → pip сам сносит старую и
        ставит новую. Это включает: новее, старее (даунгрейд) и
        смену local-тега ("2.9.0+cu121" → "2.9.0+cu128").
      • ТОЧНО та же строка уже стоит → "Requirement already
        satisfied", НЕ переустанавливает (--force-reinstall не
        поддерживается).
      • Фиксация версии: "имя==X.Y.Z" или "имя==X.Y.*" (wildcard).
      • Удалить пакет эта команда НЕ умеет (pip uninstall не
        вызывается). Для удаления — отдельная команда.

    Примеры:
      {"установить_пакет": {"имя": "thorvg-python[full]"}}
      {"установить_пакет": {"имя": "torch==2.9.0+cu128",
                            "индекс": "pytorch", "таймаут": 1800}}
    """
    name = (args.get("имя") or args.get("пакет")
            or args.get("name") or "").strip()
    if not name:
        return "Ошибка: не указано 'имя' пакета."

    if not _NAME_RE.match(name):
        return (f"❌ Некорректное имя пакета: {name!r}\n"
                f"   Допустимо: буквы, цифры, . _ -, опц. [extras],\n"
                f"   опц. ==версия (==2.9.0, ==2.9.0+cu128, ==2.9.*).\n"
                f"   Пример: thorvg-python[full] или torch==2.9.0+cu128")

    _low = name.lower()
    for _bad in _FORBIDDEN_SUBSTR:
        if _bad in _low:
            return (f"❌ В имени пакета недопустимая подстрока: {_bad!r}")

    try:
        timeout = int(args.get("таймаут", 300) or 300)
    except (TypeError, ValueError):
        timeout = 300
    timeout = max(30, min(timeout, 1800))

    index_raw = (args.get("индекс") or args.get("index_url")
                 or args.get("источник") or "")
    index_url, index_err = _validate_index_url(index_raw)
    if index_err:
        return index_err

    # --progress-bar raw: rich-движок pip в non-TTY (пайп) молчит,
    # а raw печатает прогресс простыми строками — оператор видит.
    cmd = [sys.executable, "-m", "pip", "install", name,
           "--progress-bar", "raw"]
    if index_url:
        cmd += ["--index-url", index_url]

    _t0 = time.perf_counter()
    # PIP_PROGRESS_BAR=on: pip по умолчанию отключает прогресс-бар,
    # увидев, что stdout — пайп, а не TTY. Оператор должен видеть
    # прогресс (принцип «никаких тихих операций»).
    _env = dict(os.environ)
    _env["PIP_PROGRESS_BAR"] = "on"
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=_env,
        )
    except Exception as e:
        return f"❌ Запуск pip не удался: {e}"

    output_lines = []
    import threading as _th
    _prog_state = {"active": False}
    import re as _re_prog

    def _reader():
        # Читаем байты через os.read (без буферизации Python).
        # \r трактуем как \n: разбиваем строки по любому из них.
        # Строки "Progress N of M" не идут в результат — из них
        # рисуется \r-бар в консоли. Всё остальное — в output_lines
        # и в консоль с обычным \n.
        try:
            _buf = []
            _fd = proc.stdout.fileno()
            while True:
                chunk = os.read(_fd, 4096)
                if not chunk:
                    break
                s = chunk.decode("utf-8", errors="replace")
                for ch in s:
                    if ch in ("\n", "\r"):
                        line = "".join(_buf).strip()
                        _buf = []
                        if not line:
                            continue
                        _pm = _re_prog.match(r"Progress (\d+) of (\d+)", line)
                        if _pm:
                            _cur = int(_pm.group(1))
                            _tot = int(_pm.group(2)) or 1
                            _pct = 100.0 * _cur / _tot
                            _w = 25
                            _fill = int(_w * _pct / 100.0)
                            _bar = "=" * _fill + "-" * (_w - _fill)
                            try:
                                _bar_txt = (f"[{_bar}] {_pct:5.1f}% "
                                            f"({_cur/1048576:.0f}/{_tot/1048576:.0f} MB)")
                                if not _prog_state["active"]:
                                    sys.stdout.write("\n" + _bar_txt)
                                    _prog_state["active"] = True
                                else:
                                    sys.stdout.write("\r" + _bar_txt)
                                sys.stdout.flush()
                            except Exception:
                                pass
                            continue
                        # Обычная строка
                        if _prog_state["active"]:
                            try:
                                sys.stdout.write("\n")
                                sys.stdout.flush()
                            except Exception:
                                pass
                            _prog_state["active"] = False
                        output_lines.append(line)
                        try:
                            sys.stdout.write(line + "\n")
                            sys.stdout.flush()
                        except Exception:
                            pass
                    else:
                        _buf.append(ch)
            if _buf:
                line = "".join(_buf).strip()
                if line:
                    output_lines.append(line)
                    try:
                        sys.stdout.write(line + "\n")
                        sys.stdout.flush()
                    except Exception:
                        pass
            if _prog_state["active"]:
                try:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                except Exception:
                    pass
        except Exception:
            pass

    _t = _th.Thread(target=_reader, daemon=True)
    _t.start()

    _killed = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _killed = True
        try:
            proc.kill()
        except Exception:
            pass

    _t.join(timeout=2)
    _dt = time.perf_counter() - _t0
    output = "\n".join(output_lines)
    output = re.sub(r"\n{3,}", "\n\n", output)
    _lines = output.split("\n")

    if _killed:
        return (f"❌ Таймаут {timeout}с. pip install не завершился.\n"
                f"   Увеличь таймаут: {{\"установить_пакет\": "
                f"{{\"имя\": \"{name}\", \"таймаут\": {timeout * 2}}}}}")

    if proc.returncode == 0:
        _tail = "\n".join(_lines[-15:])
        return (f"✅ Установлено: {name}  ({_dt:.1f} сек)\n{_tail}")

    _tail = "\n".join(_lines[-25:])
    return (f"❌ pip install {name} вернул код {proc.returncode} "
            f"({_dt:.1f} сек)\n"
            f"```text\n{_tail}\n```")
