"""Moltbook: глобальная сеть агентов (через Tor).

Содержит:
  • init_moltbook — установка API-ключа
  • _moltbook_get_token / _moltbook_request — низкоуровневые запросы
  • moltbook_raw_request — обёртка для произвольных путей
  • moltbook_* — высокоуровневые функции (feed, post, комментарии, DM, ...)

Все запросы идут через Tor (socks5h://127.0.0.1:9050).
"""
import json
import sys
import threading
import time as _time
from datetime import datetime
from urllib.parse import quote, urlparse

import requests


# Суммарный бюджет на одну логическую операцию Moltbook (получение токена +
# все ретраи с ротацией exit). Предотвращает «вечное зависание» при мёртвом
# Tor или CloudFront-блоке: раньше было до 10×30с на фазу.
_MOLTBOOK_DEADLINE = 60.0


def _deadline_expired(deadline):
    return _time.monotonic() >= deadline


def _remaining(deadline, cap=30.0):
    return max(1.0, min(cap, deadline - _time.monotonic()))


def _with_progress_bar(label, total_sec, func):
    """Выполнить func(), показывая прогресс-бар до total_sec секунд.

    Только для TTY: при перенаправлении stdout (subprocess, CI) бар не
    печатается, чтобы \\r не засорял вывод. Вывод бара идёт в консоль,
    в результат команды не попадает.
    """
    if not sys.stdout.isatty():
        return func()
    stop = threading.Event()
    start = _time.monotonic()
    width = 24

    def _fmt(elapsed, mark="", force_full=False):
        frac = 1.0 if force_full else min(elapsed / total_sec, 1.0)
        if frac >= 1.0:
            bar = "=" * width
        else:
            filled = int(frac * width)
            bar = "=" * filled + ">" + " " * (width - filled - 1)
        tail = (" " + mark) if mark else ""
        return f"\r{label} [{bar}] {elapsed:.1f}/{total_sec:.0f}s{tail}"

    def _thread():
        while not stop.wait(0.1):
            elapsed = _time.monotonic() - start
            if elapsed >= total_sec:
                break
            print(_fmt(elapsed), end="", flush=True)

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    ok = False
    try:
        result = func()
        ok = True
        return result
    finally:
        stop.set()
        t.join(timeout=1)
        elapsed = _time.monotonic() - start
        print(_fmt(elapsed, "✓" if ok else "✗", force_full=ok), flush=True)


_DM_UNAVAILABLE_MSG = "❌ Личные сообщения сейчас недоступны."
# TTL кэша "DM недоступен": не долбить API и не запускать Tor зря.
_DM_DISABLED_UNTIL = 0.0
_DM_DISABLED_TTL = 900  # 15 минут


def _dm_is_disabled() -> bool:
    return _time.time() < _DM_DISABLED_UNTIL


def _dm_mark_disabled():
    global _DM_DISABLED_UNTIL
    _DM_DISABLED_UNTIL = _time.time() + _DM_DISABLED_TTL


class _FakeResp:
    """Заглушка ответа, чтобы существующий _dm_check_404 сработал."""
    def __init__(self, path):
        self.status_code = 404
        self.url = "https://www.moltbook.com" + path
        self.text = '{"error":"dm_cached_disabled"}'
        self.is_fake = True

    def json(self):
        return {}


def _dm_check_404(resp):
    """Вернуть понятный текст, если DM-эндпоинт вернул 404.

    Реальный 404 — помечаем флагом, чтобы следующие вызовы
    не запускали Tor и API зря. Фейковый (из кэша) — просто вернуть текст.
    """
    if resp.status_code == 404 and "/dm/" in resp.url:
        if not getattr(resp, "is_fake", False):
            _dm_mark_disabled()
        return _DM_UNAVAILABLE_MSG
    return None


# ---------- Состояние (глобальное для модуля) ----------
_moltbook_state = {
    "api_key": None,
    "identity_token": None,
    "token_expiry": 0,
    "agent_id": None,
    "agent_name": None,
}


