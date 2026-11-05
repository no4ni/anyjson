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
            if ch in '|;\n':
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


_PS_WRAPPERS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}


def _strip_ps_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        q = s[0]
        inner = s[1:-1]
        if q == '"':
            inner = inner.replace('\\"', '"').replace('`"', '"')
        else:
            inner = inner.replace("''", "'")
        return inner
    return s


def _unwrap_powershell(command: str):
    """Снимает обёртку `powershell [-flags] -c "..."` / `pwsh ...`.

    Возвращает (тело, распознана_ли_обёртка):
      (None, True)   — обёртка распознана, но небезопасна (блок);
      (тело, True)   — обёртка распознана, тело извлечено;
      (команда, False) — обёртки нет, всё как было.
    """
    s = command.strip()
    if not s:
        return command, False
    parts = s.split(None, 1)
    head = parts[0].lower().rstrip(",;")
    head = head.rsplit("\\\\", 1)[-1].rsplit("/", 1)[-1]
    if head not in _PS_WRAPPERS:
        return command, False
    rest = parts[1] if len(parts) > 1 else ""

    no_value = {
        "-noprofile", "-nologo", "-noninteractive", "-sta", "-mta",
        "-noexit", "-login", "-noprofileloadtime",
    }
    with_value = {
        "-executionpolicy", "-windowstyle", "-version",
        "-inputformat", "-outputformat", "-workingdirectory",
        "-settingsfile", "-configurationname", "-psconsolefile",
        "-custompipename",
    }
    command_flags = {"-command", "-c", "-commandwithargs"}
    file_flags = {"-file", "-f"}
    encoded_flags = {"-encodedcommand", "-e", "-ec", "-en"}

    i = 0
    n = len(rest)
    while i < n:
        while i < n and rest[i] in " \\t":
            i += 1
        if i >= n:
            break
        j = i
        while j < n and rest[j] not in " \\t":
            j += 1
        tok = rest[i:j]
        tok_l = tok.lower().rstrip(",;")
        if tok_l in file_flags or tok_l in encoded_flags:
            return None, True
        if tok_l in command_flags:
            body = rest[j:].strip()
            if not body:
                return None, True
            body = _strip_ps_quotes(body)
            if not body:
                return None, True
            return body, True
        if tok_l in no_value:
            i = j
            continue
        if tok_l in with_value:
            k = j
            while k < n and rest[k] in " \\t":
                k += 1
            while k < n and rest[k] not in " \\t":
                k += 1
            i = k
            continue
        return None, True
    return None, True


