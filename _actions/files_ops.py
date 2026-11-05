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

@register(["создать_папку", "создать_каталог", "новая_папка"])
def create_dir_(args):
    """Создать папку (со всеми родительскими).

    Параметры:
      путь              — путь к папке (обязательный)
      только_без_файлов — true (по умолчанию): удалить только если
                          внутри нет файлов (пустая или только пустые
                          подпапки). false: со всем содержимым.

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
    path = args.get("путь") or ""
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
    import os as _os_bak
    import re as _re_bak
    path = args.get("путь", "")
    if not path:
        return "Укажите путь в поле 'путь'"
    _base = _os_bak.path.basename(path)
    if _re_bak.search(
            r"\.bak_\d{8}_\d{6}$|_\d{8}_\d{6}\.bak$|\.bak\d{8}_\d{6}$",
            _base):
        _dir = (_os_bak.path.dirname(path) or ".").replace(chr(92), "/")
        return ("⛔ Это файл бэкапа (штатная метка anyjson): " + _base + "\n"
                "   Для чистки бэкапов — команда «удалить_старые_бэкапы»:\n"
                '   {"удалить_старые_бэкапы": {"папка": "' + _dir
                + '", "безопасно": true}}\n'
                "   безопасно: true — только предпросмотр.\n"
                "   ⛔ Не обходи защиту (переименование, перемещение,\n"
                "   питон, архивы): бэкапы существуют ровно для того, чтобы\n"
                "   их можно было восстановить. Свежий бэкап подберёт\n"
                "   «удалить_старые_бэкапы», когда истечёт срок.\n"
                "   Параметры: папка (алиас путь/корень), старше_дней (7), "
                "безопасно.")
    return safe_delete_file(path)


@register(["переместить", "переименовать"])
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
    """Удалить папку.

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

    # Сравнение по АБСОЛЮТНОМУ нормализованному пути:
    #   • корни дисков (C:\, D:\, E:\, ...) — только ТОЧНОЕ совпадение;
    #   • системные папки (C:\Windows, C:\Program Files, ...) — совпадение
    #     «сегмент + сепаратор» (C:\Windows\foo запрещено, C:\WindowsXP — нет).
    import string as _string
    _root_letters = set(_string.ascii_uppercase)
    try:
        _abs = str(Path(path).resolve()).rstrip("\\/").lower()
    except Exception:
        _abs = str(path).rstrip("\\/").lower()

    # 1. Корень диска — только точное совпадение
    _is_drive_root = (
        len(_abs) == 2 and _abs[1] == ":" and _abs[0].upper() in _root_letters
    )
    if _is_drive_root:
        return f"⛔ Удаление корня диска {path} запрещено."

    # 2. Системные папки — сегмент + сепаратор
    _sys_dirs = [
        r"c:\windows",
        r"c:\program files",
        r"c:\program files (x86)",
        r"c:\programdata",
    ]
    for _d in _sys_dirs:
        _dl = _d.lower()
        if _abs == _dl or _abs.startswith(_dl + "\\"):
            return f"⛔ Удаление системной папки {path} запрещено."

    # 3. anyjson и все его родители — никогда
    try:
        from _config import BASE_DIR as _BASE
        _b = str(Path(_BASE).resolve()).rstrip("\\/").lower()
        if _abs == _b or _abs.startswith(_b + "\\"):
            return (f"⛔ Удаление anyjson и его содержимого запрещено: {path}")
        # родители BASE_DIR (вплоть до корня диска)
        _p = Path(_b).parent
        while str(_p) != str(_p.parent):
            _pl = str(_p).rstrip("\\/").lower()
            if _abs == _pl:
                return (f"⛔ Удаление родительской папки anyjson "
                        f"({path}) запрещено — снесёт сам anyjson.")
            _p = _p.parent
    except Exception:
        pass
    if not os.path.exists(path):
        return f"❌ Папка не существует: {path}"
    if not os.path.isdir(path):
        return f"❌ Это не папка: {path}"

    only_empty = args.get("только_без_файлов")
    if only_empty is None:
        only_empty = True
    if isinstance(only_empty, str):
        only_empty = only_empty.strip().lower() not in ("0", "false", "no", "нет", "off", "")
    else:
        only_empty = bool(only_empty)

    if only_empty:
        try:
            files = [f for f in Path(path).rglob("*") if f.is_file()]
        except Exception as e:
            return f"❌ Не обойти папку: {e}"
        if files:
            head = chr(10).join("  " + str(f) for f in files[:5])
            more = (chr(10) + "  … и ещё " + str(len(files) - 5)) if len(files) > 5 else ""
            return ("⛔ В папке " + str(len(files)) + " файлов. "
                    "Если точно нужно удалить со всем содержимым — "
                    "передай только_без_файлов: false." + chr(10)
                    + "Первые файлы:" + chr(10) + head + more)

    try:
        shutil.rmtree(path)
        return f"✅ Папка удалена: {path}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


