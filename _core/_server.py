"""
E:/Jericho/Private/anyjson/_core/_server.py
Веб-сервер: стрим общения с llama.cpp + инструменты через persistent agent.py daemon.
"""
import io
import os
import re
import sys
import json
import logging
import threading
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import urllib.request
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("AnyJSON-Web")

LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
PORT = 8081
AGENT_NAME = "Аниджсон"
MAX_TOOL_ITERATIONS = 5

# ANSI-коды (ESC[31m и т.п.): cmd_parser красит вывод через color_red/color_yellow,
# демон отдаёт его вместе с кодами - они невидимы, но ломают строковые сравнения.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_messages = []
_system_prompt = None

_agent_process = None
_agent_lock = threading.Lock()

def _init_system_prompt():
    """Вызвать agent.py без JSON, получить приветствие как system-промпт и дописать веб-протокол инструментов."""
    global _system_prompt
    # Базовый промпт: приветствие из agent.py либо заглушка
    base = "Ты — AI-ассистент."
    agent_py = Path(__file__).parent.parent / "agent.py"
    if not agent_py.exists():
        logger.warning(f"[INIT] agent.py не найден: {agent_py}")
    else:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(
                [sys.executable, str(agent_py), AGENT_NAME],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            if result.returncode == 0 and result.stdout.strip():
                base = result.stdout.strip()
            else:
                logger.warning(f"[INIT] agent.py вернул код {result.returncode}")
        except Exception as e:
            logger.error(f"[INIT] не удалось вызвать agent.py: {e}")
    # Протокол инструментов - минимальный, так как llama.cpp возвращает tool_calls нативно
    protocol = (
        "\n\nИНСТРУМЕНТЫ:\n"
        "Используй инструменты (tools) для выполнения команд. Не выдумывай данные, которые можно получить командой.\n"
        "Финальный ответ пользователю - простым текстом, после получения результатов."
    )
    _system_prompt = base + protocol
    logger.info(f"[INIT] system prompt loaded ({len(_system_prompt)} chars)")

def _ensure_agent_daemon():
    global _agent_process
    with _agent_lock:
        if _agent_process is not None and _agent_process.poll() is None:
            return
        
        logger.info("[INIT] Запуск agent.py в режиме демона...")
        agent_py = Path(__file__).parent.parent / "agent.py"
        
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        
        try:
            _agent_process = subprocess.Popen(
                [sys.executable, str(agent_py), "--daemon", AGENT_NAME],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            def _read_stderr():
                for line in _agent_process.stderr:
                    sys.stderr.write(f"[AGENT-STDERR] {line}")
            
            threading.Thread(target=_read_stderr, daemon=True).start()
            time.sleep(2)
            if _agent_process.poll() is not None:
                raise RuntimeError(f"Демон упал при старте. Код: {_agent_process.returncode}")
                
            logger.info("[INIT] Агент-демон успешно запущен.")
        except Exception as e:
            logger.error(f"[INIT] Ошибка запуска демона: {e}")
            raise

def _call_agent_daemon(json_text):
    global _agent_process
    _ensure_agent_daemon()
    with _agent_lock:
        try:
            _agent_process.stdin.write(json_text + "\n")
            _agent_process.stdin.flush()
            response_line = _agent_process.stdout.readline()
            if not response_line:
                raise RuntimeError("Демон закрыл stdout")
            resp = json.loads(response_line)
            if resp.get("status") == "error":
                return f"[AGENT ERROR] {resp.get('error')}"
            clipboard = resp.get("clipboard", "").strip()
            output = resp.get("output", "").strip()
            # Фильтр служебных строк демона: DEBUG, "Результаты:", буфер.
            # Применяется и к output, и к clipboard — иначе при return clipboard
            # (путь "[BUF]") "Результаты:" просачивается в UI и в context нетронутым.
            def _filter_noise(text):
                if not text:
                    return text
                _filtered = []
                _skip_rest = False
                for _ln in text.splitlines():
                    # Сначала счищаем ANSI-коды: невидимые escape-последовательности
                    # не должны спасать служебные строки от фильтра.
                    _s = _ANSI_RE.sub("", _ln).strip()
                    if not _skip_rest and _s.startswith("\U0001F4A1") and "Пакетный режим" in _s:
                        _skip_rest = True
                        continue
                    if _skip_rest:
                        continue
                    if _s.startswith("[DEBUG]"): continue
                    if "Результаты скопированы в буфер" in _s: continue
                    # Заголовок «Результаты:» ловим нормализованно: NBSP, ZWSP, BOM,
                    # полноширинное двоеточие и регистр не должны спасать его от фильтра.
                    if "".join(_s.split()).replace(chr(8203), "").replace(chr(65279), "").strip(":：").lower() == "результаты": continue
                    _filtered.append(_s)
                return "\n".join(_filtered).strip()
            output = _filter_noise(output)
            clipboard = _filter_noise(clipboard)
            if clipboard and "[BUF]" in output:
                return clipboard
            if clipboard and not output:
                return clipboard
            return output or "[AGENT EMPTY RESPONSE]"
        except Exception as e:
            logger.error(f"[AGENT CALL ERROR] {e}")
            _agent_process = None
            return f"[AGENT CRASH] {e}"



def stream_llm(messages, max_tokens=1024, tools=None):
    """Стрим SSE из llama.cpp с нативными tool calls.

    Вызов инструмента приходит фрагментами: первый delta несёт имя и
    пустые arguments, остальные - куски JSON-строки без type:function.
    Поэтому копим по index и отдаём один раз после конца stream.
    Отдача на первом чанке теряла аргументы (вызов уходил с {}).
    """
    payload = {
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": True,
        "reasoning_effort": "low",
        "reasoning_budget": 512,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    payload_bytes = json.dumps(payload).encode("utf-8-sig")
    req = urllib.request.Request(
        LLM_URL, data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    in_think = False
    tc_acc = {}      # index -> {"id", "name", "arguments"} - накопитель вызовов
    tc_order = []    # порядок появления индексов
    finish = None    # finish_reason последнего choice (для диагностики)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in io.TextIOWrapper(resp, encoding="utf-8-sig"):
                line = raw.strip()
                if not line.startswith("data: "):
                    continue
                ds = line[6:]
                if ds == "[DONE]":
                    break
                try:
                    obj = json.loads(ds)
                except json.JSONDecodeError:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                # --- Tool calls: только копим, наружу пока ничего не отдаём ---
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)          # index связывает фрагменты
                    if idx not in tc_acc:
                        tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        tc_order.append(idx)
                    slot = tc_acc[idx]
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]     # имя приходит один раз
                    a = fn.get("arguments")
                    if isinstance(a, str):
                        slot["arguments"] += a        # очередной кусок JSON-строки
                    elif isinstance(a, dict):
                        slot["arguments"] = json.dumps(a, ensure_ascii=False)
                # --- Мысли и текст стримим как раньше ---
                thinking_tok = delta.get("reasoning_content", "") or delta.get("thinking", "")
                content_tok = delta.get("content", "")
                if thinking_tok:
                    yield {"type": "thought", "content": thinking_tok}
                if content_tok:
                    if "<think>" in content_tok or "<thinking>" in content_tok:
                        in_think = True
                    yield {"type": "thought" if in_think else "token", "content": content_tok}
                    if "</think>" in content_tok or "</thinking>" in content_tok:
                        in_think = False
    except Exception as ex:
        logger.error(f"[LLM ERROR] {ex}")
        yield {"type": "error", "content": str(ex)}
        return
    # --- Stream кончился: разбираем накопленные вызовы ---
    if len(tc_order) > 1:
        logger.warning(f"[TOOL] модель прислала {len(tc_order)} вызовов, исполняю первый")
    for idx in tc_order:
        slot = tc_acc[idx]
        if not slot["name"]:
            continue                                  # мусор без имени - пропускаем
        raw_args = slot["arguments"].strip()
        logger.info(f"[TOOL RAW] {slot['name']} finish={finish} args={raw_args[:300]!r}")
        args = {}
        if raw_args:
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as e:
                # Раньше здесь молча подставлялся {} - баг маскировался
                logger.error(f"[TOOL PARSE] {slot['name']}: {e}")
                yield {"type": "error",
                       "content": f"Аргументы {slot['name']} не разобраны: {raw_args[:300]}"}
                return
            if not isinstance(args, dict):
                logger.error(f"[TOOL PARSE] {slot['name']}: не объект, а {type(args).__name__}")
                yield {"type": "error",
                       "content": f"Аргументы {slot['name']} должны быть объектом"}
                return
        else:
            logger.warning(f"[TOOL] {slot['name']}: аргументы пусты у самой модели")
        yield {"type": "tool_call", "name": slot["name"], "arguments": args,
               "id": slot["id"], "raw_arguments": raw_args}
        return                                        # контракт do_GET: один вызов

def _load_web_ui():
    # Читать HTML интерфейса из _core/web_ui.html при каждом запросе (live-дизайн).
    _ui_path = Path(__file__).parent / "web_ui.html"
    try:
        return _ui_path.read_text(encoding="utf-8")
    except Exception as _e:
        logger.error(f"[UI] web_ui.html не прочитан: {_e}")
        return '<html><body style="background:#0b1220;color:#e9eef8;font-family:sans-serif;padding:40px"><h1>AnyJSON Chat</h1><p>web_ui.html не найден рядом с _server.py.</p></body></html>'
HTML = _load_web_ui()

_stats = None

def _load_stats():
    """Загрузить статистику команд из JSON."""
    path = Path(__file__).parent / "command_stats.json"
    fallback = {"sessions": [], "fallback_top5": ["время", "читать", "профиль_свой", "список_файлов", "эхо"]}
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "sessions" in data and "fallback_top5" in data:
            return data
        return fallback
    except Exception:
        return fallback

def _save_stats(stats):
    """Сохранить статистику команд в JSON."""
    path = Path(__file__).parent / "command_stats.json"
    try:
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"[STATS] не удалось сохранить: {e}")

def _record_command(name, stats):
    """Записать вызов команды в текущую сессию."""
    if not stats["sessions"]:
        return
    current = stats["sessions"][-1]
    current["counts"][name] = current["counts"].get(name, 0) + 1
    _save_stats(stats)

def _get_last5(stats):
    """Получить 5 последних вызванных команд из текущей сессии (в порядке вызова)."""
    if not stats or not stats.get("sessions"):
        return []
    current = stats["sessions"][-1]
    counts = current.get("counts") or {}
    # dict в Python 3.7+ сохраняет порядок вставки — последние ключи = последние вызовы
    keys = list(counts.keys())
    return keys[-5:] if len(keys) >= 5 else keys

def _get_top5(stats):
    """Получить топ-5 команд по сумме вызовов за все сессии."""
    counts = {}
    for session in stats["sessions"]:
        for name, count in session["counts"].items():
            counts[name] = counts.get(name, 0) + count
    sorted_names = sorted(counts.keys(), key=lambda x: counts[x], reverse=True)
    top5 = sorted_names[:5]
    for name in stats["fallback_top5"]:
        if len(top5) >= 5:
            break
        if name not in top5:
            top5.append(name)
    return top5[:5]

def _build_context_hint(stats):
    """Построить подсказку о последних и популярных командах для system-сообщения."""
    last5 = []
    if stats.get("sessions"):
        current = stats["sessions"][-1]
        last5 = list(current.get("counts", {}).keys())[-5:]
    popular5 = _get_top5(stats)
    seen = set()
    names = []
    for n in last5 + popular5:
        if n not in seen and len(names) < 7:
            seen.add(n)
            names.append(n)
    if not names:
        return ""
    return f"Доступные команды: {', '.join(names)}. Используй выполнить_команду для известных имён, поиск_команд для остальных."


def _build_full_messages():
    """Собрать массив messages для LLM: базовый system + _messages (уже содержит system-блоки перед user).
    Используется и в /api/chat (для отправки), и в /api/history (для отображения полной истории).
    """
    full_messages = []
    # Базовый system-промпт (не трогаем, он первый)
    if _system_prompt:
        full_messages.append({"role": "system", "content": _system_prompt})
    # _messages уже содержит system-блоки перед каждым user (добавляются в do_GET)
    full_messages.extend(_messages)
    return full_messages

def _generate_context_hint():
    """Сгенерировать подсказку с дедуплицированным списком команд: 5 последних + топ-5 популярных."""
    try:
        _last5 = _get_last5(_stats)
        _top5 = _get_top5(_stats)
        _top5_names = _top5 if isinstance(_top5, list) else []
        # Исключения: мета-команды, которые и так видны модели через tools
        _excluded = {"диагностика", "справка", "список_команд"}
        # Объединяем: сначала последние, потом топ-5 дополняет недостающими
        _seen = set()
        _merged = []
        for _c in _last5 + _top5_names:
            if _c and _c not in _seen and _c not in _excluded:
                _seen.add(_c)
                _merged.append(_c)
        if _merged:
            _names_str = ", ".join([f'"{c}"' for c in _merged])
            return f"💡 Последние и популярные команды: {_names_str}."
    except Exception:
        pass
    return ""

def _build_tools(stats):
    """Построить массив tools для llama.cpp: только мета-команды (статично для KV-кэша)."""
    top5 = _get_top5(stats)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "поиск_команд",
                "description": "Найти команду anyjson по описанию задачи. Возвращает имя, параметры, пример.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "запрос": {"type": "string", "description": "Что нужно сделать (по-русски)"}
                    },
                    "required": ["запрос"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "выполнить_команду",
                "description": "Выполнить команду anyjson. Имя должно быть из списка известных команд.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "имя": {"type": "string", "description": "Имя команды (кириллица)"},
                        "аргументы": {"type": "object", "description": "Параметры команды"}
                    },
                    "required": ["имя"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "справка",
                "description": "Показать справку по УЖЕ ИЗВЕСТНОЙ команде: объяснить, что значат её параметры и как именно она работает. НЕ использовать для поиска команд по описанию задачи (для этого есть поиск_команд).", 
                "parameters": {
                    "type": "object",
                    "properties": {
                        "команда": {"type": "string", "description": "Имя команды или краткое описание (кириллица)"}
                    },
                    "required": ["команда"]
                }
            }
        }
    ]
    # Топ-5 команды убраны из tools.
    # Модель обязана использовать поиск_команд, выполнить_команду и справку,
    # так как только они имеют полные описания параметров (schemas).
    return tools

