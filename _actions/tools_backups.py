# -*- coding: utf-8 -*-
"""
Команда «удалить_старые_бэкапы» — рекурсивная чистка *.bak*.

Логика:
  • находит все файлы, чьё имя содержит .bak (X.bak, X.bak_TS, X.bak.1);
  • группирует по базовому имени X;
  • для каждой группы:
      – если есть свежие (моложе порога) — удаляются все старые;
      – если все старше порога — остаётся один самый новый.

Timestamp берётся из имени в любом из форматов (.bak_TS, _TS.bak,
.bakTS), если он там есть; иначе — mtime. Группировка идёт по базовому
имени X — оно считается без TS и без .bak-суффикса.
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from _actions import register


_BLOCKED_ROOTS = (
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\$Recycle.Bin",
    r"C:\Recovery",
    r"C:\Boot",
)

# Форматы бэкапов, которые умеем распознавать по имени:
#   X.bak_YYYYMMDD_HHMMSS
#   X_YYYYMMDD_HHMMSS.bak
#   X.bakYYYYMMDD_HHMMSS
#   X.bak, X.bak.1, X.bak_old  (без TS — берём mtime)
_TS_RE = re.compile(r"(?P<ts>\d{8}_\d{6})")
_BAK_SUFFIX_RE = re.compile(r"\.bak(?:[._][^/\\]*)?$", re.IGNORECASE)


def _is_blocked(path: Path) -> bool:
    s = str(path).replace("/", "\\").lower()
    for b in _BLOCKED_ROOTS:
        bl = b.lower()
        if s == bl or s.startswith(bl + "\\"):
            return True
    return False


def _fmt(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} МБ"
    if n >= 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n} Б"


def _parse_bak(name):
    """(base, ts_str_or_None).

    base — имя без TS и без .bak-суффикса. ts_str — строка
    YYYYMMDD_HHMMSS, если TS удалось выделить из имени.

    None возвращается, если это не похоже на .bak-файл.
    """
    ts_str = None
    work = name
    m = _TS_RE.search(name)
    if m:
        ts_str = m.group("ts")
        left = name[:m.start()].rstrip("._")
        right = name[m.end():]
        work = left + right
    m2 = _BAK_SUFFIX_RE.search(work)
    if not m2:
        return None, None
    base = work[:m2.start()]
    if not base:
        return None, None
    return base, ts_str


def _group_key(name):
    base, _ = _parse_bak(name)
    return base


def _file_ts(full: Path, fname: str):
    """Timestamp для решения: из имени, если есть; иначе — mtime."""
    _, ts_str = _parse_bak(fname)
    if ts_str:
        try:
            dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            return dt.timestamp()
        except ValueError:
            pass
    try:
        return full.stat().st_mtime
    except OSError:
        return None


@register(["удалить_старые_бэкапы", "чистить_бэкапы", "старые_бэкапы"])
def delete_old_backups(args: dict) -> str:
    """Команда "удалить_старые_бэкапы".

    Рекурсивно находит *.bak* в папке и удаляет устаревшие.

    Для каждого X (базового имени) рассматриваются все его бэкапы
    X.bak*:
      • если среди них есть свежие (моложе порога) — удаляются все
        старые;
      • если все старше порога — остаётся один самый новый, остальные
        удаляются.

    Timestamp берётся из имени файла, если он там есть — в любом из
    форматов: X.bak_YYYYMMDD_HHMMSS, X_YYYYMMDD_HHMMSS.bak,
    X.bakYYYYMMDD_HHMMSS. Иначе — mtime. Это важно: при копировании
    mtime может быть унаследован от оригинала, а имя всегда содержит
    реальный момент бэкапа.

    Параметры:
      • папка         — корневая папка (обязательно), алиас: путь
      • старше_дней   — порог в днях (по умолчанию 7)
      • безопасно     — true: показать, что было бы удалено, но не удалять
    """
    root_arg = (args.get("папка") or args.get("путь")
                or args.get("корень") or "")
    if not root_arg:
        return "❌ удалить_старые_бэкапы: не указана папка"

    root = Path(root_arg).expanduser()
    if not root.exists():
        return f"❌ удалить_старые_бэкапы: папка не найдена: {root}"
    if not root.is_dir():
        return f"❌ удалить_старые_бэкапы: это файл, а не папка: {root}"
    if _is_blocked(root):
        return f"❌ удалить_старые_бэкапы: системная папка запрещена: {root}"

    try:
        days = int(args.get("старше_дней", 7) or 7)
    except (TypeError, ValueError):
        days = 7
    if days < 0:
        days = 0

    dry = bool(args.get("безопасно", False))

    now = time.time()
    cutoff = now - days * 86400
    cutoff_dt = datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M")

    groups = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if _is_blocked(Path(dirpath)):
            dirnames[:] = []
            continue
        for fname in filenames:
            if ".bak" not in fname.lower():
                continue
            key = _group_key(fname)
            if key is None:
                continue
            full = Path(dirpath) / fname
            ts = _file_ts(full, fname)
            if ts is None:
                continue
            # ключ группы — (папка, базовое имя). Иначе X.bak_* из разных
            # папок склеятся в одну группу и мешаются.
            group_key = (str(full.parent), key)
            groups.setdefault(group_key, []).append((full, ts))

    if not groups:
        return (f"🧹 Чистка бэкапов: {root}\n"
                f"   Порог: {days} дн. (старше {cutoff_dt})\n\n"
                f"Ничего не найдено.")

    # (path, size, ts, kept_name)
    to_delete = []
    kept_recent = 0
    kept_last = []
    deleted_by_group = []

    for group_key, items in groups.items():
        # group_key = (папка, базовое_имя)
        group_dir, base_name = group_key
        items.sort(key=lambda x: x[1], reverse=True)
        recent = [(p, m) for p, m in items if m >= cutoff]
        old = [(p, m) for p, m in items if m < cutoff]

        def _rel(pp):
            try:
                return str(pp.relative_to(root))
            except ValueError:
                return str(pp)

        if recent:
            kept_rel = _rel(recent[0][0])
            for p, m in old:
                try:
                    sz = p.stat().st_size
                except OSError:
                    sz = 0
                to_delete.append((p, sz, m, kept_rel))
            kept_recent += len(recent)
            if old:
                deleted_by_group.append((group_dir, base_name,
                                         len(old), None))
        else:
            newest_path, newest_ts = items[0]
            kept_last.append((group_key, newest_path))
            kept_rel = _rel(newest_path)
            for p, m in items[1:]:
                try:
                    sz = p.stat().st_size
                except OSError:
                    sz = 0
                to_delete.append((p, sz, m, kept_rel))
            if len(items) > 1:
                deleted_by_group.append((group_dir, base_name,
                                         len(items) - 1, newest_path))

    total_size = sum(sz for _, sz, _, _ in to_delete)
    n_del = len(to_delete)

    out = []
    out.append(f"🧹 Чистка бэкапов: {root}")
    out.append(f"   Порог: {days} дн. (старше {cutoff_dt})")
    out.append("")

    if dry:
        out.append(f"[безопасно] Будут удалены ({n_del} файлов, "
                   f"{_fmt(total_size)}):")
        out.append("")
        for p, sz, ts, kept_name in to_delete[:30]:
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = p
            ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            out.append(f"  {ts_str}  {_fmt(sz):>10}  {rel}")
            out.append(f"      → в группе оставлен: {kept_name}")
        if len(to_delete) > 30:
            out.append(f"  … и ещё {len(to_delete) - 30}")
    else:
        removed = 0
        failed = 0
        for p, _, _, _ in to_delete:
            try:
                p.unlink()
                removed += 1
            except OSError:
                failed += 1
        out.append(f"Удалено: {removed} файлов, {_fmt(total_size)}")
        if failed:
            out.append(f"Не удалось удалить: {failed}")

    out.append("")
    out.append("Оставлено:")
    out.append(f"  • свежих (моложе порога): {kept_recent}")
    out.append(f"  • «последний в группе»: {len(kept_last)}")

    if deleted_by_group:
        out.append("")
        out.append("Сводка по группам (первые 15):")
        for gdir, bname, n, kept in deleted_by_group[:15]:
            try:
                gdir_rel = str(Path(gdir).relative_to(root))
            except ValueError:
                gdir_rel = gdir
            loc = gdir_rel if gdir_rel != "." else "<корень>"
            if kept is None:
                out.append(f"  {loc}/{bname} — удалено {n} (свежие оставлены)")
            else:
                out.append(f"  {loc}/{bname} — удалено {n}, "
                           f"оставлен {kept.name}")
        if len(deleted_by_group) > 15:
            out.append(f"  … и ещё {len(deleted_by_group) - 15}")

    return "\n".join(out)
@register(["создать_бэкап", "бэкап", "сделать_бэкап"])
def create_backup(args: dict) -> str:
    """Создать бэкап файла: <путь>.bak_YYYYMMDD_HHMMSS.

    Формат имени совпадает с тем, что создаёт _parse_bak / удалить_старые_бэкапы.
    Так что старые бэкапы потом чистятся штатно.

    Параметры:
      путь      — путь к файлу (обязательный), алиас: файл
      в_папку   — куда положить бэкап (по умолчанию рядом с исходным),
                  алиас: куда
      суффикс   — свой суффикс вместо .bak_<TS> (опционально)

    Примеры:
      {"создать_бэкап": {"путь": "E:/x.py"}}
        → E:/x.py.bak_20261017_012345

      {"создать_бэкап": {"путь": "E:/x.py", "в_папку": "E:/backups"}}
        → E:/backups/x.py.bak_20261017_012345
    """
    src = args.get("путь") or args.get("файл") or ""
    if not src:
        return '❌ создать_бэкап: не указан "путь"'
    p = Path(str(src)).expanduser()
    if not p.exists():
        return f"❌ Файл не найден: {p}"
    if not p.is_file():
        return (f"❌ Это не файл: {p}\n"
                f"   (бэкап папок не поддерживается — только файлы)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = args.get("суффикс") or f".bak_{ts}"
    if not str(suffix).startswith("."):
        suffix = "." + str(suffix)

    dst_dir = args.get("в_папку") or args.get("куда")
    if dst_dir:
        dst = Path(str(dst_dir)).expanduser() / (p.name + suffix)
    else:
        dst = p.with_name(p.name + suffix)

    # если по каким-то причинам имя уже занято — добавим _N
    if dst.exists():
        _base = dst
        _i = 2
        while dst.exists():
            dst = _base.with_name(_base.name + f"_{_i}")
            _i += 1

    # запрет на запись в защищённые папки — как у переписать/добавить
    try:
        from _actions.files import _check_write_path
        ok, err = _check_write_path(str(dst))
        if not ok:
            return f"⛔ Бэкап в защищённую папку запрещён: {err}\n   Путь: {dst}"
    except Exception:
        pass

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"❌ Не создать папку {dst.parent}: {e}"

    try:
        import shutil
        shutil.copy2(p, dst)
    except Exception as e:
        return f"❌ Не удалось скопировать: {e}"

    try:
        sz = dst.stat().st_size
    except Exception:
        sz = 0
    if sz < 1024:
        sz_str = f"{sz} Б"
    elif sz < 1024 * 1024:
        sz_str = f"{sz / 1024:.1f} КБ"
    else:
        sz_str = f"{sz / 1024 / 1024:.2f} МБ"

    return f"✅ Бэкап: {dst}\n   {sz_str}"