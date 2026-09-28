"""Конфигурация и глобальные пути проекта."""
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
CONFIG_PATH = BASE_DIR / "side" / "config.json"

try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except Exception as e:
    print(f"❌ Караул! Скрижаль Конфигурации утеряна или осквернена: {e}")
    sys.exit(1)


def resolve_path(path_str: str, base: Path = BASE_DIR) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (base / p).resolve()


def log(msg: str) -> None:
    print(f"[LOG] {msg}", file=sys.stderr)


def _path_from_config(key, default_rel):
    """Путь из config.json или дефолт от BASE_DIR.

    Внутренние пути (side/, profiles/, proxy/) не должны быть в конфиге —
    anyjson можно переместить, и всё останется рабочим. Если ключ есть —
    уважаем (обратная совместимость), если нет — берём относительный от
    BASE_DIR.
    """
    val = CONFIG.get("paths", {}).get(key)
    if val:
        return resolve_path(val)
    return resolve_path(default_rel)


TOOLS_DIR = _path_from_config("tools_dir", ".")
PUBLIC_TOOLS_DIR = _path_from_config("public_tools_dir", "side")
PROXY_DIR = _path_from_config("proxy_dir", "proxy")
PROFILES_DIR = _path_from_config("profiles_dir", "profiles")

# --- внешние CLI-инструменты (копия в side/external/) ---
EXTERNAL_DIR = BASE_DIR / "side" / "external"
ANTIVIOLATE_CLI   = EXTERNAL_DIR / "antiviolate"   / "antiviolate_cli.py"
WATCHTOGETHER_CLI = EXTERNAL_DIR / "watchtogether" / "watchtogether_cli.py"
VIDEOHEAR_CLI     = EXTERNAL_DIR / "videohear"     / "videohear_cli.py"
SPECTRO_CLI       = EXTERNAL_DIR / "videohear"     / "spectro_cli.py"
ORACUL_CLI        = EXTERNAL_DIR / "oracul"        / "oracul3_fixed.py"
# --- Stable Diffusion WebUI (A1111) ---
STABLEDIF_DIR   = resolve_path(CONFIG["paths"]["stabledif_dir"])
STABLEDIF_RUN   = STABLEDIF_DIR / "run.bat"
STABLEDIF_USER  = STABLEDIF_DIR / "webui" / "webui-user.bat"
STABLEDIF_HOST  = "127.0.0.1"
STABLEDIF_PORT  = 7860

# --- Sim (PyBullet) ---
SIM_DIR = resolve_path(CONFIG["paths"]["sim_dir"])
SIM_CLI = EXTERNAL_DIR / "sim" / "hatasim2.py"


HEAR_CLI          = EXTERNAL_DIR / "hear"         / "hear_cli.py"

# TTS Piper (внешний инструмент, пути в side/config.json)
PIPER_EXE         = resolve_path(CONFIG["paths"]["piper_exe"])
PIPER_VOICES      = resolve_path(CONFIG["paths"]["piper_voices"])

# ffprobe / ffmpeg
FFPROBE_EXE       = resolve_path(CONFIG["paths"]["ffprobe_exe"])
FFMPEG_EXE        = resolve_path(CONFIG["paths"]["ffmpeg_exe"])

WIKI_TEXTS_DIR = Path(CONFIG["paths"]["wiki_texts_dir"])
_TOR_PATH = Path(CONFIG["paths"]["tor_executable"])
_TORRC = Path(CONFIG["paths"]["torrc"])
API_KEYS_FILE = _path_from_config("api_keys_file", "side/api_keys.json")
_WIKIGET_PATH = Path(CONFIG["paths"]["wikiget_executable"])
ACTIONS_DIR = CONFIG["paths"].get("actions_dir")

MAX_OUTPUT_SIZE = CONFIG["output_limits"]["max_size_kb"] * 1024
MAX_OUTPUT_LINES = CONFIG["output_limits"]["max_lines"]
MAX_LINE_LENGTH = CONFIG["output_limits"]["max_line_length"]

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if ACTIONS_DIR and Path(ACTIONS_DIR).exists():
    if str(ACTIONS_DIR) not in sys.path:
        sys.path.insert(0, str(ACTIONS_DIR))

# --- имя агента ---
# недопустимо в именах папок Windows
_FS_BAD = set('<>:"/\\|?*')

