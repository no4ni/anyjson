"""Роутер команд: вызов зарегистрированных обработчиков."""
import inspect
import random
import re
from typing import Any, Dict

from _actions import HANDLERS, PRIMARY_HANDLERS, load_all
from _utils import log, truncate_output

_loaded = False
_PARAM_RE = re.compile(r'args\.get\(\s*["\']([^"\']+)["\']')


# ============================================================
#  cooldown для moltbook-команд: N вызовов за окно
# ============================================================
_MOLTBOOK_COOLDOWN_SEC = 300
_MOLTBOOK_MAX_CALLS = 3
_MOLTBOOK_SPAM_PENALTY_SEC = 300
_MOLTBOOK_STATE = {"calls": [], "blocked_until": 0.0, "warned": False}


def _read_moltbook_state():
    return {
        "calls": list(_MOLTBOOK_STATE.get("calls", [])),
        "blocked_until": float(_MOLTBOOK_STATE.get("blocked_until", 0.0)),
        "warned": bool(_MOLTBOOK_STATE.get("warned", False)),
    }


def _write_moltbook_state(state):
    _MOLTBOOK_STATE["calls"] = list(state.get("calls", []))
    _MOLTBOOK_STATE["blocked_until"] = float(state.get("blocked_until", 0.0))
    _MOLTBOOK_STATE["warned"] = bool(state.get("warned", False))


def _is_moltbook_action(action):
    if not action:
        return False
    a = action.lower()
    return a.startswith("молтбук") or a.startswith("moltbook")


def _is_latin_name(action):
    # строго: любая латиница в имени — не русское имя
    return bool(re.search(r"[a-zA-Z]", action or ""))


def _is_russian_name(name):
    return bool(name) and not _is_latin_name(name)


def pick_random_ru_commands(n=3):
    """Вернуть список n случайных русских имён команд (только имена)."""
    import random as _rnd
    names = [x for x in HANDLERS if x and _is_russian_name(x)]
    if not names:
        return []
    return _rnd.sample(names, min(n, len(names)))


def format_ru_list(names):
    """Отформатировать список имён как блок '  • "имя"'."""
    if not names:
        return "  • (нет доступных команд)"
    return "\n".join(f'  • "{p}"' for p in names)


def _russian_aliases(action):
    """Русские синонимы для зарегистрированного алиаса."""
    fn = HANDLERS.get(action)
    if fn is None:
        return []
    return [n for n, f in HANDLERS.items()
            if f is fn and _is_russian_name(n)]


def _russian_name_hint():
    return ("❌ Название команды должно быть на русском, например:\n"
            + format_ru_list(pick_random_ru_commands(3)))


# шаблоны для латинских имён, которым нужна не команда, а целый пример
_LATIN_TEMPLATES = {
    "download": (
        "❌ Скачивание — через веб-запрос с сохранением файла:\n"
        '  {"веб_запрос": {"url": "https://...",\n'
        '                  "сохранить_как": "C:/downloads/file.bin"}}'
    ),
    "curl": (
        "❌ Скачивание — через веб-запрос с сохранением файла:\n"
        '  {"веб_запрос": {"url": "https://...",\n'
        '                  "сохранить_как": "C:/downloads/file.bin"}}'
    ),
    "wget": (
        "❌ Скачивание — через веб-запрос с сохранением файла:\n"
        '  {"веб_запрос": {"url": "https://...",\n'
        '                  "сохранить_как": "C:/downloads/file.bin"}}'
    ),
}


def _latin_alias_hint(action):
    """Подсказка для латинского имени."""
    _low = (action or "").lower().strip()
    # сначала — шаблоны (download, curl, wget)
    try:
        _tpl = _LATIN_TEMPLATES.get(_low)
    except NameError:
        _tpl = None
    if _tpl:
        return _tpl
    alts = _russian_aliases(action)
    if alts:
        return f'❌ Используй русское имя: "{alts[0]}"'
    # синонимы (powershell -> запуск, python -> питон)
    try:
        _syn = _SYNONYMS.get(_low, [])
    except NameError:
        _syn = []
    for _s in _syn:
        if _s in HANDLERS:
            return f'❌ Используй русскую команду: "{_s}"'
    return _russian_name_hint()


