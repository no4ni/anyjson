"""Файловые операции и анализ диска."""
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

from _actions import register
from _config import PUBLIC_TOOLS_DIR, MY_DIR, PROFILES_DIR, AGENT_NAME
from _utils import log
from _ps import run_with_wait

# ============================================================
#  запрет на запись в системные папки
# ============================================================

_FORBIDDEN_WRITE_PREFIXES = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
]


def _check_write_path(path_str):
    """True/'' если писать можно, иначе False/сообщение."""
    if not path_str:
        return False, "пустой путь"
    try:
        p = str(Path(path_str).resolve()).lower().replace("/", "\\")
    except Exception as e:
        return False, f"неверный путь: {e}"
    for prefix in _FORBIDDEN_WRITE_PREFIXES:
        if p.startswith(prefix.lower()):
            return False, prefix
    return True, ""


_CYR_RE = re.compile(r'[а-яА-ЯёЁ]')


_LAT_RE = re.compile(r'[a-zA-Z]')


_HINT_EN = "\n\n⚠️ ЗАДАЧА: ПЕРЕВЕСТИ НА РУССКИЙ И ЗАМЕНИТЬ ФАЙЛ"


_HINT_EXTS = {".txt", ".md"}



def _is_foreign_profile(path_str):
    """True, если путь ведёт в чужой профиль (не текущего агента)."""
    try:
        from _config import PROFILES_DIR, AGENT_NAME
        p = str(Path(path_str).resolve()).lower().replace("/", "\\")
        pd = str(PROFILES_DIR.resolve()).lower().replace("/", "\\")
        if not p.startswith(pd + "\\"):
            return False
        rel = p[len(pd) + 1:]
        agent = rel.split("\\", 1)[0]
        return bool(agent) and agent != str(AGENT_NAME).lower()
    except Exception:
        return False


def _should_hint_english(path):
    """True, если для этого файла имеет смысл подсказка о переводе."""
    ext = os.path.splitext(str(path))[1].lower()
    return ext in _HINT_EXTS


def _is_secret_file(path):
    """True, если файл — приватный ключ (private_key.pem и *private_key.pem)."""
    name = os.path.basename(str(path)).lower()
    return name.endswith("private_key.pem")


def _is_mostly_english(text: str) -> bool:
    """True, если файл реально английский (кириллицы почти нет).

    Смешанные файлы (русские комменты + английские термины/код)
    НЕ считаются английскими, hint не показываем.
    """
    if not text:
        return False
    cyr = len(_CYR_RE.findall(text))
    lat = len(_LAT_RE.findall(text))
    total = cyr + lat
    if total < 50:          # слишком короткий, не гадаем
        return False
    if cyr >= 5:            # есть заметная кириллица — это не английский
        return False
    return (lat / total) > 0.98


# ------------------------------------------------------------
#  бинарные расширения: содержимое не читается как текст
# ------------------------------------------------------------
_BINARY_EXT = {
    # документы (не plain text)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".epub",
    # изображения (svg — текстовый XML, поэтому его тут нет)
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff",
    ".tif", ".ico", ".heic", ".heif", ".raw",
    # архивы
    ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz",
    ".tgz", ".tbz2",
    # исполняемые / скомпилированные
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".pyo",
    ".o", ".a", ".class", ".jar", ".msi", ".com",
    # медиа
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".ogg", ".webm", ".m4a", ".wma", ".wmv", ".flv",
    # базы данных
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb",
    # образы дисков, пакеты
    ".iso", ".img", ".dmg", ".apk", ".deb", ".rpm",
}


