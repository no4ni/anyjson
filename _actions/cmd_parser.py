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
from _color import color_red, color_yellow


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
        from _actions import HANDLERS
    except Exception:
        return []
    import re as _re_hint3
    _ru_name_re3 = _re_hint3.compile(r"^[а-яА-ЯёЁ0-9_]+$")
    avail = []
    for name, fn in HANDLERS.items():
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
        if action in ("запуск", "run"):
            return {"команда": params}
        return {"запрос": params}
    if isinstance(params, dict):
        return params
    return {"значение": params}


def _extract_params_ast(fn):
    """Достаёт args.get("...") ТОЛЬКО из тела функции fn, через AST.

    Отсеивает служебные имена (api_key, token, password, secret).
    """
    try:
        import ast as _ast
        import inspect as _ins
        import textwrap as _tw
        src = _tw.dedent(_ins.getsource(fn))
        tree = _ast.parse(src)
    except Exception:
        return []

    names = set()

    def _walk(node):
        for child in _ast.iter_child_nodes(node):
            if isinstance(child, _ast.Call):
                f = child.func
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
            # не заходим во вложенные функции/лямбды
            if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                  _ast.Lambda)):
                continue
            _walk(child)

    for top in _ast.iter_child_nodes(tree):
        if isinstance(top, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            for stmt in top.body:
                _walk(stmt)
            break

    BAD = ("key", "token", "secret", "password", "passwd", "pwd")
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
        if params and error_text:
            _err = error_text.lower()
            params = [p for p in params if p.lower() not in _err]

        if params:
            body += ("\n\n📌 Недостающие параметры:\n"
                     + "".join(f"  • {p}\n" for p in params))
            body = body.rstrip()

        return body
    except Exception:
        return ""


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
        res.append(_wrap_error(cmd, execute_tool_safe(cmd, {})))
    elif isinstance(cmd, list):
        for item in cmd:
            res.extend(_process_single_command(item))
    elif isinstance(cmd, dict):
        for action, params in cmd.items():
            r = execute_tool_safe(action, _normalize_params(action, params))
            res.append(_wrap_error(action, r))
    else:
        r = execute_tool_safe("запуск", {"команда": str(cmd)})
        res.append(_wrap_error("запуск", r))
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
                _extra_hint = (
                    chr(10) + chr(10) + "💡 Похоже, это текст (не команда). Сохранить в память?" + chr(10) +
                    "```text" + chr(10) +
                    '{"запомнить": {"содержимое": "' + _preview + '"}}' + chr(10) +
                    "```"
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

            return None, (
                "❌ Не понял ввод. Ожидаю JSON-команду, например:\n"
                "```text\n"
                f"{_ex_str}\n"
                "```\n"
                'Спецификация:\n'
                '```text\n'
                '{"помощь": {"команда": "<имя>"}}\n'
                '```'
             + _extra_hint)
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


# Команды, которые кладут результат в буфер обмена (картинка/файл).
# Их нельзя смешивать с другими командами в одном конвейере ||| —
# иначе гонка: BUF-команда затирает то, что положили обычные, или наоборот.
BUF_COMMANDS = {
    "прочитать_полностью", "читать_полностью",
    "рендер", "отрендерить", "svg_в_буфер", "рендер_svg",
    "картинка_в_буфер",
    "увеличить_часть_изображения", "вырезать_часть", "кроп", "обрезка",
    "смотреть_видео", "кадр+субтитр", "слушать_видео",
    "слушать_аудио", "слушать_звук", "аудио",
    "стабилдиф", "нарисуй", "сгенерировать", "txt2img",
    "перерисуй", "нарисуй_по", "img2img",
    "стилизация", "обработка_картинки",
}


def _collect_all_actions(parsed):
    """Собирает все имена команд из parsed (включая вложенные)."""
    total = 0
    buf_names = []
    for _idx, _part, cmd, err in parsed:
        if err or cmd is None:
            continue
        actions = _extract_actions(cmd)
        total += len(actions)
        for a in actions:
            if a in BUF_COMMANDS and a not in buf_names:
                buf_names.append(a)
    return total, buf_names


def process_command_input(input_str: str) -> None:
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

    # --- авто-перезагрузка _actions/*.py при изменениях ---
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

    parts = split_preserving_quotes(input_str)

    # === Фаза 1: разбор (без выполнения) ===
    parsed = []
    for idx, part in enumerate(parts, 1):
        part = part.strip()
        if not part:
            continue
        debug_preview = part[:80].replace('\n', ' ')
        print(f"[DEBUG] Часть {idx}: {debug_preview}{'...' if len(part) > 80 else ''}")
        cmd, err = _parse_part(part)
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

    # === Проверка: операторские команды только по одной ===
    total_actions, buf_names = _collect_all_actions(parsed)
    if buf_names and total_actions > 1:
        msg = ("Всего команд: " + str(total_actions)
               + ", ⛔ " + ", ".join(buf_names)
               + " нельзя смешивать с другими в |||.\n"
               + "   Выполни её отдельной командой.")
        print(color_red("\n" + msg))
        try:
            pyperclip.copy(msg)
            print(color_yellow("📋 Скопировано в буфер обмена."))
        except Exception:
            pass
        return

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
            print(color_yellow("📋 Уже в буфере (текст не копирую)."))
        else:
            plain_results = "\n".join([f"{i}. {res}" for i, res in enumerate(results, 1)])
            try:
                pyperclip.copy(plain_results)
                print(color_yellow("📋 Результаты скопированы в буфер обмена."))
            except Exception as e:
                print(f"⚠️ Не удалось скопировать в буфер: {e}")

