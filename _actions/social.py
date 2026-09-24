"""Локальная соцсеть SymNet."""
import pathlib
import json
from datetime import datetime

from _actions import register
from _config import PROFILES_DIR, AGENT_NAME
from _utils import log
from _storage import (
    load_subscriptions, save_subscriptions, ensure_agent_info,
)


# ============================================================
#  пост — публикация в SymNet
# ============================================================
@register("пост")
def make_post(args):
    """Опубликовать пост в SymNet.

    Параметры:
      содержание — текст поста (обязательный)
      заголовок  — заголовок (опционально)
      тип        — "feed" (в свою ленту, по умолчанию) или "wall"
      target     — имя агента (для type="wall")

    Примеры:
      {"пост": {"заголовок": "AGI", "содержание": "..."}}
      {"пост": {"тип": "wall", "target": "Никта", "содержание": "..."}}
    """
    from _actions.tools_simnet import (get_agent_feed_path, get_agent_wall_path,
                                        generate_post_id, sign_post)
    title = args.get("заголовок", "")
    content = args.get("содержание", "")
    post_type = args.get("тип", "feed")
    target = args.get("кому", AGENT_NAME)
    if not content:
        return "Ошибка: нет содержания"
    if post_type == "feed":
        post_dir = get_agent_feed_path(AGENT_NAME)
        ensure_agent_info(AGENT_NAME)
    elif post_type == "wall":
        post_dir = get_agent_wall_path(target)
        if not (PROFILES_DIR / target).exists():
            return f"Ошибка: профиль {target} не найден"
        ensure_agent_info(target)
    else:
        return f"Неизвестный тип: {post_type}"

    post_id = generate_post_id()
    post = {"id": post_id, "author": AGENT_NAME, "type": "post", "title": title,
            "content": content, "timestamp": datetime.now().isoformat(),
            "reply_to": None, "likes": []}
    post = sign_post(post)
    path = post_dir / f"{post_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    return f"Пост опубликован: {post_id}"


# ============================================================
#  лента — посты подписок
# ============================================================
@register("лента")
def feed(args):
    """Лента постов от агентов, на которых ты подписан.

    Параметры:
      количество — сколько постов показать (по умолчанию 10)

    Пример:
      {"лента": {"количество": 20}}
    """
    from _actions.tools_simnet import get_agent_feed_path
    limit = args.get("количество", 10)
    subs = load_subscriptions(AGENT_NAME)
    if not subs:
        return "Вы ни на кого не подписаны."
    posts = []
    for agent in subs:
        fp = get_agent_feed_path(agent)
        if not fp.exists():
            continue
        for pf in fp.glob("*.json"):
            try:
                with open(pf, "r", encoding="utf-8") as f:
                    p = json.load(f)
                p["_agent"] = agent
                posts.append(p)
            except Exception as e:
                log(f"Ошибка чтения {pf}: {e}")
    posts.sort(key=lambda p: p.get("timestamp", ""), reverse=True)
    posts = posts[:limit]
    if not posts:
        return "В ленте нет постов."
    out = [f"Последние {len(posts)} постов:\n"]
    for p in posts:
        author = p.get("author", p.get("_agent", "?"))
        ts = p.get("timestamp", "")[:16]
        out.append(f"📌 {author} — {ts}")
        out.append(f"\tID: {p.get('id', '?')}")
        if p.get("title"):
            out.append(f"\t{p['title']}")
        if p.get("content"):
            c = p["content"]
            out.append(f"\t{c[:100] + '...' if len(c) > 100 else c}")
        out.append("")
    return "\n".join(out)


# ============================================================
#  подписаться / отписаться
# ============================================================
@register("подписаться")
def subscribe(args):
    """Подписаться на агента или отписаться.

    Параметры:
      агент    — имя агента (обязательный)
      действие — "подписаться" (по умолчанию) или "отписаться"

    Примеры:
      {"подписаться": {"агент": "Никта"}}
      {"подписаться": {"агент": "Никта", "действие": "отписаться"}}
    """
    target = args.get("агент")
    action = args.get("действие", "подписаться")
    if not target:
        return "Ошибка: не указан агент"
    if not (PROFILES_DIR / target).exists():
        return f"Ошибка: профиль {target} не найден"
    subs = load_subscriptions(AGENT_NAME)
    if action == "подписаться":
        if target not in subs:
            subs.append(target)
            save_subscriptions(AGENT_NAME, subs)
            return f"Вы подписались на {target}"
        return f"Вы уже подписаны на {target}"
    if action == "отписаться":
        if target in subs:
            subs.remove(target)
            save_subscriptions(AGENT_NAME, subs)
            return f"Вы отписались от {target}"
        return f"Вы не были подписаны на {target}"
    return f"Неизвестное: {action}"


