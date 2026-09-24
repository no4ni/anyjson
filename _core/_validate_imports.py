"""Валидация импортов в _actions/*.py.

Запрещает импорты из внешних папок (старый tools.py, чужие модули).
Разрешает: stdlib, наши модули, известные сторонние пакеты.
"""
import ast
import pathlib
import sys


# --- разрешённые «корневые» наши модули ---
ALLOWED_OUR = {
    "_config", "_utils", "_executor", "_storage", "_tor", "_ps",
    "_color", "_web", "_validate",
    "cmd_parser", "_command",
    "_actions", "_core",
}

# --- известные сторонние пакеты ---
ALLOWED_THIRD = {
    "tqdm", "requests", "bs4", "PIL", "numpy", "sklearn", "psutil",
    "wmi", "pythoncom", "urllib3", "pyperclip",
    "ijson", "aiofiles", "playwright", "cryptography",
    "win32clipboard", "win32con", "magic",
    # медиа
    "yt_dlp", "moviepy", "cv2", "imageio", "imageio_ffmpeg",
    "sounddevice", "pydub", "whisper",
    # поиск / API
    "ddgs_tool", "duckduckgo_search", "newsapi", "newsdata",
    "weatherapi", "tavily", "wolframalpha", "wikipedia",
    # LLM / langchain
    "langchain_community", "langchain_core", "langchain",
    "openai", "anthropic", "ollama", "transformers",
    # прочее
    "docker", "click", "rich", "colorama", "tabulate",
    "dateutil", "pytz", "regex", "lxml", "html5lib",
    "svglib", "reportlab",
    "yaml", "toml", "jsonschema", "aiohttp", "httpx",
    # rembg и его зависимости
    "rembg", "onnxruntime", "pymatting", "skimage", "pooch",
}

# --- стандартная библиотека (достаточно верхнего уровня) ---
ALLOWED_STDLIB = {
    "os", "sys", "json", "re", "time", "datetime", "pathlib",
    "subprocess", "threading", "random", "hashlib", "base64",
    "tempfile", "shutil", "math", "collections", "functools",
    "itertools", "typing", "io", "urllib", "xml", "csv", "sqlite3",
    "zipfile", "tarfile", "unicodedata", "secrets", "hmac",
    "contextlib", "concurrent", "asyncio", "logging", "string",
    "ast", "glob", "uuid", "copy", "pickle", "struct", "socket",
    "email", "http", "html", "webbrowser", "platform", "getpass",
    "warnings", "traceback", "inspect", "importlib", "pkgutil",
    "unittest", "sqlite3", "statistics", "textwrap", "shlex",
    "decimal", "fractions", "queue", "weakref", "gc", "atexit",
    "signal", "errno", "ctypes", "array", "binascii", "calendar",
    "codecs", "difflib", "enum", "filecmp", "fnmatch", "getopt",
    "graphlib", "gzip", "lzma", "bz2", "numbers", "operator",
    "pprint", "profile", "pstats", "runpy", "sched", "shutil",
    "site", "smtplib", "ssl", "stat", "tabnanny", "tarfile",
    "telnetlib", "this", "timeit", "token", "tokenize", "trace",
    "tracemalloc", "types", "typing", "zipapp", "zoneinfo",
}


def _is_allowed(top):
    if top in ALLOWED_STDLIB:
        return True
    if top in ALLOWED_OUR:
        return True
    if top in ALLOWED_THIRD:
        return True
    # наши _tools_* и tools_*
    if top.startswith("_tools_") or top.startswith("tools_"):
        return True
    return False


def _check_file(path):
    """Возвращает список (lineno, что_импортируется, почему_плохо)."""
    bad = []
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception as e:
        return [(0, f"<parse error>", str(e))]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if not _is_allowed(top):
                    bad.append((node.lineno, alias.name,
                                "импорт из внешнего модуля"))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # относительный импорт — ок
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if not _is_allowed(top):
                bad.append((node.lineno, node.module,
                            "from-импорт из внешнего модуля"))
    return bad


def check_actions():
    """Проверить все _actions/*.py. Возвращает {файл: [(lineno, модуль, почему)]}."""
    here = pathlib.Path(__file__).resolve().parent.parent
    actions = here / "_actions"
    if not actions.exists():
        return {}
    report = {}
    for f in sorted(actions.glob("*.py")):
        if f.name.startswith("__"):
            continue
        bad = _check_file(f)
        if bad:
            report[f.name] = bad
    return report


def format_report(report):
    if not report:
        return "✅ Все _actions/*.py импортируют только разрешённые модули."
    lines = ["⛔ Запрещённые импорты в _actions/*.py:"]
    for fname, bad in report.items():
        lines.append(f"   {fname}")
        for lineno, mod, why in bad:
            lines.append(f"     строка {lineno}: {mod}   ({why})")
    return "\n".join(lines)
