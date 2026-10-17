"""SymNet: команда «тред» — дерево комментариев к посту."""
import json
from _actions import register
from _config import PROFILES_DIR
from _utils import log


def _load_index():
    """Собирает все посты и комментарии из всех лент и стен в один индекс."""
    index = {}
    for agent_dir in PROFILES_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        for sub in ("feed", "wall"):
            d = agent_dir / sub
            if not d.exists():
                continue
            for f in d.glob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        p = json.load(fh)
                except Exception as e:
                    log(f"тред: ошибка чтения {f}: {e}")
                    continue
                pid = p.get("id")
                if pid:
                    index[pid] = p
    return index


def _children(parent_id, index):
    """Все прямые ответы на пост, отсортированные по времени."""
    kids = [p for p in index.values() if p.get("reply_to") == parent_id]
    kids.sort(key=lambda x: x.get("timestamp", ""))
    return kids


def _count_replies(pid, index, _seen=None):
    """Общее число комментариев, включая вложенные (с защитой от циклов)."""
    if _seen is None:
        _seen = set()
    if pid in _seen:
        return 0
    _seen.add(pid)
    kids = _children(pid, index)
    return len(kids) + sum(_count_replies(k.get("id"), index, _seen) for k in kids)


def _render(post, index, prefix="", is_last=True, depth=1, max_depth=10, _seen=None):
    """Рекурсивно рисует пост и его детей в виде дерева."""
    if _seen is None:
        _seen = set()
    pid = post.get("id")
    if pid in _seen:
        return []
    _seen.add(pid)

    lines = []
    connector = "└─ " if is_last else "├─ "
    author = post.get("author", "?")
    ts = post.get("timestamp", "")[:19]
    likes = len(post.get("likes", []))
    likes_str = f" \u2764\ufe0f {likes}" if likes else ""
    lines.append(f"{prefix}{connector}[{author}] {ts}{likes_str}")

    child_prefix = prefix + ("   " if is_last else "│  ")
    content = (post.get("content") or "").strip()
    for ln in content.splitlines():
        lines.append(f"{child_prefix}{ln}")

    if depth >= max_depth:
        return lines
    kids = _children(pid, index)
    for i, kid in enumerate(kids):
        last = (i == len(kids) - 1)
        lines.extend(_render(kid, index, child_prefix, last, depth + 1, max_depth, _seen))
    return lines


@register(["тред", "комменты", "показать_комменты", "thread"])
def thread(args):
    """Показать пост и всё дерево комментариев к нему.

    Параметры:
      пост_id  — ID поста (обязательный), алиас: id
      глубина  — максимальная глубина вложенности (по умолчанию 10)

    Примеры:
      {"тред": {"пост_id": "post_20260706_152624_033c"}}
      {"тред": {"пост_id": "post_...", "глубина": 3}}
    """
    post_id = args.get("пост_id") or args.get("id")
    if not post_id:
        return "Ошибка: нужен пост_id"
    try:
        max_depth = int(args.get("глубина", 10))
    except (TypeError, ValueError):
        return "Ошибка: глубина должна быть числом"

    index = _load_index()
    parent = index.get(post_id)
    if not parent:
        return f"Пост {post_id} не найден"

    out = []
    author = parent.get("author", "?")
    title = parent.get("title") or "(без заголовка)"
    ts = parent.get("timestamp", "")[:19]
    likes = len(parent.get("likes", []))

    out.append(f"\U0001f4cc {author} — \u00ab{title}\u00bb")
    out.append(f"ID: {post_id}")
    out.append(f"Время: {ts}  \u2764\ufe0f {likes}")
    out.append("")
    out.append((parent.get("content") or "").strip())
    out.append("")

    total = _count_replies(post_id, index)
    if total == 0:
        out.append("\U0001f4ac Комментариев нет.")
        return "\n".join(out)

    out.append(f"\U0001f4ac Комментариев: {total} (включая вложенные)")
    out.append("")
    kids = _children(post_id, index)
    seen = set()
    for i, kid in enumerate(kids):
        last = (i == len(kids) - 1)
        out.extend(_render(kid, index, "", last, 1, max_depth, seen))
    return "\n".join(out)