def _looks_binary(path, sample_size=8192):
    """Эвристика: похож ли файл на бинарный. Возвращает (is_binary, reason)."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in _BINARY_EXT:
        return True, "расширение " + ext
    try:
        with open(path, "rb") as f:
            head = f.read(sample_size)
    except Exception:
        return False, ""
    if not head:
        return False, ""
    if chr(0).encode("latin-1") in head:
        return True, "нулевые байты в начале"
    printable = 0
    for b in head:
        if b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E or b >= 0x80:
            printable += 1
    if printable / len(head) < 0.85:
        return True, "много непечатных символов"
    return False, ""


def _binary_redirect_msg(path, size=None):
    """Сообщение-редирект для бинарного файла."""
    _win = str(path).replace("/", chr(92))
    head = "📦 " + str(path)
    if size is not None:
        if size > 1024 * 1024:
            head += " — " + format(size / (1024 * 1024), ".2f") + " МБ (" + str(size) + " байт)"
        elif size > 1024:
            head += " — " + format(size / 1024, ".2f") + " КБ (" + str(size) + " байт)"
        else:
            head += " — " + str(size) + " байт"
    ext = os.path.splitext(str(path))[1].lower()
    ext_label = ext if ext else "без расширения"
    j1 = '{"прочитать_полностью": {"путь": "' + _win + '"}}'
    lines = [
        head,
        "   Это бинарный файл (" + ext_label + ") — как текст не читается.",
        "",
        "Открыть в контекст целиком:",
        "```text",
        j1,
        "```",
    ]
    _img_ext = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                ".tiff", ".tif", ".ico", ".heic", ".heif"}
    if ext in _img_ext:
        j2 = '{"анализ_изображения": {"путь": "' + _win + '"}}'
        lines += [
            "",
            "Метаданные изображения (размер, разрешение, EXIF):",
            "```text",
            j2,
            "```",
        ]
    return chr(10).join(lines)


@register(["читать", "прочитать", "прочитай", "прочитать_файл", "читать_файл"])
def read_(args):
    """Прочитать файл или показать содержимое папки.

    Поведение:
      • файл < 1 КБ — вернёт текст целиком;
      • файл ≥ 1 КБ — вернёт размер и подсказку использовать
        "прочитать_полностью" или "читать_по_кб";
      • папка — вернёт листинг (как "список_файлов");
      • если указаны начало/конец — вернёт только эти строки.

    Параметры:
      путь      — путь к файлу или папке (обязательный)
      кодировка — принудительная (по умолчанию utf-8, с fallback)
      скрытые   — если true, показывать скрытые файлы (для папок)
      начало    — с какой строки читать (алиас: с_строки)
      конец     — по какую строку читать включительно (алиас: по_строку)

    Примеры:
      {"читать": {"путь": "C:/log.txt"}}
      {"читать": {"путь": "E:/Jericho", "скрытые": true}}
      {"читать": {"путь": "E:/data.csv", "кодировка": "cp1251"}}
      {"читать": {"путь": "E:/Readme.txt", "начало": 135, "конец": 150}}
    """
    from _actions.tools_files import read_file
    path = args.get("путь") or args.get("файл") or ""
    if not path:
        return "Укажите 'путь'."

    if _is_secret_file(path):
        return "Приватный ключ — чтение запрещено."

    if os.path.isdir(path):
        return list_files({"путь": path, "скрытые": args.get("скрытые", False)})

    if not os.path.exists(path):
        _name = os.path.basename(path)
        _parent = os.path.dirname(os.path.dirname(path)) or os.path.dirname(path) or "."
        _drive = "E:/"
        if len(path) > 3 and path[1:3] in (":/", ":\\"):
            _drive = path[:3]
        return (
            f"❌ Файл не найден: {path}\n"
            f"\n💡 Поискать этот файл в других папках:\n"
            f'  {{"найти_файл": {{"корень": "{_parent}", "имя": "{_name}"}}}}\n'
            f'  {{"найти_файл": {{"корень": "{_drive}", "имя": "{_name}"}}}}'
        )

    # --- НОВОЕ: чтение по строкам ---
    start_line = args.get("начало") or args.get("с_строки")
    end_line = args.get("конец") or args.get("по_строку")

    if start_line is not None or end_line is not None:
        try:
            start = max(1, int(start_line)) if start_line is not None else 1
            end = int(end_line) if end_line is not None else None
            if end is not None and end < start:
                return "❌ Параметр 'конец' не может быть меньше 'начало'."
            
            encoding = args.get("кодировка") or "utf-8"
            lines_out = []
            with open(path, "r", encoding=encoding, errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if i >= start:
                        if end is not None and i > end:
                            break
                        lines_out.append(line)
            if not lines_out:
                return f"⚠️ Файл пуст или диапазон [{start}-{end if end else 'до конца'}] вне границ."
            
            _end_str = str(end) if end else 'конца'
            return f"📄 {path} [строки {start}-{_end_str}]:\n" + "".join(lines_out)
        except ValueError:
            return "❌ Параметры 'начало' и 'конец' должны быть целыми числами."
    # --- КОНЕЦ НОВОГО ---

    size = os.path.getsize(path)
    KB = 1024

    _is_bin, _ = _looks_binary(path)
    if _is_bin:
        return _binary_redirect_msg(path, size)

    if size <= KB:
        try:
            with open(path, "r",
                      encoding=args.get("кодировка") or "utf-8",
                      errors="replace") as f:
                text = f.read()
        except Exception as e:
            return f"❌ Ошибка чтения: {e}"
        if not text:
            return f"📄 Файл пустой: {path} (0 байт)"
        if _should_hint_english(path) and _is_mostly_english(text):
            text += _HINT_EN
        return text

    # файл больше 1 КБ: показываем первые 1 КБ + подсказки
    try:
        with open(path, "rb") as f:
            head = f.read(KB)
        text = head.decode(args.get("кодировка") or "utf-8", errors="replace")
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"

    size_kb = size / KB
    header = (f"📦 {path} — {size_kb:.2f} КБ ({size} байт), "
              f"показаны первые 1 КБ:\n" + "-" * 50 + "\n")
    _win = str(path).replace("/", "\\")
    _has_ext = bool(os.path.splitext(str(path))[1])
    _footer_parts = [
        "\n\n▶ Дальше: ```text",
        f'{{"читать_по_кб": {{"путь": "{_win}", "кб": 1, "скролл": 1}}}}',
        "```",
    ]
    if _has_ext:
        _footer_parts += [
            "   Полностью: ```text",
            f'{{"прочитать_полностью": {{"путь": "{_win}"}}}}',
            "```",
        ]
    footer = "\n".join(_footer_parts)
    if _should_hint_english(path) and _is_mostly_english(text):
        text += _HINT_EN
    return header + text + footer


_SUPPORTED_CATEGORIES = [
    ("📄 Документы", ["PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX",
                      "ODT", "ODS", "ODP", "RTF", "TXT", "MD", "CSV",
                      "JSON", "XML", "YAML", "YML", "TEX", "LOG", "INI",
                      "CFG", "CONF"]),
    ("🖼️ Изображения", ["PNG", "GIF", "JPG", "JPEG", "SVG", "BMP", "WEBP",
                        "TIFF", "TIF", "ICO", "HEIC"]),
    ("💻 Код", ["PY", "JS", "TS", "JSX", "TSX", "CSS", "SCSS", "SASS",
                "LESS", "HTML", "HTM", "C", "CPP", "CC", "H", "HPP",
                "CS", "JAVA", "GO", "RS", "RB", "PHP", "SWIFT", "KT",
                "SH", "BASH", "PS1", "BAT", "CMD", "SQL", "R", "LUA",
                "PL", "VUE", "SVELTE"]),
    ("Проекты и конфиги", ["CSPROJ", "SLN", "XML", "CONFIG", "PROPS", "TARGETS", "NUSPEC"]),
]


_SUPPORTED_EXT = set()
for _cat, _exts in _SUPPORTED_CATEGORIES:
    for _e in _exts:
        _SUPPORTED_EXT.add("." + _e.lower())


def _copy_file_to_clipboard(paths):
    """Кладёт в буфер ссылку на файл(ы) — как Ctrl+C в проводнике.
    Принимает строку или список. Возвращает True/False."""
    if isinstance(paths, str):
        paths = [paths]
    paths = [str(p) for p in (paths or []) if str(p).strip()]
    if not paths:
        return False
    try:
        win_paths = [p.replace("/", "\\\\") for p in paths]
        if len(win_paths) == 1:
            ps_arg = "'" + win_paths[0] + "'"
        else:
            ps_arg = "@(" + ",".join("'" + p + "'" for p in win_paths) + ")"
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Set-Clipboard -Path {ps_arg}"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _read_full_many(paths):
    """Несколько файлов — один Set-Clipboard -Path @(...).
    Все файлы кладутся в буфер одним Ctrl+C (как в проводнике)."""
    import os as _os
    ok, bad = [], []
    for p in paths:
        if not _os.path.exists(p):
            bad.append((p, "не найден"))
            continue
        if not _os.path.isfile(p):
            bad.append((p, "не файл"))
            continue
        ext = _os.path.splitext(p)[1].lower()
        if not ext:
            bad.append((p, "без расширения"))
            continue
        if ext not in _SUPPORTED_EXT:
            bad.append((p, "формат " + ext + " не поддерживается"))
            continue
        ok.append(p)

    if not ok:
        lines = ["❌ Не удалось прочитать ни один файл:"]
        # Группируем по причине, чтобы заголовок не врал.
        _by_reason = {}
        for p, why in bad:
            _by_reason.setdefault(why, []).append(p)
        for why, paths in _by_reason.items():
            lines.append("  " + why + ":")
            for p in paths:
                lines.append("    • " + p)
        if "не найден" in _by_reason:
            try:
                _cwd = os.getcwd()
            except Exception:
                _cwd = "?"
            lines.append("")
            lines.append("💡 Относительные пути резолвятся от: " + _cwd)
            lines.append("   Если файл в другом месте — укажи полный путь.")
        return chr(10).join(lines)

    copied = _copy_file_to_clipboard(ok)
    if not copied:
        return "❌ Не удалось положить в буфер (" + str(len(ok)) + " файлов)."

    msg = "[BUF] 📋 " + str(len(ok)) + " файлов в буфере обмена"
    if bad:
        _tail = ", ".join(p for p, _ in bad[:5])
        if len(bad) > 5:
            _tail += " ... и ещё " + str(len(bad) - 5)
        msg += chr(10) + "   ⚠️ пропущено " + str(len(bad)) + ": " + _tail
    return msg

@register(["прочитать_полностью", "читать_полностью", "открыть", "показать_картинку", "показать_файл"])
def read_full(args):
    """Прочитать файл целиком или адаптивно (в веб-режиме).

    Параметры:
      путь — путь к файлу (обязательный)
      веб  — если true, работает в адаптивном режиме для браузера:
             • Текст < 8 КБ: читает содержимое целиком.
             • Текст >= 8 КБ: автоматически делегирует выполнение в «читать_по_кб» (блок 4 КБ).
             • Изображения: сервер перехватывает запрос, даунскейлит изображение и отдаёт модели в base64.
             • Прочие форматы: автоматически делегирует выполнение в команду «сенсор».

    Примеры:
      {"прочитать_полностью": {"путь": "E:/docs/report.txt", "веб": true}}
      {"читать_полностью": {"путь": "D:/image.png", "веб": true}}
    """
    raw = args.get("путь") or args.get("файл") or ""

    # Веб-режим: адаптивное поведение вместо буфера обмена
    if args.get("веб") or os.environ.get("ANYJSON_WEB") == "1":
        path_str = str(raw) if raw else ""
        if not path_str or not os.path.exists(path_str):
            return f"❌ Файл не найден: {path_str}"
        if not os.path.isfile(path_str):
            from _utils import not_a_file_msg
            return not_a_file_msg(path_str)
        
        full_path = os.path.abspath(path_str)
        ext = os.path.splitext(full_path)[1].lower()
        size = os.path.getsize(full_path)
        
        # Текстовые форматы
        _TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".json", ".xml", ".html", ".css", ".csv", ".log", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".r", ".lua", ".tex", ".rst"}
        # Графические форматы
        _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".tif", ".ico", ".heic"}
        
        if ext in _TEXT_EXTS:
            if size < 8 * 1024:  # < 8КБ — читаем целиком с заголовком # <полный путь>
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        return f"# {full_path}\n{f.read()}"
                except Exception as e:
                    return f"❌ Ошибка чтения: {e}"
            else:  # >= 8КБ — делегируем в читать_по_кб с 4КБ
                return read_kb({"путь": full_path, "кб": 4, "скролл": 0})
        elif ext in _IMAGE_EXTS:
            # Возвращаем спец. маркер, чтобы _server.py сделал base64 + даунскейл
            _win = path_str.replace("/", "\\")
            import json
            return json.dumps({"_server_image_process": _win}, ensure_ascii=False)
        else:
            # Делегируем в сенсор
            try:
                from _actions.tools_files import run_sensor
                return run_sensor(path_str, config=None)
            except Exception as e:
                return f"❌ Ошибка вызова сенсора: {e}"

    if isinstance(raw, list):
        paths = [str(p).strip() for p in raw if str(p).strip()]
        if not paths:
            return "Укажите 'путь'."
        if len(paths) > 1:
            return _read_full_many(paths)
        raw = paths[0]

    path = raw
    if not path:
        return "Укажите 'путь'."
    import os as _os
    if not _os.path.exists(path):
        return f"❌ Файл не найден: {path}"
    if not _os.path.isfile(path):
        from _utils import not_a_file_msg
        return not_a_file_msg(path)
    win_path = str(path).replace("/", "\\")

    ext = os.path.splitext(str(path))[1].lower()
    if not ext:
        status = "⚠️ Файл без расширения — формат определить невозможно."
        supported = False
    elif ext in _SUPPORTED_EXT:
        status = f"✅ Расширение {ext} поддерживается."
        supported = True
    else:
        status = f"❌ Расширение {ext} не поддерживается."
        supported = False

    if supported:
        # файл кладём в буфер (как Ctrl+C в проводнике)
        copied_as_file = _copy_file_to_clipboard(path)
        if not copied_as_file:
            try:
                import pyperclip as _pc
                _pc.copy(win_path)
            except Exception:
                pass
        return "[BUF] 📋 Файл в буфере обмена"

    # не поддерживается: показываем форматы и подсказки
    lines = [status, "", "Поддерживаемые форматы:"]
    for cat, exts in _SUPPORTED_CATEGORIES:
        lines.append(f"  {cat}: {', '.join(exts)}")
    lines.append("")
    lines.append("💡 Другие команды для чтения:")
    lines.append("")
    _win = str(path).replace("/", "\\")
    _win_dir = os.path.dirname(str(path)).replace("/", "\\")
    lines.append("```text")
    lines.append(f'{{"читать_по_кб": {{"путь": "{_win}", "кб": 1, "скролл": 0}}}}  — постранично')
    lines.append(f'{{"читать": {{"путь": "{_win}"}}}}  — начало файла (<1 КБ — целиком)')
    lines.append(f'{{"осмотреть": {{"путь": "{_win}", "ключевые_слова": "..."}}}}  — поиск по содержимому')
    lines.append(f'{{"список_файлов": {{"путь": "{_win_dir}"}}}}  — листинг папки')
    lines.append("```")
    return chr(10).join(lines)


def _read_kb_lines(path, start, end, encoding):
    """Строчный режим читать_по_кб: с / по (1-based номера строк)."""
    try:
        with open(path, encoding=encoding, errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"
    total = len(lines)
    try:
        s = int(start) if start is not None else 1
    except (TypeError, ValueError):
        s = 1
    try:
        e = int(end) if end is not None else total
    except (TypeError, ValueError):
        e = total
    if s < 1:
        s = 1
    if e > total:
        e = total
    if s > e or s > total:
        return (f"❌ Диапазон строк пуст: с={s}, по={e} "
                f"(всего строк: {total})")
    block = lines[s - 1:e]
    width = max(3, len(str(e)))
    numbered = "".join(
        f"{str(s + i).rjust(width)}| {ln}"
        for i, ln in enumerate(block))
    header = (f"📄 {path} — строки {s}..{e} "
              f"(всего {total}, показано {len(block)})")
    _win = str(path).replace("/", "\\")
    footer = ""
    step = e - s + 1
    if e < total:
        ns, ne = e + 1, min(total, e + step)
        footer += (f'\n\n▶ Дальше: ```text\n'
                   f'{{"читать_по_кб": {{"путь": "{_win}", '
                   f'"с": {ns}, "по": {ne}}}}}\n```')
    if s > 1:
        ps, pe = max(1, s - step), s - 1
        footer += (f'\n\n◀ Назад: ```text\n'
                   f'{{"читать_по_кб": {{"путь": "{_win}", '
                   f'"с": {ps}, "по": {pe}}}}}\n```')
    return f"{header}\n{'-' * 50}\n{numbered}{footer}"


@register(["читать_по_кб", "прочитать_по_кб"])
def read_kb(args):
    """Постраничное чтение файла блоками по КБ (или по строкам).

    Параметры:
      путь      — путь к файлу (обязательный)
      кб        — размер блока в КБ (по умолчанию 1)
      скролл    — номер блока (0 = начало), алиас: страница
      смещение  — абсолютное смещение в байтах (переопределяет скролл),
                  алиас: от
      до        — конечный байт, полуинтервал [от, до). При заданном
                  «до» читается ровно этот диапазон; «кб» и «скролл»
                  игнорируются. «до» без «от» — от начала файла.
      с         — первая строка (1-based). Включает строчный режим;
                  приоритет над кб/скролл/от/до
      по        — последняя строка (включительно). Без «по» — до конца
      кодировка — принудительная (по умолчанию utf-8 с fallback)

    Возвращает заголовок, блок текста и подсказки навигации
    (▶ Дальше / ◀ Назад).

    Примеры:
      {"читать_по_кб": {"путь": "C:/log.txt"}}
      {"читать_по_кб": {"путь": "C:/big.txt", "кб": 5, "скролл": 2}}
      {"читать_по_кб": {"путь": "C:/big.txt", "кб": 1, "смещение": 40000}}
      {"читать_по_кб": {"путь": "C:/big.txt", "от": 0, "до": 4000}}  # байты 0..3999
    """
    path = args.get("путь") or args.get("файл") or ""
    if not path:
        return "Укажите 'путь' (можно и 'файл')."
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"

    if _is_secret_file(path):
        return "Приватный ключ — чтение запрещено."

    _is_bin, _ = _looks_binary(path)
    if _is_bin:
        try:
            _sz = os.path.getsize(path)
        except OSError:
            _sz = None
        return _binary_redirect_msg(path, _sz)

    # Строчный режим: с / по (1-based). Приоритет над байтовыми от/до.
    if args.get("с") is not None or args.get("по") is not None:
        return _read_kb_lines(path, args.get("с"), args.get("по"),
                              args.get("кодировка") or "utf-8")

    try:
        kb = int(args.get("кб", 1) or 1)
    except (TypeError, ValueError):
        kb = 1
    # скролл / страница — синонимы
    scroll_raw = args.get("скролл", args.get("страница", 0))
    try:
        scroll = int(scroll_raw or 0)
    except (TypeError, ValueError):
        scroll = 0
    if kb <= 0:
        kb = 1
    if scroll < 0:
        scroll = 0

    # Смещение — абсолютная байтовая позиция, если задано.
    # «от» — алиас «смещение».
    _off_raw = args.get("смещение")
    if _off_raw is None:
        _off_raw = args.get("от")
    use_offset = _off_raw is not None
    offset = None
    if use_offset:
        try:
            offset = int(_off_raw or 0)
        except (TypeError, ValueError):
            use_offset = False
            offset = None
        if use_offset and offset < 0:
            offset = 0

    # «до» — конечный байт [от, до). Включает range-режим.
    _to_raw = args.get("до")
    use_to = _to_raw is not None
    to_pos = None
    if use_to:
        try:
            to_pos = int(_to_raw or 0)
        except (TypeError, ValueError):
            use_to = False
            to_pos = None
        if use_to and to_pos < 0:
            to_pos = 0
    # «до» без «от» — от начала файла.
    if use_to and not use_offset:
        use_offset = True
        offset = 0
    if use_to and to_pos is not None and offset is not None and to_pos <= offset:
        return (f"❌ Диапазон пуст: «до» ({to_pos}) должно быть больше "
                f"«от» ({offset})")

    _orig_chunk = kb * 1024
    chunk = _orig_chunk
    size = os.path.getsize(path)
    total_pages = max(1, (size + _orig_chunk - 1) // _orig_chunk)

    # Range-режим: длина ровно до-от (переопределяет кб).
    if use_to:
        chunk = to_pos - offset

    if use_offset:
        start = offset
        # Для отображения номера страницы
        scroll_display = start // chunk
    else:
        start = scroll * chunk
        scroll_display = scroll

    _clamped = False
    if start >= size:
        # Запрошено дальше конца — показываем последний блок.
        start = max(0, size - chunk)
        scroll_display = start // chunk
        _clamped = True

    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(chunk)
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"

    text = data.decode(args.get("кодировка") or "utf-8", errors="replace")

    if not text.strip():
        return (f"📄 {path} — позиция {start}/{size}: "
                f"пусто (0 байт на этой позиции)")

    # Нумерация страниц — 0-based, как и параметр «скролл»:
    # скролл 0 → страница 0, скролл 10 → страница 10.
    _last_page = max(0, total_pages - 1)
    if use_to:
        header = f"📄 {path} — байты {start}..{start + len(data) - 1} ({len(data)} Б, всего {size} Б)"
    elif use_offset:
        header = f"📄 {path} — смещение {start} Б (стр. ~{scroll_display}/{_last_page}, {kb} КБ, всего {size} Б)"
    else:
        header = f"📄 {path} — страница {scroll_display}/{_last_page} ({kb} КБ/стр, всего {size} Б)"
        if _clamped:
            header += f"  ← запрошена {scroll}, показана последняя"

    _win = str(path).replace("/", "\\")
    footer = ""

    if use_offset:
        next_pos = start + chunk
        prev_pos = start - chunk
        if next_pos < size:
            footer += (f'\n\n▶ Дальше: ```text\n'
                       f'{{"читать_по_кб": {{"путь": "{_win}", '
                       f'"кб": {kb}, "смещение": {next_pos}}}}}\n```')
        if prev_pos >= 0:
            if prev_pos < 0:
                prev_pos = 0
            footer += (f'\n\n◀ Назад: ```text\n'
                       f'{{"читать_по_кб": {{"путь": "{_win}", '
                       f'"кб": {kb}, "смещение": {prev_pos}}}}}\n```')
    else:
        if scroll + 1 < total_pages:
            footer += (f'\n\n▶ Дальше: ```text\n'
                       f'{{"читать_по_кб": {{"путь": "{_win}", '
                       f'"кб": {kb}, "скролл": {scroll + 1}}}}}\n```')
        if scroll > 0:
            footer += (f'\n\n◀ Назад: ```text\n'
                       f'{{"читать_по_кб": {{"путь": "{_win}", '
                       f'"кб": {kb}, "скролл": {scroll - 1}}}}}\n```')

    footer += (f'\n\n📄 Весь файл целиком: ```text\n'
               f'{{"прочитать_полностью": {{"путь": "{_win}"}}}}\n```')

    if _should_hint_english(path) and _is_mostly_english(text):
        text += _HINT_EN
    return f"{header}\n{'-' * 50}\n{text}{footer}"


@register(["список_файлов", "файлы", "каталог", "показать_каталог", "список"])
def list_files(args):
    """Листинг папки: имена, размеры, даты изменения.

    Параметры:
      путь     — папка или файл (по умолчанию текущая)
      скрытые  — если true, показывать файлы, начинающиеся с "."
      страница — номер страницы, 1-based (по умолчанию 1)
      размер   — элементов на странице (по умолчанию 100, максимум 500)
      маска    — glob-фильтр по имени файла/папки (*.py, checkpoint-*, *log*)
      рекурсивно — true: обходить подпапки. Показывает только файлы
                   с путями относительно «путь». Маска применяется
                   к имени и к относительному пути. По умолчанию false.

    Постраничный вывод: если элементов больше «размер», показываются
    только текущая страница и навигация ▶/◀. На огромных папках
    метаданные (размер, mtime) собираются только для видимой страницы.

    Примеры:
      {"список_файлов": {"путь": "E:/Jericho"}}
      {"файлы": {"путь": "E:/Jericho", "скрытые": true, "страница": 2}}
      {"список_файлов": {"путь": "E:/big", "размер": 300}}
    """
    path = args.get("путь", "") or "."
    if not os.path.exists(path):
        return f"❌ Путь не найден: {path}"
    if not os.path.isdir(path):
        try:
            size = os.path.getsize(path)
            mtime = time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(os.path.getmtime(path)))
        except OSError:
            size, mtime = "?", "?"
        return (f"📄 {path}\n"
                f"Это файл, не папка.\n"
                f"Размер: {size} Б\n"
                f"Изменён: {mtime}")

    show_hidden = args.get("скрытые", False)
    mask = (args.get("маска", "") or "").strip()
    recursive = bool(args.get("рекурсивно", False))

    # --- параметры пагинации ---
    try:
        page = int(args.get("страница", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(args.get("размер", 100) or 100)
    except (TypeError, ValueError):
        page_size = 100
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 100
    if page_size > 500:
        page_size = 500

    # --- быстрый первый проход: только имена и типы ---
    dirs_all = []
    files_all = []
    if recursive:
        try:
            for root, dirs, files in os.walk(path):
                if not show_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                # Добавляем папки (кроме корневой, если она уже учтена, но os.walk даёт вложенные)
                rel_root = os.path.relpath(root, path)
                if rel_root != ".":
                    dirs_all.append(rel_root)
                for fn in files:
                    if not show_hidden and fn.startswith("."):
                        continue
                    full = os.path.join(root, fn)
                    files_all.append(os.path.relpath(full, path))
        except PermissionError:
            return f"❌ Нет доступа: {path}"
    else:
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs_all.append(entry.name)
                        else:
                            files_all.append(entry.name)
                    except OSError:
                        continue
        except PermissionError:
            return f"❌ Нет доступа: {path}"

    if mask:
        import fnmatch
        if recursive:
            files_all = [n for n in files_all
                         if fnmatch.fnmatch(n, mask)
                         or fnmatch.fnmatch(os.path.basename(n), mask)]
        else:
            dirs_all = [n for n in dirs_all if fnmatch.fnmatch(n, mask)]
            files_all = [n for n in files_all if fnmatch.fnmatch(n, mask)]

    dirs_all.sort(key=lambda n: n.lower())
    files_all.sort(key=lambda n: n.lower())

    total = len(dirs_all) + len(files_all)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    # --- второй проход: метаданные только для видимой страницы ---
    selected = [("dir", n) for n in dirs_all] + [("file", n) for n in files_all]
    page_items = selected[start_idx:end_idx]

    show_progress = len(page_items) > 30
    items_meta = []
    total_items = len(page_items)
    for i, (kind, name) in enumerate(page_items, 1):
        full = os.path.join(path, name)
        try:
            st = os.stat(full)
            mtime = time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(st.st_mtime))
        except OSError:
            st = None
            mtime = "?"
        if kind == "dir":
            items_meta.append(("dir", name, None, mtime))
        else:
            size = st.st_size if st is not None else None
            items_meta.append(("file", name, size, mtime))
        if show_progress and (i % 25 == 0 or i == total_items):
            sys.stdout.write(f"\r\u23f3 Метаданные {i}/{total_items}...")
            sys.stdout.flush()
    if show_progress:
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

    _abs = os.path.abspath(path)
    _header = f"📁 {_abs}"
    if os.path.normpath(_abs) != os.path.normpath(path):
        _header += f"  (из «{path}»)"
    out = [_header]
    out.append(f"   папок: {len(dirs_all)}, файлов: {len(files_all)}, "
               f"всего: {total}"
               + (" (рекурсивно)" if recursive else ""))
    if total_pages > 1:
        _to = min(end_idx, total)
        out.append(f"   страница {page}/{total_pages}, "
                   f"показано {start_idx + 1}–{_to}")
    out.append("")

    for kind, name, size, mtime in items_meta:
        if kind == "dir":
            out.append(f"  📁 {name}{os.sep}    {mtime}")
        else:
            sz = f"{size:>10} Б" if size is not None else "         ?"
            out.append(f"  📄 {name:<40} {sz}  {mtime}")

    if total == 0:
        out.append("   (пусто)")

    # --- навигация ---
    _win = str(path).replace(chr(92), chr(47))
    _win_json = _win.replace(chr(92), chr(92) * 2)
    nav = []
    if page > 1:
        nav.append('◀ Назад:  {"список_файлов": {"путь": "' + _win_json
                   + '", "страница": ' + str(page - 1) + '}}')
    if page < total_pages:
        nav.append('▶ Дальше: {"список_файлов": {"путь": "' + _win_json
                   + '", "страница": ' + str(page + 1) + '}}')
    if nav:
        out.append("")
        out.extend(nav)

    return chr(10).join(out)

@register(["переписать", "перезаписать_файл", "записать_файл", "переписать_файл"])
def rewrite(args):
    """Перезаписать файл целиком.

    Запись в системные папки (C:\\Windows, C:\\Program Files и т.п.) запрещена.

    Параметры:
      путь                — путь к файлу (обязательный)
      содержание          — новый текст (обязательный), алиасы: содержимое, текст
      подтвердить_потерю  — true: разрешить перезапись, если новый текст меньше старого

    Пример:
      {"переписать": {"путь": "E:/out.txt", "содержание": "привет"}}

    Для многострочного текста используйте inline-heredoc.
    """
    from _actions.tools_files import write_file
    path = args.get("путь", "")
    ok, err = _check_write_path(path)
    if not ok:
        return f"⛔ Запись в системную папку {err} запрещена" if (":\\" in err or ":/" in err) else f"⛔ Запись запрещена: {err}"
    
    _content = args.get("содержание")
    if _content is None:
        _content = args.get("содержимое")
    if _content is None:
        _content = args.get("текст", "")
        
    from pathlib import Path as _Path
    if path and _Path(path).is_file():
        _old_size = _Path(path).stat().st_size
        _new_size = len(str(_content).encode('utf-8'))
        if _new_size < _old_size and not args.get("подтвердить_потерю"):
            return (f"⚠️ Новое содержимое ({_new_size} Б) меньше старого ({_old_size} Б).\n"
                    f"Возможна случайная потеря данных. Если это намеренно, добавь параметр: \"подтвердить_потерю\": true.")

    # Бэкап существующего файла перед перезаписью (как у заменить_текст).
    # Если бэкап не удался — запись отменяем, файл не трогаем.
    try:
        if path and _Path(path).is_file():
            from _actions.tools_backups import create_backup
            _bk = create_backup({"путь": path})
            if not str(_bk).startswith("✅"):
                return f"⛔ Бэкап не создан, запись отменена: {_bk}"
    except Exception as _e:
        return f"⛔ Бэкап не создан, запись отменена: {_e}"
    return write_file(path, _content)


@register(["добавить", "дописать_файл", "записать_в_файл", "создать_файл"])
def append_(args):
    """Дописать текст в конец файла (создаст, если файла нет).

    Если файла нет — создаст его.
    Родительские папки тоже создаются автоматически.

    Параметры:
      путь       — путь к файлу (обязательный)
      содержание — текст для добавления (обязательный)
                   алиасы: содержимое, текст
      склеить    — true: дописать вплотную без авто-перевода строки (по умолч. false)

    Обычный вызов (одна строка):
      {"добавить": {"путь": "E:/log.txt", "содержание": "новая строка"}}

    Многострочный текст — через inline-heredoc:
      значение поля оборачивается в <<< ... >>>

    Пример:
      ```text
      {"добавить": {"путь": "E:/log.txt", "содержание": <<<
      первая строка
      вторая строка
      "кавычки" и \\ обратные слэши — как есть
      >>>}}
      ```

    Альтернативное имя:
      {"записать_в_файл": {"путь": "E:/tmp/x.txt", "содержание": "привет"}}
      {"создать_файл": {"путь": "E:/tmp/new.txt"}}     — создать пустой файл

    Важно: команда ДОПИСЫВАЕТ в конец, не перезаписывает.
    Чтобы заменить содержимое — используйте "переписать".
    """
    from _actions.tools_files import append_to_file
    path = args.get("путь", "")
    ok, err = _check_write_path(path)
    if not ok:
        return f"⛔ Запись в системную папку {err} запрещена" if (":\\" in err or ":/" in err) else f"⛔ Запись запрещена: {err}"
    raw_join = bool(args.get("склеить") or args.get("без_переноса"))
    return append_to_file(path, args.get("содержание") or args.get("содержимое") or args.get("текст", ""), raw_join=raw_join)

