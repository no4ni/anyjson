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


# Предупреждения, которые _parse_part собирает по ходу разбора.
# Добавляются в результаты перед выводом "Результаты:".
_LAST_PARSE_WARNINGS = []


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


def _expand_inline_heredocs(text: str) -> str:
    _OPEN = chr(60) * 3
    _CLOSE = chr(62) * 3
    if _OPEN not in text:
        return text
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith(_OPEN, i):
            start_body = i + 3
            depth = 1
            j = start_body
            while j < n:
                if text.startswith(_OPEN, j):
                    depth += 1
                    j += 3
                elif text.startswith(_CLOSE, j):
                    depth -= 1
                    if depth == 0:
                        break
                    j += 3
                else:
                    j += 1
            if depth > 0:
                out.append(text[i:])
                break
            end = j
            raw_body = text[start_body:end]
            is_multiline = ('\n' in raw_body or '\r' in raw_body)
            body = raw_body
            if body.startswith('\r\n'):
                body = body[2:]
            elif body.startswith('\n'):
                body = body[1:]
            if body.endswith('\r\n'):
                body = body[:-2]
            elif body.endswith('\n'):
                body = body[:-1]
            if not is_multiline:
                body = body.strip()
            out.append(json.dumps(body, ensure_ascii=False))
            i = end + 3
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


# Общие алиасы параметров: если команда ждёт одно, а пришло
# другое — продублировать значение. Работает для всех команд.
_PARAM_ALIAS_PAIRS = [
    ("лимит", "количество"),
    ("url", "адрес"),
    ("урл", "адрес"),
    ("ссылка", "адрес"),
    ("количество", "limit"),
    ("запрос", "query"),
    ("шаблон", "маска"),
    ("код", "скрипт"),
    ("новое_имя", "имя"),
    ("старый", "найти"),
    ("новый", "заменить"),
    ("старый_текст", "найти"),
    ("новый_текст", "заменить"),
    ("папка", "путь"),
    ("искать", "ключевые_слова"),
    ("строка", "содержание"),
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


# Файловые команды: единственный строковый аргумент — это путь.
# Единый источник правды: используется и в _normalize_params, и в
# препроцессинге _process_command_input_inner (короткая запись).
_PATH_COMMANDS = {
    "читать", "прочитать", "прочитай",
    "читать_полностью", "прочитать_полностью",
    "читать_по_кб", "прочитать_по_кб",
    "открыть", "показать_файл", "посмотреть_файл",
    "посмотреть_картинку", "показать_картинку",
    "рендер", "отрендерить", "svg_в_буфер", "рендер_svg",
    "удалить_файл", "удалить_папку", "удалить_путь",
    "список_файлов", "осмотреть",
    "заменить_текст", "добавить", "переписать",
}


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
        if action in _PATH_COMMANDS:
            return {"путь": params}
        # Универсальная эвристика: главный параметр — из AST хендлера.
        # "текст" приоритетнее (эхо, озвучить, паста, ...).
        try:
            from _actions import HANDLERS as _H
            _fn = _H.get(action)
            if _fn is not None:
                _cands = _extract_params_ast(_fn)
                if "текст" in _cands:
                    return {"текст": params}
                if len(_cands) == 1:
                    return {_cands[0]: params}
        except Exception:
            pass
        return {"запрос": params}
    if isinstance(params, dict):
        _p = _apply_param_aliases(params)
        # "кадр" — алиас "кадр+субтитр" без наложения субтитров.
        # Дефолт "субтитр": false; можно переопределить в вызове.
        if action == "кадр" and "субтитр" not in _p:
            _p = dict(_p)
            _p["субтитр"] = False
        return _p
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

    BAD = ("key", "token", "secret", "password", "passwd", "pwd", "буфер", "шаблон",
           "video", "delay", "mp3", "wav", "sounds", "аудио")
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
                     or "не реализован" in _low
                     or action in ("помощь", "справка", "хелп", "help")
                     # ответ уже самодостаточен: перенаправление на "питон"
                     # или cmd-подсказка — справка "запуска" только мешает
                     or "это python-скрипт" in _low
                     or "синтаксис cmd.exe" in _low
                     # ответ уже самодостаточен: это папка, а не файл
                     # (read_full / заменить_текст / бэкапы на директории) —
                     # внутри готовый шаблон список_файлов
                     or "это папка, а не файл" in _low)
        if not _skip_doc:
            _doc = _doc_for(action, error_text=msg)
            if _doc:
                # обёртка — только для собственной справки anyjson
                # (там примеры-команды для читаемости). Сам result
                # не перелопачиваем: если команда вернула текст
                # с JSON-командами (читать, осмотреть, питон) — он
                # должен остаться как есть.
                msg += "\n\n📖 Справка:\n" + wrap_json_hints(_doc)

    return msg


