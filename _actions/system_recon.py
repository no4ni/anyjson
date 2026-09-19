"""Системные команды: телеметрия, запуск, питон, экран, сенсоры."""
import concurrent.futures
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

from _actions import register
from _actions.system_ps import run_ps
from _config import MY_DIR, AGENT_NAME
from _utils import log, truncate_output, fix_powershell_body
from _ps import run_with_wait, run_powershell_with_cwd
from _storage import reminders, reminders_lock, parse_reminder_time, save_reminders


@register(["разведка_системы", "кто_ты"])
def system_recon(args):
    """Собирает сведения о системе. Кроссплатформенно.
    Если передан "команды" — перенаправляет в "запуск" (белый список PS).
    """
    if isinstance(args, dict) and args.get("команды"):
        cmds = args["команды"]
        if isinstance(cmds, str):
            cmds = [cmds]
        out = []
        for c in cmds:
            out.append(f"$ {c}")
            out.append(run_ps({"команда": str(c)}))
            out.append("")
        return "\n".join(out).rstrip()

    if isinstance(args, dict) and args:
        keys = ", ".join(repr(k) for k in args)
        return (f'\u2139\ufe0f "разведка_системы" не принимает параметры ({keys}). '
                f'Запустите без параметров: {{"разведка_системы": {{}}}}. '
                f'Для PowerShell-команд: {{"запуск": {{"команда": "dir"}}}}.')

    import platform

    lines = []
    lines.append(f"ОС:        {platform.system()} {platform.release()} ({platform.version()})")
    lines.append(f"Узел:      {platform.node()}")
    lines.append(f"Арх:       {platform.machine()}")
    lines.append(f"Процессор: {platform.processor() or '—'}")
    lines.append(f"CPU ядер:  {os.cpu_count()} логических")
    lines.append(f"Python:    {sys.version.split()[0]}  ({sys.executable})")

    if platform.system() == "Windows":
        _system_recon_windows(lines)
    else:
        _system_recon_unix(lines)

    return "\n".join(lines)


def _system_recon_windows(lines):
    """Windows: RAM, uptime, диски через CIM (UTF-8 из PowerShell)."""
    def _ps(cmd, timeout=15):
        wrapped = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        ).stdout.strip()

    ps_mem = (
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "'RAM: {0} МБ своб / {1} МБ всего' -f "
        "[math]::Round($os.FreePhysicalMemory/1024), "
        "[math]::Round($os.TotalVisibleMemorySize/1024); "
        "$up = (Get-Date) - $os.LastBootUpTime; "
        "'Uptime:    {0}' -f $up.ToString('d\\.hh\\:mm\\:ss')"
    )
    try:
        out = _ps(ps_mem)
        if out:
            lines.append(out)
    except Exception as e:
        lines.append(f"RAM/Uptime: <нет данных: {e}>")

    ps_disk = (
        "Get-PSDrive -PSProvider FileSystem | "
        "Where-Object {$_.Used -ne $null} | "
        "ForEach-Object { '{0}: занято {1} ГБ, своб {2} ГБ' -f "
        "$_.Name, [math]::Round($_.Used/1GB,1), [math]::Round($_.Free/1GB,1) }"
    )
    try:
        out = _ps(ps_disk)
        if out:
            lines.append("Диски:")
            lines.append(out)
    except Exception as e:
        lines.append(f"Диски:     <нет данных: {e}>")


def _system_recon_unix(lines):
    for cmd, title in ((["free", "-m"], "RAM (free -m)"),
                       (["df", "-h"], "Диски (df -h)")):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=5).stdout.strip()
            if out:
                lines.append(f"{title}:")
                lines.append(out)
        except Exception as e:
            lines.append(f"{title}: <нет данных: {e}>")
