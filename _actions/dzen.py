"""Dzen: обновление канала и чтение скачанных статей."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from _actions import register
from _config import PUBLIC_TOOLS_DIR

PY_BROWSER = PUBLIC_TOOLS_DIR / "py_browser"
SCRAPER = PY_BROWSER / "scrape_channel.py"
DEFAULT_CHANNEL = "macreative"


def _slug(channel=None):
    ch = channel or DEFAULT_CHANNEL
    return ch.rstrip("/").split("/")[-1] or DEFAULT_CHANNEL


def _channel_dir(channel=None):
    return PY_BROWSER / "output" / _slug(channel)


def _iter_articles(channel=None):
    d = _channel_dir(channel)
    if not d.exists():
        return []
    out = []
    for f in d.glob("*.txt"):
        m = re.match(r"(\d+)_(.+)\.txt$", f.name)
        if not m:
            continue
        out.append({"номер": int(m.group(1)), "заголовок": m.group(2), "путь": f})
    out.sort(key=lambda x: x["номер"])
    return out


@register(["обновить_дзен", "дзен_обновить"])
def update_dzen(args):
    """Скачать новые статьи с канала Дзена (инкрементально).

    Качает только те статьи, которых ещё нет в _index.json.
    По умолчанию — канал macreative.

    Параметры:
      канал   — URL или slug канала (по умолчанию macreative)
      таймаут — секунд (по умолчанию 600)

    Пример:
      {"обновить_дзен": {}}
      {"обновить_дзен": {"канал": "macreative", "таймаут": 1200}}
    """
    if not SCRAPER.exists():
        return f"❌ Скрипт не найден: {SCRAPER}"
    args = args or {}
    channel = args.get("канал") or args.get("channel") or DEFAULT_CHANNEL
    try:
        timeout = int(args.get("таймаут", 600))
    except (TypeError, ValueError):
        timeout = 600

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    ch_arg = channel if str(channel).startswith("http") else f"https://dzen.ru/{channel}"

    try:
        proc = subprocess.run(
            [sys.executable, str(SCRAPER), ch_arg],
            cwd=str(PY_BROWSER),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"⏱ Таймаут ({timeout} сек) — скрапер не успел закончить."
    except Exception as e:
        return f"❌ Ошибка запуска скрапера: {e}"

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    lines = [l.strip() for l in out.split("\n") if l.strip()]
    tail = "\n".join(lines[-15:])
    if proc.returncode == 0:
        return f"✅ Дзен обновлён (канал {_slug(channel)}).\n{tail}"
    return f"⚠️ Скрапер завершился с кодом {proc.returncode}.\n{tail}"


@register(["статьи_дзена", "дзен_статьи", "список_статей_дзена"])
def list_dzen(args):
    """Список скачанных статей канала Дзена.

    Параметры:
      канал      — URL или slug (по умолчанию macreative)
      количество — сколько показать с конца (0 = все)

    Пример:
      {"статьи_дзена": {}}
      {"статьи_дзена": {"количество": 20}}
    """
    args = args or {}
    channel = args.get("канал") or args.get("channel") or DEFAULT_CHANNEL
    try:
        limit = int(args.get("количество", 0) or 0)
    except (TypeError, ValueError):
        limit = 0

    items = _iter_articles(channel)
    if not items:
        return f"📭 Нет скачанных статей для канала {_slug(channel)}."

    total = len(items)
    show = items[-limit:] if limit > 0 else items

    out = [f"📚 Статьи канала {_slug(channel)} — всего {total}:"]
    for it in show:
        out.append(f"  {it['номер']:03d}. {it['заголовок']}")
    if limit > 0 and total > limit:
        out.append(f"\n(показаны последние {limit} из {total})")
    return "\n".join(out)


@register(["прочитать_статью_дзен", "читать_статью_дзен", "дзен_читать"])
def read_dzen_article(args):
    """Прочитать скачанную статью Дзена постранично.

    Если ни 'заголовок', ни 'номер' не заданы — читается последняя статья.

    Параметры:
      заголовок — подстрока в названии (регистр не важен)
      номер     — точный номер статьи (001, 42, ...)
      канал     — URL или slug (по умолчанию macreative)
      страница  — номер страницы вывода (по умолчанию 1)
      размер    — символов на страницу (по умолчанию 30000)
      лимит     — алиас для 'размер'

    Каталог статей: {"статьи_дзена": {}}

    Примеры:
      {"прочитать_статью_дзен": {}}                      # последняя, стр. 1
      {"прочитать_статью_дзен": {"размер": 500}}         # последняя, по 500 симв.
      {"прочитать_статью_дзен": {"заголовок": "нейросети", "страница": 3}}
      {"прочитать_статью_дзен": {"номер": 5}}
    """
    args = args or {}
    channel = args.get("канал") or args.get("channel") or DEFAULT_CHANNEL
    try:
        size = int(args.get("размер") or args.get("лимит") or 30000)
    except (TypeError, ValueError):
        size = 30000
    if size <= 0:
        size = 30000
    try:
        page = int(args.get("страница", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    items = _iter_articles(channel)
    if not items:
        return f"📭 Нет скачанных статей для канала {_slug(channel)}."

    target = None
    if args.get("номер") is not None or "номер" in args:
        try:
            num = int(args["номер"])
        except (TypeError, ValueError):
            return "Ошибка: 'номер' должен быть числом"
        target = next((it for it in items if it["номер"] == num), None)
        if not target:
            nums = [it["номер"] for it in items]
            head = nums[:20]
            tail = "..." if len(nums) > 20 else ""
            return f"Статья с номером {num} не найдена. Доступны: {head}{tail}"
    elif args.get("заголовок"):
        q = str(args["заголовок"]).lower()
        matches = [it for it in items if q in it["заголовок"].lower()]
        if not matches:
            return f"Ничего не найдено по '{args['заголовок']}'."
        if len(matches) > 1:
            lines = [f"Найдено несколько ({len(matches)}), уточните заголовок:"]
            for m in matches[:15]:
                lines.append(f"  {m['номер']:03d}. {m['заголовок']}")
            return "\n".join(lines)
        target = matches[0]
    else:
        # ничего не указано — читаем последнюю статью
        target = items[-1]

    try:
        text = target["путь"].read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"❌ Ошибка чтения {target['путь'].name}: {e}"

    total = len(text)
    total_pages = max(1, (total + size - 1) // size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * size
    end = min(start + size, total)
    chunk = text[start:end]

    head = (f"📄 {target['заголовок']} (№{target['номер']:03d})\n"
            f"Страница {page}/{total_pages} "
            f"(символов {start+1}-{end} из {total})\n\n")

    parts = [head, chunk]

    # навигация
    nav = []
    if page < total_pages:
        nav.append(f'➡️ Дальше: добавь "страница": {page+1}')
    if page > 1:
        nav.append(f'⬅️ Назад: добавь "страница": {page-1}')
    nav.append('📚 Каталог статей: {"статьи_дзена": {}}')

    if nav:
        parts.append("\n\n" + "\n".join(nav))
    return "".join(parts)