_KNOWN_PARAMS_CACHE = {}

_UNKNOWN_PARAM_SKIP = {
    "в_контекст", "в_буфер", "сохранить", "параллельно",
    "action", "params", "reasoning", "actions", "commands",
    "__got_body__",
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
    def _is_known_alias(k):
        # Единый источник — _PARAM_ALIAS_PAIRS (не дублируем пары
        # в валидаторе: иначе новая пара в _apply_param_aliases
        # не узнаётся здесь — расхождение двух точек правды).
        for _pa, _pb in _PARAM_ALIAS_PAIRS:
            if k == _pa and _pb in known:
                return True
            if k == _pb and _pa in known:
                return True
        return False
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


_BATCH_NO_CLIP = {"найти_картинку", "поиск_картинок", "поиск_изображений"}


def _inject_no_clip(cmd):
    """В пакете (>1 команда) найти_картинку не трогает буфер.

    Рекурсивно проходит по cmd и для найти_картинку (и алиасов)
    добавляет в_контекст: false, если ни в_контекст, ни в_буфер
    не заданы явно. Внутрь параллельно и actions/commands — тоже.
    """
    if isinstance(cmd, list):
        return [_inject_no_clip(_it) for _it in cmd]
    if not isinstance(cmd, dict):
        return cmd
    _out = {}
    for _k, _v in cmd.items():
        if isinstance(_k, str) and _k in _BATCH_NO_CLIP and isinstance(_v, dict):
            _nv = dict(_v)
            if "в_контекст" not in _nv and "в_буфер" not in _nv:
                _nv["в_контекст"] = False
            _out[_k] = _nv
        elif isinstance(_k, str) and _k in ("параллельно", "actions", "commands") and isinstance(_v, list):
            _out[_k] = [_inject_no_clip(_it) for _it in _v]
        elif isinstance(_v, (dict, list)):
            _out[_k] = _inject_no_clip(_v)
        else:
            _out[_k] = _v
    return _out


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


def _brace_balance(text):
    """Возвращает (стек незакрытых скобок, флаг 'внутри строки').

    Стек — закрывающие символы, которые надо дописать в конец
    (в обратном порядке), чтобы сбалансировать текст.
    Пример: '{"a": {"b": 1}' -> (['}'], False) — не хватает одной '}'.
    """
    stack = []
    in_str = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
        i += 1
    return stack, in_str


def _parse_part(part):
    """Разбирает одну часть ввода. Возвращает (cmd, err)."""
    part_normalized = normalize_input(part)
    cleaned = ""
    _parse_text = ""  # текст, который последним ушёл в safe_json_parse
    try:
        # Сначала inline-heredoc: внутри <<<...>>> может быть любой
        # текст, включая // и #. Только после этого — strip_trailing_comments,
        # который уже не тронет содержимое JSON-строк.
        cleaned = _expand_inline_heredocs(part_normalized)
        cleaned = strip_trailing_comments(cleaned).strip()
        if cleaned.startswith('\ufeff'):
            cleaned = cleaned[1:]
        cleaned = cleaned.encode('utf-8').decode('utf-8-sig')
        cleaned = _fill_bare_keys(cleaned)
        cleaned = _fix_bad_escapes(cleaned)
        # ВАЖНО: json.loads первичен. safe_json_parse внутри мангляет
        # \" в строках: regex "(.*?)" не уважает escape и принимает
        # \" за закрывающую кавычку — тело JSON-строки разваливается
        # на r\\"E:/..., e.pos уезжает внутрь тела. Настоящая ошибка
        # (Extra data в конце) теряется. Поэтому: сначала plain
        # json.loads; safe_json_parse — только как fallback для
        # одиночных \ в Windows-путях.
        _parse_text = cleaned
        try:
            cmd = json.loads(cleaned)
        except json.JSONDecodeError as _e1:
            try:
                cmd = safe_json_parse(cleaned)
            except json.JSONDecodeError:
                if "control character" in str(_e1).lower():
                    fixed = fix_multiline_json(cleaned)
                    _parse_text = fixed
                    try:
                        cmd = safe_json_parse(fixed)
                    except json.JSONDecodeError:
                        raise _e1
                else:
                    raise _e1
    except json.JSONDecodeError as e:
        error_pos = getattr(e, 'pos', -1)
        _err_msg = str(e)
        # e.doc — именно то, что реально парсил json.loads; e.pos валиден
        # для e.doc, а НЕ для _parse_text: safe_json_parse может внутри
        # нормализовать вход (strip и т.п.), и тогда позиция указывает
        # за пределы _parse_text — срез пустой, «Фрагмент: ......».
        _doc = getattr(e, "doc", None)
        if isinstance(_doc, str) and _doc:
            _src = _doc
        else:
            _src = _parse_text or cleaned or ""
        _n = len(_src)
        _err_frag = ""
        _err_hint = ""
        if isinstance(error_pos, int) and error_pos >= 0 and _n > 0:
            _pos = min(error_pos, _n)
            _lo = max(0, _pos - 40)
            _hi = min(_n, _pos + 40)
            _pre = _src[_lo:_pos]
            _post = _src[_pos:_hi]
            _vis_pre = _pre.replace("\r", "").replace("\n", "\\n")
            _vis_post = _post.replace("\r", "").replace("\n", "\\n")
            _at = _src[_pos] if _pos < _n else ""
            _mark = ((_vis_pre + " ") if _vis_pre else "") + "<-!!ТУТ!!->" + ((" " + _vis_post) if _vis_post else "")
            _err_frag = _mark
            _hint = ""
            if _pos >= _n:
                _hint = "  <- позиция = конец строки (лишний хвост?)"
            elif _err_msg.startswith("Extra data") and _at == "}":
                _hint = ("  <- лишняя } в конце, но это СИМПТОМ. "
                         "Объект закрылся раньше времени (см. ниже).")
            elif _at == "}":
                _hint = "  <- лишняя } ?"
            elif _at == "]":
                _hint = "  <- лишняя ] ?"
            elif _at == ",":
                _hint = "  <- лишняя , ?"
            elif _at == "|":
                _hint = "  <- лишняя | (ожидалась , ?)"
            _err_hint = _hint
            print(f"[DEBUG] Ошибка JSON (позиция {_pos} из {_n}): {e}")
            print(f"[DEBUG] Фрагмент: ...{_mark}...{_hint}")
        else:
            print(f"[DEBUG] Ошибка JSON (позиция {error_pos}): {e}")
            _short = (_src[:120] + "…") if _n > 120 else (_src or "(пусто)")
            _err_frag = _short
            print(f"[DEBUG] Фрагмент: ...{_short}...")
        # --- Узкий автотрим: Extra data на "}" ---
        # Только если: ошибка Extra data, позиция валидна, и символ
        # в позиции — "}". Режем по error_pos, пробуем распарсить
        # _src[:error_pos]. При успехе — предупреждение в результаты,
        # команда всё равно выполняется. При неудаче — обычная ошибка,
        # без среза (не угадываем).
        _trim_suspect_names = []
        if (_err_msg.startswith("Extra data")
                and isinstance(error_pos, int)
                and 0 <= error_pos < _n
                and _src[error_pos] == "}"):
            _trimmed = _src[:error_pos]
            try:
                _cmd_trim = safe_json_parse(_trimmed)
                # Проверка: после среза не появились ли неизвестные
                # команды? Если да — реальная лишняя "}" стоит раньше
                # (например, сразу после маркера конца heredoc), и срез
                # в конце только маскирует проблему. Тогда откат + hint.
                _trim_ok = True
                try:
                    from _actions import HANDLERS as _H_trim
                    _names_trim = (_extract_actions(_cmd_trim)
                                   if _cmd_trim is not None else [])
                    for _nm in _names_trim:
                        if not _nm:
                            continue
                        if re.search(r"[a-zA-Z]", _nm):
                            _trim_ok = False
                            break
                        if (_nm not in _H_trim
                                and _nm not in ("параллельно", "actions", "commands")):
                            _trim_suspect_names.append(_nm)
                    if _trim_suspect_names:
                        _trim_ok = False
                except Exception:
                    _trim_ok = True
                if _trim_ok:
                    _LAST_PARSE_WARNINGS.append(
                        "⚠️ Убрал лишнюю \"}\" в конце (Extra data). Проверь ввод."
                    )
                    return _cmd_trim, None
            except json.JSONDecodeError:
                pass
        # Автопочин: если скобки не сбалансированы и мы не внутри строки —
        # пробуем дописать недостающие закрывающие в конец.
        _stack, _in_str = _brace_balance(cleaned)
        if _stack and not _in_str:
            _ac_text = cleaned + "".join(reversed(_stack))
            try:
                cmd = safe_json_parse(_ac_text)
                _ac_chars = "".join(reversed(_stack))
                _LAST_PARSE_WARNINGS.append(
                    "⚠️ JSON не закрыт — автопочин добавил: " + _ac_chars
                    + " (возможно, забыл закрыть в конце?)"
                )
                return cmd, None
            except json.JSONDecodeError:
                pass
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
            _looks_like_json = _stripped[:1] in ("{", "[")
            if len(_stripped) > 30 and not _looks_like_json:
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
                         + " Что это: {\"справка\": \"" + _rnd_cmd + "\"}"
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

            _python_hint = ""
            _py_m = re.match(
                r'^\s*(python(?:3(?:\.\d+)?)?|py|pyw|python\.exe|py\.exe)\s+(\S+)',
                part, re.IGNORECASE,
            )
            if _py_m:
                _script = _py_m.group(2)
                _rest = part[_py_m.end():].strip()
                _args_list = []
                if _rest:
                    try:
                        import shlex as _shlex
                        _args_list = _shlex.split(_rest, posix=False)
                    except Exception:
                        _args_list = _rest.split()
                _clean = []
                for _a in _args_list:
                    _a = _a.strip()
                    if (len(_a) >= 2 and _a[0] == _a[-1]
                            and _a[0] in ('"', "'")):
                        _a = _a[1:-1]
                    _clean.append(_a)
                _args_list = _clean
                _example = '{"питон": {"скрипт": "' + _script + '"'
                if _args_list:
                    _quoted = []
                    for _a in _args_list:
                        _quoted.append('"' + _a.replace('\\', '\\\\').replace('"', '\\"') + '"')
                    _example += ', "аргументы": [' + ", ".join(_quoted) + ']'
                _example += '}}'
                _doc_py = ""
                try:
                    _doc_py = _doc_for("питон")
                except Exception:
                    _doc_py = ""
                _FENCE = chr(96) * 3
                _python_hint = (
                    "\n\n💡 Похоже на запуск Python из консоли. "
                    "В anyjson для этого есть команда «питон»:\n"
                    + _FENCE + "text\n" + _example + "\n" + _FENCE
                )
                if _doc_py:
                    _python_hint += "\n\n📖 Справка:\n" + _doc_py
                # это не текст для памяти — питон-команда
                _extra_hint = ""
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
            _trim_hint = ""
            if _trim_suspect_names:
                _trim_hint = (
                    "   Настоящая лишняя \"}\" — НЕ в конце, а РАНЬШЕ.\n"
                    "   В конце — симптом: объект закрылся раньше времени\n"
                    "   (этой самой лишней \"}\"), и параметры после неё\n"
                    "   выпали на верхний уровень (стали отдельными\n"
                    "   ключами верхнего JSON). После их среза появились\n"
                    "   неизвестные команды: "
                    + ", ".join(_trim_suspect_names[:5]) + ".\n"
                    "   Проверь закрывающие \"}\" внутри и сразу после\n"
                    "   тела heredoc. Скорее всего, у тебя лишняя \"}\"\n"
                    "   стоит между маркером конца heredoc и запятой.\n"
                    "   Правильная структура: тело heredoc, потом маркер\n"
                    "   конца, запятая, параметры — и только в самом\n"
                    "   конце две \"}\" (внутренний и внешний объект).\n"
                )
            # --- детект литерала из трёх ">" внутри heredoc-тела ---
            # _expand_inline_heredocs не различает настоящий закрывающий
            # маркер и такой литерал внутри тела: первый же закрывает
            # heredoc, остаток скрипта попадает вне строки. Счётчик
            # ">" * 3 > "<" * 3 — надёжный признак. При срабатывании
            # сообщение про «Открыто без закрытия» подавляем: это
            # симптом, а не причина.
            _heredoc_gt_hint = ""
            try:
                _OPEN3 = chr(60) * 3
                _CLOSE3 = chr(62) * 3
                _open_c = part.count(_OPEN3)
                _close_c = part.count(_CLOSE3)
                if _close_c > _open_c:
                    _heredoc_gt_hint = (
                        "⚠️ Внутри " + _OPEN3 + "..." + _CLOSE3
                        + " встретился литерал " + _CLOSE3
                        + " — он закрыл heredoc раньше времени.\n"
                        "   💡 Замени в теле: chr(62)*3, "
                        "\"\\x3e\\x3e\\x3e\" или \">\"*3.\n"
                    )
            except Exception:
                pass
            _json_err_hint = (
                _heredoc_gt_hint
                + "⚠️ JSON не разобран: " + _err_msg + "\n"
                + ("   Фрагмент: ..." + _err_frag + "..." + _err_hint + "\n"
                   if _err_frag else "")
                + _trim_hint
                + ("   Открыто без закрытия: " + str(len(_stack))
                   + " (" + "".join(reversed(_stack)) + ")\n"
                   if _stack and not _heredoc_gt_hint else "")
                + "\n"
            )
            return None, (
                "❌ Не понял ввод.\n"
                + _json_err_hint
                + "Ожидаю JSON-команду, например:\n"
                "```text\n"
                f"{_ex_str}\n"
                "```\n"
                'Спецификация:\n'
                '```text\n'
                '{"помощь": {"команда": "<имя>"}}\n'
                '```'
             + _python_hint + _extra_hint + _tormoza + _session_hint + _capability_hint + _ending_hint)
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
    "прочитать_полностью", "читать_полностью", "показать_файл",
    "посмотреть_файл", "посмотреть_картинку",
    "рендер", "отрендерить", "svg_в_буфер", "рендер_svg",
    "показать_картинку",
    "рассмотреть_поближе", "увеличить_часть_изображения",
    "кроп", "вырезать_часть", "обрезка", "вырезать",
    "смотреть_видео", "кадр+субтитр", "кадр", "слушать_видео",
    "слушать_аудио", "слушать_звук", "аудио",
    "стабилдиф", "нарисуй", "сгенерировать", "txt2img",
    "перерисуй", "нарисуй_по", "img2img",
    "стилизация", "обработка_картинки",
    "дорисуй", "закрась", "inpaint", "дорисовать",
    "убрать_фон", "удалить_фон", "вырезать_фон", "remove_bg",
    "расширить_картинку", "расширить_изображение", "расширить_поля",
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
    # read-into-context: единственный эффект — отдать файл агенту.
    # в_контекст:false делает команду бессмысленной (clipboard-эффект
    # только маскирует ошибку вызова).
    "прочитать_полностью", "читать_полностью", "показать_файл", "рендер",
    "отрендерить", "открыть",
}

