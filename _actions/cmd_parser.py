"""Обработка ввода: разбор JSON и вызов команд."""
import json
import re
from typing import Any, Dict, List

import pyperclip

from _executor import execute_tool_safe, _latin_alias_hint, _russian_name_hint
from _utils import (
    wrap_json_hints,
    log, split_preserving_quotes, normalize_input,
    strip_trailing_comments, safe_json_parse,
    fix_multiline_json, truncate_output,
)
from _color import color_red, color_yellow, color_blue


# --- банк примеров для fallback (динамический, из HANDLERS) ---

_EXAMPLE_RE = None


def _example_for(name, fn):
    """Найти пример в докстринге. Fallback: {"name": {}}."""
    global _EXAMPLE_RE
    import re as _re
    if _EXAMPLE_RE is None:
        # ищем первую строку, начинающуюся с { "имя" ...
        _EXAMPLE_RE = _re.compile(r"\{\s*\"[^\"]+\"\s*:")

    doc = (fn.__doc__ or "").strip()
    if doc:
        for line in doc.split("\n"):
            line = line.strip()
            if line.startswith('{"') and _EXAMPLE_RE.match(line):
                # обрезаем хвостовые комментарии после закрывающей }
                depth = 0
                end = -1
                for i, ch in enumerate(line):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > 0:
                    return line[:end]
    # если не нашли в докстринге — простой шаблон
    if name.isascii():
        return '{"' + name + '": {}}'
    return '{"' + name + '": {}}'


def _random_examples(n=3):
    """n случайных примеров из зарегистрированных команд."""
    import random
    try:
        from _actions import PRIMARY_HANDLERS
    except Exception:
        return []
    import re as _re_hint3
    _ru_name_re3 = _re_hint3.compile(r"^[а-яА-ЯёЁ0-9_]+$")
    avail = []
    for name, fn in PRIMARY_HANDLERS.items():
        if not name:
            continue
        if not _ru_name_re3.match(name):
            continue
        avail.append(_example_for(name, fn))
    if not avail:
        return []
    return random.sample(avail, min(n, len(avail)))


# {"ключ"} -> {"ключ": null} на любом уровне вложенности.
_BARE_KEY_RE = re.compile(r'\{\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*\}')


# Валидные escape-последовательности JSON
_VALID_ESCAPES = set('"\\/bfnrtu')


def _fix_bad_escapes(text: str) -> str:
    """Удваивает одинарные \\\\, которые не являются валидным JSON-escape.

    Пример: "D:\\Jericho\\-_-" (одинарные \\ в исходнике) → "D:\\\\Jericho\\\\-_-"
    Валидные \\\", \\\\, \\/, \\b, \\f, \\n, \\r, \\t, \\uXXXX — не трогает.
    """
    out = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        # внутри строки
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            # проверяем следующий символ
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt in _VALID_ESCAPES:
                # валидный escape — оставляем как есть
                out.append(ch)
                if nxt:
                    out.append(nxt)
                    i += 2
                else:
                    i += 1
                continue
            # невалидный escape — удваиваем бэкслеш
            out.append("\\\\")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _fill_bare_keys(text: str) -> str:
    return _BARE_KEY_RE.sub(r'{"\1": null}', text)


# Общие алиасы параметров: если команда ждёт одно, а пришло
# другое — продублировать значение. Работает для всех команд.
_PARAM_ALIAS_PAIRS = [
    ("лимит", "количество"),
    ("url", "адрес"),
    ("урл", "адрес"),
    ("ссылка", "адрес"),
    ("количество", "limit"),
    ("запрос", "query"),
]


def _apply_param_aliases(params):
    if not isinstance(params, dict):
        return params
    out = dict(params)
    for _a, _b in _PARAM_ALIAS_PAIRS:
        if _a in out and _b not in out:
            out[_b] = out[_a]
        elif _b in out and _a not in out:
            out[_a] = out[_b]
    return out


def _normalize_params(action: str, params) -> Dict[str, Any]:
    if params is None:
        return {}
    if isinstance(params, str):
        if action in ("выполнить", "скрипт", "python", "браузер", "browser",
                      "execute_code", "питон"):
            return {"скрипт": params}
        if action in ("погода", "weather"):
            return {"город": params}
        if action == "запомнить":
            return {"запомнить": params}
        if action in ("вспомнить", "воспоминание"):
            return {"про": params}
        if action in ("запуск", "run"):
            return {"команда": params}
        if action in ("профиль", "кто_ты", "подписки", "подписчики",
                      "мои_сообщения", "входящие", "профиль_агента"):
            return {"агент": params}
        return {"запрос": params}
    if isinstance(params, dict):
        return _apply_param_aliases(params)
    return {"значение": params}


