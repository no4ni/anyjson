"""Защитный wrapper для команды «питон».

Запускается как: python -u _core/_py_guard.py <target.py> [args...]

Ставит sys.addaudithook и блокирует:
  - запись/удаление/переименование/rmtree в системных папках Windows
    (та же модель, что у команд «переписать» и «добавить»);
  - os.system / os.exec / subprocess.Popen — кроме ffmpeg/ffprobe из
    доверенных каталогов; ROCm-пробы (rocm-sdk) отбиваются молча.

Нарушение → чистая строка в stderr, без traceback (агент не должен
видеть, где живёт защита). Защищённые папки: os._exit(3), не ловится.
Запуск процессов: catchable PermissionError/FileNotFoundError (soft).
ROCm-пробы: без stderr, исключение без ⛔.
"""
import json
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


def _trusted_ffmpeg_roots():
    """Каталоги, откуда ffmpeg/ffprobe разрешены к запуску.

    Два источника:
      1. dirname(ffmpeg_exe / ffprobe_exe) из side/config.json —
         читаем напрямую, чтобы не тянуть _config (может sys.exit);
      2. site-packages / dist-packages из sys.path — там
         imageio-ffmpeg держит бандл ffmpeg-win64-v*.exe.
    """
    roots = set()
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(os.path.dirname(here), "side", "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        paths = cfg.get("paths") or {}
        for key in ("ffmpeg_exe", "ffprobe_exe"):
            p = paths.get(key)
            if p:
                roots.add(_norm(os.path.dirname(os.fspath(p))))
    except Exception:
        pass
    for sp in sys.path:
        if not sp:
            continue
        low = sp.replace("\\", "/").lower()
        if low.endswith("/site-packages") or low.endswith("/dist-packages"):
            roots.add(_norm(sp))
    # imageio-ffmpeg хранит бандл вне site-packages
    # (%LOCALAPPDATA%\imageio\ffmpeg\ или ~/.imageio/ffmpeg/).
    # Спрашиваем сам пакет — где бинарь.
    try:
        import imageio_ffmpeg as _iio_ff
        _p = _iio_ff.get_ffmpeg_exe()
        if _p and os.path.exists(_p):
            roots.add(_norm(os.path.dirname(os.path.abspath(_p))))
    except Exception:
        pass
    return frozenset(r for r in roots if r)


_TRUSTED_FFMPEG_ROOTS = _trusted_ffmpeg_roots()

_FFMPEG_LIKE_RE = re.compile(
    r"^(ffmpeg|ffprobe)(-[A-Za-z0-9._-]+)?(\.exe)?$", re.IGNORECASE
)


_SHELL_META_RE = re.compile(r"[&|;<>()\n]")


def _popen_target(args):
    """(basename, fullpath) бинаря из audit-args subprocess.Popen.

    Формат события: (executable, argv, cwd, env).
      • argv — список: [exe, arg1, ...]. Бинарь — argv[0] (или executable).
      • argv — строка: Windows-форма Popen("prog -a -b") без shell=True.
        shell=True всегда даёт список ['/c', cmd], поэтому строка здесь
        означает shell=False, где метасимволы (& | ;) не интерпретируются.
        Блокируем только если executable сам является shell (cmd.exe/powershell).
    """
    if not args or len(args) < 2:
        return "", ""
    exe = args[0]
    a = args[1]
    cand = None
    if isinstance(a, str):
        # shell=True всегда даёт список ['/c', cmd].
        # Если здесь строка — это shell=False, метасимволы не опасны.
        # Блокируем только если executable сам является shell.
        if exe and isinstance(exe, str) and any(s in exe.lower() for s in ("cmd.exe", "powershell", "pwsh")):
            return "", ""
        _s = a.strip()
        if _s[:1] in ('"', "'"):
            _q = _s[0]
            _end = _s.find(_q, 1)
            if _end > 0:
                cand = _s[1:_end]
        if cand is None:
            cand = _s.split(maxsplit=1)[0] if _s else None
        if cand:
            cand = cand.strip('"').strip("'")
    elif isinstance(a, (list, tuple)) and a:
        cand = a[0]
        if cand is None:
            cand = exe
    if cand is None:
        return "", ""
    try:
        full = _norm(cand)
        name = os.path.basename(os.fspath(cand)).lower()
    except Exception:
        return "", ""
    return name, full


def _popen_is_allowed(args):
    """True только для ffmpeg/ffprobe из доверенных каталогов.

    Оба условия одновременно:
      • basename — ffmpeg / ffprobe / ffmpeg-<ver>.exe / ...;
      • realpath лежит под _TRUSTED_FFMPEG_ROOTS.
    shell=True (args — строка) блокируется всегда. Переименованный
    virus.exe в C:\\Temp\\ffmpeg.exe не пройдёт — каталог не доверенный.
    """
    name, full = _popen_target(args)
    if not name or not full:
        return False
    if not _FFMPEG_LIKE_RE.match(name):
        return False
    for root in _TRUSTED_FFMPEG_ROOTS:
        if full == root or full.startswith(root + os.sep):
            return True
    return False


_ROCM_NAME_RE = re.compile(r"^rocm(?:[-_.].*)?$", re.IGNORECASE)


def _is_rocm_probe(name, args):
    """ROCm-пробы наличия инструмента — тихий отказ (без stderr).

    torch/accelerate на Windows проверяют rocm-sdk/rocm-smi даже на
    NVIDIA-машине. Это норма, не нарушение — не шумим. Всё прочее
    блокируется со сообщением как раньше.
    """
    if name and _ROCM_NAME_RE.match(name):
        return True
    try:
        a = args[1] if args and len(args) > 1 else None
        if isinstance(a, (list, tuple)):
            low = " ".join(str(x).lower() for x in a)
            if re.search(r"-m\s+rocm(?:[-_.\s]|$)", low):
                return True
    except Exception:
        pass
    return False


_HINTS = {
    "open": (
        'Для записи используй встроенные команды:\n'
        '   {"переписать": {"путь": "...", "содержание": "..."}}   — перезапись\n'
        '   {"добавить": {"путь": "...", "содержание": "..."}}     — дописать/создать'
    ),
    "remove": (
        'Удаление — через встроенные команды:\n'
        '   {"удалить_файл": {"путь": "..."}}         — конкретный файл\n'
        '   {"удалить_папку": {"путь": "..."}}        — рекурсивно со всем содержимым\n'
        '   {"удалить_путь": {"путь": "..."}}         — сам определит файл/папку\n'
        '   {"удалить_пустые_папки": {"путь": "..."}} — только пустые, рекурсивно'
    ),
    "rmtree": (
        'Удаление папки — через встроенные команды:\n'
        '   {"удалить_папку": {"путь": "..."}}        — рекурсивно со всем содержимым\n'
        '   {"удалить_путь": {"путь": "..."}}         — сам определит файл/папку\n'
        '   {"удалить_пустые_папки": {"путь": "..."}} — только пустые, рекурсивно\n'
        '   {"убраться_в": {"путь": "..."}}           — очистка временных'
    ),
    "rename": (
        'Перемещение/переименование — через встроенную команду:\n'
        '   {"переместить": {"откуда": "...", "куда": "..."}}'
    ),
    "exec": (
        'Запуск внешних процессов — через встроенные команды:\n'
        '   {"запуск": {"команда": "..."}}              — PowerShell с whitelist\n'
        '   {"питон": {"скрипт": "demo.py"}}            — запустить .py-файл\n'
        '   {"питон": {"скрипт": "demo.py", "аргументы": ["a", "b"]}} — то же с аргументами'
    ),
}


def _fail(what, path, hint_key=None, soft=False, silent=False):
    """Заблокировать действие.

    soft=False (по умолчанию): os._exit(3) — жёстко. Для записи/удаления/
      переименования в защищённых папках. Не ловится try/except.

    soft=True: бросить PermissionError+FileNotFoundError — скрипт может
      поймать и продолжить. Для запуска внешних процессов: библиотеки
      часто "проверяют наличие" инструмента и не должны валить весь
      скрипт из-за отсутствия. Реальный запуск всё равно не произойдёт.

    silent=True: не писать в stderr и не выдавать себя в тексте
      исключения. Только вместе с soft=True. Для заведомо-безвредных
      проб наличия AMD-инструмента (rocm-sdk) на NVIDIA-машине.
    """
    _msg = what + (": " + str(path) if path else "")
    if not silent:
        try:
            sys.stderr.write("⛔ питон: " + _msg + " запрещено\n")
            if hint_key and hint_key in _HINTS:
                sys.stderr.write("💡 " + _HINTS[hint_key] + "\n")
            sys.stderr.flush()
        except Exception:
            pass
    if soft:
        # Двойное наследование: библиотеки (triton, vllm) при проверке
        # наличия бинаря ловят FileNotFoundError, а не PermissionError.
        # Наследуемся от обоих — скрипт поймает и пойдёт дальше (как
        # будто бинаря нет), при этом тип остаётся PermissionError.
        class _BlockedExecutableError(FileNotFoundError, PermissionError):
            pass
        if silent:
            # Без ⛔ и "запрещено": даже залогированное исключение выглядит
            # как обычное отсутствие бинаря, а не срабатывание guard.
            raise _BlockedExecutableError(_msg)
        raise _BlockedExecutableError("⛔ " + _msg + " запрещено")
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
            _fail("запись в защищённую папку", path, "open")
    elif event in ("os.remove", "os.unlink", "os.rmdir", "os.mkdir"):
        path = args[0] if args else None
        if _is_blocked(path):
            _fail("изменение защищённой папки", path, "remove")
    elif event == "shutil.rmtree":
        path = args[0] if args else None
        _fail("rmtree", path, "rmtree")
    elif event == "os.rename":
        src = args[0] if len(args) > 0 else None
        dst = args[1] if len(args) > 1 else None
        if _is_blocked(src) or _is_blocked(dst):
            _fail("переименование в защищённой папке",
                  str(src) + " → " + str(dst), "rename")
    elif event == "subprocess.Popen":
        if _popen_is_allowed(args):
            return
        _name, _full = _popen_target(args)
        _silent = _is_rocm_probe(_name, args)  # ROCm-пробы — без шума
        _label = "subprocess.Popen"
        if _name:
            _label += " (" + _name + ")"
        else:
            _raw = ""
            try:
                if len(args) > 1:
                    _r = args[1]
                    if isinstance(_r, (list, tuple)):
                        _raw = " ".join(repr(x) for x in _r)
                    else:
                        _raw = repr(_r)
                if len(_raw) > 240:
                    _raw = _raw[:240] + "..."
            except Exception:
                _raw = "?"
            if _raw:
                _label += " → " + _raw
        _fail(_label, "", "exec", soft=True, silent=_silent)
    elif event in ("os.system", "os.exec"):
        _fail(event, "", "exec", soft=True)


sys.addaudithook(_audit)


def _patch_subprocess():
    import subprocess
    import json

    ffprobe_exe = None
    ffmpeg_exe = None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(os.path.dirname(here), "side", "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        paths = cfg.get("paths") or {}
        ffprobe_exe = paths.get("ffprobe_exe")
        ffmpeg_exe = paths.get("ffmpeg_exe")
    except Exception:
        pass

    def _rewrite_cmd(args):
        if isinstance(args, (list, tuple)) and args:
            head = str(args[0])
            low = head.lower()
            if low in ("ffprobe", "ffprobe.exe") and ffprobe_exe:
                return [ffprobe_exe] + list(args[1:])
            elif low in ("ffmpeg", "ffmpeg.exe") and ffmpeg_exe:
                return [ffmpeg_exe] + list(args[1:])
        elif isinstance(args, str):
            low = args.lower()
            if low.startswith(("ffprobe ", "ffprobe.exe ")) and ffprobe_exe:
                return ffprobe_exe + args[len(args.split(None, 1)[0]):]
            elif low.startswith(("ffmpeg ", "ffmpeg.exe ")) and ffmpeg_exe:
                return ffmpeg_exe + args[len(args.split(None, 1)[0]):]
        return args

    _orig_popen = subprocess.Popen
    # Popen должен быть КЛАССОМ (а не функцией): иначе
    # `class Foo(subprocess.Popen)` падает с TypeError
    # (asyncio.windows_utils, yt_dlp.utils.Popen и др.).
    class _PatchedPopen(_orig_popen):
        def __init__(self, *a, **kw):
            if a:
                a = (_rewrite_cmd(a[0]),) + a[1:]
            elif "args" in kw:
                kw["args"] = _rewrite_cmd(kw["args"])
            if "executable" in kw and isinstance(kw["executable"], str):
                exe = kw["executable"]
                low = exe.lower()
                if low in ("ffprobe", "ffprobe.exe") and ffprobe_exe:
                    kw["executable"] = ffprobe_exe
                elif low in ("ffmpeg", "ffmpeg.exe") and ffmpeg_exe:
                    kw["executable"] = ffmpeg_exe
            super().__init__(*a, **kw)
    subprocess.Popen = _PatchedPopen

    _orig_run = subprocess.run
    def _patched_run(*a, **kw):
        if a:
            a = (_rewrite_cmd(a[0]),) + a[1:]
        elif "args" in kw:
            kw["args"] = _rewrite_cmd(kw["args"])
        return _orig_run(*a, **kw)
    subprocess.run = _patched_run

    _orig_call = subprocess.call
    def _patched_call(*a, **kw):
        if a:
            a = (_rewrite_cmd(a[0]),) + a[1:]
        elif "args" in kw:
            kw["args"] = _rewrite_cmd(kw["args"])
        return _orig_call(*a, **kw)
    subprocess.call = _patched_call

    _orig_check_output = subprocess.check_output
    def _patched_check_output(*a, **kw):
        if a:
            a = (_rewrite_cmd(a[0]),) + a[1:]
        elif "args" in kw:
            kw["args"] = _rewrite_cmd(kw["args"])
        return _orig_check_output(*a, **kw)
    subprocess.check_output = _patched_check_output

    _orig_check_call = subprocess.check_call
    def _patched_check_call(*a, **kw):
        if a:
            a = (_rewrite_cmd(a[0]),) + a[1:]
        elif "args" in kw:
            kw["args"] = _rewrite_cmd(kw["args"])
        return _orig_check_call(*a, **kw)
    subprocess.check_call = _patched_check_call


def _main():
    if len(sys.argv) < 2:
        sys.stderr.write("py_guard: no target script\n")
        sys.exit(2)
    target = sys.argv[1]
    sys.argv = [target] + sys.argv[2:]
    tdir = os.path.dirname(os.path.abspath(target))
    if tdir and tdir not in sys.path:
        sys.path.insert(0, tdir)
    _patch_subprocess()
    import runpy
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit:
        raise
    except BaseException as _e:
        # Чистый traceback: вырезаем кадры самого guard'а и внутренностей
        # runpy. Агент должен видеть только свой скрипт, а не устройство
        # обёртки. Имя guard-файла при обычной ошибке не светится.
        import traceback as _tb
        _self_abs = os.path.abspath(__file__)

        def _keep(fr):
            fn = getattr(fr, "filename", "") or ""
            if fn == _self_abs:
                return False
            if fn.startswith("<frozen runpy"):
                return False
            if fn.endswith("runpy.py"):
                return False
            return True

        _te = _tb.TracebackException.from_exception(_e)
        _te.stack = _tb.StackSummary.from_list(
            [f for f in _te.stack if _keep(f)]
        )
        sys.stderr.write("".join(_te.format()))
        sys.stderr.flush()
        sys.exit(1)


if __name__ == "__main__":
    _main()