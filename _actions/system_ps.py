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
from _config import MY_DIR, AGENT_NAME
from _utils import log, truncate_output, fix_powershell_body
from _ps import run_with_wait, run_powershell_with_cwd
from _storage import reminders, reminders_lock, parse_reminder_time, save_reminders


def _log_run(command, status, extra=""):
    from datetime import datetime as _dt
    try:
        log_file = MY_DIR / "scripts_runs.log"
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {status} | {command[:300]}"
        if extra:
            line += f" | {str(extra)[:200]}"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _split_ps_links(command):
    """Разбивает PS-команду по |, ;, &&, || — не трогая кавычки и бэктик-эскейпы."""
    parts = []
    buf = []
    i = 0
    n = len(command)
    in_s = in_d = False
    while i < n:
        ch = command[i]
        if in_s:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and command[i + 1] == "'":
                    buf.append(command[i + 1])
                    i += 2
                    continue
                in_s = False
            i += 1
            continue
        if in_d:
            buf.append(ch)
            if ch == '`' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == '"':
                in_d = False
            i += 1
            continue
        if ch == '`' and i + 1 < n:
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue
        if ch == "'":
            in_s = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_d = True
            buf.append(ch)
            i += 1
            continue
        if ch == '|' and i + 1 < n and command[i + 1] == '|':
            parts.append(''.join(buf))
            buf = []
            i += 2
            continue
        if ch in '|;':
            parts.append(''.join(buf))
            buf = []
            i += 1
            continue
        if ch == '&' and i + 1 < n and command[i + 1] == '&':
            parts.append(''.join(buf))
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append(''.join(buf))
    return [x.strip() for x in parts if x.strip()]


