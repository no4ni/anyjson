"""Глобальная защита от запуска чужих .py через subprocess.

Разрешённые корни:
  * BASE_DIR  (вся папка anyjson; включает profiles/ и side/external/)

Исключение — код внутри контекста allow_external_py().

Устанавливается один раз через install_guard() при старте anyjson.
"""
import contextvars
import subprocess
import sys
from pathlib import Path

from _config import BASE_DIR

_ALLOW_EXTERNAL_PY = contextvars.ContextVar(
    "anyjson_allow_external_py", default=False
)


def _safe_resolve(p):
    try:
        return Path(p).resolve()
    except Exception:
        return None


_rp = _safe_resolve(BASE_DIR)
ALLOWED_PY_ROOTS = (_rp,) if _rp else ()

_PY_EXE_NAMES = {
    "python", "python.exe", "python3", "python3.exe",
    "py", "py.exe", "pyw", "pyw.exe",
}

import sys as _sys
# флаг вне модуля, чтобы переживал importlib.reload
_installed = _sys.modules.get('_safe_subprocess_installed', False)


class ExternalPyBlocked(PermissionError):
    """Попытка запустить .py вне разрешённых папок."""


class _AllowCtx:
    def __enter__(self):
        self._tok = _ALLOW_EXTERNAL_PY.set(True)
        return self

    def __exit__(self, *exc):
        _ALLOW_EXTERNAL_PY.reset(self._tok)
        return False


def allow_external_py():
    """with allow_external_py(): ... — разрешить любой .py."""
    return _AllowCtx()


def _looks_like_python(cmd0):
    return Path(str(cmd0)).name.lower() in _PY_EXE_NAMES


def _extract_py_target(cmd):
    if isinstance(cmd, (str, bytes)):
        return None
    if not cmd:
        return None
    if not _looks_like_python(cmd[0]):
        return None
    for arg in cmd[1:]:
        a = str(arg)
        if a in ("-c", "-m"):
            return None
        if a.startswith("-"):
            continue
        if a.lower().endswith(".py"):
            return Path(a)
        return None
    return None


def _check(cmd):
    if _ALLOW_EXTERNAL_PY.get():
        return
    target = _extract_py_target(cmd)
    if target is None:
        return
    rp = _safe_resolve(target)
    if rp is None:
        return
    if any(rp.is_relative_to(r) for r in ALLOWED_PY_ROOTS):
        return
    raise ExternalPyBlocked(
        f"запуск внешнего .py запрещён: {rp}\n"
        f"разрешено только внутри:\n"
        + "\n".join(f"  - {r}" for r in ALLOWED_PY_ROOTS)
        + "\n"
        "Если это команда \"питон\" — оберни вызов в "
        "_safe_subprocess.allow_external_py()."
    )


def install_guard():
    global _installed
    if _installed:
        return
    _installed = True

    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen
    _orig_call = subprocess.call
    _orig_check_output = subprocess.check_output
    _orig_check_call = subprocess.check_call

    def run(cmd, *a, **kw):
        _check(cmd); return _orig_run(cmd, *a, **kw)

    # Popen должен быть КЛАССОМ — иначе `class Foo(subprocess.Popen)` падает
    # с TypeError: function() argument 'code' must be code, not str
    # (так делает, например, yt_dlp.utils.Popen).
    class Popen(_orig_popen):
        def __init__(self, cmd, *a, **kw):
            _check(cmd)
            super().__init__(cmd, *a, **kw)

    def call(cmd, *a, **kw):
        _check(cmd); return _orig_call(cmd, *a, **kw)

    def check_output(cmd, *a, **kw):
        _check(cmd); return _orig_check_output(cmd, *a, **kw)

    def check_call(cmd, *a, **kw):
        _check(cmd); return _orig_check_call(cmd, *a, **kw)

    subprocess.run = run
    subprocess.Popen = Popen
    subprocess.call = call
    subprocess.check_output = check_output
    subprocess.check_call = check_call
    _sys.modules['_safe_subprocess_installed'] = True
    print(
        "[SAFE-SUBPROCESS] guard установлен; разрешено: "
        + ", ".join(str(r) for r in ALLOWED_PY_ROOTS),
        file=sys.stderr,
    )
