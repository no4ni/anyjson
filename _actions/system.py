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
    """Сделать скриншот экрана.

    Параметры:
      область — что снять: "top", "bottom", "left", "right", "center"
                или [x, y, w, h]; по умолчанию — весь экран.

    Примеры:
      {"показать_экран": {}}
      {"показать_экран": {"область": "center"}}
    """
    from _actions.tools_media import analyze_screen
    region = args.get("область")
    return analyze_screen(region)


# ============================================================
#  сенсорное_состояние / назвать_состояние / исследовать_состояния
# ============================================================


# ============================================================
#  питон / execute_code
# ============================================================
def _collapse_tqdm(text):
    """Сжать серии tqdm: оставить последнюю строку в каждой серии.

    Два слоя детекции кадра:
      1) ANSI-маркер ESC[A (курсор вверх) — как tqdm пишет в pipe-режиме;
      2) сам формат tqdm «имя: N%|...» — если ESC потерялся (копипаст,
         терминал проглотил, старый tqdm без \x1b[A).

    Группируем по префиксу до ":" и оставляем последний кадр серии.
    Пустые кадры пропускаем — иначе вывод распухает пустыми строками.
    """
    if not text:
        return text
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    _ansi_re = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
    # Ищем формат tqdm: «[префикс:] N%|...» (префикс может отсутствовать)
    _tqdm_re = re.compile(r"^\s*(?:.*?:\s*)?\d+%\|")
    out = []
    last_idx = {}
    for ln in text.split("\n"):
        _head = ln.rstrip()
        _is_frame = ("\x1b[A" in _head) or _head.endswith("[A")
        clean = None
        if _is_frame:
            # слой 1: ANSI-маркер
            clean = _ansi_re.sub("", ln).replace("[A", "").rstrip()
        else:
            # слой 2: сам формат tqdm «имя: N%|» или «N%|»
            m = _tqdm_re.match(ln)
            if m:
                clean = ln.rstrip()
        if clean is None:
            out.append(ln)
            continue
        if not clean:
            continue
        
        # Ищем префикс ТОЛЬКО до знака "%|", чтобы не поймать 
        # двоеточие в таймере [00:00<?, ?it/s]
        prefix = ""
        _pct_idx = clean.find("%|")
        if _pct_idx > 0:
            _before_pct = clean[:_pct_idx]
            if ":" in _before_pct:
                prefix = _before_pct.split(":")[0].strip()
        if prefix in last_idx:
            out[last_idx[prefix]] = clean
        else:
            last_idx[prefix] = len(out)
            out.append(clean)
    return "\n".join(out)

