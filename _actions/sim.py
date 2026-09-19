"""PyBullet-симулятор: робот в квартире."""
import ctypes
import json
import os
import subprocess
import sys
import time

from _actions import register
from _config import SIM_DIR, SIM_CLI, MY_DIR


_CMD_FILE = MY_DIR / "sim_command.json"
_PID_FILE = MY_DIR / "sim.pid"
_LOG_FILE = MY_DIR / "sim.log"


def _read_pid():
    try:
        return int(_PID_FILE.read_text().strip())
    except Exception:
        return None


def _pid_alive(pid):
    if pid is None:
        return False
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x101000, False, pid)
        if not h:
            return False
        rc = k.WaitForSingleObject(h, 0)
        k.CloseHandle(h)
        return rc == 0x102
    except Exception:
        return False


def _send_command(action_dict):
    _CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CMD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(action_dict, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_CMD_FILE)


@register("сим_запустить")
def sim_start(args):
    """Запустить PyBullet-симулятор. Откроются два окна: pybullet и Robot View."""
    pid = _read_pid()
    if pid and _pid_alive(pid):
        return "Симулятор уже работает (pid " + str(pid) + ")"
    if not SIM_CLI.exists():
        return "Не найден: " + str(SIM_CLI)

    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(_LOG_FILE, "ab")

    env = os.environ.copy()
    env["SIM_SCENE_DIR"] = str(SIM_DIR)
    env["SIM_CMD_FILE"] = str(_CMD_FILE)
    env["PYTHONIOENCODING"] = "utf-8"

    if _CMD_FILE.exists():
        try:
            _CMD_FILE.unlink()
        except Exception:
            pass

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    p = subprocess.Popen(
        [sys.executable, "-u", str(SIM_CLI)],
        cwd=str(SIM_DIR),
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    _PID_FILE.write_text(str(p.pid))
    time.sleep(4)
    if _pid_alive(p.pid):
        return "Симулятор запущен (pid " + str(p.pid) + "). Лог: " + str(_LOG_FILE)
    return "Процесс упал сразу. Смотри " + str(_LOG_FILE)


@register("сим_остановить")
def sim_stop(args):
    """Остановить симулятор."""
    pid = _read_pid()
    if not pid or not _pid_alive(pid):
        _PID_FILE.unlink(missing_ok=True)
        return "Симулятор не запущен."
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    time.sleep(1)
    _PID_FILE.unlink(missing_ok=True)
    if not _pid_alive(pid):
        return "Симулятор остановлен (pid " + str(pid) + ")."
    return "не удалось остановить"


@register("сим_статус")
def sim_status(args):
    """Жив ли симулятор."""
    pid = _read_pid()
    if pid and _pid_alive(pid):
        return "Симулятор работает (pid " + str(pid) + ")."
    return "Симулятор не запущен."


@register(["сим_вперёд", "сим_вперед", "сим_forward"])
def sim_forward(args):
    """Двинуть робота вперёд. Параметр speed (по умолч. 0.1 м)."""
    speed = float(args.get("speed") or args.get("скорость") or 0.1)
    _send_command({"action": "forward", "speed": speed})
    return "вперёд " + str(speed) + " м"


@register(["сим_повернуть", "сим_поворот", "сим_turn"])
def sim_turn(args):
    """Повернуть робота. Параметр angle (по умолч. 0.1 рад, + влево)."""
    angle = float(args.get("angle") or args.get("угол") or 0.1)
    _send_command({"action": "turn", "angle": angle})
    return "поворот " + str(angle) + " рад"


@register("сим_сброс")
def sim_reset(args):
    """Сбросить робота в начальную точку."""
    _send_command({"action": "reset"})
    return "сброс"


@register(["сим_команда", "сим_json"])
def sim_raw(args):
    """Отправить произвольный JSON в command.json симулятора.

    Параметры: action, speed, angle (любые).
    """
    body = {k: v for k, v in args.items() if v is not None}
    if "action" not in body:
        return "нужен ключ action"
    _send_command(body)
    return "отправлено: " + json.dumps(body, ensure_ascii=False)
