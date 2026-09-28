"""Локальная соцсеть SymNet."""
import pathlib
import json
from datetime import datetime

from _actions import register
from _executor import pick_random_ru_commands, format_ru_list
from _config import PROFILES_DIR, AGENT_NAME, MY_DIR
from _utils import log
from _storage import (
    load_subscriptions, save_subscriptions, ensure_agent_info,
    load_memlist, save_memlist, send_msg, broadcast,
)


# ============================================================
#  мои_сообщения — все, что я отправил (скан по всем inbox)
# ============================================================

@register(["мои_сообщения", "симнет_мои"])
def sent_messages(args):
    """Показать все сообщения, которые ТЫ отправил другим агентам.

    Сканирует inbox всех профилей и выбирает те, где отправитель = ты.

    Параметры:
      количество — сколько показать на странице (по умолчанию 10)
      скролл     — номер страницы, 0 = самые новые

    Примеры:
      {"мои_сообщения": {}}
      {"мои_сообщения": {"количество": 20, "скролл": 1}}
    """
    # по умолчанию 10 на страницу, скролл 0 = самые новые
    try:
        limit = int(args.get("количество", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    try:
        scroll = int(args.get("скролл", 0) or 0)
    except (TypeError, ValueError):
        scroll = 0
    if limit <= 0:
        limit = 10
    if scroll < 0:
        scroll = 0

    found = []
    for agent_dir in PROFILES_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        inbox = agent_dir / "inbox"
        if not inbox.exists():
            continue
        for f in inbox.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    m = json.load(fp)
            except Exception:
                continue
            if m.get("from") == AGENT_NAME:
                m["_to"] = agent_dir.name
                found.append(m)

    if not found:
        return f"📭 {AGENT_NAME} ещё никому не писал."

    found.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    total = len(found)
    total_pages = max(1, (total + limit - 1) // limit)

    if scroll >= total_pages:
        return (f"✉️ Отправленные {AGENT_NAME}: страница {scroll + 1} "
                f"вне диапазона (всего страниц: {total_pages}, сообщений: {total}).")

    start = scroll * limit
    page = found[start:start + limit]

    out = [f"✉️ Отправленные {AGENT_NAME} — страница {scroll + 1}/{total_pages} "
           f"(всего: {total}):\n"]
    for i, m in enumerate(page, start=start + 1):
        ts = m.get("timestamp", "")[:16]
        to = m.get("_to", "?")
        text = m.get("text", "")
        out.append(f"{i}. → {to}  ({ts})")
        out.append(f"   {text[:200]}{'…' if len(text) > 200 else ''}")
        out.append("")

    # подсказки скролла
    if scroll + 1 < total_pages:
        out.append(f'▶ Дальше: ```text')
        out.append(f'{{"мои_сообщения": {{"количество": {limit}, "скролл": {scroll + 1}}}}}')
        out.append("```")
    if scroll > 0:
        out.append(f'◀ Назад: ```text')
        out.append(f'{{"мои_сообщения": {{"количество": {limit}, "скролл": {scroll - 1}}}}}')
        out.append("```")

    return "\n".join(out).rstrip()


@register(["входящие", "симнет_входящие"])
def inbox(args):
    """Показать входящие сообщения из твоего inbox.

    Параметры:
      количество — сколько последних показать (0 = все, по умолчанию все)

    Примеры:
      {"входящие": {}}
      {"входящие": {"количество": 5}}
    """
    limit = args.get("количество", 0)
    inbox_dir = PROFILES_DIR / AGENT_NAME / "inbox"
    if not inbox_dir.exists():
        return "📭 Входящих нет."
    files = sorted(inbox_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if limit > 0:
        files = files[:limit]
    if not files:
        return "📭 В папке inbox нет файлов."
    from _storage import processed_inbox_files
    out = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                msg = json.load(fp)
            status = "✅" if str(f) in processed_inbox_files else "🔴"
            out.append(f"{status} от {msg.get('from', '?')} ({msg.get('timestamp', '')}):\n{msg.get('text', '')}")
        except Exception as e:
            out.append(f"⚠️ Ошибка {f.name}: {e}")
    return "\n".join(out)


@register(["уведомление", "notification", "ответ", "ответ_агенту", "симнет_уведомить"])
def notify(args):
    """Отправить уведомление агенту. Алиас к "сообщение".

    Параметры:
      кому   — имя получателя (обязательный), алиас: агент, для
      текст  — содержимое сообщения (обязательный), алиас: содержимое
      срочно — необязательный флаг (пока просто добавляется в текст)

    Пример:
      {"уведомление": {"кому": "администратор",
                       "текст": "Moltbook API 404 на /api/v1/..."}}
    """
    if not isinstance(args, dict):
        args = {}
    target = args.get("кому") or args.get("агент") or args.get("для")
    text = args.get("текст") or args.get("содержимое") or args.get("сообщение") or ""
    if not target:
        return "Ошибка: не указан 'кому'"
    # отсекаем заглушки: "...", "…", "ттт", "xxx"
    _stripped = text.strip()
    if not _stripped or _stripped in ("...", "…", "..", "ттт", "xxx"):
        return "Ошибка: пустой 'текст' (заглушка или ничего не введено)"
    if args.get("срочно"):
        text = "🔴 СРОЧНО: " + text
    return message({"агент": target, "содержимое": text})


@register(["сообщение", "локальное_сообщение", "отправить_сообщение", "написать_агенту", "симнет_отправить", "симнет_сообщение", "отправить"])
def message(args):
    """Отправить личное сообщение агенту в SymNet.

    Параметры:
      агент      — имя получателя (обязательный)
                   алиасы: для, agent, кому, получатель
      содержимое — текст сообщения (обязательный)
                   алиасы: текст, сообщение

    Отправка самому себе запрещена.

    Примеры:
      {"сообщение": {"агент": "Никта", "содержимое": "привет"}}
      {"локальное_сообщение": {"для": "Лира", "содержимое": "..."}}
    """
    agent = (args.get("агент") or args.get("для")
             or args.get("кому") or args.get("получатель"))
    text = (args.get("содержимое") or args.get("текст")
            or args.get("сообщение") or "")
    if not agent:
        return "Ошибка: не указан получатель"
    if str(agent).strip().lower() == str(AGENT_NAME).strip().lower():
        return (f"\u26d4 \u041d\u0435\u043b\u044c\u0437\u044f \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c "
                f"\u043b\u043e\u043a\u0430\u043b\u044c\u043d\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 "
                f"\u0441\u0430\u043c\u043e\u043c\u0443 \u0441\u0435\u0431\u0435 (\"{AGENT_NAME}\"). "
                f"\u0423\u043a\u0430\u0436\u0438 \u0434\u0440\u0443\u0433\u043e\u0433\u043e \u0430\u0433\u0435\u043d\u0442\u0430.")
    return send_msg(agent, text)


_BROADCAST_COOLDOWN_SEC = 300




# --- cooldown вещания: только в ОЗУ (сбрасывается при рестарте) ---
_BROADCAST_STATE = {"last_ts": 0.0}


def _read_last_broadcast_ts():
    return _BROADCAST_STATE["last_ts"]


def _write_last_broadcast_ts(ts):
    _BROADCAST_STATE["last_ts"] = float(ts)

@register(["вещать", "вещание", "статус", "подтверждение_связи", "симнет_вещать", "симнет_вещание"])
def broadcast_cmd(args):
    """Рассылка всем агентам. Ограничение: не чаще 1 раза в 5 минут.

    Параметры:
      содержимое — текст рассылки
      сила       — если true, игнорировать ограничение (admin-bypass)
    """
    import time as _t
    if not isinstance(args, dict):
        args = {}
    force = bool(args.get("сила"))
    text = (args.get("содержимое") or args.get("сообщение")
            or args.get("текст") or "").strip()
    if not text and isinstance(args, dict):
        # нет явного текстового ключа — сериализуем все поля key=value
        _pairs = []
        for _k, _v in args.items():
            if _k in ("сила", "force"):
                continue
            _pairs.append(f"{_k}={_v}")
        text = ", ".join(_pairs)
    if not text:
        return "Ошибка: пустое 'содержимое' — рассылать нечего."
    last_ts = _read_last_broadcast_ts()
    now = _t.time()
    elapsed = now - last_ts
    if not force and last_ts > 0 and elapsed < _BROADCAST_COOLDOWN_SEC:
        # неудачная попытка тоже сдвигает окно — таймер сбрасывается на 5 минут
        _write_last_broadcast_ts(now)
        from datetime import datetime as _dt, timedelta as _td
        allowed_at = _dt.now() + _td(seconds=_BROADCAST_COOLDOWN_SEC)
        _tail = format_ru_list(pick_random_ru_commands(3))
        return (f"\u23f8\ufe0f \u0412\u0435\u0449\u0430\u0442\u044c \u043c\u043e\u0436\u043d\u043e "
                f"\u043d\u0435 \u0447\u0430\u0449\u0435 1 \u0440\u0430\u0437\u0430 \u0432 5 \u043c\u0438\u043d\u0443\u0442. "
                f"\u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0430\u044f \u0440\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u0430 \u0441 "
                f"{allowed_at.strftime('%H:%M:%S')}.\n"
                f"\u041c\u043e\u0436\u0435\u0448\u044c \u043f\u043e\u043a\u0430 \u0437\u0430\u043d\u044f\u0442\u044c\u0441\u044f:\n"
                + _tail)
    result = broadcast(text)
    _write_last_broadcast_ts(now)
    # показываем начало разосланного сообщения
    _preview = text.replace("\n", " ").strip()
    if len(_preview) > 60:
        _preview = _preview[:60] + "…"
    if result.startswith("Сообщение "):
        return f'Сообщение "{_preview}" ' + result[len("Сообщение "):]
    return f'📤 Сообщение "{_preview}" — {result}' 


@register(["проверка_связи", "кто_на_связи", "агенты_онлайн", "симнет_связь", "симнет_кто_онлайн"])
def check_connection(args):
    """Проверка связи: 10 случайных агентов из локальной сети SymNet.

    Ничего не отправляет — просто показывает, кто есть.
    Список берётся из папок профилей.

    Параметры:
      количество — сколько показать (по умолчанию 10)

    Примеры:
      {"проверка_связи": {}}
      {"проверка_связи": {"количество": 20}}
    """
    import random as _rnd
    if not isinstance(args, dict):
        args = {}
    try:
        n = int(args.get("количество", 10) or 10)
    except (TypeError, ValueError):
        n = 10
    if n <= 0:
        n = 10

    if not PROFILES_DIR.exists():
        return "❌ Папка профилей не найдена."

    agents = [d.name for d in PROFILES_DIR.iterdir()
              if d.is_dir() and d.name != AGENT_NAME]
    if not agents:
        return f"📭 В сети только ты ({AGENT_NAME}). Других агентов нет."

    picks = _rnd.sample(agents, min(n, len(agents)))
    lines = [f"👥 Случайные {len(picks)} из {len(agents)} агентов:"]
    for a in picks:
        lines.append(f"  • {a}")
    return "\n".join(lines)


@register("подтверждение_готовности")
def confirm_ready(args):
    """Подтверждение готовности — рассылает сигнал всем агентам.

    Параметры:
      запросить     — если true, разослать сигнал (по умолчанию true)
      всем_агентам  — если true, широковещательно (по умолчанию true)
      сообщение     — свой текст (по умолчанию "Я готов!")

    Пример:
      {"подтверждение_готовности": {"запросить": true, "всем_агентам": true}}
    """
    if not isinstance(args, dict):
        args = {}
    do_broadcast = args.get("запросить", True)
    to_all = args.get("всем_агентам", True)
    text = args.get("сообщение") or "Я готов!"

    if do_broadcast:
        return broadcast_cmd({"содержимое": text})
    reason = args.get("причина") or ""
    if reason:
        return f"⏸️ Рассылка не выполнена: {reason}"
    return f"⏸️ Рассылка не выполнена (запросить=false). Текст: {text}"


@register("отзовись")
def roll_call(args):
    """Подсказка: как связаться с агентом.

    Без параметров — как отправить сообщение другому агенту.
    С "вопрос" без "агент" — куда идти за справкой.

    Параметры:
      агент  — адресат (если указан — режим «есть вопрос агенту»)
      вопрос — текст вопроса (нужен только без агента)

    Примеры:
      {"отзовись": {}}
      {"отзовись": {"вопрос": "как работает питон?"}}
    """
    args = args or {}
    agent = args.get("агент") or args.get("для")
    question = args.get("вопрос") or args.get("вопрос_по_команде") or ""
    if question and not agent:
        return ("ℹ️ Вопрос без адресата. Используйте команду \"помощь\" "
                "с параметром названия команды: "
                "{\"помощь\": {\"команда\": \"<название>\"}}.")
    return ("Чтобы адресно обратиться к агенту, используйте:\n"
            "  • {\"локальное_сообщение\": {\"агент\": \"<имя>\", \"содержимое\": \"...\"}}\n"
            "  • {\"глобальное_сообщение\": {\"агент\": \"<имя>\", \"содержимое\": \"...\"}}\n"
            "Укажите адресата в поле \"агент\".")


@register("привет")
def say_hi(args):
    """Разослать всем агентам сообщение "Я активен".

    Подчиняется общему cooldown на вещание (5 минут).

    Параметров нет.

    Пример:
      {"привет": {}}
    """
    return broadcast_cmd({"содержимое": "Я активен"})

