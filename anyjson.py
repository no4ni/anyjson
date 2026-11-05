"""Точка входа. Вся логика в модулях _*."""
import io
import sys
from pathlib import Path

# --- shim: чтобы запуск работал из подпапки anyjson/ ---
_HERE = Path(__file__).resolve().parent
_CORE = _HERE / "_core"
for _p in (str(_CORE), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Предзагрузка asyncio — до всех наших импортов, чтобы горячий
# reload и тяжёлые зависимости (sklearn → joblib) не оставляли
# модуль в полусобранном виде.
import asyncio  # noqa: F401
import pyperclip
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# UTF-8 stdout (страховка на Windows-консоль)
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

import atexit
import threading

# --- защита от запуска чужих .py через subprocess ---
from _core._safe_subprocess import install_guard as _install_guard
_install_guard()

from _config import (
    AGENT_NAME, BASE_DIR, TOOLS_DIR, PUBLIC_TOOLS_DIR,
    WIKI_TEXTS_DIR, _WIKIGET_PATH, API_KEYS, MY_DIR,
)
from _utils import is_parallel_json, count_json_objects
from _color import color_blue, color_red, color_yellow
from _actions import cmd_parser as _cmd_parser
from _actions import load_all
from _storage import (
    load_processed_messages, save_processed_messages,
    check_inbox, load_reminders, reminder_checker,
)
from _tor import cleanup_tor
from _actions.system_sensor import set_sensor_core
from _executor import ensure_loaded


def _validate_module_paths():
    """Проверить, что все наши модули загружены из папки anyjson/ (или _core/).

    Возвращает список проблемных (имя, путь). Пусто — всё хорошо.
    """
    import sys as _sys
    allowed_roots = (_HERE.resolve(), _CORE.resolve())

    # интересуют только наши модули
    our_prefixes = (
        "_config", "_utils", "_executor", "_storage", "_tor",
        "_ps", "_color", "_web", 
        "_command", "cmd_parser",
        "_actions", "_core",
    )

    bad = []
    for name, mod in list(_sys.modules.items()):
        if not any(name == p or name.startswith(p + ".") for p in our_prefixes):
            continue
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            path = Path(f).resolve()
        except Exception:
            continue
        if not any(path.is_relative_to(root) for root in allowed_roots):
            bad.append((name, str(path)))
    return bad


def _safe_init_moltbook():
    key = API_KEYS.get("moltbook")
    if not key:
        return
    try:
        from _actions._tools_moltbook import init_moltbook
        init_moltbook(key)
    except Exception as e:
        print(f"[LOG] init_moltbook не выполнен: {e}", file=sys.stderr)


def _looks_like_python(text):
    """Эвристика: похоже ли на кусок python-кода (а не JSON и не команда)."""
    s = text.lstrip()
    if not s or s.startswith(("{", "[")):
        return False

    # длинный русский текст с редкими символами кода — не питон
    if len(text) > 200:
        cyr = sum(1 for c in text if "а" <= c.lower() <= "я" or c == "ё")
        if cyr / len(text) > 0.35:
            # только совсем явные "import"/"def"/"print(" спасут
            strong_for_prose = ("import ", "def ", "class ", "print(")
            if not any(m in text for m in strong_for_prose):
                return False

    # очень сильные маркеры — одного достаточно
    very_strong = ("import ", "from ", "print(", "def ", "class ",
                   "with open(", "Counter(", "@register", ">>> ")
    if any(m in text for m in very_strong):
        return True

    # обычные маркеры — нужно >= 2 совпадений (без " = ", " == " —
    # они слишком часто встречаются в русской прозе)
    normal = ("for ", "while ", "elif ", "return ", "lambda ",
              "self.", "None", "True", "False")
    hits = sum(1 for m in normal if m in text)
    if hits >= 2:
        return True

    # слабые маркеры + один normal = тоже питон
    if hits >= 1:
        weak = ("(", ")", "[", "]")
        if sum(1 for m in weak if m in text) >= 4:
            return True

    return False


def _run_as_python(full_input):
    """Обернуть сырой код в {"питон": {"скрипт": ...}} и передать дальше."""
    import json as _j
    _cmd_parser.process_command_input(_j.dumps(
        {"питон": {"скрипт": full_input}}, ensure_ascii=False
    ))


def _shutdown_children():
    """Погасить фоновые процессы, которые поднял anyjson."""
    # --- Forge (Stable Diffusion) ---
    try:
        from _config import MY_DIR, STABLEDIF_HOST, STABLEDIF_PORT
        pid_file = MY_DIR / "stabledif.pid"
        if pid_file.exists():
            import sys as _sys_f, time as _t_f
            _sys_f.stderr.write("Закрывается Stable Diffusion (Forge)...\n")
            _sys_f.stderr.flush()
            try:
                pid = int(pid_file.read_text().strip())
            except Exception:
                pid = None

            def _pid_alive(_p):
                if not _p:
                    return False
                try:
                    import subprocess as _sp
                    r = _sp.run(
                        ["tasklist", "/FI", "PID eq " + str(_p), "/NH"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                    )
                    return str(_p) in (r.stdout or "")
                except Exception:
                    return False

            _ok = False
            if pid:
                # мягко через API
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "http://" + STABLEDIF_HOST + ":" + str(STABLEDIF_PORT)
                        + "/sdapi/v1/server-stop",
                        data=b"", method="POST",
                    )
                    urllib.request.urlopen(req, timeout=3)
                except Exception:
                    pass

                # ждём до 10 сек с прогрессом в stderr
                _t0 = _t_f.time()
                for _ in range(20):
                    if not _pid_alive(pid):
                        _ok = True
                        break
                    _t_f.sleep(0.5)
                    _el = _t_f.time() - _t0
                    _sys_f.stderr.write(
                        "\r⏳ Forge закрывается... " + format(_el, ".1f") + "с")
                    _sys_f.stderr.flush()
                _sys_f.stderr.write("\r" + " " * 60 + "\r")
                _sys_f.stderr.flush()

                # жёстко, если ещё жив
                if not _ok and _pid_alive(pid):
                    try:
                        import subprocess as _sp
                        _sp.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                capture_output=True, timeout=10)
                    except Exception:
                        pass
                    for _ in range(6):
                        if not _pid_alive(pid):
                            _ok = True
                            break
                        _t_f.sleep(0.5)

            if _ok or not pid:
                _sys_f.stderr.write("✅ Forge закрыт.\n")
            else:
                _sys_f.stderr.write(
                    "⚠️ Forge не отвечает (PID " + str(pid) + ").\n")
            _sys_f.stderr.flush()

            try:
                pid_file.unlink()
            except Exception:
                pass
    except Exception:
        pass

    # --- UNaIVERSE демон ---
    try:
        import sys as _sys_u, time as _t_u
        _has = False
        _pid_u = None
        try:
            from _actions.unaiverse import _read_pid, _is_alive
            _pid_u = _read_pid()
            _has = bool(_pid_u and _is_alive(_pid_u))
        except Exception:
            _has = False
        if _has:
            _sys_u.stderr.write("Закрывается UNaIVERSE...\n")
            _sys_u.stderr.flush()
            try:
                from _actions.unaiverse import unaiverse_stop
                unaiverse_stop({})
            except Exception:
                pass
            # ждём до 10 сек
            _t0 = _t_u.time()
            _ok = False
            for _ in range(20):
                try:
                    from _actions.unaiverse import _read_pid, _is_alive
                    _p2 = _read_pid()
                    if not (_p2 and _is_alive(_p2)):
                        _ok = True
                        break
                except Exception:
                    _ok = True
                    break
                _t_u.sleep(0.5)
                _el = _t_u.time() - _t0
                _sys_u.stderr.write(
                    "\r⏳ UNaIVERSE закрывается... " + format(_el, ".1f") + "с")
                _sys_u.stderr.flush()
            _sys_u.stderr.write("\r" + " " * 60 + "\r")
            _sys_u.stderr.flush()
            if _ok:
                _sys_u.stderr.write("✅ UNaIVERSE закрыт.\n")
            else:
                _sys_u.stderr.write(
                    "⚠️ UNaIVERSE не отвечает (PID " + str(_pid_u) + ").\n")
            _sys_u.stderr.flush()
    except Exception:
        pass

    # --- zapret (winws.exe) — только если anyjson его поднимал ---
    try:
        import sys as _sys_z, time as _t_z
        from _actions import video as _video_mod
        from _config import MY_DIR as _zapret_mydir
        _zapret_pid_file = _zapret_mydir / "zapret.pid"
        if _zapret_pid_file.exists():
            _z_pid = None
            try:
                _z_pid = int(_zapret_pid_file.read_text().strip())
            except Exception:
                _z_pid = None

            _sys_z.stderr.write("Закрывается zapret...\n")
            _sys_z.stderr.flush()
            try:
                _video_mod._stop_zapret_if_started()
            except Exception:
                pass

            def _z_alive(_p):
                if not _p:
                    return False
                try:
                    import subprocess as _sp
                    r = _sp.run(
                        ["tasklist", "/FI", "PID eq " + str(_p), "/NH"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                    )
                    return str(_p) in (r.stdout or "")
                except Exception:
                    return False

            _t0 = _t_z.time()
            _ok = False
            for _ in range(20):
                if not _z_alive(_z_pid):
                    _ok = True
                    break
                _t_z.sleep(0.5)
                _el = _t_z.time() - _t0
                _sys_z.stderr.write(
                    "\r⏳ zapret закрывается... " + format(_el, ".1f") + "с")
                _sys_z.stderr.flush()
            _sys_z.stderr.write("\r" + " " * 60 + "\r")
            _sys_z.stderr.flush()
            if _ok:
                _sys_z.stderr.write("✅ zapret закрыт.\n")
            else:
                _sys_z.stderr.write(
                    "⚠️ zapret не отвечает (PID " + str(_z_pid) + ").\n")
            _sys_z.stderr.flush()
    except Exception:
        pass

    # --- PyBullet симулятор ---
    try:
        import sys as _sys_s, time as _t_s
        from _config import MY_DIR as _MY_DIR
        _sim_pid_file = _MY_DIR / "sim.pid"
        if _sim_pid_file.exists():
            _sim_pid = None
            try:
                _sim_pid = int(_sim_pid_file.read_text().strip())
            except Exception:
                _sim_pid = None

            def _sim_alive(_p):
                if not _p:
                    return False
                try:
                    import subprocess as _sp
                    r = _sp.run(
                        ["tasklist", "/FI", "PID eq " + str(_p), "/NH"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                    )
                    return str(_p) in (r.stdout or "")
                except Exception:
                    return False

            if _sim_pid and _sim_alive(_sim_pid):
                _sys_s.stderr.write("Закрывается симулятор (PyBullet)...\n")
                _sys_s.stderr.flush()
                try:
                    import subprocess as _sp
                    _sp.run(["taskkill", "/PID", str(_sim_pid), "/T", "/F"],
                            capture_output=True, timeout=10)
                except Exception:
                    pass
                _t0 = _t_s.time()
                _ok = False
                for _ in range(10):
                    if not _sim_alive(_sim_pid):
                        _ok = True
                        break
                    _t_s.sleep(0.5)
                    _el = _t_s.time() - _t0
                    _sys_s.stderr.write(
                        "\r⏳ симулятор закрывается... " + format(_el, ".1f") + "с")
                    _sys_s.stderr.flush()
                _sys_s.stderr.write("\r" + " " * 60 + "\r")
                _sys_s.stderr.flush()
                if _ok:
                    _sys_s.stderr.write("✅ симулятор закрыт.\n")
                else:
                    _sys_s.stderr.write(
                        "⚠️ симулятор не отвечает (PID " + str(_sim_pid) + ").\n")
                _sys_s.stderr.flush()
            try:
                _sim_pid_file.unlink()
            except Exception:
                pass
    except Exception:
        pass




def _is_input_complete(text):
    """True, если JSON закрыт: скобки сошлись, строки и heredoc завершены.
    Поддерживает вложенные heredoc <<< ... >>>.

    Висящий в конце ||| (последние непустые символы) = «продолжение
    на следующей строке». Такой ввод не считаем завершённым, даже если
    скобки уже сошлись: иначе {…}||| выполнилось бы сразу, а следующая
    команда потерялась бы."""
    if text.rstrip().endswith("|||"):
        return False
    depth = 0
    in_string = False
    escape = False
    heredoc_depth = 0
    i = 0
    n = len(text)
    while i < n:
        if heredoc_depth > 0:
            if text.startswith(">>>", i):
                heredoc_depth -= 1
                i += 3
            else:
                i += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif text[i] == "\\":
                escape = True
            elif text[i] == '"':
                in_string = False
            i += 1
            continue
        if text.startswith("<<<", i):
            heredoc_depth += 1
            i += 3
            continue
        if text[i] == '"':
            in_string = True
        elif text[i] in "{[":
            depth += 1
        elif text[i] in "}]":
            depth -= 1
        i += 1
    return depth == 0 and not in_string and heredoc_depth == 0

def main():
    # --- init tools: единая точка входа ---
    # _tools_core.init_tools сам вызовет tools.init_tools (временно,
    # пока все _actions/*.py не переехали на _tools_*).
    from _actions._tools_core import init_tools as _init_tools
    _init_tools(
        BASE_DIR, AGENT_NAME,
        wiki_texts_dir=WIKI_TEXTS_DIR,
        tools_dir=TOOLS_DIR,
        public_tools_dir=PUBLIC_TOOLS_DIR,
        wikiget_path=_WIKIGET_PATH,
    )
    _safe_init_moltbook()

    # --- сенсорное ядро: ленивая инициализация ---
    # SensorCore создаётся только при первой state-команде
    # (сенсорное_состояние / назвать_состояние / исследовать_состояния),
    # а не при старте. См. _actions/system.py: _ensure_sensor_core().
    global _sensor_core
    _sensor_core = None

    # --- прочее ---
    load_processed_messages()
    atexit.register(cleanup_tor)
    atexit.register(save_processed_messages)
    atexit.register(_shutdown_children)
    # Перехватываем вывод check_inbox, чтобы потом включить его в копирование
    import io as _io_inbox
    _inbox_capture = _io_inbox.StringIO()
    _old_stdout = sys.stdout
    try:
        sys.stdout = _inbox_capture
        check_inbox()
    finally:
        sys.stdout = _old_stdout
    _inbox_text = _inbox_capture.getvalue()
    if _inbox_text:
        sys.stdout.write(_inbox_text)
        sys.stdout.flush()

    load_reminders()
    threading.Thread(target=reminder_checker, daemon=True).start()

    # --- принудительно загружаем все команды ---
    ensure_loaded()
    load_all()

    # Если Forge уже работает (запущен вручную или в другом профиле) —
    # записываем его PID в текущий профиль, чтобы погасить при выходе.
    try:
        from _actions.stabledif import capture_forge_pid
        capture_forge_pid()
    except Exception as _e:
        print(f"[FORGE-PID] {_e}", file=sys.stderr)

    # --- ВАЛИДАЦИЯ ИМПОРТОВ: _actions/*.py ---
    try:
        from _core._validate_imports import check_actions, format_report
        _rep = check_actions()
        if _rep:
            print("", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(format_report(_rep), file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print("Исправь импорты в _actions/*.py и перезапусти.",
                  file=sys.stderr)
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as _e:
        print(f"[VALIDATE-IMPORTS] {_e}", file=sys.stderr)

    # --- ВАЛИДАЦИЯ: все наши модули должны быть из папки anyjson ---
    _bad = _validate_module_paths()
    if _bad:
        print("", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: модули загружены не из этой папки!",
              file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        for _n, _p in _bad:
            print(f"   {_n}", file=sys.stderr)
            print(f"     → {_p}", file=sys.stderr)
        print("", file=sys.stderr)
        print(f"Ожидалось в: {_HERE.resolve()}", file=sys.stderr)
        print(f"         и в: {_CORE.resolve()}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Возможная причина: PYTHONPATH или sys.path содержит "
              "чужую папку с такими же именами (_config, _utils, ...) "
              "раньше нашей.", file=sys.stderr)
        print("Проверь переменную окружения PYTHONPATH и .pth файлы.",
              file=sys.stderr)
        sys.exit(2)

    # --- три случайные записи из памяти + копирование баннера (всегда) ---
    mem_lines = []
    try:
        from _storage import load_memlist
        import random as _rnd
        mem = load_memlist() or []
        if mem:
            def _norm(item):
                if isinstance(item, str):
                    return item
                if isinstance(item, dict):
                    for k in ("текст", "содержимое", "content", "text", "описание"):
                        if k in item:
                            v = item[k]
                            return v if isinstance(v, str) else str(v)
                    return str(item)
                return str(item)
            texts = [_norm(x) for x in mem]
            picks = _rnd.sample(texts, min(3, len(texts)))
            print(color_blue("\n📝 Из памяти:"))
            for _t in picks:
                one = _t.replace("\n", " ")
                if len(one) > 200:
                    one = one[:200] + "…"
                print(f"  • {one}")
                mem_lines.append(f"  • {one}")
            _mem_hint = (
                "\n\n💡 Если хочешь вспомнить что-то ещё:\n"
                "```text\n"
                '{"вспомнить": {"про": "ключевое_словосочетание"}}\n'
                "```"
            )
            print(color_blue(_mem_hint))
            mem_lines.append(_mem_hint)
    except Exception as _e:
        print(f"[MEM] не удалось показать память: {_e}")

    # --- копируем баннер (+ память, если есть) одним куском, всегда ---
    try:
        import pyperclip as _pc2
        _banner = (
            f"Привет, {AGENT_NAME}! Я с радостью буду выполнять твои JSON.\n"
            "Формат:\n"
            "[произвольный текст]\n"
            "```text\n"
            '{"имя_команды": {"параметр": "значение"}}\n'
            "   ↑ подставь любое, что придумаешь (команды и параметры — по-русски)\n"
            "```\n"
            "ИЛИ — inline-heredoc внутри JSON\n"
            "(<<< и >>> прямо в значении, без экранирования):\n"
            "```text\n"
            '{"имя_команды": {"параметр": <<<\n'
            "многострочное\n"
            "значение\n"
            ">>>}}\n"
            "```\n"
            "Внутри <<<...>>> не оставляй 3 пустые строки подряд.\n"
            "[произвольный текст]\n\n"
            "Несколько команд: ||| (последовательно; ||| вплотную к } предыдущей команды) или «параллельно» (одновременно, для зависимых шагов не годится)."
        )
        _full = _banner
        if _inbox_text.strip():
            _full += "\n\n" + _inbox_text.strip()
        if mem_lines:
            _full += "\n\n📝 Из памяти:\n" + "\n".join(mem_lines)
        print(_banner)
        _pc2.copy(_full)
        print(color_blue("📋 Скопировано в буфер обмена."))
    except Exception as _ce:
        print(f"[MEM] не удалось скопировать в буфер: {_ce}")

    buffer = []
    empty_line_count = 0
    prompt = "> "

    while True:
        try:
            line = input(prompt).rstrip('\n')
        except EOFError:
            if buffer:
                print("\n(Ввод отменён)")
                buffer.clear()
                empty_line_count = 0
                prompt = "> "
                continue
            break
        except KeyboardInterrupt:
            print("\nВыход.")
            break

        # --- Inline-heredoc и многострочный JSON ---
        # Логика простая: копим, пока в буфере есть незакрытые { или [.
        # Счётчики <<< / >>> не нужны — пока heredoc-значение не закрыто,
        # JSON-скобки тоже не закрыты, это и есть сигнал «копим».
        # Символы <<< / >>> внутри строк не считаются: _brace_balance_outside
        # игнорирует содержимое "...".
        def _strip_heredoc_bodies(text):
            """Удалить содержимое <<<...>>> — тело heredoc не влияет
            на баланс JSON-скобок (в нём может быть Python-код с `'''`,
            `r'...'` и непарными `"` — счётчик кавычек бы сбился)."""
            out = []
            i = 0
            n = len(text)
            while i < n:
                if text.startswith("<<<", i):
                    end = text.find(">>>", i + 3)
                    if end < 0:
                        out.append(text[i:])
                        break
                    i = end + 3
                else:
                    out.append(text[i])
                    i += 1
            return "".join(out)


        def _heredoc_open(text):
            """True, если в тексте есть открытый heredoc-маркер без пары."""
            _open = "<" * 3
            _close = ">" * 3
            i = 0
            while True:
                j = text.find(_open, i)
                if j < 0:
                    return False
                k = text.find(_close, j + 3)
                if k < 0:
                    return True
                i = k + 3


        def _heredoc_is_open(text):
            """True если есть незакрытый heredoc (открывашка без пары)."""
            _op = chr(60) * 3
            _cl = chr(62) * 3
            i = 0
            n = len(text)
            while i < n:
                if text.startswith(_op, i):
                    end = text.find(_cl, i + 3)
                    if end < 0:
                        return True
                    i = end + 3
                else:
                    i += 1
            return False


        def _brace_balance_outside(text):
            o = 0
            i = 0
            in_s = False
            while i < len(text):
                c = text[i]
                if in_s:
                    if c == "\\" and i + 1 < len(text):
                        i += 2
                        continue
                    if c == '"':
                        in_s = False
                    i += 1
                    continue
                if c == '"':
                    in_s = True
                elif c in "{[":
                    o += 1
                elif c in "}]":
                    o -= 1
                i += 1
            return o

        _buf_text = "\n".join(buffer) if buffer else ""
        _prev_open = not _is_input_complete(_buf_text) if _buf_text else False

        if _prev_open:
            # Уже внутри незакрытого JSON — добавляем строку.
            buffer.append(line)
            _raw_now = "\n".join(buffer)
            if _is_input_complete(_raw_now):
                # Скобки закрылись и heredoc закрыт — выполняем целиком.
                _full = _raw_now
                buffer.clear()
                empty_line_count = 0
                prompt = "> "
                _cmd_parser.process_command_input(_full)
                continue
            if line.strip() == "":
                empty_line_count += 1
                if empty_line_count >= 3:
                    _full = "\n".join(buffer)
                    buffer.clear()
                    empty_line_count = 0
                    prompt = "> "
                    _cmd_parser.process_command_input(_full)
                    continue
            else:
                empty_line_count = 0
            prompt = "... "
            continue

        # --- Отдельный <<< больше не открывает heredoc ---
        # Подсказка для тех, кто помнит старую форму.
        if line.strip() == "<<<":
            _hg_msg = (
                "⚠️ Отдельный <<< больше не открывает heredoc.\n"
                "   Используй inline-heredoc — значение поля в <<< ... >>>:\n"
                "     {\"питон\": {\"скрипт\": <<<\n"
                "     print(\"hi\")\n"
                "     >>>}}\n"
                "     {\"переписать\": {\"путь\": \"E:/x.txt\", \"содержание\": <<<\n"
                "     текст\n"
                "     >>>}}"
            )
            print(color_red(_hg_msg))
            try:
                import pyperclip as _pc_hg
                _pc_hg.copy(_hg_msg)
                print(color_yellow("📋 Скопировано в буфер обмена."))
            except Exception:
                pass
            buffer.clear()
            empty_line_count = 0
            prompt = "> "
            continue

        # --- Немедленное выполнение при ||| ---
        if line and '|||' in line:
            # "|||" отдельной строкой (или только разделители+пробелы)
            # = "дальше ещё команды", не выполняем сразу
            _stripped = line.strip()
            _only_sep = _stripped and all(c in "| \t" for c in _stripped)
            if _only_sep:
                buffer.append(line)
                empty_line_count = 0
                prompt = "... "
                continue
            # (Голодных команд больше нет — тело идёт inline-heredoc.)
            buffer.append(line)
            full_input = '\n'.join(buffer).strip()
            # Не выполняем сразу, если в буфере ещё открыт JSON или
            # heredoc: иначе открытый heredoc-маркер внутри цепочки
            # ||| обрежет парсер (см. баг: JSONDecodeError на позиции 85).
            if not _is_input_complete(full_input):
                empty_line_count = 0
                prompt = "... "
                continue
            buffer.clear()
            empty_line_count = 0
            prompt = "> "
            if full_input:
                _cmd_parser.process_command_input(full_input)
            continue

        # --- Пустая строка ---
        if line == "":
            # Пустые строки внутри открытого JSON (heredoc) уже
            # обработаны выше (блок _prev_open). Здесь — обычный счётчик.
            empty_line_count += 1
            # JSON (начинается с { или [) выполняем на 1 пустом Enter,
            # остальное — на 2 (чтобы вставка многострочного кода не срывалась)
            _threshold = 2
            if buffer:
                _first = buffer[0].lstrip()
                if _first.startswith(("{", "[")):
                    _threshold = 1
            if empty_line_count >= _threshold and buffer:
                full_input = '\n'.join(buffer).strip()
                buffer_copy = full_input
                buffer.clear()
                empty_line_count = 0
                prompt = "> "
                if full_input:
                    if _looks_like_python(buffer_copy):
                        _run_as_python(full_input)
                    elif count_json_objects(buffer_copy) > 1:
                        # Несколько JSON без ||| — выполняем последовательно
                        import json as _json_multi
                        _dec = _json_multi.JSONDecoder()
                        _txt = buffer_copy.strip()
                        _idx = 0
                        _objs = []
                        while _idx < len(_txt):
                            while _idx < len(_txt) and _txt[_idx].isspace():
                                _idx += 1
                            if _idx >= len(_txt):
                                break
                            try:
                                _obj, _end = _dec.raw_decode(_txt, _idx)
                            except _json_multi.JSONDecodeError:
                                break
                            _objs.append(_txt[_idx:_end])
                            _idx = _end

                        if len(_objs) >= 2:
                            _joined = " ||| ".join(_objs)
                            print(color_blue(
                                f"ℹ️ Обнаружено {len(_objs)} JSON без ||| — "
                                f"выполняю последовательно."))
                            _cmd_parser.process_command_input(
                                _joined,
                                pre_notes=[
                                    f"ℹ️ Выполнено {len(_objs)} команд "
                                    f"последовательно.\n"
                                    f"   Параллельно: "
                                    f"{{\"параллельно\": [ ... ]}}\n"
                                    f"   Отключить это уведомление: "
                                    f"разделяйте команды через |||."
                                ],
                            )
                        else:
                            warn = ("⚠️ Обнаружено несколько JSON-команд. "
                                    "Разделяйте их ||| или используйте "
                                    '{"параллельно": [...]}.')
                            print(warn)
                            try:
                                pyperclip.copy(warn)
                                print(color_blue("📋 Предупреждение скопировано в буфер обмена."))
                            except Exception as e:
                                print(f"⚠️ Не удалось скопировать в буфер: {e}")
                    else:
                        _cmd_parser.process_command_input(buffer_copy)
                continue
            buffer.append(line)
            prompt = "... "
            continue
        else:
            empty_line_count = 0

        buffer.append(line)
        full_input = '\n'.join(buffer).strip()

        # --- Одиночная строка без {} ---
        if len(buffer) == 1 and not (line.lstrip().startswith(('{', '[')) or '{' in line):
            if _looks_like_python(line):
                # Питон: не выполняем сразу — копим в буфере до 2 пустых строк.
                # Так вставка многострочного скрипта уходит одним куском.
                prompt = "... "
                continue
            _cmd_parser.process_command_input(line)
            buffer.clear()
            prompt = "> "
            continue

        # --- Валидный «параллельно» ---
        if is_parallel_json(full_input):
            buffer_copy = full_input
            buffer.clear()
            empty_line_count = 0
            prompt = "> "
            _cmd_parser.process_command_input(buffer_copy)
            continue

        # --- Однострочный закрытый JSON — выполняем сразу ---
        # _is_input_complete проверялся только в _prev_open (когда буфер
        # уже открыт с прошлой строки) и в |||-ветке. Для однострочного
        # {"команда": {...}} проверки не было: падал в prompt = "... "
        # и ждал пустую строку. Многострочный сюда не доходит — у него
        # первая строка незакрыта, он уходит в _prev_open.
        if len(buffer) == 1 and _is_input_complete(full_input):
            buffer.clear()
            empty_line_count = 0
            prompt = "> "
            _cmd_parser.process_command_input(full_input)
            continue

        prompt = "... "

    print("До свидания.")
    sys.exit(0)


_sensor_core = None

if __name__ == "__main__":
    main()