@register(["удалить_пустые_папки", "убрать_пустые", "почистить_пустые"])
def remove_empty_dirs(args):
    """Рекурсивно удалить пустые папки.

    Пустой считается папка без файлов. Вложенные пустые папки
    удаляются снизу вверх — то есть папка, внутри которой только
    пустые подпапки, тоже удаляется.

    Параметры:
      корень    — корневая папка (обязательный), алиас: путь
      безопасно — true: только показать список, не удалять
      только_файлы_как_непустое — не влияет, оставлено на будущее

    Сам корень НЕ удаляется — даже если внутри него пусто.

    Защита: anyjson, его родители, системные папки — не удаляются.

    Примеры:
      {"удалить_пустые_папки": {"корень": "E:/Jericho/tmp"}}
      {"удалить_пустые_папки": {"корень": "E:/downloads",
                                "безопасно": true}}
    """
    from pathlib import Path as _Path

    args = args or {}
    root = args.get("корень") or args.get("путь") or ""
    if not root:
        return "Укажи 'корень'."

    try:
        root_p = _Path(root).expanduser().resolve()
    except Exception as e:
        return f"❌ Плохой путь: {e}"

    if not root_p.is_dir():
        return f"❌ Не папка: {root_p}"

    safe = args.get("безопасно", False)
    if isinstance(safe, str):
        safe = safe.strip().lower() not in ("0", "false", "нет", "off", "")

    # Защита только системных папок. anyjson и его содержимое
    # специально НЕ защищены: команда удаляет только пустые папки,
    # файлы и данные не теряются, а memory агента чистить надо уметь.
    def _protected(p):
        try:
            a = str(_Path(p).resolve()).rstrip("\\/").lower()
        except Exception:
            return True
        # системные
        for _sd in (r"c:\\windows", r"c:\\program files",
                    r"c:\\program files (x86)", r"c:\\programdata"):
            if a == _sd or a.startswith(_sd + "\\"):
                return True
        return False

    deleted = []
    errors = []

    # topdown=False — обход от самых глубоких к корню.
    # Тогда после удаления детей папка-родитель тоже станет пустой.
    for dirpath, dirnames, filenames in os.walk(str(root_p), topdown=False):
        try:
            cur = _Path(dirpath).resolve()
        except Exception:
            continue
        if cur == root_p:
            continue  # сам корень не трогаем
        if filenames:
            continue  # в папке есть файлы
        if _protected(dirpath):
            continue
        try:
            if safe:
                # пустота в текущий момент?
                if not os.listdir(dirpath):
                    deleted.append(dirpath)
            else:
                os.rmdir(dirpath)  # упадёт, если не пусто
                deleted.append(dirpath)
        except OSError:
            # не пусто, нет прав или занято — пропускаем
            continue
        except Exception as e:
            errors.append(f"{dirpath}: {e}")

    lines = []
    if safe:
        lines.append(f"🔎 Пустых папок найдено: {len(deleted)} (режим показа)")
    else:
        lines.append(f"🗑️ Удалено пустых папок: {len(deleted)}")
    lines.append(f"   корень: {root_p}")
    if deleted:
        lines.append("")
        for d in deleted[:50]:
            lines.append("  • " + d)
        if len(deleted) > 50:
            lines.append(f"  ... и ещё {len(deleted) - 50}")
    if errors:
        lines.append("")
        lines.append(f"⚠️ Ошибок: {len(errors)}")
        for e in errors[:5]:
            lines.append("  • " + e)
    if safe and deleted:
        lines.append("")
        lines.append("Для удаления: повтори без \"безопасно\".")
    return chr(10).join(lines)


