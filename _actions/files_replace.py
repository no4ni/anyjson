"""Замена текста в файлах: X → Y."""

import re
from pathlib import Path

from _actions import register
from _actions.files import _FORBIDDEN_WRITE_PREFIXES


def _is_forbidden(path: Path):
    try:
        s = str(path.resolve()).lower()
    except Exception:
        s = str(path).lower()
    for prefix in _FORBIDDEN_WRITE_PREFIXES:
        p = str(prefix).lower().rstrip("\\/")
        if s == p or s.startswith(p + "\\") or s.startswith(p + "/"):
            return prefix
    return None


def _read_text(path: Path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be"), "utf-16-be"
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def _write_text(path: Path, text: str, enc: str) -> None:
    if enc == "utf-8-sig":
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    else:
        path.write_text(text, encoding=enc)


def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "нет", "off", ""):
        return False
    if s in ("1", "true", "yes", "да", "on"):
        return True
    return default


@register(["заменить_текст", "замена_текста", "заменить_в_тексте"])
def replace_text(args: dict) -> str:
    """Заменить X на Y в текстовом файле.

    Параметры:
      путь (обяз.)      — путь к файлу
      найти (обяз.)     — что искать (X)
      заменить (обяз.)  — на что заменить (Y)
      все               — заменить все вхождения (по умолчанию true)
      регулярка         — трактовать «найти» как regex (по умолчанию false)
      регистр           — учитывать регистр (по умолчанию true)
      предпросмотр      — не писать файл, только показать, сколько заменится

    Пример:
      {"заменить_текст": {"путь": "C:/temp/a.txt",
                          "найти": "foo",
                          "заменить": "bar"}}
    """
    file_arg = args.get("путь") or args.get("файл") or args.get("path")
    if not file_arg:
        return "❌ Не указан параметр «путь»"

    find_arg = args.get("найти")
    if find_arg is None:
        find_arg = args.get("что") or args.get("старое") or args.get("x")
    if find_arg is None:
        return "❌ Не указан параметр «найти» (X)"

    repl_arg = args.get("заменить")
    if repl_arg is None:
        repl_arg = args.get("на") or args.get("новое") or args.get("y")
    if repl_arg is None:
        return "❌ Не указан параметр «заменить» (Y)"

    find_s = str(find_arg)
    repl_s = str(repl_arg)
    use_regex = _as_bool(args.get("регулярка", args.get("regex")), False)
    replace_all = _as_bool(args.get("все"), True)
    case_sensitive = _as_bool(args.get("регистр"), True)
    preview = _as_bool(args.get("предпросмотр"), False)

    path = Path(file_arg).expanduser()
    if not path.exists():
        return f"❌ Файл не найден: {path}"
    if not path.is_file():
        return f"❌ Это не файл: {path}"

    forbidden = _is_forbidden(path)
    if forbidden:
        return f"❌ Запись в {forbidden} запрещена"

    try:
        text, enc = _read_text(path)
    except Exception as e:
        return f"❌ Не удалось прочитать файл: {e}"

    flags = 0 if case_sensitive else re.IGNORECASE
    cnt = 0
    if use_regex:
        try:
            pattern = re.compile(find_s, flags)
        except re.error as e:
            return f"❌ Ошибка в регулярном выражении: {e}"
        new_text, cnt = pattern.subn(repl_s, text, count=0 if replace_all else 1)
    else:
        if case_sensitive:
            if replace_all:
                cnt = text.count(find_s)
                new_text = text.replace(find_s, repl_s)
            else:
                cnt = 1 if find_s in text else 0
                new_text = text.replace(find_s, repl_s, 1)
        else:
            pattern = re.compile(re.escape(find_s), re.IGNORECASE)
            new_text, cnt = pattern.subn(repl_s, text, count=0 if replace_all else 1)

    if cnt == 0:
        return f"ℹ️ Совпадений не найдено в {path.name}"

    if preview:
        _cap = 2000
        _shown = new_text if len(new_text) <= _cap else new_text[:_cap] + "\n…(обрезано, всего " + str(len(new_text)) + " симв.)"
        return f"👁 [предпросмотр] Заменилось бы {cnt} вхождений в {path.name}:\n\n{_shown}"

    try:
        _write_text(path, new_text, enc)
    except Exception as e:
        return f"❌ Не удалось записать файл: {e}"

    return f"✅ Заменено {cnt} вхождений в {path.name} (кодировка {enc})"

