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
                for _ln in text.splitlines():
                    # Сначала счищаем ANSI-коды: невидимые escape-последовательности
                    # не должны спасать служебные строки от фильтра.
                    _s = _ANSI_RE.sub("", _ln).strip()
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
    """Стрим SSE из llama.cpp. Парсит tool_calls из ответа, fallback на парсинг JSON из content."""
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
    
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        LLM_URL, data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    buffer = []
    in_think = False
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in io.TextIOWrapper(resp, encoding="utf-8"):
                line = raw.strip()
                if not line.startswith("data: "):
                    continue
                ds = line[6:]
                if ds == "[DONE]":
                    break
                try:
                    obj = json.loads(ds)
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    # Tool calls
                    tool_calls = delta.get("tool_calls", [])
                    if tool_calls:
                        for tc in tool_calls:
                            if tc.get("type") == "function":
                                fn = tc.get("function", {})
                                name = fn.get("name", "")
                                args_str = fn.get("arguments", "")
                                try:
                                    args = json.loads(args_str) if args_str else {}
                                except json.JSONDecodeError:
                                    args = {}
                                yield {"type": "tool_call", "name": name, "arguments": args}
                                return
                    # Thinking-токены
                    thinking_tok = delta.get("reasoning_content", "") or delta.get("thinking", "")
                    content_tok = delta.get("content", "")
                    if thinking_tok:
                        yield {"type": "thought", "content": thinking_tok}
                    if content_tok:
                        if "<think>" in content_tok or "<thinking>" in content_tok:
                            in_think = True
                        yield_type = "thought" if in_think else "token"
                        yield {"type": yield_type, "content": content_tok}
                        if "</think>" in content_tok or "</thinking>" in content_tok:
                            in_think = False
                except json.JSONDecodeError:
                    continue
    except Exception as ex:
        logger.error(f"[LLM ERROR] {ex}")
        yield {"type": "error", "content": str(ex)}

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
    fallback = {"sessions": [], "fallback_top5": ["время", "справка", "профиль_свой", "список_команд", "эхо"]}
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

def _build_tools(stats):
    """Построить массив tools для llama.cpp: мета-команды + топ-5 частых."""
    top5 = _get_top5(stats)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_commands",
                "description": "Найти команду anyjson по описанию задачи. Возвращает имя, параметры, пример.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Что нужно сделать (по-русски)"}
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Выполнить команду anyjson. Имя должно быть из списка известных команд.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Имя команды (кириллица)"},
                        "arguments": {"type": "object", "description": "Параметры команды"}
                    },
                    "required": ["name"]
                }
            }
        }
    ]
    for name in top5:
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": f"Команда anyjson: {name}",
                "parameters": {"type": "object", "properties": {}}
            }
        })
    return tools

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
            history_resp = {
                "system": _system_prompt or "",
                "messages": _messages.copy()
            }
            self.wfile.write(json.dumps(history_resp, ensure_ascii=False).encode("utf-8"))
        elif parsed.path == "/api/chat":
            qs = parse_qs(parsed.query)
            msg = (qs.get("msg") or [""])[0].strip()
            logger.info(f"[CHAT] msg={msg[:200]!r}")
            _messages.append({"role": "user", "content": msg})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                iterations = 0
                force_text_only = False
                tools = _build_tools(_stats)
                while True:
                    iterations += 1
                    full_messages = [{"role": "system", "content": _system_prompt or "Ты — AI-ассистент."}]
                    full_messages.extend(_messages)
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
                            if name == "search_commands":
                                json_detected = json.dumps({"диагностика": {"сервис": args.get("query", "")}}, ensure_ascii=False)
                            elif name == "execute_command":
                                cmd_name = args.get("name", "")
                                cmd_args = args.get("arguments", {})
                                json_detected = json.dumps({cmd_name: cmd_args}, ensure_ascii=False)
                            else:
                                json_detected = json.dumps({name: args}, ensure_ascii=False)
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