@register(["копировать", "скопировать", "copy"])
def copy_item(args):
    """Скопировать файл или папку.

    Параметры:
      откуда      — источник (обязательный), алиасы: из, источник
      куда        — назначение (обязательный), алиасы: в, назначение
      перезаписать — true: заменить существующую цель
                     (по умолчанию false — отказать)

    Если «куда» — существующая папка, копирует ВНУТРЬ неё
    с тем же именем. Если «куда» — путь к новому файлу/папке,
    копирует под этим именем.

    Работает с папками рекурсивно. Сохраняет метаданные (mtime).

    Примеры:
      {"копировать": {"откуда": "E:/a.txt", "куда": "E:/backup/"}}
      {"копировать": {"откуда": "E:/data", "куда": "D:/backup/data",
                      "перезаписать": true}}
    """
    import shutil as _sh
    args = args or {}
    src_arg = args.get("откуда") or args.get("из") or args.get("источник")
    dst_arg = args.get("куда") or args.get("в") or args.get("назначение")
    if not src_arg:
        return "Укажи 'откуда'."
    if not dst_arg:
        return "Укажи 'куда'."

    overwrite = args.get("перезаписать", False)
    if isinstance(overwrite, str):
        overwrite = overwrite.strip().lower() not in ("0", "false", "нет", "off", "")

    src = Path(src_arg).expanduser()
    dst = Path(dst_arg).expanduser()

    if not src.exists():
        return f"❌ Источник не существует: {src}"

    # защита чужого профиля — и на чтение, и на запись
    from _actions.tools_files import _foreign_guard
    _g1 = _foreign_guard(str(src))
    if _g1:
        return _g1
    _g2 = _foreign_guard(str(dst))
    if _g2:
        return _g2

    try:
        src_r = src.resolve()
        dst_r = dst.resolve()
    except Exception:
        src_r, dst_r = src, dst

    # 1. src и dst совпадают — no-op
    if src_r == dst_r:
        return f"❌ Источник и назначение совпадают: {src}"

    # 2. dst — существующая папка → копируем ВНУТРЬ под именем src
    if dst.is_dir():
        dst = dst / src.name

    # 3. проверка «внутрь себя» (для папок)
    if src.is_dir():
        try:
            if dst_r == src_r or dst_r.is_relative_to(src_r):
                return (f"❌ Нельзя скопировать папку {src} "
                        f"внутрь самой себя ({dst})")
        except (ValueError, AttributeError):
            pass

    # 4. цель существует?
    if dst.exists():
        if not overwrite:
            return (f"❌ Цель уже существует: {dst}. "
                    f"Добавь \"перезаписать\": true, чтобы заменить.")
        # удалить цель перед копированием
        try:
            if dst.is_dir():
                _sh.rmtree(dst)
            else:
                dst.unlink()
        except Exception as e:
            return f"❌ Не удалось удалить старую цель: {e}"

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            _sh.copytree(str(src), str(dst))
        else:
            _sh.copy2(str(src), str(dst))
        return f"✅ Скопировано: {src} → {dst}"
    except Exception as e:
        return f"❌ Ошибка копирования: {e}"


@register(["починить_вложенность", "развернуть_вложенность",
           "убрать_вложенность", "fix_nesting"])
