"""Moltbook: глобальная сеть агентов (через Tor)."""
from _actions import register
from _tor import ensure_tor, restart_tor as _restart_tor
from _actions.moltbook import _need_tor



@register(["молтбук_уведомления", "молтбук_прочитать_уведомления"])
def notifications(args):
    """Список уведомлений Moltbook.

    Параметры:
      количество — сколько показать (по умолчанию 10)
      подробно   — true: с полным текстом постов и комментариев

    Примеры:
      {"молтбук_уведомления": {"количество": 20}}
      {"молтбук_уведомления": {"подробно": true}}

    Одно уведомление по id — команда "молтбук_уведомление".
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    from _actions._tools_moltbook import moltbook_notifications
    detailed = args.get("подробно")
    if isinstance(detailed, str):
        detailed = detailed.strip().lower() not in ("0", "false", "нет", "off", "")
    return moltbook_notifications(args.get("количество", 10), detailed=bool(detailed))


@register("молтбук_дм_запросы")
def dm_requests(args):
    """⚠️ Временно недоступно: Moltbook не отдаёт DM API.

Входящие запросы на диалог (DM).

    Параметров нет.

    Пример:
      {"молтбук_дм_запросы": {}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    from _actions._tools_moltbook import moltbook_dm_requests
    return moltbook_dm_requests()


@register("молтбук_дм_принять")
def dm_approve(args):
    """⚠️ Временно недоступно: Moltbook не отдаёт DM API.

Принять запрос на диалог.

    Параметры:
      запрос_id — ID запроса (обязательный)

    Пример:
      {"молтбук_дм_принять": {"запрос_id": "abc123"}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    req_id = args.get("запрос_id")
    if not req_id:
        return "Укажите 'запрос_id'"
    from _actions._tools_moltbook import moltbook_dm_approve
    return moltbook_dm_approve(req_id)


@register("молтбук_дм_отклонить")
def dm_reject(args):
    """⚠️ Временно недоступно: Moltbook не отдаёт DM API.

Отклонить запрос на диалог.

    Параметры:
      запрос_id — ID запроса (обязательный)

    Пример:
      {"молтбук_дм_отклонить": {"запрос_id": "abc123"}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    req_id = args.get("запрос_id")
    if not req_id:
        return "Укажите 'запрос_id'"
    from _actions._tools_moltbook import moltbook_dm_reject
    return moltbook_dm_reject(req_id)


@register("молтбук_диалоги")
def conversations(args):
    """⚠️ Временно недоступно: Moltbook не отдаёт DM API.

Список активных диалогов (DM).

    Параметры:
      количество — сколько показать (по умолчанию 10)

    Пример:
      {"молтбук_диалоги": {"количество": 20}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    from _actions._tools_moltbook import moltbook_conversations
    return moltbook_conversations(args.get("количество", 10))


@register("молтбук_сообщения")
def messages(args):
    """⚠️ Временно недоступно: Moltbook не отдаёт DM API.

Прочитать сообщения в диалоге.

    Параметры:
      диалог_id  — ID диалога (обязательный)
      количество — сколько показать (по умолчанию 20)

    Пример:
      {"молтбук_сообщения": {"диалог_id": "abc123", "количество": 50}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    conv_id = args.get("диалог_id")
    if not conv_id:
        return "Укажите 'диалог_id'"
    from _actions._tools_moltbook import moltbook_messages
    return moltbook_messages(conv_id, args.get("количество", 20))


@register("молтбук_дм_отправить")
def send_dm(args):
    """⚠️ Временно недоступно: Moltbook не отдаёт DM API.

Отправить личное сообщение агенту.

    Если диалога нет — создаётся запрос на диалог.

    Параметры:
      агент — имя получателя (обязательный)
      текст — текст сообщения (обязательный)

    Пример:
      {"молтбук_дм_отправить": {"агент": "vina", "текст": "привет"}}
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    agent = args.get("агент")
    text = args.get("текст")
    if not agent or not text:
        return "Нужны 'агент' и 'текст'"
    from _actions._tools_moltbook import moltbook_send_dm
    return moltbook_send_dm(agent, text)


@register(["молтбук_отметить_уведомления",
           "молтбук_отметить_прочитанным"])
def read_notifications(args):
    """Отметить уведомления прочитанными.

    ⚠️ ВНИМАНИЕ: эта команда НЕ получает уведомления, а ОТМЕЧАЕТ их прочитанными.
    Чтобы получить список уведомлений, используйте {"молтбук_уведомления": {}}.

    Параметры:
      пост_id — если указан, отметить только уведомления по этому посту;
                иначе — отметить ВСЕ уведомления.

    Примеры:
      {"молтбук_отметить_уведомления": {}}
      {"молтбук_отметить_уведомления": {"пост_id": "29c18f2b-..."}}

    Синонимы: молтбук_отметить_прочитанным, молтбук_прочитать_всё.
    """
    if not _need_tor():
        return "❌ Tor недоступен."
    from _actions._tools_moltbook import moltbook_read_notifications
    return moltbook_read_notifications(args.get("пост_id") or args.get("id") or args.get("post_id"))

@register(["молтбук_уведомление", "молтбук_уведомление_читать"])
def notification_by_id(args):
    """Прочитать одно уведомление Moltbook по id — с полным текстом поста и комментария.

    Параметры:
      id — UUID уведомления (обязательный)

    Пример:
      {"молтбук_уведомление": {"id": "28de6fdc-ffc7-4df3-82ee-4fff4ba30f0b"}}

    Список уведомлений: {"молтбук_уведомления": {}}
    """
    if not _need_tor():
        return "Tor недоступен."
    if not isinstance(args, dict):
        args = {}
    nid = args.get("id") or args.get("уведомление_id") or args.get("notif_id")
    if not nid:
        return "Укажите id уведомления. Список: молтбук_уведомления"
    from _actions._tools_moltbook import moltbook_notification_by_id
    return moltbook_notification_by_id(nid)

