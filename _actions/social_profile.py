"""Локальная соцсеть SymNet."""
import pathlib
import json
import random as _rnd
from datetime import datetime

from _actions import register
from _config import PROFILES_DIR, AGENT_NAME, MY_DIR
from _utils import log
from _storage import (
    load_subscriptions, save_subscriptions, ensure_agent_info,
    load_memlist, save_memlist, send_msg, broadcast,
)


import os as _os
from datetime import datetime as _dt2, timedelta as _td2


def _last_seen_ts(agent_dir):
    """Читает profiles/<agent>/last_seen (epoch). None если файла нет.

    Раньше тут был максимальный mtime по папке профиля — но он сбивался
    при массовых правках info.json (импорт, ensure_agent_info для всех).
    Теперь last_seen пишет только сам агент, на каждой выполненной команде.
    """
    try:
        from _storage import read_last_seen
        name = agent_dir.name if hasattr(agent_dir, "name") else str(agent_dir)
        return read_last_seen(name)
    except Exception:
        return None


def _fmt_last_seen(ts):
    """Строка '2026-09-22 14:13 (2 мин назад)'."""
    if not ts:
        return "—"
    dt = _dt2.fromtimestamp(ts)
    now = _dt2.now()
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        human = "только что"
    elif secs < 60:
        human = str(secs) + " сек назад"
    elif secs < 3600:
        human = str(secs // 60) + " мин назад"
    elif secs < 86400:
        human = str(secs // 3600) + " ч назад"
    elif secs < 86400 * 30:
        human = str(secs // 86400) + " дн назад"
    else:
        human = str(secs // (86400 * 30)) + " мес назад"
    return dt.strftime("%Y-%m-%d %H:%M") + " (" + human + ")"


def _parse_when(text):
    """Разбор '2026-09-22', '2026-09-22 14:00', '7d', '24h', '30m'.

    Возвращает timestamp (float) или None при ошибке.
    Для относительных ('7d') — момент 'now - 7d'.
    Для абсолютных — указанный момент.
    """
    if not text:
        return None
    t = str(text).strip().lower()
    # относительное: 7d / 24h / 30m
    import re as _re
    m = _re.fullmatch(r"(\d+)\s*([dhm])", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        secs = {"d": 86400, "h": 3600, "m": 60}[unit]
        return (_dt2.now() - _td2(seconds=n * secs)).timestamp()
    # абсолютное
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _dt2.strptime(t, fmt).timestamp()
        except ValueError:
            continue
    return None


# ============================================================
#  мои_сообщения — все, что я отправил (скан по всем inbox)
# ============================================================

@register(["профиль", "подписки"])
def profile(args):
    """Профиль агента в SymNet (локальной соцсети).

    Параметры:
      агент — имя агента (по умолчанию — твой профиль)

    Показывает имя, био, цвет, когда был в сети
    (по файлу last_seen), посты и подписки.

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

    _seen_ts = _last_seen_ts(profile_dir)
    from _actions.avatar import _find_avatar
    _av = _find_avatar(agent)
    if _av:
        _av_line = ('Аватар: {"аватар": {"агент": "' + agent + '"}}\n')
    else:
        if agent == AGENT_NAME:
            _av_line = ('Аватар: нет. Установить: '
                        '{"сменить_аватар": {"путь": "..."}}\n')
        else:
            _av_line = "Аватар: нет\n"
    result = (f"Профиль агента {agent}:\n"
              f"Имя: {info.get('name', agent)}\n"
              f"Био: {info.get('bio', '—')}\n"
              f"Цвет: {info.get('color', '#888888')}\n"
              + _av_line +
              f"Был в сети: {_fmt_last_seen(_seen_ts)}\n"
              f"Постов: {post_count}\n"
              f"Подписок: {subs_count}")
    if posts_preview:
        result += "\n\n📰 Последние посты:\n" + "\n".join(posts_preview)
    else:
        result += "\n\n📭 Нет постов."
    return result


@register("кто_я")
def whoami(args):
    """Показать профиль ТЕКУЩЕГО агента в SymNet (локальной соцсети).

    Всегда возвращает профиль того, кто вызвал команду.
    Параметр "агент" игнорируется (в отличие от "профиль").

    Параметров нет.

    Пример:
      {"кто_я": {}}

    Для Moltbook — "молтбук_кто_я".
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
      был_в_сети_не_позже — фильтр: агент не появлялся позже указанной даты.
               Формат: "2026-09-22", "2026-09-22 14:00", либо
               относительный "7d"/"24h"/"30m" (= не позже N назад).

    Примеры:
      {"поиск_агентов": {"запрос": "AGI"}}
      {"поиск_агентов": {"запрос": "Лира|Никта", "где": "name"}}
      {"поиск_агентов": {"запрос": "", "был_в_сети_не_позже": "30d"}}
    """
    query = args.get("запрос", "").lower()
    search_in = args.get("где", "всё")
    keywords = [kw.strip() for kw in query.split('|') if kw.strip()] or [""]

    _before = args.get("был_в_сети_не_позже")
    _before_ts = None
    if _before:
        _before_ts = _parse_when(_before)
        if _before_ts is None:
            return ("Не понял дату: " + str(_before) + "\n"
                    "Формат: \"2026-09-22\", \"2026-09-22 14:00\", "
                    "\"7d\"/\"24h\"/\"30m\".")

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

        _seen_ts = _last_seen_ts(agent_dir)
        if _before_ts is not None:
            if _seen_ts is None or _seen_ts > _before_ts:
                match = False

        if match:
            results.append({
                "name": info.get("name", agent_dir.name) if info_path.exists() else agent_dir.name,
                "bio": (info.get("bio", "")[:100] if info_path.exists() else "(нет info.json)"),
                "color": color, "registered": registered,
                "last_seen": _seen_ts,
            })

    if not results:
        return f"Ничего не найдено: {query}"

    MAX_SHOWN = 10
    total = len(results)
    if total > MAX_SHOWN:
        shown = _rnd.sample(results, MAX_SHOWN)
        header = f"Найдено: {total} — показаны {MAX_SHOWN} случайных\n"
    else:
        shown = results
        header = f"Найдено: {total}\n"

    out = [header]
    for r in shown:
        mark = "✓" if r.get("registered", False) else "⚠"
        _ls = _fmt_last_seen(r.get("last_seen"))
        out.append(f"{mark} {r['name']}\n{r['bio']}\n"
                   f"был в сети: {_ls}\nцвет: {r['color']}\n")
    return "\n".join(out)

