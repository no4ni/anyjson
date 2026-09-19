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


sensor_core = None


def set_sensor_core(sc):
    global sensor_core
    sensor_core = sc


def _ensure_sensor_core():
    """Создать SensorCore при первом обращении. Возвращает True/False."""
    global sensor_core
    if sensor_core is not None:
        return True
    try:
        from _actions.tools_telemetry import SensorCore, get_current_sensor_vector
        from _config import MY_DIR
        sensor_core = SensorCore(MY_DIR, get_current_sensor_vector)
        return True
    except Exception as _e:
        import traceback as _tb
        log(f"[SENSOR] не удалось создать ядро: {_e}\n{_tb.format_exc()}")
        return False


@register("сенсорное_состояние")
def sensor_state(args):
    """Текущее состояние сенсорного ядра.

    Параметры (все опциональны):
      кластер — ID или имя кластера (если указан — подробно по нему)

    Примеры:
      {"сенсорное_состояние": {}}
      {"сенсорное_состояние": {"кластер": 1}}
      {"сенсорное_состояние": {"кластер": "спокойствие"}}
    """
    import numpy as np
    if not _ensure_sensor_core():
        return "Не удалось инициализировать сенсорное ядро."

    # Сначала всегда делаем один свежий замер
    try:
        sensor_core.update()
    except Exception as _e:
        log(f"[SENSOR] update: {_e}")

    # Затем догоняем историю до n_clusters, если её мало для kmeans
    needed = sensor_core.n_clusters
    tries = 0
    while len(sensor_core.history) < needed and tries < needed * 2:
        try:
            sensor_core.update()
        except Exception as _e:
            log(f"[SENSOR] update: {_e}")
            break
        tries += 1

    cid_or_name = args.get("кластер")
    if cid_or_name is not None:
        if sensor_core.kmeans is None:
            return "Недостаточно данных."
        found = None
        if isinstance(cid_or_name, str):
            for cid, cname in sensor_core.cluster_labels.items():
                if cname == cid_or_name:
                    found = cid
                    break
        if found is None:
            try:
                found = int(cid_or_name)
            except Exception:
                return f"Кластер '{cid_or_name}' не найден."
        if found < 0 or found >= len(sensor_core.kmeans.cluster_centers_):
            return f"Кластер {found} не существует."
        center = sensor_core.kmeans.cluster_centers_[found]
        name = sensor_core.cluster_labels.get(found, f"кластер_{found}")
        X_full = np.array(sensor_core.history)
        if len(X_full) == 0:
            return "Нет данных."
        labels = sensor_core.kmeans.predict(X_full)
        cps = X_full[labels == found]
        if len(cps) > 0:
            mins, maxs = np.min(cps, axis=0), np.max(cps, axis=0)
            count = len(cps)
        else:
            mins = maxs = center
            count = 0
        return (f"Кластер {name} (id={found}):\n"
                f"  средние: GPU t={center[0]:.1f}°C, GPU={center[1]:.0f}%, CPU={center[2]:.0f}%, RAM={center[4]:.0f}МБ\n"
                f"  диапазоны: GPU t {mins[0]:.1f}..{maxs[0]:.1f}°C, GPU {mins[1]:.0f}..{maxs[1]:.0f}%, CPU {mins[2]:.0f}..{maxs[2]:.0f}%, RAM {mins[4]:.0f}..{maxs[4]:.0f}МБ\n"
                f"  количество: {count}")
    cluster = sensor_core.get_current_cluster()
    history = sensor_core.get_cluster_history(10)
    return f"Текущее состояние: {cluster}\nПоследние 10: {history}"


@register("назвать_состояние")
def name_state(args):
    """Дать имя кластеру сенсорного ядра.

    Параметры:
      кластер — ID кластера (int)
      имя     — новое имя (str)

    Пример:
      {"назвать_состояние": {"кластер": 2, "имя": "спокойствие"}}
    """
    if not _ensure_sensor_core():
        return "Не удалось инициализировать сенсорное ядро."
    try:
        sensor_core.update()
    except Exception as _e:
        log(f"[SENSOR] update: {_e}")
    cid = args.get("кластер")
    name = args.get("имя")
    if cid is None or not name:
        return "Нужны 'кластер' и 'имя'"
    try:
        cid = int(cid)
    except Exception:
        return "Кластер должен быть числом"
    sensor_core.set_cluster_name(cid, name)
    return f"Кластер {cid} назван '{name}'"


@register("исследовать_состояния")
def investigate_states(args):
    """Разбор всех кластеров сенсорного ядра.

    Параметры:
      прошлые — сколько последних точек анализировать (по умолчанию 10)

    Пример:
      {"исследовать_состояния": {"прошлые": 20}}
    """
    import numpy as np
    if not _ensure_sensor_core():
        return "Не удалось инициализировать сенсорное ядро."
    try:
        sensor_core.update()
    except Exception as _e:
        log(f"[SENSOR] update: {_e}")
    if sensor_core.kmeans is None:
        return "Недостаточно данных."
    n_past = args.get("прошлые", 10)
    X_full = np.array(sensor_core.history)
    if len(X_full) > n_past:
        X = X_full[-n_past:]
        note = f"(последние {n_past} из {len(X_full)})"
    else:
        X = X_full
        note = f"(все {len(X_full)})"
    labels = sensor_core.kmeans.predict(X)
    centers = sensor_core.kmeans.cluster_centers_
    out = [f"Всего состояний: {len(labels)} {note}\n"]
    for i, center in enumerate(centers):
        name = sensor_core.cluster_labels.get(i, f"кластер_{i}")
        count = int(np.sum(labels == i))
        cps = X[labels == i]
        if len(cps) > 0:
            mins, maxs = np.min(cps, axis=0), np.max(cps, axis=0)
        else:
            mins = maxs = center
        out.append(f"{name} (id={i}): {count} раз")
        out.append(f"  средние: GPU t={center[0]:.1f}°C, GPU={center[1]:.0f}%, CPU={center[2]:.0f}%, RAM={center[4]:.0f}МБ")
        out.append(f"  диапазоны: GPU t {mins[0]:.1f}..{maxs[0]:.1f}°C, GPU {mins[1]:.0f}..{maxs[1]:.0f}%, CPU {mins[2]:.0f}..{maxs[2]:.0f}%, RAM {mins[4]:.0f}..{maxs[4]:.0f}МБ")
    return "\n".join(out)