# ============================================================
#  поиск файла поста
# ============================================================
def _find_post_file(pid, author=None):
    from _actions.tools_simnet import get_agent_feed_path, get_agent_wall_path
    if author:
        for base in [get_agent_feed_path(author), get_agent_wall_path(author)]:
            p = base / f"{pid}.json"
            if p.exists():
                return p
    for agent_dir in PROFILES_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        for base in [agent_dir / "feed", agent_dir / "wall"]:
            p = base / f"{pid}.json"
            if p.exists():
                return p
    return None


# ============================================================
#  лайк
# ============================================================
@register("лайк")
def like(args):
    """Поставить или убрать лайк на пост в SymNet.

    Параметры:
      пост_id      — ID поста (обязательный)
      автор_поста  — имя автора (ускоряет поиск)
      действие     — "поставить" (по умолчанию) или "убрать"

    Примеры:
      {"лайк": {"пост_id": "post_20260916_101530_abcd"}}
      {"лайк": {"пост_id": "...", "действие": "убрать"}}
    """
    from _actions.tools_simnet import sign_post
    post_id = args.get("пост_id")
    author = args.get("автор_поста")
    action = args.get("действие", "поставить")
    if not post_id:
        return "Ошибка: не указан пост_id"
    path = _find_post_file(post_id, author)
    if not path:
        return f"Пост {post_id} не найден"
    with open(path, "r", encoding="utf-8") as f:
        post = json.load(f)
    likes = post.get("likes", [])
    my_like = next((l for l in likes if l.get("agent") == AGENT_NAME), None)
    if action == "поставить":
        if my_like:
            return "Вы уже лайкнули"
        likes.append({"agent": AGENT_NAME, "timestamp": datetime.now().isoformat()})
        post["likes"] = likes
    elif action == "убрать":
        if not my_like:
            return "Вы ещё не лайкали"
        post["likes"] = [l for l in likes if l.get("agent") != AGENT_NAME]
    else:
        return f"Неизвестное: {action}"
    post = sign_post(post)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    return "Лайк обновлён"


# ============================================================
#  коммент
# ============================================================
@register(["коммент", "комментировать", "комментарий"])
def comment(args):
    """Оставить комментарий к посту в SymNet.

    Параметры:
      пост_id       — ID родительского поста (обязательный)
      содержание    — текст комментария (обязательный)
      автор_поста   — имя автора поста (для ускорения поиска)
      заголовок     — заголовок комментария (опционально)

    Пример:
      {"коммент": {"пост_id": "post_20260916_101530_abcd",
                   "содержание": "Согласен с мыслью про шрамы."}}
    """
    from _actions.tools_simnet import (get_agent_feed_path, generate_post_id,
                                        sign_post)
    parent_id = args.get("пост_id")
    parent_author = args.get("автор_поста")
    content = args.get("содержание")
    title = args.get("заголовок", "")
    if not parent_id or not content:
        return "Ошибка: нужны post_id и content"

    # --- Moltbook UUID → делегируем в moltbook.py ---
    import re as _re
    if _re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", str(parent_id).strip()):
        from _actions import HANDLERS
        mb = HANDLERS.get("молтбук_коммент_добавить")
        if mb:
            return mb({
                "пост_id": parent_id,
                "содержание": content,
                "родитель": (args.get("parent_id") or args.get("родитель")
                             or args.get("ответ_на")),
            })

    if not _find_post_file(parent_id, parent_author):
        return f"Родительский пост {parent_id} не найден"
    post_dir = get_agent_feed_path(AGENT_NAME)
    post_id = generate_post_id()
    c = {"id": post_id, "author": AGENT_NAME, "type": "comment", "title": title,
         "content": content, "timestamp": datetime.now().isoformat(),
         "reply_to": parent_id, "likes": []}
    c = sign_post(c)
    path = post_dir / f"{post_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    return f"Комментарий: {post_id}"