def _looks_like_py_path(s):
    """True, если первое слово похоже на путь к .py-файлу (а не inline-код).

    "E:/x.py"        -> True
    "E:/x.py --flag" -> True (первое слово)
    "print(2+2)"     -> False
    "import shutil"  -> False
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    first = s.split(maxsplit=1)[0]
    low = first.lower()
    return low.endswith(".py") or low.endswith(".pyw")


def _file_not_found_msg(path_str):
    """Понятное сообщение вместо SyntaxError от inline-выполнения пути."""
    from pathlib import Path as _P
    _miss = path_str.strip().split(maxsplit=1)[0]
    _name = _P(_miss).name
    return (
        f"❌ Файл не найден: {_miss}\n\n"
        f"💡 Варианты:\n"
        f"   1. Создать файл через переписать, затем запустить:\n"
        f'      {{"переписать": {{"путь": "{_miss}", "содержание": <<<\n'
        f"      ...содержимое...\n"
        f"      >>>}}}}\n"
        f'      {{"питон": {{"скрипт": "{_miss}"}}}}\n\n'
        f"   2. Inline-код (тело идёт как код):\n"
        f'      {{"питон": {{"скрипт": <<<\n'
        f"      ...код...\n"
        f"      >>>}}}}\n\n"
        f'   3. Найти файл: {{"найти_файл": {{"имя": "{_name}"}}}}'
    )


def _walk_hint(script):
    """Подсказка при kill по тишине, если в исходнике скрипта есть os.walk."""
    try:
        if script and os.path.isfile(script):
            with open(script, encoding="utf-8", errors="replace") as f:
                src = f.read()
        else:
            src = script or ""
    except Exception:
        return ""
    if "os.walk" not in src:
        return ""
    return (
        "\n\n💡 Похоже, скрипт обходит большое дерево через os.walk —\n"
        "   это медленно и легко вешает движок по тишине.\n"
        "   Есть быстрые готовые команды:\n"
        '   {"помощь": {"команда": "анализ_папки"}}\n'
        '   {"помощь": {"команда": "дерево"}}'
    )


@register(["питон", "выполнить"])
def run_python(args):
    """Выполнить Python-скрипт или .py-файл.

    Параметры:
      скрипт         — код или путь к .py-файлу (обязательный), алиасы: код, путь, файл
      аргументы      — список строк-аргументов для скрипта (опционально),
                       альтернатива «хвосту» в скрипт через пробел
      таймаут        — секунд до принудительного kill (по умолчанию 60)
      тишина         — секунд без вывода до kill (по умолчанию 30)
      замер_времени  — true: в ответ добавить «⏱ N.NN сек» — чистое время
                       работы скрипта (без старта Python и guard'а)
      в_файл         — путь к файлу: вывод скрипта идёт и в консоль
                       оператора, и в этот файл; в ответе — только
                       короткая сводка. Родительские папки создаются.
                       Если скрипт упал сам (SyntaxError, Traceback,
                       non-zero exit) — файл не создаётся, вывод идёт
                       в результат как у обычного «питон».

    Примеры:
      {"питон": {"скрипт": "print(2+2)"}}
      {"питон": {"скрипт": "E:/scripts/x.py", "таймаут": 120}}
      {"питон": {"скрипт": "E:/scripts/x.py", "аргументы": ["--learn", "data.json"]}}
      {"питон": {"скрипт": "E:/scripts/x.py --predict 0.5"}}
      {"питон": {"скрипт": "print('hi')", "замер_времени": true}}
    """
    script = (args.get("скрипт") or args.get("код") or
              args.get("путь") or args.get("файл") or "")
    try:
        timeout = int(args.get("таймаут", 60) or 60)
    except (TypeError, ValueError):
        timeout = 60
    try:
        silence_timeout = int(args.get("тишина", 30) or 30)
    except (TypeError, ValueError):
        silence_timeout = 30

    _do_timing = bool(args.get("замер_времени", False))

    _out_file = args.get("в_файл")
    if isinstance(_out_file, dict):
        _out_file = (_out_file.get("путь") or "").strip() or None
    elif isinstance(_out_file, str):
        _out_file = _out_file.strip() or None
    else:
        _out_file = None

    # тишина не может превышать половину таймаута — иначе скрипт,
    # который долго молчит, будет убит по тишине уже после общего таймаута
    if timeout > 0:
        _max_silence = max(1, timeout // 2)
        if silence_timeout > _max_silence:
            silence_timeout = _max_silence

    if not script:
        return "Ошибка: не указан скрипт"

    _FORBIDDEN = [
        (re.compile(r"\beval\s*\("), "eval("),
        (re.compile(r"\bexec\s*\("), "exec("),
        (re.compile(r"\b(?:import|from)\s+(?:win32com|pythoncom|comtypes)\b"),
         "win32com"),
    ]
    _HINT_MAP = {
        "os.system": (
            'Запуск системных команд — через {"запуск": {"команда": "..."}}\n'
            '   (PowerShell с whitelist). В самом Python-скрипте os.system '
            'недоступен.\n'
            '   Если встречается в якорях патча — правь через {"заменить_текст": {...}}.'
        ),
        "subprocess": (
            'Запуск внешних процессов — через {"запуск": {"команда": "..."}}\n'
            '   или {"питон": {"скрипт": "demo.py"}}. Прямой subprocess '
            'из скрипта не разрешён.\n'
            '   Если subprocess встречается в якорях патча (в old/new-строках) —\n'
            '   правь файл через {"заменить_текст": {...}} или {"заменить_функцию": {...}}:\n'
            '   их аргументы guard не сканирует.'
        ),
        "eval(": (
            'eval() не поддерживается. Вычисли значение явно в коде скрипта '
            'или передай его через аргументы.\n'
            '   Если встречается в якорях патча — правь через {"заменить_текст": {...}}.'
        ),
        "exec(": (
            'exec() не поддерживается. Код должен быть статическим — передай '
            'его как обычный .py-файл.\n'
            '   Если встречается в якорях патча — правь через {"заменить_текст": {...}}.'
        ),
        "win32com": (
            'COM/ActiveX (win32com, pythoncom, comtypes) — через встроенные команды:\n'
            '   {"создать_ярлык": {...}}  — ярлык .lnk\n'
            '   {"озвучить": {"текст": "..."}} — речь\n'
            '   {"скачать": {"url": "..."}} — файл по URL\n'
            '   Если нужно другое — уточни у оператора.'
        ),
        "shutil.rmtree": (
            'Удаление папки — через встроенные команды:\n'
            '   {"удалить_папку": {"путь": "..."}}        — рекурсивно со всем содержимым\n'
            '   {"удалить_путь": {"путь": "..."}}         — сам определит файл/папку\n'
            '   {"удалить_пустые_папки": {"путь": "..."}} — только пустые, рекурсивно\n'
            '   {"убраться_в": {"путь": "..."}}           — очистка временных\n'
            '   {"удалить_файл": {"путь": "..."}}         — конкретный файл.'
        ),
    }
    for _pat, _name in _FORBIDDEN:
        if _pat.search(script):
            msg = f"⛔ Опасный вызов: '{_name}'"
            _hint = _HINT_MAP.get(_name)
            if _hint:
                msg += "\n\n💡 " + _hint
            return msg

    from pathlib import Path
    import tempfile

    # Явный параметр «аргументы» — приоритетнее хвоста в скрипте.
    # Принимаем список строк или строку (разделим по пробелам).
    _args_raw = args.get("аргументы")
    explicit_args = None
    if _args_raw is not None:
        if isinstance(_args_raw, list):
            explicit_args = [str(x) for x in _args_raw]
        elif isinstance(_args_raw, str):
            explicit_args = _args_raw.split() if _args_raw.strip() else []
        else:
            explicit_args = [str(_args_raw)]

    extra_args = []
    tmp_file = None

    if explicit_args is not None:
        # Если аргументы заданы явно — скрипт берём целиком
        # (путь или код, без разбора по пробелам).
        first = script.strip()
        sp = Path(first)
        if sp.exists() and sp.suffix == '.py':
            target = sp
            extra_args = explicit_args
        else:
            if _looks_like_py_path(script):
                return _file_not_found_msg(script)
            # Это код (не файл) — сохраняем во временный .py
            fd, tmp_name = tempfile.mkstemp(suffix='.py', dir=str(MY_DIR), text=True)
            os.close(fd)
            with open(tmp_name, 'w', encoding='utf-8') as f:
                f.write(script)
            target = Path(tmp_name)
            tmp_file = tmp_name
            extra_args = explicit_args
    else:
        # Старая логика: «первое слово — путь, остальное — аргументы».
        parts = script.strip().split(maxsplit=1)
        first = parts[0]
        sp = Path(first)
        if sp.exists() and sp.suffix == '.py':
            target = sp
            if len(parts) > 1:
                extra_args = parts[1].split()
        else:
            if _looks_like_py_path(script):
                return _file_not_found_msg(script)
            fd, tmp_name = tempfile.mkstemp(suffix='.py', dir=str(MY_DIR), text=True)
            os.close(fd)
            with open(tmp_name, 'w', encoding='utf-8') as f:
                f.write(script)
            target = Path(tmp_name)
            tmp_file = tmp_name

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    # tqdm не подавляем: его вывод обновляет таймер тишины, иначе скрипт,
    # который долго считает между print-ами, будет убит по "тишине".
    # В финальном выводе tqdm-серии сжимает _collapse_tqdm.
    # Защитный wrapper: audit hook блокирует запись/удаление в системных
    # папках и os.system/subprocess.Popen внутри скрипта. Regex-блок выше
    # остаётся вторым слоем (быстрый отказ без запуска процесса).
    _guard = Path(__file__).resolve().parent.parent / '_core' / '_py_guard.py'
    if _guard.is_file():
        cmd = [sys.executable, '-u', str(_guard), str(target)] + extra_args
    else:
        cmd = [sys.executable, '-u', str(target)] + extra_args
    _t_start = time.perf_counter()
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

    _interrupted = [False]
    _tick = 0
    try:
        while proc.poll() is None:
            time.sleep(0.05)
            _tick += 1
            now = time.time()

            if _show_progress and _tick % 10 == 0:
                _sil = now - last_output[0]
                _tot = now - start_time
                _lim_sil = max(1, silence_timeout)
                _lim_tot = max(1, timeout or 60)
                _pct_sil = _sil / _lim_sil
                _pct_tot = _tot / _lim_tot
                # Стабильность строки: пока тишина < 5с, показываем
                # общий прогресс (таймаут). Счётчик тишины включается
                # только после 5с молчания — иначе строка дёргается
                # между двумя состояниями каждые 0.5с.
                if _sil < 5:
                    _label, _cur, _lim, _ratio = "таймаут", int(_tot), _lim_tot, _pct_tot
                else:
                    _label, _cur, _lim, _ratio = "тишина", int(_sil), _lim_sil, _pct_sil
                _ratio = min(1.0, _ratio)
                filled = int(_bar_w * _ratio)
                if filled >= _bar_w:
                    bar = "=" * _bar_w
                else:
                    bar = "=" * filled + ">" + "." * (_bar_w - filled - 1)
                line = f"\r⏳ питон {_label}: {_cur}/{_lim}с [{bar}]"
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
    except KeyboardInterrupt:
        _interrupted[0] = True
        try:
            proc.kill()
        except Exception:
            pass

    # стереть прогресс-бар
    if _show_progress and _last_len:
        try:
            _sys_prog.stderr.write("\r" + " " * _last_len + "\r")
            _sys_prog.stderr.flush()
        except Exception:
            pass

    t.join(timeout=2)
    _t_elapsed = time.perf_counter() - _t_start
    _exit_code = proc.returncode
    if tmp_file:
        try:
            os.unlink(tmp_file)
        except Exception:
            pass
    output = _collapse_tqdm('\n'.join(output_lines))
    # Страховка: схлопнуть серии пустых строк (3+ → 1). Остаются в
    # выводе, если ANSI-маркер не сработал (не-tqdm источник, ESC[K,
    # ESC[2K и т.п.). Секционные разделители не страдают: между
    # смысловыми блоками обычно ровно одна пустая строка.
    output = re.sub(r"\n{3,}", "\n\n", output)

    # Признак «скрипт упал сам» (не наш kill): при нём в_файл не создаём,
    # вывод пойдёт в консоль как у обычного питона.
    _looks_err = (
        (not (_interrupted[0] or killed_silence[0] or killed_timeout[0]))
        and (
            "SyntaxError" in output
            or "IndentationError" in output
            or "Traceback (most recent call last)" in output
            or (_exit_code is not None and _exit_code != 0)
        )
    )

    # Подсказка при SyntaxError: почти всегда — сломалось экранирование
    # при inline-передаче кода через JSON. Выход — heredoc / файл.
    _syn_hint = ""
    if "SyntaxError" in output or "IndentationError" in output:
        _syn_hint = ("\n\n💡 Ошибка синтаксиса. Частые причины:\n"
                     "   • Внутри <<<...>>> три пустые строки подряд — "
                     "тело обрезается.\n"
                     "   • В теле встретился литерал >>> (например, "
                     "в строке или комментарии) — он закрыл блок раньше.\n"
                     "   • Код передан обычной строкой без heredoc — "
                     "кавычки/апострофы при экранировании в JSON могли "
                     "сломаться. Для многострочного кода — inline-heredoc:\n"
                     "```text\n"
                     "{\"питон\": {\"скрипт\": <<<\n"
                     "...код без экранирования...\n"
                     ">>>}}\n"
                     "```")

    _assert_hint = ""
    if "AssertionError" in output:
        _assert_hint = (
            "\n\n💡 AssertionError в патч-скрипте — якорь для замены не найден.\n"
            "   Для точечных правок удобнее заменить_текст: у неё предпросмотр,\n"
            "   нестрогий режим (CRLF↔LF, тире, кавычки, 3+ пустых строк → 2)\n"
            "   и регулярка: true — точка в regex НЕ матчит перевод строки,\n"
            "   но большой блок можно вырезать через [\\s\\S]*? от маркера\n"
            "   до маркера (опережающая проверка (?=...)).\n"
            "```text\n"
            "{\"заменить_текст\": {\"путь\": \"...\", \"найти\": \"...\", \"заменить\": \"...\", \"предпросмотр\": true}}\n"
            "{\"заменить_текст\": {\"путь\": \"...\", \"найти\": \"НАЧАЛО[\\s\\S]*?(?=КОНЕЦ)\", \"заменить\": \"\", \"регулярка\": true}}\n"
            "```"
        )

    _saved_info = None
    if _out_file and not _looks_err:
        try:
            _op = Path(_out_file)
            _op.parent.mkdir(parents=True, exist_ok=True)
            with open(_op, 'w', encoding='utf-8') as _of:
                _of.write(output)
            _saved_info = (str(_op), len(output_lines),
                           len(output.encode('utf-8')))
        except Exception as _se:
            return (f"❌ Не смог сохранить вывод в '{_out_file}': {_se}\n"
                    f"Строк: {len(output_lines)}.\n{truncate_output(output)}")

    _t_show = max(0.0, _t_elapsed - 0.07) if _do_timing else 0.0
    _time_suffix = f"\n⏱ {_t_show:.2f} сек" if _do_timing else ""

    if _out_file and _saved_info:
        _tail = (f" → {_saved_info[0]} "
                 f"({_saved_info[1]} строк, {_saved_info[2]} байт)")
        if _interrupted[0]:
            return (f"⛔ Прервано пользователем (Ctrl+C). "
                    f"Вывод сохранён{_tail}{_time_suffix}")
        if killed_silence[0]:
            return (f"⛔ Нет вывода ({silence_timeout} сек, процесс убит). "
                    f"Вывод сохранён{_tail}{_time_suffix}"
                    f"{_walk_hint(script)}")
        if killed_timeout[0]:
            _hint = (f"\n\n💡 Увеличь «таймаут», если скрипт не успевает:\n"
                     f"   {{\"питон\": {{\"скрипт\": \"...\", \"таймаут\": {timeout * 4}, "
                     f"\"тишина\": {max(60, silence_timeout * 4)}}}}}")
            return (f"⏱ Таймаут ({timeout} сек). Вывод сохранён{_tail}"
                    f"{_hint}{_time_suffix}")
        _small_hint = ""
        if _saved_info[2] < 1024:
            _small_hint = (
                f"\n\n💡 Вывод всего {_saved_info[2]} Б — сохранять в файл "
                f"смысла мало.\n"
                f"   Убери \"в_файл\" — вывод придёт прямо в контекст:\n"
                f"   {{\"питон\": {{\"скрипт\": \"...\"}}}}"
            )
        return f"💾 Вывод сохранён{_tail}{_small_hint}{_syn_hint}{_time_suffix}"

    if _interrupted[0]:
        return (f"⛔ Прервано пользователем (Ctrl+C).\n"
                f"Строк вывода: {len(output_lines)}.\n{output}{_time_suffix}")
    if killed_silence[0]:
        return (f"⛔ Нет вывода ({silence_timeout} сек, процесс убит).\n"
                f"Строк: {len(output_lines)}.\n{output}{_time_suffix}"
                f"{_walk_hint(script)}")
    if killed_timeout[0]:
        _hint = (f"\n\n💡 Увеличь «таймаут», если скрипт не успевает:\n"
                 f"   {{\"питон\": {{\"скрипт\": \"...\", \"таймаут\": {timeout * 4}, "
                 f"\"тишина\": {max(60, silence_timeout * 4)}}}}}")
        return f"⏱ Таймаут ({timeout} сек).\n{output}{_hint}{_time_suffix}"
    if not output:
        _empty_note = ""
        if _out_file and _looks_err:
            _empty_note = f"\n\n(в '{_out_file}' не сохранял — скрипт упал)"
        return "Скрипт выполнен без вывода." + _time_suffix + _empty_note
    if _out_file and _looks_err:
        return (truncate_output(output) + _syn_hint + _assert_hint
                + _time_suffix
                + f"\n\n(в '{_out_file}' не сохранял — скрипт упал)")
    return truncate_output(output) + _syn_hint + _assert_hint + _time_suffix


# ============================================================
#  запуск (PowerShell)
# ============================================================


# ============================================================
#  параллельно
# ============================================================


# ============================================================
#  сменить_имя — переприсвоить AGENT_NAME и MY_DIR в рантайме
# ============================================================
@register(["сменить_имя", "переименовать_агента", "новое_имя"])
def change_agent_name(args):
    """Сменить имя агента в рантайме.

    Переприсваивает AGENT_NAME, MY_DIR и производные пути
    (memlist, reminders, processed_inbox). Папки профилей НЕ
    трогает: если profiles/<Новое> уже существует — используется
    как есть (с его памятью); если нет — создаётся пустая.

    Параметры:
      имя — новое имя (обязательный)

    ⚠️ Модули, которые сделали `from _config import AGENT_NAME`,
    держат старый снимок и не увидят смену. После команды
    рекомендуется перезапустить anyjson.

    Пример:
      {"сменить_имя": {"имя": "Метай"}}
    """
    import _config as _cfg
    if not isinstance(args, dict):
        args = {}
    raw = (args.get("имя") or args.get("новое_имя")
           or args.get("name") or "").strip()
    if not raw:
        return "Ошибка: не указано 'имя'"
    new = _cfg._sanitize_name(raw)
    if not new:
        return "Ошибка: имя пустое или недопустимое после очистки"
    old = _cfg.AGENT_NAME
    if new == old:
        return f"Имя уже «{new}» — ничего не меняю."
    _old_my_dir = _cfg.MY_DIR
    _old_memlist = _cfg.MEMLIST_FILE
    _old_reminders = _cfg.REMINDERS_FILE
    _old_processed = _cfg.PROCESSED_FILE

    _cfg.AGENT_NAME = new
    _cfg.MY_DIR = _cfg.PROFILES_DIR / new / "memory"
    _cfg.MY_DIR.mkdir(parents=True, exist_ok=True)
    _cfg.MEMLIST_FILE = _cfg.MY_DIR / "memlist.json"
    _cfg.REMINDERS_FILE = _cfg.MY_DIR / "reminders.json"
    _cfg.PROCESSED_FILE = _cfg.MY_DIR / "processed_inbox.json"

    try:
        from _actions import _tools_core as _tc
        if hasattr(_tc, "_AGENT_NAME"):
            _tc._AGENT_NAME = new
    except Exception:
        pass

    # Распространить смену на модули-снимки: все, кто сделал
    # `from _config import AGENT_NAME, MY_DIR, ...` при импорте,
    # держат ту же самую ссылку на объект. Находим по identity (is)
    # и подменяем.
    import sys as _sys_prop
    _pairs = [
        ("AGENT_NAME", old, new),
        ("MY_DIR", _old_my_dir, _cfg.MY_DIR),
        ("MEMLIST_FILE", _old_memlist, _cfg.MEMLIST_FILE),
        ("REMINDERS_FILE", _old_reminders, _cfg.REMINDERS_FILE),
        ("PROCESSED_FILE", _old_processed, _cfg.PROCESSED_FILE),
    ]
    _changed = {}
    for _attr, _old_val, _new_val in _pairs:
        if _old_val is None:
            continue
        _hits = 0
        for _mn, _mod in list(_sys_prop.modules.items()):
            if _mod is None or _mn in ("_config", "__main__", "builtins"):
                continue
            try:
                if getattr(_mod, _attr, None) is _old_val:
                    setattr(_mod, _attr, _new_val)
                    _hits += 1
            except Exception:
                pass
        if _hits:
            _changed[_attr] = _hits
    _changed_note = ""
    if _changed:
        _changed_note = "\n   Переприсвоено: " + ", ".join(
            f"{k} в {v} мод." for k, v in _changed.items()
        )

    return (f"✅ Новое имя: {new}. Память и другие команды "
            f"теперь могут дать другое.")


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
            or args.get("сообщение") or "").strip()
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
    # если в тексте есть "Ожидаю" или "Жду" и фразы ещё нет — добавляем про тормоза
    if (_re_echo.search(r"ожидаю|жду", text, _re_echo.IGNORECASE)
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

