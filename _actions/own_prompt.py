"""Промпт-активатор текущего агента."""
import os

from _actions import register
from _config import PROFILES_DIR, AGENT_NAME


@register(["мой_промпт", "мой_prompt", "my_prompt", "промпт_агента"])
def get_own_prompt(args):
    """Показать промпт-активатор текущего агента (из profiles/<Имя>/prompt.txt).

    Параметры:
      страница — номер страницы (по умолчанию 1)
      размер   — символов на страницу (по умолчанию 5000)
      имя      — чужой профиль (по умолчанию свой)

    Примеры:
      {"мой_промпт": {}}
      {"мой_промпт": {"размер": 3000, "страница": 2}}
      {"мой_промпт": {"имя": "Лира"}}
    """
    args = args or {}

    target = str(args.get("имя") or AGENT_NAME or "").strip()
    if not target:
        return "Не удалось определить имя агента (AGENT_NAME пуст)."

    profile = PROFILES_DIR / target
    if not profile.is_dir():
        return f"Профиль не найден: {profile}"

    prompt_file = profile / "prompt.txt"
    if not prompt_file.exists():
        # ищем альтернативы
        alts = []
        for f in profile.iterdir():
            if f.is_file() and "prompt" in f.name.lower() and f.suffix.lower() in (".txt", ".md"):
                alts.append(f.name)
        if alts:
            return (f"В профиле {target} нет prompt.txt. Есть похожие: {', '.join(alts)}")
        return f"В профиле {target} нет prompt.txt."

    try:
        text = prompt_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения {prompt_file}: {e}"

    total = len(text)
    try:
        size = int(args.get("размер") or 5000)
    except (TypeError, ValueError):
        size = 5000
    if size <= 0:
        size = 5000
    try:
        page = int(args.get("страница") or 1)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    total_pages = max(1, (total + size - 1) // size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * size
    end = min(start + size, total)
    chunk = text[start:end]

    head = (f"📄 prompt.txt профиля {target}\n"
            f"Страница {page}/{total_pages} "
            f"(символов {start+1}-{end} из {total})\n"
            f"Путь: {prompt_file.as_posix()}\n\n")

    nav = []
    if page < total_pages:
        nav.append(f'➡️ Дальше: добавь "страница": {page+1}')
    if page > 1:
        nav.append(f'⬅️ Назад: добавь "страница": {page-1}')
    nav.append(f'📄 Весь файл: {{"прочитать_полностью": {{"путь": "{prompt_file.as_posix()}"}}}}')

    return head + chunk + "\n\n" + "\n".join(nav)