def _tool_to_command(name, args):
    """Развернуть вызов инструмента в JSON-команду для agent.py.

    поиск_команд -> диагностика(сервис=запрос)
    выполнить_команду -> <имя>(<аргументы>)
    справка -> справка(сервис=запрос)
    Остальные имена проходят напрямую.
    """
    if not isinstance(args, dict):
        args = {}
    if name == "справка":
        query = args.get("команда") or args.get("запрос") or args.get("имя") or args.get("сервис") or ""
        return json.dumps({"справка": {"команда": str(query)}}, ensure_ascii=False)
    if name == "поиск_команд":
        query = args.get("запрос") or args.get("query") or args.get("сервис") or ""
        return json.dumps({"диагностика": {"сервис": str(query)}}, ensure_ascii=False)
    if name == "выполнить_команду":
        cmd = args.get("имя") or args.get("name") or args.get("команда")
        inner = args.get("аргументы")
        if inner is None:
            inner = args.get("параметры") or args.get("params")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                logger.warning(f"[TOOL ROUTE] аргументы не JSON: {inner[:200]!r}")
                inner = {}
        if not isinstance(inner, dict):
            inner = {}
        if not inner:
            # Модель могла положить параметры рядом с именем, без вложенного объекта
            meta = {"имя", "name", "команда", "аргументы", "параметры", "params"}
            inner = {k: v for k, v in args.items() if k not in meta}
        if not isinstance(cmd, str) or not cmd.strip():
            return json.dumps({"эхо": {"текст": "⛔ выполнить_команду: не передано имя команды"}}, ensure_ascii=False)
        cmd = cmd.strip()
        if cmd == "выполнить_команду":
            return json.dumps({"эхо": {"текст": "⛔ выполнить_команду: рекурсивный вызов запрещён"}}, ensure_ascii=False)
        return json.dumps({cmd: inner}, ensure_ascii=False)
    return json.dumps({name: args}, ensure_ascii=False)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_load_web_ui().encode("utf-8"))
        elif parsed.path == "/api/history":
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            # Возвращаем полный массив messages со всеми system-блоками
            # (базовый system + динамические system перед каждым user)
            history_resp = {
                "messages": _build_full_messages()
            }
            self.wfile.write(json.dumps(history_resp, ensure_ascii=False).encode("utf-8"))
        elif parsed.path == "/api/chat":
            qs = parse_qs(parsed.query)
            msg = (qs.get("msg") or [""])[0].strip()
            logger.info(f"[CHAT] msg={msg[:200]!r}")
            # Перед добавлением user-сообщения генерируем и добавляем system-блок с подсказкой
            # Это фиксирует подсказку на момент отправки и улучшает попадание в KV-кэш
            context_hint = _generate_context_hint()
            if context_hint:
                _messages.append({"role": "system", "content": context_hint})
            _messages.append({"role": "user", "content": msg})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                iterations = 0
                force_text_only = False
                tools = _build_tools(_stats)
                logger.info(f"[TOOLS] передано модели {len(tools)} инструментов: {[t['function']['name'] for t in tools]}")
                logger.debug(f"[TOOLS] полное описание: {json.dumps(tools, ensure_ascii=False)}")
                while True:
                    iterations += 1
                    # Собираем полный массив messages через общую функцию
                    full_messages = _build_full_messages()
                    json_detected = None
                    assistant_text = []
                    for event in stream_llm(full_messages, tools=tools):
                        if event["type"] == "token":
                            assistant_text.append(event["content"])
                            ln = f"data: {json.dumps({'type': 'token', 'content': event['content']}, ensure_ascii=False)}\n\n"
                            self.wfile.write(ln.encode("utf-8"))
                            self.wfile.flush()
                        elif event["type"] == "thought":
                            ln = f"data: {json.dumps({'type': 'thought', 'content': event['content']}, ensure_ascii=False)}\n\n"
                            self.wfile.write(ln.encode("utf-8"))
                            self.wfile.flush()
                        elif event["type"] == "tool_call" and not force_text_only:
                            name = event["name"]
                            args = event["arguments"]
                            # Логируем полное описание вызова инструмента
                            logger.info(f"[TOOL] вызов демона: имя={name}, аргументы={args}")
                            json_detected = _tool_to_command(name, args)
                            break

                        elif event["type"] == "error":
                            ln = f"data: {json.dumps({'type': 'error', 'content': event['content']}, ensure_ascii=False)}\n\n"
                            self.wfile.write(ln.encode("utf-8"))
                            self.wfile.flush()
                            break
                    if assistant_text:
                        _messages.append({"role": "assistant", "content": "".join(assistant_text)})
                    
                    if not json_detected or force_text_only:
                        break
                    
                    logger.info(f"[TOOL] вызов демона: {json_detected[:100]}")
                    tool_result = _call_agent_daemon(json_detected)
                    logger.info(f"[TOOL] результат: {tool_result[:100]}")
                    
                    if iterations >= MAX_TOOL_ITERATIONS:
                        tool_result += "\n\nКоличество шагов исчерпано. Дай ответ пользователю, команды можно будет отправлять после если понадобится."
                        force_text_only = True
                    
                    _messages.append({"role": "tool", "content": tool_result})
                    ln = f"data: {json.dumps({'type': 'tool', 'content': tool_result}, ensure_ascii=False)}\n\n"
                    self.wfile.write(ln.encode("utf-8"))
                    self.wfile.flush()
                    
                    # Запись статистики
                    try:
                        cmd_obj = json.loads(json_detected)
                        cmd_name = list(cmd_obj.keys())[0]
                        _record_command(cmd_name, _stats)
                    except Exception:
                        pass
                        
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception as ex:
                logger.error(f"[SSE ERROR] {ex}")
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        try:
            msg = fmt % args if args else fmt
        except Exception:
            msg = fmt
        logger.info(f"HTTP {self.command} {self.path} - {msg}")

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def start():
    print(f"Запущен на http://127.0.0.1:{PORT}", flush=True)
    # Инициализация статистики команд
    global _stats
    _stats = _load_stats()
    new_session = {"id": time.strftime("%Y-%m-%dT%H:%M:%S"), "counts": {}}
    _stats["sessions"].append(new_session)
    if len(_stats["sessions"]) > 3:
        _stats["sessions"] = _stats["sessions"][-3:]
    _save_stats(_stats)
    # Сначала получаем system prompt
    _init_system_prompt()
    try:
        _ensure_agent_daemon()
    except Exception as e:
        print(f"[FATAL] Не удалось запустить agent.py: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    try:
        srv = ThreadedHTTPServer(("127.0.0.1", PORT), H)
    except OSError as e:
        print(f"[ERROR] bind {PORT} failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    logger.info(f"Запущен на http://127.0.0.1:{PORT}")
    print(f"[SUCCESS] serving {PORT}. Ctrl+C to stop gracefully.", flush=True)
    t = threading.Thread(target=srv.serve_forever, name="http-serve", daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(timeout=1.0)
    except KeyboardInterrupt:
        logger.info("Ctrl+C received -> graceful shutdown")
        print("[STOP] shutting down...", flush=True)
        srv.shutdown()
        srv.server_close()
        global _agent_process
        if _agent_process and _agent_process.poll() is None:
            _agent_process.terminate()
            _agent_process.wait(timeout=3)
    logger.info(f"Stopped. Port {PORT} released.")
    print("[STOP] done.", flush=True)
