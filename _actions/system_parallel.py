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


DANGEROUS_PARALLEL_ACTIONS = {"переместить", "move", "удалить_файл",
                              "delete_file", "убраться_в", "переписать", "удалить_папку"}


KNOWN_ACTIONS = {
    "новости", "погода", "наука", "багфикс", "исчисление",
    "энциклопедия", "веб_поиск", "поиск_информации", "поиск_агентов",
    "анализ_папки", "осмотреть", "профиль",
    "время", "запрос", "питон", "телеметрия",
    "показать_экран", "паста", "получить_прокси",
    "удалить_прокси", "сенсорное_состояние",
    "назвать_состояние", "исследовать_состояния",
    "заполнить_профиль", "напомнить", "читать",
    "переписать", "добавить", "входящие",
    "подписаться", "лайк", "коммент",
    "анализ_изображения", "формат", "сообщение",
    "вещать", "запомнить", "вспомнить",
    "страница", "веб_страница",
    "убраться_в", "найти_файл", "запуск",
    "удалить_папку",
}


STRING_QUERY_ACTIONS = {"новости", "погода", "наука",
                        "багфикс", "исчисление", "энциклопедия",
                        "веб_поиск", "поиск_информации", "поиск_агентов"}


def _normalize_params(action, params):
    if params is None:
        return {}
    if isinstance(params, str):
        if action in ("выполнить", "скрипт", "python", "браузер", "browser",
                       "питон"):
            return {"скрипт": params}
        if action in ("погода", "weather"):
            return {"город": params}
        if action == "запомнить":
            return {"запомнить": params}
        if action in ("запуск", "run"):
            return {"команда": params}
        return {"запрос": params}
    if isinstance(params, dict):
        return params
    return {"значение": params}


