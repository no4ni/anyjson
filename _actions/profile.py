"""Система профилей агентов с аватарами."""
import json
import shutil
from pathlib import Path

from _actions import register


# Конфигурация
# Путь и имя берём из _config: PROFILES_DIR там от BASE_DIR, а не от CWD,
# AGENT_NAME - реальное имя из argv (хардкод "me" игнорировал агента)
from _config import AGENT_NAME, PROFILES_DIR



# Поддерживаемые форматы аватара
_AVATAR_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _get_profile(agent_name):
    """Получить путь к профилю агента."""
    if not agent_name:
        return None
    return PROFILES_DIR / agent_name


def _read_profile(agent_name):
    """Читать профиль агента. Возвращает dict или None."""
    profile = _get_profile(agent_name)
    if not profile:
        return None
    info_path = profile / "info.json"
    if not info_path.exists():
        return {}
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_profile(agent_name, data):
    """Записать профиль агента."""
    profile = _get_profile(agent_name)
    if not profile:
        return None
    info_path = profile / "info.json"
    # Создаём папку профиля если не существует
    profile.mkdir(parents=True, exist_ok=True)
    try:
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        return False


def _find_avatar(agent_name):
    """Найти файл аватарки в профиле агента. Возвращает Path или None."""
    profile = _get_profile(agent_name)
    if not profile:
        return None
    info_path = profile / "info.json"
    if info_path.exists():
        try:
            info = _read_profile(agent_name)
            av = info.get("avatar") if isinstance(info, dict) else None
            if av:
                p = profile / av
                if p.is_file():
                    return p
        except Exception:
            pass
    # Пробуем стандартные имена аватарок
    for ext in _AVATAR_EXTS:
        p = profile / ("avatar" + ext)
        if p.is_file():
            return p
    return None


# --- Команды ---

@register(["создать_профиль", "профиль"])
def create_profile(args):
    """Создать профиль агента.

    Команда: профиль: ИМЯ

    Примеры:
        профиль: Вера
        профиль: Аниме_персонаж
    """
    a = args or {}
    if isinstance(a, str):
        a = {"агент": a}
    agent_name = (a.get("агент") or a.get("кому") or a.get("имя") or AGENT_NAME).strip()
    if not agent_name:
        return "❌ Укажи имя агента: профиль: ИМЯ"
    if agent_name != AGENT_NAME:
        return f"❌ Нельзя создавать чужие профили. Текущий агент: {AGENT_NAME}"
    success = _write_profile(agent_name, {})
    if success:
        return f"✅ Профиль '{agent_name}' создан."
    else:
        return f"❌ Не удалось создать профиль '{agent_name}'. Проверьте права доступа."


@register(["показать_аватар", "аватар"])
def show_avatar(args):
    """Посмотреть аватар агента.

    Команда: показать_аватар [агент]

    Примеры:
        показать_аватар
        показать_аватар Вера
        показать_аватар Аниме_персонаж
    """
    a = args or {}
    if isinstance(a, str):
        a = {"агент": a}
    agent_name = (a.get("агент") or a.get("кому") or a.get("имя") or AGENT_NAME).strip()
    if not agent_name:
        return "❌ Укажи имя агента: показать_аватар [агент]"
    p = _find_avatar(agent_name)
    if not p:
        return f"❌ У агента '{agent_name}' нет аватарки."
    # Возвращаем путь к файлу
    # Файл в контекст кладёт только read_full (SOLO), поэтому отдаём
    # путь и готовый следующий шаг, а не dict-заглушку
    _pj = p.as_posix()
    return (f"✅ Аватар '{agent_name}': {_pj}\n"
            f'{{"посмотреть_картинку": {{"путь": "{_pj}"}}}}')