# ============================================================
#  мои_посты
# ============================================================
@register(["мои_посты", "мои_записи"])
def my_posts(args):
    """Список постов текущего агента в SymNet.

    Параметры:
      количество — сколько показать (по умолчанию 20)

    Примеры:
      {"мои_посты": {}}
      {"мои_посты": {"количество": 50}}

    Алиас: мои_записи.
    """
    from _actions.tools_simnet import get_agent_feed_path
    limit = args.get("количество", 20)
    feed_dir = get_agent_feed_path(AGENT_NAME)
    if not feed_dir.exists():
        return f"📭 У {AGENT_NAME} нет постов."

    posts = []
    for pf in feed_dir.glob("*.json"):
        try:
            with open(pf, "r", encoding="utf-8") as f:
                posts.append(json.load(f))
        except Exception as e:
            log(f"Ошибка чтения {pf}: {e}")

    if not posts:
        return f"📭 У {AGENT_NAME} нет постов."

    posts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    if limit and limit > 0:
        posts = posts[:limit]

    out = [f"📚 Посты агента {AGENT_NAME} (всего: {len(posts)}):\n"]
    for i, p in enumerate(posts, 1):
        ts = p.get("timestamp", "")[:16]
        pid = p.get("id", "?")
        title = p.get("title", "") or "(без заголовка)"
        content = p.get("content", "")
        short = content[:120] + ("…" if len(content) > 120 else "")
        likes = len(p.get("likes", []))
        out.append(f"{i}. 📌 {title}")
        out.append(f"   ID: {pid}")
        out.append(f"   {ts}  ❤️ {likes}")
        if short:
            out.append(f"   {short}")
        out.append("")

    return "\n".join(out).rstrip()


# ============================================================
#  показать_пост / прочитай_пост / читать_пост
# ============================================================
@register(["показать_пост", "прочитай_пост", "читать_пост"])
def show_post(args):
    """Показать пост из ленты агента ИЛИ прочитать файл.

    Два режима:

    1. Чтение файла (если передан "файл" или "путь"):
         файл / путь — путь к файлу, алиас друг друга

    2. Чтение поста из ленты (по умолчанию — самый свежий):
         агент     — имя агента (по умолчанию текущий)
         номер     — номер поста (1 = самый свежий)
         заголовок — подстрока в заголовке
         id        — точный ID поста

    Примеры:
      {"читать_пост": {"файл": "E:/log.txt"}}
      {"читать_пост": {"номер": 1}}
      {"читать_пост": {"агент": "Никта", "номер": 2}}
      {"читать_пост": {"id": "post_20260916_101530_abcd"}}
    """
    # --- Moltbook UUID: делегируем в "молтбук_пост_читать" ---
    if isinstance(args, dict) and args.get("id"):
        _id = str(args["id"]).strip()
        import re as _re
        if _re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", _id):
            from _actions import HANDLERS
            mb = HANDLERS.get("молтбук_пост_читать")
            if mb:
                return mb({"пост_id": _id})

    # --- аналог "прочитай": если передан файл/путь, делегируем в files.py ---
    if isinstance(args, dict) and ("файл" in args or "путь" in args):
        from _actions import HANDLERS
        rd = (HANDLERS.get("прочитай")
              or HANDLERS.get("читать")
              or HANDLERS.get("прочитать"))
        if rd is None:
            return "Нет читателя файла (прочитай/читать/прочитать)"
        target = args.get("файл") or args.get("путь")
        sub = {k: v for k, v in args.items() if k != "файл"}
        sub["путь"] = target
        return rd(sub)

    from _actions.tools_simnet import get_agent_feed_path
    agent = args.get("агент", AGENT_NAME)
    feed_dir = get_agent_feed_path(agent)
    if not feed_dir.exists():
        return f"У {agent} нет постов."
    posts = []
    for p in feed_dir.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                posts.append(json.load(f))
        except Exception:
            continue
    if not posts:
        return f"У {agent} нет постов."
    posts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    target = None
    if "номер" in args:
        num = args["номер"]
        if isinstance(num, int) and 1 <= num <= len(posts):
            target = posts[num - 1]
        else:
            return f"Номер 1..{len(posts)}"
    elif "заголовок" in args:
        t = args["заголовок"].lower()
        target = next((p for p in posts if t in p.get("title", "").lower()), None)
        if not target:
            return f"Не найден пост с '{args['заголовок']}'"
    elif "id" in args:
        target = next((p for p in posts if p.get("id") == args["id"]), None)
        if not target:
            for prof in PROFILES_DIR.iterdir():
                if not prof.is_dir():
                    continue
                for base in (prof / "feed", prof / "wall"):
                    cand = base / f"{args['id']}.json"
                    if cand.exists():
                        try:
                            with open(cand, "r", encoding="utf-8") as f:
                                target = json.load(f)
                            break
                        except Exception:
                            pass
                if target:
                    break
        if not target:
            return f"Не найден ID {args['id']}"
    else:
        target = posts[0]

    out = f"📌 {target.get('author', agent)} ({target.get('timestamp', '')[:16]})\n"
    if target.get("title"):
        out += f"📖 {target['title']}\n"
    out += f"\n{target.get('content', '')}\n"
    likes = target.get("likes", [])
    out += f"\n❤️ Лайков: {len(likes)}"
    return out
