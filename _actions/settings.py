"""Просмотр и изменение локальных путей (side/config.json) и API-ключей."""
import json
from _actions import register


def _load_example_keys(cfg):
    """Собрать эталонные имена paths и api_keys из *.example.json."""
    ex_paths, ex_keys = [], []
    cfg_ex = cfg.CONFIG_PATH.parent / "config.example.json"
    if cfg_ex.exists():
        try:
            d = json.loads(cfg_ex.read_text(encoding="utf-8"))
            ex_paths = [k for k, v in (d.get("paths") or {}).items() if not v]
        except Exception:
            pass
    keys_ex = cfg.API_KEYS_FILE.parent / "api_keys.example.json"
    if keys_ex.exists():
        try:
            d = json.loads(keys_ex.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                ex_keys = list(d.keys())
        except Exception:
            pass
    return ex_paths, ex_keys


@register(["настроить", "настройки", "конфиг"])
def configure_settings(args):
    """Посмотреть или изменить локальные пути (side/config.json) и API-ключи (side/api_keys.json).

    Параметры:
      путь     — имя параметра из paths (например: ffmpeg_exe, stabledif_dir, piper_exe)
      ключ     — имя API-ключа (например: ollama, tavily, moltbook, wolfram)
      значение — новое значение (если не указано при заданном путь/ключ — спросит в консоли)
      сбросить — true: очистить указанный путь или ключ
      мастер   — true: поочерёдно спросить в консоли ТОЛЬКО незаполненные пути и ключи

    Примеры:
      {"настроить": {}}
      {"настроить": {"мастер": true}}
      {"настроить": {"путь": "ffmpeg_exe", "значение": "D:/ffmpeg/bin/ffmpeg.exe"}}
      {"настроить": {"ключ": "ollama"}}
    """
    import _config as cfg

    if not isinstance(args, dict):
        args = {}

    path_key = (args.get("путь") or args.get("path") or "").strip()
    api_key = (args.get("ключ") or args.get("key") or "").strip()
    has_val = ("значение" in args) or ("value" in args)
    val = str(args.get("значение") if "значение" in args else args.get("value", "")).strip().strip('"').strip("'")
    reset = bool(args.get("сбросить") or args.get("reset"))
    wizard = bool(args.get("мастер") or args.get("wizard") or args.get("пропуски") or args.get("missing"))

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
    ex_paths, ex_keys = _load_example_keys(cfg)
    missing_paths = [k for k in ex_paths if not paths.get(k) or not cfg.resolve_path(paths[k]).exists()]
    missing_keys = [k for k in ex_keys if not cfg.API_KEYS.get(k)]

    if wizard:
        if not missing_paths and not missing_keys:
            return "✅ Все пути и API-ключи из шаблонов уже настроены!"
        updated = []
        for k in missing_paths:
            p = cfg.ensure_path(k, f"📂 [Мастер] Путь '{k}' не задан. Введи путь (Enter — пропустить): ", force_ask=True)
            if p:
                updated.append(f"путь {k}")
        for k in missing_keys:
            val_k = cfg.ensure_api_key(k, f"🔑 [Мастер] API-ключ '{k}' не задан. Введи ключ (Enter — пропустить): ")
            if val_k:
                updated.append(f"ключ {k}")
        if updated:
            return "✅ Мастер настройки завершён. Сохранено: " + ", ".join(updated)
        return "ℹ️ Мастер настройки завершён (все пропуски оставлены без изменений)."

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
    if missing_paths:
        lines.append("  💡 Не заполнены из шаблона: " + ", ".join(missing_paths))

    lines.append("\n🔑 API-ключи (side/api_keys.json):")
    if not cfg.API_KEYS:
        lines.append("  (пока не заданы)")
    for k, v in sorted(cfg.API_KEYS.items()):
        masked = (v[:4] + "***" + v[-2:]) if len(v) > 8 else ("***" if v else "(пусто)")
        lines.append(f"  • {k}: {masked}")
    if missing_keys:
        lines.append("  💡 Не заполнены из шаблона: " + ", ".join(missing_keys))
    if missing_paths or missing_keys:
        lines.append('\n✨ Чтобы поочерёдно заполнить только недостающие параметры: {"настроить": {"мастер": true}}')
    return "\n".join(lines)
