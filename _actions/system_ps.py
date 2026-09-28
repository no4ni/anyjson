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
    """Разбивает PS-команду по |, ;, &&, || — не трогая кавычки, бэктик-эскейпы
    и вложенные скобки { } ( ) [ ]."""
    parts = []
    buf = []
    i = 0
    n = len(command)
    in_s = in_d = False
    depth_b = depth_p = depth_s = 0   # { }, ( ), [ ]
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

        # Обновляем глубину скобок
        if ch == '{':
            depth_b += 1
        elif ch == '}':
            if depth_b > 0:
                depth_b -= 1
        elif ch == '(':
            depth_p += 1
        elif ch == ')':
            if depth_p > 0:
                depth_p -= 1
        elif ch == '[':
            depth_s += 1
        elif ch == ']':
            if depth_s > 0:
                depth_s -= 1

        # Режем только на верхнем уровне (вне скобок)
        _inside = (depth_b > 0) or (depth_p > 0) or (depth_s > 0)
        if not _inside:
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
    """PowerShell с БЕЛЫМ списком.

    Чтение файлов — через встроенные команды (читать, читать_по_кб,
    прочитать_полностью). Разрешены только навигация, процессы,
    сеть (ipconfig/ping/nslookup), git и rclone (read-only).

    Параметры:
      команда    — PowerShell-команда из белого списка
      таймаут    — секунд (мин/по умолч. 45, макс 3600)
      тишина     — секунд без вывода до убийства
                   (по умолч. max(30, min(таймаут/2, 300)))
    """
    command = args.get("команда", "").strip()
    if not command:
        return "Пустая команда."

    if re.search(r'(^|\s|/|\\)(python|py)(\.exe)?(\s|$)', command, re.IGNORECASE):
        _log_run(command, "BLOCKED", "python-in-ps")
        return ("⛔ Запуск Python через PowerShell запрещён. "
                "Используйте 'питон'.")

    ALLOWED = {
        "dir", "get-childitem", "ls", "gci",
        "test-path", "select-string", "findstr",
        "echo", "write-output", "write-host",
        "get-process", "get-service", "get-date",
        "get-location", "get-psdrive", "get-disk",
        "get-volume", "get-item", "get-itemproperty",
        "ipconfig", "ping", "nslookup", "tracert", "netstat",
        "where.exe", "where", "hostname", "whoami", "systeminfo",
        # смена рабочей папки (сохраняется между вызовами запуска)
        "cd", "set-location", "sl", "push-location", "pop-location",
        # git, rclone — только читающие подкоманды (проверяются ниже)
        "git", "rclone",
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
        # MEGAcmd — только безобидное (версия, справка).
        # Никаких mega-whoami/ls/pwd/du/find/mount: утечка персональных данных.
        "mega-version", "mega-help",
    }

    # Дополнительная проверка для git: только безопасные подкоманды
    _git_safe = {
        "status", "log", "diff", "show", "branch", "remote", "tag",
        "ls-files", "rev-parse", "config", "describe", "blame",
        "shortlog", "reflog", "whatchanged", "cat-file", "ls-tree",
        "for-each-ref", "show-ref", "rev-list", "name-rev",
        "count-objects", "verify-pack", "fsck", "grep", "cherry",
        "merge-base", "symbolic-ref", "var", "notes", "worktree",
        "stash", "bisect", "gc", "prune", "repack", "pack-refs",
        "pack-objects", "unpack-objects", "index-pack",
        "maintenance", "commit-graph", "multi-pack-index",
    }
    # git lfs — отдельный whitelist подкоманд
    _git_lfs_safe = {
        "ls-files", "env", "status", "logs", "version", "help",
    }
    # rclone — только читающие подкоманды
    _rclone_safe = {
        "size", "ls", "lsd", "lsl", "lsf", "lsjson", "lsdirs",
        "about", "check", "md5sum", "sha1sum", "sha256sum", "hashsum",
        "version", "listremotes", "tree", "backend",
    }
    # Разбиваем по |, ;, && — проверяем только ПЕРВЫЙ токен каждого сегмента.
    # Так "Get-Command rclone ..." не считается вызовом rclone.
    _segments = _split_ps_links(command)
    for _seg in _segments:
        _parts = _seg.split()
        if not _parts:
            continue
        _head = _parts[0].lower().lstrip('(\'"&@$.')
        _head = _head.rsplit('\\', 1)[-1].rsplit('/', 1)[-1].rstrip(')]},:;')

        if _head in ("git", "git.exe"):
            if len(_parts) < 2:
                return "⚠️ git без подкоманды."
            _sub = _parts[1].lower().lstrip("-")
            if _sub == "lfs":
                if len(_parts) < 3:
                    return "⚠️ git lfs без подкоманды."
                _lfs = _parts[2].lower().lstrip("-")
                if _lfs not in _git_lfs_safe:
                    return (f"⛔ git lfs {_lfs} запрещён (только чтение). "
                            f"Разрешены: {', '.join(sorted(_git_lfs_safe))}")
                continue
            if _sub not in _git_safe:
                return (f"⛔ git {_sub} запрещён (только чтение). "
                        f"Разрешены: {', '.join(sorted(_git_safe))}")
            continue

        if _head in ("rclone", "rclone.exe"):
            if len(_parts) < 2:
                return "⚠️ rclone без подкоманды."
            _sub = _parts[1].lower().lstrip("-")
            if _sub == "config":
                return ("⛔ rclone config запрещён: содержит секреты "
                        "(user, pass, session_id, master_key).")
            if _sub not in _rclone_safe:
                return (f"⛔ rclone {_sub} запрещён (только чтение). "
                        f"Разрешены: {', '.join(sorted(_rclone_safe))}")
            continue

    # dir/gci + Length — это анализ размеров, отсылаем на анализ_папки/список_файлов
    _cmd_early = command.lower()
    if ("length" in _cmd_early or "size" in _cmd_early):
        _head = _cmd_early.split(None, 1)[0].rstrip('(') if _cmd_early else ""
        if _head in ("dir", "get-childitem", "gci", "ls",
                     "(dir", "(get-childitem", "(gci"):
            _log_run(command, "BLOCKED", "use-analiz-papki")
            return (
                "⚠️ Похоже, это анализ размеров папки.\n"
                "Используй встроенные команды:\n"
                '  {"анализ_папки": {"путь": "D:/"}}\n'
                '  {"список_файлов": {"путь": "D:/"}}\n'
                '  {"найти_файл": {"путь": "D:/", "маска": "*.7z"}}'
            )

    links = _split_ps_links(command)
    _SAFE_VARS = {
        "psversiontable", "pshome", "psedition",
        "psculture", "psuiculture",
    }
    for link in links:
        link = link.strip()
        if not link:
            continue
        # берём первый непустой токен (пропускаем ведущий & / & "$env:...")
        base = ""
        _base_raw = ""
        for _t in link.split():
            _base_raw = _t
            _tl = _t.lower().lstrip('(\'"&@$.').strip('"\'')
            _tl = _tl.rsplit('\\', 1)[-1].rsplit('/', 1)[-1].rstrip(')]},:;')
            if _tl:
                base = _tl
                break
        # Автоматические переменные PowerShell ($PSVersionTable, $PSHOME, ...).
        # Свойства и методы (.PSVersion.ToString()) не мешают — берём
        # только имя до первой точки / скобки / двоеточия.
        # $env:* НЕ пропускаем: утечка произвольных переменных окружения.
        if _base_raw.startswith('$'):
            _var = _base_raw[1:].split('.', 1)[0].split('(', 1)[0].split(':', 1)[0].lower()
            if _var in _SAFE_VARS:
                continue
        if base not in ALLOWED:
            _log_run(command, "BLOCKED", f"not-in-whitelist:{base}")
            # подсказка: похожая команда + 3 случайные из реестра
            import random as _rnd
            from _actions import HANDLERS
            guess = ""
            extra_hint = ""
            base_low = base.lower().replace("-", "_")

            # New-Item / mkdir / md / ni — от контекста зависит
            _dir_markers = ("-itemtype directory", "-itemtype dir",
                            "-type directory", "-type dir")
            _cmd_low = command.lower()
            _is_dir_create = any(m in _cmd_low for m in _dir_markers)
            if base_low in ("type", "get_content", "getcontent",
                            "cat", "gc"):
                guess = "читать"
            elif base_low in ("new_item", "ni", "mkdir", "md"):
                if _is_dir_create or base_low in ("mkdir", "md"):
                    guess = "создать_папку"
                else:
                    guess = "добавить"
            else:
                for _alias, _target in (
                    ("remove_item", "удалить_файл"),
                    ("rm", "удалить_файл"),
                    ("del", "удалить_файл"),
                    ("erase", "удалить_файл"),
                    ("rmdir", "удалить_папку"),
                    ("copy_item", "переместить"),
                    ("move_item", "переместить"),
                    ("move", "переместить"),
                    ("set_content", "переписать"),
                    ("attrib", "снять_только_чтение"),
                ):
                    if base_low == _alias or _alias in base_low:
                        guess = _target
                        break
            if base_low == "attrib":
                extra_hint = " (или поставить_только_чтение, если нужно добавить флаг)"

            # архивы — на архив_список
            if base_low in ("7z", "7za", "7z.exe",
                            "rar", "unrar",
                            "unzip", "zipinfo",
                            "tar", "gzip", "bzip2", "xz",
                            "zstd", "cabextract"):
                guess = "архив_список"

            # dir/gci + сортировка по Length — это анализ_папки
            _cmd_low2 = command.lower()
            if (_cmd_low2.startswith("dir") or _cmd_low2.startswith("get-childitem")
                    or _cmd_low2.startswith("gci")) and (
                    "length" in _cmd_low2 or "size" in _cmd_low2):
                guess = "анализ_папки"
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
            # 2>&1, 1>&2 и т.п. — часть редиректа, НЕ трогаем
            _prev = _out[-1] if _out else ""
            _next = command[_i + 1] if _i + 1 < _n else ""
            if _prev == ">" or _next.isdigit():
                _out.append("&"); _i += 1; continue
            # ведущий & (call operator) — оставляем как есть
            if not _prev or _prev in ";|":
                _out.append("&"); _i += 1; continue
            # одиночный &  -> ;
            _out.append(";"); _i += 1; continue
        _out.append(_ch); _i += 1
    command = "".join(_out)

    # Таймаут: из args["таймаут"] (по умолч. 30, макс 3600).
    # Тишина: min(таймаут/2, 300); отключается "без_тишины": true.
    try:
        _user_timeout = int(args.get("таймаут") or 0)
    except (ValueError, TypeError):
        _user_timeout = 0
    if _user_timeout <= 0:
        _user_timeout = 45
    _user_timeout = max(45, min(_user_timeout, 3600))

    # Тишина: из args["тишина"], иначе min(таймаут/2, 300), не меньше 30
    try:
        _user_silence = float(args.get("тишина") or 0)
    except (ValueError, TypeError):
        _user_silence = 0
    if _user_silence > 0:
        _silence = max(10.0, min(_user_silence, 3600.0))
    else:
        _silence = max(30.0, min(_user_timeout / 2.0, 300.0))

    result = run_with_wait(
        run_powershell_with_cwd,
        (command, _user_timeout, _silence),
        timeout=_user_timeout + 15,
    )
    _log_run(command, "OK", (result or "")[:200])
    return result

