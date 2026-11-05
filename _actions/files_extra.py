"""Файловые операции и анализ диска."""
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

from _actions import register
from _config import PUBLIC_TOOLS_DIR, MY_DIR
from _utils import log
from _ps import run_with_wait

# ============================================================
#  запрет на запись в системные папки
# ============================================================

def _find_common(args, only_files, only_dirs=False):
    """Общий вызов find_file для «найти_файл», «найти_всё» и «найти_папку»."""
    from _actions.tools_dataset import find_file
    if not isinstance(args, dict):
        args = {}
    return find_file(
        root_path=(args.get("корень") or args.get("где")
                   or args.get("путь") or os.getcwd()),
        search_name=args.get("имя", ""),
        search_content=(args.get("содержание") or args.get("содержимое")
                        or args.get("подстрока", "")),
        search_all=args.get("всё", ""),
        encoding=args.get("кодировка", "utf-8"),
        max_file_size=args.get("макс_размер", 1_000_000),
        timeout=min(int(args.get("таймаут", 30) or 30), 60),
        target_num=int(args.get("номер", 1) or 1),
        only_files=only_files,
        only_dirs=only_dirs,
        search_mask=args.get("маска") or "",
        ignore_cache=bool(args.get("свежий")),
        others_limit=args.get("других_путей", 30),
    )


@register(["найти_файл", "поиск_в_файлах"])
def find_file_cmd(args):
    """Поиск ФАЙЛА по имени и/или содержимому.

    Ищет только файлы. Папки пропускает — для них команда «найти_всё».

    Параметры:
      корень      — папка старта (по умолчанию текущая), алиасы: где, путь
      имя         — подстрока в имени файла
      содержание  — подстрока в тексте (поддерживает "a|b" для ИЛИ),
                    алиас: подстрока
      всё         — искать и по имени, и по содержимому
      маска       — glob по имени (например "*mega*", "*.py")
      кодировка   — для чтения (по умолчанию utf-8)
      макс_размер — максимальный размер файла для поиска (байт)
      таймаут     — секунд (по умолчанию 30, максимум 60)
      свежий      — true: не использовать кэш обхода (искать заново)
      других_путей — сколько соседних путей показать в списке (30)

    Примеры:
      {"найти_файл": {"корень": "E:/", "имя": "log"}}
      {"найти_файл": {"корень": "E:/scripts", "содержание": "AGI|LLM"}}
    """
    return _find_common(args, only_files=True)


@register(["найти_всё", "найти", "искать_всё"])
def find_all_cmd(args):
    """Поиск файлов И папок по имени и/или содержимому.

    В отличие от «найти_файл», показывает и папки тоже.
    Параметры — те же.

    Примеры:
      {"найти_всё": {"корень": "E:/Jericho", "имя": "tools"}}
      {"найти": {"корень": "E:/", "маска": "*mega*"}}
    """
    return _find_common(args, only_files=False)



def _run_dupes_ps1(paths, what_if=False):
    """Запуск delete_duplicates_safe.ps1."""
    from _config import PUBLIC_TOOLS_DIR
    script = PUBLIC_TOOLS_DIR / "delete_duplicates_safe.ps1"
    if not script.exists():
        return f"❌ Скрипт не найден: {script}"

    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return "Укажите путь к папке (путь или пути)."

    # Формируем массив для PowerShell: -Path 'a','b'
    ps_paths = ",".join(f"'{p}'" for p in paths)
    ps_cmd = f"& '{script}' -Path {ps_paths}"
    if what_if:
        ps_cmd += " -WhatIf"
    cmd = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command", ps_cmd
    ]

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', env=env, bufsize=1)
        out = []
        for line in proc.stdout:
            print(line, end='')
            sys.stdout.flush()
            out.append(line)
        proc.wait(timeout=600)
        return ''.join(out).strip() or "Скрипт выполнен (вывод пуст)."
    except subprocess.TimeoutExpired:
        proc.kill()
        return "❌ Таймаут (10 мин)."
    except Exception as e:
        return f"❌ Ошибка запуска: {e}"


@register(["найти_дубликаты", "поиск_дубликатов"])
def find_dupes_cmd(args):
    """Найти дубликаты (безопасно, без удаления).

    Параметры:
      путь  — папка или список папок (обязательно)
      пути  — алиас для списка
    """
    if isinstance(args, str):
        args = {"путь": args}
    paths = args.get("путь") or args.get("пути") or args.get("папка") or ""
    return _run_dupes_ps1(paths, what_if=True)


@register(["удалить_дубликаты", "чистка_дубликатов"])
def delete_dupes_cmd(args):
    """Удалить дубликаты (оставляя файлы с кратчайшим путём).

    Параметры:
      путь      — папка или список папок (обязательно)
      безопасно — true: только посмотреть (аналог найти_дубликаты)
    """
    if isinstance(args, str):
        args = {"путь": args}
    paths = args.get("путь") or args.get("пути") or args.get("папка") or ""
    safe = bool(args.get("безопасно", False))
    return _run_dupes_ps1(paths, what_if=safe)


@register(["найти_папку", "поиск_папок", "найти_директорию"])
def find_folder_cmd(args):
    """Поиск ПАПОК по имени. Файлы не показываются.

    Параметры — как у «найти_всё»: корень, имя, маска, кодировка,
    таймаут, свежий, номер. Поиск по содержимому не работает —
    папки текста не имеют.

    Примеры:
      {"найти_папку": {"корень": "E:/Jericho", "имя": "patterns"}}
      {"найти_папку": {"корень": "E:/", "маска": "*backup*"}}
    """
    return _find_common(args, only_files=False, only_dirs=True)


