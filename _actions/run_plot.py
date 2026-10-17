"""Запуск .py как независимого процесса: ждать закрытия, без таймаута."""
import os
import subprocess
import sys
import time
from pathlib import Path

from _actions import register
from _core._safe_subprocess import allow_external_py


@register(["запуск_графики"])
def run_plot(args):
    """Запустить любой .py как отдельный процесс и ждать его закрытия.

    Без таймаута, без kill'а по тишине. Для GUI-скриптов (matplotlib,
    PyQt, turtle, pygame) — окно само решает, когда закрыться. Для
    долгих вычислений — скрипт сам решает, когда закончить.

    Параметры:
      путь      — .py-файл (обязательно), алиас: скрипт
      аргументы — список аргументов для скрипта (опционально),
                  строка через пробелы тоже принимается

    Примеры:
      {"запуск_графики": {"путь": "E:/projects/plot.py"}}
      {"запуск_графики": {"путь": "plot.py", "аргументы": ["--save", "out.png"]}}
    """
    path_arg = args.get("путь") or args.get("скрипт")
    if not path_arg:
        return 'Укажи путь: {"запуск_графики": {"путь": "E:/plot.py"}}'

    p = Path(path_arg).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    if not p.is_file():
        return "❌ Файл не найден: " + str(p)
    if p.suffix.lower() != ".py":
        return "❌ Не .py-файл: " + str(p)

    extra = args.get("аргументы") or []
    if isinstance(extra, str):
        extra = extra.split() if extra.strip() else []
    elif isinstance(extra, list):
        extra = [str(x) for x in extra]
    else:
        extra = [str(extra)]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, "-u", str(p)] + extra
    try:
        with allow_external_py():
            proc = subprocess.Popen(
                cmd,
                cwd=str(Path.cwd()),
                env=env,
            )
    except Exception as e:
        return "❌ Не удалось запустить: " + str(e)

    _start = time.time()
    _tick = 0
    _line_len = 0
    try:
        while proc.poll() is None:
            time.sleep(0.5)
            _tick += 1
            if _tick % 2 == 0:
                _el = int(time.time() - _start)
                _msg = ("\r⏳ запуск_графики: " + p.name
                        + " (PID " + str(proc.pid) + ") — жду закрытия... "
                        + str(_el) + "с")
                try:
                    sys.stderr.write(_msg)
                    sys.stderr.flush()
                    _line_len = len(_msg)
                except Exception:
                    pass
    except KeyboardInterrupt:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        if _line_len:
            try:
                sys.stderr.write("\r" + " " * _line_len + "\r")
                sys.stderr.flush()
            except Exception:
                pass
        return "⛔ Прервано (Ctrl+C). Процесс " + str(proc.pid) + " убит."

    if _line_len:
        try:
            sys.stderr.write("\r" + " " * _line_len + "\r")
            sys.stderr.flush()
        except Exception:
            pass

    _rc = proc.returncode
    _el = time.time() - _start
    return ("✅ " + p.name + " завершился (код " + str(_rc)
            + ", " + format(_el, ".1f") + "с)")