@register(["запуск", "запустить", "терминал", "шелл"])
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

    # Обёртка `powershell -NoProfile -c "..."` / `pwsh ...` — снимаем:
    # «запуск» и так PowerShell, но людям привычнее писать с обёрткой.
    # Внутренняя команда ниже проверяется тем же whitelist'ом.
    # -File / -EncodedCommand / без -c — блок: скрипт не проверить.
    _orig_command = command
    command, _wrapped = _unwrap_powershell(command)
    if _wrapped:
        if command is None:
            _log_run(_orig_command, "BLOCKED", "ps-wrapper-unsafe")
            return ("⛔ Обёртка powershell с -File/-EncodedCommand или без -c "
                    "запрещена: внутреннюю команду нельзя проверить whitelist'ом. "
                    "Передай команду прямо, без обёртки: "
                    '{"запуск": {"команда": "Get-ChildItem ..."}}')
        command = command.strip()
        if not command:
            return "Пустая команда после снятия обёртки powershell."

    if re.search(r'(^|\s|/|\\)(python\d*|py\d*|питон)(\.exe)?(\s|$)', command, re.IGNORECASE):
        _log_run(command, "BLOCKED", "python-in-ps")
        # Собираем готовый {"питон": {...}} из самой команды (если получится).
        _py_example = ""
        try:
            import shlex as _shlex_py
            _toks = _shlex_py.split(command, posix=False)
            if _toks:
                _toks = _toks[1:]
            while _toks and _toks[0].startswith("-"):
                _toks = _toks[1:]
            if _toks:
                _script = _toks[0].strip('"\'')
                _args = [t.strip('"\'') for t in _toks[1:]]
                import json as _json_py
                _obj = {"питон": {"скрипт": _script}}
                if _args:
                    _obj["питон"]["аргументы"] = _args
                _py_example = ("\n   Готовый JSON для ввода в anyjson:\n     "
                               + _json_py.dumps(_obj, ensure_ascii=False))
        except Exception:
            pass
        return ("⛔ Запуск Python через PowerShell запрещён.\n"
                "   «питон» — это НЕ PowerShell-команда, а встроенная JSON-команда anyjson.\n"
                "   Не пиши её в поле «команда» — PowerShell такого файла не найдёт.\n"
                "   Формат: {\"питон\": {\"скрипт\": \"путь.py\", \"аргументы\": [\"arg1\", \"arg2\"]}}\n"
                "   Параметры: скрипт, аргументы (список), таймаут, в_файл."
                + _py_example)

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
        # ffprobe, ffmpeg — пути резолвятся через config.paths (защита от PATH).
        "ffprobe", "ffprobe.exe",
        "ffmpeg", "ffmpeg.exe",
        "nvidia-smi", "nvidia-smi.exe",
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
        # `$var = RHS` — присваивание. Проверяем RHS, а не $var.
        # Литерал (строка, число, @(...), @{...}, $true/$false/$null,
        # голая ссылка $other) — не команда, пропускаем.
        _m_assign = re.match(r'^\$[\w:]+\s*=\s*(.+)$', link, re.DOTALL)
        if _m_assign:
            _rhs = _m_assign.group(1).strip()
            if (not _rhs
                    or re.match(r"^[\"']", _rhs)
                    or re.match(r"^-?\d", _rhs)
                    or re.match(r"^\$(true|false|null)\b", _rhs, re.I)
                    or re.match(r"^@[\(\{]", _rhs)
                    or re.match(r"^\$[\w:]+\s*$", _rhs)):
                continue
            link = _rhs
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
            # cmd.exe-встроенные (for, if, set, call...) — PowerShell их не знает
            _CMD_BUILTINS = {"for", "if", "goto", "call", "set", "shift",
                             "exit", "pause", "start", "title", "chcp", "cls"}
            _bl = base.lower().replace("-", "_")
            if _bl in _CMD_BUILTINS:
                _cl_check = command.lower()
                if _bl == "for" and ("%~z" in _cl_check or " in (" in _cl_check):
                    _hint = ('💡 Получить размер файла в PowerShell:\n'
                             '   (Get-Item "D:\\path\\file.mp4").Length')
                elif _bl == "for":
                    _hint = ('💡 for — синтаксис cmd.exe. В PowerShell:\n'
                             '   foreach ($x in $list) { ... }\n'
                             '   $list | ForEach-Object { ... }')
                elif _bl == "if":
                    _hint = ('💡 if — синтаксис cmd.exe. В PowerShell:\n'
                             '   if (Test-Path "файл") { ... }\n'
                             '   if ($a -eq $b) { ... }')
                elif _bl == "set":
                    _hint = ('💡 set — синтаксис cmd.exe. В PowerShell:\n'
                             '   $var = "value"\n'
                             '   $env:VAR = "value"')
                elif _bl == "start":
                    _hint = ('💡 start — синтаксис cmd.exe. В PowerShell:\n'
                             '   Start-Process "программа"')
                elif _bl == "call":
                    _hint = ('💡 call — синтаксис cmd.exe. В PowerShell:\n'
                             '   & "script.ps1"')
                else:
                    _hint = ('💡 Это встроенная команда cmd.exe. '
                             'В PowerShell используй соответствующий командлет.')
                return f"⛔ Команда \"{base}\" — синтаксис cmd.exe, а не PowerShell.\n{_hint}"

            # New-Object: COM/.NET — почти всегда есть штатная команда
            if base.lower().replace("-", "_") == "new_object":
                _cl = command.lower()
                _hints = []
                if any(k in _cl for k in ("wscript.shell", "createshortcut", ".lnk")):
                    _hints.append('ярлык .lnk → {"создать_ярлык": {...}}'
                                  ' (справка: {"справка": "создать_ярлык"})')
                if any(k in _cl for k in ("net.webclient", "downloadstring",
                                          "downloadfile", "invoke-webrequest")):
                    _hints.append('скачать файл → '
                                  '{"скачать": {"url": "https://...", '
                                  '"сохранить_как": "E:/file.bin"}}')
                if any(k in _cl for k in ("speechsynthesizer", "spvoice",
                                          "sapi.spvoice", "system.speech")):
                    _hints.append('озвучить текст → '
                                  '{"озвучить": {"текст": "Привет"}}')
                if _hints:
                    return ("⚠️ Команда \"new-object\" недоступна через "
                            "\"запуск\" (не в белом списке).\n"
                            "Похоже, через COM/.NET ты хочешь одно из этого — "
                            "есть встроенные команды:\n  • "
                            + "\n  • ".join(_hints)
                            + "\nЕсли ничего не подошло — уточни у оператора "
                              'или посмотри {"список_команд": {}}.')

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
            extra_cmds = ""
            if base_low in ("type", "get_content", "getcontent",
                            "cat", "gc"):
                guess = "читать"
            elif base_low in ("new_item", "ni", "mkdir", "md"):
                if _is_dir_create or base_low in ("mkdir", "md"):
                    guess = "создать_папку"
                else:
                    guess = "добавить"
            elif base_low in ("cmd", "cmd.exe"):
                # cmd /c del ... — подсказать встроенные аналоги
                _cmd_heuristics = [
                    (("del ", "erase ", "del/", "erase/"), "удалить_файл",
                     '{"удалить_файл": {"путь": "E:/..."}}'),
                    (("rmdir ", "rd ", "rmdir/", "rd/"), "удалить_папку",
                     '{"удалить_папку": {"путь": "E:/..."}}'),
                    (("move ",), "переместить",
                     '{"переместить": {"откуда": "E:/a", "куда": "E:/b"}}'),
                    (("copy ", "xcopy ", "robocopy "), "копировать",
                     '{"копировать": {"откуда": "E:/a", "куда": "E:/b"}}'),
                    (("mkdir ", "md "), "создать_папку",
                     '{"создать_папку": {"путь": "E:/new"}}'),
                    (("type ",), "читать",
                     '{"читать": {"путь": "E:/file.txt"}}'),
                    (("dir ", "dir/"), "список_файлов",
                     '{"список_файлов": {"путь": "E:/"}}'),
                ]
                for _keys, _name, _example in _cmd_heuristics:
                    if any(k in _cmd_low for k in _keys):
                        guess = _name
                        extra_cmds = (
                            '\n\n💡 Команда cmd → встроенная команда:\n  '
                            + _example
                            + '\nСправка: {"справка": "' + _name + '"}'
                        )
                        break
            else:
                for _alias, _target in (
                    ("remove_item", "удалить_файл"),
                    ("rm", "удалить_файл"),
                    ("del", "удалить_файл"),
                    ("erase", "удалить_файл"),
                    ("rmdir", "удалить_папку"),
                    ("copy_item", "копировать"),
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
                _CMD_ONLY = {"del", "erase", "copy", "move", "mkdir", "md",
                             "rmdir", "rd", "ren", "rename", "type", "xcopy",
                             "robocopy", "more", "tree", "attrib", "subst",
                             "assoc", "ftype", "fc", "comp"}
                if base.lower() in _CMD_ONLY:
                    head = (f'Команда \"{base}\" недоступна через \"запуск\" (это PowerShell, а не cmd.exe). '
                            f'Попробуй через встроенные команды.')
                else:
                    _py_hint = '{"питон": {"скрипт": "demo.py", "аргументы": ["arg1"]}}'
                    head = (f'Команда \"{base}\" недоступна через \"запуск\" (не в белом списке). '
                            f'Используй встроенные команды или {_py_hint}.')
            return (f"⚠️ {head}\n"
                    f"Другие команды:\n{tail}{extra_cmds}")

    # Резолв ffmpeg/ffprobe через config.paths (защита от PATH-подмены).
    try:
        from _config import ffmpeg_exe as _ffm, ffprobe_exe as _ffp
        if _ffm or _ffp:
            def _repl_exe(m):
                sep = m.group(1)
                prog = m.group(2).lower()
                if prog in ("ffmpeg", "ffmpeg.exe") and _ffm:
                    return f'{sep} "{_ffm}"'
                if prog in ("ffprobe", "ffprobe.exe") and _ffp:
                    return f'{sep} "{_ffp}"'
                return m.group(0)
            command = re.sub(
                r'(^|[|;&])\s*(ffmpeg|ffprobe)(?:\.exe)?(?=\s|$)',
                _repl_exe,
                command,
                flags=re.IGNORECASE
            )
    except ImportError:
        pass

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