@register("анализ_папки")
def analyze_folder(args):
    """Анализ занятого места в папке через cleanDrive.py.

    Параметры:
      путь    — анализируемая папка (обязательный)
      быстро  — true (по умолчанию) — быстро, false — через TreeSize.ps1
      топ     — сколько крупнейших показать в быстром режиме (по умолчанию 50);
                в режиме TreeSize («быстро: false») не используется
      глубина — глубина углублённого анализа TreeSize, 1..10 (по умолч. 1).
                Работает только при «быстро: false».
      порог   — порог углубления в ГБ, по умолч. 0.5 (TreeSize).
                Работает только при «быстро: false».

    Примеры:
      {"анализ_папки": {"путь": "E:/Downloads"}}
      {"анализ_папки": {"путь": "D:/Games", "быстро": false, "топ": 20}}
      {"анализ_папки": {"путь": "D:/Games", "быстро": false, "глубина": 3, "порог": 1.0}}
    """
    if isinstance(args, str):
        target_path, fast = args, True
    elif isinstance(args, dict):
        target_path = args.get("путь") or args.get("значение") or args.get("запрос") or ""
        fast = args.get("быстро", True)
    else:
        return "Ошибка формата"
    if not target_path:
        return "Ошибка: не указан путь"
    target_path = target_path.strip().strip('"').strip("'")
    if os.path.isfile(target_path):
        _sz = os.path.getsize(target_path)
        return (f"Это файл, а не папка: {target_path} ({_sz} Б)\n"
                f"Анализ папки неприменим. Для файла — читать / читать_по_кб / "
                f"анализ_изображения / анализ_видео / анализ_аудио.")
    if not os.path.isdir(target_path):
        return f"❌ Папка не существует: {target_path}"
    script = PUBLIC_TOOLS_DIR / "cleanDrive.py"
    if not script.exists():
        return f"Ошибка: {script} не найден"
    cmd = [sys.executable, str(script), "--path", target_path, "--nodelete", "--nocleanmgr"]
    if fast:
        cmd.append("--fast")
    else:
        depth = args.get("глубина", 1) if isinstance(args, dict) else 1
        mingb = args.get("порог", 0.5) if isinstance(args, dict) else 0.5
        cmd.extend(["--depth", str(depth), "--mingb", str(mingb)])
    top = args.get("топ", 50) if isinstance(args, dict) else 50
    cmd.extend(["--top", str(top)])
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', env=env, bufsize=1)
        out = []
        for line in proc.stdout:
            print(line, end='')
            sys.stdout.flush()
            if not re.match(r'^\[\d+/\d+\]', line):
                out.append(line)
        proc.wait(timeout=300)
        return ''.join(out) or "Анализ выполнен."
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Ошибка: таймаут (5 мин)"
    except Exception as e:
        return f"Ошибка cleanDrive.py: {e}"


@register(["дерево", "древовидный_список", "структура_папки"])
def tree_view(args):
    """Дерево папки с размерами (углублённый анализ TreeSize.ps1).

    Синоним «анализ_папки» с «быстро: false» по умолчанию.
    Параметры — как у «анализ_папки»: путь, топ, глубина, порог.
    """
    if isinstance(args, str):
        args = {"путь": args}
    if isinstance(args, dict):
        args = {"быстро": False, **args}
    return analyze_folder(args)


@register(["убраться_в", "очистка_диска"])
def cleanup_drive(args):
    """Анализ диска и очистка временных файлов.

    Что чистит по умолчанию:
      • %TEMP% — файлы старше 7 дней;
      • cleanmgr /sagerun:1 — стандартная очистка Windows.
    Запись идёт в корзину или удаляется сразу — восстановление
    штатными средствами Windows.

    Параметры:
      путь      — папка или диск (обязательный), алиасы: буква, диск
      быстро    — быстрый режим (по умолчанию true)
      топ       — сколько крупнейших элементов показать (по умолчанию 15)
      безопасно — true: только анализ, без очистки
      чистить   — false: не чистить (синоним безопасно=true)

    Примеры:
      {"убраться_в": {"путь": "E:/Jericho/Private/profiles"}}
      {"убраться_в": {"буква": "E", "топ": 20}}
      {"убраться_в": {"путь": "D:/Downloads", "быстро": false}}
      {"убраться_в": {"путь": "C:/", "безопасно": true}}
    """
    target = (args.get("путь") or
              args.get("буква") or args.get("диск") or
              args.get("значение") or args.get("запрос") or "")
    if not target:
        return "Ошибка: не указан диск или папка"
    target = target.strip().strip('"').strip("'")
    script = PUBLIC_TOOLS_DIR / "cleanDrive.py"
    if not script.exists():
        return f"Ошибка: {script} не найден"
    use_fast = args.get("быстро", True)
    top = args.get("топ", 15)
    if ':' in target and any(sep in target for sep in ('\\', '/')):
        cmd = [sys.executable, str(script), "--path", target]
    else:
        drive_letter = target.rstrip(':').upper()
        if drive_letter not in ('C', 'D', 'E', 'F', 'G'):
            return f"Ошибка: диск {drive_letter} не поддерживается"
        cmd = [sys.executable, str(script), "--drive", drive_letter]
    if use_fast:
        cmd.append("--fast")
        cmd.extend(["--top", str(top)])
    # По умолчанию — реальная очистка (temp + cleanmgr).
    # Отключить: "безопасно": true или "чистить": false.
    _safe = args.get("безопасно")
    _clean_raw = args.get("чистить")
    if _clean_raw is None:
        _clean = True   # по умолчанию — чистим
    else:
        if isinstance(_clean_raw, str):
            _clean = _clean_raw.strip().lower() not in ("0", "false", "no", "нет", "off", "")
        else:
            _clean = bool(_clean_raw)
    if _clean and not _safe:
        cmd.append("--yes-cleanmgr")
    else:
        cmd.append("--nodelete")
        cmd.append("--nocleanmgr")
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', env=env, bufsize=1)
        out = []
        for line in proc.stdout:
            print(line, end='')
            sys.stdout.flush()
            if not re.match(r'^\[\d+/\d+\]', line):
                out.append(line)
        proc.wait(timeout=600)
        return ''.join(out) or "Скрипт выполнен."
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Ошибка: таймаут (10 мин)"
    except Exception as e:
        return f"Ошибка cleanDrive.py: {e}"


# Кэш результатов «осмотреть» — чтобы пагинация не гоняла PS заново.
_INSPECT_CACHE = {}
_INSPECT_CACHE_TTL = 300
_INSPECT_CACHE_MAX = 5


def _inspect_cache_get(key):
    import time as _t
    e = _INSPECT_CACHE.get(key)
    if not e:
        return None
    if _t.time() - e[0] > _INSPECT_CACHE_TTL:
        del _INSPECT_CACHE[key]
        return None
    return e[1]


