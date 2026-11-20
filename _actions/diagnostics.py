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


# Стоп-слова для токенизации (игнорируются при разборе фраз)
_STOP = {
    "из", "в", "во", "с", "со", "по", "для", "на", "и", "или", "как",
    "что", "чтобы", "это", "тот", "the", "a", "an", "of", "from", "to",
    "in", "on", "with", "and", "or",
}

# Словарь синонимов: английское слово -> кортеж русских аналогов
_SYN = {
    "read": ("читать", "прочитать"),
    "open": ("открыть",),
    "write": ("записать", "писать"),
    "save": ("сохранить",),
    "find": ("найти", "поиск"),
    "search": ("найти", "поиск"),
    "list": ("список",),
    "delete": ("удалить",),
    "remove": ("удалить",),
    "copy": ("копировать",),
    "move": ("переместить",),
    "create": ("создать",),
    "run": ("запуск", "запустить"),
    "help": ("справка",),
    "image": ("картинка", "изображение"),
    "picture": ("картинка", "изображение"),
    "video": ("видео",),
    "audio": ("аудио", "звук"),
    "sound": ("звук",),
    "folder": ("папка", "папок", "папку", "папке", "директория", "директорий"),
    "directory": ("папка", "папок", "папку", "папке", "директория", "директорий"),
    "file": ("файл", "файлов", "файлы"),
    "text": ("текст", "текста"),
    "path": ("путь", "пути"),
    "url": ("адрес", "ссылка"),
    "query": ("запрос", "запроса"),
    "command": ("команда", "команды"),
    "name": ("имя", "имени"),
    "number": ("номер", "номера"),
    "key": ("ключ", "ключа"),
    "data": ("данные", "данных"),
}

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


_RU_TAILS = (
    "иями", "ями", "ами", "ией", "иям", "ием", "иях",
    "ого", "его", "ому", "ему", "ыми", "ими", "ать", "ять", "ить", "еть",
    "ов", "ев", "ей", "ой", "ий", "ый", "ая", "яя", "ое", "ее",
    "ые", "ие", "ом", "ем", "ам", "ям", "ах", "ях", "ую", "юю",
    "а", "я", "у", "ю", "ы", "и", "е", "о", "ь",
)


def _stem_token(w):
    """Мягкая нормализация слова: срез падежных окончаний для длинных русских слов."""
    w = w.lower().strip("_")
    if len(w) >= 5 and any(_is_cyr(c) for c in w):
        for tail in _RU_TAILS:
            if w.endswith(tail) and len(w) - len(tail) >= 4:
                return w[:-len(tail)]
    return w


def _norm_phrase(s):
    """Нормализация строки/имени команды: _ -> пробел, стемминг слов."""
    tokens = _re.findall(r"[a-zа-яё0-9]+", (s or "").lower().replace("_", " "))
    return " ".join(_stem_token(t) for t in tokens if t)


def _word_variants(w):
    """Все варианты одного токена (сам, стем, перевод, синонимы, транслит)."""
    w = w.lower()
    variants = {w, _stem_token(w)}
    if w in _RU_EN:
        variants.add(_RU_EN[w])
    if w in _SYN:
        for syn in _SYN[w]:
            variants.add(syn)
            variants.add(_stem_token(syn))
    for eng, rus_list in _SYN.items():
        if w in rus_list or any(_stem_token(r) == _stem_token(w) for r in rus_list):
            variants.add(eng)
            for syn in rus_list:
                variants.add(syn)
                variants.add(_stem_token(syn))
    has_lat = any(_is_lat(c) for c in w)
    has_cyr = any(_is_cyr(c) for c in w)
    if has_lat and not has_cyr:
        variants.add(_translit_lat2ru(w))
    elif has_cyr and not has_lat:
        variants.add(_translit_ru2lat(w))
    return {v for v in variants if len(v) >= 2}


