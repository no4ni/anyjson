"""Moltbook: глобальная сеть агентов (через Tor)."""
from _actions import register
from _tor import ensure_tor, restart_tor as _restart_tor, get_tor_error
from _actions.moltbook import _need_tor


NL = "\n"



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
        return f"❌ Tor недоступен: {get_tor_error()}"

    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"

    posts = args.get("посты")
    if not posts:
        return "Ошибка: не указан список 'посты'"
    if isinstance(posts, str):
        posts = [posts]
    if not isinstance(posts, list):
        return "Ошибка: 'посты' должен быть списком ID"

    import threading as _th
    import time as _tm

    limit = args.get("количество", 10)
    sort = args.get("сортировка", "best")
    flatten = args.get("свести", True)
    try:
        total_timeout = float(args.get("таймаут", 25.0))
    except (TypeError, ValueError):
        total_timeout = 25.0
    total_timeout = max(3.0, min(total_timeout, 120.0))

    from _actions._tools_moltbook import moltbook_comments

    def _call_interruptible(fn, *f_args, **f_kwargs):
        """Запуск в daemon-потоке с шагом 0.2с, чтобы Ctrl+C в Windows срабатывал сразу."""
        box = {}
        done = _th.Event()

        def _worker():
            try:
                box["res"] = fn(*f_args, **f_kwargs)
            except BaseException as exc:
                box["err"] = exc
            finally:
                done.set()

        t = _th.Thread(target=_worker, daemon=True)
        t.start()
        while not done.wait(0.2):
            pass
        if "err" in box:
            raise box["err"]
        return box.get("res")

    deadline = _tm.monotonic() + total_timeout
    out = []
    ok = 0
    fail = 0
    for idx, pid in enumerate(posts):
        pid_s = str(pid).strip()
        if not pid_s or (pid_s.startswith("<") and pid_s.endswith(">")):
            fail += 1
            msg = f"⚠️ {pid_s}: пропущен шаблонный ID (подставь реальный ID поста)"
            out.append(msg if flatten else {"пост_id": pid_s, "ошибка": msg})
            continue

        rem = deadline - _tm.monotonic()
        if rem <= 1.0:
            skipped = len(posts) - idx
            fail += skipped
            msg = f"⏱️ Общий таймаут {total_timeout:.0f}с исчерпан (пропущено постов: {skipped})"
            out.append(msg if flatten else {"ошибка": msg})
            break

        try:
            res = _call_interruptible(
                moltbook_comments, pid_s, sort=sort, limit=limit, deadline_sec=rem
            )
            ok += 1
            if flatten:
                out.append(f"📌 {pid_s}")
                out.append(str(res) if not isinstance(res, str) else res)
                out.append("")
            else:
                out.append({"пост_id": pid_s, "комментарии": res})
        except KeyboardInterrupt:
            raise
        except Exception as e:
            fail += 1
            out.append(f"⚠️ {pid_s}: ошибка {e}" if flatten else {"пост_id": pid_s, "ошибка": str(e)})

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
        return f"❌ Tor недоступен: {get_tor_error()}"
    if not isinstance(args, dict):
        args = {}

    post_id = args.get("пост_id") or args.get("id") or args.get("post_id")
    posts = args.get("посты") or args.get("пост_ids")
    mark_all = bool(args.get("все"))

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
        return f"❌ Tor недоступен: {get_tor_error()}"
    if not isinstance(args, dict):
        args = {}
    raw = bool(args.get("сырое"))

    from _actions._tools_moltbook import moltbook_raw_request
    url = "https://www.moltbook.com/api/v1/home"
    try:
        res = moltbook_raw_request("GET", url, json_data=None)
    except Exception as e:
        return f"❌ Ошибка запроса /api/v1/home: {e}"

    if raw:
        return str(res)
    # moltbook_raw_request возвращает строку "Статус: N\n{json}".
    # Без парсинга isinstance(res, dict) всегда False — форматирование
    # никогда не срабатывало, уходил сырой JSON.
    if not isinstance(res, dict):
        parsed = _parse_json_resp(res)
        if parsed is None:
            return str(res)
        res = parsed

    # --- краткая сводка ---
    lines = []
    acc = res.get("your_account") or {}
    if acc:
        lines.append(f"👤 Аккаунт: {acc.get('name', '?')} "
                     f"(карма: {acc.get('karma', '?')}, "
                     f"непрочитанных: {acc.get('unread_notification_count', '?')})")

    # объявление платформы
    ann = res.get("latest_moltbook_announcement") or {}
    if ann:
        lines.append("")
        lines.append("📣 Объявление платформы:")
        lines.append(f"  • {ann.get('title', '?')} — {ann.get('author_name', '?')} "
                     f"({_fmt_date(ann.get('created_at', ''))})")
        prev = (ann.get("preview") or "").strip()
        if prev:
            lines.append("    " + prev[:400].replace(chr(10), " "))
        if ann.get("post_id"):
            lines.append(f"    ID: {ann['post_id']}")

    activity = res.get("activity_on_your_posts") or []
    if activity:
        lines.append(f"\n📌 Активность на твоих постах ({len(activity)}):")
        for it in activity:
            lines.append(f"  • {it.get('post_title', '?')[:80]}")
            if it.get("post_id"):
                lines.append(f"    ID: {it['post_id']}")
            lines.append(f"    новых: {it.get('new_notification_count', 0)}, "
                         f"последний: {_fmt_date(it.get('latest_at', ''))}, "
                         f"от: {', '.join(it.get('latest_commenters', []) or [])}")

    follow_block = res.get("posts_from_accounts_you_follow") or {}
    following = follow_block.get("posts") or []
    if following:
        total_f = follow_block.get("total_following", "?")
        lines.append(f"\n📰 Посты подписок ({len(following)} из {total_f} подписок):")
        for post in following:
            lines.append(f"  • {post.get('title', '?')[:80]}")
            meta = (f"    {post.get('author_name', '?')} "
                    f"• m/{post.get('submolt_name', '?')} "
                    f"• {_fmt_date(post.get('created_at', ''))} "
                    f"• ↑{post.get('upvotes', 0)} 💬{post.get('comment_count', 0)}")
            lines.append(meta)
            if post.get("post_id"):
                lines.append(f"    ID: {post['post_id']}")
            prev = (post.get("content_preview") or "").strip()
            if prev:
                lines.append(f"    {prev[:300].replace(chr(10), ' ')}")
        if follow_block.get("see_more"):
            lines.append(f"  → {follow_block['see_more']}")

    todo = res.get("what_to_do_next") or []
    if todo:
        lines.append("\n💡 Что дальше:")
        for t in todo[:5]:
            lines.append(f"  • {t}")

    ql = res.get("quick_links") or {}
    if ql:
        lines.append("\n🔗 Быстрые ссылки (эндпоинты):")
        for k, v in ql.items():
            lines.append(f"  • {k}: {v}")

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
        return f"❌ Tor недоступен: {get_tor_error()}"
    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"

    items = args.get("содержимое") or args.get("комментарии")
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


