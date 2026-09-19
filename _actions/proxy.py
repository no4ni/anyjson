"""Управление локальным хранилищем прокси."""
from _actions import register
from _config import PROXY_DIR


@register("получить_прокси")
def get_proxy(args):
    """Команда "получить_прокси".

    Параметры: см. реализацию.
    Пример: {"получить_прокси": {}}
    """
    if not PROXY_DIR or not PROXY_DIR.exists():
        return "❌ Папка с прокси не найдена. Проверьте proxy_dir в config.json."
    count = args.get("количество", 5)
    check = args.get("проверить", False)
    proxy_type = args.get("тип", None)
    try:
        count = int(count)
    except (ValueError, TypeError):
        return "Параметр 'количество' должен быть целым числом."
    from _actions.tools_web import get_local_proxies
    proxies = get_local_proxies(str(PROXY_DIR), count=count, check=check, proxy_type=proxy_type)
    if not proxies:
        return "Прокси не найдены в локальном хранилище."
    lines = [f"{i+1}. {p}" for i, p in enumerate(proxies)]
    return "Доступные прокси:\n" + "\n".join(lines)


@register("удалить_прокси")
def del_proxy(args):
    """Команда "удалить_прокси".

    Параметры: см. реализацию.
    Пример: {"удалить_прокси": {}}
    """
    if not PROXY_DIR or not PROXY_DIR.exists():
        return "❌ Папка с прокси не найдена."
    proxy_addr = args.get("прокси") or args.get("адрес") or args.get("значение") or ""
    if not proxy_addr:
        return "❌ Укажите прокси в поле 'прокси' (ip:port)"
    parts = proxy_addr.split(':')
    if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
        return "❌ Формат должен быть ip:port, например 1.2.3.4:8080"
    from _actions.tools_web import remove_local_proxy
    try:
        removed = remove_local_proxy(PROXY_DIR, proxy_addr)
        if removed > 0:
            return f"✅ Прокси {proxy_addr} удалён из {removed} файлов."
        return f"ℹ️ Прокси {proxy_addr} не найден в локальном хранилище."
    except Exception as e:
        return f"❌ Ошибка при удалении: {e}"