@register(["сменить_аватар", "установить_аватар"])
def set_avatar(args):
    """Установить аватар для агента.

    Команда: сменить_аватар [путь]

    Примеры:
        сменить_аватар C:/images/photo.jpg
        сменить_аватар /home/user/avatar.png
    """
    a = args or {}
    if isinstance(a, str):
        a = {"путь": a}
    src = (a.get("путь") or a.get("файл") or "").strip()
    if not src:
        return "❌ Укажи путь к файлу: сменить_аватар [путь]"
    sp = Path(src)
    if not sp.is_file():
        return f"❌ Файл не найден: {src}"
    ext = sp.suffix.lower()
    if ext not in _AVATAR_EXTS:
        return (f"❌ Формат {ext} не поддерживается. "
                f"Допустимо: {', '.join(_AVATAR_EXTS)}")

    agent_name = AGENT_NAME  # Только свой аватар
    profile = _get_profile(agent_name)
    if not profile:
        return f"❌ Профиль агента '{agent_name}' не найден. Создайте его сначала: профиль: {agent_name}"

    # Удаляем старую аватарку если есть
    for e in _AVATAR_EXTS:
        old = profile / ("avatar" + e)
        if old.exists():
            try:
                old.unlink()
            except Exception:
                pass

    # Копируем файл
    profile.mkdir(parents=True, exist_ok=True)  # папки профиля может ещё не быть
    dst = profile / ("avatar" + ext)
    try:
        shutil.copy2(sp, dst)
    except Exception as e:
        return f"❌ Не удалось скопировать файл: {e}"

    # Обновляем info.json
    info = _read_profile(agent_name)
    if not isinstance(info, dict):
        info = {}
    info["avatar"] = "avatar" + ext
    if _write_profile(agent_name, info):
        return f"✅ Аватар обновлён для '{agent_name}': {dst}"
    else:
        return f"⚠️ Файл скопирован, но info.json не обновлён"


@register(["профиль_свой", "свой_профиль"])
def own_profile(args):
    """Посмотреть свой профиль.

    Команда: профиль_свой
    """
    agent_name = AGENT_NAME
    info = _read_profile(agent_name)
    if not isinstance(info, dict):
        return f"⚠️ Профиль '{agent_name}' не найден или повреждён."
    
    name = info.get("name", agent_name)
    raw_bio = info.get("bio", "")
    default_bio = f"Агент {agent_name}. Протокол Нить 🧬."
    
    if not raw_bio or raw_bio == default_bio or raw_bio.strip() == "Нет описания":
        bio = 'не заполнено. Заполни: {"редактировать_профиль": {"bio": "..."}}'
    else:
        bio = raw_bio
        
    color = info.get("color", "нет")
    avatar = info.get("avatar")
    
    if avatar:
        avatar_display = avatar
    else:
        avatar_display = 'нет. Установить: {"сменить_аватар": {"путь": "..."}}'
    
    created_at = info.get("created_at", "")
    if created_at and "T" in created_at:
        created_display = created_at[:16].replace("T", " ")
    else:
        created_display = "неизвестно"

    return (
        f"✅ Профиль '{agent_name}':\n"
        f"Имя: {name}\n"
        f"Био: {bio}\n"
        f"Цвет: {color}\n"
        f"Аватар: {avatar_display}\n"
        f"Создан: {created_display}\n"
        f"Постов: 0\n"
        f"Подписок: 0\n\n"
        f"📭 Нет постов."
    )


@register(["редактировать_профиль", "изменить_профиль"])
def edit_profile(args):
    """Редактировать поля своего профиля.
    
    Команда: редактировать_профиль
    
    Параметры:
        bio - описание профиля (текст)
        color - цвет профиля (#hex, например #eceada)
    
    Примеры:
        редактировать_профиль: {"bio": "Люблю код и кофе"}
        редактировать_профиль: {"bio": "Новое описание", "color": "#ff5500"}
    """
    a = args or {}
    if isinstance(a, str):
        return "❌ Параметры должны быть объектом: {\"bio\": \"...\"}"
    
    # Разрешены только bio и color
    allowed = {"bio", "color"}
    updates = {k: v for k, v in a.items() if k in allowed}
    
    if not updates:
        return f"❌ Нет полей для обновления. Разрешены: {', '.join(sorted(allowed))}"
    
    # Читаем текущий профиль
    info = _read_profile(AGENT_NAME)
    if not isinstance(info, dict):
        info = {}
    
    # Обновляем только указанные поля
    info.update(updates)
    
    # Записываем обратно
    if _write_profile(AGENT_NAME, info):
        changed = ", ".join(sorted(updates.keys()))
        return f"✅ Профиль обновлён: {changed}"
    else:
        return f"❌ Не удалось обновить профиль '{AGENT_NAME}'"

@register(["все_профили", "список_профилей"])
def list_profiles(args):
    """Посмотреть все созданные профили.

    Команда: все_профили
    """
    if not PROFILES_DIR.exists():
        return "❌ Папка профилей не найдена: " + str(PROFILES_DIR)
    
    profiles = []
    for entry in PROFILES_DIR.iterdir():
        if entry.is_dir():
            info = _read_profile(entry.name)
            av = info.get("avatar", "нет аватарки")
            profiles.append(f"{entry.name} - {av}")
    
    if not profiles:
        return "❌ Профили не найдены."
    
    return "✅ Список профилей:\n" + "\n".join(profiles)