def _inspect_cache_set(key, value):
    import time as _t
    _INSPECT_CACHE[key] = (_t.time(), value)
    if len(_INSPECT_CACHE) > _INSPECT_CACHE_MAX:
        oldest = min(_INSPECT_CACHE.items(), key=lambda kv: kv[1][0])
        del _INSPECT_CACHE[oldest[0]]


@register(["осмотреть", "осмотр"])
def inspect(args):
    """Осмотр файла или URL: поиск по ключевым словам.

    Параметры:
      путь           — файл, папка или URL (обязательный)
      ключевые_слова — строка "a,b,c" или список; пусто = любые строки
      количество     — сколько совпадений вернуть (0 = все)
      вероятность    — 0..1, случайная выборка
      кодировка      — utf8 / cp1251 (по умолчанию utf8)
      вокруг         — контекстных строк вокруг совпадения
      таймаут        — секунд (по умолчанию 30)
      маска          — glob-фильтр имён файлов при обходе папки
                       ("*.py", "log*.txt"); для файла/URL игнорируется
      страница       — номер страницы вывода (1-based, по умолчанию 1)
      размер         — символов на страницу (по умолчанию 4000, мин 500)
      обновить       — true: обойти кэш (заново запустить inspect.ps1)
      регулярка      — true: трактовать ключевые_слова как regex
                       (по умолчанию false, обычная подстрока)

    Примеры:
      {"осмотреть": {"путь": "E:/log.txt", "ключевые_слова": "error,warning"}}
      {"осмотреть": {"путь": "https://example.com", "ключевые_слова": "AGI"}}
    """
    from _actions.tools_files import execute_command
    from _actions.tools_web import view_page
    file_path = args.get("путь", "")
    keywords = args.get("ключевые_слова")
    try:
        target_count = int(args.get("количество", 0) or 0)
    except (TypeError, ValueError):
        target_count = 0
    probability = args.get("вероятность", 0.5)
    encoding = args.get("кодировка", "utf8")
    use_regex = bool(args.get("регулярка", False))
    context = args.get("вокруг", 3)
    try:
        timeout = int(args.get("таймаут", 30) or 0)
    except (TypeError, ValueError):
        timeout = 30

    if not file_path:
        return "Ошибка: необходимо указать 'путь'"

    # Pre-flight: если это не URL и файла нет — говорим об этом сразу.
    # Иначе идём в inspect.ps1, он возвращает пусто — и команда молчит.
    if not file_path.startswith(("http://", "https://")) and not os.path.exists(file_path):
        _parent = os.path.dirname(file_path) or "."
        return (
            "❌ Файл не найден: " + file_path + chr(10)
            + "💡 Проверь путь. Листинг папки:" + chr(10)
            + "```text" + chr(10)
            + '{"список_файлов": {"путь": "' + _parent.replace(chr(92), "/") + '"}}' + chr(10)
            + "```"
        )

    # Бинарные/специальные форматы — перенаправляем на прочитать_полностью.
    # Текстовые (.txt/.log/.py/...) — НЕ перенаправляем: для них смысл
    # осмотра в поиске по содержимому, а read_full кладёт файл в буфер
    # как CF_HDROP и поиска не делает. Раньше сюда уходили все .txt.
    _TEXT_EXTS = {".txt", ".log", ".md", ".py", ".js", ".html",
                  ".css", ".json", ".xml", ".csv", ".ini", ".cfg",
                  ".yaml", ".yml", ".toml", ".rst", ".tex", ".sql",
                  ".sh", ".bat", ".ps1", ".tsv", ".env",
                  ".svg", ".vtt", ".srt", ".ass", ".reg", ".m3u",
                  ".cs", ".cpp", ".c", ".h", ".hpp", ".java", ".go",
                  ".rs", ".rb", ".php", ".ts", ".tsx", ".jsx", ".vue",
                  ".svelte", ".csproj", ".sln", ".config", ".props",
                  ".targets", ".nuspec"}
    if not file_path.startswith(("http://", "https://")) and os.path.isfile(file_path):
        _ext = os.path.splitext(file_path)[1].lower()
        if _ext and _ext not in _TEXT_EXTS:
            try:
                from _actions.files import _SUPPORTED_EXT as _SX
            except Exception:
                _SX = set()
            if _ext and _ext in _SX:
                from _actions.files import read_full
                return read_full({"путь": file_path})

    if keywords is None or (isinstance(keywords, (str, list)) and not keywords):
        kw_list = [" "]
    elif isinstance(keywords, str):
        # разделители: запятая и/или вертикальная черта
        import re as _re
        parts = _re.split(r"[,\|]", keywords)
        kw_list = [kw.strip() for kw in parts if kw.strip()] or [" "]
    elif isinstance(keywords, list):
        kw_list = [str(kw) for kw in keywords if str(kw).strip()] or [" "]
    else:
        kw_list = [str(keywords)]

    if file_path.startswith(('http://', 'https://')):
        page_text = view_page(file_path, max_length=30_000_000)
        if page_text.startswith("Ошибка"):
            return f"Не удалось загрузить URL: {page_text}"
        temp_file = MY_DIR / "last_site.txt"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(page_text)
        file_path = str(temp_file)

    if os.path.isdir(file_path) and keywords:
        import fnmatch as _fnmatch
        _mask_raw = args.get("маска")
        _mask = (_mask_raw or "").strip() or None
        start_time = time.time()
        all_matches = []
        timed_out = False
        scanned = 0
        last_tick = 0.0
        _cap_limit = (target_count * 10) if target_count > 0 else 10000
        text_exts = _TEXT_EXTS
        try:
            for root, dirs, files in os.walk(file_path):
                if timeout and (time.time() - start_time) > timeout:
                    timed_out = True
                    break
                for f in files:
                    if timeout and (time.time() - start_time) > timeout:
                        timed_out = True
                        break
                    if _mask is not None and not _fnmatch.fnmatch(f, _mask):
                        continue
                    scanned += 1
                    _fname_low = f.lower()
                    _name_hit = any(kw.lower() in _fname_low for kw in kw_list)
                    if _name_hit:
                        full_path = os.path.join(root, f)
                        all_matches.append((full_path, "[имя файла: " + f + "]"))
                        if len(all_matches) >= _cap_limit:
                            break
                        continue
                    if os.path.splitext(f)[1].lower() not in text_exts:
                        continue
                    now = time.time()
                    if now - last_tick >= 0.5:
                        elapsed = int(now - start_time)
                        sys.stdout.write("\r\u23f3 осмотр... " + str(elapsed)
                                         + "/" + str(timeout)
                                         + " сек, файлов: " + str(scanned)
                                         + ", найдено: " + str(len(all_matches)))
                        sys.stdout.flush()
                        last_tick = now
                    full_path = os.path.join(root, f)
                    try:
                        with open(full_path, 'r', encoding=encoding) as fp:
                            for line in fp:
                                if any(kw in line for kw in kw_list):
                                    all_matches.append((full_path, line.strip()))
                                    if len(all_matches) >= _cap_limit:
                                        break
                        if len(all_matches) >= _cap_limit:
                            break
                    except Exception:
                        continue
                if timed_out or len(all_matches) >= _cap_limit:
                    break
        finally:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()
        if not all_matches:
            _kw_str = ", ".join(kw_list) if isinstance(kw_list, (list, tuple)) else str(kw_list)
            if timed_out:
                return f"⏱ Таймаут ({timeout} с). В {file_path} не нашлось: {_kw_str}"
            return f"В {file_path} не нашлось: {_kw_str}"
        selected = all_matches if (target_count <= 0 or target_count > len(all_matches)) else random.sample(all_matches, target_count)
        body = "\n".join(f"{p}:\n{l[:1024]}{'…' if len(l) > 1024 else ''}" for p, l in selected)
        if timed_out:
            return (f"⏱ Превышен таймаут ({timeout} с) — показано то, что успели "
                    f"собрать ({len(all_matches)}):\n\n" + body)
        return body

    if os.path.isdir(file_path):
        from _actions.tools_files import read_file
        return read_file(file_path)

    kw_esc = [kw.replace("'", "''") for kw in kw_list]
    ps_array = "@('" + "','".join(kw_esc) + "')"
    fp_esc = file_path.replace("'", "''")
    inspect_script = PUBLIC_TOOLS_DIR / "inspect.ps1"
    _regex_flag = " -Regex" if use_regex else ""
    ps_command = f'& "{inspect_script}" -FilePath \'{fp_esc}\' -Keywords {ps_array} -TargetCount {target_count} -Probability {probability} -Encoding \'{encoding}\' -Context {context}{_regex_flag}'
    # Кэш: одинаковые параметры — не гоняем inspect.ps1 повторно.
    _cache_key = (
        str(file_path),
        "|".join(str(x) for x in kw_list),
        "regex" if use_regex else "substr",
        str(encoding),
        str(context),
        str(target_count),
        str(probability),
    )
    _refresh_raw = args.get("обновить", args.get("свежий"))
    if isinstance(_refresh_raw, bool):
        _refresh = _refresh_raw
    elif isinstance(_refresh_raw, (int, float)):
        _refresh = bool(_refresh_raw)
    elif isinstance(_refresh_raw, str):
        _refresh = _refresh_raw.strip().lower() in (
            "1", "true", "yes", "да", "on")
    else:
        _refresh = False

    _cached = None if _refresh else _inspect_cache_get(_cache_key)
    if _cached is not None:
        result = _cached
        _from_cache = True
    else:
        result = run_with_wait(execute_command, (ps_command, timeout, True), timeout)
        # PS 5.1 сериализует Progress/Information в CLIXML и пихает в stderr.
        # Режем здесь — это последняя точка перед выводом.
        import re as _re
        result = _re.sub(r"#<\s*CLIXML.*", "", result, flags=_re.S).rstrip()
        _from_cache = False
        if result.strip():
            _inspect_cache_set(_cache_key, result)
    # Если PS-скрипт не вернул ничего — не молчим, а сообщаем.
    if not result.strip():
        return (
            "⚠️ inspect.ps1 вернул пустой результат." + chr(10)
            + "  путь: " + str(file_path) + chr(10)
            + "  ключевые_слова: " + repr(kw_list) + chr(10)
            + "  кодировка: " + str(encoding) + chr(10)
            + "  таймаут: " + str(timeout) + " с" + chr(10)
            + "💡 Проверь: файл читается? ключевые слова есть в тексте? "
            + "таймаут не мал?"
        )

    # --- пагинация результата ---
    try:
        _page = int(args.get("страница", 1) or 1)
    except (TypeError, ValueError):
        _page = 1
    if _page < 1:
        _page = 1
    try:
        _page_size = int(args.get("размер", 4000) or 4000)
    except (TypeError, ValueError):
        _page_size = 4000
    if _page_size < 500:
        _page_size = 500
    if _page_size > 200000:
        _page_size = 200000

    _total = len(result)
    _total_pages = max(1, (_total + _page_size - 1) // _page_size)
    if _page > _total_pages:
        _page = _total_pages
    _start = (_page - 1) * _page_size
    _end = min(_start + _page_size, _total)
    _body = result[_start:_end]

    _header = ""
    if _total_pages > 1:
        _cache_mark = " (кэш)" if _from_cache else ""
        _header = (
            "📄 Страница " + str(_page) + "/" + str(_total_pages)
            + " (симв. " + str(_start + 1) + ".." + str(_end)
            + " из " + str(_total) + ")" + _cache_mark
            + chr(10) + chr(10)
        )

    _nav = ""
    if _total_pages > 1:
        import json as _json_pg
        _base = {k: v for k, v in args.items()
                 if k not in ("страница", "размер")}
        _nav_lines = []
        if _page < _total_pages:
            _nx = dict(_base)
            _nx["страница"] = _page + 1
            _nx["размер"] = _page_size
            _nav_lines.append(
                "▶ Дальше: " + _json_pg.dumps({"осмотреть": _nx},
                                              ensure_ascii=False))
        if _page > 1:
            _pv = dict(_base)
            _pv["страница"] = _page - 1
            _pv["размер"] = _page_size
            _nav_lines.append(
                "◀ Назад: " + _json_pg.dumps({"осмотреть": _pv},
                                             ensure_ascii=False))
        if _nav_lines:
            _nav = chr(10) + chr(10) + chr(10).join(_nav_lines)

    return _header + _body + _nav


@register("формат")
def format_file(args):
    """Преобразовать бинарный файл в текст (через sensor.py).

    Работает с PDF, DOC, PNG, JPG, exe, bin и другими бинарными форматами — извлекает текст.

    Параметры:
      путь     — файл (обязательный)
      язык     — языковая модель (по умолчанию rus+eng)
      весь     — если true, обрабатывать весь файл (nolimit)
      в_файл   — путь для сохранения результата
      таймаут  — секунд

    Примеры:
      {"формат": {"путь": "E:/scans/page01.png"}}
      {"формат": {"путь": "E:/docs/report.pdf", "в_файл": "E:/out/report.txt"}}
      {"формат": {"путь": "E:/scans/photo.jpg", "язык": "rus+eng"}}
    """
    from _actions.tools_files import run_sensor
    path = args.get("путь") or args.get("файл") or ""
    path = str(path).strip()

    if not path:
        return (
            "❌ Не указан 'путь'.\n\n"
            "Формат превращает любой бинарный файл в текст.\n"
            "Примеры:\n"
            "```text\n"
            '{"формат": {"путь": "E:/docs/report.docx"}}\n'
            '{"формат": {"путь": "E:/docs/report.pdf"}}\n'
            '{"формат": {"путь": "E:/tools/app.exe"}}\n'
            '{"формат": {"путь": "E:/scans/photo.jpg", "язык": "rus+eng"}}\n'
            "```"
        )

    import os as _os
    if not _os.path.exists(path):
        return f"❌ Файл не найден: {path}"
    if _os.path.isdir(path):
        return f"❌ Это папка, а не файл: {path}\n   Укажите конкретный файл."

    return run_sensor(
        path,
        lang=args.get("язык", "rus+eng"),
        config=None,
        nolimit=args.get("весь", False),
        output=args.get("в_файл"),
        timeout=args.get("таймаут"),
    )


@register("анализ_изображения")
def analyze_image_cmd(args):
    """Сведения об изображении: размер, разрешение, каналы, EXIF, DPI.

    Параметры:
      путь — путь к изображению (обязательный)

    Пример:
      {"анализ_изображения": {"путь": "E:/pic.jpg"}}

    Посмотреть саму картинку: {"посмотреть_файл": {"путь": "..."}}
    """
    import os as _os
    import math as _math

    path = (args.get("путь") or args.get("файл") or "").strip()
    if not path:
        return 'Укажи путь: {"анализ_изображения": {"путь": "C:/pic.jpg"}}'
    if not _os.path.isfile(path):
        return "❌ Файл не найден: " + path

    src = Path(path).expanduser()
    try:
        size_bytes = src.stat().st_size
    except Exception:
        size_bytes = 0
    ext = src.suffix.lower().lstrip(".")

    if ext == "svg":
        return _analyze_svg(src, size_bytes)

    try:
        from PIL import Image, ExifTags
    except ImportError as e:
        return "❌ Нет PIL: " + str(e)

    try:
        img = Image.open(str(src))
        w, h = img.size
        mode = img.mode
        fmt = (img.format or ext or "?").lower()
        info = dict(img.info or {})
        frames = getattr(img, "n_frames", 1)
        try:
            exif = img.getexif()
        except Exception:
            exif = None
        img.close()
    except Exception as e:
        return "❌ Не удалось открыть: " + str(e)

    lines = ["📷 " + src.name + "  (" + str(w) + "×" + str(h) + ")"]
    lines.append("   Размер: " + _fmt_size(size_bytes)
                 + " (" + "{:,}".format(size_bytes).replace(",", " ")
                 + " байт)")

    try:
        g = _math.gcd(w, h)
        ar = str(w // g) + ":" + str(h // g)
    except Exception:
        ar = "?"
    orient = ("портрет" if h > w else
              ("альбомная" if w > h else "квадрат"))
    lines.append("   Разрешение: " + str(w) + "×" + str(h)
                 + "  (" + ar + ", " + orient + ")")

    if fmt and ext and fmt != ext and not (ext == "jpg" and fmt == "jpeg"):
        lines.append("   Формат внутри: " + fmt.upper()
                     + "  (расширение: ." + ext + ")")

    ch_map = {"1": 1, "L": 1, "P": 1, "LA": 2, "RGB": 3, "RGBA": 4,
              "CMYK": 4, "YCbCr": 3, "I": 1, "F": 1}
    ch = ch_map.get(mode)
    line_m = "   Режим: " + mode
    if ch:
        line_m += ("  (" + str(ch) + " кан., " + str(ch * 8)
                   + " бит/пикс.)")
    lines.append(line_m)

    has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in info
    lines.append("   Прозрачность: " + ("есть" if has_alpha else "нет"))

    dpi = info.get("dpi")
    if dpi:
        try:
            lines.append("   DPI: " + format(float(dpi[0]), ".0f")
                         + "×" + format(float(dpi[1]), ".0f"))
        except Exception:
            pass

    if frames and frames > 1:
        lines.append("   Кадров (анимация): " + str(frames))

    icc = info.get("icc_profile")
    if icc:
        lines.append("   ICC-профиль: есть ("
                     + _fmt_size(len(icc)) + ")")
    else:
        lines.append("   ICC-профиль: нет")

    if exif:
        wanted = {
            "Make": "Камера",
            "Model": "Модель",
            "DateTime": "Изменён",
            "DateTimeOriginal": "Снято",
            "ExposureTime": "Выдержка",
            "FNumber": "Диафрагма",
            "ISOSpeedRatings": "ISO",
            "PhotographicSensitivity": "ISO",
            "FocalLength": "Фокус",
            "LensModel": "Объектив",
            "Software": "Обработка",
            "Orientation": "Ориентация",
        }
        got = []
        for tag_id, value in exif.items():
            tag = ExifTags.TAGS.get(tag_id, str(tag_id))
            if tag in wanted:
                got.append("     " + wanted[tag] + ": " + str(value))
        if got:
            lines.append("   EXIF:")
            lines.extend(got)

    lines.append("")
    _p = str(src.resolve()).replace(chr(92), "/")
    lines.append('💡 Посмотреть: {"посмотреть_файл": {"путь": "'
                 + _p + '"}}')
    return "\n".join(lines)


def _analyze_svg(src, size_bytes):
    """Сведения об SVG без рендеринга (PIL не читает SVG)."""
    import xml.etree.ElementTree as _ET
    lines = ["📷 " + src.name]
    lines.append("   Размер: " + _fmt_size(size_bytes)
                 + " (" + str(size_bytes) + " байт)")
    try:
        tree = _ET.parse(str(src))
        root = tree.getroot()
        tag = root.tag
        if tag.startswith("{"):
            tag = tag.split("}", 1)[1]
        lines.append("   Корень: " + tag)
        w = root.get("width") or "?"
        h = root.get("height") or "?"
        vb = root.get("viewBox")
        lines.append("   Width/Height: " + str(w) + "×" + str(h))
        if vb:
            lines.append("   viewBox: " + vb)
        _tags = {}
        for el in root.iter():
            t = el.tag
            if t.startswith("{"):
                t = t.split("}", 1)[1]
            _tags[t] = _tags.get(t, 0) + 1
        _top = sorted(_tags.items(), key=lambda x: -x[1])[:10]
        lines.append("   Элементов: " + str(sum(_tags.values())))
        for t, n in _top:
            lines.append("     " + t + ": " + str(n))
    except Exception as e:
        lines.append("   ⚠️ Парсинг XML упал: " + str(e))
    lines.append("")
    _p = str(src.resolve()).replace(chr(92), "/")
    lines.append('💡 Посмотреть: {"посмотреть_файл": {"путь": "'
                 + _p + '"}}')
    return "\n".join(lines)



ARCHIVE_EXTS = {'.7z', '.zip', '.rar', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz2'}


def _find_7z():
    """Ищет 7z.exe в PATH или в стандартных местах установки."""
    import shutil as _sh
    for name in ("7z", "7za", "7z.exe"):
        found = _sh.which(name)
        if found:
            return found
    for path in (r"C:\\Program Files\\7-Zip\\7z.exe",
                 r"C:\\Program Files (x86)\\7-Zip\\7z.exe"):
        if os.path.isfile(path):
            return path
    return None


def _fmt_size(n):
    if n is None:
        return "?"
    if n > 1024 * 1024:
        return f"{n / (1024*1024):.2f} МБ"
    if n > 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n} Б"


def _list_archives_in_dir(dir_path, limit):
    """Список архивов в папке (сортировка по размеру)."""
    archives = []
    for name in os.listdir(dir_path):
        full = os.path.join(dir_path, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in ARCHIVE_EXTS:
            continue
        try:
            size = os.path.getsize(full)
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(full)))
        except OSError:
            size, mtime = 0, "?"
        archives.append((name, size, mtime))

    if not archives:
        return f"📭 В папке {dir_path} нет архивов ({', '.join(sorted(ARCHIVE_EXTS))})"

    archives.sort(key=lambda x: -x[1])
    out = [f"📦 Архивы в {dir_path} — всего {len(archives)}, "
           f"показано {min(limit, len(archives))}:"]
    for i, (name, size, mtime) in enumerate(archives[:limit], 1):
        out.append(f"  {i:3}. {name:<50} {_fmt_size(size):>12}  {mtime}")
    return "\n".join(out)


def _parse_7z_listing(archive_path, output, limit, mask=None):
    """Разбор вывода '7z l -slt'."""
    entries = []
    current = {}
    in_files = False
    for line in output.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("----------"):
            in_files = True
            continue
        if not in_files:
            continue
        if line == "":
            if current.get("Path"):
                entries.append(current)
            current = {}
            continue
        if " = " in line:
            k, v = line.split(" = ", 1)
            current[k.strip()] = v.strip()
    if current.get("Path"):
        entries.append(current)

    total_all = len(entries)
    if mask:
        import fnmatch as _fnm
        pat = mask.lower()
        entries = [e for e in entries
                   if _fnm.fnmatch((e.get("Path") or "").lower(), pat)
                   or pat in (e.get("Path") or "").lower()]
        total_filtered = len(entries)
    else:
        total_filtered = total_all

    out = [f"📦 Архив: {archive_path}"]
    if mask:
        out.append(f"   всего записей: {total_all}, "
                   f"по маске '{mask}': {total_filtered}, "
                   f"показано: {min(limit, total_filtered)}")
    else:
        out.append(f"   всего записей: {total_all}, "
                   f"показано: {min(limit, total_filtered)}")
    out.append("")
    for i, e in enumerate(entries[:limit], 1):
        name = e.get("Path", "?")
        size_raw = e.get("Size", "")
        is_folder = e.get("Folder") == "+" or not size_raw
        if is_folder:
            sz_str = "<папка>"
            icon = "📁"
        else:
            try:
                sz_str = _fmt_size(int(size_raw))
            except (ValueError, TypeError):
                sz_str = "?"
            icon = "📄"
        out.append(f"  {i:3}. {icon} {name:<60} {sz_str:>12}")
    return "\n".join(out)


def _list_archive_contents(archive_path, limit, mask=None):
    """Список файлов внутри архива."""
    ext = os.path.splitext(archive_path)[1].lower()
    if ext not in ARCHIVE_EXTS:
        return (f"❌ Не похоже на архив: {archive_path}\n"
                f"   Ожидается: {', '.join(sorted(ARCHIVE_EXTS))}")

    # 1) через 7z.exe (универсально)
    seven_z = _find_7z()
    if seven_z:
        try:
            proc = subprocess.run(
                [seven_z, "l", "-slt", archive_path],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            if proc.returncode == 0:
                return _parse_7z_listing(archive_path, proc.stdout, limit, mask)
        except Exception:
            pass

    # 2) fallback для .zip через zipfile
    if ext == ".zip":
        try:
            import zipfile
            with zipfile.ZipFile(archive_path, "r") as zf:
                infos = zf.infolist()
                total_all = len(infos)
                if mask:
                    import fnmatch as _fnm
                    pat = mask.lower()
                    infos = [x for x in infos
                             if _fnm.fnmatch(x.filename.lower(), pat)
                             or pat in x.filename.lower()]
                    total_filtered = len(infos)
                else:
                    total_filtered = total_all
                out = [f"📦 Архив: {archive_path}"]
                if mask:
                    out.append(f"   всего записей: {total_all}, "
                               f"по маске '{mask}': {total_filtered}, "
                               f"показано: {min(limit, total_filtered)}")
                else:
                    out.append(f"   всего записей: {total_all}, "
                               f"показано: {min(limit, total_filtered)}")
                out.append("")
                for i, info in enumerate(infos[:limit], 1):
                    name = info.filename
                    is_dir = name.endswith("/")
                    sz = "<папка>" if is_dir else _fmt_size(info.file_size)
                    icon = "📁" if is_dir else "📄"
                    out.append(f"  {i:3}. {icon} {name:<60} {sz:>12}")
                return "\n".join(out)
        except Exception as e:
            return f"❌ Ошибка чтения zip: {e}"

    return (f"❌ Не удалось прочитать архив {archive_path}.\n"
            f"   Установите 7-Zip (7z.exe) — он поддерживает "
            f".7z, .zip, .rar, .tar и др.")


@register(["архив_список", "список_архива", "архивы"])
def archive_list(args):
    """Показать содержимое архива ИЛИ список архивов в папке.

    Поведение:
      • путь к файлу .7z/.zip/.rar — печатает первые N записей внутри;
      • путь к папке — печатает N самых больших архивов в ней.

    Параметры:
      путь       — файл-архив или папка (обязательный)
      количество — сколько показать (по умолчанию 50)
      маска      — фильтр по имени внутри архива, например "*.py",
                   "config*", "src/*" (по умолчанию — без фильтра)

    Примеры:
      {"архив_список": {"путь": "E:/backup/data.7z"}}
      {"архив_список": {"путь": "E:/backup", "количество": 100}}
      {"архив_список": {"путь": "E:/backup/old.rar", "количество": 30}}
    """
    args = args or {}
    path = args.get("путь") or args.get("значение") or ""
    if not path:
        return "Укажите 'путь' — к архиву или к папке с архивами."
    if not os.path.exists(path):
        return f"❌ Путь не найден: {path}"

    try:
        limit = int(args.get("количество", 50) or 50)
    except (TypeError, ValueError):
        limit = 50
    if limit <= 0:
        limit = 50

    mask = args.get("маска") or args.get("имя") or ""

    if os.path.isdir(path):
        return _list_archives_in_dir(path, limit)
    return _list_archive_contents(path, limit, mask if mask else None)


def _find_function_range(src, name):
    """Найти границы функции по имени. Возвращает (start, end) в строках или None.
    start — индекс строки с decorator/def (0-based).
    end — индекс ПОСЛЕ последней строки функции (0-based, exclusive).

    Если имя содержит точку (Класс.функция), ищет только в этом классе.
    Если найдено несколько совпадений без класса — ValueError (просит уточнить).
    """
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"синтаксическая ошибка в файле: {e}")

    has_dot = "." in name
    target_cls = None
    target_func = name
    if has_dot:
        target_cls, target_func = name.split(".", 1)

    matches = []  # (node, parent_class)

    def _walk(node, parent_cls=None):
        for child in _ast.iter_child_nodes(node):
            if isinstance(child, _ast.ClassDef):
                _walk(child, child.name)
            elif isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                if child.name == target_func:
                    if not has_dot or parent_cls == target_cls:
                        matches.append((child, parent_cls))
                # Идём глубже (вложенные функции)
                _walk(child, parent_cls)

    _walk(tree)

    if not matches:
        return None
    if len(matches) > 1:
        opts = []
        for n, c in matches:
            opts.append(f"{c}.{n.name}" if c else n.name)
        raise ValueError(f"Найдено несколько '{name}': {', '.join(opts)}. Уточни имя как 'Класс.функция'.")

    node, _ = matches[0]
    start = node.lineno - 1
    if node.decorator_list:
        start = min(d.lineno for d in node.decorator_list) - 1
    end = node.end_lineno  # 1-based, inclusive
    return start, end


def _find_class_range(src, name):
    """Границы класса по имени (включая декораторы). (start, end) или None."""
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"синтаксическая ошибка в файле: {e}")
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef) and node.name == name:
            start = node.lineno - 1
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list) - 1
            end = node.end_lineno
            return start, end
    return None


