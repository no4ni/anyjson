"""Просмотр и изменение локальных путей (side/config.json) и API-ключей."""
from _actions import register


@register(["настроить", "настройки", "конфиг"])
def configure_settings(args):
    """Посмотреть или изменить локальные пути (side/config.json) и API-ключи (side/api_keys.json).

    Параметры:
      путь     — имя параметра из paths (например: ffmpeg_exe, stabledif_dir, piper_exe)
      ключ     — имя API-ключа (например: moltbook, tavily, wolfram)
      значение — новое значение (если не указано при заданном путь/ключ — спросит в консоли)
      сбросить — true: очистить указанный путь или ключ

    Примеры:
      {"настроить": {}}
      {"настроить": {"путь": "ffmpeg_exe", "значение": "D:/ffmpeg/bin/ffmpeg.exe"}}
      {"настроить": {"путь": "ffmpeg_exe"}}
      {"настроить": {"ключ": "moltbook"}}
    """
    import _config as cfg

    if not isinstance(args, dict):
        args = {}

    path_key = (args.get("путь") or args.get("path") or "").strip()
    api_key = (args.get("ключ") or args.get("key") or "").strip()
    has_val = ("значение" in args) or ("value" in args)
    val = str(args.get("значение") if "значение" in args else args.get("value", "")).strip().strip('"').strip("'")
    reset = bool(args.get("сбросить") or args.get("reset"))

    if path_key:
        if reset:
            cfg.save_config_path(path_key, "")
            return f"✅ Путь '{path_key}' очищен в {cfg.CONFIG_PATH}"
        if has_val and val:
            p = cfg.resolve_path(val)
            if not p.exists():
                return f"❌ Путь не существует: {p}\n💡 Чтобы ввести заново в консоли: {{\"настроить\": {{\"путь\": \"{path_key}\"}}}}"
            cfg.save_config_path(path_key, val)
            return f"✅ Путь '{path_key}' обновлён: {p}"
        p = cfg.ensure_path(path_key, f"📂 Введи новый путь для '{path_key}' (Enter — отмена): ", force_ask=True)
        return f"✅ Путь '{path_key}': {p}" if p else "ℹ️ Изменение отменено."

    if api_key:
        if reset:
            cfg.API_KEYS.pop(api_key, None)
            cfg._save_api_key(api_key, "")
            return f"✅ Ключ '{api_key}' очищен в {cfg.API_KEYS_FILE}"
        if has_val and val:
            cfg.API_KEYS[api_key] = val
            cfg._save_api_key(api_key, val)
            return f"✅ Ключ '{api_key}' сохранён в {cfg.API_KEYS_FILE}"
        cfg.API_KEYS.pop(api_key, None)
        k = cfg.ensure_api_key(api_key, f"🔑 Введи новое значение ключа '{api_key}' (Enter — отмена): ")
        return f"✅ Ключ '{api_key}' обновлён." if k else "ℹ️ Изменение отменено."

    paths = cfg.CONFIG.get("paths") or {}
    lines = ["📂 Локальные пути (side/config.json):"]
    if not paths:
        lines.append("  (пока не заданы — спросятся автоматически при первом вызове нужной команды)")
    for k, v in sorted(paths.items()):
        if not v:
            lines.append(f"  • {k}: (не задан)")
        else:
            p = cfg.resolve_path(v)
            mark = "✅" if p.exists() else "⚠️ не найден"
            lines.append(f"  • {k}: {v} [{mark}]")

    lines.append("\n🔑 API-ключи (side/api_keys.json):")
    if not cfg.API_KEYS:
        lines.append("  (пока не заданы)")
    for k, v in sorted(cfg.API_KEYS.items()):
        masked = (v[:4] + "***" + v[-2:]) if len(v) > 8 else ("***" if v else "(пусто)")
        lines.append(f"  • {k}: {masked}")
    return "\n".join(lines)