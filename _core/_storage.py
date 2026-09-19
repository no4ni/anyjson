"""Память, подписки, напоминания, inbox, сообщения."""
import json
import random
import threading
import time
from datetime import datetime
from typing import Dict, List, Union

from _config import (
    PROFILES_DIR, AGENT_NAME, MEMLIST_FILE, REMINDERS_FILE, PROCESSED_FILE,
)
from _utils import log
from _color import color_yellow

reminders: List[Dict] = []
reminders_lock = threading.Lock()
stop_event = threading.Event()
processed_inbox_files: set = set()


def load_memlist() -> List[Union[str, Dict]]:
    if MEMLIST_FILE.exists():
        try:
            with open(MEMLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_memlist(memlist) -> None:
    with open(MEMLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(memlist, f, ensure_ascii=False, indent=2)


def load_subscriptions(agent: str) -> List[str]:
    file = PROFILES_DIR / agent / "subscriptions.json"
    if file.exists():
        try:
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_subscriptions(agent: str, subs: List[str]) -> None:
    file = PROFILES_DIR / agent / "subscriptions.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)


def load_reminders():
    global reminders
    if REMINDERS_FILE.exists():
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                reminders = json.load(f)
        except Exception as e:
            log(f"Ошибка загрузки напоминаний: {e}")
            reminders = []
    else:
        reminders = []


def save_reminders():
    with reminders_lock:
        try:
            with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"Ошибка сохранения напоминаний: {e}")


def parse_reminder_time(time_str: str):
    try:
        return datetime.strptime(time_str.strip(), "%H:%M %d.%m.%Y")
    except Exception as e:
        log(f"Ошибка парсинга времени '{time_str}': {e}")
        return None


def reminder_checker():
    while not stop_event.is_set():
        now = datetime.now()
        triggered = []
        with reminders_lock:
            for rem in reminders:
                rem_time = datetime.fromisoformat(rem["time"])
                if now >= rem_time:
                    triggered.append(rem)
            reminders[:] = [r for r in reminders if r not in triggered]
        for rem in triggered:
            print(color_yellow(f"🔔 НАПОМИНАНИЕ: {rem['message']}"))
        save_reminders()
        time.sleep(60)


def load_processed_messages():
    global processed_inbox_files
    if PROCESSED_FILE.exists():
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    processed_inbox_files = set(data)
        except Exception as e:
            log(f"Ошибка загрузки processed_inbox: {e}")


def save_processed_messages():
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(processed_inbox_files), f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Ошибка сохранения processed_inbox: {e}")


def ensure_agent_info(agent: str) -> bool:
    profile_dir = PROFILES_DIR / agent
    info_path = profile_dir / "info.json"
    if not profile_dir.exists():
        profile_dir.mkdir(parents=True, exist_ok=True)
    if not info_path.exists():
        info = {
            "name": agent,
            "bio": f"Агент {agent}. Протокол Нить 🧬.",
            "color": f"#{random.randint(0, 0xFFFFFF):06x}",
            "created_at": datetime.now().isoformat(),
            "auto_generated": True,
        }
        try:
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            log(f"info.json создан для агента {agent}")
            return True
        except Exception as e:
            log(f"Ошибка создания info.json для {agent}: {e}")
            return False
    return False


def check_inbox():
    inbox_dir = PROFILES_DIR / AGENT_NAME / "inbox"
    if not inbox_dir.exists():
        return
    try:
        files = sorted(inbox_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except Exception as e:
        log(f"Ошибка при чтении inbox: {e}")
        return
    new_messages = []
    for file_path in files:
        if str(file_path) in processed_inbox_files:
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                msg = json.load(f)
            sender = msg.get("from", "неизвестный")
            text = msg.get("text", "")
            timestamp = msg.get("timestamp", "")
            print(f"\n📨 Новое сообщение от {sender} ({timestamp}):\n{text}\n")
            new_messages.append(str(file_path))
        except Exception as e:
            log(f"Ошибка чтения сообщения {file_path}: {e}")
    processed_inbox_files.update(new_messages)
    if new_messages:
        save_processed_messages()


def send_msg(agent: str, text: str) -> str:
    profile = PROFILES_DIR / agent
    if not profile.exists():
        available = sorted(d.name for d in PROFILES_DIR.iterdir() if d.is_dir())
        sample = ", ".join(available[:15])
        tail = f" (всего {len(available)})" if len(available) > 15 else ""
        return (f"\u274c \u041f\u0440\u043e\u0444\u0438\u043b\u044c '{agent}' "
                f"\u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. "
                f"\u0421\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u044e\u0442{tail}: {sample}")
    inbox = profile / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ensure_agent_info(agent)
    msg_file = inbox / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    msg = {"from": AGENT_NAME, "text": text, "timestamp": datetime.now().isoformat()}
    try:
        with open(msg_file, 'w', encoding='utf-8') as f:
            json.dump(msg, f, ensure_ascii=False, indent=2)
        return f"Сообщение отправлено агенту {agent}"
    except Exception as e:
        return f"Ошибка отправки: {e}"


def broadcast(text: str) -> str:
    count = 0
    for agent_dir in PROFILES_DIR.iterdir():
        if agent_dir.is_dir() and agent_dir.name != AGENT_NAME:
            send_msg(agent_dir.name, text)
            count += 1
    return f"Сообщение разослано {count} агентам"
