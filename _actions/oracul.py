"""Оракул: обучение IDW-полиномиальной модели и предсказание по ней."""
import subprocess
from _config import ORACUL_CLI as ORACUL
import sys
from pathlib import Path

from _actions import register
from _config import MY_DIR
MODEL_FILE = "all_formulas.txt"   # oracul3_fixed.py жёстко читает его из cwd
DEFAULT_TIMEOUT = 600


def _run(args_list, timeout=DEFAULT_TIMEOUT):
    """Запускает oracul3_fixed.py с cwd=MY_DIR.
    Вывод стримится построчно — не ждём завершения.
    """
    import os as _os
    import threading as _th
    import time as _time

    if not ORACUL.exists():
        return None, f"❌ oracul3_fixed.py не найден: {ORACUL}"

    cmd = [sys.executable, "-u", str(ORACUL)] + args_list
    env = _os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(MY_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env, bufsize=1,
        )
    except Exception as e:
        return None, f"❌ Ошибка запуска: {e}"

    lines = []
    start = _time.time()
    timed_out = [False]

    def _reader():
        try:
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                print(line, end="", flush=True)
        except Exception:
            pass

    t = _th.Thread(target=_reader, daemon=True)
    t.start()

    while proc.poll() is None:
        _time.sleep(0.3)
        if _time.time() - start > timeout:
            timed_out[0] = True
            try:
                proc.kill()
            except Exception:
                pass
            break

    t.join(timeout=3)
    if timed_out[0]:
        return None, f"❌ Таймаут {timeout}с"

    out = "\n".join(lines)
    return proc.returncode, out


@register(["обучить_оракула", "обучи_оракула"])
def train_oracul(args):
    """Обучить модель Оракула по JSON-данным.

    Параметры:
      данные  — путь к JSON-файлу (обязательный)
                алиасы: файл, путь
      таймаут — секунд (по умолчанию 600)

    Формат JSON:
      {
        "t":     [1.0, 1.5, 2.0, ...],   // входные переменные
        "x":     [10, 20, 30, ...],      // любые другие — тоже входы
        "need":  "t",                    // что предсказывать (целевые)
        "iter":  3000                    // опционально
      }

    Поля "bounds" быть НЕ должно — настройки оракула зашиты в код.

    Примеры:
      {"обучить_оракула": {"данные": "E:/data/points.json"}}
      {"обучить_оракула": {"файл": "E:/data/points.json", "таймаут": 1200}}
    """
    data = args.get("данные") or args.get("файл") or args.get("путь")
    if not data:
        return ('Укажи файл данных: {"обучить_оракула": {"данные": "C:/path/data.json"}}')

    data_path = Path(data)
    if not data_path.is_file():
        return f"❌ Файл данных не найден: {data_path}"

    timeout = int(args.get("таймаут", DEFAULT_TIMEOUT))
    code, out = _run(["--learn", str(data_path), "--novisualize"], timeout=timeout)
    if code is None:
        return out
    if code != 0:
        return f"❌ Обучение упало (код {code}):\n{out[-2000:]}"

    # Проверить, что файл модели появился
    model = MY_DIR / MODEL_FILE
    if not model.exists():
        return f"⚠️ Обучение прошло, но файл модели не появился.\n{out[-1000:]}"

    return f"✅ Модель обучена.\n\n{out[-1500:]}"


@register(["предсказать_продолжение", "предскажи_продолжение"])
def predict_continuation(args):
    """Предсказать значение по обученной модели Оракула.

    Оракул отлично справляется и с интерполяцией (внутри обучающего
    диапазона), и с экстраполяцией (за его пределами) — на то он и Оракул.

    Способы задать вход:
      • {"t": 5.5} — одно число, broadcast на все входные признаки
      • {"x": 2.0, "y": 1.0, "u": 3.0} — именованные ВХОДНЫЕ признаки
        (только те, что в модели — их предсказует Оракул)
      • {"значение": 5.5} — явный broadcast-скаляр
      • {"значения": [1.5, 2.0, 3.0]} — позиционный вектор

    ⚠️ Целевые переменные (то, что модель предсказывает) передавать
    НЕ нужно. Например, если модель обучена с "need": "t" — то t не
    входит в аргументы: его-то мы и предсказываем.

    Параметры:
      значение / значения / values — broadcast-скаляр или вектор
      <признак> — имя входного признака = значение (можно несколько)
      таймаут   — секунд (по умолчанию 600)

    Примеры:
      {"предсказать_продолжение": {"t": 5.5}}
      {"предсказать_продолжение": {"x": 2.0, "y": 1.0, "u": 3.0}}
      {"предсказать_продолжение": {"значение": 5.5}}
    """
    # Только эти ключи — служебные. t — НЕ служебный,
    # это либо broadcast, либо признак (скрипт сам разберётся).
    _service = {"значение", "значения", "values",
                "таймаут", "timeout"}
    named = {k: v for k, v in (args or {}).items() if k not in _service}

    t = args.get("точка")
    explicit = (args.get("значение")
                or args.get("значения")
                or args.get("значения"))

    if t is None and explicit is None and not named:
        return ('Укажи значения — одним из способов:\n'
                '```text\n'
                '{"предсказать_продолжение": {"t": 5.5}}\n'
                '{"предсказать_продолжение": {"x": 2.0, "y": 1.0, "u": 3.0}}\n'
                '{"предсказать_продолжение": {"значение": 5.5}}\n'
                '{"предсказать_продолжение": {"значения": [1.5, 2.0]}}\n'
                '```')

    try:
        timeout = int(args.get("таймаут", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    model = MY_DIR / MODEL_FILE
    if not model.exists():
        return ('❌ Модель не обучена.\n'
                'Сначала: {"обучить_оракула": {"данные": "..."}}')

    if explicit is not None:
        # явный broadcast через "значение"/"значения"/"values"
        if isinstance(explicit, (list, tuple)):
            args_list = ["--predict"] + [str(v) for v in explicit]
        else:
            args_list = ["--predict", str(explicit)]
    elif t is not None and len(named) == 1 and "t" in named:
        # только t — broadcast или позиционный вектор
        if isinstance(t, (list, tuple)):
            args_list = ["--predict"] + [str(v) for v in t]
        else:
            args_list = ["--predict", str(t)]
    else:
        # именованные признаки — включая случай, когда t передан вместе
        # с другими. Скрипт сам скажет, что t — целевая, если так.
        args_list = ["--predict-named"] + [f"{k}={v}" for k, v in named.items()]

    code, out = _run(args_list, timeout=timeout)
    if code is None:
        return out
    if code != 0:
        return f"❌ Предсказание упало (код {code}):\n{out[-2000:]}"
    return out
