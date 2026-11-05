"""Общие утилиты: нормализация, JSON, обрезка вывода."""
import json
import re
import sys
import unicodedata
from typing import List


def log(msg: str) -> None:
    print(f"[LOG] {msg}", file=sys.stderr)


def is_parallel_json(text: str) -> bool:
    if '"параллельно"' not in text and "'параллельно'" not in text:
        return False
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def fix_multiline_json(raw: str) -> str:
    def replacer(match):
        content = match.group(1)
        content = content.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        return f'"{content}"'
    return re.sub(r'"(.*?)"', replacer, raw, flags=re.DOTALL)


def count_json_objects(text: str) -> int:
    decoder = json.JSONDecoder()
    idx = 0
    count = 0
    text = text.strip()
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            _, end = decoder.raw_decode(text, idx)
            count += 1
            idx = end
        except json.JSONDecodeError:
            break
    return count


def normalize_input(text: str) -> str:
    replacements = {
        ' ': ' ',
        ' ': ' ',
        ' ': ' ',
        ' ': ' ',
        '﻿': '',
    }
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)

    allowed_controls = {0x09, 0x0a, 0x0d, 0x20}
    allowed_categories = {
        'Lu', 'Ll', 'Lt', 'Lm', 'Lo',
        'Mn', 'Mc', 'Me',
        'Nd', 'Nl', 'No',
        'Pc', 'Pd', 'Ps', 'Pe', 'Pi', 'Pf', 'Po',
        'Sm', 'Sc', 'Sk', 'So',
        'Zs',
    }
    result = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ('Cc', 'Cf'):
            if ord(ch) in allowed_controls:
                result.append(ch)
            continue
        if (cat in allowed_categories or
                (0x20 <= ord(ch) <= 0x7E) or
                (0x0400 <= ord(ch) <= 0x04FF)):
            result.append(ch)
    cleaned = ''.join(result)
    if cleaned.startswith('﻿'):
        cleaned = cleaned[1:]
    return cleaned


def split_preserving_quotes(text: str) -> List[str]:
    parts = []
    current = []
    in_double = False
    in_single = False
    in_heredoc = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_heredoc:
            if text.startswith("<<<", i):
                in_heredoc += 1
                current.append("<<<")
                i += 3
                continue
            if text.startswith(chr(62) * 3, i):
                in_heredoc -= 1
                current.append(chr(62) * 3)
                i += 3
                continue
            current.append(ch)
            i += 1
            continue
        if escape:
            current.append(ch)
            escape = False
            i += 1
            continue
        if ch == '\\':
            current.append(ch)
            escape = True
            i += 1
            continue
        if not in_double and not in_single and text.startswith("<<<", i):
            current.append("<<<")
            in_heredoc += 1
            i += 3
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            i += 1
            continue
        if i + 2 < n and text[i] == '|' and text[i+1] == '|' and text[i+2] == '|' and not in_double and not in_single:
            parts.append(''.join(current).strip())
            current = []
            i += 3
            continue
        current.append(ch)
        i += 1
    if current:
        parts.append(''.join(current).strip())
    return [p for p in parts if p]


def strip_trailing_comments(text: str) -> str:
    """Убирает // и # комментарии ПОСЛЕ закрытия JSON-объекта.

    Правила:
      • внутри JSON-строк ничего не трогает (ни //, ни #, ни URL);
      • внутри JSON-скобок ({ } [ ]) ничего не трогает — глубина > 0;
      • режет // и # только когда JSON уже открылся и все скобки закрыты;
      • вне JSON (до первой { или [) ничего не трогает.

    Текст не обязан быть валидным JSON — функция просто идёт
    посимвольно и следит за состоянием.
    """
    out = []
    i = 0
    n = len(text)
    depth = 0
    started = False
    in_string = False
    escape = False
    in_comment = False

    while i < n:
        ch = text[i]

        if in_comment:
            if ch == '\n':
                in_comment = False
                out.append(ch)
            i += 1
            continue

        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch in '{[':
            depth += 1
            started = True
            out.append(ch)
            i += 1
            continue

        if ch in '}]':
            if depth > 0:
                depth -= 1
            out.append(ch)
            i += 1
            continue

        if started and depth == 0:
            if ch == '/' and i + 1 < n and text[i + 1] == '/':
                in_comment = True
                i += 2
                continue
            if ch == '#':
                in_comment = True
                i += 1
                continue

        out.append(ch)
        i += 1

    return ''.join(out)