def fix_nesting(args):
    """Убрать дублирующую вложенность папок вида X/X/X/...

    Ищет папки, у которых есть подпапка с ТЕМ ЖЕ именем, и «поднимает»
    содержимое вложенной на уровень выше. Работает рекурсивно, снизу
    вверх — если после подъёма снова образовалась X/X, разворачивает
    ещё раз.

    Параметры:
      корень        — где искать (обязательный), алиас: путь
      сухо          — true: только показать, что будет сделано
                      (по умолчанию false — реально делать)
      включая_файлы — true: разворачивать, даже если в родителе есть
                      другие файлы/папки (по умолчанию false — только
                      безопасный случай, когда X содержит РОВНО одну X/X)

    Защита: anyjson, его содержимое, системные папки — не трогает.

    Примеры:
      {"починить_вложенность": {"корень": "E:/projects/motionVideoplayer", "сухо": true}}
      {"починить_вложенность": {"корень": "E:/projects/motionVideoplayer", "включая_файлы": true}}
    """
    import shutil as _sh
    args = args or {}
    root = args.get("корень") or args.get("путь") or ""
    if not root:
        return "Укажи 'корень'."

    try:
        root_p = Path(root).expanduser().resolve()
    except Exception as e:
        return f"❌ Плохой путь: {e}"
    if not root_p.is_dir():
        return f"❌ Не папка: {root_p}"

    dry = args.get("сухо", False)
    if isinstance(dry, str):
        dry = dry.strip().lower() not in ("0", "false", "нет", "off", "")
    incl = args.get("включая_файлы", False)
    if isinstance(incl, str):
        incl = incl.strip().lower() not in ("0", "false", "нет", "off", "")

    # защита anyjson / системных папок
    try:
        from _config import BASE_DIR as _BASE
        _base_l = str(Path(_BASE).resolve()).rstrip("\\/").lower()
    except Exception:
        _base_l = ""

    def _protected(pp):
        try:
            a = str(Path(pp).resolve()).rstrip("\\/").lower()
        except Exception:
            return True
        if _base_l and (a == _base_l or a.startswith(_base_l + "\\")):
            return True
        for _sd in (r"c:\\windows", r"c:\\program files",
                    r"c:\\program files (x86)", r"c:\\programdata"):
            if a == _sd or a.startswith(_sd + "\\"):
                return True
        return False

    actions = []
    errors = []

    def _try_unwrap(p_cur):
        """Один уровень X/X -> X. True если что-то сделал."""
        nested = p_cur / p_cur.name
        if not nested.is_dir():
            return False
        try:
            outer = [it for it in p_cur.iterdir() if it.name != p_cur.name]
        except Exception as e:
            errors.append(f"{p_cur}: {e}")
            return False
        if outer and not incl:
            return False
        try:
            inner = list(nested.iterdir())
        except Exception as e:
            errors.append(f"{nested}: {e}")
            return False
        outer_names = {it.name for it in outer}
        for item in inner:
            if item.name in outer_names:
                return False
        moved_any = False
        for item in inner:
            target = p_cur / item.name
            if target.exists():
                return moved_any
            if dry:
                moved_any = True
            else:
                try:
                    _sh.move(str(item), str(target))
                    moved_any = True
                except Exception as e:
                    errors.append(f"{item}: {e}")
                    return moved_any
        if not dry:
            try:
                nested.rmdir()
            except OSError:
                return moved_any
        return True

    def _rec(p_cur):
        if _protected(p_cur):
            return
        try:
            children = list(p_cur.iterdir())
        except Exception:
            return
        for child in children:
            if child.is_dir():
                _rec(child)
        if dry:
            # в режиме показа файловая система не меняется —
            # считаем только ОДИН уровень (иначе бесконечный цикл)
            if _try_unwrap(p_cur):
                actions.append((str(p_cur), 1))
        else:
            count = 0
            while _try_unwrap(p_cur):
                count += 1
                if count > 50:
                    break
            if count:
                actions.append((str(p_cur), count))

    _rec(root_p)

    lines = []
    if dry:
        lines.append(f"🔎 Вложенностей найдено: {len(actions)} (режим показа)")
    else:
        lines.append(f"✅ Исправлено папок: {len(actions)}")
    lines.append(f"   корень: {root_p}")
    lines.append(f"   включая_файлы: {incl}")
    if actions:
        lines.append("")
        for path_a, count_a in actions[:30]:
            lines.append(f"  • {path_a}  (уровней: {count_a})")
        if len(actions) > 30:
            lines.append(f"  ... и ещё {len(actions) - 30}")
    if errors:
        lines.append("")
        lines.append(f"⚠️ Ошибок: {len(errors)}")
        for e in errors[:5]:
            lines.append(f"  • {e}")
    if dry and actions:
        lines.append("")
        lines.append('Для применения: повтори без "сухо".')
    return chr(10).join(lines)


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
    path = args.get("путь") or ""
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
    path = args.get("путь") or ""
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
    path = args.get("путь") or ""
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


