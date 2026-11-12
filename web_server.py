"""
E:/Jericho/Private/anyjson/web_server.py
Минимальная обёртка: просто общение с llama.cpp, БЕЗ инструментов.
Запуск: py E:/Jericho/Private/anyjson/web_server.py
"""
import sys
import os
from pathlib import Path

# Пути: локальные модулы в приоритете
_SCRIPT_DIR = Path(__file__).resolve().parent
_CORE_DIR = _SCRIPT_DIR / "_core"
for _p in (str(_CORE_DIR), str(_SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    os.chdir(_SCRIPT_DIR)
except Exception:
    pass

# Тяжёлые импорты (_config/_actions/_storage) НЕ тянем — они не нужны для чата.
from _core import _server

print(f"Core loaded from: {_SCRIPT_DIR}", flush=True)

if __name__ == "__main__":
    try:
        _server.start()
    except Exception as e:
        print(f"FATAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
