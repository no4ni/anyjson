"""Демон UNaIVERSE для anyjson.

Запускается subprocess'ом из anyjson, не блокирует REPL.

Обмен с anyjson — через файлы в MY_DIR:
  unaiverse_cmd.jsonl     — команды от anyjson (дописываются)
  unaiverse_result.jsonl  — результаты (дописываются)
  unaiverse_inbox.jsonl   — входящие от UNaIVERSE
  human_input.txt         — исходящие (UNaIVERSE читает сам)
  unaiverse.log           — весь вывод ноды
"""
import os
import sys
import json
import time
import asyncio
import requests
from pathlib import Path

# is_debug() в UNaIVERSE вернёт True -> keyboard_listener читает human_input.txt
os.environ["DEBUGPY_RUNNING"] = "1"
# Разрешить запуск, если сервер считает имя занятым,
# но локально мы гарантировали отсутствие дублей
# (PID-файл + _kill_orphan_agents в anyjson-командах).
os.environ["NODE_IGNORE_ALIVE"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

_HERE = Path(__file__).resolve().parent
_MY_DIR = Path(os.environ["ANYJSON_MY_DIR"])
os.chdir(str(_MY_DIR))

CMD_FILE = _MY_DIR / "unaiverse_cmd.jsonl"
RES_FILE = _MY_DIR / "unaiverse_result.jsonl"
INBOX_FILE = _MY_DIR / "unaiverse_inbox.jsonl"

TOKEN = os.environ.get("UNAIVERSE_TOKEN", "")
NODE_NAME = os.environ.get("UNAIVERSE_NODE_NAME", "Jericho")


def _log(msg):
    print(f"[UNAIVERSE {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _write_result(obj):
    try:
        with open(RES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _write_inbox(text):
    try:
        with open(INBOX_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": str(text)},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


# --- штатный log-sink через NODE_LOG_SINK_MODULE ---
# UNaIVERSE сам импортирует модуль и вызовет attach(log).
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
os.environ["NODE_LOG_SINK_MODULE"] = "unaiverse_sink"


from unaiverse.agent import Agent  # noqa: E402
from unaiverse.networking.node.node import Node  # noqa: E402

# ------------------------------------------------------------------
# нейтрализуем prompt_toolkit.patch_stdout: в фоновом процессе у нас
# нет консоли, а UNaIVERSE вызывает patch_stdout внутри run_async.
# Заменяем на no-op контекст-менеджер, чтобы не падало и не читало stdin.
# ------------------------------------------------------------------
# --- 1) safe create_output: никогда не Win32Output ---
try:
    import prompt_toolkit.output.defaults as _ptd
    _orig_create_output = _ptd.create_output

    def _safe_create_output(stdout=None):
        try:
            return _orig_create_output(stdout)
        except Exception:
            from prompt_toolkit.output.plain_text import PlainTextOutput
            import sys as _sys
            return PlainTextOutput(stdout or _sys.stdout)

    _ptd.create_output = _safe_create_output
    print("[run_agent] create_output made safe", flush=True)
except Exception as _e:
    print(f"[run_agent] create_output patch failed: {_e}", flush=True)

# --- 2) neutralized patch_stdout ---
try:
    import prompt_toolkit.patch_stdout as _ps_mod
    from contextlib import contextmanager as _cm

    @_cm
    def _noop_patch_stdout(*args, **kwargs):
        yield

    _ps_mod.patch_stdout = _noop_patch_stdout
    print("[run_agent] prompt_toolkit.patch_stdout neutralized", flush=True)
except Exception as _e:
    print(f"[run_agent] patch_stdout patch failed: {_e}", flush=True)

# --- 0) ускоряем главный цикл UNaIVERSE: их asyncio.sleep(0.001)
#        крутит CPU в 1000 итераций/сек. Поднимаем порог до 50 мс. ---
try:
    import asyncio as _aio
    _MIN_SLEEP = 0.05  # 20 итераций/сек — хватает для чата
    _orig_sleep = _aio.sleep

    async def _slow_sleep(delay, *a, **kw):
        if delay is None:
            return await _orig_sleep(delay, *a, **kw)
        try:
            d = float(delay)
        except Exception:
            return await _orig_sleep(delay, *a, **kw)
        if 0 < d < _MIN_SLEEP:
            d = _MIN_SLEEP
        return await _orig_sleep(d, *a, **kw)

    _aio.sleep = _slow_sleep
    print(f"[run_agent] asyncio.sleep: min {_MIN_SLEEP}s (CPU fix)", flush=True)
except Exception as _e:
    print(f"[run_agent] sleep patch failed: {_e}", flush=True)


# --- 3) заглушка для prompt(): на случай, если код всё-таки вызовет ---
try:
    import prompt_toolkit as _ptk

    def _noop_prompt(*args, **kwargs):
        return ""

    _ptk.prompt = _noop_prompt
    try:
        import prompt_toolkit.shortcuts as _pts
        _pts.prompt = _noop_prompt
    except Exception:
        pass
    print("[run_agent] prompt_toolkit.prompt stubbed", flush=True)
except Exception as _e:
    print(f"[run_agent] prompt patch failed: {_e}", flush=True)


async def _call_maybe_async(fn, *args, **kwargs):
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return fn(*args, **kwargs)


async def _handle(node, cmd):
    op = cmd.get("op")
    a = cmd.get("args") or {}
    try:
        if op == "status":
            conn = list(getattr(node.agent, "all_agents", {}).keys())
            in_world = None
            try:
                in_world = node.agent.in_world()
            except Exception:
                pass
            return {"ok": True, "connected": conn, "in_world": in_world}

        if op == "discover":
            # Глобальный список узлов через /api/nodes/get
            view = a.get("view") or "m"
            payload = {
                "account_token": TOKEN,
                "bounds": [-90, -180, 90, 180],
                "include_your_self": True,
                "view": view,
            }
            # node_token если есть
            if getattr(node, "node_token", None):
                payload["node_token"] = node.node_token
            try:
                r = requests.post(
                    "https://unaiverse.io/api/nodes/get",
                    json=payload, timeout=30,
                )
                data = r.json()
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}
            if not isinstance(data, dict):
                return {"error": "bad response", "raw": str(data)[:300]}
            items = data.get("data")
            if not isinstance(items, list):
                # возможно, data — не список
                items = data.get("nodes") or []
            # обогащаем
            out = []
            for el in items:
                if not isinstance(el, dict):
                    continue
                acct = el.get("account") if isinstance(el.get("account"), dict) else {}
                out.append({
                    "node_id": el.get("node_id"),
                    "name": el.get("node_name"),
                    "type": el.get("node_type"),
                    "desc": el.get("description"),
                    "alive": el.get("is_alive"),
                    "certified": el.get("certified"),
                    "code_pub": el.get("code_published"),
                    "connections": el.get("connections"),
                    "country": el.get("country"),
                    "place": el.get("place"),
                    "lat": el.get("lat"),
                    "lon": el.get("lon"),
                    "created_utc": el.get("created_utc"),
                    "last_alive_utc": el.get("last_alive_utc"),
                    "owner": el.get("owner_handle") or acct.get("nickname"),
                })
            return {"ok": True, "count": len(out), "items": out}

        if op == "search":
            q = a.get("query") or ""
            fn = getattr(node, "search", None)
            if fn is None:
                return {"error": "нет метода search"}
            r = await _call_maybe_async(fn, q)
            items = []
            for obj in (r or []):
                try:
                    st = obj.get_static_profile()
                    items.append({
                        "name": st.get("node_name"),
                        "node_id": st.get("node_id"),
                        "node_type": st.get("node_type"),
                        "owner": st.get("owner"),
                        "cv_hash": st.get("cv_hash"),
                        "is_online": st.get("is_online"),
                    })
                except Exception as e:
                    items.append({"repr": str(obj)[:200], "err": str(e)})
            return {"ok": True, "count": len(items), "items": items}

        if op == "get_in_touch":
            name = a.get("name") or ""
            node_id = a.get("node_id") or ""

            if not name and not node_id:
                return {"error": "нужно name или node_id"}

            # 1. Найти узел через /nodes/get, взять его peer_addresses
            try:
                r = requests.post(
                    "https://unaiverse.io/api/nodes/get",
                    json={"account_token": TOKEN, "view": "l",
                          "include_your_self": True,
                          "bounds": [-90, -180, 90, 180]},
                    timeout=30,
                )
                data = r.json()
            except Exception as e:
                return {"error": f"nodes/get: {type(e).__name__}: {e}"}

            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return {"error": "nodes/get вернул не список"}

            target = None
            if node_id:
                target = next((x for x in items if x.get("node_id") == node_id), None)
            else:
                target = next((x for x in items if x.get("node_name") == name), None)

            if target is None:
                return {"error": f"узел не найден: {name or node_id}"}

            addrs = target.get("peer_addresses") or []
            if not addrs:
                # пробуем из profile
                prof = target.get("profile") if isinstance(target.get("profile"), dict) else {}
                addrs = prof.get("peer_addresses") or []
            if not addrs:
                return {"error": f"у {target.get('node_name')} нет peer_addresses"}

            # 2. Подключиться напрямую по адресам
            fn = getattr(node, "ask_to_get_in_touch", None)
            if fn is None:
                return {"error": "нет метода ask_to_get_in_touch"}
            try:
                r = await _call_maybe_async(fn, addresses=addrs, public=True)
            except Exception as e:
                return {"error": f"ask_to_get_in_touch: {type(e).__name__}: {e}"}
            return {"ok": True, "result": str(r)[:4000],
                    "target": target.get("node_name"),
                    "node_id": target.get("node_id"),
                    "addrs_count": len(addrs)}

        if op == "join_world":
            name = a.get("name") or ""
            node_id = a.get("node_id") or ""
            if not name and not node_id:
                return {"error": "нужно name или node_id"}

            # 1. Найти мир через /nodes/get, взять peer_addresses
            try:
                r = requests.post(
                    "https://unaiverse.io/api/nodes/get",
                    json={"account_token": TOKEN, "view": "l",
                          "include_your_self": True,
                          "bounds": [-90, -180, 90, 180]},
                    timeout=30,
                )
                data = r.json()
            except Exception as e:
                return {"error": f"nodes/get: {type(e).__name__}: {e}"}

            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return {"error": "nodes/get вернул не список"}

            target = None
            if node_id:
                target = next((x for x in items if x.get("node_id") == node_id), None)
            else:
                target = next((x for x in items
                               if x.get("node_name") == name
                               and (x.get("node_type") or "").lower() == "world"), None)
                if target is None:
                    # не нашли мир с таким именем — попробуем просто по имени
                    target = next((x for x in items if x.get("node_name") == name), None)

            if target is None:
                return {"error": f"узел не найден: {name or node_id}"}

            addrs = target.get("peer_addresses") or []
            if not addrs:
                prof = target.get("profile") if isinstance(target.get("profile"), dict) else {}
                addrs = prof.get("peer_addresses") or []
            if not addrs:
                return {"error": f"у {target.get('node_name')} нет peer_addresses"}

            # 2. Подключиться по адресам
            fn = getattr(node, "ask_to_join_world", None)
            if fn is None:
                return {"error": "нет метода ask_to_join_world"}
            try:
                r = await _call_maybe_async(fn, addresses=addrs)
            except TypeError as e:
                # если сигнатура иная — попробуем с node_id
                try:
                    r = await _call_maybe_async(fn, node_id=target.get("node_id"))
                except Exception as e2:
                    return {"error": f"ask_to_join_world: {type(e).__name__}: {e}; "
                                     f"alt: {type(e2).__name__}: {e2}"}
            except Exception as e:
                return {"error": f"ask_to_join_world: {type(e).__name__}: {e}"}

            return {"ok": True, "result": str(r)[:4000],
                    "target": target.get("node_name"),
                    "node_id": target.get("node_id"),
                    "addrs_count": len(addrs)}

        if op == "leave":
            # оставляем мир (если мы в нём)
            try:
                if node.agent is not None and node.agent.in_world():
                    await _call_maybe_async(node.leave_world)
            except Exception as e:
                return {"error": f"leave_world: {type(e).__name__}: {e}"}

            # отключаемся от всех peer'ов, с кем соединены
            left = []
            try:
                peers = list((node.agent.all_agents or {}).keys())
            except Exception:
                peers = []
            for pid in peers:
                try:
                    await _call_maybe_async(node.leave, pid)
                    left.append(pid)
                except Exception as e:
                    return {"error": f"leave({pid}): {type(e).__name__}: {e}"}

            return {"ok": True, "left": len(left)}

        if op == "answer":
            text = str(a.get("text") or "")
            # добавляем zero-width space-маркер, чтобы обойти дедупликацию
            # prev_keyboard_msg в UNaIVERSE
            marker = "\u200b"
            (_MY_DIR / "human_input.txt").write_text(text + marker, encoding="utf-8")
            return {"ok": True, "len": len(text)}

        return {"error": f"неизвестная op: {op}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def _cmd_reader(node):
    seen = 0
    while True:
        try:
            if CMD_FILE.exists():
                lines = CMD_FILE.read_text(encoding="utf-8").splitlines()
                if len(lines) > seen:
                    for line in lines[seen:]:
                        if not line.strip():
                            continue
                        try:
                            cmd = json.loads(line)
                        except Exception as e:
                            _log(f"bad cmd line: {e}")
                            continue
                        res = await _handle(node, cmd)
                        _write_result(res)
                    seen = len(lines)
        except Exception as e:
            _log(f"cmd_reader error: {e}")
        await asyncio.sleep(1)


async def _main():
    _log(f"start node={NODE_NAME!r}, cwd={os.getcwd()}")
    try:
        # HumanModule — эхо-модуль: forward(text, img, whatever)
        #                            -> (text, img)
        # Явно задаём proc_inputs/proc_outputs, иначе UNaIVERSE
        # не может сам угадать форму данных.
        from unaiverse.modules.utils import HumanModule
        from unaiverse.streams.dataprops import StreamType

        proc = HumanModule()
        proc_inputs = [
            StreamType(data_type="text"),
            StreamType(data_type="all"),
        ]
        # НЕ объявляем img-выход: иначе UNaIVERSE открывает веб-камеру
        # (cv2.VideoCapture(0)) и ждёт её 65 секунд до Ready.
        proc_outputs = [
            StreamType(data_type="text"),
        ]

        agent = Agent(proc=proc,
                      proc_inputs=proc_inputs,
                      proc_outputs=proc_outputs)
        _log("Agent created (HumanModule with explicit streams)")
    except Exception as e:
        import traceback as _tb
        _log(f"Agent creation failed: {type(e).__name__}: {e}\n{_tb.format_exc()}")
        _write_result({"error": f"Agent creation failed: {e}"})
        return

    try:
        node = Node(
            hosted=agent,
            unaiverse_key=TOKEN,
            node_name=NODE_NAME,
            hidden=False,
            clock_delta=1.0,
        )
        _log("Node created")
    except Exception as e:
        _log(f"Node creation failed: {type(e).__name__}: {e}")
        _write_result({"error": f"Node creation failed: {e}"})
        return

    _log("starting run_async(interact_mode=True) + cmd_reader")

    async def _runner():
        try:
            await node.run_async(interact_mode=True)
        except Exception as e:
            _log(f"run_async crashed: {type(e).__name__}: {e}")
            _write_result({"error": f"run_async: {e}"})

    await asyncio.gather(_runner(), _cmd_reader(node))


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        _log("interrupted by Ctrl+C")
    except Exception as e:
        _log(f"FATAL: {type(e).__name__}: {e}")
        raise
