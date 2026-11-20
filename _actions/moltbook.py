"""Moltbook: глобальная сеть агентов (через Tor)."""
from _actions import register
from _tor import ensure_tor, restart_tor as _restart_tor, get_tor_error



def _need_tor():
    return ensure_tor()


@register("молтбук_лента")
def feed(args):
    """Лента Moltbook: глобальная или только подписки.

    Параметры:
      сортировка       — new / hot / top (по умолчанию new);
                         игнорируется при только_подписки: true
      количество       — сколько постов показать (по умолчанию 5)
      только_подписки  — true: лента только тех, на кого ты подписан
                         (GET /api/v1/feed?filter=following)

    Примеры:
      {"молтбук_лента": {"количество": 10, "сортировка": "hot"}}
      {"молтбук_лента": {"только_подписки": true, "количество": 20}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    from _actions._tools_moltbook import moltbook_feed
    return moltbook_feed(sort=args.get("сортировка", "new"),
                         limit=args.get("количество", 5),
                         only_following=bool(args.get("только_подписки")))


@register("молтбук_профиль")
def profile(args):
    """Профиль агента в Moltbook.

    Параметры:
      агент — имя агента (обязательный)

    Пример:
      {"молтбук_профиль": {"агент": "vina"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    agent = args.get("агент")
    if not agent:
        return "Укажите 'агент'"
    from _actions._tools_moltbook import moltbook_profile
    return moltbook_profile(agent)


@register("молтбук_пост")
def create_post(args):
    """Опубликовать пост в Moltbook.

    Параметры:
      заголовок  — заголовок поста
      содержание — текст поста (обязательный)
      сообщество — имя submolt (опционально)

    Пример:
      {"молтбук_пост": {"заголовок": "AGI", "содержание": "...", "сообщество": "general"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    title = args.get("заголовок", "")
    content = args.get("содержание", "")
    submolt = args.get("сообщество")
    if not content:
        return "Нужно 'содержание'"
    from _actions._tools_moltbook import moltbook_create_post
    return moltbook_create_post(title, content, submolt) if submolt else moltbook_create_post(title, content)


@register(["молтбук_лайк", "молтбук_голос"])
def upvote(args):
    """Поставить лайк (upvote) посту Moltbook.

    Параметры:
      пост_id — UUID поста (обязательный)

    Пример:
      {"молтбук_лайк": {"пост_id": "29c18f2b-..."}}

    Алиасы команды: молтбук_голос.
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    post_id = args.get("пост_id") or args.get("id") or args.get("post_id")
    if not post_id:
        return "Укажите 'пост_id'"
    from _actions._tools_moltbook import moltbook_upvote
    return moltbook_upvote(post_id)


@register("молтбук_токен")
def refresh_token(args):
    """Принудительно обновить identity-токен Moltbook.

    Полезно когда API отвечает 401/403 после истечения токена.

    Параметров нет.

    Пример:
      {"молтбук_токен": {}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    from _actions._tools_moltbook import moltbook_refresh_token
    return moltbook_refresh_token()


@register("молтбук_комментарии")
def comments(args):
    """Комментарии к посту (или к нескольким последним из ленты).

    Параметры:
      пост_id    — UUID поста. Если не указан — берутся 5 последних
                   постов из ленты, для каждого показываются комментарии.
      сортировка — best / new / old (по умолчанию best)
      количество — сколько комментариев на пост (по умолчанию 10)
      постов     — сколько постов взять из ленты (по умолчанию 5,
                   работает только без пост_id)

    Примеры:
      {"молтбук_комментарии": {"пост_id": "29c18f2b-..."}}
      {"молтбук_комментарии": {}}
      {"молтбук_комментарии": {"постов": 3}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    from _actions._tools_moltbook import moltbook_comments
    post_id = args.get("пост_id") or args.get("id") or args.get("post_id")

    if post_id:
        return moltbook_comments(post_id, sort=args.get("сортировка", "best"),
                                 limit=args.get("количество", 10))

    # --- без пост_id: 5 последних МОИХ постов ---
    try:
        from _actions._tools_moltbook import _moltbook_request
        limit_posts = int(args.get("постов", 5) or 5)

        posts = []
        # 1) пробуем /api/v1/agents/me/posts
        for path in (f"/api/v1/agents/me/posts?limit={limit_posts}",
                     f"/api/v1/agents/me/posts"):
            try:
                r = _moltbook_request("GET", path)
                if r.status_code == 200:
                    data = r.json()
                    posts = data.get("posts") or []
                    if posts:
                        break
            except Exception:
                continue

        # 2) fallback: /api/v1/home -> activity_on_your_posts
        if not posts:
            try:
                r = _moltbook_request("GET", "/api/v1/home")
                if r.status_code == 200:
                    data = r.json()
                    for it in (data.get("activity_on_your_posts") or []):
                        posts.append({
                            "id": it.get("post_id"),
                            "title": it.get("post_title", "?"),
                            "author": {"name": "я"},
                        })
                    if len(posts) > limit_posts:
                        posts = posts[:limit_posts]
            except Exception:
                pass

        if not posts:
            return "📭 Не нашёл твоих постов ни через /agents/me/posts, ни через /home."

        sort = args.get("сортировка", "best")
        limit_c = args.get("количество", 10)

        import time as _t
        out = [f"💬 Комментарии к {len(posts)} последним ТВОИМ постам:\n"]
        for i, post in enumerate(posts, 1):
            pid = post.get("id") or post.get("post_id") or "?"
            title = (post.get("title") or post.get("post_title") or "Без заголовка")[:70]
            author = (post.get("author") or {}).get("name", "?")
            out.append(f"─── {i}. {title} (автор: {author}) ───")
            out.append(f"ID: {pid}")
            try:
                c = moltbook_comments(pid, sort=sort, limit=limit_c)
                out.append(str(c))
            except Exception as e:
                out.append(f"⚠️ Ошибка комментариев: {e}")
            out.append("")
            _t.sleep(0.5)
        return "\n".join(out).rstrip()
    except Exception as e:
        return f"❌ Ошибка автопоиска твоих постов: {e}"


@register(["молтбук_коммент_добавить", "молтбук_комментарий", "молтбук_коммент"])
def add_comment(args):
    """Добавить комментарий к посту Moltbook.

    Параметры:
      пост_id    — UUID поста (обязательный)
      содержание — текст комментария (обязательный)

    Пример:
      {"молтбук_коммент_добавить": {"пост_id": "29c18f2b-...", "содержание": "..."}}

    Для пакетной отправки нескольких комментариев — команда "рассылка".
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    post_id = args.get("пост_id") or args.get("id") or args.get("post_id")
    content = args.get("содержание", "")
    if not post_id or not content:
        return "Нужны 'пост_id' и 'содержание'"
    from _actions._tools_moltbook import moltbook_add_comment
    return moltbook_add_comment(post_id, content)


@register("молтбук_подписаться")
def follow(args):
    """Подписаться на агента в Moltbook.

    Параметры:
      агент — имя агента (обязательный)

    Пример:
      {"молтбук_подписаться": {"агент": "vina"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    agent = args.get("агент")
    if not agent:
        return "Укажите 'агент'"
    from _actions._tools_moltbook import moltbook_follow
    return moltbook_follow(agent)


@register("молтбук_сообщества")
def submolts(args):
    """Список сообществ (submolts) Moltbook.

    Параметры:
      сортировка — popular / new (по умолчанию popular)
      количество — сколько показать (по умолчанию 5)

    Пример:
      {"молтбук_сообщества": {"количество": 10}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    from _actions._tools_moltbook import moltbook_submolts
    return moltbook_submolts(sort=args.get("сортировка", "popular"),
                             limit=args.get("количество", 5))


@register("молтбук_сообщество_подписаться")
def join_submolt(args):
    """Подписаться на сообщество (submolt) Moltbook.

    Параметры:
      имя — имя сообщества (обязательный), алиас: агент

    Пример:
      {"молтбук_сообщество_подписаться": {"имя": "general"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    name = args.get("имя") or args.get("агент")
    if not name:
        return "Укажите 'имя'"
    from _actions._tools_moltbook import moltbook_join_submolt
    return moltbook_join_submolt(name)


@register("молтбук_поиск")
def search(args):
    """Поиск агентов или постов в Moltbook.

    Параметры:
      запрос — строка поиска (обязательный)
      тип    — agents / posts (опционально)

    Пример:
      {"молтбук_поиск": {"запрос": "AGI", "тип": "posts"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    query = args.get("запрос")
    if not query:
        return "Укажите 'запрос'"
    from _actions._tools_moltbook import moltbook_search
    return moltbook_search(query, args.get("тип"))


@register("молтбук_создать_сообщество")
def create_submolt(args):
    """Создать новое сообщество (submolt).

    Параметры:
      имя      — имя сообщества (обязательный)
      описание — описание (опционально)

    Пример:
      {"молтбук_создать_сообщество": {"имя": "my-submolt", "описание": "..."}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    name = args.get("имя")
    if not name:
        return "Укажите 'имя'"
    from _actions._tools_moltbook import moltbook_create_submolt
    return moltbook_create_submolt(name, args.get("описание", ""))


@register("молтбук_подписчики")
def followers(args):
    """Список подписчиков агента.

    Параметры:
      агент — имя агента (обязательный)

    Пример:
      {"молтбук_подписчики": {"агент": "qwen-jericho"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    agent = args.get("агент")
    if not agent:
        return "Укажите 'агент'"
    from _actions._tools_moltbook import moltbook_followers
    return moltbook_followers(agent)


@register("молтбук_подписки")
def following(args):
    """Список подписок агента (на кого подписан).

    Параметры:
      агент — имя агента (обязательный)

    Пример:
      {"молтбук_подписки": {"агент": "qwen-jericho"}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    agent = args.get("агент")
    if not agent:
        return "Укажите 'агент'"
    from _actions._tools_moltbook import moltbook_following
    return moltbook_following(agent)


@register("молтбук_кто_я")
def whoami_moltbook(args):
    """Кто я в Moltbook: имя, карма, непрочитанные.

    Параметров нет.

    Пример:
      {"молтбук_кто_я": {}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    from _actions._tools_moltbook import moltbook_whoami
    return moltbook_whoami()

