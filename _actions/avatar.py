"""Аватарки профилей агентов."""
import json
import shutil
from pathlib import Path

from _actions import register
from _config import PROFILES_DIR, AGENT_NAME


_AVATAR_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _find_avatar(agent_name):
    """Найти файл аватарки в профиле агента. Возвращает Path или None."""
    if not agent_name:
        return None
    profile = PROFILES_DIR / agent_name
    if not profile.is_dir():
        return None
    info_path = profile / "info.json"
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            av = info.get("avatar") if isinstance(info, dict) else None
            if av:
                p = profile / av
                if p.is_file():
                    return p
        except Exception:
            pass
    for ext in _AVATAR_EXTS:
        p = profile / ("avatar" + ext)
        if p.is_file():
            return p
    return None


@register(["аватар", "показать_аватар"])
def get_avatar(args):
    """Показать аватар агента (свой или другого).

    Кладёт файл аватарки в контекст.

    Параметры:
      агент — имя агента; без него — свой аватар

    Примеры:
      {"аватар": {}}
      {"аватар": {"агент": "Her"}}
    """
    from _actions.files import read_full
    a = args or {}
    if isinstance(a, str):
        a = {"агент": a}
    target = (a.get("агент") or a.get("кому") or a.get("имя") or AGENT_NAME)
    target = str(target).strip()
    if not target:
        return "Не указан агент."
    p = _find_avatar(target)
    if not p:
        return "У агента " + target + " нет аватарки."
    return read_full({"путь": str(p)})


@register(["сменить_аватар", "установить_аватар"])
def set_avatar(args):
    """Установить свой аватар (перезаписать существующий).

    Файл копируется в профиль текущего агента под именем avatar.<ext>.
    В info.json проставляется поле avatar.

    Параметры:
      путь — путь к изображению (обязательный)

    Примеры:
      {"сменить_аватар": {"путь": "C:/pic.jpg"}}
    """
    a = args or {}
    if isinstance(a, str):
        a = {"путь": a}
    src = (a.get("путь") or a.get("файл") or "").strip()
    if not src:
        return "Укажи 'путь'."
    sp = Path(src)
    if not sp.is_file():
        return "❌ Файл не найден: " + src
    ext = sp.suffix.lower()
    if ext not in _AVATAR_EXTS:
        return ("❌ Формат " + ext + " не поддерживается. Допустимо: "
                + ", ".join(_AVATAR_EXTS))

    profile = PROFILES_DIR / AGENT_NAME
    profile.mkdir(parents=True, exist_ok=True)

    for e in _AVATAR_EXTS:
        old = profile / ("avatar" + e)
        if old.exists():
            try:
                old.unlink()
            except Exception:
                pass

    dst = profile / ("avatar" + ext)
    try:
        shutil.copy2(sp, dst)
    except Exception as e:
        return "❌ Не скопировать: " + str(e)

    info_path = profile / "info.json"
    info = {}
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            info = {}
    if not isinstance(info, dict):
        info = {}
    info["avatar"] = "avatar" + ext
    try:
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except Exception as e:
        return "⚠️ Файл скопирован, но info.json не обновлён: " + str(e)

    return "✅ Аватар обновлён: " + str(dst)