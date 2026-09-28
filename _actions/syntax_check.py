"""Проверка синтаксиса Python-скрипта (без выполнения)."""

import ast
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


@register(["проверить_синтаксис", "проверка_синтаксиса", "синтаксис"])
def check_syntax(args: dict) -> str:
    """
    Проверить синтаксис Python-скрипта (без выполнения).

    Параметры:
      • путь — путь к .py-файлу
      • скрипт — код строкой (если нет пути)

    Возвращает «✅ Синтаксис OK: <где> — N строк, M верхнеуровневых узлов»
    либо «❌ Синтаксическая ошибка: строка L, столбец C — <текст>»
    с указанием проблемной строки и каретки под ней.
    """
    path = args.get("путь") or args.get("файл") or ""
    code = args.get("скрипт") or args.get("код") or ""

    if not path and not code:
        return "❌ не указан ни путь, ни скрипт"

    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            return f"❌ файл не найден: {path}"
        if not path.lower().endswith(".py"):
            return f"❌ не .py-файл: {path}"
        try:
            code = _read_source(path)
        except OSError as e:
            return f"❌ не прочитать файл: {e}"

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