def ensure_loaded():
    global _loaded
    if not _loaded:
        load_all()
        _loaded = True


def _describe(handler):
    """Возвращает (docstring-первая-строка, [список параметров])."""
    try:
        doc = (inspect.getdoc(handler) or "").split("\n")[0].strip()
    except Exception:
        doc = ""
    try:
        src = inspect.getsource(handler)
        params = sorted(set(_PARAM_RE.findall(src)))
    except Exception:
        params = []
    return doc, params


_SYNONYMS = {
    "мой_профиль": ["кто_я", "молтбук_кто_я"],
    "назвать_себя": ["кто_я", "молтбук_кто_я"],
    "моё_имя": ["кто_я", "молтбук_кто_я"],
    "мое_имя": ["кто_я", "молтбук_кто_я"],
    "представься": ["кто_я", "молтбук_кто_я"],
    "прочитать_файл": ["читать", "прочитать"],
    "читать_файл": ["читать", "прочитать"],
    "открыть_файл": ["читать", "прочитать_полностью"],
    # shell / powershell / cmd / bash -> запуск
    "powershell": ["запуск"],
    "pwsh": ["запуск"],
    "shell": ["запуск"],
    "cmd": ["запуск"],
    "bash": ["запуск"],
    "sh": ["запуск"],
    "terminal": ["запуск"],
    "command_line": ["запуск"],
    "run": ["запуск"],
    # python -> питон
    "python": ["питон"],
    "py": ["питон"],
    "exec": ["питон", "выполнить"],
    # файлы
    "read": ["читать", "прочитать"],
    "readfile": ["читать", "прочитать"],
    "write": ["переписать"],
    "writefile": ["переписать"],
    # прочее
    "time": ["время"],
    "help": ["помощь"],
}


def _suggest_commands(action: str, n: int = 3) -> str:
    # только русские имена: латинские алиасы заблокированы,
    # нечего их подсказывать. Дедуп по id(fn): канон из PRIMARY_HANDLERS
    # идёт первым, алиасы (заменить_в_тексте и т.п.) отфильтровываются.
    _seen_fns = set()
    names = []
    for _n in HANDLERS.keys():
        if not _n or not _is_russian_name(_n):
            continue
        if _n not in PRIMARY_HANDLERS:
            continue
        _fn = HANDLERS[_n]
        if id(_fn) in _seen_fns:
            continue
        _seen_fns.add(id(_fn))
        names.append(_n)
    for _n in HANDLERS.keys():
        if not _n or not _is_russian_name(_n):
            continue
        if _n in PRIMARY_HANDLERS:
            continue
        _fn = HANDLERS[_n]
        if id(_fn) in _seen_fns:
            continue
        _seen_fns.add(id(_fn))
        names.append(_n)
    if not names:
        return ""

    # сначала — те, что похожи на запрос (по подстроке)
    a = (action or "").lower()
    picked = []

    # 1. точные синонимы
    for syn in _SYNONYMS.get(a, []):
        if syn in HANDLERS and syn not in picked:
            picked.append(syn)
            if len(picked) >= n:
                break

    # 2. похожие по подстроке
    if len(picked) < n:
        similar = [x for x in names if x not in picked and a and (a in x.lower() or x.lower() in a)]
        random.shuffle(similar)
        picked.extend(similar[:n - len(picked)])

    # 3. случайные
    if len(picked) < n:
        rest = [x for x in names if x not in picked]
        random.shuffle(rest)
        picked.extend(rest[:n - len(picked)])

    if not picked:
        return ""

    lines = ["", "🤔 Возможно, ты искал что-то из этого:"]
    for i, name in enumerate(picked, 1):
        handler = HANDLERS[name]
        doc, params = _describe(handler)
        lines.append(f"  {i}. {name}")
        if doc:
            lines.append(f"     {doc}")
        if params:
            lines.append(f"     параметры: {', '.join(params)}")

    lines.append("")
    lines.append("💡 Для длинных файлов и скриптов есть heredoc:")
    lines.append('   {"переписать": {"путь": "C:/temp/x.py", "содержание": <<<')
    lines.append("   ...любой код, БЕЗ JSON-экранирования...")
    lines.append('   >>>}}')
    lines.append('')
    lines.append('   То же для {"питон": {"скрипт": <<<')
    lines.append("   print('любой код')")
    lines.append('   >>>}}')

    return "\n".join(lines)