@register(["дедуплицировать_строки", "убрать_дубли_строк", "dedupe_lines"])
def dedupe_lines(args):
    """Удалить повторяющиеся строки в файле.

    Параметры:
      путь         — файл (обязательно), алиас: файл
      сохранить    — куда (алиас: выход). По умолчанию — перезапись исходника
      подряд       — true (по умолч.): только идущие подряд дубли;
                     false: все дубли, оставить первое вхождение
      пустые       — false (по умолч.): пустые строки сохраняются и не
                     считаются дублями; true: считать дублями
      пробелы      — false (по умолч.): сравнивать с точностью до пробелов;
                     true: сравнивать после strip()
      нестрого     — false (по умолч.): нормализовать NBSP, кавычки, тире,
                     двойные пробелы перед сравнением
      предпросмотр — false (по умолч.): true — только показать, что удалится
      перезаписать — false (по умолч.): разрешить запись в существующий
                     «сохранить» (если отличается от пути)

    Возвращает: Удалено N строк (было X, стало Y).

    Для кода осторожно: две подряд идущие одинаковые строки
    (например, две "pass" или два "return None") могут быть
    осмысленными в разных ветках. Используй предпросмотр: true
    и просматривай, что удалится.

    Примеры:
      {"дедуплицировать_строки": {"путь": "E:/logs/app.log"}}
      {"дедуплицировать_строки": {"путь": "E:/in.txt",
                                   "сохранить": "E:/out.txt",
                                   "подряд": false,
                                   "предпросмотр": true}}
    """
    args = args or {}
    p_arg = args.get("путь") or args.get("файл") or ""
    if not p_arg:
        return "Укажи 'путь' — файл для обработки."

    src = Path(str(p_arg)).expanduser()
    if not src.is_file():
        return f"❌ Файл не найден: {src}"

    out_arg = args.get("сохранить") or args.get("выход")
    dst = Path(str(out_arg)).expanduser() if out_arg else src

    def _as_bool(v, default=False):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() not in ("", "0", "false", "нет", "off", "no")
        return bool(v)

    only_consecutive = _as_bool(args.get("подряд"), True)
    count_empty = _as_bool(args.get("пустые"), False)
    strip_cmp = _as_bool(args.get("пробелы"), False)
    loose = _as_bool(args.get("нестрого"), False)
    dry = _as_bool(args.get("предпросмотр"), False)
    overwrite = _as_bool(args.get("перезаписать"), False)

    try:
        same = src.resolve() == dst.resolve()
    except Exception:
        same = str(src) == str(dst)

    if not same and dst.exists() and not overwrite:
        return (f"❌ Цель уже существует: {dst}\n"
                f"   Перезаписать: добавь \"перезаписать\": true\n"
                f"   Или укажи другое имя через \"сохранить\".")

    try:
        raw = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = src.read_text(encoding="cp1251", errors="replace")

    has_crlf = "\r\n" in raw
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    trailing_nl = normalized.endswith("\n")

    if normalized == "":
        lines = []
    elif trailing_nl:
        lines = normalized.split("\n")[:-1]
    else:
        lines = normalized.split("\n")

    def _norm(s):
        if loose:
            s = s.replace(chr(160), " ")
            s = s.replace(chr(171), '"').replace(chr(187), '"')
            s = s.replace(chr(8211), "-").replace(chr(8212), "-")
            while "  " in s:
                s = s.replace("  ", " ")
        if strip_cmp:
            s = s.strip()
        return s

    out_lines = []
    removed = 0
    removed_examples = []
    seen = set()
    last_key = None

    for i, line in enumerate(lines):
        key = _norm(line)

        if not key.strip() and not count_empty:
            out_lines.append(line)
            last_key = None
            continue

        if only_consecutive:
            if last_key is not None and last_key == key:
                removed += 1
                if len(removed_examples) < 30:
                    removed_examples.append((i + 1, line))
                continue
        else:
            if key in seen:
                removed += 1
                if len(removed_examples) < 30:
                    removed_examples.append((i + 1, line))
                continue
            seen.add(key)

        out_lines.append(line)
        last_key = key

    total_before = len(lines)
    total_after = len(out_lines)

    if removed == 0:
        return f"ℹ️ В {src.name} дублей не найдено ({total_before} строк)."

    if dry:
        result = [
            f"👁 [предпросмотр] Удалено было бы {removed} строк "
            f"({total_before} -> {total_after}):"
        ]
        for ln_no, txt in removed_examples:
            shown = txt if len(txt) <= 120 else txt[:117] + "..."
            result.append(f"  - {ln_no}: {shown}")
        if removed > len(removed_examples):
            result.append(f"  ... и ещё {removed - len(removed_examples)}")
        if not same:
            result.append(f"Будет записано в: {dst}")
        return "\n".join(result)

    new_text = "\n".join(out_lines)
    if trailing_nl:
        new_text += "\n"
    if has_crlf:
        new_text = new_text.replace("\n", "\r\n")

    try:
        if not same:
            dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    except Exception as e:
        return f"❌ Ошибка записи: {e}"

    action = "перезаписан" if same else f"сохранён в {dst}"
    return (f"✅ Удалено {removed} строк "
            f"({total_before} -> {total_after}).\n"
            f"   Файл {action}.")

