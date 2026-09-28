"""Разбор и создание ярлыков Windows (.lnk)."""
import os
import subprocess
import sys
from pathlib import Path

from _actions import register


def _read_lnk(target_path):
    """Читает один .lnk через WScript.Shell. Возвращает dict или None."""
    p = str(target_path).replace("'", "''")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('" + p + "'); "
        "Write-Output $s.TargetPath; "
        "Write-Output $s.Arguments; "
        "Write-Output $s.WorkingDirectory; "
        "Write-Output $s.Description"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    lines = (r.stdout or "").splitlines()
    while len(lines) < 4:
        lines.append("")
    return {
        "target": lines[0].strip(),
        "args": lines[1].strip(),
        "workdir": lines[2].strip(),
        "description": lines[3].strip(),
    }


def _write_lnk(lnk_path, target, arguments="", workdir="", description=""):
    """Создаёт/перезаписывает .lnk. Возвращает (ok, msg)."""
    def _q(s):
        return str(s).replace("'", "''")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('" + _q(lnk_path) + "'); "
        "$s.TargetPath = '" + _q(target) + "'; "
        "$s.Arguments = '" + _q(arguments) + "'; "
        "$s.WorkingDirectory = '" + _q(workdir) + "'; "
        "$s.Description = '" + _q(description) + "'; "
        "$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        return False, str(e)
    if r.returncode != 0:
        return False, ((r.stdout or "") + (r.stderr or "")).strip()[:500]
    return True, ""


def _fmt_one(name, info, show_all=False):
    if info is None:
        return "  " + name + " -> не прочитать"
    lines = ["  " + name + " -> " + (info["target"] or "(пусто)")]
    if info["target"] and not os.path.exists(info["target"]):
        lines[0] += "  цель не существует"
    if show_all:
        if info["args"]:
            lines.append("      аргументы: " + info["args"])
        if info["workdir"]:
            lines.append("      рабочая: " + info["workdir"])
        if info["description"]:
            lines.append("      описание: " + info["description"])
    return chr(10).join(lines)


@register(["куда_ссылается", "разобрать_ярлык", "ярлык"])
def where_shortcut(args):
    """Куда указывает ярлык (.lnk).

    Параметры:
      путь      — путь к .lnk-файлу или папке с ярлыками (обязательный)
      подробно  — true: показать аргументы, рабочую папку, описание

    Примеры:
      {"куда_ссылается": {"путь": "C:/temp/my.lnk"}}
      {"куда_ссылается": {"путь": "E:/Jericho/Private/tools"}}
      {"куда_ссылается": {"путь": "E:/Jericho/Private/tools", "подробно": true}}
    """
    a = args or {}
    path = (a.get("путь") or a.get("файл") or "").strip()
    if not path:
        return "Укажи 'путь'."
    show_all = bool(a.get("подробно"))

    if not os.path.exists(path):
        return "Путь не найден: " + path

    if os.path.isfile(path):
        if not path.lower().endswith(".lnk"):
            return "Это не .lnk: " + path
        info = _read_lnk(path)
        return _fmt_one(os.path.basename(path), info, show_all=show_all)

    found = sorted([f for f in os.listdir(path) if f.lower().endswith(".lnk")])
    if not found:
        return "В папке нет .lnk-ярлыков: " + path
    out = ["Ярлыки в " + path + " — всего " + str(len(found)) + ":"]
    for name in found:
        info = _read_lnk(os.path.join(path, name))
        out.append(_fmt_one(name, info, show_all=show_all))
    return chr(10).join(out)


@register(["перезаписать_ссылку", "создать_ярлык", "обновить_ярлык"])
def rewrite_shortcut(args):
    """Создать или перезаписать ярлык (.lnk).

    Параметры:
      путь      — путь к .lnk-файлу (обязательный);
                  если расширения .lnk нет — добавится
      цель      — куда ведёт ярлык (обязательный); алиасы: target, на
      аргументы — аргументы командной строки (опционально)
      рабочая   — рабочая папка (опционально); алиасы: папка, рабочий_каталог
      описание  — текст описания (опционально)

    Примеры:
      {"перезаписать_ссылку": {"путь": "C:/tmp/gh.lnk", "цель": "C:/app.exe"}}
      {"перезаписать_ссылку": {"путь": "C:/tmp/py.lnk",
                                "цель": "C:/py/main.py",
                                "рабочая": "C:/py"}}
    """
    a = args or {}
    lnk = (a.get("путь") or a.get("файл") or "").strip()
    target = (a.get("цель") or a.get("target") or a.get("на") or "").strip()
    if not lnk:
        return "Укажи 'путь' к .lnk-файлу."
    if not target:
        return "Укажи 'цель' — куда должен вести ярлык."
    if not lnk.lower().endswith(".lnk"):
        lnk = lnk + ".lnk"

    parent = os.path.dirname(lnk)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:
            return "Не создать папку для ярлыка: " + str(e)

    arguments = (a.get("аргументы") or a.get("args") or "").strip()
    workdir = (a.get("рабочая") or a.get("рабочий_каталог")
               or a.get("папка") or a.get("workdir") or "").strip()
    description = (a.get("описание") or a.get("description") or "").strip()

    ok, err = _write_lnk(lnk, target, arguments, workdir, description)
    if not ok:
        return "Не создать ярлык: " + err

    lines = ["Ярлык сохранён: " + lnk, "   ведёт на: " + target]
    if arguments:
        lines.append("   аргументы: " + arguments)
    if workdir:
        lines.append("   рабочая: " + workdir)
    if not os.path.exists(target):
        lines.append("   цель пока не существует (ярлык всё равно сохранён)")
    return chr(10).join(lines)