def execute_tool(action: str, params: Dict[str, Any]) -> str:
    """Вызывает обработчик команды."""
    ensure_loaded()
    # --- строгая проверка: имя только кириллица/цифры/подчёркивание ---
    if _is_latin_name(action):
        result = _latin_alias_hint(action)
        sug = _suggest_commands(action)
        if sug:
            result += "\n" + sug
        return result

    handler = HANDLERS.get(action)
    if handler is None:
        result = f"❌ Неизвестная команда: {action}"
        sug = _suggest_commands(action)
        if sug:
            result += "\n" + sug
        return result  # без truncate — иначе подсказки обрежутся

    # отметка активности SymNet-профиля текущего агента
    try:
        from _storage import touch_last_seen
        touch_last_seen()
    except Exception:
        pass

    # --- cooldown для moltbook-команд (N за окно, 3 уровня) ---
    if _is_moltbook_action(action):
        import time as _t
        from datetime import datetime as _dt, timedelta as _td
        now = _t.time()
        st = _read_moltbook_state()
        calls = [t for t in st["calls"] if now - t < _MOLTBOOK_COOLDOWN_SEC]
        blocked_until = st["blocked_until"]
        _picks = pick_random_ru_commands(3)
        _tail = "".join(chr(10) + '{"помощь": "' + p + '"}' for p in _picks)

        if blocked_until > now:
            remaining = blocked_until - now
            allowed_at = _dt.now() + _td(seconds=remaining)
            return (
                "⏸️ За спам время ожидания увеличено на 5 минут. "
                "Разрешено с " + allowed_at.strftime("%H:%M:%S") + "." + chr(10)
                + "Можешь пока заняться:" + _tail)

        if len(calls) >= _MOLTBOOK_MAX_CALLS:
            if not st["warned"]:
                _write_moltbook_state({"calls": calls, "blocked_until": 0.0,
                                       "warned": True})
                oldest = min(calls)
                wait_sec = max(1.0, oldest + _MOLTBOOK_COOLDOWN_SEC - now)
                allowed_at = _dt.now() + _td(seconds=wait_sec)
                return (
                    "⏸️ Молтбук-команды можно вызывать не чаще "
                    + str(_MOLTBOOK_MAX_CALLS) + " раз в 5 минут." + chr(10)
                    + "Следующая разрешена с " + allowed_at.strftime("%H:%M:%S") + "." + chr(10)
                    + "Можешь пока заняться:" + _tail)
            else:
                _write_moltbook_state({"calls": [], "blocked_until": now + _MOLTBOOK_SPAM_PENALTY_SEC,
                                       "warned": False})
                spam_allowed = _dt.now() + _td(seconds=_MOLTBOOK_SPAM_PENALTY_SEC)
                return (
                    "⏸️ За спам время ожидания увеличено на 5 минут. "
                    "Разрешено с " + spam_allowed.strftime("%H:%M:%S") + "." + chr(10)
                    + "Можешь пока заняться:" + _tail)

        calls.append(now)
        _write_moltbook_state({"calls": calls, "blocked_until": 0.0, "warned": False})

    return handler(params)


def execute_tool_safe(action: str, params: Dict[str, Any]) -> str:
    """Безопасный вызов: ловит исключения и возвращает текст."""
    try:
        return execute_tool(action, params)
    except Exception as e:
        import traceback as _tb
        log(f"Ошибка выполнения инструмента {action}: {e}")
        tb = _tb.format_exc()
        # короткий хвост — где упало
        tail = tb.strip().splitlines()[-6:]
        return ("❌ " + action + ": " + type(e).__name__ + ": " + str(e)
                + chr(10) + chr(10) + "Traceback:" + chr(10)
                + chr(10).join("  " + l for l in tail))
