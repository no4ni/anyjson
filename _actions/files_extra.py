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

def _find_common(args, only_files):
    """Общий вызов find_file для «найти_файл» и «найти_всё»."""
    from _actions.tools_dataset import find_file
    if not isinstance(args, dict):
        args = {}
    return find_file(
        root_path=(args.get("корень") or args.get("где")
                   or args.get("путь") or os.getcwd()),
        search_name=args.get("имя", ""),
        search_content=args.get("содержание") or args.get("содержимое", ""),
        search_all=args.get("всё", ""),
        encoding=args.get("кодировка", "utf-8"),
        max_file_size=args.get("макс_размер", 1_000_000),
        timeout=min(int(args.get("таймаут", 30) or 30), 60),
        target_num=int(args.get("номер", 1) or 1),
        only_files=only_files,
        search_mask=args.get("маска") or "",
        ignore_cache=bool(args.get("свежий")),
    )


@register("найти_файл")
def find_file_cmd(args):
    """Поиск ФАЙЛА по имени и/или содержимому.

    Ищет только файлы. Папки пропускает — для них команда «найти_всё».

    Параметры:
      корень      — папка старта (по умолчанию текущая), алиасы: где, путь
      имя         — подстрока в имени файла
      содержание  — подстрока в тексте (поддерживает "a|b" для ИЛИ)
      всё         — искать и по имени, и по содержимому
      маска       — glob по имени (например "*mega*", "*.py")
      кодировка   — для чтения (по умолчанию utf-8)
      макс_размер — максимальный размер файла для поиска (байт)
      таймаут     — секунд (по умолчанию 30, максимум 60)
      свежий      — true: не использовать кэш обхода (искать заново)

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



@register("анализ_папки")
def analyze_folder(args):
    """Анализ занятого места в папке через cleanDrive.py.

    Параметры:
      путь   — анализируемая папка (обязательный)
      быстро — true (по умолчанию) — быстро, false — через TreeSize.ps1
      топ    — сколько крупнейших показать (по умолчанию 50)

    Примеры:
      {"анализ_папки": {"путь": "E:/Downloads"}}
      {"анализ_папки": {"путь": "D:/Games", "быстро": false, "топ": 20}}
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
    script = PUBLIC_TOOLS_DIR / "cleanDrive.py"
    if not script.exists():
        return f"Ошибка: {script} не найден"
    cmd = [sys.executable, str(script), "--path", target_path, "--nodelete", "--nocleanmgr"]
    if fast:
        cmd.append("--fast")
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


@register("осмотреть")
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
    context = args.get("вокруг", 3)
    try:
        timeout = int(args.get("таймаут", 30) or 0)
    except (TypeError, ValueError):
        timeout = 30

    if not file_path:
        return "Ошибка: необходимо указать 'путь'"

    # Бинарные/специальные форматы — перенаправляем на прочитать_полностью.
    # Текстовые (.txt/.log/.py/...) — НЕ перенаправляем: для них смысл
    # осмотра в поиске по содержимому, а read_full кладёт файл в буфер
    # как CF_HDROP и поиска не делает. Раньше сюда уходили все .txt.
    _TEXT_EXTS = {".txt", ".log", ".md", ".py", ".js", ".html",
                  ".css", ".json", ".xml", ".csv", ".ini", ".cfg",
                  ".yaml", ".yml", ".toml", ".rst", ".tex", ".sql",
                  ".sh", ".bat", ".ps1", ".tsv", ".env"}
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
        start_time = time.time()
        all_matches = []
        timed_out = False
        scanned = 0
        last_tick = 0.0
        _cap_limit = (target_count * 10) if target_count > 0 else 10000
        text_exts = {'.txt', '.log', '.py', '.js', '.html', '.css', '.json', '.xml', '.csv', '.md'}
        try:
            for root, dirs, files in os.walk(file_path):
                if timeout and (time.time() - start_time) > timeout:
                    timed_out = True
                    break
                for f in files:
                    if timeout and (time.time() - start_time) > timeout:
                        timed_out = True
                        break
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
            if timed_out:
                return (f"⏱ Превышен таймаут ({timeout} с). "
                        "Совпадений не найдено.")
            return "Не найдено строк с ключевыми словами."
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
    ps_command = f'& "{inspect_script}" -FilePath \'{fp_esc}\' -Keywords {ps_array} -TargetCount {target_count} -Probability {probability} -Encoding \'{encoding}\' -Context {context}'
    result = run_with_wait(execute_command, (ps_command,), timeout)
    # PS 5.1 сериализует Progress/Information в CLIXML и пихает в stderr.
    # Режем здесь — это последняя точка перед выводом.
    import re as _re
    result = _re.sub(r"#<\s*CLIXML.*", "", result, flags=_re.S).rstrip()
    return result[:2048] + ("… (обрезано)" if len(result) > 2048 else "")


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
    end — индекс ПОСЛЕ последней строки функции (0-based, exclusive)."""
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"синтаксическая ошибка в файле: {e}")
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list) - 1
            end = node.end_lineno  # 1-based, inclusive
            return start, end
    return None


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
    header = f"📍 {path} [{name}] — строки {start + 1}..{end}"
    return f"{header}{chr(10)}{chr(10)}{block_text}"


@register(["заменить_функцию", "replace_function"])
def replace_function_cmd(args):
    """Заменить функцию в .py-файле на новое содержимое.

    Находит функцию по имени (через AST), заменяет её целиком.
    Перед заменой создаётся бэкап <файл>.bak.
    После замены файл проверяется парсером — если синтаксис сломан, откат.

    Параметры:
      имя         — имя функции (обязательный)
      путь        — путь к .py-файлу (обязательный)
      содержание  — новый текст функции (обязательный), алиас: содержимое

    Пример:
      {"заменить_функцию": {"имя": "load_all", "путь": "C:/project/example.py",
                            "содержание": "def load_all():\n    ..."}}
    """
    import ast as _ast
    import shutil as _sh
    args = args or {}
    name = (args.get("имя")
            or args.get("функция") or "").strip()
    path = (args.get("путь")
            or args.get("файл") or "").strip()
    new_code = args.get("содержание") or args.get("содержимое") or ""
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

    # определить отступ оригинальной def-строки
    def_line = lines[start]
    indent = ""
    for ch in def_line:
        if ch in " \t":
            indent += ch
        else:
            break

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

    indented = [(indent + ln if ln.strip() else ln) for ln in new_lines]
    new_block = chr(10).join(indented)
    if not new_block.endswith(chr(10)):
        new_block += chr(10)

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
            f"Было строк {end - start}, стало {len(indented)}. Бэкап: {bak}")

