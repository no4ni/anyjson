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


def _is_7z(path):
    return str(path).lower().endswith(".7z")


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


def _split_masks(mask_str):
    if not mask_str:
        return []
    if isinstance(mask_str, (list, tuple)):
        return [str(m).strip().lower() for m in mask_str if str(m).strip()]
    return [m.strip().lower() for m in str(mask_str).split("|") if m.strip()]


def _matches(name, includes, excludes):
    """Подходит ли путь name под маски (case-insensitive glob)."""
    import fnmatch
    norm = str(name).replace("\\", "/").lower()
    base = norm.rsplit("/", 1)[-1]
    parts = norm.split("/")
    for pat in excludes:
        if pat in parts:
            return False
        if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(norm, pat):
            return False
    if includes:
        for pat in includes:
            if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(norm, pat):
                return True
        return False
    return True


def _unpack_zip(src, dst, overwrite, password, includes=None, excludes=None):
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
            if not _matches(info.filename, includes, excludes):
                skipped += 1
                continue
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


def _unpack_tar(src, dst, overwrite, includes=None, excludes=None):
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
            if not _matches(member.name, includes, excludes):
                skipped += 1
                continue
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


def _unpack_7z(src, dst, overwrite, includes=None, excludes=None, password=None):
    import subprocess
    try:
        from _config import SEVEN_ZIP_EXE
        seven_zip = SEVEN_ZIP_EXE
    except Exception:
        seven_zip = None
    if not seven_zip or not pathlib.Path(seven_zip).exists():
        return "❌ 7z.exe не найден. Укажи путь в side/config.json -> paths.7z_exe."
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return "❌ Не создать папку " + str(dst) + ": " + str(e)

    cmd = [seven_zip, "x", str(src), f"-o{str(dst)}", "-bsp1", "-y"]
    if overwrite:
        cmd.append("-aoa")
    else:
        cmd.append("-aos")
    for pat in (excludes or []):
        if "*" not in pat and "?" not in pat:
            pat = "*" + pat + "*"
        cmd.append("-xr!" + pat)
    for pat in (includes or []):
        if "*" not in pat and "?" not in pat:
            pat = "*" + pat + "*"
        cmd.append("-ir!" + pat)
    if password:
        cmd.append(f"-p{password}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )
        out_buf = []
        while True:
            char = proc.stdout.read(1)
            if not char:
                break
            print(char, end='', flush=True)
            out_buf.append(char)
            if len(out_buf) > 2000:
                out_buf = out_buf[-2000:]
        proc.wait()
        print()
        _raw_tail = "".join(out_buf).strip()

        # Чистим вывод от интерактивного прогресс-бара (строки вида " 10% 187 - file")
        import re
        _clean_lines = []
        for _ln in _raw_tail.splitlines():
            _ln = _ln.strip()
            if not _ln:
                continue
            if re.match(r"^\d+%", _ln):
                continue
            _clean_lines.append(_ln)

        _stats = "\n".join(_clean_lines[-15:])  # Последние 15 чистых строк — это статистика

        if proc.returncode == 0:
            msg = f"✅ Распаковано 7z -> {dst.resolve()}"
            if _stats:
                msg += f"\n\n{_stats}"
            return msg
        else:
            return f"❌ Ошибка 7z (код {proc.returncode}):\n{_stats or _raw_tail}"
    except Exception as e:
        return f"❌ Ошибка вызова 7z: {type(e).__name__}: {e}"


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
      маска        — включить только подходящие файлы (glob через "|":
                     "*.md|*.py"). Для .7z идёт как -i!.
      исключить    — не извлекать файлы/папки с такими именами (glob
                     через "|": "site-packages|venv|__pycache__")

    Поддержка: .zip, .tar, .tar.gz/.tgz, .tar.bz2/.tbz2, .tar.xz/.txz,
    .7z (через py7zr). .rar — подсказка поставить rarfile.

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

    includes = _split_masks(args.get("маска") or args.get("включить") or "")
    excludes = _split_masks(args.get("исключить") or args.get("без") or "")

    # 7z.exe — универсальный архиватор: zip, tar, tar.gz, 7z, rar...
    # Если он есть в конфиге — используем его для ВСЕХ форматов.
    # Python-ветки (zipfile/tarfile) остаются как fallback.
    try:
        from _config import SEVEN_ZIP_EXE
        _has_7z = SEVEN_ZIP_EXE and pathlib.Path(SEVEN_ZIP_EXE).exists()
    except Exception:
        _has_7z = False

    if _has_7z:
        return _unpack_7z(src, dst, overwrite, includes, excludes, password)

    # Fallback: стандартная библиотека Python.
    if _is_zip(src):
        return _unpack_zip(src, dst, overwrite, password, includes, excludes)
    if _is_tar(src):
        return _unpack_tar(src, dst, overwrite, includes, excludes)
    low = str(src).lower()
    if low.endswith(".rar"):
        return ("❌ .rar требует rarfile. Поставь "
                '{"установить_пакет": {"имя": "rarfile"}}, '
                "затем добавь поддержку в _actions/archive.py.")
    return "❌ Неизвестный формат архива: " + src.name