@register("параллельно")
def parallel(args):
    """Запустить несколько команд параллельно.

    Параметры:
      команды — список команд (словари или строки)
      таймаут  — общий лимит на всю группу в секундах (по умолчанию 1800)

    Пример:
      {"параллельно": [
        {"время": {}},
        {"разведка_системы": {}}
      ]}

      {"параллельно": [
        {"скачать_видео": {"url": "https://..."}}
      ], "таймаут": 3600}
    """
    from _executor import execute_tool_safe
    if isinstance(args, list):
        args = [t for t in args if not (
            (isinstance(t, dict) and (t.get("action") in DANGEROUS_PARALLEL_ACTIONS or
                                      any(k in DANGEROUS_PARALLEL_ACTIONS for k in t.keys()))) or
            (isinstance(t, str) and t in DANGEROUS_PARALLEL_ACTIONS)
        )]

    tasks = args if isinstance(args, list) else (
        args.get("команды") or args.get("значение")
        if isinstance(args, dict) else None
    )
    if not tasks or not isinstance(tasks, list):
        return "Ошибка: 'параллельно' должен содержать список команд"

    normalized = []
    for idx, task in enumerate(tasks):
        if isinstance(task, str):
            normalized.append((task, {}))
            continue
        if not isinstance(task, dict):
            return f"Ошибка: подкоманда #{idx+1} не словарь и не строка"
        if "action" in task:
            normalized.append((task["action"], task.get("params", {})))
            continue
        if len(task) == 1:
            action = next(iter(task))
            params = task[action]
            if isinstance(params, str):
                params = _normalize_params(action, params)
            elif not isinstance(params, dict):
                params = {"значение": params}
            normalized.append((action, params))
            continue
        action, params = None, {}
        for k, v in task.items():
            if k in KNOWN_ACTIONS:
                if action is not None:
                    action = None
                    break
                action = k
                if isinstance(v, str):
                    params.update(_normalize_params(action, v))
                elif isinstance(v, dict):
                    params.update(v)
            else:
                params[k] = v
        if action is None:
            return f"Не удалось определить команду #{idx+1}"
        normalized.append((action, params))

    if not normalized:
        return "Нет корректных команд"
    # Слияние: если ВСЕ подзадачи — read_full-команды (прочитать_полностью,
    # посмотреть_картинку, показать_файл, рендер и т.п.), кладём все файлы
    # в буфер одним Ctrl+C (CF_HDROP с несколькими файлами, как в проводнике).
    _READ_FULL_NAMES = {
        "прочитать_полностью", "читать_полностью", "открыть",
        "показать_файл", "посмотреть_файл",
        "посмотреть_картинку", "показать_картинку",
        "рендер", "отрендерить", "svg_в_буфер", "рендер_svg",
    }
    if all(_a in _READ_FULL_NAMES for _a, _ in normalized):
        _paths = []
        _all_have_paths = True
        for _a, _p in normalized:
            _pv = _p.get("путь") or _p.get("файл") or ""
            if isinstance(_pv, list):
                _paths.extend(str(x).strip() for x in _pv if str(x).strip())
            elif isinstance(_pv, str) and _pv.strip():
                _paths.append(_pv.strip())
            else:
                _all_have_paths = False
                break
        if _all_have_paths and len(_paths) > 1:
            return execute_tool_safe("прочитать_полностью", {"путь": _paths})

    # Таймаут всей группы. По умолчанию 30 минут.
    # Можно переопределить в самом вызове: {"параллельно": [...], "таймаут": 3600}
    # или через параметр "таймаут" у подзадачи.
    TIMEOUT = 1800
    if isinstance(args, dict):
        _t = args.get("таймаут")
        if isinstance(_t, (int, float)) and _t > 0:
            TIMEOUT = int(_t)
    for _action, _params in normalized:
        if isinstance(_params, dict):
            _pt = _params.get("таймаут")
            if isinstance(_pt, (int, float)) and _pt > 0:
                TIMEOUT = max(TIMEOUT, int(_pt) + 60)

    def run_single(i, action, params):
        try:
            return {"index": i + 1, "action": action, "result": execute_tool_safe(action, params), "error": None}
        except Exception as e:
            return {"index": i + 1, "action": action, "result": None, "error": str(e)}

    _print_lock = threading.Lock()
    def _say(msg):
        with _print_lock:
            try:
                print(msg, flush=True)
            except Exception:
                pass

    _n = len(normalized)
    _start_ts = time.monotonic()
    _started = {}
    _say("\U0001F680 Параллельно: " + str(_n) + " задач (таймаут "
         + str(TIMEOUT) + "s). Ctrl+C \u2014 прервать.")
    for _i, (_a, _p) in enumerate(normalized, 1):
        _started[_i] = time.monotonic()
        _say("   \u25B6 [" + str(_i) + "/" + str(_n) + "] " + _a)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=_n)
    futures = [executor.submit(run_single, i, a, p) for i, (a, p) in enumerate(normalized)]
    results = []
    _interrupted = False
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=TIMEOUT):
            r = fut.result()
            _el = time.monotonic() - _started.get(r["index"], time.monotonic())
            _mark = "\u2705" if r["error"] is None else "\u274C"
            _say("   " + _mark + " [" + str(r["index"]) + "/" + str(_n) + "] "
                 + r["action"] + " \u2014 " + format(_el, ".1f") + "s")
            results.append(r)
    except concurrent.futures.TimeoutError:
        for i, fut in enumerate(futures):
            if not fut.done():
                results.append({"index": i + 1, "action": normalized[i][0],
                                "result": None,
                                "error": f"Таймаут {TIMEOUT} сек "
                                         f"(задача оставлена в фоне)"})
    except KeyboardInterrupt:
        _interrupted = True
        for i, fut in enumerate(futures):
            if fut.done():
                try:
                    results.append(fut.result())
                except Exception:
                    pass
            else:
                fut.cancel()
        _say("")
        _say("\u26D4 Прервано пользователем (Ctrl+C). Завершено "
             + str(len(results)) + " из " + str(_n) + ".")
    finally:
        executor.shutdown(wait=False)

    if _interrupted:
        _el_total = time.monotonic() - _start_ts
        return ("\u26D4 Параллельно прервано пользователем.\n"
                + "Завершено " + str(len(results)) + " из " + str(_n)
                + " задач за " + format(_el_total, ".1f") + "s.\n"
                + "Незавершённые задачи могут продолжить работу в фоне "
                + "(их не остановить из Python).")

    results.sort(key=lambda x: x["index"])
    max_len = max(500, 2048 // len(results)) if results else 500
    lines = []
    for _idx, r in enumerate(results):
        if _idx > 0:
            lines.append("")
        mark = "✅" if r["error"] is None else "❌"
        lines.append(f"{mark} Задача {r['index']}: {r['action']}")
        if r["error"]:
            lines.append(f"   Ошибка: {r['error']}")
        else:
            preview = (r["result"][:max_len] + "...") if len(r["result"]) > max_len else r["result"]
            lines.append(f"   Результат: {preview}")
    return "\n".join(lines)