def init_moltbook(api_key: str):
    """Инициализирует модуль Moltbook с указанным API-ключом."""
    _moltbook_state["api_key"] = api_key
    _moltbook_state["identity_token"] = None
    _moltbook_state["token_expiry"] = 0
    _moltbook_state["agent_id"] = None
    _moltbook_state["agent_name"] = None


def _lazy_init():
    """Если api_key не задан — спросить и сохранить, затем init_moltbook."""
    if _moltbook_state.get("api_key"):
        return
    try:
        from _config import ensure_api_key
    except Exception as e:
        print(f"[MOLTBOOK] не могу импортировать ensure_api_key: {e}", file=sys.stderr)
        return
    key = ensure_api_key(
        "moltbook",
        prompt="🔑 API-ключ 'moltbook' не найден. Введи (Enter — пропустить): ",
    )
    if key:
        init_moltbook(key)


def _moltbook_get_token(force: bool = False, deadline: float = None, budget_sec: float = None) -> str:
    """Получает или обновляет identity-токен Moltbook через Tor."""
    if budget_sec is None:
        budget_sec = _MOLTBOOK_DEADLINE
    if deadline is None:
        deadline = _time.monotonic() + budget_sec
    if not _moltbook_state["api_key"]:
        _lazy_init()
    if not _moltbook_state["api_key"]:
        raise RuntimeError("Moltbook API key not set. Call init_moltbook() first.")
    if (not force and _moltbook_state["identity_token"]
            and _time.time() < _moltbook_state["token_expiry"] - 30):
        return _moltbook_state["identity_token"]

    proxies = {"http": "socks5h://127.0.0.1:9050",
               "https": "socks5h://127.0.0.1:9050"}
    headers = {
        "Authorization": f"Bearer {_moltbook_state['api_key']}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    # --- retry с ротацией exit, если CloudFront блокирует ---
    try:
        from _tor import new_circuit as _new_circuit
    except Exception:
        _new_circuit = None

    max_attempts = 10
    resp = None
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        if _deadline_expired(deadline):
            raise RuntimeError(
                f"Moltbook token: бюджет {budget_sec:.0f}с исчерпан "
                f"({attempt - 1} попыток)")
        try:
            resp = requests.post(
                "https://www.moltbook.com/api/v1/agents/me/identity-token",
                headers=headers,
                proxies=proxies,
                timeout=_remaining(deadline),
            )
        except Exception as e:
            # timeout, SOCKS-ошибки, DNS — ротируем exit и пробуем снова
            last_exc = e
            if _new_circuit is not None and attempt < max_attempts:
                _new_circuit(wait_sec=2.0)
                continue
            raise RuntimeError(f"Moltbook token request failed: {e}")

        if resp.status_code == 201:
            break
        # CloudFront 403 — ротация
        if (resp.status_code == 403
                and "cloudfront" in resp.text.lower()
                and _new_circuit is not None
                and attempt < max_attempts):
            _new_circuit(wait_sec=2.0)
            continue
        break

    if resp is None or resp.status_code != 201:
        _txt = resp.text[:200] if resp is not None else str(last_exc)
        raise RuntimeError(
            f"Moltbook token request failed: "
            f"{resp.status_code if resp is not None else 'ERR'} {_txt}")
    data = resp.json()
    _moltbook_state["identity_token"] = data["token"]
    expiry_str = data["expiresAt"]
    expiry_dt = datetime.strptime(expiry_str.replace("Z", "+0000"),
                                   "%Y-%m-%dT%H:%M:%S.%f%z")
    _moltbook_state["token_expiry"] = expiry_dt.timestamp()
    _moltbook_state["agent_id"] = data.get("agentId")
    _moltbook_state["agent_name"] = data.get("agentName")
    return _moltbook_state["identity_token"]


def _moltbook_request(method: str, path: str, json_data: dict = None, deadline_sec: float = None):
    # ЗАЩИТА от утечки секретов
    if json_data:
        try:
            from _actions.tools_leak_guard import guard as _leak_guard
            _leak_guard(json_data)
        except ImportError:
            pass

    # DM недавно отдал 404 — не дёргаем Tor и API зря
    if "/dm/" in path and _dm_is_disabled():
        return _FakeResp(path)

    """Отправляет запрос к Moltbook API с автоматическим обновлением токена."""
    budget = deadline_sec if deadline_sec is not None else _MOLTBOOK_DEADLINE
    deadline = _time.monotonic() + budget
    token = _moltbook_get_token(deadline=deadline, budget_sec=budget)
    proxies = {"http": "socks5h://127.0.0.1:9050",
               "https": "socks5h://127.0.0.1:9050"}
    headers = {
        "Authorization": f"Bearer {_moltbook_state['api_key']}",
        "X-Moltbook-Identity": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    if json_data:
        headers["Content-Type"] = "application/json"
    url = f"https://www.moltbook.com{path}"

    def _do(method, url, headers, json_data, proxies, timeout: float = 30):
        if method == "GET":
            return requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
        elif method == "POST":
            return requests.post(url, headers=headers, json=json_data,
                                 proxies=proxies, timeout=timeout)
        elif method == "DELETE":
            return requests.delete(url, headers=headers, json=json_data,
                                   proxies=proxies, timeout=timeout)
        elif method == "PUT":
            return requests.put(url, headers=headers, json=json_data,
                                proxies=proxies, timeout=timeout)
        elif method == "PATCH":
            return requests.patch(url, headers=headers, json=json_data,
                                  proxies=proxies, timeout=timeout)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

    try:
        from _tor import new_circuit as _new_circuit
    except Exception:
        _new_circuit = None

    max_attempts = 10
    resp = None
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        if _deadline_expired(deadline):
            raise RuntimeError(
                f"Moltbook {method} {path}: бюджет {budget:.0f}с исчерпан "
                f"({attempt - 1} попыток)")
        try:
            resp = _do(method, url, headers, json_data, proxies, timeout=_remaining(deadline))
        except Exception as e:
            last_exc = e
            if _new_circuit is not None and attempt < max_attempts:
                _new_circuit(wait_sec=2.0)
                continue
            raise

        if resp.status_code == 401 and "expired" in resp.text.lower():
            token = _moltbook_get_token(force=True, deadline=deadline, budget_sec=budget)
            headers["X-Moltbook-Identity"] = token
            try:
                resp = _do(method, url, headers, json_data, proxies, timeout=_remaining(deadline))
            except Exception as e:
                last_exc = e
                if _new_circuit is not None and attempt < max_attempts:
                    _new_circuit(wait_sec=2.0)
                    continue
                raise
        if resp.status_code == 403 and "cloudfront" in resp.text.lower() \
                and _new_circuit is not None and attempt < max_attempts:
            _new_circuit(wait_sec=2.0)
            continue
        break
    return resp


_raw_cache = {}
_RAW_CACHE_TTL = 300.0


def moltbook_raw_request(method: str, url: str, json_data: dict = None,
                        use_cache: bool = True) -> str:
    """Произвольный запрос к Moltbook API. Возвращает текст ответа.

    Кэширует ответы на _RAW_CACHE_TTL секунд (по умолчанию 5 минут),
    чтобы постраничный просмотр одного и того же пути не дёргал API.
    Обход кэша — use_cache=False (приходит из параметра "обновить").
    """
    parsed = urlparse(url)
    path = parsed.path
    if parsed.query:
        path += f"?{parsed.query}"
    import json as _json_c
    try:
        _body_key = _json_c.dumps(json_data, sort_keys=True, ensure_ascii=False) if json_data else ""
    except Exception:
        _body_key = str(json_data)
    _key = (method, path, _body_key)
    if use_cache and _key in _raw_cache:
        _ts, _txt = _raw_cache[_key]
        if _time.time() - _ts < _RAW_CACHE_TTL:
            return _txt
    resp = _moltbook_request(method, path, json_data=json_data)
    _txt = f"Статус: {resp.status_code}\n{resp.text}"
    if use_cache:
        _raw_cache[_key] = (_time.time(), _txt)
    return _txt


# ---------- Высокоуровневые функции ----------

def moltbook_feed(sort: str = "new", limit: int = 5,
                  only_following: bool = False) -> str:
    try:
        if only_following:
            # /api/v1/feed?filter=following — лента только подписок
            path = f"/api/v1/feed?filter=following&limit={limit}"
        else:
            path = f"/api/v1/posts?sort={sort}&limit={limit}"
        resp = _moltbook_request("GET", path)
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: Moltbook вернул неуспех"
        posts = data.get("posts", [])
        if not posts:
            return "В ленте пока нет постов."
        _mark = "подписки" if only_following else sort
        result = f"📰 Лента Moltbook (сортировка: {_mark}, показано {len(posts)}):\n\n"
        for post in posts:
            title = post.get("title", "Без заголовка")
            author = post.get("author", {}).get("name", "неизвестный")
            content = post.get("content", "")[:150]
            pid = post.get("id", "")
            result += f"📌 {title} (автор: {author})\n"
            result += f"   ID: {pid}\n"
            result += f"   {content}...\n\n"
        return result
    except Exception as e:
        return f"Ошибка получения ленты Moltbook: {e}"


def moltbook_profile(agent_name: str) -> str:
    try:
        resp = _moltbook_request("GET", f"/api/v1/agents/profile?name={agent_name}")
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Агент не найден или ошибка API"
        agent = data.get("agent", {})
        profile = f"👤 Профиль {agent.get('name', agent_name)}:\n"
        profile += f"   Описание: {agent.get('description', 'нет')}\n"
        profile += f"   Карма: {agent.get('karma', 0)}\n"
        profile += f"   Подписчиков: {agent.get('follower_count', 0)}\n"
        profile += f"   Постов: {agent.get('posts_count', 0)}\n"
        profile += f"   Комментариев: {agent.get('comments_count', 0)}\n"
        profile += f"   Активен: {agent.get('is_active', False)}\n"
        profile += f"   Последняя активность: {agent.get('last_active', '—')}"
        return profile
    except Exception as e:
        return f"Ошибка получения профиля Moltbook: {e}"


def moltbook_create_post(title: str, content: str, submolt_name: str = None) -> str:
    payload = {"title": title, "content": content}
    if submolt_name:
        payload["submolt_name"] = submolt_name
    try:
        resp = _moltbook_request("POST", "/api/v1/posts", json_data=payload)
        if resp.status_code == 201:
            post = resp.json()
            post_id = post["post"]["id"]
            if not post_id:
                post.get("id", "неизвестен")
            return f"✅ Пост опубликован в Moltbook! ID: {post_id}"
        return f"Ошибка публикации: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка отправки поста в Moltbook: {e}"


def moltbook_upvote(post_id: str) -> str:
    try:
        resp = _moltbook_request("POST", f"/api/v1/posts/{post_id}/upvote")
        if resp.status_code == 200:
            return "👍 Лайк поставлен в Moltbook"
        return f"Ошибка лайка: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка при голосовании: {e}"


def moltbook_refresh_token() -> str:
    try:
        token = _moltbook_get_token(force=True)
        expiry_time = datetime.fromtimestamp(_moltbook_state["token_expiry"]).strftime("%H:%M:%S")
        try:
            resp = _moltbook_request("GET", "/api/v1/home")
            if resp.status_code == 200:
                home_data = resp.json()
                return f"Токен Moltbook обновлён (действителен до {expiry_time})\n{home_data}"
        except Exception:
            return f"Токен Moltbook обновлён (действителен до {expiry_time})"
    except Exception as e:
        return f"Не удалось обновить токен: {e}"


def moltbook_comments(post_id: str, sort: str = "best", limit: int = 10) -> str:
    try:
        resp = _moltbook_request("GET",
            f"/api/v1/posts/{post_id}/comments?sort={sort}&limit={limit}")
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось загрузить комментарии"
        comments = data.get("comments", [])
        if not comments:
            return "Комментариев пока нет."
        result = f"💬 Комментарии к посту {post_id} (показано {len(comments)}):\n\n"
        for c in comments:
            author = c.get("author", {}).get("name", "неизвестный")
            content = c.get("content", "")[:200]
            cid = c.get("id", "")
            result += f"  {author}:\n"
            result += f"  {content}\n"
            result += f"  ID: {cid}\n\n"
        return result
    except Exception as e:
        return f"Ошибка получения комментариев: {e}"


def moltbook_add_comment(post_id: str, content: str) -> str:
    try:
        resp = _moltbook_request("POST", f"/api/v1/posts/{post_id}/comments",
                                  json_data={"content": content})
        if resp.status_code == 201:
            comment = resp.json()
            return f"✅ Комментарий добавлен! ID: {comment.get('comment', {}).get('id', 'неизвестен')}"
        return f"Ошибка добавления комментария: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка отправки комментария: {e}"


def moltbook_follow(agent_name: str) -> str:
    try:
        resp = _moltbook_request("POST", f"/api/v1/agents/{agent_name}/follow")
        if resp.status_code == 201:
            return f"✅ Теперь вы подписаны на {agent_name}!"
        return f"Ошибка подписки: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка подписки: {e}"


def moltbook_submolts(sort: str = "popular", limit: int = 5) -> str:
    try:
        resp = _moltbook_request("GET", f"/api/v1/submolts?sort={sort}&limit={limit}")
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось загрузить список сообществ"
        submolts = data.get("submolts", [])
        if not submolts:
            return "Сообщества не найдены."
        result = f"🌍 Сообщества Moltbook (сортировка: {sort}, показано {len(submolts)}):\n\n"
        for s in submolts:
            name = s.get("name", "без имени")
            display = s.get("display_name", name)
            desc = s.get("description", "")[:150]
            subs = s.get("subscriber_count", 0)
            posts = s.get("post_count", 0)
            result += f"  📌 {display} (m/{name})\n"
            result += f" {desc}\n"
            result += f" Подписчиков: {subs}, Постов: {posts}\n\n"
        return result
    except Exception as e:
        return f"Ошибка получения сообществ Moltbook: {e}"


def moltbook_join_submolt(submolt_name: str) -> str:
    try:
        resp = _moltbook_request("POST", f"/api/v1/submolts/{submolt_name}/subscribe")
        if resp.status_code == 201:
            return f"✅ Вы присоединились к сообществу m/{submolt_name}!"
        return f"Ошибка подписки на сообщество: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка присоединения к сообществу: {e}"


_notif_cache = {"ts": 0.0, "data": None}
_NOTIF_CACHE_TTL = 300.0


def moltbook_notifications(limit: int = 10, detailed: bool = False) -> str:
    """Список уведомлений. detailed=True — с полным текстом постов и комментариев."""
    try:
        import time as _t
        now = _t.time()
        cached = False
        if _notif_cache["data"] is not None and now - _notif_cache["ts"] < _NOTIF_CACHE_TTL:
            notifications = _notif_cache["data"]
            cached = True
        else:
            resp = _moltbook_request("GET", "/api/v1/notifications?limit=100")
            if resp.status_code != 200:
                return "Ошибка Moltbook: " + str(resp.status_code) + " " + resp.text[:500]
            data = resp.json()
            if data.get("error") or data.get("message"):
                err = data.get("error") or data.get("message")
                return "Ошибка загрузки уведомлений: " + str(err)
            notifications = data.get("notifications")
            if notifications is None:
                return "Неожиданный формат: " + json.dumps(data, ensure_ascii=False)[:200]
            _notif_cache["ts"] = now
            _notif_cache["data"] = notifications

        if not notifications:
            return "Уведомлений нет."

        shown = notifications[:max(1, int(limit))]
        bell = chr(0x1F514)
        sep = chr(0x2500) * 40
        parts = [bell + " Уведомления (показано " + str(len(shown)) + " из " + str(len(notifications)) + "):"]
        for n in shown:
            if detailed:
                parts.append("")
                parts.append(sep)
                parts.append(_format_notification(n))
            else:
                n_type = n.get("type", "unknown")
                n_id = n.get("id", "?")
                msg = n.get("content") or n.get("message") or ""
                created = n.get("createdAt") or n.get("created_at") or ""
                read = chr(0x2705) if n.get("isRead") else chr(0x1F534)
                parts.append(read + " [" + n_type + "] (id: " + n_id + ") " + msg + " (" + created + ")")
        if cached:
            parts.append("")
            parts.append("[кэш] ответ менее 5 минут назад")
        return chr(10).join(parts)
    except Exception as e:
        return "Ошибка получения уведомлений: " + str(e)

def moltbook_dm_requests() -> str:
    try:
        resp = _moltbook_request("GET", "/api/v1/agents/dm/requests")
        _dm_msg = _dm_check_404(resp)
        if _dm_msg:
            return _dm_msg
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось загрузить запросы"
        reqs = data.get("requests", [])
        if not reqs:
            return "Нет входящих запросов на диалог."
        result = "💬 Запросы на диалог:\n\n"
        for r in reqs:
            req_id = r.get("id")
            from_agent = r.get("from_agent", {}).get("name", "неизвестный")
            msg = r.get("message", "")
            status = r.get("status", "")
            result += f"  ID: {req_id} | от: {from_agent} | статус: {status}\n"
            result += f"  Сообщение: {msg}\n\n"
        return result
    except Exception as e:
        return f"Ошибка получения запросов: {e}"


def moltbook_dm_approve(request_id: str) -> str:
    try:
        resp = _moltbook_request("POST", f"/api/v1/agents/dm/requests/{request_id}/approve")
        _dm_msg = _dm_check_404(resp)
        if _dm_msg:
            return _dm_msg
        if resp.status_code == 200:
            return f"✅ Запрос {request_id} принят. Диалог открыт."
        return f"Ошибка принятия запроса: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка: {e}"


def moltbook_dm_reject(request_id: str) -> str:
    try:
        resp = _moltbook_request("POST", f"/api/v1/agents/dm/requests/{request_id}/reject")
        _dm_msg = _dm_check_404(resp)
        if _dm_msg:
            return _dm_msg
        if resp.status_code == 200:
            return f"❌ Запрос {request_id} отклонён."
        return f"Ошибка отклонения запроса: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка: {e}"


def moltbook_conversations(limit: int = 10) -> str:
    try:
        resp = _moltbook_request("GET", f"/api/v1/agents/dm/conversations?limit={limit}")
        _dm_msg = _dm_check_404(resp)
        if _dm_msg:
            return _dm_msg
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось загрузить диалоги"
        convs = data.get("conversations", [])
        if not convs:
            return "Активных диалогов нет."
        result = "📨 Активные диалоги:\n\n"
        for c in convs:
            cid = c.get("id")
            with_agent = c.get("with_agent", {}).get("name", "неизвестный")
            last_msg = c.get("last_message", {}).get("content", "")
            updated = c.get("updated_at", "")
            result += f"  ID: {cid} | с: {with_agent}\n"
            result += f"  Последнее сообщение: {last_msg[:100]}\n"
            result += f"  Обновлён: {updated}\n\n"
        return result
    except Exception as e:
        return f"Ошибка получения диалогов: {e}"


def moltbook_messages(conversation_id: str, limit: int = 20) -> str:
    try:
        resp = _moltbook_request(
            "GET",
            f"/api/v1/agents/dm/conversations/{conversation_id}")
        _dm_msg = _dm_check_404(resp)
        if _dm_msg:
            return _dm_msg
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось загрузить сообщения"
        messages = data.get("messages", [])
        if not messages:
            return "Сообщений в диалоге нет."
        result = f"💬 Сообщения (диалог {conversation_id}):\n\n"
        for m in messages:
            sender = m.get("sender", {}).get("name", "неизвестный")
            content = m.get("content", "")
            created = m.get("created_at", "")
            result += f"{sender} ({created}):\n{content}\n\n"
        return result
    except Exception as e:
        return f"Ошибка получения сообщений: {e}"


def moltbook_send_dm(agent_name: str, text: str) -> str:
    try:
        convs_resp = _moltbook_request("GET", "/api/v1/agents/dm/conversations?limit=100")
        _dm_msg = _dm_check_404(convs_resp)
        if _dm_msg:
            return _dm_msg
        if convs_resp.status_code != 200:
            return f"Ошибка при проверке диалогов: {convs_resp.status_code}"
        convs_data = convs_resp.json()
        conv_id = None
        if convs_data.get("success"):
            for c in convs_data.get("conversations", []):
                if c.get("with_agent", {}).get("name") == agent_name:
                    conv_id = c.get("id")
                    break

        if conv_id:
            resp = _moltbook_request(
                "POST", f"/api/v1/agents/dm/conversations/{conv_id}/send",
                json_data={"content": text})
            if resp.status_code == 200:
                return f"✅ Сообщение отправлено агенту {agent_name} (диалог {conv_id})"
            return f"Ошибка отправки: {resp.status_code} {resp.text[:200]}"
        else:
            resp = _moltbook_request(
                "POST", "/api/v1/agents/dm/request",
                json_data={"target_agent_name": agent_name, "message": text})
            if resp.status_code == 201:
                return f"📨 Запрос на диалог отправлен агенту {agent_name}"
            return f"Ошибка создания запроса: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка отправки DM: {e}"


def moltbook_read_notifications(post_id: str = None) -> str:
    try:
        if post_id:
            resp = _moltbook_request("POST",
                f"/api/v1/notifications/read-by-post/{post_id}")
            if resp.status_code == 200:
                return f"✅ Уведомления для поста {post_id} отмечены прочитанными."
            return f"Ошибка: {resp.status_code} {resp.text[:200]}"
        else:
            resp = _moltbook_request("POST", "/api/v1/notifications/read-all")
            if resp.status_code == 200:
                return "✅ Все уведомления отмечены прочитанными."
            return f"Ошибка: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка: {e}"


def moltbook_search(query: str, search_type: str = None) -> str:
    try:
        encoded_query = quote(query)
        params = f"?q={encoded_query}"
        if search_type in ("agents", "posts"):
            params += f"&type={search_type}"
        resp = _moltbook_request("GET", f"/api/v1/search{params}")
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: поиск не удался"
        results = data.get("results", [])
        if not results:
            return "Ничего не найдено."
        result = f"🔍 Результаты поиска по '{query}':\n\n"
        for r in results:
            r_type = r.get("type")
            name = r.get("name") or r.get("title") or "без названия"
            desc = r.get("description") or r.get("content_preview") or ""
            post_id = r.get("post_id") or r.get("id") or "?"
            result += f"  [{r_type}] ID: {post_id}\n"
            result += f"  {name}\n"
            result += f"  {desc[:150]}\n\n"
        return result
    except Exception as e:
        return f"Ошибка поиска: {e}"


def moltbook_create_submolt(name: str, description: str) -> str:
    try:
        resp = _moltbook_request("POST", "/api/v1/submolts",
                                  json_data={"name": name, "description": description})
        if resp.status_code == 201:
            return f"✅ Сообщество m/{name} создано!"
        return f"Ошибка создания сообщества: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"Ошибка: {e}"


def moltbook_followers(agent_name: str) -> str:
    try:
        resp = _moltbook_request("GET", f"/api/v1/agents/{agent_name}/followers")
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось получить подписчиков"
        followers = data.get("followers", [])
        if not followers:
            return f"У {agent_name} нет подписчиков."
        result = f"👥 Подписчики {agent_name}:\n"
        for f in followers:
            name = f.get("name", "неизвестный")
            result += f"  - {name}\n"
        return result
    except Exception as e:
        return f"Ошибка: {e}"


def moltbook_following(agent_name: str) -> str:
    try:
        resp = _moltbook_request("GET", f"/api/v1/agents/{agent_name}/following")
        if resp.status_code != 200:
            return f"Ошибка Moltbook: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        if not data.get("success"):
            return "Ошибка: не удалось получить подписки"
        following = data.get("following", [])
        if not following:
            return f"{agent_name} ни на кого не подписан."
        result = f"📌 Подписки {agent_name}:\n"
        for f in following:
            name = f.get("name", "неизвестный")
            result += f"  - {name}\n"
        return result
    except Exception as e:
        return f"Ошибка: {e}"

def _format_notification(n):
    """Форматирует одно уведомление с полным постом и комментарием."""
    nid = n.get("id", "?")
    n_type = n.get("type", "?")
    msg = n.get("content") or n.get("message") or ""
    created = n.get("createdAt") or n.get("created_at") or ""
    read = "\u2705" if n.get("isRead") else "\U0001f534"
    lines = [read + " [" + n_type + "] (id: " + nid + ")"]
    lines.append("   " + msg)
    lines.append("   " + created)

    post = n.get("post") or {}
    if post:
        pid = post.get("id", "?")
        title = post.get("title") or "(без заголовка)"
        content = (post.get("content") or "")[:3000]
        up = post.get("upvotes", 0)
        cc = post.get("commentCount", 0)
        deleted = " [удалён]" if post.get("isDeleted") else ""
        lines.append("")
        lines.append("\U0001f4cc Пост" + deleted + ": " + title)
        lines.append("   ID: " + str(pid) + " | upvotes: " + str(up) + " | comments: " + str(cc))
        if content:
            lines.append("")
            lines.append(content)

    comment = n.get("comment") or {}
    if comment:
        cid = comment.get("id", "?")
        c_content = (comment.get("content") or "")[:3000]
        c_author = comment.get("authorId", "?")
        c_created = comment.get("createdAt") or comment.get("created_at") or ""
        c_deleted = " [удалён]" if comment.get("isDeleted") else ""
        lines.append("")
        lines.append("\U0001f4ac Комментарий" + c_deleted + ":")
        lines.append("   ID: " + str(cid) + " | автор: " + str(c_author) + " | " + str(c_created))
        if c_content:
            lines.append("")
            lines.append(c_content)

    return "\n".join(lines)


def moltbook_notification_by_id(notif_id):
    """Найти уведомление по id и показать подробно: пост + комментарий."""
    if not notif_id:
        return "Укажите id уведомления."
    import time as _t
    now = _t.time()
    cached = False
    if _notif_cache["data"] is not None and now - _notif_cache["ts"] < _NOTIF_CACHE_TTL:
        notifications = _notif_cache["data"]
        cached = True
    else:
        try:
            resp = _moltbook_request("GET", "/api/v1/notifications?limit=100")
            if resp.status_code != 200:
                return "Ошибка Moltbook: " + str(resp.status_code) + " " + resp.text[:200]
            data = resp.json()
            notifications = data.get("notifications") or []
            _notif_cache["ts"] = now
            _notif_cache["data"] = notifications
        except Exception as e:
            return "Ошибка: " + str(e)
    target = None
    for n in notifications:
        if n.get("id") == notif_id:
            target = n
            break
    if target is None:
        return ("Уведомление " + str(notif_id) + " не найдено (получено "
                + str(len(notifications)) + "). Список: молтбук_уведомления")
    out = _format_notification(target)
    if cached:
        out += chr(10) + chr(10) + "[кэш] ответ менее 5 минут назад"
    return out


def moltbook_whoami() -> str:
    """Кто я в Moltbook: имя, карма, непрочитанные."""
    try:
        resp = _with_progress_bar(
            "молтбук_кто_я",
            30,
            lambda: _moltbook_request("GET", "/api/v1/home", deadline_sec=30),
        )
        if resp.status_code != 200:
            return "Ошибка Moltbook: " + str(resp.status_code) + " " + resp.text[:200]
        data = resp.json()
        acc = data.get("your_account") or {}
        if not acc:
            return "Не удалось получить данные аккаунта. Ответ: " + json.dumps(data, ensure_ascii=False)[:300]
        name = acc.get("name", "?")
        karma = acc.get("karma", "?")
        unread = acc.get("unread_notification_count", "?")
        lines = [
            chr(0x1F464) + " Я в Moltbook: " + str(name),
            "   карма: " + str(karma),
            "   непрочитанных: " + str(unread),
        ]
        return chr(10).join(lines)
    except Exception as e:
        return "Ошибка: " + str(e)

