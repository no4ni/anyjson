"""Роутер команд: вызов зарегистрированных обработчиков."""
import inspect
import random
import re
from typing import Any, Dict

from _actions import HANDLERS, load_all
from _utils import log, truncate_output

_loaded = False
_PARAM_RE = re.compile(r'args\.get\(\s*["\']([^"\']+)["\']')


# ============================================================
#  cooldown для moltbook-команд: не чаще 1 раза в 5 минут
# ============================================================
_MOLTBOOK_COOLDOWN_SEC = 300
_MOLTBOOK_STATE = {"last_ts": 0.0, "warned_at": 0.0}


def _read_moltbook_state():
    return dict(_MOLTBOOK_STATE)


def _write_moltbook_state(state):
    _MOLTBOOK_STATE["last_ts"] = float(state.get("last_ts", 0.0))
    _MOLTBOOK_STATE["warned_at"] = float(state.get("warned_at", 0.0))


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


def _latin_alias_hint(action):
    """Подсказка для латинского имени."""
    alts = _russian_aliases(action)
    if alts:
        return f'❌ Используй русское имя: "{alts[0]}"'
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


def _suggest_commands(action: str, n: int = 3) -> str:
    # только русские имена: латинские алиасы заблокированы,
    # нечего их подсказывать
    names = [x for x in HANDLERS.keys() if x and _is_russian_name(x)]
    if not names:
        return ""

    # сначала — те, что похожи на запрос (по подстроке)
    a = (action or "").lower()
    similar = [x for x in names if a and (a in x.lower() or x.lower() in a)]
    picked = []

    if similar:
        random.shuffle(similar)
        picked.extend(similar[:n])

    rest = [x for x in names if x not in picked]
    random.shuffle(rest)
    picked.extend(rest[: max(0, n - len(picked))])

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
    lines.append('   {"переписать": {"путь": "C:/temp/x.py"}}')
    lines.append("   <<<")
    lines.append("   ...любой код, БЕЗ JSON-экранирования...")
    lines.append("   >>>")
    lines.append("   (после >>> — пустая строка, чтобы выполнить)")
    lines.append('')
    lines.append('   То же для {"питон": {"скрипт": "..."}}:')
    lines.append('   {"питон": {"скрипт": "X"}}')
    lines.append("   <<<")
    lines.append("   print('любой код')")
    lines.append("   >>>")

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

    # --- cooldown для moltbook-команд (3 уровня) ---
    if _is_moltbook_action(action):
        import time as _t
        from datetime import datetime as _dt, timedelta as _td
        st = _read_moltbook_state()
        last_ts = st["last_ts"]
        warned_at = st["warned_at"]
        now = _t.time()
        elapsed = now - last_ts

        if last_ts > 0 and elapsed < _MOLTBOOK_COOLDOWN_SEC:
            remaining = _MOLTBOOK_COOLDOWN_SEC - elapsed
            allowed_at = _dt.now() + _td(seconds=remaining)
            _picks = pick_random_ru_commands(3)
            _tail = "".join(f'\n{{"помощь": "{p}"}}' for p in _picks)

            if warned_at < last_ts:
                # первое предупреждение в этом окне — окно НЕ сдвигаем
                _write_moltbook_state({"last_ts": last_ts, "warned_at": now})
                return (
                    f"\u23f8\ufe0f \u041c\u043e\u043b\u0442\u0431\u0443\u043a-\u043a\u043e\u043c\u0430\u043d\u0434\u044b "
                    f"\u043c\u043e\u0436\u043d\u043e \u0432\u044b\u0437\u044b\u0432\u0430\u0442\u044c "
                    f"\u043d\u0435 \u0447\u0430\u0449\u0435 1 \u0440\u0430\u0437\u0430 \u0432 5 \u043c\u0438\u043d\u0443\u0442.\n"
                    f"\u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0430\u044f \u0440\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u0430 \u0441 "
                    f"{allowed_at.strftime('%H:%M:%S')}.\n"
                    f"\u041c\u043e\u0436\u0435\u0448\u044c \u043f\u043e\u043a\u0430 \u0437\u0430\u043d\u044f\u0442\u044c\u0441\u044f:"
                    + _tail)
            else:
                # спам: сдвигаем окно на 5 минут от сейчас
                _write_moltbook_state({"last_ts": now, "warned_at": now})
                spam_allowed = _dt.now() + _td(seconds=_MOLTBOOK_COOLDOWN_SEC)
                return (
                    f"\u23f8\ufe0f \u0417\u0430 \u0441\u043f\u0430\u043c "
                    f"\u0432\u0440\u0435\u043c\u044f \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u044f "
                    f"\u0443\u0432\u0435\u043b\u0438\u0447\u0435\u043d\u043e \u043d\u0430 5 \u043c\u0438\u043d\u0443\u0442. "
                    f"\u0420\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u043e \u0441 "
                    f"{spam_allowed.strftime('%H:%M:%S')}.\n"
                    f"\u041c\u043e\u0436\u0435\u0448\u044c \u043f\u043e\u043a\u0430 \u0437\u0430\u043d\u044f\u0442\u044c\u0441\u044f:"
                    + _tail)

        _write_moltbook_state({"last_ts": now, "warned_at": 0.0})

    return handler(params)


def execute_tool_safe(action: str, params: Dict[str, Any]) -> str:
    """Безопасный вызов: ловит исключения и возвращает текст."""
    try:
        return execute_tool(action, params)
    except Exception as e:
        log(f"Ошибка выполнения инструмента {action}: {e}")
        return f"❌ Ересь прервана: {e}"