@register(["прочитать_класс", "read_class"])
def read_class_cmd(args):
    """Прочитать класс из .py-файла по имени — целиком, со всеми методами.

    Параметры:
      имя  — имя класса (обязательный)
      путь — путь к .py-файлу (опционально; без него — поиск по _actions/*.py)

    Примеры:
      {"прочитать_класс": {"имя": "ContextBuilder", "путь": "E:/project/example.py"}}
      {"прочитать_класс": {"имя": "ContextBuilder"}}
    """
    import pathlib as _pl
    args = args or {}
    name = (args.get("имя") or args.get("класс") or "").strip()
    path = (args.get("путь") or args.get("файл") or "").strip()
    if not name:
        return "Укажите имя класса."
    if not path:
        _here = _pl.Path(__file__).parent
        _candidates = sorted(_here.glob("*.py"))
        _found = []
        for _f in _candidates:
            try:
                _src = _f.read_text(encoding="utf-8")
            except Exception:
                continue
            try:
                if _find_class_range(_src, name) is not None:
                    _found.append(str(_f))
            except Exception:
                continue
        if not _found:
            return "❌ Класс '" + name + "' не найден в _actions/*.py"
        if len(_found) > 1:
            _nl = chr(10)
            return ("🤔 Класс '" + name + "' найден в нескольких файлах:"
                    + _nl + "  • " + (_nl + "  • ").join(_found)
                    + _nl + "Уточни путь.")
        path = _found[0]
    if not os.path.isfile(path):
        return "❌ Файл не найден: " + path
    try:
        src = open(path, "r", encoding="utf-8").read()
    except Exception as e:
        return "❌ Ошибка чтения: " + str(e)
    try:
        rng = _find_class_range(src, name)
    except ValueError as e:
        return "❌ " + str(e)
    if rng is None:
        return "❌ Класс '" + name + "' не найден в " + path
    start, end = rng
    lines = src.splitlines(keepends=True)
    block_text = "".join(lines[start:end])
    _nl = chr(10)
    header = "📄 " + path + " [" + name + "] — строки " + str(start + 1) + ".." + str(end)
    return header + _nl + _nl + block_text


