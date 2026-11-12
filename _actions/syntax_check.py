"""Проверка синтаксиса Python-скрипта (без выполнения)."""

import ast
import json
import os

from _actions import register


def _read_source(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _format_syntax_error(e: SyntaxError) -> str:
    line = e.lineno or 0
    col = e.offset or 0
    msg = e.msg or str(e)
    out = f"❌ Синтаксическая ошибка: строка {line}, столбец {col} — {msg}"
    if e.text:
        out += f"\n  {e.text.rstrip()}"
        if col:
            out += "\n  " + " " * max(col - 1, 0) + "^"
    return out


def _format_json_error(e: json.JSONDecodeError, text: str) -> str:
    line = e.lineno or 0
    col = e.colno or 0
    msg = e.msg or str(e)
    out = f"❌ JSON: строка {line}, столбец {col} — {msg}"
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        src = lines[line - 1]
        out += f"\n  {src}"
        if col:
            out += "\n  " + " " * max(col - 1, 0) + "<-!!ТУТ!!->"
    return out


def _check_json(text: str, where: str = "<скрипт>") -> str:
    """Разобрать JSON. Возвращает ✅/❌ в стиле check_syntax."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return _format_json_error(e, text)
    except (ValueError, TypeError) as e:
        return f"❌ Ошибка разбора JSON: {type(e).__name__}: {e}"
    n_lines = len(text.splitlines())
    n_chars = len(text)
    if isinstance(data, dict):
        shape = f"объект, {len(data)} ключ(ей)"
    elif isinstance(data, list):
        shape = f"массив, {len(data)} элемент(ов)"
    else:
        shape = type(data).__name__
    return (f"✅ JSON валиден: {where} — {n_lines} строк, "
            f"{n_chars} символов, {shape}")


@register(["проверить_синтаксис", "проверка_синтаксиса", "синтаксис", "проверить_json"])
def check_syntax(args: dict) -> str:
    """
    Проверить синтаксис Python-скрипта или JSON (без выполнения).

    Параметры:
      • путь — путь к .py- или .json-файлу
      • скрипт — код строкой (если нет пути). Строка, начинающаяся
        с { или [, проверяется как JSON

    Для .py: «✅ Синтаксис OK: <где> — N строк, M узлов» либо
    «❌ Синтаксическая ошибка» с кареткой.
    Для .json / JSON-строки: «✅ JSON валиден» либо «❌ JSON: строка L,
    столбец C — <текст>» с маркером <-!!ТУТ!!-> под позицией.
    Алиас: проверить_json.
    """
    path = args.get("путь") or args.get("файл") or ""
    code = args.get("скрипт") or args.get("код") or ""

    if not path and not code:
        return "❌ не указан ни путь, ни скрипт"

    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            return f"❌ файл не найден: {path}"
        if path.lower().endswith(".json"):
            try:
                text = _read_source(path)
            except OSError as e:
                return f"❌ не прочитать файл: {e}"
            return _check_json(text, where=path)
        if not path.lower().endswith(".py"):
            return f"❌ не .py-файл: {path}"
        try:
            code = _read_source(path)
        except OSError as e:
            return f"❌ не прочитать файл: {e}"
    elif code.lstrip().startswith(("{", "[")):
        return _check_json(code, where="<скрипт>")

    try:
        tree = ast.parse(code, filename=path or "<скрипт>")
    except SyntaxError as e:
        return _format_syntax_error(e)
    except (ValueError, TypeError) as e:
        return f"❌ Ошибка разбора: {type(e).__name__}: {e}"

    n_lines = len(code.splitlines())
    n_top = len(tree.body)
    where = path if path else "<скрипт>"
    return f"✅ Синтаксис OK: {where} — {n_lines} строк, {n_top} верхнеуровневых узлов"