"""Диагностика реестра команд: поиск по имени / докстрингу / параметрам."""
import re as _re
from _actions import register, HANDLERS, PRIMARY_HANDLERS


def _params_of(fn):
    """Список параметров функции через общий экстрактор из cmd_parser.

    Использует cmd_parser._extract_params_ast — там фильтр BAD
    (key/token/secret/password/буфер), чтобы служебные ключи
    не всплывали в подсказках.
    """
    try:
        from _actions.cmd_parser import _extract_params_ast
        return _extract_params_ast(fn)
    except Exception:
        return []


# Соответствия латиница ↔ кириллица для типовых ключей
_RU_EN = {
    "text": "текст", "текст": "text",
    "file": "файл", "файл": "file",
    "path": "путь", "путь": "path",
    "url": "адрес", "адрес": "url",
    "query": "запрос", "запрос": "query",
    "command": "команда", "команда": "command",
    "name": "имя", "имя": "name",
    "id": "ид",
    "number": "номер", "номер": "number",
    "key": "ключ", "ключ": "key",
    "data": "данные", "данные": "data",
}


# --- транслитерация (простые правила) ---
_LAT2RU = [
    ("shch", "щ"), ("sch", "щ"),
    ("yo", "ё"), ("jo", "ё"),
    ("yu", "ю"), ("ju", "ю"),
    ("ya", "я"), ("ja", "я"),
    ("zh", "ж"), ("kh", "х"), ("ch", "ч"), ("sh", "ш"),
    ("ts", "ц"), ("th", "т"), ("ph", "ф"), ("ck", "к"),
    ("ee", "и"), ("oo", "у"), ("ou", "у"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"),
    ("e", "е"), ("z", "з"), ("i", "и"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"),
    ("c", "к"), ("y", "ы"), ("j", "й"), ("x", "кс"), ("w", "в"), ("q", "к"),
]

_RU2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit_lat2ru(s):
    s = s.lower()
    out = []
    i = 0
    while i < len(s):
        for pat, ru in _LAT2RU:
            if s.startswith(pat, i):
                out.append(ru)
                i += len(pat)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _translit_ru2lat(s):
    s = s.lower()
    return "".join(_RU2LAT.get(c, c) for c in s)


def _is_lat(c):
    return "a" <= c <= "z"


def _is_cyr(c):
    return "а" <= c <= "я" or c == "ё"


def _alts(q):
    """Варианты запроса: сам + словарный + транслит."""
    q = q.strip().lower()
    out = [q]
    if q in _RU_EN:
        out.append(_RU_EN[q])
    has_lat = any(_is_lat(c) for c in q)
    has_cyr = any(_is_cyr(c) for c in q)
    if has_lat and not has_cyr:
        out.append(_translit_lat2ru(q))
    elif has_cyr and not has_lat:
        out.append(_translit_ru2lat(q))
    return list(dict.fromkeys(out))


def _smart_match(alts, target):
    """Совпадение в трёх представлениях target: как есть, латиница, кириллица."""
    t_low = (target or "").lower()
    t_lat = _translit_ru2lat(t_low)
    t_ru = _translit_lat2ru(t_low)
    for a in alts:
        if not a:
            continue
        if a in t_low or a in t_lat or a in t_ru:
            return True
    return False


def _score_of(alts, primary, aliases, params, doc, doc_first):
    """Релевантность: точное имя > алиас > подстрока > параметр > док."""
    score = 0
    for a in alts:
        if not a:
            continue
        if a == primary:
            score = max(score, 100)
        elif a in aliases:
            score = max(score, 80)
        elif a in primary:
            score = max(score, 60)
        elif any(a in al for al in aliases):
            score = max(score, 15)
        if a in params:
            score = max(score, 30)
        elif any(a in p for p in params):
            score = max(score, 20)
        if a in doc_first:
            score = max(score, 10)
        elif a in doc:
            score = max(score, 5)
    return score


def _scan(alts, where, params_of):
    """Один проход по HANDLERS. Возвращает (score, primary, aliases, doc, params)."""
    hits = []
    seen = set()
    for name, fn in sorted(HANDLERS.items()):
        if not name or id(fn) in seen:
            continue
        seen.add(id(fn))

        aliases = sorted(n for n, f in HANDLERS.items() if f is fn and n)
        if not aliases:
            continue
        # приоритет — имя из PRIMARY_HANDLERS (первое в @register)
        primary = None
        for _pn, _pf in PRIMARY_HANDLERS.items():
            if _pf is fn:
                primary = _pn
                break
        if primary is None:
            _ru = [a for a in aliases if not _re.search(r"[a-zA-Z]", a)]
            primary = _ru[0] if _ru else aliases[0]

        doc = (fn.__doc__ or "")
        params = params_of(fn)
        doc_first = doc.strip().split("\n", 1)[0]

        hit_name = any(_smart_match(alts, n) for n in aliases)
        hit_param = any(_smart_match(alts, p) for p in params)
        hit_doc_full = _smart_match(alts, doc)

        if where in ("всё", "все", "all", ""):
            take = hit_name or hit_param or hit_doc_full
        elif where == "имя":
            take = hit_name
        elif where == "параметр":
            take = hit_param
        elif where in ("док", "докстринг"):
            take = hit_doc_full
        else:
            take = hit_name or hit_param or hit_doc_full

        if not take:
            continue

        sc = _score_of(alts, primary, aliases, params, doc, doc_first)
        hits.append((sc, primary, aliases, doc, params))
    return hits


@register(["диагностика", "поиск"])
def diagnostics(args):
    """Найти команды по подстроке в имени / докстринге / параметрах.

    Параметры:
      сервис / подстрока — что искать (обязательный)
      где     — 'имя' | 'док' | 'параметр' | 'всё' (по умолчанию 'всё':
                имя + параметры + весь докстринг)
      алиасы  — true/false, показывать ли альтернативные имена
      лимит   — сколько показать (по умолчанию 10; 0 или 'все' — все)

    Ищет с учётом латиница↔кириллица: 'text' найдёт и 'текст'.
    Сортирует по релевантности (точное имя → алиас → подстрока →
    параметр → докстринг).

    Примеры:
      {"диагностика": {"сервис": "text"}}
      {"диагностика": {"сервис": "text", "лимит": 5}}
      {"диагностика": {"сервис": "text", "лимит": 0}}
      {"диагностика": {"сервис": "молтбук", "где": "имя"}}
    """
    if not isinstance(args, dict):
        return 'Укажи сервис: {"диагностика": {"сервис": "text"}}'
    q = (args.get("сервис") or args.get("подстрока") or args.get("запрос") or "").strip()
    if not q:
        return ('Укажи сервис: {"диагностика": {"сервис": "text"}}\n'
                'Например: {"диагностика": {"сервис": "молтбук"}}')
    where = (args.get("где") or "всё").strip().lower()
    alts = _alts(q)

    # --- шаблоны латинских имён (download/curl/wget) ---
    try:
        from _executor import _LATIN_TEMPLATES
        _tpl_hits = []
        for _lat, _tpl in _LATIN_TEMPLATES.items():
            if _smart_match(alts, _lat):
                _tpl_hits.append((_lat, _tpl))
    except Exception:
        _tpl_hits = []

    params_cache = {}

    def params_of(fn):
        key = id(fn)
        if key not in params_cache:
            params_cache[key] = _params_of(fn)
        return params_cache[key]

    matches = _scan(alts, where, params_of)

    # --- fallback: если не нашли, пробуем без последней буквы (1..3 раз) ---
    _original_q = q
    _tries = 0
    while not matches and len(q) >= 4 and _tries < 3:
        q = q[:-1]
        _tries += 1
        alts = _alts(q)
        matches = _scan(alts, where, params_of)

    if not matches and _tpl_hits:
        out_tpl = []
        for _lat, _tpl in _tpl_hits:
            out_tpl.append(f"• шаблон (латиница): {_lat}")
            out_tpl.append("")
            for _ln in _tpl.split("\n"):
                out_tpl.append("  " + _ln)
            out_tpl.append("")
        return "\n".join(out_tpl).rstrip()

    if not matches:
        from _color import color_red
        return color_red(f"По '{_original_q}' ничего не найдено.")

    # --- сортировка по релевантности + лимит ---
    matches.sort(key=lambda x: (-x[0], x[1]))

    lim = args.get("лимит", 10)
    if isinstance(lim, str):
        _ls = lim.strip().lower()
        if _ls in ("все", "all", "0", ""):
            lim = 0
        else:
            try:
                lim = int(_ls)
            except ValueError:
                lim = 10
    lim = max(0, int(lim))

    total = len(matches)
    if lim > 0:
        matches = matches[:lim]

    show_aliases = args.get("алиасы", True)
    if isinstance(show_aliases, str):
        show_aliases = show_aliases.strip().lower() not in ("0", "false", "нет", "off", "")
    else:
        show_aliases = bool(show_aliases)

    _suffix = ""
    if q != _original_q:
        _suffix = f" [уточнено: '{_original_q}' → '{q}']"
    header = f"🔎 Найдено команд: {total} (по '{q}', где={where}){_suffix}"
    if lim > 0 and total > lim:
        header += f" — топ-{lim}"
    lines = [header, ""]

    for _sc, name, aliases, doc, params in matches:
        lines.append(f"• команда: {name}")
        if show_aliases:
            other = [a for a in aliases if a != name]
            if other:
                lines.append(f"  алиасы: {', '.join(other)}")
        doc_short = doc.strip().split("\n", 1)[0].strip()
        if doc_short:
            lines.append(f"  {doc_short}")
        if params:
            lines.append(f"  параметры: {', '.join(params)}")
        lines.append("")

    if lim > 0 and total > lim:
        lines.append(
            f"… и ещё {total - lim}. Показать все: "
            f'{{"диагностика": {{"сервис": "{q}", "лимит": 0}}}}'
        )

    return "\n".join(lines).rstrip()