"""Распаковка архивов: zip и tar-семейство на stdlib.

.7z и .rar требуют сторонних пакетов (py7zr, rarfile) — возвращается
подсказка, а не тихое падение. Записи с абсолютным путём или ".."
пропускаются (zip-slip). Симлинки и устройства из tar не извлекаются.
"""
import pathlib
import tarfile
import zipfile

from _actions import register


_TAR_SUFFIXES = (
    ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar",
)
_STRIP_SUFFIXES = _TAR_SUFFIXES + (".zip",)


def _is_tar(path):
    low = str(path).lower()
    return any(low.endswith(s) for s in _TAR_SUFFIXES)


def _is_zip(path):
    return str(path).lower().endswith(".zip")


def _default_dst(src):
    name = src.name
    low = name.lower()
    for suf in _STRIP_SUFFIXES:
        if low.endswith(suf):
            return src.parent / name[:-len(suf)]
    return src.parent / src.stem


def _safe_join(root, member):
    m = str(member).replace("\\\\", "/")
    if m.startswith("/") or (len(m) > 1 and m[1] == ":"):
        return None
    try:
        target = (root / m).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return target


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "нет", "off")
    return bool(v)


def _unpack_zip(src, dst, overwrite, password):
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        return "❌ Битый zip: " + str(e)
    except Exception as e:
        return "❌ Не открыть zip: " + str(e)

    pwd = password.encode("utf-8") if isinstance(password, str) and password else None
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return "❌ Не создать папку " + str(dst) + ": " + str(e)

    extracted = 0
    skipped = 0
    with zf:
        for info in zf.infolist():
            target = _safe_join(dst, info.filename)
            if target is None:
                skipped += 1
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists() and not overwrite:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info, pwd=pwd) as fsrc, open(target, "wb") as fdst:
                    while True:
                        chunk = fsrc.read(1 << 20)
                        if not chunk:
                            break
                        fdst.write(chunk)
            except RuntimeError as e:
                return ("❌ Не прочитать " + info.filename + ": " + str(e)
                        + ". Укажи 'пароль' (AES-шифрование stdlib не умеет).")
            except Exception as e:
                return "❌ Ошибка на " + info.filename + ": " + str(e)
            extracted += 1

    tail = ", пропущено " + str(skipped) if skipped else ""
    return "✅ Распаковано " + str(extracted) + " файлов" + tail + " -> " + str(dst.resolve())


def _unpack_tar(src, dst, overwrite):
    try:
        tf = tarfile.open(src, "r:*")
    except Exception as e:
        return "❌ Не открыть tar: " + str(e)
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return "❌ Не создать папку " + str(dst) + ": " + str(e)

    extracted = 0
    skipped = 0
    with tf:
        for member in tf:
            target = _safe_join(dst, member.name)
            if target is None:
                skipped += 1
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                skipped += 1
                continue
            if target.exists() and not overwrite:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                fsrc = tf.extractfile(member)
                if fsrc is None:
                    skipped += 1
                    continue
                with fsrc, open(target, "wb") as fdst:
                    while True:
                        chunk = fsrc.read(1 << 20)
                        if not chunk:
                            break
                        fdst.write(chunk)
            except Exception as e:
                return "❌ Ошибка на " + member.name + ": " + str(e)
            extracted += 1

    tail = ", пропущено " + str(skipped) if skipped else ""
    return "✅ Распаковано " + str(extracted) + " файлов" + tail + " -> " + str(dst.resolve())


@register(["распаковать", "разархивировать", "извлечь_архив", "распаковать_архив"])
def unpack_archive(args):
    """Распаковать архив (zip, tar, tar.gz/tgz, tar.bz2, tar.xz).

    Параметры:
      путь         — файл-архив (обязательно)
      куда         — папка назначения (по умолч. рядом, имя = архив
                     без расширений)
      перезаписать — true: заменить существующие файлы (по умолч. false,
                     существующие пропускаются)
      пароль       — пароль zip (опционально; AES не поддерживается)

    Поддержка: .zip, .tar, .tar.gz/.tgz, .tar.bz2/.tbz2, .tar.xz/.txz.
    .7z/.rar — подсказка поставить py7zr/rarfile.

    Примеры:
      {"распаковать": {"путь": "E:/tmp/data.zip"}}
      {"распаковать": {"путь": "E:/tmp/a.tar.gz", "куда": "E:/tmp/a"}}
      {"распаковать": {"путь": "E:/tmp/p.zip", "перезаписать": true}}

    Записи с абсолютным путём или ".." внутри архива пропускаются
    (zip-slip). Смотреть содержимое без распаковки — архив_список.
    """
    args = args or {}
    src_s = (args.get("путь") or args.get("файл")
             or args.get("архив") or args.get("значение") or "")
    src_s = str(src_s).strip()
    if not src_s:
        return 'Укажи архив: {"распаковать": {"путь": "E:/.../file.zip"}}'
    src = pathlib.Path(src_s).expanduser()
    if not src.exists():
        return "❌ Архив не найден: " + str(src)
    if not src.is_file():
        return "❌ Не файл: " + str(src)

    dst_s = (args.get("куда") or args.get("папка")
             or args.get("сохранить_как") or "")
    dst = pathlib.Path(str(dst_s).strip()).expanduser() if dst_s else _default_dst(src)
    overwrite = _truthy(args.get("перезаписать", False))
    password = args.get("пароль") or args.get("password")

    if _is_zip(src):
        return _unpack_zip(src, dst, overwrite, password)
    if _is_tar(src):
        return _unpack_tar(src, dst, overwrite)
    low = str(src).lower()
    if low.endswith(".7z") or low.endswith(".rar"):
        return ("❌ .7z/.rar требуют сторонних пакетов. Поставь "
                '{"установить_пакет": {"имя": "py7zr"}} (7z) или rarfile (rar), '
                "затем добавь поддержку в _actions/archive.py.")
    return "❌ Неизвестный формат архива: " + src.name