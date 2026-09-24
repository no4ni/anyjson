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

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(normalized))
    futures = [executor.submit(run_single, i, a, p) for i, (a, p) in enumerate(normalized)]
    results = []
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=TIMEOUT):
            results.append(fut.result())
    except concurrent.futures.TimeoutError:
        for i, fut in enumerate(futures):
            if not fut.done():
                results.append({"index": i + 1, "action": normalized[i][0],
                                "result": None,
                                "error": f"Таймаут {TIMEOUT} сек "
                                         f"(задача оставлена в фоне)"})
    finally:
        executor.shutdown(wait=False)
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