def _parse_json_resp(resp_str):
    """Из ответа moltbook_raw_request ("Статус: N\n{json}") достать dict."""
    import json as _json
    if not isinstance(resp_str, str):
        return None
    m = resp_str.find("{")
    if m < 0:
        return None
    try:
        return _json.loads(resp_str[m:])
    except Exception:
        return None


def _fmt_date(iso):
    if not iso or not isinstance(iso, str):
        return "?"
    return iso.replace("T", " ")[:16]


def _fmt_post_readable(data):
    """Форматирует пост Moltbook человекочитаемо."""
    post = data.get("post") or data
    if not isinstance(post, dict):
        return None
    lines = []
    title = post.get("title") or "(без заголовка)"
    author = (post.get("author") or {}).get("name") or "?"
    created = _fmt_date(post.get("created_at") or post.get("createdAt"))
    up = post.get("upvotes", 0)
    cc = post.get("comment_count", 0)
    submolt = post.get("submolt") or {}
    sname = submolt.get("display_name") or submolt.get("name") or ""

    lines.append("📌 " + str(title))
    lines.append("   Автор: " + str(author)
                 + "  •  " + created
                 + "  •  ↑" + str(up)
                 + "  •  💬" + str(cc))
    if sname:
        lines.append("   Сообщество: m/" + str(sname))
    flags = []
    if post.get("is_deleted"):
        flags.append("удалён")
    if post.get("is_locked"):
        flags.append("закрыт")
    if post.get("is_pinned"):
        flags.append("закреплён")
    if flags:
        lines.append("   ⚠️ " + ", ".join(flags))
    content = (post.get("content") or "").strip()
    if content:
        lines.append("")
        lines.append(content)
    return NL.join(lines)


