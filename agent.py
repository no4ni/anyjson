"""Точка входа агента: поддерживает интерактивный режим и режим демона (--daemon).
В режиме демона читает JSON из stdin, выполняет команду, пишет JSON-ответ в stdout.
"""
import io
import json
import os
import sys
import threading
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE = _HERE / "_core"
for _p in (str(_CORE), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

_LAST_AGENT_FILE = _HERE / ".last_agent"

def _get_last_agent():
    try:
        if _LAST_AGENT_FILE.exists():
            return _LAST_AGENT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None

def _save_last_agent(name):
    try:
        _LAST_AGENT_FILE.write_text(name, encoding="utf-8")
    except Exception:
        pass

def _init_agent_core(agent_name):
    """Инициализация ядра (вызывается один раз)."""
    sys.argv = [sys.argv[0], agent_name]
    
    _init_log = io.StringIO()
    try:
        with redirect_stdout(_init_log), redirect_stderr(_init_log):
            import asyncio
            import atexit
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            from _core._safe_subprocess import install_guard as _install_guard
            _install_guard()

            from _config import (
                AGENT_NAME, BASE_DIR, TOOLS_DIR, PUBLIC_TOOLS_DIR,
                WIKI_TEXTS_DIR, _WIKIGET_PATH, API_KEYS, MY_DIR,
            )
            from _actions import cmd_parser as _cmd_parser
            from _actions import load_all
            from _executor import ensure_loaded
            from _storage import load_processed_messages, save_processed_messages, check_inbox, load_reminders, reminder_checker
            from _tor import cleanup_tor

            from _actions._tools_core import init_tools as _init_tools
            _init_tools(
                BASE_DIR, AGENT_NAME,
                wiki_texts_dir=WIKI_TEXTS_DIR,
                tools_dir=TOOLS_DIR,
                public_tools_dir=PUBLIC_TOOLS_DIR,
                wikiget_path=_WIKIGET_PATH,
            )

            _mb_key = API_KEYS.get("moltbook")
            if _mb_key:
                try:
                    from _actions._tools_moltbook import init_moltbook
                    init_moltbook(_mb_key)
                except Exception:
                    pass

            load_processed_messages()
            atexit.register(cleanup_tor)
            atexit.register(save_processed_messages)
            
            # Фоновые задачи
            load_reminders()
            threading.Thread(target=reminder_checker, daemon=True).start()

            ensure_loaded()
            load_all()
            
            # Тихая проверка inbox при старте демона
            _inbox_capture = io.StringIO()
            _old_stdout = sys.stdout
            try:
                sys.stdout = _inbox_capture
                check_inbox()
            finally:
                sys.stdout = _old_stdout
                
    except SystemExit:
        sys.stderr.write(_init_log.getvalue())
        raise
    except BaseException as _e:
        import traceback
        sys.stderr.write(_init_log.getvalue())
        print(f"❌ Инициализация не выполнена: {repr(_e)}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(3)

def run_daemon(agent_name):
    """Бесконечный цикл чтения из stdin и ответа в stdout."""
    _init_agent_core(agent_name)
    sys.stderr.write(f"[DAEMON] Агент '{agent_name}' запущен и готов.\n")
    sys.stderr.flush()
    
    # Перехватываем stdout команды, чтобы вернуть его как JSON
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
            
        try:
            # Валидация входного JSON
            cmd_obj = json.loads(line)
            if not isinstance(cmd_obj, dict) or len(cmd_obj) != 1:
                raise ValueError("Ожидается объект с одним ключом")
        except Exception as e:
            resp = {"status": "error", "error": f"Невалидный JSON на входе: {e}"}
            print(json.dumps(resp, ensure_ascii=False))
            sys.stdout.flush()
            continue

        # Выполняем команду
        ans = io.StringIO()
        try:
            with redirect_stdout(ans):
                # process_command_input может вызвать sys.exit, перехватываем
                from _actions import cmd_parser as _cmd_parser
                _cmd_parser.process_command_input(line)
        except SystemExit as _e:
            pass # Игнорируем exit внутри команд, продолжаем работу демона
        except Exception as _e:
            import traceback
            ans.write(f"❌ Ошибка выполнения: {repr(_e)}\n")
            traceback.print_exc(file=ans)

        output_text = ans.getvalue().strip()
        
        # Проверяем буфер обмена (для команд типа прочитать_полностью)
        clipboard_text = ""
        try:
            import pyperclip
            clipboard_text = pyperclip.paste() or ""
        except Exception:
            pass

        resp = {
            "status": "ok",
            "output": output_text,
            "clipboard": clipboard_text
        }
        print(json.dumps(resp, ensure_ascii=False))
        sys.stdout.flush()

def main():
    args = sys.argv[1:]
    if "--daemon" in args:
        # Режим демона: имя берём из .last_agent или аргумента
        agent_name = _get_last_agent() or "Аниджсон"
        for i, arg in enumerate(args):
            if arg != "--daemon" and not arg.startswith("-"):
                agent_name = arg
                _save_last_agent(agent_name)
                break
        run_daemon(agent_name)
    else:
        # Одноразовый режим: выводим приветствие и инструкцию (для получения system-промпта веб-сервером)
        _name_parts = [a for a in args if not a.startswith("-")]
        _agent_name = " ".join(_name_parts).strip() or _get_last_agent() or "Аниджсон"
        if _name_parts:
            _save_last_agent(_agent_name)
        
        _bio = ""
        _profile_path = _HERE / "profiles" / _agent_name / "info.json"
        if _profile_path.exists():
            try:
                _prof = json.loads(_profile_path.read_text(encoding="utf-8"))
                _bio = _prof.get("bio", "")
                if "Протокол Нить" in _bio:
                    _bio = ""
            except Exception:
                pass
        
        _greeting = f"Ты - агент {_agent_name}."
        if _bio:
            _greeting += f" {_bio}"
        if len(_greeting.encode('utf-8')) > 5120:
            _greeting = _greeting[:5000] + "\n..."
        print(_greeting)
        sys.exit(0)

if __name__ == "__main__":
    main()