@register(["прочитать_функцию", "read_function"])
def read_function_cmd(args):
    """Прочитать одну функцию из .py-файла по имени.

    Параметры:
      имя  — имя функции (обязательный)
      путь — путь к .py-файлу (обязательный)

    Пример:
      {"прочитать_функцию": {"имя": "load_all", "путь": "C:/project/example.py"}}
    """
    args = args or {}
    name = (args.get("имя")
            or args.get("функция") or "").strip()
    path = (args.get("путь")
            or args.get("файл") or "").strip()
    if not name:
        return "Укажите 'имя' функции."
    # если путь не указан — ищем по всем _actions/*.py
    if not path:
        from _actions import HANDLERS  # noqa: F401
        import pathlib as _pl
        _here = _pl.Path(__file__).parent
        _candidates = sorted(_here.glob("*.py"))
        _found = []
        for _f in _candidates:
            try:
                _src = _f.read_text(encoding="utf-8")
            except Exception:
                continue
            if _find_function_range(_src, name) is not None:
                _found.append(str(_f))
        if not _found:
            return f"❌ Функция '{name}' не найдена в _actions/*.py"
        if len(_found) > 1:
            return ("🤔 Функция '" + name + "' найдена в нескольких файлах:\n  • "
                    + "\n  • ".join(_found)
                    + "\nУточни путь: {\"прочитать_функцию\": {\"функция\": \"" + name + "\", \"путь\": \"...\"}}")
        path = _found[0]
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"
    try:
        src = open(path, "r", encoding="utf-8").read()
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"

    try:
        rng = _find_function_range(src, name)
    except ValueError as e:
        return f"❌ {e}"
    if rng is None:
        return f"❌ Функция '{name}' не найдена в {path}"

    start, end = rng
    lines = src.splitlines(keepends=True)
    block_text = "".join(lines[start:end])
    header = f"📄 {path} [{name}] — строки {start + 1}..{end}"
    return f"{header}{chr(10)}{chr(10)}{block_text}"