@register(["запуск", "терминал", "шелл"])
def run_ps(args):
    """PowerShell с БЕЛЫМ списком. Разрешено только чтение."""
    command = args.get("команда", "").strip()
    if not command:
        return "Пустая команда."

    if re.search(r'(^|\s|/|\\)(python|py)(\.exe)?(\s|$)', command, re.IGNORECASE):
        _log_run(command, "BLOCKED", "python-in-ps")
        return ("⛔ Запуск Python через PowerShell запрещён. "
                "Используйте 'питон'.")

    ALLOWED = {
        "dir", "get-childitem", "ls", "gci",
        "type", "get-content", "cat", "gc",
        "test-path", "select-string", "findstr",
        "echo", "write-output", "write-host",
        "get-process", "get-service", "get-date",
        "get-location", "get-psdrive", "get-disk",
        "get-volume", "get-item", "get-itemproperty",
        "ipconfig", "ping", "nslookup", "tracert", "netstat",
        "where.exe", "where", "hostname", "whoami", "systeminfo",
        # смена рабочей папки (сохраняется между вызовами запуска)
        "cd", "set-location", "sl", "push-location", "pop-location",
        # git — только читающие подкоманды (проверяются ниже)
        "git",
        # --- конвейерные (только трансформация данных, без записи) ---
        "sort-object", "sort", "select-object", "select",
        "where-object", "foreach-object", "foreach",
        "format-table", "format-list", "format-wide", "ft", "fl", "fw",
        "measure-object", "measure", "group-object", "group",
        "out-string", "out-host", "tee-object",
        "convertto-json", "convertfrom-json", "convertto-csv",
        "convertfrom-csv", "convertto-html",
        "get-unique", "compare-object", "join-string",
        "split-path", "resolve-path", "convert-path",
        "get-member", "get-command", "get-help",
        # --- WMI/CIM (чтение объектов; запись идёт через Invoke-CimMethod — он НЕ разрешён) ---
        "get-ciminstance", "gcim",
        "get-wmiobject", "gwmi",
    }

    # Дополнительная проверка для git: только безопасные подкоманды
    _git_safe = {
        "status", "log", "diff", "show", "branch", "remote", "tag",
        "ls-files", "rev-parse", "config", "describe", "blame",
        "shortlog", "reflog", "whatchanged", "cat-file", "ls-tree",
        "for-each-ref", "show-ref", "rev-list", "name-rev",
    }
    # git lfs — отдельный whitelist подкоманд
    _git_lfs_safe = {
        "ls-files", "env", "status", "logs", "version", "help",
    }
    _git_parts = command.split()
    for _i, _tok in enumerate(_git_parts):
        if _tok.lower() in ("git", "git.exe"):
            if _i + 1 >= len(_git_parts):
                return "⚠️ git без подкоманды."
            _sub = _git_parts[_i + 1].lower().lstrip("-")
            # git lfs <подкоманда>
            if _sub == "lfs":
                if _i + 2 >= len(_git_parts):
                    return "⚠️ git lfs без подкоманды."
                _lfs = _git_parts[_i + 2].lower().lstrip("-")
                if _lfs not in _git_lfs_safe:
                    return (f"⛔ git lfs {_lfs} запрещён (только чтение). "
                            f"Разрешены: {', '.join(sorted(_git_lfs_safe))}")
                break
            if _sub not in _git_safe:
                return (f"⛔ git {_sub} запрещён (только чтение). "
                        f"Разрешены: {', '.join(sorted(_git_safe))}")
            break

    links = _split_ps_links(command)
    for link in links:
        link = link.strip()
        if not link:
            continue
        first = re.split(r'\s+', link, maxsplit=1)[0].lower()
        # срезаем ведущие группирующие символы: (, @(, $(, &, ., -,
        first = first.lstrip('(\'\"&@$.')
        first = first.strip('"\'')
        base = first.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
        # убираем хвостовую пунктуацию: ), ], },
        base = base.rstrip(')]},:;')
        if base not in ALLOWED:
            _log_run(command, "BLOCKED", f"not-in-whitelist:{base}")
            # подсказка: похожая команда + 3 случайные из реестра
            import random as _rnd
            from _actions import HANDLERS
            guess = ""
            extra_hint = ""
            base_low = base.lower().replace("-", "_")
            for _alias, _target in (
                ("remove_item", "удалить_файл"),
                ("rm", "удалить_файл"),
                ("del", "удалить_файл"),
                ("erase", "удалить_файл"),
                ("rmdir", "удалить_папку"),
                ("copy_item", "переместить"),
                ("move_item", "переместить"),
                ("move", "переместить"),
                ("new_item", "добавить"),
                ("set_content", "переписать"),
                ("attrib", "снять_только_чтение"),
            ):
                if base_low == _alias or _alias in base_low:
                    guess = _target
                    break
            if base_low == "attrib":
                extra_hint = " (или поставить_только_чтение, если нужно добавить флаг)"
            import re as _re_hint
            _ru_name_re = _re_hint.compile(r"^[а-яА-ЯёЁ0-9_]+$")
            names = [n for n in HANDLERS if n and _ru_name_re.match(n) and n != guess]
            picks = _rnd.sample(names, min(3, len(names))) if names else []
            tail = "\n".join(f'  • "{n}"' for n in picks) if picks else "  • (нет доступных команд)"
            if guess == "снять_только_чтение" and extra_hint:
                head = f'Возможно, вы имели ввиду команду "снять_только_чтение"{extra_hint}'
            elif guess:
                head = f'Возможно, вы имели ввиду команду "{guess}".'
            else:
                head = (f'Команда \"{base}\" недоступна через \"запуск\". '
                        f'Попробуй через встроенные команды.')
            return (f"⚠️ {head}\n"
                    f"Другие команды:\n{tail}")

    command = fix_powershell_body(command)

    # Автозамена cmd-style "&" (разделитель) на PowerShell ";".
    # Не трогаем "&&", не трогаем внутри кавычек (одинарных и двойных).
    _out = []
    _in_s = _in_d = False
    _i = 0
    _n = len(command)
    while _i < _n:
        _ch = command[_i]
        if _in_s:
            _out.append(_ch)
            if _ch == "'":
                if _i + 1 < _n and command[_i + 1] == "'":
                    _out.append(command[_i + 1]); _i += 2; continue
                _in_s = False
            _i += 1
            continue
        if _in_d:
            _out.append(_ch)
            if _ch == "`" and _i + 1 < _n:
                _out.append(command[_i + 1]); _i += 2; continue
            if _ch == '"':
                _in_d = False
            _i += 1
            continue
        if _ch == "'":
            _in_s = True; _out.append(_ch); _i += 1; continue
        if _ch == '"':
            _in_d = True; _out.append(_ch); _i += 1; continue
        if _ch == "&":
            # &&  — оставляем как есть
            if _i + 1 < _n and command[_i + 1] == "&":
                _out.append("&&"); _i += 2; continue
            # одиночный &  -> ;
            _out.append(";"); _i += 1; continue
        _out.append(_ch); _i += 1
    command = "".join(_out)

    result = run_with_wait(run_powershell_with_cwd, (command,), timeout=45)
    _log_run(command, "OK", (result or "")[:200])
    return result