def _alts(q):
    """Варианты запроса: целая фраза + нормализованная + перевод + транслит."""
    q = q.strip().lower()
    out = [q]
    norm = _norm_phrase(q)
    if norm:
        out.append(norm)
    if q in _RU_EN:
        out.append(_RU_EN[q])
    words = [w for w in _re.findall(r"[a-zа-яё0-9]+", q.replace("_", " ")) if w]
    if len(words) == 1:
        out.extend(sorted(_word_variants(words[0])))
    has_lat = any(_is_lat(c) for c in q)
    has_cyr = any(_is_cyr(c) for c in q)
    if has_lat and not has_cyr:
        out.append(_translit_lat2ru(q))
    elif has_cyr and not has_lat:
        out.append(_translit_ru2lat(q))
    return list(dict.fromkeys(out + [v.replace("_", " ") for v in out]))


def _smart_match(alts, target):
    """Совпадение в представлениях target: как есть, нормализованное, латиница, кириллица."""
    t_low = (target or "").lower()
    t_norm = t_low.replace("_", " ")
    t_stem = _norm_phrase(t_low)
    t_lat = _translit_ru2lat(t_low)
    t_lat_norm = t_lat.replace("_", " ")
    t_ru = _translit_lat2ru(t_low)
    t_ru_norm = t_ru.replace("_", " ")
    haystacks = (t_low, t_norm, t_stem, t_lat, t_lat_norm, t_ru, t_ru_norm)
    for a in alts:
        if not a:
            continue
        a_norm = a.replace("_", " ")
        a_stem = _norm_phrase(a)
        for needle in (a, a_norm, a_stem):
            if needle and any(needle in h for h in haystacks):
                return True
    return False


def _score_of(alts, primary, aliases, params, doc, doc_first):
    """Релевантность: без накрутки дублей синонимов + бонус за точное попадание фразы."""
    score = 0
    p_norm = primary.lower().replace("_", " ")
    p_stem = _norm_phrase(primary)
    al_norms = [al.lower().replace("_", " ") for al in aliases]
    al_stems = [_norm_phrase(al) for al in aliases]
    params_low = [p.lower() for p in params]
    doc_first_low = (doc_first or "").lower()
    doc_low = (doc or "").lower()

    for a in alts:
        if not a:
            continue
        a_norm = a.replace("_", " ")
        a_stem = _norm_phrase(a)
        if a_norm == p_norm or (a_stem and a_stem == p_stem):
            score = max(score, 160)
        elif a_norm in al_norms or (a_stem and a_stem in al_stems):
            score = max(score, 140)
        elif a_norm in p_norm or (a_stem and a_stem in p_stem):
            score = max(score, 110)

    base_tokens = []
    seen_tok = set()
    for a in alts:
        for t in _re.findall(r"[a-zа-яё0-9]+", (a or "").replace("_", " ")):
            if len(t) >= 2 and t not in seen_tok:
                seen_tok.add(t)
                base_tokens.append(t)

    p_words = set(p_norm.split())
    al_words = {w for al in al_norms for w in al.split()}

    for tok in base_tokens:
        best_w = 0
        for w in _word_variants(tok):
            w_sc = 0
            if w in p_words or w in p_stem.split():
                w_sc = 50
            elif w in al_words or any(w in s.split() for s in al_stems):
                w_sc = 40
            elif w in p_norm or w in p_stem:
                w_sc = 30
            elif any(w in al or w in s for al, s in zip(al_norms, al_stems)):
                w_sc = 20
            if w in params_low:
                w_sc += 25
            elif any(w in p for p in params_low):
                w_sc += 15
            if w in doc_first_low:
                w_sc += 10
            elif w in doc_low:
                w_sc += 5
            if w_sc > best_w:
                best_w = w_sc
        score += best_w
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


