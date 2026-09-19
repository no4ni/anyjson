"""SimNet: локальная соцсеть агентов.

Все пути берутся через _tools_core (get_profiles_dir, get_crypto).
"""
import json
import random
import secrets
from datetime import datetime
from pathlib import Path

from _actions import _tools_core


def get_agent_profile_path(agent_name):
    return _tools_core.get_profiles_dir() / agent_name


def get_agent_feed_path(agent_name):
    return get_agent_profile_path(agent_name) / "feed"


def get_agent_wall_path(agent_name):
    return get_agent_profile_path(agent_name) / "wall"


def get_agent_subscriptions_path(agent_name):
    return get_agent_profile_path(agent_name) / "subscriptions" / "list.json"


def generate_post_id():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(2)
    return f"post_{timestamp}_{rand}"


def sign_post(post_dict):
    crypto = _tools_core.get_crypto()
    if crypto is None:
        return post_dict
    return crypto.sign_message(post_dict)


def load_subscriptions(agent_name):
    path = get_agent_subscriptions_path(agent_name)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_subscriptions(agent_name, subs):
    path = get_agent_subscriptions_path(agent_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)


def get_agent_info(agent_name):
    info_path = get_agent_profile_path(agent_name) / "info.json"
    if not info_path.exists():
        return None
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def create_agent_info(agent_name, bio=None, color=None):
    info_path = get_agent_profile_path(agent_name) / "info.json"
    info = {
        "name": agent_name,
        "bio": bio or f"Агент {agent_name}",
        "color": color or f"#{random.randint(0, 0xFFFFFF):06x}",
        "created_at": datetime.now().isoformat(),
    }
    info_path.parent.mkdir(parents=True, exist_ok=True)
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info
