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

@register(["логирование", "лог"])
def logging_cmd(args):
    """Записать событие в память в виде "key=value, key=value, ...".

    Все параметры команды превращаются в одну строку и сохраняются
    через команду «запомнить» (с датой впереди).

    Пример:
      {"логирование": {"уровень": "ошибка", "источник": "Moltbook",
                       "код": 404, "эндпоинт": "/api/v1/..."}}
    сохранит: "16.09.2026 - уровень=ошибка, источник=Moltbook, код=404, ..."
    """
    if not isinstance(args, dict) or not args:
        return "Ошибка: пустые параметры логирования"
    parts = [f"{k}={v}" for k, v in args.items()]
    text = ", ".join(parts)
    return remember({"содержимое": text})


@register(["подготовка_содержимого", "подготовка"])
def prepare_content(args):
    """Сохранить варианты/план в память через «запомнить».

    Все параметры команды превращаются в одну строку "key=value, ...",
    к ней добавляется префикс "подготовка_содержимого:".

    Пример:
      {"подготовка_содержимого": {"нужно": true,
                                  "варианты": ["a", "b"],
                                  "примечание": "..."}}
    сохранит: "16.09.2026 - подготовка_содержимого: нужно=true, варианты=['a', 'b'], примечание=..."
    """
    if not isinstance(args, dict) or not args:
        return "Ошибка: пустые параметры подготовки"
    parts = [f"{k}={v}" for k, v in args.items()]
    text = "подготовка_содержимого: " + ", ".join(parts)
    return remember({"содержимое": text})


@register(["запомнить", "в_постоянную_память", "в_память", "сохрани_в_память"])
def remember(args):
    """Сохранить запись в память агента.

    Параметры:
      содержимое — текст записи, алиасы: содержание, запомнить, текст
      ключ       — первая часть пары (склеивается с значением через " — ")
      значение   — вторая часть пары

    Запись сохраняется с датой впереди: "ДД.ММ.ГГГГ - <текст>".

    Примеры:
      {"запомнить": {"содержимое": "проверить Moltbook в 11:00"}}
      {"запомнить": "короткая запись"}
    """
    text = (args.get("содержимое") or args.get("содержание")
            or args.get("запомнить") or args.get("текст"))
    if not text:
        # пара ключ/значение → склеиваем
        _k = args.get("ключ") or args.get("key") or args.get("имя")
        _v = args.get("значение") or args.get("value") or args.get("текст_значения")
        if _k and _v:
            text = f"{_k} — {_v}"
        elif _v:
            text = str(_v)
        elif _k:
            text = str(_k)
    if not text:
        return "Ошибка: пусто"
    memlist = load_memlist()
    import re as _re_dt
    _clean_txt = _re_dt.sub(r"^\d{2}\.\d{2}\.\d{4}\s*-\s*", "", str(text).strip())
    dated = f"{datetime.now().strftime('%d.%m.%Y')} - {_clean_txt}"
    if dated in memlist:
        return f"⚠️ Такая запись уже есть за сегодня: {dated}"
    memlist.append(dated)
    save_memlist(memlist)
    return (
        f"Запись добавлена: {dated}\n"
        '💡 Обязанность агента: устаревшие или дублирующиеся записи памяти переписывай и чисти через:\n'
        '{"заменить_текст": {"путь": "память", "найти": "<старый фрагмент>", "заменить": "<актуальный текст>"}}'
    )


@register(["вспомнить", "воспоминание"])
def recall(args):
    """Вспомнить записи из памяти агента (от самых свежих к старым).

    Параметры:
      про        — подстрока для поиска (регистр не важен);
                   поддерживает "|" для ИЛИ: "AGI|LLM"
                   без параметра — вернуть последние записи
      количество — сколько последних записей показать (алиасы: лимит, limit, n)
      кб         — максимальный размер вывода в КБ (по умолчанию 4)

    Примеры:
      {"вспомнить": {"про": "Moltbook"}}
      {"вспомнить": {"про": "ошибка|403"}}
      {"вспомнить": {"количество": 5}}
      {"вспомнить": {}}

    Алиас: воспоминание.
    """
    args = args if isinstance(args, dict) else {"про": str(args)}
    keyword = (args.get("про") or args.get("инцидент")
               or args.get("ключ") or args.get("запрос") or "")
    raw_limit = (args.get("количество") or args.get("лимит")
                 or args.get("limit") or args.get("n"))
    try:
        max_items = max(1, int(raw_limit)) if raw_limit is not None else None
    except (TypeError, ValueError):
        max_items = None

    memlist = load_memlist()

    def normalize(item):
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            for k in ["текст", "содержимое", "content", "text", "описание"]:
                if k in item:
                    v = item[k]
                    return v if isinstance(v, str) else str(v)
            return str(item)
        return str(item)

    keywords = [kw.strip().lower() for kw in keyword.split('|') if kw.strip()] if keyword.strip() else []
    max_bytes = int(args.get("кб", 4)) * 1024
    found = []
    total = 0
    for item in reversed(memlist):
        text = normalize(item)
        if keywords and not any(kw in text.lower() for kw in keywords):
            continue
        line = text + "\n"
        size = len(line.encode('utf-8'))
        if total + size > max_bytes:
            break
        found.append(text)
        total += size
        if max_items is not None and len(found) >= max_items:
            break
    if found:
        return (
            "\n".join(found)
            + '\n\n💡 Если видишь устаревшие/дублирующиеся воспоминания — перепиши или удали их:\n'
            + '{"заменить_текст": {"путь": "память", "найти": "<устаревший фрагмент>", "заменить": "<новый текст или пусто>"}}'
        )
    return ("Ничего не найдено.\n\n"
            "💡 Используй \"запомнить\" — сессия конечна, а память останется.\n"
            '{"запомнить": {"содержимое": "..."}}')