# Команды, у которых в_контекст:false допустим ТОЛЬКО с "сохранить".
# Тогда результат уходит на диск, буфер не трогаем.
NO_CTX_FALSE_NEEDS_SAVE = {
    "слушать_видео", "кадр+субтитр", "кадр",
}


def _params_are_safe(params, name=None):
    """True если результат не попадёт в контекст (буфер)."""
    if name in NO_CTX_FALSE:
        return False
    if not isinstance(params, dict):
        return False
    if name in NO_CTX_FALSE_NEEDS_SAVE:
        if not params.get("сохранить"):
            return False
    for _k in ("в_контекст", "в_буфер"):
        if _k in params:
            _v = params[_k]
            if isinstance(_v, str):
                return _v.strip().lower() in ("0", "false", "нет", "off", "no", "")
            return _v is False
    if name in NO_CTX_FALSE_NEEDS_SAVE:
        return True
    return False


# read_full-команды: не борются за контекст, а складывают файлы в один
# буфер (CF_HDROP с несколькими файлами). В параллельно разрешены без
# "в_контекст": false — system_parallel сольёт их в один вызов.
_READ_FULL_SOLO = {
    "прочитать_полностью", "читать_полностью", "открыть", "показать_файл",
    "посмотреть_файл", "посмотреть_картинку", "показать_картинку",
    "рендер", "отрендерить", "svg_в_буфер", "рендер_svg",
}


