"""Реестр обработчиков команд."""
HANDLERS = {}


def register(names):
    """Декоратор: names — строка или список имён команд."""
    if isinstance(names, str):
        names = [names]

    def deco(func):
        for n in names:
            HANDLERS[n] = func
        return func
    return deco


def load_all():
    """Импортирует все модули пакета, чтобы они зарегистрировались.

    Автодискавери: новый файл _actions/foo.py подхватится сам,
    без правки этого файла.
    """
    import pkgutil as _pk
    import importlib as _il
    import os as _os
    import sys as _sys
    _base = _os.path.dirname(__file__)
    _errors = []
    for _mi in _pk.iter_modules([_base]):
        _name = _mi.name
        if _name.startswith("_"):
            continue
        try:
            _il.import_module(f"_actions.{_name}")
        except Exception as _e:
            _errors.append(f"{_name}: {_e}")
    if _errors:
        for _msg in _errors:
            print(f"[LOAD_ALL] {_msg}", file=_sys.stderr)


# ============================================================
#  горячая перезагрузка _actions/*.py + безопасных _core/*.py
# ============================================================
import pathlib as _pl_init
import sys as _sys_init

_RELOAD_MTIMES = {}
_CORE_RELOAD_MTIMES = {}
_ACTIONS_BASE = _pl_init.Path(__file__).parent
_CORE_BASE = _ACTIONS_BASE.parent / "_core"

# модули _core, которые можно безопасно перезагрузить на лету
_CORE_RELOADABLE = ("_utils", "_color", "_executor", "_ps")

# модули _core, которые НЕ перезагружаем — только предупреждаем
_CORE_WARN_ONLY = {
    "_config", "_storage", "_tor",
    "_safe_subprocess", "_validate_imports", "_web",
}

# атрибуты модулей, которые нужно сохранить при reload
_CORE_PRESERVE = {
    "_ps": ("_ps_cwd",),
}


def _scan_dir(dir_path, skip_underscore=False, skip_dunder_init=False):
    out = {}
    if not dir_path.is_dir():
        return out
    for f in dir_path.glob("*.py"):
        if skip_underscore and f.name.startswith("_"):
            continue
        if skip_dunder_init and f.name == "__init__.py":
            continue
        out[f.name] = f.stat().st_mtime
    return out


_RELOAD_MTIMES.update(_scan_dir(_ACTIONS_BASE, skip_underscore=True))
_CORE_RELOAD_MTIMES.update(_scan_dir(_CORE_BASE, skip_dunder_init=True))


def _reload_core(changed_names):
    """Перезагрузить безопасные _core-модули. Вернуть список предупреждений."""
    import importlib as _il
    warnings = []
    for fname in changed_names:
        mod_name = fname[:-3]
        if mod_name in _CORE_RELOADABLE:
            try:
                mod = _sys_init.modules.get(mod_name)
                if mod is None:
                    continue
                saved = {}
                for _attr in _CORE_PRESERVE.get(mod_name, ()):
                    if hasattr(mod, _attr):
                        saved[_attr] = getattr(mod, _attr)
                _il.reload(mod)
                for _attr, _val in saved.items():
                    setattr(mod, _attr, _val)
            except Exception as e:
                warnings.append(f"{fname}: reload error: {e}")
        elif mod_name in _CORE_WARN_ONLY:
            warnings.append(f"⚠️ {fname} изменён — нужен перезапуск anyjson")
        else:
            warnings.append(f"⚠️ {fname} изменён — перезапусти anyjson вручную")
    return warnings


def reload_if_changed():
    """Перечитать _actions/*.py и безопасные _core/*.py при изменении mtime.

    Возвращает dict {changed, errors, skipped} или None.
    """
    global _RELOAD_MTIMES, _CORE_RELOAD_MTIMES
    try:
        _RELOAD_MTIMES
        _CORE_RELOAD_MTIMES
    except NameError:
        _RELOAD_MTIMES = {}
        _CORE_RELOAD_MTIMES = {}

    import os as _os
    import importlib as _il
    import pkgutil as _pk
    import py_compile as _pc

    actions_base = _os.path.dirname(__file__)
    core_base = _os.path.join(_os.path.dirname(actions_base), "_core")

    cur_actions = {}
    for fname in _os.listdir(actions_base):
        if fname.endswith(".py") and not fname.startswith("_"):
            cur_actions[fname] = _os.path.getmtime(
                _os.path.join(actions_base, fname))

    cur_core = {}
    if _os.path.isdir(core_base):
        for fname in _os.listdir(core_base):
            if fname.endswith(".py") and fname != "__init__.py":
                cur_core[fname] = _os.path.getmtime(
                    _os.path.join(core_base, fname))

    if not _RELOAD_MTIMES and not _CORE_RELOAD_MTIMES:
        _RELOAD_MTIMES.update(cur_actions)
        _CORE_RELOAD_MTIMES.update(cur_core)
        return None

    changed_actions = [f for f, mt in cur_actions.items()
                       if _RELOAD_MTIMES.get(f) != mt]
    changed_core = [f for f, mt in cur_core.items()
                    if _CORE_RELOAD_MTIMES.get(f) != mt]

    if not changed_actions and not changed_core:
        return None

    syn_errors = []
    for fname in changed_actions:
        path = _os.path.join(actions_base, fname)
        try:
            _pc.compile(path, doraise=True)
        except _pc.PyCompileError as e:
            syn_errors.append(f"{fname}: {e}")
    if syn_errors:
        _RELOAD_MTIMES.update(cur_actions)
        _CORE_RELOAD_MTIMES.update(cur_core)
        return {"changed": changed_actions + changed_core,
                "errors": syn_errors, "skipped": True}

    core_warnings = _reload_core(changed_core) if changed_core else []

    saved_sc = None
    if "_actions.system_sensor" in _sys_init.modules:
        saved_sc = getattr(_sys_init.modules["_actions.system_sensor"],
                           "sensor_core", None)

    HANDLERS.clear()
    reload_errors = []
    for modinfo in _pk.iter_modules([actions_base]):
        name = modinfo.name
        if name.startswith("_"):
            continue
        full = f"_actions.{name}"
        try:
            if full in _sys_init.modules:
                _il.reload(_sys_init.modules[full])
            else:
                _il.import_module(full)
        except Exception as e:
            reload_errors.append(f"{name}: {e}")

    _RELOAD_MTIMES.clear()
    _RELOAD_MTIMES.update(cur_actions)
    _CORE_RELOAD_MTIMES.clear()
    _CORE_RELOAD_MTIMES.update(cur_core)

    if saved_sc is not None and "_actions.system_sensor" in _sys_init.modules:
        try:
            _sys_init.modules["_actions.system_sensor"].set_sensor_core(saved_sc)
        except Exception as e:
            reload_errors.append(f"sensor_core restore: {e}")

    return {"changed": changed_actions + changed_core,
            "errors": reload_errors + core_warnings,
            "skipped": False}
