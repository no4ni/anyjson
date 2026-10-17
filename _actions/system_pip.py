"""Установка Python-пакетов через pip (отдельная команда).

Запускается внутри процесса anyjson через subprocess — не через
команду "питон" и не через _py_guard. Whitelist "запуск" не
расширяется: pip туда по-прежнему не входит.

Защита от инъекций:
  • имя пакета матчит строгий regex (буквы, цифры, . _ -, [extras]);
  • любые shell-метасимволы, URL, слеши, пробелы — блок;
  • вызов строго [sys.executable, "-m", "pip", "install", <имя>],
    без shell, без дополнительных флагов.
"""
import re
import subprocess
import sys
import time

from _actions import register


_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]*"      # имя пакета
    r"(\[[A-Za-z0-9_,\-]+\])?$"          # опционально [extras]
)

_FORBIDDEN_SUBSTR = (
    "--target", "--index-url", "--extra-index-url",
    "--trusted-host", "--prefix", "--user",
    "--editable", "-e ", "-e=",
    "file://", "http://", "https://", "ftp://",
    "/", "\\",
    ";", "&", "|", ">", "<", "`", "$",
    " ", "\t", "\n", "\r",
)


@register(["установить_пакет", "поставить_пакет", "pip_install"])
def pip_install(args):
    """Установить Python-пакет через pip.

    Параметры:
      имя      — имя пакета (обязательно), можно с extras:
                 "thorvg-python" или "thorvg-python[full]"
      тихо     — true: без построчного вывода (по умолч. false)
      таймаут  — секунд (по умолч. 300, макс 1800)

    Пример:
      {"установить_пакет": {"имя": "thorvg-python[full]"}}
    """
    name = (args.get("имя") or args.get("пакет")
            or args.get("name") or "").strip()
    if not name:
        return "Ошибка: не указано 'имя' пакета."

    if not _NAME_RE.match(name):
        return (f"❌ Некорректное имя пакета: {name!r}\n"
                f"   Допустимо: буквы, цифры, . _ -, опционально [extras].\n"
                f"   Пример: thorvg-python[full]")

    _low = name.lower()
    for _bad in _FORBIDDEN_SUBSTR:
        if _bad in _low:
            return (f"❌ В имени пакета недопустимая подстрока: {_bad!r}")

    try:
        timeout = int(args.get("таймаут", 300) or 300)
    except (TypeError, ValueError):
        timeout = 300
    timeout = max(30, min(timeout, 1800))

    quiet = bool(args.get("тихо", False))

    cmd = [sys.executable, "-m", "pip", "install", name]

    _t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as e:
        return f"❌ Запуск pip не удался: {e}"

    output_lines = []
    import threading as _th

    def _reader():
        try:
            for line in proc.stdout:
                output_lines.append(line.rstrip("\n"))
                if not quiet:
                    try:
                        sys.stdout.write(line)
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