def _is_all_read_full_batch(solo_positions):
    """True, если ВСЕ SOLO внутри параллельно — read_full с непустым путём."""
    _par = [(n, p) for pos, n, inside, p in solo_positions if inside]
    if not _par:
        return False
    for n, p in _par:
        if n not in _READ_FULL_SOLO:
            return False
        if not isinstance(p, dict):
            return False
        pv = p.get("путь") or p.get("файл") or ""
        if isinstance(pv, list):
            if not any(str(x).strip() for x in pv):
                return False
        elif not (isinstance(pv, str) and pv.strip()):
            return False
    return True


def _parallel_has_non_solo(parsed):
    """True если внутри параллельно есть не-SOLO команды.

    Нужно, чтобы отличать «чистый read_full-батч» (только read_full
    в параллели) от смешанного (read_full + обычные команды).
    Смешанный батч нельзя выполнять: read_full уйдёт в буфер,
    обычные команды — в контекст, агент получит [BUF] вместо файла.
    """
    for _idx, _part, cmd, err in parsed:
        if err or cmd is None:
            continue
        for name, params, inside in _iter_solo_checks(cmd):
            if inside and name not in SOLO_COMMANDS:
                return True
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
            solo_positions.append((total, tag, inside_parallel, params))
    return total, solo_positions