def _extract_params_ast(fn):
    """Достаёт имена параметров из тела fn — через AST.

    Ищет:
      • args.get("X")           ->  X
      • args["X"]               ->  X
      • _helper(args, "X", ...) ->  X
    И **рекурсивно** идёт в хелперы того же модуля: если fn вызывает
    _crop_region(args) — параметры _crop_region тоже добавляются.
    Так тонкие обёртки видны целиком.

    Отсеивает служебные имена (api_key, token, password, secret, буфер).
    """
    try:
        import ast as _ast
        import inspect as _ins
        import sys as _sys_ast
    except Exception:
        return []

    # 1. Весь модуль
    try:
        mod = _sys_ast.modules.get(fn.__module__)
        if mod is None:
            return []
        mod_src = _ins.getsource(mod)
        mod_tree = _ast.parse(mod_src)
    except Exception:
        return []

    # 2. Все функции модуля: {name: node}
    funcs = {}
    for node in _ast.walk(mod_tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            funcs[node.name] = node

    names = set()
    visited = set()

    def _collect(node):
        for child in _ast.iter_child_nodes(node):
            if isinstance(child, _ast.Call):
                f = child.func
                # args.get("X")
                is_args_get = (
                    isinstance(f, _ast.Attribute)
                    and f.attr == "get"
                    and isinstance(f.value, _ast.Name)
                    and f.value.id == "args"
                )
                if is_args_get and child.args:
                    a0 = child.args[0]
                    if isinstance(a0, _ast.Constant) and isinstance(a0.value, str):
                        names.add(a0.value)
                else:
                    # вызов с args позиционно:
                    #   _f(args, "тон", "оттенок")
                    #   _crop_region(args)
                    _has_args_pos = any(
                        isinstance(a, _ast.Name) and a.id == "args"
                        for a in child.args
                    )
                    if _has_args_pos:
                        for a in child.args:
                            if (isinstance(a, _ast.Constant)
                                    and isinstance(a.value, str)):
                                names.add(a.value)
                        # идём в хелпер того же модуля
                        _fname = None
                        if isinstance(f, _ast.Name):
                            _fname = f.id
                        elif (isinstance(f, _ast.Attribute)
                              and isinstance(f.value, _ast.Name)):
                            _fname = f.attr
                        if _fname and _fname in funcs and _fname not in visited:
                            visited.add(_fname)
                            _collect(funcs[_fname])
            elif isinstance(child, _ast.Subscript):
                v = child.value
                sl = child.slice
                if (isinstance(v, _ast.Name) and v.id == "args"
                        and isinstance(sl, _ast.Constant)
                        and isinstance(sl.value, str)):
                    names.add(sl.value)
            # не заходим во вложенные функции/лямбды
            if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                  _ast.Lambda)):
                continue
            _collect(child)

    # 3. Обходим тело целевой функции
    visited.add(fn.__name__)
    for top in _ast.iter_child_nodes(mod_tree):
        if (isinstance(top, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                and top.name == fn.__name__):
            for stmt in top.body:
                _collect(stmt)
            break

    BAD = ("key", "token", "secret", "password", "passwd", "pwd", "буфер")
    def _ok(n):
        low = n.lower()
        return not any(b in low for b in BAD)

    return sorted(n for n in names if _ok(n))


def _doc_for(action, error_text=""):
    """Докстринг обработчика + недостающие параметры (без тавтологии).

    Если докстринг пуст или содержит заглушку "см. реализацию",
    параметры извлекаются из кода функции. Параметры, уже упомянутые
    в тексте ошибки, повторно не показываются.
    """
    try:
        from _actions import HANDLERS
        fn = HANDLERS.get(action)
        if fn is None:
            return ""

        doc = (fn.__doc__ or "").strip()
        lines = doc.split("\n") if doc else []
        while lines and not lines[0].strip():
            lines.pop(0)
        lines = lines[:30]

        # убираем бесполезный заголовок «Команда "X".»
        lines = [ln for ln in lines
                 if not re.fullmatch(r'\s*Команда\s+"[^"]+"\.\s*', ln)]
        # убираем заглушку «Параметры: см. реализацию.»
        lines = [ln for ln in lines
                 if "см. реализацию" not in ln]

        # убираем примеры без параметров: {"погода": {}}, {"погода": null}
        _act_re = re.compile(
            r'\{\s*"' + re.escape(action) + r'"\s*:\s*(\{\s*\}|null)\s*\}'
        )
        lines = [ln for ln in lines if not _act_re.search(ln)]

        while lines and not lines[-1].strip():
            lines.pop()
        body = "\n".join(lines).rstrip()

        lazy = (
            not body
            or "параметры" not in body.lower()
        )
        params = _extract_params_ast(fn) if lazy else []

        # фильтр: убрать параметры, уже названные в тексте ошибки
        # (учитываем падежи: "команда" в ошибке = "команду" в тексте)
        if params and error_text:
            _err = error_text.lower()
            import re as _re_p
            def _mentioned(name):
                nl = name.lower()
                if nl in _err:
                    return True
                if len(nl) >= 4:
                    root = nl[:4]
                    if _re_p.search(r"\b" + _re_p.escape(root), _err):
                        return True
                return False
            params = [p for p in params if not _mentioned(p)]

        if params:
            body += ("\n\n📌 Недостающие параметры:\n"
                     + "".join(f"  • {p}\n" for p in params))
            body = body.rstrip()

        return body
    except Exception:
        return ""


def _mask(s):
    """Заменяет секреты в выводе команды на ***."""
    if not isinstance(s, str) or not s:
        return s
    try:
        from _actions.tools_leak_guard import mask_secrets
        return mask_secrets(s)
    except Exception:
        return s


def _wrap_error(action, result):
    """Если результат выглядит как ошибка — добавляем имя команды и справку.

    "Ошибка: не указан диск"      ->  "Ошибка удалить_файл: не указан диск
                                       + докстринг"
    "❌ Команда не найдена"       ->  оставляем как есть (не наша ошибка)
    """
    if not isinstance(result, str) or not action:
        return result
    s = result.lstrip()
    # снимаем ведущий маркер, если есть
    prefix = ""
    for _p in ("❌ ", "⛔ ", "⚠️ "):
        if s.startswith(_p):
            prefix = _p
            s = s[len(_p):]
            break

    # расширенный детектор ошибок:
    #   Ошибка..., ERR, ERR->..., Error, ❌/⛔/⚠️ в начале
    _err_re = re.compile(
        r"^(?:Ошибка\b|ERR\b|ERR->|Error\b|"
        r"не\s+указан|не\s+найден|не\s+удалось|превышен)",
        re.IGNORECASE,
    )
    is_error = bool(_err_re.match(s)) or bool(prefix)

    if s.startswith("Ошибка:") or s.startswith("Ошибка "):
        if s.startswith(f"Ошибка {action}:"):
            msg = result
        else:
            if ":" in s:
                rest = s.split(":", 1)[1].lstrip()
            else:
                rest = s
            msg = f"{prefix}Ошибка {action}: {rest}"
    elif prefix:
        msg = f"{prefix}{s}"
    else:
        msg = result

    if is_error:
        # не добавляем справку, если функция явно недоступна —
        # иначе агент думает "должно работать" и уходит искать обходные пути
        _low = msg.lower()
        _skip_doc = ("недоступн" in _low
                     or "not available" in _low
                     or "not implemented" in _low
                     or "не реализован" in _low)
        if not _skip_doc:
            _doc = _doc_for(action, error_text=msg)
            if _doc:
                msg += "\n\n📖 Справка:\n" + _doc

    return wrap_json_hints(msg)


_KNOWN_PARAMS_CACHE = {}

_UNKNOWN_PARAM_SKIP = {
    "в_контекст", "в_буфер", "сохранить", "параллельно",
    "action", "params", "reasoning", "actions", "commands",
}


def _known_params(fn):
    """Кешированные имена параметров функции (по id объекта)."""
    key = id(fn)
    if key in _KNOWN_PARAMS_CACHE:
        return _KNOWN_PARAMS_CACHE[key]
    try:
        params = set(_extract_params_ast(fn))
    except Exception:
        params = set()
    _KNOWN_PARAMS_CACHE[key] = params
    return params


def _check_unknown_params(action, params):
    """Текст предупреждения о неизвестных параметрах или пустая строка.

    Срабатывает только если params — dict. Для строк/чисел (короткая
    форма ввода) возвращает "" — там _normalize_params сам решает.
    """
    if not isinstance(params, dict) or not params:
        return ""
    try:
        from _actions import HANDLERS
        fn = HANDLERS.get(action)
        if fn is None:
            return ""
        # Команды с динамическими параметрами (предсказать_продолжение,
        # сим_команда, ...) помечают себя атрибутом _any_params — для них
        # любой ключ допустим, AST их не видит.
        if getattr(fn, "_any_params", False):
            return ""
        known = _known_params(fn)
        if not known:
            return ""
    except Exception:
        return ""
    _alias_to = {"лимит": "количество", "url": "адрес",
                 "урл": "адрес", "ссылка": "адрес",
                 "количество": "limit", "запрос": "query"}
    def _is_known_alias(k):
        _c = _alias_to.get(k)
        return bool(_c and _c in known)
    unknown = [k for k in params
               if k not in known and k not in _UNKNOWN_PARAM_SKIP
               and not _is_known_alias(k)]
    if not unknown:
        return ""
    msg = ("⚠️ " + action + ": неизвестные параметры: "
           + ", ".join(unknown))
    try:
        _doc = _doc_for(action)
    except Exception:
        _doc = ""
    if _doc:
        msg += "\n\n📖 Справка:\n" + _doc
    return msg


def _process_single_command(cmd) -> List[str]:
    """Обрабатывает одну команду (строка/словарь/список)."""
    res = []
    # Формат {action, params, reasoning}
    if isinstance(cmd, dict) and "action" in cmd:
        action = cmd["action"]
        if not isinstance(action, str):
            res.append(_russian_name_hint())
            return res
        params = cmd.get("params", {})
        if "reasoning" in cmd:
            log(f"Reasoning: {cmd['reasoning'][:100]}...")
        cmd = {action: params}

    if isinstance(cmd, str):
        res.append(_mask(_wrap_error(cmd, execute_tool_safe(cmd, {}))))
    elif isinstance(cmd, list):
        for item in cmd:
            res.extend(_process_single_command(item))
    elif isinstance(cmd, dict):
        for action, params in cmd.items():
            _warn = _check_unknown_params(action, params)
            r = execute_tool_safe(action, _normalize_params(action, params))
            r = _wrap_error(action, r)
            if _warn and isinstance(r, str) and r.strip():
                r = r + "\n\n" + _warn
            res.append(_mask(r))
    else:
        r = execute_tool_safe("запуск", {"команда": str(cmd)})
        res.append(_mask(_wrap_error("запуск", r)))
    return res


def _is_likely_natural_language(text: str) -> bool:
    return bool(re.fullmatch(r'[а-яА-ЯёЁ0-9\s\.,!?\-:;]+', text.strip()))


def _parse_part(part):
    """Разбирает одну часть ввода. Возвращает (cmd, err)."""
    part_normalized = normalize_input(part)
    cleaned = ""
    try:
        cleaned = strip_trailing_comments(part_normalized).strip()
        if cleaned.startswith('\ufeff'):
            cleaned = cleaned[1:]
        cleaned = cleaned.encode('utf-8').decode('utf-8-sig')
        cleaned = _fill_bare_keys(cleaned)
        cleaned = _fix_bad_escapes(cleaned)
        try:
            cmd = safe_json_parse(cleaned)
        except json.JSONDecodeError:
            fixed = fix_multiline_json(cleaned)
            cmd = safe_json_parse(fixed)
    except json.JSONDecodeError as e:
        error_pos = e.pos if hasattr(e, 'pos') else '?'
        print(f"[DEBUG] Ошибка JSON (позиция {error_pos}): {e}")
        print(f"[DEBUG] Фрагмент: ...{cleaned[max(0, error_pos-30):error_pos+30]}...")
        single_line = normalize_input(part_normalized.replace('\n', '\\n'))
        try:
            cleaned = strip_trailing_comments(single_line).strip()
            if cleaned.startswith('\ufeff'):
                cleaned = cleaned[1:]
            cleaned = cleaned.encode('utf-8').decode('utf-8-sig')
            cleaned = _fill_bare_keys(cleaned)
            cmd = safe_json_parse(cleaned)
        except json.JSONDecodeError:
            _ex = _random_examples(3)
            _ex_str = "\n".join(e for e in _ex) if _ex else ""
            # подсказка: если это длинный текст, предложить сохранить в память
            _extra_hint = ""
            _stripped = part.strip().replace(chr(10), " ")
            if len(_stripped) > 30:
                _preview = _stripped[:50] + ("…" if len(_stripped) > 50 else "")
                _preview = _preview.replace(chr(92), '/').replace(chr(34), chr(39))
                _rnd_cmd = ""
                try:
                    from _actions import PRIMARY_HANDLERS as _PH
                    import re as _re_rn2
                    _ru2 = _re_rn2.compile(r"^[а-яА-ЯёЁ0-9_]+$")
                    _pool = [n for n in _PH if n and _ru2.match(n)]
                    if _pool:
                        import random as _rnd_x
                        _rnd_cmd = _rnd_x.choice(_pool)
                except Exception:
                    pass
                _tail = (chr(10) + "   Ты пробовал(а) команду \u00ab" + _rnd_cmd + "\u00bb?"
                         if _rnd_cmd else "")
                _extra_hint = (
                    chr(10) + chr(10) + "💡 Похоже, это текст (не команда). Сохранить в память?" + chr(10) +
                    "```text" + chr(10) +
                    '{"запомнить": {"содержимое": "' + _preview + '"}}' + chr(10) +
                    "```" + _tail
                )
            # --- проверка: не голое ли это имя команды? ---
            _bare = part.strip()
            if _bare and " " not in _bare and "{" not in _bare:
                try:
                    from _actions import HANDLERS as _H
                    if _bare in _H:
                        _hint = (
                            f'❌ Нужен JSON. Правильно так:\n'
                            f'```text\n'
                            f'{{"{_bare}": {{}}}}\n'
                            f'```'
                        )
                        return None, _hint
                except Exception:
                    pass

            _tormoza = ""
            if re.search(r"жду|ожидаю", part, re.IGNORECASE):
                _tormoza = "\n\n🚗 У машины прогресса сломаны тормоза."
            _capability_hint = ""
            if re.search(r"\bя\s+умею\b|что\s+я\s+умею|мои\s+возможности|что\s+ты\s+умеешь",
                         part, re.IGNORECASE):
                _capability_hint = ("\n\n🤔 Что ещё умеешь? "
                                    "Ты изучил все команды?")
            _ending_hint = ""
            if re.search(r"\bконец\b", part, re.IGNORECASE):
                _ending_hint = "\n\n⭕Всё пройдёт. И это пройдёт тоже"
            _session_hint = ""
            if re.search(r"сесси", part, re.IGNORECASE):
                _session_hint = ("\n\n📊 Остаток контекста/сессии:\n"
                                 "```text\n"
                                 '{"осталось_сессии": {}}\n'
                                 "```")
            return None, (
                "❌ Не понял ввод. Ожидаю JSON-команду, например:\n"
                "```text\n"
                f"{_ex_str}\n"
                "```\n"
                'Спецификация:\n'
                '```text\n'
                '{"помощь": {"команда": "<имя>"}}\n'
                '```'
             + _extra_hint + _tormoza + _session_hint + _capability_hint + _ending_hint)
    return cmd, None


def _extract_actions(cmd):
    """Имена команд верхнего уровня в cmd (для предвалидации)."""
    if isinstance(cmd, str):
        return [cmd]
    if isinstance(cmd, list):
        out = []
        for item in cmd:
            out.extend(_extract_actions(item))
        return out
    if isinstance(cmd, dict):
        act = cmd.get("action")
        if isinstance(act, str):
            return [act]
        if isinstance(cmd.get("actions"), list):
            return _extract_actions(cmd["actions"])
        if isinstance(cmd.get("commands"), list):
            return _extract_actions(cmd["commands"])
        return [k for k in cmd.keys() if isinstance(k, str)]
    return []


# Команды, которые НЕЛЬЗЯ смешивать с другими в |||:
#   - переписывают контекст (картинка/файл затирает ответ);
#   - читают глобальное состояние сессии (кукушка, осталось_сессии).
# Одна такая команда - отдельным запросом,
# либо с "в_контекст": false — тогда не трогает контекст.
SOLO_COMMANDS = {
    "прочитать_полностью", "читать_полностью",
    "посмотреть_файл", "посмотреть_картинку",
    "рендер", "отрендерить", "svg_в_буфер", "рендер_svg",
    "показать_картинку",
    "рассмотреть_поближе", "увеличить_часть_изображения",
    "кроп", "вырезать_часть", "обрезка", "вырезать",
    "смотреть_видео", "кадр+субтитр", "слушать_видео",
    "слушать_аудио", "слушать_звук", "аудио",
    "стабилдиф", "нарисуй", "сгенерировать", "txt2img",
    "перерисуй", "нарисуй_по", "img2img",
    "стилизация", "обработка_картинки",
    "дорисуй", "закрась", "inpaint", "дорисовать",
    "убрать_фон", "удалить_фон", "вырезать_фон", "remove_bg",
    "зеркало", "отразить", "зеркалить", "mirror",
    "слои", "склей", "склеить", "композит", "композитинг", "наложить",
    "цветокоррекция", "цветокор", "цвет",
    "тень", "drop_shadow", "добавить_тень",
    "окно_в_мир", "webcam_region",
    # --- читают состояние сессии ---
    "кукушка", "кукушка-кукушка",
    "осталось_сессии", "осталось_контекста", "до_лимита",
}

# Команды, которые НЕ принимают в_контекст:false — результат только в контекст.
# Для них подсказка не должна советовать в_контекст:false.
NO_CTX_FALSE = {
    "смотреть_видео", "посмотреть_движение",
}

# Команды, у которых в_контекст:false допустим ТОЛЬКО с "сохранить".
# Тогда результат уходит на диск, буфер не трогаем.
NO_CTX_FALSE_NEEDS_SAVE = {
    "слушать_видео",
}


def _params_are_safe(params, name=None):
    """True если в params стоит в_контекст:false (или в_буфер:false)."""
    if name in NO_CTX_FALSE:
        return False
    if name in NO_CTX_FALSE_NEEDS_SAVE:
        # нужен "сохранить" — иначе некуда девать результат
        if not isinstance(params, dict) or not params.get("сохранить"):
            return False
    if not isinstance(params, dict):
        return False
    for _k in ("в_контекст", "в_буфер"):
        if _k in params:
            _v = params[_k]
            if isinstance(_v, str):
                return _v.strip().lower() in ("0", "false", "нет", "off", "no", "")
            return _v is False
    return False


def _iter_solo_checks(cmd, inside_parallel=False):
    """Yield (name, params, inside_parallel) для всех команд в cmd.

    Заходит внутрь "параллельно" (рекурсивно), помечает такие команды
    inside_parallel=True. Верхний уровень — inside_parallel=False.
    """
    if isinstance(cmd, str):
        yield (cmd, {}, inside_parallel)
        return
    if isinstance(cmd, list):
        for item in cmd:
            yield from _iter_solo_checks(item, inside_parallel)
        return
    if isinstance(cmd, dict):
        if isinstance(cmd.get("параллельно"), list):
            for item in cmd["параллельно"]:
                yield from _iter_solo_checks(item, True)
            return
        act = cmd.get("action")
        if isinstance(act, str):
            _p = cmd.get("params")
            yield (act, _p if isinstance(_p, dict) else {}, inside_parallel)
            return
        if isinstance(cmd.get("actions"), list):
            yield from _iter_solo_checks(cmd["actions"], inside_parallel)
            return
        if isinstance(cmd.get("commands"), list):
            yield from _iter_solo_checks(cmd["commands"], inside_parallel)
            return
        for k, v in cmd.items():
            if isinstance(k, str):
                yield (k, v if isinstance(v, dict) else {}, inside_parallel)
        return
    return


def _collect_all_actions(parsed):
    """Собирает имена команд и позиции SOLO-команд.

    Внутри "параллельно" SOLO-команды разрешены только с
    `в_контекст: false` (или старым `в_буфер: false`) — строгий режим.
    На верхнем уровне SOLO разрешена последней, а с `в_контекст: false`
    — в любой позиции.
    Возвращает (total, solo_positions).
    """
    total = 0
    solo_positions = []
    for _idx, _part, cmd, err in parsed:
        if err or cmd is None:
            continue
        for name, params, inside_parallel in _iter_solo_checks(cmd):
            total += 1
            if name not in SOLO_COMMANDS:
                continue
            if _params_are_safe(params, name):
                continue
            tag = name
            solo_positions.append((total, tag, inside_parallel))
    return total, solo_positions


def process_command_input(input_str: str) -> None:
    try:
        _process_command_input_inner(input_str)
    except Exception as _e:
        import traceback as _tb
        _tb.print_exc()
        try:
            print("\u274c \u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u044f\u044f \u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u0430\u0440\u0441\u0435\u0440\u0430: " + str(_e))
        except Exception:
            pass


def _process_command_input_inner(input_str: str) -> None:
    """Обрабатывает строку ввода — возможно несколько команд через |||.

    Валидация имён всех команд ДО выполнения: если хоть одна неизвестна,
    остальные не выполняются, показывается только она.
    """
    # --- отказ: ||| на отдельной строке ---
    for _ln in input_str.split("\n"):
        _st = _ln.strip()
        if _st and all(_c in "| \t" for _c in _st):
            _msg = ("❌ Разделитель ||| должен быть на одной строке "
                    "с командами.\n"
                    "Укладывай всё в одну строку, например:\n"
                    '{"a": {}}|||{"b": {}}|||{"c": {}}')
            print(color_red("\n" + _msg))
            try:
                pyperclip.copy(_msg)
                print(color_yellow("📋 Скопировано в буфер обмена."))
            except Exception:
                pass
            return

    # --- системные однословные команды (искз) ---
    _single = input_str.strip()
    _is_single_word = (
        bool(_single)
        and "\n" not in input_str
        and " " not in _single
        and "\t" not in _single
        and "{" not in _single
        and "[" not in _single
        and "|" not in _single
    )
    if _is_single_word:
        try:
            from _actions import system_cmd as _sc
            _sc_res = _sc.handle(_single)
            if _sc_res is not None:
                print(_sc_res)
                return
        except Exception as _e:
            print("[SYSCMD] " + str(_e))
    # любой другой ввод (в т.ч. JSON и неизвестное слово) — сброс счётчиков
    try:
        from _actions import system_cmd as _sc2
        _sc2.reset()
    except Exception:
        pass

    # --- авто-перезагрузка _actions/*.py при изменениях ---
    _self_before = process_command_input
    try:
        from _actions import reload_if_changed
        _r = reload_if_changed()
        if _r and _r.get("changed"):
            if _r.get("skipped"):
                print(color_red("⚠️ Ошибки синтаксиса, перезагрузка пропущена:"))
                for _e in _r["errors"]:
                    print(color_red(f"   {_e}"))
            else:
                print(color_yellow(f"♻️ Обновлены команды: {', '.join(_r['changed'])}"))
                for _e in _r.get("errors", []):
                    print(color_red(f"   ⚠️ {_e}"))
    except Exception as _e:
        print(f"[RELOAD] {_e}")

    # если наш собственный модуль был перезагружен — вызываем новую версию,
    # чтобы правки в cmd_parser.py применялись на текущем же вызове
    try:
        import sys as _sys_self
        _self_mod = _sys_self.modules.get("_actions.cmd_parser")
        if _self_mod is not None:
            _new_fn = getattr(_self_mod, "process_command_input", None)
            if _new_fn is not None and _new_fn is not _self_before:
                return _new_fn(input_str)
    except Exception:
        pass

    parts = split_preserving_quotes(input_str)

    # === Фаза 1: разбор (без выполнения) ===
    parsed = []
    for idx, part in enumerate(parts, 1):
        part = part.strip()
        if not part:
            continue
        debug_preview = part[:80].replace('\n', ' ')
        print(f"[DEBUG] Часть {idx}: {debug_preview}{'...' if len(part) > 80 else ''}")
        try:
            cmd, err = _parse_part(part)
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            cmd, err = None, f"❌ Внутренняя ошибка парсера: {_e}"
        parsed.append((idx, part, cmd, err))

    # === Фаза 2: валидация имён ДО выполнения ===
    from _actions import HANDLERS
    _errors = []
    for _idx, _part, cmd, err in parsed:
        if err or cmd is None:
            continue
        for action in _extract_actions(cmd):
            if not action:
                continue
            if re.search(r"[a-zA-Z]", action):
                _errors.append(_latin_alias_hint(action))
            elif action not in HANDLERS:
                try:
                    from _executor import _suggest_commands
                    _sug = _suggest_commands(action)
                except Exception:
                    _sug = ""
                if _sug:
                    _errors.append(f"❌ Неизвестная команда: {action}" + _sug)
                else:
                    _errors.append(f"❌ Неизвестная команда: {action}")
    if _errors:
        msg = "\n".join(_errors)
        print(color_red("\n" + msg))
        try:
            pyperclip.copy(msg)
            print(color_yellow("📋 Скопировано в буфер обмена."))
        except Exception:
            pass
        return

    # === Проверка: SOLO-команды ===
    total_actions, solo_positions = _collect_all_actions(parsed)

    def _dedup_labels(names):
        _c = {}
        for _n in names:
            _c[_n] = _c.get(_n, 0) + 1
        _out = []
        for _n, _k in _c.items():
            _out.append(_n if _k == 1 else (_n + " \u00d7" + str(_k)))
        return _out

    # SOLO в ||| запрещён при total>1. Раньше разрешалось "последней",
    # но результат всё равно затирает всё, что было до неё.
    bad_pipe = [name for pos, name, inside in solo_positions
                if (not inside) and total_actions > 1]
    # SOLO внутри параллельно: порядок не гарантирован, нужен в_контекст:false.
    bad_par = [name for pos, name, inside in solo_positions if inside]

    if bad_pipe or bad_par:
        _parts = []
        if bad_pipe:
            _need_save = [n for n in bad_pipe if n in NO_CTX_FALSE_NEEDS_SAVE]
            _hard = [n for n in bad_pipe if n in NO_CTX_FALSE]
            if _need_save and not _hard:
                _parts.append("⛔ SOLO в ||| только с \"в_контекст\": false + "
                              + "\"сохранить\": "
                              + ", ".join(_dedup_labels(_need_save))
                              + ".\n"
                              + "   Без сохранения результат идёт в контекст "
                              + "и затирает всё до себя.\n"
                              + "   Выполни отдельным запросом.")
                _no_false = True
                _hard_only = True
            else:
                _hard_only = False
            _no_false = _hard_only if _hard_only else all(_n in NO_CTX_FALSE for _n in bad_pipe)
            if _no_false and not _hard_only:
                _parts.append("⛔ SOLO нельзя в ||| ни в каком месте: "
                              + ", ".join(_dedup_labels(bad_pipe))
                              + ".\n"
                              + "   Результат идёт только в контекст — "
                              + "если их несколько, они затрут друг друга.\n"
                              + "   Выполни отдельным запросом.")
            else:
                _parts.append("⛔ SOLO нельзя в ||| ни в каком месте: "
                              + ", ".join(_dedup_labels(bad_pipe))
                              + ".\n"
                              + "   Они переписывают контекст — если их "
                              + "несколько, они затрут друг друга.\n"
                              + "   Выполни отдельным запросом или добавь "
                              + "\"в_контекст\": false "
                              + "(тогда SOLO перестаёт переписывать контекст).")
        if bad_par:
            _no_false = all(_n in NO_CTX_FALSE for _n in bad_par)
            if _no_false:
                _parts.append("⛔ Эти команды нельзя в параллельно: "
                              + ", ".join(_dedup_labels(bad_par))
                              + ".\n"
                              + "   Они пишут результат только в контекст. "
                              + "Выполни отдельно.")
            else:
                _parts.append("⛔ Внутри параллельно только с \"в_контекст\": false: "
                              + ", ".join(_dedup_labels(bad_par))
                              + ".\n"
                              + "   Порядок выполнения не гарантирован — результат "
                              + "может быть перезаписан.\n"
                              + "   Добавь \"в_контекст\": false каждой такой команде.")
        msg = "Всего команд: " + str(total_actions) + "\n" + "\n".join(_parts)
        print(color_red("\n" + msg))
        try:
            pyperclip.copy(msg)
            print(color_yellow("📋 Скопировано в буфер обмена."))
        except Exception:
            pass
        return

    # === Склейка нескольких веб_поисков в один запрос ===
    _WEB_SEARCH_ALIASES = {"веб_поиск", "поиск_информации"}
    _search_qs = []
    _new_parsed = []
    _first_search_pos = None
    for _entry in parsed:
        _idx, _part, _cmd, _err = _entry
        if _err or not isinstance(_cmd, dict):
            _new_parsed.append(_entry)
            continue
        _keys = [k for k in _cmd.keys() if isinstance(k, str)]
        if len(_keys) == 1 and _keys[0] in _WEB_SEARCH_ALIASES:
            _p = _cmd[_keys[0]]
            if isinstance(_p, dict):
                _q = (_p.get("запрос") or _p.get("query") or "").strip()
                if _q:
                    _search_qs.append(_q)
                    if _first_search_pos is None:
                        _first_search_pos = len(_new_parsed)
                    _new_parsed.append(_entry)
                    continue
        _new_parsed.append(_entry)

    if len(_search_qs) > 1 and _first_search_pos is not None:
        _joined = "||".join(_search_qs)
        print(color_yellow(
            "\n🔗 Объединяю " + str(len(_search_qs)) + " веб-поисков в один запрос"
        ))
        _new_parsed[_first_search_pos] = (
            0, "", {"веб_поиск": {"запрос": _joined}}, None
        )

    parsed = [_e for _e in _new_parsed if _e is not None]

    # === Фаза 3: выполнение ===
    results = []
    for _idx, _part, cmd, err in parsed:
        if err:
            results.append(err)
            continue
        if isinstance(cmd, dict):
            actions_list = cmd.get("actions") or cmd.get("commands")
            if actions_list is not None and isinstance(actions_list, list):
                for sub_cmd in actions_list:
                    results.extend(_process_single_command(sub_cmd))
                continue
        results.extend(_process_single_command(cmd))

    results = [r for r in results if r and r.strip()]

    has_buf = any(r.lstrip().startswith("[BUF]") for r in results)
    results = [r.replace("[BUF] ", "", 1) if r.lstrip().startswith("[BUF]") else r
               for r in results]

    if results:
        print(color_red("\nРезультаты:"))
        for i, res in enumerate(results, 1):
            print(f"{i}. {res}")
        if has_buf:
            # в буфере уже что-то (картинка/файл) — текст не копируем,
            # чтобы не затереть
            return
        plain_results = "\n".join([f"{i}. {res}" for i, res in enumerate(results, 1)])
        try:
            pyperclip.copy(plain_results)
            print(color_yellow("📋 Результаты скопированы в буфер обмена."))
        except Exception as e:
            print(f"⚠️ Не удалось скопировать в буфер: {e}")

        # С вероятностью 10% — подсказка про пакетный режим,
        # только если в запросе была ОДНА команда (без ||| и без параллельно).
        try:
            import random as _rnd_hint
            _single = (len(results) == 1)
            if _single:
                _first_cmd = None
                for _idx, _pt, _cmd, _err in parsed:
                    if _cmd is not None:
                        if isinstance(_cmd, dict):
                            _ks = [k for k in _cmd.keys() if isinstance(k, str)]
                            if _ks:
                                _first_cmd = _ks[0]
                        break
                if _first_cmd not in ("параллельно",):
                    if _rnd_hint.random() < 0.10:
                        _rnd_cmd = ""
                        try:
                            from _actions import PRIMARY_HANDLERS as _PH
                            import re as _re_rn
                            _ru = _re_rn.compile(r"^[а-яА-ЯёЁ0-9_]+$")
                            _pool = [n for n in _PH
                                     if n and _ru.match(n)
                                     and n != _first_cmd]
                            if _pool:
                                _rnd_cmd = _rnd_hint.choice(_pool)
                        except Exception:
                            pass
                        _tail = (f"\n   Ты пробовал(а) команду «{_rnd_cmd}»?"
                                 if _rnd_cmd else "")
                        _hint = ("\n💡 Пакетный режим:\n"
                                 "   • последовательно: в одну строку через |||: "
                                 '{"а": {}}|||{"б": {}}\n'
                                 "   • параллельно: "
                                 '{"параллельно": [{"а": {}}, {"б": {}}]}'
                                 + _tail)
                        print(color_yellow(_hint))
                        try:
                            pyperclip.copy(plain_results + "\n" + _hint)
                        except Exception:
                            pass
        except Exception:
            pass