def safe_json_parse(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        if s.startswith('﻿'):
            s = s[1:]
        def fix_lone_backslashes(match):
            inner = match.group(1)
            inner = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', inner)
            return f'"{inner}"'
        fixed = re.sub(r'"(.*?)"', fix_lone_backslashes, s)
        return json.loads(fixed)


def truncate_output(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return '\n'.join(text.splitlines())


def compress_output(text):
    if not isinstance(text, str):
        return text
    lines = [line.rstrip() for line in text.splitlines()]
    compressed = []
    prev_empty = False
    for line in lines:
        if line == '':
            if not prev_empty:
                compressed.append('')
                prev_empty = True
        else:
            compressed.append(line)
            prev_empty = False
    text = '\n'.join(compressed)
    return re.sub(r' {2,}', ' ', text).strip()


def fix_windows_paths_in_json(text: str) -> str:
    pattern = r'([a-zA-Z]):\\\\([^"\\\\]*(?:\\\\[^"\\\\]*)*)"'
    def replacer(match):
        drive = match.group(1)
        path_part = match.group(2).replace('\\\\', '\\\\\\\\')
        return f'{drive}:\\\\\\\\{path_part}"'
    return re.sub(pattern, replacer, text)


def fix_powershell_body(cmd: str) -> str:
    pattern = r"(-Body\s*:?\s*')(.*?)('(?:\s+|$))"
    def repl(match):
        prefix, inner, suffix = match.group(1), match.group(2), match.group(3)
        return prefix + inner.replace("'", "''") + suffix
    return re.sub(pattern, repl, cmd, flags=re.IGNORECASE | re.DOTALL)


def progress_bar(percent, width=30):
    filled = int(width * percent / 100)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {percent:.1f}%"

def wrap_json_hints(text):
    """Обернуть JSON-команды {...} в ```text-блоки.

    Обрабатывает:
      * строки целиком из {...}
      * строки вида "текст: {...}"
      * группы подряд идущих строк с {...}
    Уже завёрнутые в ```...``` куски не трогает.
    """
    if not isinstance(text, str) or '{"' not in text:
        return text
    import re as _re

    def _is_json_line(s):
        st = s.strip()
        return st.startswith('{"') and st.rstrip().endswith("}")

    parts = _re.split(r'(\s*```[^\n]*\n.*?\n\s*```)', text, flags=_re.DOTALL)
    for i in range(0, len(parts), 2):  # только сырые сегменты
        lines = parts[i].split("\n")
        out = []
        buf = []

        def _flush():
            if not buf:
                return
            indents = [len(l) - len(l.lstrip()) for l in buf if l.strip()]
            indent = " " * (min(indents) if indents else 0)
            out.append(f"{indent}```text")
            out.extend(buf)
            out.append(f"{indent}```")
            buf.clear()

        for ln in lines:
            if _is_json_line(ln):
                buf.append(ln)
                continue
            _flush()
            m = _re.match(r'^(.*?:)\s*(\{"[^\n]*\})\s*$', ln)
            if m and not m.group(1).strip().endswith('```'):
                out.append(m.group(1).rstrip())
                ind = " " * (len(ln) - len(ln.lstrip()))
                out.append(f"{ind}```text")
                out.append(f"{ind}{m.group(2)}")
                out.append(f"{ind}```")
            else:
                out.append(ln)
        _flush()
        parts[i] = "\n".join(out)
    return "".join(parts)
