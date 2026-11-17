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
import base64
from pathlib import Path

# Импорт функций сжатия истории
_COMPRESS_SCRIPT = Path(__file__).parent.parent / "side" / "external" / "squeeze" / "compress_history.py"

def _count_tokens_approx(messages):
    """Приблизительный подсчёт токенов: ~4 символа на токен для русского текста."""
    total_chars = 0
    for m in messages:
        c = m.get('content', '')
        if isinstance(c, list):
            for item in c:
                if isinstance(item, dict) and item.get('type') == 'text':
                    total_chars += len(item.get('text', ''))
        elif isinstance(c, str):
            total_chars += len(c)
        tc = m.get('tool_calls')
        if tc:
            total_chars += len(json.dumps(tc, ensure_ascii=False))
    return total_chars // 4

def _compress_history_inplace(messages, on_progress=None):
    """Сжимает массив messages на месте, возвращает новый массив."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump({'messages': messages}, f, ensure_ascii=False)
        tmp_in = f.name
    tmp_out = tmp_in + '.compressed.json'
    
    try:
        cmd = [sys.executable, str(_COMPRESS_SCRIPT), tmp_in, tmp_out, '--fp16']
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                text=True, encoding='utf-8', errors='replace', bufsize=1, env=env)
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith('[i]') and '%' in line:
                    if on_progress:
                        on_progress(line)
            proc.wait()
        except BaseException:
            # Обрыв клиента (F5) или Ctrl+C: гасим дочерний процесс сжатия,
            # иначе он остаётся сиротой с загруженной моделью в RAM.
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            raise
        
        if proc.returncode == 0 and os.path.exists(tmp_out):
            with open(tmp_out, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('messages', messages)
        else:
            logger.warning(f"[COMPRESS] Сжатие не удалось, код {proc.returncode}")
            return messages
    finally:
        for p in (tmp_in, tmp_out):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                # Windows: файл может быть ещё открыт дочерним процессом.
                # Не маскируем исходное исключение (обрыв клиента) ошибкой unlink.
                pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("AnyJSON-Web")

LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLM_PROPS_URL = "http://127.0.0.1:8080/props"
LLM_CONTEXT_SIZE = None  # Заполняется при старте из /props
PORT = 8081
AGENT_NAME = "Аниджсон"
MAX_TOOL_ITERATIONS = 15
def _get_compress_thresholds():
    """Пороги сжатия: 33% от контекста llama.cpp (или 4000 если не получен)."""
    ctx = LLM_CONTEXT_SIZE if LLM_CONTEXT_SIZE else 4096
    base = int(ctx * 0.33)
    return base, int(base * 1.36)

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
    # Протокол инструментов: позитивные правила работы и паттерны вызова
    protocol = (
        "\n\nПРАВИЛА РАБОТЫ АГЕНТА:\n"
        "1. Действуй сразу: если нужен инструмент — вызывай его в этом же ходу без вводных фраз и обещаний.\n"
        "2. Для любой задачи ищи готовый инструмент через поиск_команд(запрос='...') и вызывай его через выполнить_команду.\n"
        "3. Используй долговременную память агента: сохраняй важные факты через выполнить_команду(имя='запомнить', ...) и проверяй через вспомнить.\n"
        "4. Ты напрямую видишь прикреплённые изображения (vision). НИКОГДА не описывай, что нарисовано на картинке (собеседник видит её сам), если тебя об этом прямо не попросили — сразу давай живую реакцию, суть, решение или ответ по смыслу.\n"
        "5. Отвечай кратко и по сути — только итог и результат, без пересказа списка вызванных команд.\n"
        "\nИНСТРУМЕНТЫ И ПАТТЕРНЫ:\n"
        "- Прямых инструментов три: поиск_команд, выполнить_команду и справка. Все прикладные команды запускаются ТОЛЬКО через выполнить_команду(имя='...', аргументы={...}).\n"
        "- Можно вызывать несколько инструментов за один ход.\n"
        "- Базовые команды (через выполнить_команду):\n"
        "  • чтение файла: имя='прочитать_полностью', аргументы={'путь': '...'};\n"
        "  • поиск файлов: имя='найти_файл', аргументы={'имя': '...', 'путь': '...'};\n"
        "  • поиск по тексту: имя='осмотреть', аргументы={'путь': '...', 'ключевые_слова': '...'};\n"
        "  • правка файла: имя='заменить_текст', аргументы={'путь': '...', 'найти': '...', 'заменить': '...'} (сначала прочитай файл для точного якоря);\n"
        "  • скрипты и расчёты: имя='питон', аргументы={'скрипт': '...'} (передавай чистый код строкой, без маркеров <<< и >>>).\n"
        "- При ошибке инструмента анализируй текст ошибки и меняй подход, не повторяя тот же вызов вслепую.\n"
        "\nСРЕДА:\n"
        "- ОС: Windows, рабочая директория: E:\\Jericho. Предпочитай встроенные команды и питон; в PowerShell используй here-strings и кодировку utf8NoBOM.\n"
        "- Язык ответов: русский (кроме кода и технических терминов)."
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

def _call_agent_daemon(json_text, on_progress=None):
    global _agent_process
    _ensure_agent_daemon()
    with _agent_lock:
        try:
            _agent_process.stdin.write(json_text + "\n")
            _agent_process.stdin.flush()            # Демон шлёт эфемерные {"progress": ...} во время работы команды;
            # финальный ответ - строка со "status". Прогресс наружу через callback.
            while True:
                response_line = _agent_process.stdout.readline()
                if not response_line:
                    raise RuntimeError("Демон закрыл stdout")
                resp = json.loads(response_line)
                if "status" not in resp and "progress" in resp:
                    if on_progress:
                        on_progress(resp["progress"])
                    continue
                break
            if resp.get("status") == "error":
                return f"[AGENT ERROR] {resp.get('error')}"
            clipboard = resp.get("clipboard", "").strip()
            output = resp.get("output", "").strip()
            
            # Перехват спец. маркера для обработки изображения сервером (base64 + даунскейл)
            if output.startswith('{"_server_image_process":'):
                try:
                    import json as _json
                    import os as _os
                    import base64
                    _img_data = _json.loads(output)
                    _img_path = _img_data.get("_server_image_process")
                    if _img_path and _os.path.exists(_img_path):
                        with open(_img_path, "rb") as _f:
                            _raw_b64 = base64.b64encode(_f.read()).decode('utf-8')
                        _data_url = f"data:image/jpeg;base64,{_raw_b64}"
                        _ok, _final_url = _prepare_image(_data_url)
                        if _ok:
                            output = f"🖼️ Изображение обработано и передано модели.\nData URL: {_final_url}"
                        else:
                            output = f"❌ Ошибка обработки изображения: {_final_url}"
                except Exception as _e:
                    output = f"❌ Ошибка сервера при обработке изображения: {_e}"

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
    # --- Stream кончился: разбираем все накопленные вызовы ---
    parsed_calls = []
    for idx in tc_order:
        slot = tc_acc[idx]
        if not slot["name"]:
            continue
        raw_args = slot["arguments"].strip()
        logger.info(f"[TOOL RAW] {slot['name']} finish={finish} args={raw_args[:300]!r}")
        args = {}
        parse_err = None
        if raw_args:
            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    logger.error(f"[TOOL PARSE] {slot['name']}: не объект, а {type(args).__name__}")
                    parse_err = f"❌ Ошибка JSON в аргументах {slot['name']}: ожидался JSON-объект, получен {type(args).__name__}. Исправь формат аргументов и повтори вызов."
                    args = {}
            except json.JSONDecodeError as e:
                logger.error(f"[TOOL PARSE] {slot['name']}: {e}")
                parse_err = f"❌ Ошибка синтаксиса JSON в аргументах {slot['name']}: {e}. Проверь закрывающие кавычки и скобки (для большого текста разбивай правку на короткие части) и повтори вызов."
        else:
            logger.warning(f"[TOOL] {slot['name']}: аргументы пусты у самой модели")
        parsed_calls.append({"name": slot["name"], "arguments": args, "id": slot["id"], "raw_arguments": raw_args, "parse_error": parse_err})
    if parsed_calls:
        if len(parsed_calls) > 1:
            logger.info(f"[TOOL] модель прислала {len(parsed_calls)} вызовов за ход, исполняю пакетом")
        yield {"type": "tool_calls", "calls": parsed_calls}

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
    """Загрузить статистику команд из JSON с автоматической очисткой ключей от пробелов."""
    path = Path(__file__).parent / "command_stats.json"
    fallback = {"sessions": [], "fallback_top5": ["время", "читать", "профиль_свой", "список_файлов", "эхо"]}
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Нормализуем ключи, удаляя пробелы
        clean_data = {}
        for k, v in data.items():
            clean_k = k.strip()
            if clean_k == "sessions":
                clean_sessions = []
                for s in v:
                    clean_s = {ck.strip(): cv for ck, cv in s.items()}
                    if "counts" in clean_s:
                        clean_s["counts"] = {ck.strip(): cv for ck, cv in clean_s["counts"].items()}
                    clean_sessions.append(clean_s)
                clean_data[clean_k] = clean_sessions
            elif clean_k == "fallback_top5":
                clean_data[clean_k] = [str(item).strip() for item in v]
            else:
                clean_data[clean_k] = v
        return clean_data
    except Exception as e:
        logger.warning(f"[STATS] Ошибка чтения command_stats.json: {e}")
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
    # Используем новый фолбэк, игнорируя старый из файла
    fallback_top5 = ["время", "читать", "профиль_свой", "список_файлов", "эхо"]
    for name in fallback_top5:
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
        # Если после исключения осталось меньше 5, дополняем из расширенного фолбэка
        if len(_merged) < 5:
            _extended_fallback = ["время", "читать", "профиль_свой", "список_файлов", "эхо", "заполнить_профиль", "скачать", "переписать"]
            for _c in _extended_fallback:
                if len(_merged) >= 5:
                    break
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
                "description": "Найти команду по описанию задачи. Возвращает имя, параметры, пример.",
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
                "description": "Выполнить команду. Имя команды — кириллица. Если точное имя неизвестно, пробуй по предположению (например, «читать», «найти», «создать», «запуск»); при ошибке демон вернёт подсказку с правильным именем.",
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
                "description": "Показать ДОКУМЕНТАЦИЮ по УЖЕ ИЗВЕСТНОЙ команде: параметры, примеры, поведение. НЕ выполняет команду и НЕ возвращает данные файлов. Для выполнения — выполнить_команду. Для поиска — поиск_команд.", 
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
        if cmd == "прочитать_полностью":
            inner["веб"] = True
        if cmd == "питон" and isinstance(inner.get("скрипт"), str):
            _sc = inner["скрипт"].strip()
            if _sc.startswith("<<<"):
                _sc = _sc[3:].lstrip("\r\n")
            if _sc.endswith(">>>"):
                _sc = _sc[:-3].rstrip("\r\n")
            inner["скрипт"] = _sc
        return json.dumps({cmd: inner}, ensure_ascii=False)
    return json.dumps({name: args}, ensure_ascii=False)


def _prepare_image(data_url):
    """Валидация + предобработка dataURL: даунскейл до 0.5 МПикс (PIL LANCZOS).
    Перенкод в JPEG q90 - только если ресайз реально был; иначе passthrough.
    Возвращает (ok, normalized_data_url)."""
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        return False, None
    if len(data_url) > 16 * 1024 * 1024:
        return False, None
    try:
        header, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        MAX_PIX = 500_000
        if w * h > MAX_PIX:
            scale = (MAX_PIX / float(w * h)) ** 0.5
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            im = im.resize((nw, nh), Image.LANCZOS)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=90)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            header = "data:image/jpeg;base64"
            logger.info(f"[IMG] {w}x{h} -> {nw}x{nh} (даунскейл 0.5 МПикс)")
        return True, header + "," + b64
    except Exception as e:
        logger.error(f"[IMG] предобработка не удалась: {e}")
        return False, None

def _msg_text(m):
    """Текст сообщения независимо от формата content (str или parts)."""
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return c or ""

class H(BaseHTTPRequestHandler):
    def _safe_write(self, data_str):
        """Безопасная запись в SSE: ловит отключение клиента (WinError 10053) и возвращает False."""
        try:
            self.wfile.write(f"data: {data_str}\n\n".encode("utf-8"))
            self.wfile.flush()
            return True
        except (ConnectionAbortedError, BrokenPipeError, OSError) as e:
            logger.info(f"[SSE] Клиент отключился: {e}")
            return False

    def _compress_history_now(self, reason):
        """Сжать историю прямо сейчас, с прогрессом в SSE. Клиент должен быть подключён."""
        logger.info(f"[COMPRESS] {reason}")
        def _on_compress_progress(text):
            if not self._safe_write(json.dumps({'type': 'progress', 'content': f"🗜️ Сжатие истории: {text}"}, ensure_ascii=False)):
                raise ConnectionAbortedError("Клиент отключился")
        _on_compress_progress("Запущено сжатие...")
        _messages[:] = _compress_history_inplace(_messages, on_progress=_on_compress_progress)
        new_token_count = _count_tokens_approx(_messages)
        logger.info(f"[COMPRESS] Сжатие завершено. Стало: {new_token_count} токенов.")
        self._safe_write(json.dumps({'type': 'history_compressed', 'tokens': new_token_count}, ensure_ascii=False))
        _on_compress_progress(f"✅ Готово! История сжата до {new_token_count} токенов.")
        return new_token_count

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            # Иконка задана data-URL в <head>; прямой запрос гасим без 404-шума в логе.
            self.send_response(204)
            self.end_headers()
            return
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
            _full_hist = _build_full_messages()
            _thr, _ = _get_compress_thresholds()
            history_resp = {
                "messages": _full_hist,
                "tokens": _count_tokens_approx(_full_hist),
                "thr": _thr,
                "max": LLM_CONTEXT_SIZE or 4096
            }
            self.wfile.write(json.dumps(history_resp, ensure_ascii=False).encode("utf-8"))
        elif parsed.path == "/api/reset" and self.command == "POST":
            global _messages
            _messages = []
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        elif parsed.path == "/api/edit_message":
            # Редактирование сообщения оператора по старому тексту и обрезка истории
            qs = parse_qs(parsed.query)
            old_text = (qs.get("old_text") or [""])[0]
            new_text = (qs.get("new_text") or [""])[0]
            
            # Находим последнее user-сообщение с old_text
            target_idx = -1
            for i in range(len(_messages) - 1, -1, -1):
                if _messages[i].get("role") == "user" and _msg_text(_messages[i]) == old_text:
                    target_idx = i
                    break
            
            if target_idx == -1:
                self.send_error(400, "Message not found")
                return
            
            # Обрезаем историю до этого сообщения
            _messages[:] = _messages[:target_idx]
            # Добавляем отредактированное сообщение
            _messages.append({"role": "user", "content": new_text})
            
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8"))
        elif parsed.path == "/api/chat":
            qs = parse_qs(parsed.query)
            msg = (qs.get("msg") or [""])[0].strip()
            logger.info(f"[CHAT GET] msg={msg[:200]!r}")
            try:
                self._stream_chat(msg, None)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                logger.info("[SSE] Клиент отключился (GET) — обрыв штатный.")
        else:
            self.send_error(404)

    def do_POST(self):
        """POST /api/chat {msg, images:[dataURL]} - картинки в чат.
        EventSource (GET) не тянет base64, поэтому транспорт для изображений - POST."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/reset":
            global _messages
            _messages = []
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
            return
        if parsed.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            self.send_error(400)
            return
        if length > 48 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.send_error(400)
            return
        msg = str(body.get("msg") or "").strip()
        raw_imgs = body.get("images")
        raw_imgs = raw_imgs if isinstance(raw_imgs, list) else []
        pass
        images = []
        for u in raw_imgs[:4]:
            ok, prepared = _prepare_image(u)
            if not ok:
                self.send_error(400)
                return
            images.append(prepared)
        logger.info(f"[CHAT POST] msg={msg[:200]!r} images={len(images)}")
        try:
            self._stream_chat(msg, images)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Обрыв может прилететь из фазы сжатия (она вне try внутри _stream_chat).
            logger.info("[SSE] Клиент отключился (POST) — обрыв штатный.")

    def _stream_chat(self, msg, images):
        """Цикл модель<->демон. images - список предобработанных dataURL или None."""
        context_hint = _generate_context_hint()
        if context_hint:
            _messages.append({"role": "system", "content": context_hint})
        continue_hint = None
        if images:
            content = [{"type": "text", "text": msg or "Отреагируй по существу (не описывай картинку, сразу суть или реакцию)."}]
            content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
            _messages.append({"role": "user", "content": content})
        elif msg:
            _messages.append({"role": "user", "content": msg})
        elif _messages and _messages[-1].get("role") == "assistant":
            continue_hint = {"role": "system", "content": "Продолжи ответ с того места, где остановился, не повторяя уже написанный текст."}
        
        # Отправляем заголовки СРАЗУ: клиент получает SSE-поток до первого токена модели
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
                full_messages = _build_full_messages()
                if continue_hint and iterations == 1:
                    full_messages = full_messages + [continue_hint]
                pending_calls = []
                assistant_text = []
                for event in stream_llm(full_messages, tools=tools):
                    if event["type"] == "token":
                        assistant_text.append(event["content"])
                        if not self._safe_write(json.dumps({'type': 'token', 'content': event['content']}, ensure_ascii=False)):
                            return
                    elif event["type"] == "thought":
                        if not self._safe_write(json.dumps({'type': 'thought', 'content': event['content']}, ensure_ascii=False)):
                            return
                    elif event["type"] == "tool_calls" and not force_text_only:
                        pending_calls = event["calls"]
                        break
                    elif event["type"] == "error":
                        if not self._safe_write(json.dumps({'type': 'error', 'content': event['content']}, ensure_ascii=False)):
                            return
                        break
                if not pending_calls or force_text_only:
                    if assistant_text:
                        _messages.append({"role": "assistant", "content": "".join(assistant_text)})
                    break
                tc_payload, prepared_calls = [], []
                for ci, call in enumerate(pending_calls, 1):
                    c_id = call.get("id") or f"call_{iterations}_{ci}"
                    c_name, c_args = call["name"], call["arguments"]
                    p_err = call.get("parse_error")
                    logger.info(f"[TOOL] вызов демона ({ci}/{len(pending_calls)}): имя={c_name}, parse_err={bool(p_err)}")
                    tc_payload.append({"id": c_id, "type": "function", "function": {"name": c_name, "arguments": json.dumps(c_args, ensure_ascii=False) if isinstance(c_args, dict) else str(c_args)}})
                    prepared_calls.append((c_id, None if p_err else _tool_to_command(c_name, c_args), p_err))
                _messages.append({"role": "assistant", "content": "".join(assistant_text) if assistant_text else None, "tool_calls": tc_payload})
                def _on_progress(text):
                    if not self._safe_write(json.dumps({'type': 'progress', 'content': text}, ensure_ascii=False)):
                        raise ConnectionAbortedError("Клиент отключился")
                for c_id, json_detected, p_err in prepared_calls:
                    if p_err:
                        tool_result = p_err
                    else:
                        logger.info(f"[TOOL] вызов демона: {json_detected[:100]}")
                        tool_result = _call_agent_daemon(json_detected, on_progress=_on_progress)
                        logger.info(f"[TOOL] результат: {tool_result[:100]}")
                    try:
                        _cmd_obj = json.loads(json_detected)
                        _cmd_name = next(iter(_cmd_obj.keys()), "")
                        if _cmd_name == "питон":
                            _body = (tool_result or "").strip()
                            tool_result = f"Выполнен агентский скрипт:\n{_body}" if _body else "Выполнен агентский скрипт (без вывода)."
                    except Exception:
                        pass
                    _messages.append({"role": "tool", "tool_call_id": c_id, "content": tool_result})
                    if not self._safe_write(json.dumps({'type': 'tool', 'content': tool_result}, ensure_ascii=False)):
                        return
                    try:
                        cmd_obj = json.loads(json_detected)
                        _record_command(list(cmd_obj.keys())[0], _stats)
                    except Exception:
                        pass
                _, thrconsize_force = _get_compress_thresholds()
                # Принудительное сжатие посреди хода: контекст после инструмента раздулся
                # выше 75%*1.36 — сжимаем ДО следующего обращения к модели.
                if _count_tokens_approx(_build_full_messages()) > thrconsize_force:
                    self._compress_history_now(f"Контекст после инструмента > {thrconsize_force} (33%*1.36), принудительное сжатие...")
                # Счётчик после каждого инструмента: считает сервер, клиент только отображает.
                # thr/max - тройка для счётчика UI в формате 5К/10К/32К.
                _thr, _ = _get_compress_thresholds()
                self._safe_write(json.dumps({'type': 'tokens', 'count': _count_tokens_approx(_build_full_messages()), 'thr': _thr, 'max': LLM_CONTEXT_SIZE or 4096}, ensure_ascii=False))
                try:
                    cmd_obj = json.loads(json_detected)
                    cmd_name = list(cmd_obj.keys())[0]
                    _record_command(cmd_name, _stats)
                except Exception:
                    pass
            # Сжатие ПОСЛЕ последнего ответа ассистента: ответ уже доставлен,
            # клиент ещё подключён ([DONE] не отправлен) — прогресс и счётчик доходят до UI.
            thrconsize, _ = _get_compress_thresholds()
            full_msgs = _build_full_messages()
            token_count = _count_tokens_approx(full_msgs)
            if token_count > thrconsize:
                self._compress_history_now(f"История {token_count} токенов > {thrconsize}, сжимаю после ответа...")
                thrconsize, _ = _get_compress_thresholds()
            # Финальный счётчик хода перед [DONE]: клиент не сканирует историю сам.
            # thr/max - тройка для счётчика UI в формате 5К/10К/32К.
            self._safe_write(json.dumps({'type': 'tokens', 'count': _count_tokens_approx(_build_full_messages()), 'thr': thrconsize, 'max': LLM_CONTEXT_SIZE or 4096}, ensure_ascii=False))
            self._safe_write("[DONE]")
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Клиент ушёл (F5/закрытая вкладка) — штатный обрыв, не ошибка сервера.
            logger.info("[SSE] Клиент отключился — обрыв штатный.")
        except Exception as ex:
            logger.error(f"[SSE ERROR] {ex}")

    def log_message(self, fmt, *args):
        try:
            msg = fmt % args if args else fmt
        except Exception:
            msg = fmt
        logger.info(f"HTTP {self.command} {self.path} - {msg}")

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def _query_llm_context_size():
    """Запросить размер контекста у llama.cpp при старте сервера."""
    global LLM_CONTEXT_SIZE
    try:
        req = urllib.request.Request(LLM_PROPS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # n_ctx в новых версиях llama.cpp лежит внутри default_generation_settings
            gen_settings = data.get("default_generation_settings", {})
            raw = gen_settings.get("n_ctx") or data.get("context_length") or data.get("n_ctx_train")
            if raw and isinstance(raw, (int, float)) and raw > 0:
                LLM_CONTEXT_SIZE = int(raw)
            else:
                LLM_CONTEXT_SIZE = 4096
            logger.info(f"[INIT] llama.cpp context size: {LLM_CONTEXT_SIZE} токенов (найдено: {raw})")
    except Exception as e:
        LLM_CONTEXT_SIZE = 4096
        logger.warning(f"[INIT] Не удалось получить context size от llama.cpp: {e}, использую {LLM_CONTEXT_SIZE}")

def start():
    print(f"Запущен на http://127.0.0.1:{PORT}", flush=True)
    # Инициализация статистики команд
    global _stats
    _stats = _load_stats()
    
    # Получаем размер контекста от llama.cpp
    _query_llm_context_size()
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
    
    # Явный обработчик Ctrl+C для Windows
    def _sig_handler(signum, frame):
        logger.info("Ctrl+C received -> graceful shutdown")
        print("\n[STOP] shutting down...", flush=True)
        try:
            srv.shutdown()
            srv.server_close()
        except Exception as e:
            logger.error(f"Shutdown error: {e}")
        # Принудительное завершение через 2 секунды, если shutdown завис
        import threading, time, os
        def _force_exit():
            time.sleep(2)
            os._exit(0)
        threading.Thread(target=_force_exit, daemon=True).start()
    
    import signal
    signal.signal(signal.SIGINT, _sig_handler)
    
    t = threading.Thread(target=srv.serve_forever, name="http-serve", daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(timeout=1.0)
    except KeyboardInterrupt:
        _sig_handler(signal.SIGINT, None)
        global _agent_process
        if _agent_process and _agent_process.poll() is None:
            _agent_process.terminate()
            _agent_process.wait(timeout=3)
    logger.info(f"Stopped. Port {PORT} released.")
    print("[STOP] done.", flush=True)