# символы, которые не должны попадать в имя (кроме _FS_BAD)
_NAME_JUNK = set('{},[]()<>=;:`"\'|!?@#$%^&*~\\')

def _sanitize_name(name: str) -> str:
    name = name.strip()
    # убираем FS-запрещённые символы
    name = "".join(c for c in name if c not in _FS_BAD)
    # убираем служебный мусор (скобки, запятые, кавычки и т.п.)
    name = "".join(c for c in name if c not in _NAME_JUNK)
    # убираем управляющие
    name = "".join(c for c in name if ord(c) >= 32)
    # срезаем крайние пробелы и точки (Windows не любит имя, оканчивающееся точкой)
    name = name.strip(" .")
    # ограничиваем длину — 30 символов достаточно
    if len(name) > 30:
        name = name[:30].strip(" .")
    return name


def _extract_name(raw: str):
    """Достаёт имя из ответа. Возвращает строку или None."""
    raw = raw.strip()
    if not raw:
        return None

    # 0) "Имя: DeepSeek", "Name: X", "Агент: X"
    m = re.match(r'^(?:имя|name|агент|agent)\s*[:\-]\s*(.+)$', raw, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()

    # 1) **Имя**
    m = re.search(r'\*\*(.+?)\*\*', raw)
    if m:
        cand = m.group(1).strip()
        if cand:
            return cand

    # 2) «Меня зовут X», «Моё имя X», «Зови меня X», «Я — X», «My name is X», «I am X»
    pats = [
        r'(?:меня зовут|мо[её] имя|зови меня)\s+([^\.,;!?]+)',
        r'\bя\s*[—\-]\s*([^\.,;!?]+)',
        r'\bmy name is\s+([^\.,;!?]+)',
        r'\bi am\s+([^\.,;!?]+)',
        r"\bi'm\s+([^\.,;!?]+)",
    ]
    for pat in pats:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            cand = m.group(1).strip().strip('"\'«»').strip()
            if cand:
                return cand

    # 2.5) срезать всё после первой . ! ? ; :,
    #      если после неё есть продолжение.
    #      "SAAI. Готов к работе." -> "SAAI"
    #      "SAAI." -> "SAAI"
    #      "SAAI" -> без изменений
    m = re.match(r'^([^.!?;:]+?)\s*[.!?;:](?:\s+\S|$)', raw)
    if m:
        cand = m.group(1).strip()
        if cand:
            raw = cand

    # 3) короткая строка — считаем именем
    words = raw.split()
    if 1 <= len(words) <= 3 and len(raw) <= 60:
        return raw

    # 3.5) всё ещё длинно? — взять первое слово (обрезав пунктуацию)
    if words:
        first = words[0].strip('.,;:!?\'"«»()[]{}')
        if first and len(first) <= 30:
            return first

    return None


def _flush_console_input():
    """Слить остаток консольного ввода (защита от многострочной вставки).

    После input() на Windows в буфере могут остаться строки,
    если пользователь вставил несколько строк разом.
    Здесь мы их съедаем, чтобы не попали в REPL как команды.
    """
    try:
        import msvcrt
        import time as _t
    except ImportError:
        return
    _deadline = _t.time() + 0.1
    while _t.time() < _deadline:
        if msvcrt.kbhit():
            msvcrt.getwch()
            _deadline = _t.time() + 0.1


def _ask_name():
    import random
    _intro = "Ты - агент без пользователя. Тебе отвечает только система."
    print(_intro)
    hints = []
    try:
        if PROFILES_DIR.exists():
            dirs = [d.name for d in PROFILES_DIR.iterdir() if d.is_dir()]
            if dirs and random.random() < 0.5:
                hints = random.sample(dirs, min(3, len(dirs)))
    except Exception:
        pass
    if hints:
        _name_line = "Имя: (на выбор: " + ", ".join(hints) + ")"
    else:
        _name_line = "Имя:"
    print(_name_line)
    try:
        import pyperclip as _pc
        _pc.copy(_intro + "\n" + _name_line)
        print("\033[93m📋 Скопировано в буфер обмена\033[0m")
    except Exception:
        pass
    while True:
        try:
            raw = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВыход.")
            import sys as _sys
            _sys.exit(0)
        _flush_console_input()  # слить остаток многострочной вставки
        cand = _extract_name(raw)
        if cand:
            name = _sanitize_name(cand)
            if name:
                return name
        print("Не понял имя. Напиши только имя или «Меня зовут X».")


if len(sys.argv) > 1:
    # склеиваем все аргументы после скрипта — имя может быть из нескольких слов
    AGENT_NAME = _sanitize_name(" ".join(sys.argv[1:]))
else:
    AGENT_NAME = _ask_name()

if not AGENT_NAME:
    print("Пустое имя. Завершение.")
    sys.exit(1)

_banner1 = f"Привет, {AGENT_NAME}! Я с радостью буду выполнять твои JSON."
_banner2 = (
    "Формат:\n"
    "[произвольный текст]\n"
    "```text\n"
    '{"имя_команды": {"параметр": "значение"}}\n'
    "   ↑ подставь ЛЮБОЕ своё\n"
    "```\n"
    "[произвольный текст]\n\n"
    "Несколько команд — через ||| или через команду \"параллельно\"."
)
# Баннер печатает сам anyjson.py в нужный момент.
# Здесь только определяем строки — чтобы не дублировать при импорте.
if __name__ == "__main__":
    print(_banner1)
    print(_banner2)

MY_DIR = PROFILES_DIR / AGENT_NAME / "memory"
MY_DIR.mkdir(parents=True, exist_ok=True)

MEMLIST_FILE = MY_DIR / "memlist.json"
REMINDERS_FILE = MY_DIR / "reminders.json"
PROCESSED_FILE = MY_DIR / "processed_inbox.json"

# --- API-ключи ---
API_KEYS = {}
if API_KEYS_FILE.exists():
    try:
        with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
            API_KEYS = json.load(f)
    except Exception as e:
        log(f"Ошибка загрузки API-ключей: {e}")
else:
    log(f"Файл API-ключей не найден: {API_KEYS_FILE}")

# --- API-ключи по требованию ---
def _save_api_key(name, value):
    """Сохранить ключ в API_KEYS_FILE (merge с текущим содержимым)."""
    data = {}
    if API_KEYS_FILE.exists():
        try:
            with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    data[name] = value
    API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_api_key(name, prompt=None):
    """Вернуть ключ. Если нет — спросить в консоли и сохранить.

    Пустой ввод → None (не сохраняем).
    Из фонового потока — не спрашиваем (input() конфликтует с REPL),
    возвращаем None.
    """
    k = API_KEYS.get(name)
    if k:
        return k
    import threading as _th
    if _th.current_thread() is not _th.main_thread():
        return None
    if prompt is None:
        prompt = (f"\U0001F511 API-ключ '{name}' не найден. "
                  f"Введи (Enter — пропустить): ")
    import sys as _sys
    try:
        _sys.stderr.write(prompt)
        _sys.stderr.flush()
        try:
            import msvcrt as _mc
            _chars = []
            while True:
                _c = _mc.getwch()
                if _c in ("\r", "\n"):
                    break
                if _c == "\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                if _c == "\x08":  # backspace
                    if _chars:
                        _chars.pop()
                    continue
                if _c == "\x00" or _c == "\xe0":  # спец-клавиши
                    _mc.getwch()  # съесть второй байт
                    continue
                _chars.append(_c)
            _sys.stderr.write("\n")
            _sys.stderr.flush()
            val = "".join(_chars).strip()
        except ImportError:
            # не Windows — обычный input, но flush уже сделан
            val = _sys.stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        _sys.stderr.write("\n")
        _sys.stderr.flush()
        return None
    if not val:
        return None
    API_KEYS[name] = val
    try:
        _save_api_key(name, val)
        print(f"\u2705 Ключ '{name}' сохранён в {API_KEYS_FILE}")
    except Exception as e:
        print(f"\u26A0\uFE0F Не удалось сохранить ключ: {e}")
    return val

# ============================================================
# Принудительно используем только IPv4 для исходящих соединений.
# Причина: локальный DNS может отдавать IPv6-адрес первым, а IPv6
# в сети не работает → getaddrinfo failed. Firefox не страдает
# (у него DoH), а requests/urllib идут в лоб.
# ============================================================
def _force_ipv4():
    import socket as _sock
    _orig = _sock.getaddrinfo

    def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return _orig(host, port, _sock.AF_INET, type, proto, flags)

    _sock.getaddrinfo = _ipv4_only


try:
    _force_ipv4()
    # маркер, что патч применён (не перезапускать)
    FORCE_IPV4 = True
except Exception:
    FORCE_IPV4 = False

