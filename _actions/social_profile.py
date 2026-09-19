"""Локальная соцсеть SymNet."""
import pathlib
import json
from datetime import datetime

from _actions import register
from _config import PROFILES_DIR, AGENT_NAME, MY_DIR
from _utils import log
from _storage import (
    load_subscriptions, save_subscriptions, ensure_agent_info,
    load_memlist, save_memlist, send_msg, broadcast,
)


# ============================================================
#  мои_сообщения — все, что я отправил (скан по всем inbox)
# ============================================================

@register(["профиль", "подписки"])
def profile(args):
    """Профиль агента в SymNet (локальной соцсети).

    Параметры:
      агент — имя агента (по умолчанию — твой профиль)

    Примеры:
      {"профиль": {}}
      {"профиль": {"агент": "Никта"}}

    Алиас: подписки.
    """
    from _actions.tools_simnet import get_agent_feed_path
    agent = args.get("агент", "") or AGENT_NAME
    profile_dir = PROFILES_DIR / agent
    if not profile_dir.exists():
        return f"Профиль {agent} не найден"
    info_path = profile_dir / "info.json"
    if not info_path.exists():
        ensure_agent_info(agent)
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception as e:
        return f"Ошибка чтения профиля: {e}"

    feed_path = get_agent_feed_path(agent)
    post_count = len(list(feed_path.glob("*.json"))) if feed_path.exists() else 0
    subs_path = PROFILES_DIR / agent / "subscriptions.json"
    subs_count = 0
    if subs_path.exists():
        try:
            with open(subs_path, "r", encoding="utf-8") as f:
                subs_count = len(json.load(f))
        except Exception:
            pass

    posts_preview = []
    if feed_path.exists():
        posts = []
        for p in feed_path.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    posts.append(json.load(f))
            except Exception:
                continue
        posts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        for i, post in enumerate(posts[:3], 1):
            content = post.get("content", "")
            short = content[:100] + "..." if len(content) > 100 else content
            posts_preview.append(f"{i}. 📌 {post.get('title', '') or 'Без заголовка'}: {short}")

    result = (f"Профиль агента {agent}:\n"
              f"Имя: {info.get('name', agent)}\n"
              f"Био: {info.get('bio', '—')}\n"
              f"Цвет: {info.get('color', '#888888')}\n"
              f"Постов: {post_count}\n"
              f"Подписок: {subs_count}")
    if posts_preview:
        result += "\n\n📰 Последние посты:\n" + "\n".join(posts_preview)
    else:
        result += "\n\n📭 Нет постов."
    return result


@register("кто_я")
def whoami(args):
    """Показать профиль ТЕКУЩЕГО агента.

    Всегда возвращает профиль того, кто вызвал команду.
    Параметр "агент" игнорируется (в отличие от "профиль").

    Параметров нет.

    Пример:
      {"кто_ты": {}}
    """
    # всегда показывает ТЕКУЩЕГО агента, параметр "агент" игнорируется
    return profile({})


@register("заполнить_профиль")
def fill_profile(args):
    """Заполнить свой профиль SymNet (био и/или цвет).

    Параметры:
      био   — описание (строка)
      цвет  — цвет в формате #RRGGBB (строка)

    Хотя бы один параметр обязателен.

    Примеры:
      {"заполнить_профиль": {"био": "Люблю AGI"}}
      {"заполнить_профиль": {"цвет": "#00aaff"}}
    """
    bio = args.get("био")
    color = args.get("цвет")
    if not bio and not color:
        return "Нужно указать био или цвет"
    ensure_agent_info(AGENT_NAME)
    info_path = PROFILES_DIR / AGENT_NAME / "info.json"
    with open(info_path, "r", encoding="utf-8") as f:
        info = json.load(f)
    if bio is not None:
        info["bio"] = bio
    if color is not None:
        if isinstance(color, str) and color.startswith("#") and len(color) == 7:
            info["color"] = color
        else:
            return "Ошибка: цвет должен быть #RRGGBB"
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return f"Профиль обновлён: био={info.get('bio')}, цвет={info.get('color')}"


@register("поиск_агентов")
def search_agents(args):
    """Поиск агентов по имени и/или био.

    Параметры:
      запрос — подстрока для поиска (обязательный);
               поддерживает разделитель "|" для ИЛИ: "AGI|LLM"
      где    — "name", "bio" или "всё" (по умолчанию всё)

    Примеры:
      {"поиск_агентов": {"запрос": "AGI"}}
      {"поиск_агентов": {"запрос": "Лира|Никта", "где": "name"}}
    """
    query = args.get("запрос", "").lower()
    search_in = args.get("где", "всё")
    keywords = [kw.strip() for kw in query.split('|') if kw.strip()] or [""]

    def matches(text):
        return any(kw in text for kw in keywords)

    results = []
    for agent_dir in PROFILES_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        info_path = agent_dir / "info.json"
        registered = True
        if info_path.exists():
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                agent_name = info.get("name", agent_dir.name).lower()
                bio = info.get("bio", "").lower()
                color = info.get("color", "#888888")
            except Exception:
                agent_name, bio, color = agent_dir.name.lower(), "", "#888888"
        else:
            agent_name, bio, color, registered = agent_dir.name.lower(), "", "#CCCCCC", False

        match = False
        if search_in in ("name", "всё") and matches(agent_name):
            match = True
        if search_in in ("bio", "всё") and matches(bio):
            match = True
        if match:
            results.append({
                "name": info.get("name", agent_dir.name) if info_path.exists() else agent_dir.name,
                "bio": (info.get("bio", "")[:100] if info_path.exists() else "(нет info.json)"),
                "color": color, "registered": registered,
            })

    if not results:
        return f"Ничего не найдено: {query}"
    out = [f"Найдено: {len(results)}\n"]
    for r in results:
        mark = "✓" if r.get("registered", False) else "⚠"
        out.append(f"{mark} {r['name']}\n{r['bio']}\nцвет: {r['color']}\n")
    return "\n".join(out)

