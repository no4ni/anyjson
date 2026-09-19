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

@register("создать_папку")
def create_dir_(args):
    """Создать папку (со всеми родительскими).

    Параметры:
      путь — путь к папке (обязательный)

    Пример:
      {"создать_папку": {"путь": "E:/Jericho/archive/2026"}}
    """
    from _actions.tools_files import create_dir
    path = args.get("путь", "")
    if not path:
        return "Укажите путь в поле 'путь'"
    return create_dir(path)


@register(["удалить", "удалить_путь"])
def delete_any(args):
    """Удалить файл или папку — сам определяет, что это.

    Параметры:
      путь — путь к файлу или папке (обязательный)

    Примеры:
      {"удалить": {"путь": "D:/BACKUPS/Jericho-40.7z"}}
      {"удалить": {"путь": "E:/tmp/old_folder"}}

    Внутри:
      • файл  -> удалить_файл  (защита от .sys/.dll/.exe)
      • папка -> удалить_папку (защита от C:\\, D:\\, Windows, Program Files)
    """
    if not isinstance(args, dict):
        args = {}
    path = args.get("путь") or args.get("path") or ""
    if not path:
        return "Укажите 'путь' — файл или папку."
    if not os.path.exists(path):
        return f"❌ Не существует: {path}"
    if os.path.isdir(path):
        return delete_folder({"путь": path})
    return delete_file({"путь": path})


@register("удалить_файл")
def delete_file(args):
    """Удалить файл.

    Системные файлы (.sys, .dll, .exe) и папки не удаляются —
    для папок используйте "удалить_папку".

    Параметры:
      путь — путь к файлу (обязательный)

    Пример:
      {"удалить_файл": {"путь": "E:/tmp/x.txt"}}
    """
    from _actions.tools_files import safe_delete_file
    path = args.get("путь", "")
    if not path:
        return "Укажите путь в поле 'путь'"
    return safe_delete_file(path)


@register("переместить")
def move(args):
    """Переместить файл или папку.

    Отказывается перемещать папку внутрь самой себя.
    Если цель — существующая папка, файл кладётся внутрь под тем же именем.

    Параметры:
      откуда — источник (обязательный)
      куда   — назначение (обязательный)

    Пример:
      {"переместить": {"откуда": "E:/a.txt", "куда": "E:/backup/"}}
    """
    from _actions.tools_files import move_item
    src = args.get("откуда", "")
    dst = args.get("куда", "")
    if not src or not dst:
        return "Укажите 'откуда' и 'куда'"
    return move_item(src, dst)


@register("удалить_папку")
def delete_folder(args):
    """Рекурсивно удалить папку со всем содержимым.

    Защита: отказывается удалять C:\\, D:\\, E:\\, C:\\Windows,
    C:\\Program Files и т.п.

    Параметры:
      путь — путь к папке (обязательный)

    Пример:
      {"удалить_папку": {"путь": "E:/Jericho/tmp"}}
    """
    import shutil
    path = args.get("путь", "")
    from _actions.tools_files import _foreign_guard
    _g = _foreign_guard(path)
    if _g:
        return _g
    if not path:
        return "Укажите путь в поле 'путь'"
    dangerous = [r"C:\Windows", r"C:\Program Files",
                 r"C:\Program Files (x86)", "C:\\", "D:\\", "E:\\"]
    if any(path.lower().startswith(d.lower()) for d in dangerous):
        return f"⛔ Удаление системной папки {path} запрещено."
    if not os.path.exists(path):
        return f"❌ Папка не существует: {path}"
    if not os.path.isdir(path):
        return f"❌ Это не папка: {path}"
    try:
        shutil.rmtree(path)
        return f"✅ Папка удалена: {path}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


@register(["снять_только_чтение", "снять_readonly", "сделать_записываемым", "unlock_file"])
def remove_readonly(args):
    """Снять флаг "только чтение" с файла.

    Параметры:
      путь — путь к файлу (обязательный)

    Пример:
      {"снять_только_чтение": {"путь": "D:/iso/win7.iso"}}
    """
    import stat
    args = args or {}
    path = args.get("путь") or args.get("path") or ""
    if not path:
        return "Укажите 'путь'."
    if not os.path.exists(path):
        return f"❌ Файл не найден: {path}"
    if os.path.isdir(path):
        _p = str(path).replace("\\", "/")
        return ("Это папка — используйте другую команду:\n"
                f'  {{"снять_только_чтение_со_всей_папки": {{"путь": "{_p}"}}}}')
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IWRITE)
        return f"✅ Флаг read-only снят: {path}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


@register(["поставить_только_чтение", "поставить_readonly", "сделать_только_чтение"])
def set_readonly(args):
    """Поставить флаг "только чтение" на файл.

    Параметры:
      путь — путь к файлу (обязательный)

    Пример:
      {"поставить_только_чтение": {"путь": "D:/iso/win7.iso"}}
    """
    import stat
    args = args or {}
    path = args.get("путь") or args.get("path") or ""
    if not path:
        return "Укажите 'путь'."
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode & ~stat.S_IWRITE)
        return f"✅ Флаг read-only установлен: {path}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


@register(["снять_только_чтение_со_всей_папки", "снять_readonly_рекурсивно", "unlock_tree"])
def remove_readonly_tree(args):
    """Рекурсивно снять флаг "только чтение" со всех файлов в папке.

    Параметры:
      путь       — путь к папке (обязательный)
      скрытые    — если true, обрабатывать и скрытые (по умолчанию true)
      пропускать — список расширений для пропуска, например [".sys", ".dll"]

    Возвращает:
      сколько файлов обработано, сколько было уже записываемыми,
      сколько ошибок (с примерами).

    Примеры:
      {"снять_только_чтение_со_всей_папки": {"путь": "D:/Загрузки/Windows 7"}}
      {"снять_только_чтение_со_всей_папки": {"путь": "E:/iso", "пропускать": [".lnk"]}}
    """
    import stat
    args = args or {}
    path = args.get("путь") or args.get("path") or ""
    if not path:
        return "Укажите 'путь' к папке."
    if not os.path.exists(path):
        return f"❌ Не существует: {path}"
    if not os.path.isdir(path):
        return f"Это файл, а не папка: {path}. Используйте 'снять_только_чтение'."

    skip_exts = args.get("пропускать") or []
    if isinstance(skip_exts, str):
        skip_exts = [skip_exts]
    skip_exts = {e.lower() for e in skip_exts}

    changed = 0        # сняли read-only
    already_ok = 0     # уже были записываемыми
    skipped = 0        # пропущены (расширение / папка)
    errors = []

    for dirpath, dirnames, filenames in os.walk(path):
        for name in filenames:
            full = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in skip_exts:
                skipped += 1
                continue
            try:
                st = os.stat(full)
                if st.st_mode & stat.S_IWRITE:
                    already_ok += 1
                else:
                    os.chmod(full, st.st_mode | stat.S_IWRITE)
                    changed += 1
            except Exception as e:
                errors.append(f"{full}: {e}")

    total = changed + already_ok
    out = [f"📂 {path}"]
    out.append(f"   всего файлов обработано: {total}")
    out.append(f"   снято read-only: {changed}")
    out.append(f"   уже были записываемыми: {already_ok}")
    if skipped:
        out.append(f"   пропущено по расширению: {skipped}")
    if errors:
        out.append(f"   ошибок: {len(errors)}")
        for e in errors[:5]:
            out.append(f"     • {e}")
        if len(errors) > 5:
            out.append(f"     ... и ещё {len(errors) - 5}")
    return chr(10).join(out)

