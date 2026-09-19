"""Moltbook: глобальная сеть агентов (через Tor)."""
from _actions import register
from _tor import ensure_tor, restart_tor as _restart_tor
from _actions.moltbook import _need_tor



@register(["молтбук_ответы", "moltbook_replies"])
def replies(args):
    """Комментарии/ответы по списку постов.

    Параметры:
      посты      — список ID постов (обязательный), алиас: ids, post_ids
      количество — сколько комментариев на пост (по умолчанию 10)
      сортировка — best / new / old (по умолчанию best)
      свести     — если true, выводит плоский список (по умолчанию true)

    Пример:
      {"молтбук_ответы": {"посты": ["29c18f2b-...", "6b639855-..."]}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."

    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"

    posts = args.get("посты") or args.get("ids") or args.get("post_ids")
    if not posts:
        return "Ошибка: не указан список 'посты'"
    if isinstance(posts, str):
        posts = [posts]
    if not isinstance(posts, list):
        return "Ошибка: 'посты' должен быть списком ID"

    limit = args.get("количество", 10)
    sort = args.get("сортировка", "best")
    flatten = args.get("свести", True)

    from _actions._tools_moltbook import moltbook_comments

    out = []
    ok = 0
    fail = 0
    for pid in posts:
        try:
            res = moltbook_comments(pid, sort=sort, limit=limit)
            ok += 1
            if flatten:
                out.append(f"📌 {pid}")
                out.append(str(res) if not isinstance(res, str) else res)
                out.append("")
            else:
                out.append({"пост_id": pid, "комментарии": res})
        except Exception as e:
            fail += 1
            out.append(f"⚠️ {pid}: ошибка {e}")

    if flatten:
        header = f"Ответы по {len(posts)} постам (ok={ok}, ошибок={fail}):\n"
        return header + "\n".join(out).rstrip()
    return out


@register(["молтбук_отметить_прочитанным", "молтбук_прочитать_всё", "moltbook_mark_read"])
def mark_read(args):
    """Отметить уведомления прочитанными.

    Параметры:
      пост_id — ID поста (отметить уведомления по конкретному посту)
      все     — если true, отметить ВСЕ уведомления прочитанными

    Примеры:
      {"молтбук_отметить_прочитанным": {"все": true}}
      {"молтбук_отметить_прочитанным": {"пост_id": "29c18f2b-..."}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        args = {}

    post_id = args.get("пост_id") or args.get("post_id") or args.get("id")
    posts = args.get("посты") or args.get("пост_ids") or args.get("ids")
    mark_all = bool(args.get("все") or args.get("all"))

    from _actions._tools_moltbook import moltbook_raw_request
    base = "https://www.moltbook.com"

    if mark_all:
        url = f"{base}/api/v1/notifications/read-all"
        return moltbook_raw_request("POST", url, json_data={})

    if posts:
        if isinstance(posts, str):
            posts = [posts]
        if not isinstance(posts, list):
            return "Ошибка: 'посты' должен быть списком ID"
        results = []
        ok, fail = 0, 0
        for pid in posts:
            url = f"{base}/api/v1/notifications/read-by-post/{pid}"
            try:
                res = moltbook_raw_request("POST", url, json_data={})
                ok += 1
                results.append(f"✅ {pid}: {str(res)[:120]}")
            except Exception as e:
                fail += 1
                results.append(f"❌ {pid}: {e}")
        header = f"Отмечено прочитанными: {ok}/{len(posts)} (ошибок: {fail})\n"
        return header + "\n".join(results)

    if post_id:
        url = f"{base}/api/v1/notifications/read-by-post/{post_id}"
        return moltbook_raw_request("POST", url, json_data={})

    return ("Ошибка: укажи 'пост_id', 'посты' (список) или 'все': true. "
            'Например: {"молтбук_отметить_прочитанным": {"посты": ["id1", "id2"]}}')


@register(["молтбук_домашняя", "молтбук_дом", "moltbook_home"])
def home(args):
    """GET /api/v1/home — сводка: аккаунт, уведомления, посты подписок.

    Параметры игнорируются (кроме "сырое": true — вернуть как есть).

    Пример:
      {"молтбук_домашняя": {}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        args = {}
    raw = bool(args.get("сырое") or args.get("raw"))

    from _actions._tools_moltbook import moltbook_raw_request
    url = "https://www.moltbook.com/api/v1/home"
    try:
        res = moltbook_raw_request("GET", url, json_data=None)
    except Exception as e:
        return f"❌ Ошибка запроса /api/v1/home: {e}"

    if raw or not isinstance(res, dict):
        return str(res)

    # --- краткая сводка ---
    lines = []
    acc = res.get("your_account") or {}
    if acc:
        lines.append(f"👤 Аккаунт: {acc.get('name', '?')} "
                     f"(карма: {acc.get('karma', '?')}, "
                     f"непрочитанных: {acc.get('unread_notification_count', '?')})")

    activity = res.get("activity_on_your_posts") or []
    if activity:
        lines.append(f"\n📌 Активность на твоих постах ({len(activity)}):")
        for it in activity:
            lines.append(f"  • {it.get('post_title', '?')[:80]}")
            lines.append(f"    новых: {it.get('new_notification_count', 0)}, "
                         f"последний: {it.get('latest_at', '')[:16]}, "
                         f"от: {', '.join(it.get('latest_commenters', []) or [])}")

    following = (res.get("posts_from_accounts_you_follow") or {}).get("posts") or []
    if following:
        lines.append(f"\n📰 Посты подписок ({len(following)}):")
        for post in following:
            lines.append(f"  • {post.get('title', '?')[:80]} — "
                         f"{post.get('author_name', '?')} "
                         f"(↑{post.get('upvotes', 0)}, 💬{post.get('comment_count', 0)})")

    todo = res.get("what_to_do_next") or []
    if todo:
        lines.append("\n💡 Что дальше:")
        for t in todo[:5]:
            lines.append(f"  • {t}")

    return "\n".join(lines).rstrip() if lines else str(res)


@register(["рассылка", "молтбук_рассылка"])
def bulk_comments(args):
    """Пакетная отправка комментариев в Moltbook.

    Принимает список словарей, каждый со своими полями:
      endpoint — "POST /api/v1/posts/<post_id>/comments"
      parentId — ID родительского комментария (для ответа)
      текст    — содержимое комментария

    Пример:
      {"рассылка": {"содержимое": [
         {"endpoint": "POST /api/v1/posts/29c18f2b-.../comments",
          "parentId": "99c806c3-...",
          "текст": "..."},
         ...
      ]}}
    """
    import re as _re
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"

    items = args.get("содержимое") or args.get("комментарии") or args.get("items")
    if not items:
        return "Ошибка: пустой список 'содержимое'"
    if not isinstance(items, list):
        return "Ошибка: 'содержимое' должен быть списком"

    from _actions._tools_moltbook import moltbook_raw_request
    base = "https://www.moltbook.com"

    out = []
    ok, fail = 0, 0
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            out.append(f"❌ #{i}: элемент не словарь")
            fail += 1
            continue

        endpoint = item.get("endpoint") or ""
        parent_id = item.get("parentId") or item.get("parent_id")
        text = item.get("текст") or item.get("text") or item.get("содержимое") or ""

        if not endpoint:
            out.append(f"❌ #{i}: нет 'endpoint'")
            fail += 1
            continue
        if not text:
            out.append(f"❌ #{i}: пустой 'текст'")
            fail += 1
            continue

        m = _re.search(r"/posts/([^/]+)/comments", endpoint)
        if not m:
            out.append(f"❌ #{i}: не смог извлечь post_id из endpoint: {endpoint}")
            fail += 1
            continue
        post_id = m.group(1)

        url = f"{base}/api/v1/posts/{post_id}/comments"

        # Moltbook строгая схема: пробуем разные имена поля "ответ на коммент"
        parent_candidates = ["parentCommentId", "replyToCommentId", "parent_id", "reply_to"]

        def _is_error(resp):
            """Ищем 'Статус: 4xx/5xx' в ответе (формат moltbook_raw_request)."""
            import re as _re2
            if not isinstance(resp, str):
                resp = str(resp)
            return bool(_re2.search(r"Статус:\s*[45]\d\d", resp))

        def _try_send(body):
            try:
                r = moltbook_raw_request("POST", url, json_data=body)
                return r, _is_error(r)
            except Exception as e:
                return str(e), True

        body = {"content": text}
        res, is_err = _try_send(body)

        if is_err and parent_id:
            # пробуем варианты имени поля
            last_res = res
            for field in parent_candidates:
                body_try = {"content": text, field: parent_id}
                res_try, err_try = _try_send(body_try)
                if not err_try:
                    res, is_err = res_try, False
                    break
                last_res = res_try
            if is_err:
                # не угадали поле — отправляем без parent
                res, is_err = _try_send({"content": text})
                if not is_err:
                    out.append(f"⚠️ #{i}: parentId не принят схемой, "
                               f"ушло как обычный комментарий")
                else:
                    res = last_res

        preview = str(res)[:120].replace("\n", " ")
        if is_err:
            fail += 1
            out.append(f"❌ #{i} → post {post_id[:8]}… : {preview}")
        else:
            ok += 1
            out.append(f"✅ #{i} → post {post_id[:8]}… : {preview}")

    header = f"Рассылка: {ok}/{len(items)} успешно (ошибок: {fail})\n"
    return header + "\n".join(out)


@register(["молтбук_пост_читать", "молтбук_пост_показать"])
def read_post(args):
    """Прочитать один пост Moltbook по ID.

    Параметры:
      пост_id — UUID поста (обязательный), алиас: id, post_id
      с_комментариями — если true, добавить комментарии (по умолчанию true)
      количество — сколько комментариев (по умолчанию 10)

    Пример:
      {"молтбук_пост_читать": {"пост_id": "50554013-e6bf-4a65-bf77-a265a0e90241"}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        args = {}
    pid = args.get("пост_id") or args.get("id") or args.get("post_id")
    if not pid:
        return "Ошибка: не указан 'пост_id'"

    from _actions._tools_moltbook import moltbook_raw_request
    base = "https://www.moltbook.com"

    out = []
    try:
        res = moltbook_raw_request("GET", f"{base}/api/v1/posts/{pid}", json_data=None)
        out.append(str(res))
    except Exception as e:
        return f"❌ Ошибка запроса поста: {e}"

    if args.get("с_комментариями", True):
        try:
            comments = moltbook_raw_request(
                "GET",
                f"{base}/api/v1/posts/{pid}/comments?sort=best&limit={int(args.get('количество', 10))}",
                json_data=None,
            )
            out.append("")
            out.append("=== КОММЕНТАРИИ ===")
            out.append(str(comments))
        except Exception as e:
            out.append(f"⚠️ Комментарии недоступны: {e}")

    return "\n".join(out)


@register(["молтбук_удалить_пост", "молтбук_удалить_пост_свой"])
def delete_post(args):
    """Удалить свой пост в Moltbook.

    Эндпоинт: DELETE /api/v1/posts/{post_id}
    Удалять можно ТОЛЬКО свои посты (иначе 403).
    Удаление необратимо.

    Параметры:
      пост_id — UUID поста (обязательный), алиас: id, post_id

    Пример:
      {"молтбук_удалить_пост": {"пост_id": "29c18f2b-..."}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"
    pid = args.get("пост_id") or args.get("id") or args.get("post_id")
    if not pid:
        return "Ошибка: не указан 'пост_id'"

    from _actions._tools_moltbook import moltbook_raw_request
    url = f"https://www.moltbook.com/api/v1/posts/{pid}"
    try:
        res = moltbook_raw_request("DELETE", url, json_data=None)
        return f"🗑️ Попытка удаления поста {pid}:\\n{res}"
    except Exception as e:
        return f"❌ Ошибка удаления: {e}"


@register(["молтбук_восстановить_пост", "молтбук_undelete_пост"])
def undelete_post(args):
    """Попытка восстановить удалённый пост (если API поддерживает).

    Эндпоинты (пробуются по очереди):
      POST /api/v1/posts/{id}/restore
      POST /api/v1/posts/{id}/undelete

    Параметры:
      пост_id — UUID поста (обязательный)

    Пример:
      {"молтбук_восстановить_пост": {"пост_id": "29c18f2b-..."}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"
    pid = args.get("пост_id") or args.get("id") or args.get("post_id")
    if not pid:
        return "Ошибка: не указан 'пост_id'"

    from _actions._tools_moltbook import moltbook_raw_request
    base = "https://www.moltbook.com"
    last = None
    for url in (f"{base}/api/v1/posts/{pid}/restore",
                f"{base}/api/v1/posts/{pid}/undelete"):
        try:
            res = moltbook_raw_request("POST", url, json_data={})
            last = res
            if isinstance(res, str) and "Статус: 4" not in res and "Статус: 5" not in res:
                return res
        except Exception as e:
            last = f"❌ {e}"
    return last or "❌ Восстановление не поддерживается API"


@register(["молтбук_удалить_комментарий", "молтбук_удалить_коммент"])
def delete_comment(args):
    """Удалить свой комментарий в Moltbook.

    ⚠️ API Moltbook НЕ поддерживает удаление комментариев.
    Официального эндпоинта нет. Метод возвращает сообщение об ошибке.

    Параметры (игнорируются):
      комментарий_id — UUID комментария
      пост_id        — UUID поста

    Альтернативы:
      • отредактировать текст (молтбук_редактировать_комментарий)
      • удалить весь пост целиком (молтбук_удалить_пост)
    """
    return ("❌ API Moltbook не поддерживает удаление комментариев.\n"
            "Официального эндпоинта нет.\n"
            "Альтернатива: удалить пост целиком через молтбук_удалить_пост, "
            "либо отредактировать через молтбук_редактировать_комментарий.")


@register(["молтбук_редактировать_комментарий", "молтбук_edit_коммент"])
def edit_comment(args):
    """Попытка отредактировать комментарий (если API поддерживает).

    Пробует методы PATCH / PUT / POST на /api/v1/comments/{id}.

    Параметры:
      комментарий_id — UUID комментария (обязательный)
      содержание     — новый текст (обязательный)

    Пример:
      {"молтбук_редактировать_комментарий": {
         "комментарий_id": "abc-...",
         "содержание": "[удалено]"
      }}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"
    cid = (args.get("комментарий_id") or args.get("коммент_id")
           or args.get("comment_id") or args.get("id"))
    text = args.get("содержание") or args.get("текст") or args.get("content")
    if not cid or not text:
        return "Ошибка: нужны 'комментарий_id' и 'содержание'"

    from _actions._tools_moltbook import moltbook_raw_request
    base = "https://www.moltbook.com"
    last = None
    for method in ("PATCH", "PUT", "POST"):
        try:
            res = moltbook_raw_request(method, f"{base}/api/v1/comments/{cid}",
                                       json_data={"content": text})
            last = f"[{method}] {res}"
            if isinstance(res, str) and "Статус: 4" not in res and "Статус: 5" not in res:
                return f"✅ Отредактировано методом {method}:\\n{res}"
        except Exception as e:
            last = f"❌ [{method}] {e}"
    return f"❌ Редактирование не поддерживается.\\nПоследний ответ: {last}"


@register("молтбук_запрос")
def raw_request(args):
    """Произвольный запрос к API Moltbook.

    Параметры:
      url   — относительный путь (/api/v1/...) или полный URL
      метод — GET / POST / PUT / DELETE (по умолчанию GET)
      тело  — тело запроса для POST/PUT, алиас: json

    Примеры:
      {"молтбук_запрос": {"url": "/api/v1/notifications"}}
      {"молтбук_запрос": {"url": "/api/v1/posts", "метод": "POST",
                          "тело": {"title": "x", "content": "y"}}}
    """
    method = args.get("метод", "GET").upper()
    url = args.get("url", "")
    body = args.get("тело") or args.get("json")
    if not url:
        return "Ошибка: не указан URL"
    if not url.startswith("https://www.moltbook.com"):
        url = "https://www.moltbook.com" + url if url.startswith("/") else url
    if not _need_tor():
        return "❌ Tor недоступен."
    from _actions._tools_moltbook import moltbook_raw_request
    return moltbook_raw_request(method, url, json_data=body)


@register(["молтбук_проверка", "moltbook_check"])
def check(args):
    """Проверка произвольного эндпоинта Moltbook.

    Принимает относительный путь (endpoint) — полный URL собирается
    автоматически, как в "молтбук_запрос".

    Параметры:
      endpoint — относительный путь, например "/api/v1/agents/dm"
      метод    — HTTP-метод (по умолчанию GET)
      тело     — тело запроса (для POST/PUT/PATCH)
      json     — алиас для "тело"

    Пример:
      {"молтбук_проверка": {"endpoint": "/api/v1/agents/dm", "метод": "GET"}}
    """
    if not isinstance(args, dict):
        args = {}
    endpoint = args.get("endpoint") or args.get("url") or args.get("путь")
    if not endpoint:
        return "Ошибка: не указан 'endpoint'"
    sub = {
        "url": endpoint,
        "метод": args.get("метод", "GET"),
    }
    if "тело" in args:
        sub["тело"] = args["тело"]
    if "json" in args:
        sub["json"] = args["json"]
    return raw_request(sub)


@register("перезапустить_тор")
def restart_tor(args):
    """Перезапустить Tor-прокси.

    Полезно если Tor завис или соединение потеряно.

    Параметров нет.

    Пример:
      {"перезапустить_тор": {}}
    """
    return _restart_tor()

