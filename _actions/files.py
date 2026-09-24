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
      • если текст преимущественно латинский — добавит пометку
        о необходимости перевода.

    Параметры:
      путь      — путь к файлу или папке (обязательный)
      кодировка — принудительная (по умолчанию utf-8, с fallback)
      скрытые   — если true, показывать скрытые файлы (для папок)

    Примеры:
      {"читать": {"путь": "C:/log.txt"}}
      {"читать": {"путь": "E:/Jericho", "скрытые": true}}
      {"читать": {"путь": "E:/data.csv", "кодировка": "cp1251"}}
    """
    from _actions.tools_files import read_file
    path = args.get("путь", "")
    if not path:
        return "Укажите 'путь'."

    if os.path.isdir(path):
        return list_files({"путь": path, "скрытые": args.get("скрытые", False)})

    if not os.path.exists(path):
        _name = os.path.basename(path)
        # корень поиска — родительская папка (на уровень выше), а не та же самая
        _parent = os.path.dirname(os.path.dirname(path)) or os.path.dirname(path) or "."
        # если путь короткий (типа "E:/x.txt") — сразу весь диск
        _drive = "E:/"
        if len(path) > 3 and path[1:3] in (":/", ":\\"):
            _drive = path[:3]
        return (
            f"❌ Файл не найден: {path}\n"
            f"\n💡 Поискать этот файл в других папках:\n"
            f'  {{"найти_файл": {{"корень": "{_parent}", "имя": "{_name}"}}}}\n'
            f'  {{"найти_файл": {{"корень": "{_drive}", "имя": "{_name}"}}}}'
        )

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
]


_SUPPORTED_EXT = set()
for _cat, _exts in _SUPPORTED_CATEGORIES:
    for _e in _exts:
        _SUPPORTED_EXT.add("." + _e.lower())


def _copy_file_to_clipboard(path):
    """Кладёт в буфер ссылку на файл (как Ctrl+C в проводнике).
    Возвращает True/False."""
    try:
        win_path = str(path).replace("/", "\\\\")
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Set-Clipboard -Path '{win_path}'"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except Exception:
        return False


@register(["прочитать_полностью", "читать_полностью", "открыть"])
def read_full(args):
    """Прочитать файл целиком, если его формат поддерживается.

    Параметры:
      путь — путь к файлу (обязательный)

    Примеры:
      {"прочитать_полностью": {"путь": "E:/docs/report.pdf"}}
      {"читать_полностью": {"путь": "D:/logs/app.log"}}
    """
    path = args.get("путь", "")
    if not path:
        return "Укажите 'путь'."
    import os as _os
    if not _os.path.exists(path):
        return f"❌ Файл не найден: {path}"
    if not _os.path.isfile(path):
        return f"❌ Это не файл: {path}"
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


@register("читать_по_кб")
def read_kb(args):
    """Постраничное чтение файла блоками по КБ.

    Параметры:
      путь      — путь к файлу (обязательный)
      кб        — размер блока в КБ (по умолчанию 1)
      скролл    — номер блока (0 = начало)
      кодировка — принудительная (по умолчанию utf-8 с fallback)

    Возвращает заголовок, блок текста и подсказки навигации
    (▶ Дальше / ◀ Назад).

    Примеры:
      {"читать_по_кб": {"путь": "C:/log.txt"}}
      {"читать_по_кб": {"путь": "C:/big.txt", "кб": 5, "скролл": 2}}
    """
    path = args.get("путь", "")
    if not path:
        return "Укажите 'путь'."
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"

    _is_bin, _ = _looks_binary(path)
    if _is_bin:
        try:
            _sz = os.path.getsize(path)
        except OSError:
            _sz = None
        return _binary_redirect_msg(path, _sz)

    try:
        kb = int(args.get("кб", 1) or 1)
    except (TypeError, ValueError):
        kb = 1
    try:
        scroll = int(args.get("скролл", 0) or 0)
    except (TypeError, ValueError):
        scroll = 0
    if kb <= 0:
        kb = 1
    if scroll < 0:
        scroll = 0

    chunk = kb * 1024
    size = os.path.getsize(path)
    total_pages = max(1, (size + chunk - 1) // chunk)
    start = scroll * chunk

    if start >= size:
        return f"📄 {path}\nСкролл {scroll} вне диапазона (всего страниц: {total_pages})."

    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(chunk)
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"

    text = data.decode(args.get("кодировка") or "utf-8", errors="replace")

    if not text.strip():
        return (f"📄 {path} — страница {scroll + 1}/{total_pages}: "
                f"пусто (0 байт на этой позиции)")

    header = f"📄 {path} — страница {scroll + 1}/{total_pages} ({kb} КБ/стр, всего {size} Б)"
    _win = str(path).replace("/", "\\")
    footer = ""
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


@register(["список_файлов", "файлы", "каталог", "список"])
def list_files(args):
    """Листинг папки: имена, размеры, даты изменения.

    Параметры:
      путь    — папка или файл (по умолчанию текущая)
      скрытые — если true, показывать файлы, начинающиеся с "."

    Примеры:
      {"список_файлов": {"путь": "E:/Jericho"}}
      {"файлы": {"путь": "E:/Jericho", "скрытые": true}}
    """
    path = args.get("путь", "") or "."
    if not os.path.exists(path):
        return f"❌ Путь не найден: {path}"
    if not os.path.isdir(path):
        try:
            size = os.path.getsize(path)
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        except OSError:
            size, mtime = "?", "?"
        return (f"📄 {path}\n"
                f"Это файл, не папка.\n"
                f"Размер: {size} Б\n"
                f"Изменён: {mtime}")

    show_hidden = args.get("скрытые", False)
    try:
        entries = sorted(os.listdir(path), key=lambda n: n.lower())
    except PermissionError:
        return f"❌ Нет доступа: {path}"

    if not show_hidden:
        entries = [e for e in entries if not e.startswith(".")]

    dirs, files = [], []
    for name in entries:
        full = os.path.join(path, name)
        try:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(full)))
        except OSError:
            mtime = "?"
        if os.path.isdir(full):
            dirs.append((name, mtime))
        else:
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            files.append((name, size, mtime))

    out = [f"📁 {path}"]
    out.append(f"   папок: {len(dirs)}, файлов: {len(files)}")
    out.append("")
    for name, mtime in dirs:
        out.append(f"  📁 {name}{os.sep}    {mtime}")
    for name, size, mtime in files:
        sz = f"{size:>10} Б" if size is not None else "         ?"
        out.append(f"  📄 {name:<40} {sz}  {mtime}")

    if not dirs and not files:
        out.append("   (пусто)")

    return "\n".join(out)


@register(["переписать", "перезаписать_файл"])
def rewrite(args):
    """Перезаписать файл целиком.

    Запись в системные папки (C:\\Windows, C:\\Program Files и т.п.) запрещена.

    Параметры:
      путь       — путь к файлу (обязательный)
      содержание — новый текст (обязательный), алиас: содержимое

    Пример:
      {"переписать": {"путь": "E:/out.txt", "содержание": "привет"}}

    Для многострочного текста используйте heredoc:
      {"переписать": {"путь": "E:/out.py"}}
      <<<
      ...любой код...
      >>>
    """
    from _actions.tools_files import write_file
    path = args.get("путь", "")
    ok, err = _check_write_path(path)
    if not ok:
        return f"⛔ Запись в системную папку {err} запрещена" if (":\\" in err or ":/" in err) else f"⛔ Запись запрещена: {err}"
    return write_file(path, args.get("содержание") or args.get("содержимое", ""))


@register(["добавить", "записать_файл", "создать_файл"])
def append_(args):
    """Дописать текст в конец файла (создаст, если файла нет).

    Если файла нет — создаст его.
    Родительские папки тоже создаются автоматически.

    Параметры:
      путь       — путь к файлу (обязательный)
      содержание — текст для добавления (обязательный)
                   алиасы: содержимое, текст

    Обычный вызов (одна строка):
      {"добавить": {"путь": "E:/log.txt", "содержание": "новая строка"}}

    Многострочный текст — через heredoc:
      1. сначала строка с JSON-командой
      2. затем строка <<< (три знака «меньше»)
      3. строки текста как есть (без JSON-экранирования)
      4. закрывающая строка >>> (три знака «больше») — конец блока

    Пример heredoc:
      ```text
      {"добавить": {"путь": "E:/log.txt"}}
      <<<
      первая строка
      вторая строка
      "кавычки" и \\ обратные слэши — как есть
      >>>
      ```

    Альтернативное имя:
      {"записать_файл": {"путь": "E:/tmp/x.txt", "содержание": "привет"}}
      {"создать_файл": {"путь": "E:/tmp/new.txt"}}     — создать пустой файл

    Важно: команда ДОПИСЫВАЕТ в конец, не перезаписывает.
    Чтобы заменить содержимое — используйте "переписать".
    """
    from _actions.tools_files import append_to_file
    path = args.get("путь", "")
    ok, err = _check_write_path(path)
    if not ok:
        return f"⛔ Запись в системную папку {err} запрещена" if (":\\" in err or ":/" in err) else f"⛔ Запись запрещена: {err}"
    return append_to_file(path, args.get("содержание") or args.get("содержимое", ""))

