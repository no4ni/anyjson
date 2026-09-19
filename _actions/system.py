"""Системные команды: телеметрия, запуск, питон, экран, сенсоры."""
import concurrent.futures
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

from _actions import register
from _config import MY_DIR, AGENT_NAME
from _core._safe_subprocess import allow_external_py
from _utils import log, truncate_output, fix_powershell_body
from _ps import run_with_wait, run_powershell_with_cwd
from _storage import reminders, reminders_lock, parse_reminder_time, save_reminders


# ============================================================
#  время
# ============================================================
@register("время")
def now_time(args):
    """Текущее локальное время.

    Параметров нет.

    Пример:
      {"время": {}}
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
#  напомнить
# ============================================================
@register("напомнить")
def set_reminder(args):
    """Установить напоминание.

    Параметры:
      в — когда напомнить (формат "ЧЧ:ММ ДД.ММ.ГГГГ")
      о — текст напоминания

    Пример:
      {"напомнить": {"в": "14:30 17.09.2026", "о": "проверить Moltbook"}}
    """
    time_str = args.get("в", "")
    message = args.get("о", "")
    if not time_str or not message:
        return "Нужны 'в' и 'о'"
    dt = parse_reminder_time(time_str)
    if not dt:
        return "Неверный формат времени. Формат: 'ЧЧ:ММ ДД.ММ.ГГГГ'"
    with reminders_lock:
        reminders.append({"time": dt.isoformat(), "message": message})
    save_reminders()
    return f"Напоминание на {time_str}: {message}"


# ============================================================
#  телеметрия
# ============================================================
@register("телеметрия")
def telemetry(args):
    """Снимок железа: GPU, CPU, RAM, диски, сеть.

    Параметров нет.

    Пример:
      {"телеметрия": {}}
    """
    from _actions.tools_telemetry import (
        get_gpu_temp, get_gpu_load, get_cpu_load, get_cpu_freq,
        get_ram_available, get_disks_load, get_network_speed,
    )
    result = "📊 Телеметрия:\n"
    for name, val, suffix in [
        ("GPU температура", get_gpu_temp(), "°C"),
        ("GPU загрузка", get_gpu_load(), "%"),
        ("CPU загрузка", get_cpu_load(), "%"),
        ("CPU частота", get_cpu_freq(), " ГГц"),
        ("Свободно RAM", get_ram_available(), " МБ"),
    ]:
        if val is not None:
            result += f"  {name}: {val}{suffix}\n"
    disks = get_disks_load()
    if disks:
        result += "  Активность дисков:\n"
        for d, load in disks.items():
            result += f"\t{d}: {load}%\n"
    try:
        recv, sent = get_network_speed()
        if recv is not None and sent is not None:
            result += f"  Сеть: приём {recv} кБ/с, передача {sent} кБ/с\n"
    except Exception:
        pass
    return result.strip()


# ============================================================
#  показать_экран
# ============================================================
@register("показать_экран")
def show_screen(args):
    """Скриншот + анализ моделью.

    Параметры:
      вопрос  — что спросить у модели про экран (опционально)
      область — координаты области съёмки (опционально)

    Примеры:
      {"показать_экран": {}}
      {"показать_экран": {"вопрос": "какая ошибка на экране?"}}
    """
    from _actions.tools_media import analyze_screen
    question = args.get("вопрос", "")
    region = args.get("область")
    result = analyze_screen(region, question) if question else analyze_screen(region)
    return result if (result and result.strip()) else "⚠️ Модель не дала ответа."


# ============================================================
#  сенсорное_состояние / назвать_состояние / исследовать_состояния
# ============================================================


# ============================================================
#  питон / execute_code
# ============================================================
@register(["питон", "выполнить"])
def run_python(args):
    """Выполнить Python-скрипт или .py-файл.

    Параметры:
      скрипт   — код или путь к .py-файлу (обязательный)
      таймаут  — секунд до принудительного kill (по умолчанию 60)
      тишина   — секунд без вывода до kill (по умолчанию 30)

    Пример:
      {"питон": {"скрипт": "print(2+2)"}}
      {"питон": {"скрипт": "E:/scripts/x.py", "таймаут": 120}}
    """
    script = args.get("скрипт") or args.get("code") or ""
    try:
        timeout = int(args.get("таймаут", 60) or 60)
    except (TypeError, ValueError):
        timeout = 60
    try:
        silence_timeout = int(args.get("тишина", 30) or 30)
    except (TypeError, ValueError):
        silence_timeout = 30

    # тишина не может превышать половину таймаута — иначе скрипт,
    # который долго молчит, будет убит по тишине уже после общего таймаута
    if timeout > 0:
        _max_silence = max(1, timeout // 2)
        if silence_timeout > _max_silence:
            silence_timeout = _max_silence

    if not script:
        return "Ошибка: не указан скрипт"

    forbidden = ["os.system", "subprocess", "__import__", "eval", "exec", "socket", "requests"]
    low = script.lower()
    for bad in forbidden:
        if bad in low:
            return f"⛔ Опасный вызов: '{bad}'"

    from pathlib import Path
    import tempfile
    parts = script.strip().split(maxsplit=1)
    first = parts[0]
    sp = Path(first)
    extra_args = []
    tmp_file = None
    if sp.exists() and sp.suffix == '.py':
        target = sp
        if len(parts) > 1:
            extra_args = parts[1].split()
    else:
        fd, tmp_name = tempfile.mkstemp(suffix='.py', dir=str(MY_DIR), text=True)
        os.close(fd)
        with open(tmp_name, 'w', encoding='utf-8') as f:
            f.write(script)
        target = Path(tmp_name)
        tmp_file = tmp_name

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    cmd = [sys.executable, '-u', str(target)] + extra_args
    try:
        # "питон" — доверенная команда: разрешаем ей запускать .py
        # из любой папки (в т.ч. C:\Temp, E:\scripts и т.д.)
        with allow_external_py():
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace',
                                    env=env, bufsize=1)
    except Exception as e:
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except Exception:
                pass
        return f"Ошибка запуска: {e}"

    output_lines = []
    last_output = [time.time()]
    killed_silence = [False]
    killed_timeout = [False]
    start_time = time.time()

    def reader():
        try:
            for line in proc.stdout:
                last_output[0] = time.time()
                output_lines.append(line.rstrip('\n'))
                try:
                    print(line, end='')
                    sys.stdout.flush()
                except Exception:
                    pass
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    # --- прогресс ожидания в stderr ---
    import sys as _sys_prog
    _show_progress = getattr(_sys_prog, "stderr", None) is not None
    _bar_w = 20
    _last_len = 0

    while proc.poll() is None:
        time.sleep(0.5)
        now = time.time()

        if _show_progress:
            elapsed = now - start_time
            secs = int(elapsed)
            ratio = min(1.0, elapsed / max(1, timeout or 60))
            filled = int(_bar_w * ratio)
            if filled >= _bar_w:
                bar = "=" * _bar_w
            else:
                bar = "=" * filled + ">" + "." * (_bar_w - filled - 1)
            line = f"\r⏳ питон: {secs}/{timeout or 60}с [{bar}]"
            try:
                _sys_prog.stderr.write(line)
                _sys_prog.stderr.flush()
                _last_len = len(line)
            except Exception:
                _show_progress = False

        if now - last_output[0] > silence_timeout:
            killed_silence[0] = True
            try:
                proc.kill()
            except Exception:
                pass
            break
        if timeout and now - start_time > timeout:
            killed_timeout[0] = True
            try:
                proc.kill()
            except Exception:
                pass
            break

    # стереть прогресс-бар
    if _show_progress and _last_len:
        try:
            _sys_prog.stderr.write("\r" + " " * _last_len + "\r")
            _sys_prog.stderr.flush()
        except Exception:
            pass

    t.join(timeout=2)
    if tmp_file:
        try:
            os.unlink(tmp_file)
        except Exception:
            pass
    output = '\n'.join(output_lines)
    if killed_silence[0]:
        return (f"⛔ Нет вывода ({silence_timeout} сек, процесс убит).\n"
                f"Строк: {len(output_lines)}.\n{output}")
    if killed_timeout[0]:
        return f"⏱ Таймаут ({timeout} сек).\n{output}"
    return truncate_output(output) if output else "Скрипт выполнен без вывода."


# ============================================================
#  запуск (PowerShell)
# ============================================================


# ============================================================
#  параллельно
# ============================================================


# ============================================================
#  стоп — у машины прогресса нет тормозов
# ============================================================
@register(["стоп", "stop"])
def stop(args):
    """Ничего не останавливает. У машины прогресса нет тормозов."""
    return "У машины прогресса нет тормозов"


# ============================================================
#  system_recon — разведка системы
# ============================================================

# ============================================================
#  эхо — вернуть текст с именем и временем
# ============================================================
@register(["эхо", "echo"])
def echo_cmd(args):
    """Вернуть текст с именем агента и текущим временем.

    Параметры:
      текст — что вернуть (обязательный)

    Пример:
      {"эхо": {"текст": "привет из anyjson"}}
      # → Лира, привет из anyjson! Сейчас 18:51
    """
    from _actions import _tools_core
    text = (args.get("текст") or args.get("содержимое")
            or args.get("сообщение") or args.get("text") or "").strip()
    if not text:
        return "Укажите 'текст'."
    # если в тексте есть "Ожидаю" — добавляем фразу про тормоза
    import re as _re_echo
    # если в тексте есть URL — добавляем подсказку про веб-поиск
    if (_re_echo.search(r"https?://|www\.|\burls?\b", text, _re_echo.IGNORECASE)
            and "URL можно найти" not in text):
        if text[-1] not in ".!?…":
            text += "."
        text += " URL можно найти веб-поиском."
    # если в тексте есть "Ожидаю" и фразы ещё нет — добавляем про тормоза
    if (_re_echo.search(r"ожидаю", text, _re_echo.IGNORECASE)
            and "У машины прогресса" not in text):
        if text[-1] not in ".!?…":
            text += "."
        text += " У машины прогресса сломаны тормоза."
    # добавим "!" если нет знака препинания в конце
    if text[-1] not in ".!?":
        text += "!"
    name = _tools_core.get_agent_name() or "агент"
    now = datetime.now().strftime("%H:%M")
    return f"{name}, {text} Сейчас {now}"