@register(["заменить_функцию", "replace_function"])
def replace_function_cmd(args):
    """Заменить функцию в .py-файле на новое содержимое.

    Находит функцию по имени (через AST), заменяет её целиком.
    Если в «содержание» нет декораторов, а у оригинала они были —
    сохраняются (например @register).
    Перед заменой создаётся бэкап <файл>.bak.
    После замены файл проверяется парсером — если синтаксис сломан, откат.

    Параметры:
      имя         — имя функции (обязательный). Можно указать как
                    "Класс.функция" (например, "EncoderBlock.forward"),
                    если в файле несколько методов с одинаковым именем.
                    Если имя без класса, а совпадений несколько — замена
                    НЕ выполняется, возвращается список "Класс.функция".
      путь        — путь к .py-файлу (обязательный)
      содержание  — новый текст функции (обязательный), алиасы: содержимое, код

    Пример:
      {"заменить_функцию": {"имя": "load_all", "путь": "C:/project/example.py",
                            "содержание": "def load_all():\n    ..."}}
      {"заменить_функцию": {"имя": "EncoderBlock.forward", "путь": "C:/model.py",
                            "содержание": "def forward(self, x):\n    ..."}}
    """
    import ast as _ast
    import shutil as _sh
    args = args or {}
    name = (args.get("имя")
            or args.get("функция") or "").strip()
    path = (args.get("путь")
            or args.get("файл") or "").strip()
    new_code = args.get("содержание") or args.get("содержимое") or args.get("код") or ""
    if not name:
        return "Укажите 'имя' функции."
    if not path:
        return "Укажите 'путь' к .py-файлу."
    if not new_code:
        return "Укажите 'содержание' — новый текст функции."
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"

    try:
        src = open(path, "r", encoding="utf-8").read()
    except Exception as e:
        return f"❌ Ошибка чтения: {e}"

    try:
        rng = _find_function_range(src, name)
    except ValueError as e:
        return f"❌ {e}"
    if rng is None:
        return f"❌ Функция '{name}' не найдена в {path}"

    start, end = rng
    lines = src.splitlines(keepends=True)

    # start может указывать на первый декоратор — найти реальную строку def
    def_start = start
    for i in range(start, end):
        s = lines[i].lstrip()
        if s.startswith("def ") or s.startswith("async def "):
            def_start = i
            break

    # отступ оригинальной def-строки (не декоратора)
    def_line = lines[def_start]
    indent = ""
    for ch in def_line:
        if ch in " \t":
            indent += ch
        else:
            break

    old_decorators = "".join(lines[start:def_start])

    # нормализовать new_code: убрать общий отступ, применить indent
    new_lines = new_code.splitlines()
    # найти минимальный отступ непустых строк
    min_ind = None
    for ln in new_lines:
        if not ln.strip():
            continue
        i = 0
        while i < len(ln) and ln[i] in " \t":
            i += 1
        if min_ind is None or i < min_ind:
            min_ind = i
    if min_ind:
        new_lines = [ln[min_ind:] if ln.strip() else ln for ln in new_lines]

    has_new_decorators = any(ln.lstrip().startswith("@") for ln in new_lines)

    indented = [(indent + ln if ln.strip() else ln) for ln in new_lines]
    new_block = chr(10).join(indented)
    if not new_block.endswith(chr(10)):
        new_block += chr(10)

    decor_note = ""
    if old_decorators and not has_new_decorators:
        # в новом содержании нет декораторов — сохранить оригинальные
        new_block = old_decorators + new_block
        dec_lines = [l for l in old_decorators.splitlines() if l.strip()]
        if dec_lines:
            decor_note = (f" Сохранены декораторы ({len(dec_lines)}): "
                          + dec_lines[0].strip())

    # собрать новый файл
    new_src = "".join(lines[:start]) + new_block + "".join(lines[end:])

    # проверка синтаксиса
    try:
        _ast.parse(new_src)
    except SyntaxError as e:
        return f"❌ Новый код не парсится: {e}. Замена отменена."

    # бэкап
    bak = path + ".bak"
    try:
        _sh.copy2(path, bak)
    except Exception as e:
        return f"⚠️ Не удалось создать бэкап: {e}"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_src)
    except Exception as e:
        return f"❌ Ошибка записи: {e}"

    return (f"✅ Функция '{name}' в {path} заменена. "
            f"Синтаксис проверен (ast.parse). "
            f"Было строк {end - start}, стало {len(indented)}."
            f"{decor_note} "
            f"Бэкап: {bak}")

