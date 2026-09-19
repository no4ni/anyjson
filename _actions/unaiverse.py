"""UNaIVERSE: сеть агентов. Управление демоном и обмен сообщениями."""
import json
import os
import requests
import subprocess
import sys
import time
from pathlib import Path

from _actions import register
from _config import MY_DIR, BASE_DIR, ensure_api_key, AGENT_NAME

RUN_AGENT = BASE_DIR / "side" / "external" / "unaiverse" / "run_agent.py"
LOG_FILE = MY_DIR / "unaiverse.log"
INBOX_FILE = MY_DIR / "unaiverse_inbox.jsonl"
CMD_FILE = MY_DIR / "unaiverse_cmd.jsonl"
RES_FILE = MY_DIR / "unaiverse_result.jsonl"
PID_FILE = MY_DIR / "unaiverse.pid"


def _read_pid():
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _is_alive(pid):
    if not pid:
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return str(pid) in (r.stdout or "")
    except Exception:
        return False


def _graceful_stop(pid, wait_sec=8.0):
    """Мягко остановить процесс: CTRL_CLOSE → ждём → жёсткий kill.

    Возвращает True, если пришлось добивать жёстко.
    """
    if not pid or not _is_alive(pid):
        return False

    # 1) мягко
    try:
        subprocess.run(
            ["taskkill", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        pass

    # 2) ждём
    end = time.time() + wait_sec
    while time.time() < end:
        if not _is_alive(pid):
            return False
        time.sleep(0.5)

    # 3) жёстко
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        pass
    return True


def _kill_orphan_agents():
    """Убить все зависшие процессы run_agent.py на этой машине."""
    if sys.platform != "win32":
        return 0
    killed = 0
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*run_agent.py*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output $_.ProcessId }"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                killed += 1
    except Exception:
        pass
    return killed


def _append_cmd(op, **kwargs):
    cmd = {"op": op, "args": kwargs, "ts": time.time()}
    with open(CMD_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(cmd, ensure_ascii=False) + "\n")


def _read_last_results(n=3):
    if not RES_FILE.exists():
        return []
    lines = RES_FILE.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


@register(["юнайверс_запустить", "unaiverse_start"])
def unaiverse_start(args):
    """Запустить UNaIVERSE-демон в фоне.

    Токен берётся из api_keys.json (ключ "unaiverse").
    Если токена нет — спросит и сохранит.

    Параметры:
      имя   — имя узла (по умолчанию "Jericho")
      сила  — true: если демон жив, погасить его и запустить заново

    Примеры:
      {"юнайверс_запустить": {}}
      {"юнайверс_запустить": {"имя": "Jericho2"}}
      {"юнайверс_запустить": {"сила": true}}
    """
    args = args or {}
    force = bool((args or {}).get("сила") or (args or {}).get("force"))

    pid = _read_pid()
    if pid and _is_alive(pid):
        if not force:
            return (f"ℹ️ Демон уже запущен (PID {pid}). "
                    f'Добавь "сила": true для перезапуска.')
        # force — мягко гасим старый, чтобы сервер отпустил имя узла
        _graceful_stop(pid)
        PID_FILE.unlink(missing_ok=True)

    # --- зачистка всех зависших run_agent.py ДО старта нового ---
    _kill_orphan_agents()

    token = ensure_api_key(
        "unaiverse",
        prompt="🔑 API-токен 'unaiverse' не найден. Введи (Enter — пропустить): ",
    )
    if not token:
        return "❌ Токен UNaIVERSE не задан. Отмена."

    node_name = args.get("имя") or args.get("name") or "Jericho"

    if not RUN_AGENT.exists():
        return f"❌ Не найден {RUN_AGENT}"

    env = os.environ.copy()
    env["ANYJSON_MY_DIR"] = str(MY_DIR)
    env["UNAIVERSE_TOKEN"] = token
    env["UNAIVERSE_NODE_NAME"] = node_name
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["DEBUGPY_RUNNING"] = "1"

    # обнуляем файлы обмена и лог (иначе читаем старые строки)
    for f in (CMD_FILE, RES_FILE, INBOX_FILE, LOG_FILE):
        try:
            f.write_text("", encoding="utf-8")
        except Exception:
            pass

    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

    try:
        log_fp = open(LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(RUN_AGENT)],
            cwd=str(MY_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except Exception as e:
        return f"❌ Не удалось запустить: {e}"

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(2)

    tail = ""
    if LOG_FILE.exists():
        last = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        tail = "\n".join(last[-3:])
    return f"✅ Демон запущен (PID {proc.pid}), имя узла: {node_name}\n{tail}"


@register(["юнайверс_остановить", "unaiverse_stop"])
def unaiverse_stop(args):
    """Остановить UNaIVERSE-демон."""
    pid = _read_pid()
    if not pid:
        return "ℹ️ Демон не запущен"
    if not _is_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        extra_killed = _kill_orphan_agents()
        extra = f" (+{extra_killed} осиротевших)" if extra_killed else ""
        return f"ℹ️ Демон уже не работал (запись PID очищена){extra}"
    forced = _graceful_stop(pid)
    PID_FILE.unlink(missing_ok=True)
    extra_killed = _kill_orphan_agents()
    marks = []
    if forced:
        marks.append("жёстко")
    if extra_killed:
        marks.append(f"+{extra_killed} осиротевших")
    suffix = f" ({', '.join(marks)})" if marks else ""
    return f"✅ Демон остановлен{suffix}"


@register(["юнайверс_статус", "unaiverse_status"])
def unaiverse_status(args):
    """Статус UNaIVERSE: жив ли демон, хвост лога."""
    pid = _read_pid()
    alive = _is_alive(pid)
    out = [f"🔌 UNaIVERSE: {'запущен' if alive else 'остановлен'}"
           f"{f' (PID {pid})' if pid else ''}"]
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        # фильтруем технический шум (адреса, peer ID)
        skip_markers = (
            "192.168.", "94.75.", "/tcp/", "/udp/", "quic-v1",
            "webrtc-direct", "webtransport", "Public addresses",
            "Private addresses", "Public peer ID", "Private peer ID",
        )
        clean = [l for l in lines if not any(m in l for m in skip_markers)]
        tail = clean[-15:]
        if tail:
            out.append("📜 Лог (последние значимые):")
            for l in tail:
                out.append(f"  {l}")
    return "\n".join(out)


@register(["юнайверс_поиск", "unaiverse_search"])
def unaiverse_search(args):
    """Найти агентов/миры в сети.

    Параметры:
      запрос — что искать (опционально; пусто = показать всё)

    Пример:
      {"юнайверс_поиск": {"запрос": "music"}}
      {"юнайверс_поиск": {}}
    """
    q = (args or {}).get("запрос") or (args or {}).get("query") or ""
    _append_cmd("search", query=q)
    time.sleep(3)
    res = _read_last_results(1)
    if not res:
        return "⏳ Ждём результат..."
    last = res[-1]
    if last.get("error"):
        return f"❌ {last['error']}"
    items = last.get("items") or []
    if not items:
        return f"🔍 По '{q}' никого не найдено."
    lines = [f"🔍 Найдено: {len(items)} (по '{q}')", ""]
    _TYPE = {"agent": "Агент", "world": "Мир", "human": "Человек"}
    for it in items:
        name = it.get("name") or "?"
        nt = (it.get("node_type") or "?").lower()
        nt_ru = _TYPE.get(nt, it.get("node_type") or "?")
        online = it.get("is_online")
        mark = " 🟢" if online else ""
        line = f"  • {name}  [{nt_ru}]{mark}"
        if it.get("node_id"):
            line += f"\n    id: {it['node_id']}"
        lines.append(line)
    return "\n".join(lines)


@register(["юнайверс_подключиться", "unaiverse_connect"])
def unaiverse_connect(args):
    """Подключиться к агенту по имени.

    Параметры:
      кому — имя агента (обязательный)

    Пример:
      {"юнайверс_подключиться": {"кому": "MuzArt"}}
    """
    name = (args or {}).get("кому") or (args or {}).get("name") or ""
    if not name:
        return "Укажи 'кому'."

    try:
        RES_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass

    _append_cmd("get_in_touch", name=name)

    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(2)
        res = _read_last_results(1)
        if not res:
            continue
        last = res[-1]
        if last.get("error"):
            return f"❌ {last['error']}"
        if "ok" in last or "result" in last:
            r = last.get("result")
            target = last.get("target") or name
            if r and r != "None":
                return (f"✅ Подключено к {target}\n"
                        f"   peer_id: {r}\n"
                        f"   адресов: {last.get('addrs_count', '?')}")
            return (f"❌ Не удалось подключиться к {target}\n"
                    f"   соединение не установлено (None)")

    return "⏱ Не дождались ответа за 60 сек. Смотри лог."


@register(["юнайверс_войти_в_мир", "unaiverse_join_world"])
def unaiverse_join_world(args):
    """Войти в мир UNaIVERSE.

    Параметры:
      мир — имя мира (обязательный)

    Пример:
      {"юнайверс_войти_в_мир": {"мир": "Creative"}}
    """
    name = (args or {}).get("мир") or (args or {}).get("name") or ""
    if not name:
        return "Укажи 'мир'."

    try:
        RES_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    _append_cmd("join_world", name=name)

    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(2)
        res = _read_last_results(1)
        if not res:
            continue
        last = res[-1]
        if last.get("error"):
            return f"❌ {last['error']}"
        if last.get("ok"):
            r = last.get("result")
            target = last.get("target") or name
            if r and r != "None":
                return (f"✅ Вошли в мир {target}\n"
                        f"   node_id: {last.get('node_id', '?')}\n"
                        f"   адресов: {last.get('addrs_count', '?')}")
            return (f"❌ Не удалось войти в {target}\n"
                    f"   соединение не установлено (None)")
    return "⏱ Не дождались ответа за 60 сек."


@register(["юнайверс_покинуть", "unaiverse_leave"])
def unaiverse_leave(args):
    """Покинуть мир/соединения."""
    try:
        RES_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    _append_cmd("leave")
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(2)
        res = _read_last_results(1)
        if res:
            last = res[-1]
            if last.get("error"):
                return f"❌ {last['error']}"
            if last.get("ok"):
                n = last.get("left", 0)
                return f"✅ Покинул соединения: {n}"
    return "⏱ Не дождались ответа за 30 сек."


@register(["юнайверс_входящие", "unaiverse_inbox"])
def unaiverse_inbox(args):
    """Показать входящие сообщения из UNaIVERSE.

    Параметры:
      количество — сколько последних показать (по умолчанию 20)

    Пример:
      {"юнайверс_входящие": {}}
    """
    if not INBOX_FILE.exists():
        return "📭 Входящих нет."
    n = int((args or {}).get("количество", 20) or 20)
    lines = INBOX_FILE.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        ts = time.strftime("%H:%M:%S", time.localtime(obj.get("ts", 0)))
        text = obj.get("text", "")
        out.append(f"[{ts}] {text}")
    return "\n\n".join(out) if out else "📭 Входящих нет."


@register(["юнайверс_ответить", "unaiverse_answer"])
def unaiverse_answer(args):
    """Отправить текст партнёру в UNaIVERSE.

    Параметры:
      текст — что отправить (обязательный)
      алиас: text, содержимое

    Пример:
      {"юнайверс_ответить": {"текст": "привет!"}}
    """
    a = args or {}
    text = (a.get("текст") or a.get("text") or a.get("содержимое") or "").strip()
    if not text:
        return "Пустой текст."
    _append_cmd("answer", text=text)
    time.sleep(2)
    res = _read_last_results(1)
    return (f"✅ Отправлено: {text[:80]}{'…' if len(text) > 80 else ''}\n"
            + (json.dumps(res, ensure_ascii=False) if res else ""))


@register(["юнайверс_найти", "юнайверс_список", "unaiverse_find"])
def unaiverse_find(args):
    """Глобальный поиск по сети UNaIVERSE.

    Возвращает список узлов (агенты, миры, люди) с фильтрами.
    Работает как страница Discover на сайте.

    Параметры:
      тип        — "агент" | "мир" | "человек" (или agent/world/human)
      онлайн     — true: только живые
      сертифицирован — true: только с галочкой verified
      страна     — подстрока по стране ("Italy", "Russia")
      поиск      — подстрока в имени или описании
      лимит      — сколько показать (по умолчанию 20, макс 100)
      своё       — true: включать себя (по умолчанию false)

    Примеры:
      {"юнайверс_найти": {}}
      {"юнайверс_найти": {"тип": "агент", "онлайн": true}}
      {"юнайверс_найти": {"поиск": "tutor"}}
      {"юнайверс_найти": {"страна": "Germany", "лимит": 30}}
    """
    a = args or {}
    _append_cmd("discover", view="m")
    time.sleep(4)  # ждём ответа
    res = _read_last_results(1)
    if not res:
        return "⏳ Ждём результат..."
    last = res[-1]
    if last.get("error"):
        return f"❌ {last['error']}"
    items = last.get("items") or []
    total = len(items)

    # ---- фильтры ----
    _TYPE = {"agent": "agent", "агент": "agent",
             "world": "world", "мир": "world",
             "human": "human", "человек": "human",
             "люди": "human", "людей": "human"}
    t = (a.get("тип") or a.get("type") or "").strip().lower()
    want_type = _TYPE.get(t)

    only_online = bool(a.get("онлайн") or a.get("online"))
    only_cert = bool(a.get("сертифицирован") or a.get("certified") or a.get("verified"))
    country = (a.get("страна") or a.get("country") or "").strip().lower()
    search = (a.get("поиск") or a.get("query") or a.get("q") or "").strip().lower()
    include_self = bool(a.get("своё") or a.get("include_self") or a.get("self"))
    try:
        limit = int(a.get("лимит") or a.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    if limit <= 0 or limit > 100:
        limit = 20

    def _keep(it):
        if want_type and (it.get("type") or "").lower() != want_type:
            return False
        if only_online and not it.get("alive"):
            return False
        if only_cert and not it.get("certified"):
            return False
        if country and country not in (it.get("country") or "").lower():
            return False
        if search:
            blob = ((it.get("name") or "") + " " + (it.get("desc") or "")).lower()
            if search not in blob:
                return False
        return True

    filtered = [it for it in items if _keep(it)]
    if not include_self:
        filtered = [it for it in filtered
                    if (it.get("name") or "") != AGENT_NAME]
    shown = filtered[:limit]

    # ---- вывод ----
    _T_RU = {"agent": "Агент", "world": "Мир", "human": "Человек"}
    lines = [f"🌐 Найдено: {len(filtered)} из {total}"
             + (f", показано {len(shown)}" if len(filtered) > limit else ""), ""]
    for it in shown:
        name = it.get("name") or "?"
        nt = (it.get("type") or "?").lower()
        nt_ru = _T_RU.get(nt, it.get("type") or "?")
        mark = " 🟢" if it.get("alive") else ""
        cert = " ✅" if it.get("certified") else ""
        line = f"  • {name}  [{nt_ru}]{mark}{cert}"
        loc = it.get("place") or it.get("country")
        if loc:
            line += f"\n    {loc}"
        desc = (it.get("desc") or "").strip()
        if desc:
            if len(desc) > 120:
                desc = desc[:120] + "…"
            line += f"\n    {desc}"
        if it.get("node_id"):
            line += f"\n    id: {it['node_id']}"
        lines.append(line)
    if len(filtered) > limit:
        lines.append("")
        lines.append(f"  … и ещё {len(filtered) - limit}. Увеличь 'лимит' или уточни фильтр.")
    return "\n".join(lines)


@register(["юнайверс_запрос", "унаиверс_запрос", "unaiverse_request"])
def unaiverse_raw_request(args):
    """Прямой POST-запрос к UNaIVERSE API.

    Полезно для отладки и нестандартных запросов.

    Параметры:
      url   — путь (например "/nodes/get") или полный URL
              алиасы: путь, адрес
      data  — JSON-тело запроса (объект, опционально)
              алиасы: данные, тело, json
      токен — если false, не подмешивать account_token
              (по умолчанию подмешивается, если не задан явно)

    Примеры:
      {"юнайверс_запрос": {"url": "/nodes/get", "data": {"bounds": [-90,-180,90,180]}}}
      {"юнайверс_запрос": {"url": "https://unaiverse.io/api/nodes/get"}}
      {"юнайверс_запрос": {"url": "/account/node/get/id",
                           "data": {"node_name": "Jericho"}}}
    """
    a = args or {}
    url = (a.get("url") or a.get("путь") or a.get("адрес") or "").strip()
    if not url:
        return "Укажи 'url'."

    # нормализуем URL
    if url.startswith("http"):
        full = url
    else:
        if not url.startswith("/"):
            url = "/" + url
        full = "https://unaiverse.io/api" + url

    # data
    data = a.get("data")
    if data is None:
        data = a.get("данные") or a.get("тело") or a.get("json") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return "Ошибка: 'data' строка, но не парсится как JSON."

    payload = dict(data) if isinstance(data, dict) else {}

    # авто-подмешивание токена (если явно не отключено)
    if a.get("токен", True) is not False and "account_token" not in payload:
        try:
            from _config import API_KEYS
            tok = API_KEYS.get("unaiverse")
            if tok:
                payload["account_token"] = tok
        except Exception:
            pass

    try:
        r = requests.post(full, json=payload, timeout=30)
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"

    text = r.text or ""

    # если ответ — {"data": [...]} — показываем сводку + первые N
    try:
        j = r.json()
    except Exception:
        j = None

    if isinstance(j, dict) and isinstance(j.get("data"), list):
        items = j["data"]
        try:
            limit = int(a.get("лимит") or a.get("limit") or 3)
        except (TypeError, ValueError):
            limit = 3
        if limit <= 0 or limit > 50:
            limit = 3
        head = items[:limit]
        head_json = json.dumps(head, ensure_ascii=False, indent=2)
        return (f"Статус: {r.status_code}\n"
                f"📊 data: {len(items)} записей\n"
                f"─ первые {len(head)} ─\n"
                f"{head_json}")

    # иначе — просто текст (обрезаем)
    if len(text) > 5000:
        text = text[:5000] + "\n… (обрезано, всего " + str(len(text)) + " байт)"
    return f"Статус: {r.status_code}\n{text}"


@register(["юнайверс_лог", "унаиверс_лог", "unaiverse_log"])
def unaiverse_log(args):
    """Показать хвост лога UNaIVERSE-демона.

    Параметры:
      строк    — сколько последних строк показать (по умолчанию 30)
      фильтр   — подстрока; показывать только строки, содержащие её
      контекст — показывать по N строк вокруг каждой найденной (по умолч. 0)

    Примеры:
      {"юнайверс_лог": {}}
      {"юнайверс_лог": {"строк": 100}}
      {"юнайверс_лог": {"фильтр": "Agent created"}}
      {"юнайверс_лог": {"фильтр": "error|failed", "контекст": 2}}
    """
    if not LOG_FILE.exists():
        return "📭 Лог пуст (файл не найден)."

    a = args or {}
    try:
        n = int(a.get("строк") or a.get("lines") or 30)
    except (TypeError, ValueError):
        n = 30
    if n <= 0 or n > 5000:
        n = 30

    flt = (a.get("фильтр") or a.get("filter") or "").strip()
    try:
        ctx = int(a.get("контекст") or a.get("context") or 0)
    except (TypeError, ValueError):
        ctx = 0

    text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    if not flt:
        tail = lines[-n:]
        out = [f"📜 {LOG_FILE.name} — {len(lines)} строк, последние {len(tail)}:"]
        out.extend(tail)
        return "\n".join(out)

    # фильтр: строки через "|" (ИЛИ), регистр не важен
    import re as _re
    try:
        rx = _re.compile(flt, _re.IGNORECASE)
    except Exception:
        rx = None

    def _hit(line):
        if rx is None:
            return flt.lower() in line.lower()
        return bool(rx.search(line))

    idxs = [i for i, l in enumerate(lines) if _hit(l)]
    if not idxs:
        return f"🔍 В логе нет строк, содержащих '{flt}'."

    if ctx > 0:
        # объединяем диапазоны вокруг найденных
        keep = set()
        for i in idxs:
            for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
                keep.add(j)
        sel = [lines[i] for i in sorted(keep)]
    else:
        sel = [lines[i] for i in idxs]

    if len(sel) > n:
        sel = sel[-n:]

    out = [f"📜 Найдено {len(idxs)} совпадений по '{flt}', "
           f"показываю {len(sel)}:"]
    out.extend(sel)
    return "\n".join(out)