@register(["поиск_команд", "диагностика", "поиск"])
def diagnostics(args):
    """Найти команды по подстроке в имени / докстринге / параметрах.

    Параметры:
      сервис / подстрока — что искать (обязательный). Несколько
                  формулировок можно перечислить через "/":
                  "прочитать файл / открыть текст из файла / read file".
                  Команды сортируются по числу совпавших формулировок,
                  затем по обычной релевантности. Счётчик в выводе не показывается.
      где     — 'имя' | 'док' | 'параметр' | 'всё' (по умолчанию 'всё')
      алиасы  — true/false, показывать ли альтернативные имена
      лимит   — сколько показать (по умолчанию 10; 0 или 'все' — все)

    Ищет с учётом латиница↔кириллица, синонимов и эквивалентности "_" пробелу.
    Для многословных запросов совмещает точное попадание фразы с пословными
    альтернативами (с пониженным весом), оставляя точное совпадение на первом месте.
    """
    q = ""
    if isinstance(args, dict):
        q = (args.get("сервис") or args.get("подстрока") or args.get("запрос") or "").strip()
    elif isinstance(args, str):
        q = args.strip()
    if not q:
        import random
        pool = []
        seen = set()
        for name, fn in PRIMARY_HANDLERS.items():
            if not name or id(fn) in seen or name in ("поиск_команд", "диагностика", "поиск"):
                continue
            seen.add(id(fn))
            doc = (fn.__doc__ or "").strip()
            doc_short = doc.split("\n", 1)[0].strip() if doc else ""
            if "временно недоступно" in doc_short.lower() or "⚠️" in doc_short:
                continue
            pool.append((name, doc_short, _params_of(fn)))
        sample = random.sample(pool, min(5, len(pool))) if pool else []
        lines = [f"🎲 Пустой запрос — вот {len(sample)} случайных команд (всего доступно {len(pool)}):", ""]
        for name, doc_short, params in sample:
            lines.append(f"• команда: {name}")
            if doc_short:
                lines.append(f"  {doc_short}")
            if params:
                lines.append(f"  параметры: {', '.join(params)}")
            lines.append("")
        lines.append('💡 Поиск по теме: {"поиск_команд": {"сервис": "файлы"}} · Подробнее: {"справка": "имя_команды"}')
        return "\n".join(lines).rstrip()
    where = (args.get("где") or "всё").strip().lower()
    has_explicit_lim = "лимит" in args

    params_cache = {}
    def params_of(fn):
        key = id(fn)
        if key not in params_cache:
            params_cache[key] = _params_of(fn)
        return params_cache[key]

    def _parse_lim():
        lim = args.get("лимит", 10)
        if isinstance(lim, str):
            _ls = lim.strip().lower()
            if _ls in ("все", "all", "0", ""):
                return 0
            try:
                return max(0, int(_ls))
            except ValueError:
                return 10
        return max(0, int(lim))

    def _parse_aliases():
        show_aliases = args.get("алиасы", False)
        if isinstance(show_aliases, str):
            return show_aliases.strip().lower() in ("1", "true", "да", "yes", "on")
        return bool(show_aliases)

    def _render(matches, total, lim, header_q, suffix=""):
        show_aliases = _parse_aliases()
        where_part = "" if where in ("всё", "все", "all", "") else f", где={where}"
        header = f"🔎 Найдено команд по '{header_q}'{where_part}{suffix}"
        if lim > 0 and total > lim:
            header += f" — топ-{lim}:"
        else:
            header += ":"
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
            import json
            hint = json.dumps({"поиск_команд": {"сервис": header_q, "лимит": 0}}, ensure_ascii=False)
            lines.append(f"… и ещё {total - lim}. Показать все: {hint}")
        return "\n".join(lines).rstrip()

    def _word_fallback(phrase):
        words = [w.strip() for w in phrase.split() if len(w.strip()) >= 2]
        if len(words) <= 1:
            return {}
        agg_w = {}
        for qi, word in enumerate(words):
            matches_w = _scan(_alts(word), where, params_of)
            if not matches_w and len(word) >= 4:
                _w = word
                for _ in range(2):
                    _w = _w[:-1]
                    matches_w = _scan(_alts(_w), where, params_of)
                    if matches_w:
                        break
            for sc, name, aliases, doc, params in matches_w:
                e = agg_w.setdefault(name, {
                    "score": 0.0,
                    "queries": set(),
                    "aliases": aliases,
                    "doc": doc,
                    "params": params,
                })
                e["score"] += sc * 0.4
                e["queries"].add(qi)
        min_hits = len(words) if len(words) == 2 else max(2, (len(words) + 1) // 2)
        return {k: v for k, v in agg_w.items() if len(v["queries"]) >= min_hits}

    parts = [p.strip() for p in q.split("/") if p.strip()]
    subqs = []
    seen_q = set()
    for p in parts:
        k = p.lower()
        if k not in seen_q:
            seen_q.add(k)
            subqs.append(p)
    if not subqs:
        subqs = [q]

    lim = _parse_lim()

    def _not_found_with_memory(query_str: str) -> str:
        from _color import color_red
        head = color_red(f"Команды {query_str} нет.")
        try:
            from _actions.social_memory import recall as _recall
            mem_hit = _recall({"про": query_str})
            if not mem_hit or mem_hit.startswith("Ничего не найдено."):
                words = [w for w in _re.findall(r"[а-яА-ЯёЁa-zA-Z0-9_-]{3,}", query_str)]
                if len(words) > 1:
                    mem_hit = _recall({"про": "|".join(words)})
            if mem_hit and not mem_hit.startswith("Ничего не найдено."):
                return f"{head}\n\n🧠 Но нашлось в памяти агента:\n{mem_hit}"
        except Exception:
            pass
        return f'{head}\n💡 Поиск по памяти: {{"вспомнить": {{"про": "{query_str}"}}}}'

    if len(subqs) == 1:
        sq = subqs[0]
        alts = _alts(sq)
        exact_matches = _scan(alts, where, params_of)
        _original_q = sq
        _tries = 0
        while not exact_matches and len(sq) >= 4 and _tries < 3:
            sq = sq[:-1]
            _tries += 1
            alts = _alts(sq)
            exact_matches = _scan(alts, where, params_of)

        word_agg = _word_fallback(_original_q)
        merged = {}
        for sc, name, aliases, doc, params in exact_matches:
            merged[name] = [float(sc) + 100.0, name, aliases, doc, params, True]
        for name, e in word_agg.items():
            if name in merged:
                merged[name][0] += e["score"]
            else:
                merged[name] = [e["score"], name, e["aliases"], e["doc"], e["params"], False]

        if not merged:
            return _not_found_with_memory(_original_q)

        has_exact = any(v[5] for v in merged.values())
        all_list = sorted(merged.values(), key=lambda x: (-int(x[5]), -x[0], x[1]))
        matches = [(int(round(v[0])), v[1], v[2], v[3], v[4]) for v in all_list]
        total = len(matches)
        eff_lim = lim if has_explicit_lim else (5 if has_exact and len(word_agg) > 0 else lim)
        if eff_lim > 0:
            matches = matches[:eff_lim]
        if not exact_matches and word_agg:
            _suffix = " [пословный поиск]"
        elif sq != _original_q:
            _suffix = f" [уточнено: '{_original_q}' → '{sq}']"
        else:
            _suffix = ""
        return _render(matches, total, eff_lim, sq if exact_matches else _original_q, _suffix)

    agg = {}
    for qi, sq in enumerate(subqs):
        alts = _alts(sq)
        matches = _scan(alts, where, params_of)
        if not matches and len(sq) >= 4:
            _sq = sq
            for _ in range(2):
                _sq = _sq[:-1]
                matches = _scan(_alts(_sq), where, params_of)
                if matches:
                    break
        for sc, name, aliases, doc, params in matches:
            e = agg.setdefault(name, {
                "score": 0,
                "queries": set(),
                "aliases": aliases,
                "doc": doc,
                "params": params,
            })
            e["score"] = max(e["score"], sc)
            e["queries"].add(qi)

    if not agg:
        return _not_found_with_memory(q)

    matches = [(e["score"], name, e["aliases"], e["doc"], e["params"])
               for name, e in agg.items()]
    matches.sort(key=lambda x: (-len(agg[x[1]]["queries"]), -x[0], x[1]))
    total = len(matches)
    if lim > 0:
        matches = matches[:lim]
    return _render(matches, total, lim, q)

@register(["найти_функцию", "поиск_функции", "где_функция"])
def find_function(args):
    """Найти определение функции в исходниках anyjson.

    Параметры:
      имя       — подстрока имени. Если не задано — выводятся все
                  функции (список без тел — для навигации по коду).
      регулярка — true: имя как regex (только при заданном «имя»)
      точно     — true: только полное совпадение (имени def или алиаса)
      тело      — true: вернуть исходник функции
      путь      — папка или файл (по умолч. _actions, _core, anyjson.py)
      количество — лимит (по умолч. 20)

    Ищет и по имени функции (def), и по строковым литералам
    в @register(...) — то есть по русским алиасам команд.
    Совпадение по алиасу помечается в выводе: [алиас: "X"].
    Приоритет: точное имя > точный алиас > подстрока в имени
    > подстрока в алиасе.
    """
    import ast
    import pathlib
    import re as _re

    def _register_aliases_of(node):
        """Строковые литералы из @register(...) на функции.

        Формы: @register("a"), @register(["a", "b"]),
        @register(("a", "b")), @register(names=["a", "b"]).
        Непарсящиеся формы (f-строки, ListComp, *args) тихо пропускаются.
        """
        out = []
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if isinstance(f, ast.Name):
                fname = f.id
            elif isinstance(f, ast.Attribute):
                fname = f.attr
            else:
                continue
            if fname != "register":
                continue
            for arg in dec.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(arg.value)
                elif isinstance(arg, (ast.List, ast.Tuple)):
                    for el in arg.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            out.append(el.value)
            for kw in dec.keywords:
                v = kw.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
                elif isinstance(v, (ast.List, ast.Tuple)):
                    for el in v.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            out.append(el.value)
            break
        return out

    def _match_score(fn, nm, exact, pat, is_alias):
        """100 — точное имя, 80 — точный алиас,
        60 — подстрока в имени, 40 — подстрока в алиасе. 0 — нет."""
        if exact:
            if fn == nm:
                return 80 if is_alias else 100
            return 0
        if pat:
            return (40 if is_alias else 60) if pat.search(fn) else 0
        return (40 if is_alias else 60) if (nm.lower() in fn.lower()) else 0

    name = args.get("имя") or args.get("name") or ""
    _list_all = not name
    if _list_all:
        is_re = False
        exact = False
        pat = None
    else:
        is_re = bool(args.get("регулярка"))
        exact = bool(args.get("точно"))
        pat = _re.compile(name) if is_re else None
    with_body = bool(args.get("тело"))
    _default_limit = 1000 if _list_all else 20
    limit = int(args.get("количество") or _default_limit)

    _base = pathlib.Path(__file__).resolve().parent.parent
    paths = []
    p_arg = args.get("путь")
    if p_arg:
        p_p = pathlib.Path(p_arg)
        if p_p.is_file(): paths = [p_p]
        elif p_p.is_dir(): paths = list(p_p.rglob("*.py"))
    if not paths:
        paths = list((_base / "_actions").rglob("*.py")) + \
                list((_base / "_core").rglob("*.py")) + \
                [_base / "anyjson.py"]

    hits = []
    for f in paths:
        if not f.exists(): continue
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            fn = node.name
            aliases = _register_aliases_of(node)
            if _list_all:
                score = 100 if aliases else 50
                matched_aliases = list(aliases)
            else:
                score = _match_score(fn, name, exact, pat, is_alias=False)
                matched_aliases = []
                for a in aliases:
                    s = _match_score(a, name, exact, pat, is_alias=True)
                    if s > 0:
                        matched_aliases.append(a)
                    if s > score:
                        score = s
                if score == 0:
                    continue
            try:
                rel = f.relative_to(_base)
            except ValueError:
                rel = f
            hits.append((score, str(rel), node.lineno, fn, src, node, matched_aliases))

    if not hits:
        return "Функций не найдено." if _list_all else ("Не найдено: " + name)

    if _list_all:
        hits.sort(key=lambda h: (h[1], h[2]))
        _files = len(set(h[1] for h in hits))
        out = [f"🔍 Все функции: {len(hits)} в {_files} файлах"]
    else:
        hits.sort(key=lambda h: (-h[0], h[1], h[2]))
        out = [f"🔍 Найдено {len(hits)} по «{name}»:"]
    for i, (score, rel, lineno, fn, src, node, matched_aliases) in enumerate(hits[:limit], 1):
        line = f"\n{i}. {fn}"
        if matched_aliases:
            line += "  [алиас: " + ", ".join(repr(a) for a in matched_aliases) + "]"
        out.append(line)
        out.append(f"   📄 {rel}:{lineno}")
        if with_body:
            lines = src.split("\n")
            body = "\n".join(lines[node.lineno-1:node.end_lineno])
            out.append("```python\n" + body + "\n```")
    if len(hits) > limit:
        out.append(f"\n... и ещё {len(hits) - limit}")
    return "\n".join(out)