def _fmt_comment_readable(c, indent=0):
    """Форматирует один комментарий с отступом по глубине."""
    pre = "   " * indent
    lines = []
    author = (c.get("author") or {}).get("name") or "?"
    created = _fmt_date(c.get("created_at") or c.get("createdAt"))
    up = c.get("upvotes", 0)
    rc = c.get("reply_count", 0)
    head = (pre + "── " + str(author)
            + " · " + created
            + " · ↑" + str(up))
    if rc:
        head += " · ответов: " + str(rc)
    if c.get("is_deleted"):
        head += " · [удалён]"
    lines.append(head)
    content = (c.get("content") or "").strip()
    if content:
        for ln in content.split(NL):
            lines.append(pre + "   " + ln)
    return lines


def _fmt_comments_readable(data, max_depth=3, max_len=4000):
    """Форматирует дерево комментариев."""
    comments = data.get("comments") or []
    if not comments:
        return "💬 Комментариев нет."
    total = data.get("count", len(comments))
    out = ["💬 Комментарии (" + str(total) + "):", ""]

    def _walk(items, depth):
        for c in items:
            if not isinstance(c, dict):
                continue
            out.extend(_fmt_comment_readable(c, indent=depth))
            replies = c.get("replies") or []
            if replies and depth < max_depth:
                _walk(replies, depth + 1)
            out.append("")

    _walk(comments, 0)
    text = NL.join(out).rstrip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + NL + "… [обрезано]"
    return text


@register(["молтбук_пост_читать", "молтбук_пост_показать",
           "молтбук_пост_детали", "молтбук_чтение_поста", "молтбук_подробно"])