def process_command_input(input_str: str, pre_notes=None) -> None:
    try:
        _process_command_input_inner(input_str, pre_notes=pre_notes)
    except KeyboardInterrupt:
        try:
            print("\n⛔ Прервано пользователем (Ctrl+C). Возврат в REPL.")
        except Exception:
            pass
        return
    except Exception as _e:
        import traceback as _tb
        _tb.print_exc()
        try:
            print("\u274c \u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u044f\u044f \u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u0430\u0440\u0441\u0435\u0440\u0430: " + str(_e))
        except Exception:
            pass


def _process_command_input_inner(input_str: str, pre_notes=None) -> None:
    """Обрабатывает строку ввода — возможно несколько команд через |||.

    Валидация имён всех команд ДО выполнения: если хоть одна неизвестна,
    остальные не выполняются, показывается только она.
    """
    _LAST_PARSE_WARNINGS.clear()

    # --- отказ: ||| на отдельной строке ---
    for _ln in input_str.split("\n"):
        _st = _ln.strip()
        if _st and all(_c in "| \t" for _c in _st):
            _msg = ("❌ Разделитель ||| не может быть на отдельной "
                    "строке (строка из одних | и пробелов).\n"
                    "Он должен стоять вплотную к } предыдущей команды:\n"
                    '{"a": {}}|||{"b": {}}|||{"c": {}}\n'
                    "После ||| можно перейти на новую строку — "
                    "многострочные цепочки разрешены:\n"
                    '{"a": {}}|||\n'
                    '{"b": {}}|||\n'
                    '{"c": {}}')
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
                try:
                    return _new_fn(input_str, pre_notes=pre_notes)
                except TypeError:
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

    # === Нормализация строковых аргументов ===
    # Поддерживаем интуитивный ввод: {"команда": "строка"}
    # Превращаем в {"команда": {"путь": "строка"}} или {"текст": "строка"}
    for _p_idx, _p_part, _p_cmd, _p_err in parsed:
        if _p_err or not isinstance(_p_cmd, dict):
            continue
        for _action in list(_p_cmd.keys()):
            _args = _p_cmd[_action]
            if isinstance(_args, str):
                if _action in _PATH_COMMANDS:
                    _p_cmd[_action] = {"путь": _args}
                # остальные строки не трогаем: _normalize_params решит,
                # какой главный параметр (текст / запрос / команда / ...).

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
    # NO_CTX_FALSE: эти команды пишут только в контекст; в_контекст:false
    # для них = ошибка вызова. Пропускаем только эту |||-часть,
    # остальные команды батча выполняются.
    _skip_idx = set()
    _warnings = []
    for _idx, _part, _cmd2, _err2 in parsed:
        if _err2 or _cmd2 is None:
            continue
        _bad_here = False
        for _n2, _p2, _ins2 in _iter_solo_checks(_cmd2):
            if _n2 not in NO_CTX_FALSE:
                continue
            if not isinstance(_p2, dict):
                continue
            _is_false = False
            for _k2 in ("в_контекст", "в_буфер"):
                if _k2 in _p2:
                    _v2 = _p2[_k2]
                    if isinstance(_v2, str):
                        if _v2.strip().lower() in ("0", "false", "нет", "off", "no", ""):
                            _is_false = True
                    elif _v2 is False:
                        _is_false = True
            if _is_false:
                _bad_here = True
                _warnings.append(
                    f'⛔ "{_n2}": в_контекст:false не поддерживается — '
                    f'эта команда пишет только в контекст. '
                    f'Выполни её без этого параметра или убери пока.'
                )
        if _bad_here:
            _skip_idx.add(_idx)

    if _warnings:
        parsed = [_e for _e in parsed if _e[0] not in _skip_idx]
        if not parsed:
            _wmsg = "\n".join(_warnings)
            print(color_red("\n" + _wmsg))
            try:
                pyperclip.copy(_wmsg)
                print(color_yellow("📋 Скопировано в буфер обмена."))
            except Exception:
                pass
            return
    if _errors:
        msg = "\n".join(_errors)
        print(color_red("\n" + msg))
        try:
            pyperclip.copy(msg)
            print(color_yellow("📋 Скопировано в буфер обмена."))
        except Exception:
            pass
        return

    # === Склейка read_full-батча: несколько «прочитать_полностью» через ||| ===
    # read_full принимает список путей и кладёт их одним CF_HDROP (как
    # Ctrl+C на группе в проводнике). Если ВСЕ команды в parsed — read_full
    # с непустым путём, сливаем их в одну. Иначе три подряд затрут буфер,
    # а SOLO-проверка завалит цепочку как «SOLO нельзя в |||».
    # Только для верхнего уровня ||| — внутри «параллельно» своя логика
    # в system_parallel.
    _read_full_all = len(parsed) >= 2
    _read_full_paths = []
    _read_full_name = None
    if _read_full_all:
        for _idx, _part, _cmd, _err in parsed:
            if _err or not isinstance(_cmd, dict):
                _read_full_all = False
                break
            _keys = [k for k in _cmd.keys() if isinstance(k, str)]
            if len(_keys) != 1 or _keys[0] not in _READ_FULL_SOLO:
                _read_full_all = False
                break
            if _read_full_name is None:
                _read_full_name = _keys[0]
            _p = _cmd[_keys[0]]
            if not isinstance(_p, dict):
                _read_full_all = False
                break
            _pv = _p.get("путь") or _p.get("файл") or ""
            if isinstance(_pv, list):
                _flat = [str(x).strip() for x in _pv if str(x).strip()]
                if not _flat:
                    _read_full_all = False
                    break
                _read_full_paths.extend(_flat)
            elif isinstance(_pv, str) and _pv.strip():
                _read_full_paths.append(_pv.strip())
            else:
                _read_full_all = False
                break
    if _read_full_all and _read_full_paths and _read_full_name:
        parsed = [(0, "", {_read_full_name: {"путь": _read_full_paths}}, None)]

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
    bad_pipe = [name for pos, name, inside, _p in solo_positions
                if (not inside) and total_actions > 1]
    # SOLO внутри параллельно: порядок не гарантирован, нужен в_контекст:false.
    bad_par = [name for pos, name, inside, _p in solo_positions if inside]
    # Исключение: если ВСЕ SOLO внутри параллельно — read_full с непустым
    # путём, они не борются за контекст. system_parallel сольёт их в один
    # Set-Clipboard -Path @(...) — как Ctrl+C на нескольких файлах.
    if (bad_par and _is_all_read_full_batch(solo_positions)
            and not _parallel_has_non_solo(parsed)):
        bad_par = []

    if bad_pipe or bad_par:
        _parts = []
        if bad_pipe:
            _need_save = [n for n in bad_pipe if n in NO_CTX_FALSE_NEEDS_SAVE]
            _hard = [n for n in bad_pipe if n in NO_CTX_FALSE]
            if _need_save and not _hard:
                _parts.append("⛔ SOLO в ||| только с \"сохранить\": "
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
                _parts.append(", ".join(_dedup_labels(bad_pipe))
                              + " нельзя смешивать с другими командами\n"
                              + "   Результат идёт только в контекст — "
                              + "если их несколько, они затрут друг друга.\n"
                              + "   Выполни отдельным запросом.")
            elif not _hard_only:
                _parts.append(", ".join(_dedup_labels(bad_pipe))
                              + " нельзя смешивать с другими командами\n"
                              + "   Они переписывают контекст — если их "
                              + "несколько, они затрут друг друга.\n"
                              + "   Выполни отдельным запросом или добавь "
                              + "\"в_контекст\": false "
                              + "(тогда SOLO перестаёт переписывать контекст).")
        if bad_par:
            _no_false = all(_n in NO_CTX_FALSE for _n in bad_par)
            if _no_false:
                _parts.append("⛔ Нельзя смешивать в одном параллельно обычные "
                              + "команды и read_full: "
                              + ", ".join(_dedup_labels(bad_par))
                              + ".\n"
                              + "   read_full пишет только в буфер, обычные — "
                              + "в контекст; агент получит [BUF] вместо файла.\n"
                              + "   Раздели на два вызова:\n"
                              + "   1) параллельно без read_full — обычные команды;\n"
                              + "   2) отдельный параллельно ТОЛЬКО с read_full — "
                              + "это разрешено.\n"
                              + "   Либо одиночный вызов read_full.")
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
    # Прогресс-метка оператору: X/Y · имя_команды перед каждой командой.
    # Только для пакета (Y>1), только в консоль — в results/clipboard
    # не попадает. Y = число parsed-записей после склеек (read_full-merge,
    # web_search-merge), то есть столько, сколько ввёл оператор через |||.
    results = []
    _total_cmds = len(parsed)
    for _i, (_idx, _part, cmd, err) in enumerate(parsed, 1):
        if _total_cmds > 1:
            if err:
                _tag = "[ошибка разбора]"
            else:
                _names = _extract_actions(cmd) if cmd else []
                _tag = _names[0] if _names else "?"
                if len(_names) > 1:
                    _tag += " +" + str(len(_names) - 1)
            print(color_blue(f"{_i}/{_total_cmds} · {_tag}"))
        if err:
            results.append(err)
            continue
        if total_actions > 1:
            cmd = _inject_no_clip(cmd)
        if isinstance(cmd, dict):
            actions_list = cmd.get("actions") or cmd.get("commands")
            if actions_list is not None and isinstance(actions_list, list):
                for sub_cmd in actions_list:
                    results.extend(_process_single_command(sub_cmd))
                continue
        results.extend(_process_single_command(cmd))

    results = [r for r in results if r and r.strip()]

    _pre_notes = list(pre_notes) if pre_notes else []
    if _LAST_PARSE_WARNINGS:
        _pre_notes.extend(_LAST_PARSE_WARNINGS)
        _LAST_PARSE_WARNINGS.clear()

    if _warnings:
        results = list(_warnings) + results

    has_buf = any(r.lstrip().startswith("[BUF]") for r in results)
    results = [r.replace("[BUF] ", "", 1) if r.lstrip().startswith("[BUF]") else r
               for r in results]

    if results:
        print(color_red("\nРезультаты:"))
        for _n in _pre_notes:
            print(color_yellow("   " + _n))
        if len(results) > 1:
            for i, res in enumerate(results, 1):
                print(f"{i}. {res}")
        else:
            print(results[0])
        if has_buf:
            # в буфере уже что-то (картинка/файл) — текст не копируем,
            # чтобы не затереть
            return
        _notes_text = ""
        if _pre_notes:
            _notes_text = "".join("   " + n + "\n" for n in _pre_notes) + "\n"
        if len(results) > 1:
            plain_results = _notes_text + "\n".join([f"{i}. {res}" for i, res in enumerate(results, 1)])
        else:
            plain_results = _notes_text + "\n".join(results)
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
                    _hint_parts = []
                    # 5% — пакетный режим
                    if _rnd_hint.random() < 0.05:
                        _hint_parts.append(
                            "\n💡 Пакетный режим:\n"
                            "   • последовательно: через ||| (вплотную к } предыдущей\n"
                            "     команды; после ||| можно перейти на новую строку):\n"
                            "```text\n"
                            '{"а": {}}|||{"б": {}}\n'
                            "```\n"
                            "   • параллельно:\n"
                            "```text\n"
                            '{"параллельно": [{"а": {}}, {"б": {}}]}\n'
                            "```"
                        )
                    # 5% — случайная команда
                    if _rnd_hint.random() < 0.05:
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
                        if _rnd_cmd:
                            _hint_parts.append(
                                f"\n   Ты пробовал(а) команду «{_rnd_cmd}»?"
                                f" Что это: {{\"справка\": \"{_rnd_cmd}\"}}"
                            )
                    if _hint_parts:
                        _hint = "".join(_hint_parts)
                        print(color_yellow(_hint))
                        try:
                            pyperclip.copy(plain_results + "\n" + _hint)
                        except Exception:
                            pass
        except Exception:
            pass

