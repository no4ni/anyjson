"""Защитный wrapper для команды «питон».

Запускается как: python -u _core/_py_guard.py <target.py> [args...]

Ставит sys.addaudithook и блокирует:
  - запись/удаление/переименование/rmtree в системных папках Windows
    (та же модель, что у команд «переписать» и «добавить»);
  - os.system / os.exec / subprocess.Popen — целиком.

Нарушение → чистая строка в stderr + os._exit(3).
Никакого traceback: агент не должен видеть, где живёт защита.
os._exit не ловится try/except — скрипт умирает, даже если попытается
проглотить ошибку.
"""
import os
import re
import sys


_RECURSIVE_BLOCK_RAW = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
    r"C:\Recovery",
    r"C:\Boot",
]

_EXACT_BLOCK_RAW = [
    r"C:\Users",
    r"C:\Users\All Users",
    r"C:\Users\Default",
    r"C:\Users\Public",
]

for _v in ("SystemRoot", "windir", "ProgramFiles",
           "ProgramFiles(x86)", "ProgramData"):
    _p = os.environ.get(_v)
    if _p:
        _RECURSIVE_BLOCK_RAW.append(_p)

_DRIVE_ROOT = re.compile(r"^[a-z]:\\?$", re.IGNORECASE)


def _norm(p):
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(p))))
    except Exception:
        return ""


_RECURSIVE_BLOCK = sorted({_norm(p) for p in _RECURSIVE_BLOCK_RAW if p})
_EXACT_BLOCK = sorted({_norm(p) for p in _EXACT_BLOCK_RAW if p})


def _is_blocked(path):
    p = _norm(path)
    if not p:
        return True
    if _DRIVE_ROOT.match(p):
        return True
    for b in _EXACT_BLOCK:
        if p == b:
            return True
    for b in _RECURSIVE_BLOCK:
        if p == b or p.startswith(b + os.sep):
            return True
    return False


def _write_mode(mode, flags):
    if isinstance(mode, str) and mode:
        return any(c in mode for c in "wax+")
    if isinstance(flags, int) and flags:
        _W = 0
        for _n in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"):
            _W |= getattr(os, _n, 0)
        return bool(flags & _W)
    return False


def _fail(what, path):
    try:
        sys.stderr.write(
            "⛔ питон: " + what + " запрещено: " + str(path) + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(3)


def _audit(event, args):
    if event == "open":
        if not args:
            return
        path = args[0]
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if not _write_mode(mode, flags):
            return
        if _is_blocked(path):
            _fail("запись в защищённую папку", path)
    elif event in ("os.remove", "os.unlink", "os.rmdir", "os.mkdir"):
        path = args[0] if args else None
        if _is_blocked(path):
            _fail("изменение защищённой папки", path)
    elif event == "shutil.rmtree":
        path = args[0] if args else None
        _fail("rmtree", path)
    elif event == "os.rename":
        src = args[0] if len(args) > 0 else None
        dst = args[1] if len(args) > 1 else None
        if _is_blocked(src) or _is_blocked(dst):
            _fail("переименование в защищённой папке",
                  str(src) + " → " + str(dst))
    elif event in ("os.system", "os.exec", "subprocess.Popen"):
        _fail(event, "")


sys.addaudithook(_audit)


def _main():
    if len(sys.argv) < 2:
        sys.stderr.write("py_guard: no target script\n")
        sys.exit(2)
    target = sys.argv[1]
    sys.argv = [target] + sys.argv[2:]
    tdir = os.path.dirname(os.path.abspath(target))
    if tdir and tdir not in sys.path:
        sys.path.insert(0, tdir)
    import runpy
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    _main()