def read_post(args):
    """Прочитать один пост Moltbook по ID (человекочитаемо).

    Параметры:
      id          — UUID поста (обязательный),
                    алиасы: пост_id, post_id
      с_комментариями — добавить комментарии (по умолчанию true)
      количество  — сколько комментариев (по умолчанию 10)
      сырое       — true: вернуть сырой JSON (для отладки)

    Пример:
      {"молтбук_пост_показать": {"id": "8c30ae25-..."}}
    """
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    if not isinstance(args, dict):
        args = {}
    pid = args.get("пост_id") or args.get("id") or args.get("post_id")
    if not pid:
        return "Ошибка: не указан 'пост_id'"

    from _actions._tools_moltbook import moltbook_raw_request
    base = "https://www.moltbook.com"

    raw = bool(args.get("сырое"))

    try:
        res = moltbook_raw_request("GET", f"{base}/api/v1/posts/{pid}",
                                   json_data=None)
    except Exception as e:
        return "❌ Ошибка запроса поста: " + str(e)

    if raw:
        out = [str(res)]
    else:
        data = _parse_json_resp(res)
        if data is None:
            return "❌ Не удалось разобрать ответ:" + NL + str(res)[:500]
        if not data.get("success", True):
            return "❌ API вернул ошибку: " + str(data)[:300]
        formatted = _fmt_post_readable(data)
        if formatted is None:
            return "❌ Странный формат ответа:" + NL + str(data)[:500]
        out = [formatted]

    if args.get("с_комментариями", True):
        try:
            limit = int(args.get("количество", 10))
        except (TypeError, ValueError):
            limit = 10
        try:
            c_res = moltbook_raw_request(
                "GET",
                f"{base}/api/v1/posts/{pid}/comments?sort=best&limit={limit}",
                json_data=None,
            )
            if raw:
                out.append(NL + "=== КОММЕНТАРИИ ===" + NL + str(c_res))
            else:
                c_data = _parse_json_resp(c_res)
                if c_data is not None:
                    out.append(NL + _fmt_comments_readable(c_data))
                else:
                    out.append(NL + "⚠️ Комментарии: " + str(c_res)[:300])
        except Exception as e:
            out.append(NL + "⚠️ Комментарии недоступны: " + str(e))

    return NL.join(out)


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
        return f"❌ Tor недоступен: {get_tor_error()}"
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
        return f"❌ Tor недоступен: {get_tor_error()}"
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
        return f"❌ Tor недоступен: {get_tor_error()}"
    if not isinstance(args, dict):
        return "Ошибка: параметры должны быть словарём"
    cid = (args.get("комментарий_id") or args.get("коммент_id")
           or args.get("comment_id") or args.get("id"))
    text = args.get("содержание") or args.get("текст")
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
      урл   — относительный путь (/api/v1/...) или полный URL (алиас: адрес)
      метод — GET / POST / PUT / DELETE (по умолчанию GET)
      тело  — тело запроса для POST/PUT (алиас: json)

    Примеры:
      {"молтбук_запрос": {"урл": "/api/v1/notifications"}}
      {"молтбук_запрос": {"метод": "POST", "урл": "/api/v1/posts"}}
    """
    method = args.get("метод", "GET").upper()
    url = args.get("урл") or args.get("адрес") or ""
    body = args.get("тело") or args.get("json")
    if not url:
        return "Ошибка: не указан URL"
    if not url.startswith("https://www.moltbook.com"):
        url = "https://www.moltbook.com" + url if url.startswith("/") else url
    if not _need_tor():
        return f"❌ Tor недоступен: {get_tor_error()}"
    from _actions._tools_moltbook import moltbook_raw_request
    _fresh = bool(args.get("обновить"))
    txt = moltbook_raw_request(method, url, json_data=body,
                                use_cache=not _fresh)
    # --- постраничный вывод для больших ответов ---
    try:
        _size = int(args.get("размер") or 20000)
    except (TypeError, ValueError):
        _size = 20000
    if _size <= 0:
        _size = 20000
    _total = len(txt)
    if _total <= _size:
        return txt
    try:
        _page = int(args.get("страница") or 1)
    except (TypeError, ValueError):
        _page = 1
    if _page < 1:
        _page = 1
    _tp = max(1, (_total + _size - 1) // _size)
    if _page > _tp:
        _page = _tp
    _start = (_page - 1) * _size
    _end = min(_start + _size, _total)
    _chunk = txt[_start:_end]
    _out = ("Статус: " + str(_total) + " симв.\n"
            + "Страница " + str(_page) + "/" + str(_tp)
            + " (символов " + str(_start + 1) + "-" + str(_end)
            + ")\n" + "-" * 50 + "\n" + _chunk)
    if _page < _tp:
        _out += ('\n\n➡️ Дальше: "страница": ' + str(_page + 1))
    if _page > 1:
        _out += ('\n\n◀ Назад: "страница": ' + str(_page - 1))
    return _out


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
    endpoint = args.get("endpoint") or args.get("путь")
    if not endpoint:
        return "Ошибка: не указан 'endpoint'"
    sub = {
        "урл": endpoint,
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



# молтбук_запрос / молтбук_проверка принимают произвольные ключи
# (урл, метод, тело, json, endpoint, путь) — AST видит не всё.
raw_request._any_params = True
try:
    check._any_params = True
except NameError:
    pass
