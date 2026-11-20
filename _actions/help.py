"""Помощь: проверка конкретной команды."""
from _actions import register, HANDLERS


def _syns_for(query):
    """Список команд-синонимов для запроса."""
    if not query:
        return []
    try:
        from _executor import _SYNONYMS
    except Exception:
        return []
    return list(_SYNONYMS.get(query.lower().strip(), []))


def _short(doc):
    if not doc:
        return "(без описания)"
    line = doc.strip().split("\n", 1)[0].strip()
    return line or "(без описания)"



def _find_by_param(query):
    # Ищет команды с параметром query: args.get(...) в теле + докстринг.
    import inspect as _ins
    import re as _re2
    q = (query or "").lower().strip()
    if not q:
        return []
    body_re = _re2.compile(r'args\.get\(\s*["\']([^"\']+)["\']|args\[\s*["\']([^"\']+)["\']\s*\]')
    doc_re = _re2.compile(r'^\s{2,}([а-яА-ЯёЁ_0-9]+)\s*—', _re2.MULTILINE)
    ru_re = _re2.compile(r'^[а-яА-ЯёЁ_0-9]+$')
    out = []
    seen = set()
    for name, fn in HANDLERS.items():
        if id(fn) in seen:
            continue
        if not name or not ru_re.match(name):
            continue
        try:
            src = _ins.getsource(fn)
        except (OSError, TypeError):
            src = ""
        params = set()
        for m in body_re.finditer(src):
            pp = m.group(1) or m.group(2)
            if pp:
                params.add(pp.lower())
        doc = fn.__doc__ or ""
        for m in doc_re.finditer(doc):
            params.add(m.group(1).lower())
        if q in params:
            seen.add(id(fn))
            desc = ""
            for line in doc.split("\n"):
                if q in line.lower():
                    desc = line.strip()
                    break
            if not desc:
                desc = _short(doc)
            out.append((name, desc))
    return out

@register(["помощь", "справка", "help", "?", "имя_команды", "другая_команда", "инициализация"])
def show_help(args):
    """Справка о конкретной команде.
    Параметры:
      команда — имя команды (обязательный)
    """
    if isinstance(args, str):
        query = args.strip()
    elif isinstance(args, dict):
        query = (args.get("команда") or args.get("запрос")
                 or args.get("тема") or args.get("имя")
                 or args.get("что") or "").strip()
    else:
        query = ""

    if not query:
        import random as _rnd
        import re as _re_help
        _ru_only = _re_help.compile(r'^[а-яА-ЯёЁ0-9_]+$')
        _unique_fns = {id(fn) for fn in HANDLERS.values() if fn}
        _core = [
            "поиск_команд", "читать", "переписать", "заменить_текст",
            "питон", "веб_поиск", "настроить",
        ]
        lines = [f"📚 Справка по системе (всего уникальных команд: {len(_unique_fns)})", ""]
        _mem_items = []
        _mem_total = 0
        try:
            from _actions.social_memory import load_memlist as _load_mem
            _raw_mem = _load_mem() or []
            _mem_total = len(_raw_mem)
            for _it in _raw_mem[-5:]:
                if isinstance(_it, str):
                    _s = _it.strip()
                elif isinstance(_it, dict):
                    _s = str(_it.get("текст") or _it.get("содержимое") or _it.get("content") or _it).strip()
                else:
                    _s = str(_it).strip()
                if _s:
                    if len(_s) > 180:
                        _s = _s[:180] + "…"
                    _mem_items.append(_s)
        except Exception:
            pass

        if _mem_items:
            lines.append(f"🧠 Из памяти агента (последние {len(_mem_items)} из {_mem_total}):")
            for _m in _mem_items:
                lines.append(f"  • {_m}")
            lines.append('  → Поиск по памяти: {"вспомнить": {"про": "ключевое_слово"}} · Сохранить: {"запомнить": "важный факт"}')
        else:
            lines.append("🧠 Память агента пока пуста:")
            lines.append('  • {"запомнить": "важный факт"} — сохранить прогресс на случай сброса чата')
        lines.append("")
        lines.append("🔑 Основные команды:")
        for c in _core:
            _fn = HANDLERS.get(c)
            if _fn:
                lines.append(f'  • "{c}" — {_short(_fn.__doc__)}')

        _seen_ids = {id(HANDLERS[c]) for c in _core if c in HANDLERS}
        _pool = []
        for n, fn in HANDLERS.items():
            if not n or not _ru_only.match(n) or id(fn) in _seen_ids:
                continue
            _sd = _short(fn.__doc__)
            if "временно недоступно" in _sd.lower() or "⚠️" in _sd:
                continue
            _seen_ids.add(id(fn))
            _pool.append((n, _sd))

        picks = _rnd.sample(_pool, min(3, len(_pool))) if _pool else []
        if picks:
            lines.append("")
            lines.append("🎲 Случайные команды из каталога:")
            for n, _sd in picks:
                lines.append(f'  • "{n}" — {_sd}')

        lines.append("")
        lines.append('💡 Подробно о команде: {"помощь": "имя_команды"}  ·  Поиск по теме: {"поиск_команд": "видео"}')
        return "\n".join(lines)

    fn = HANDLERS.get(query)
    if fn:
        doc = (fn.__doc__ or "(без описания)").strip()
        return f"✅ {query}\n{doc}"

    # точного совпадения нет — проверяем синонимы
    _syns = _syns_for(query)
    if _syns:
        lines = [f"🤔 Точного '{query}' нет. Возможно, ты искал:"]
        for _s in _syns:
            _fn = HANDLERS.get(_s)
            if _fn:
                lines.append(f'  • "{_s}" — {_short(_fn.__doc__)}')
        if len(lines) > 1:
            return chr(10).join(lines)

    # синонимов нет — ищем по подстроке, но БЕЗ вываливания полного списка
    matches = sorted([k for k in HANDLERS if query.lower() in k.lower()])
    if not matches:
        import difflib as _dl

        def _close_names(q, n=5):
            _ql = q.lower()
            _pref = [k for k in HANDLERS
                     if k.lower().startswith(_ql[:4])
                     or _ql.startswith(k.lower()[:4])]
            _c = _dl.get_close_matches(q, list(HANDLERS.keys()), n=n, cutoff=0.6)
            _seen, _out = set(), []
            for _k in _pref + _c:
                if _k not in _seen:
                    _seen.add(_k)
                    _out.append(_k)
            return _out[:n]

        _close = _close_names(query)
        _close_line = None
        if _close:
            _close_line = ("🔎 Похожие команды: "
                           + ", ".join('"' + _k + '"' for _k in _close))

        by_param = _find_by_param(query)
        if by_param:
            lines = ["❌ Команды '" + query + "' нет. Но параметр '" + query + "' есть в командах:"]
            for n, d in by_param[:10]:
                lines.append('  • "' + n + '" — ' + d)
            if len(by_param) > 10:
                lines.append("  ... и ещё " + str(len(by_param) - 10))
            if _close_line:
                lines.append("")
                lines.append(_close_line)
            lines.append("")
            lines.append("💡 Имя не обязательно точное — можно искать "
                         "по подстроке или по параметру.")
            lines.append('📚 Справка по команде: {"помощь": "имя_команды"}')
            return chr(10).join(lines)
        if _close_line:
            return (f"❌ Команда '{query}' не реализована.\n"
                    f"{_close_line}\n"
                    f"💡 Имя не обязательно точное — попробуй похожие "
                    f"слова (короткую подстроку), и anyjson покажет "
                    f"варианты.")
        return (f"❌ Команда '{query}' не реализована.\n"
                f"💡 Имя не обязательно точное — попробуй похожие "
                f"слова (короткую подстроку), и anyjson покажет "
                f"варианты.")
    if len(matches) == 1:
        m = matches[0]
        doc = (HANDLERS[m].__doc__ or "(без описания)").strip()
        return f"✅ {m}\n{doc}"
    lines = [f"🤔 Точного '{query}' нет. Похожие:"]
    for m in matches:
        lines.append(f"  • {m} — {_short(HANDLERS[m].__doc__)}")
    return "\n".join(lines)


@register(["список_команд", "команды", "что_умеешь", "любая_команда"])
def list_commands(args):
    """Показать 5 случайных команд из реестра.

    Это НЕ полный список и НЕ рекомендация — просто случайная выборка,
    чтобы напомнить, что вообще бывает.

    Параметры:
      количество — сколько показать (по умолчанию 5)

    Примеры:
      {"список_команд": {}}
      {"список_команд": {"количество": 10}}
    """
    import random as _rnd
    import re as _re

    args = args or {}
    try:
        n = int(args.get("количество", 5) or 5)
    except (TypeError, ValueError):
        n = 5
    if n <= 0:
        n = 5
    if n > 50:
        n = 50

    # только русские имена, без дублей по функции
    _ru = _re.compile(r"^[а-яА-ЯёЁ0-9_]+$")
    seen_fns = set()
    uniq = []
    for name, fn in HANDLERS.items():
        if not name or not _ru.match(name):
            continue
        if id(fn) in seen_fns:
            continue
        seen_fns.add(id(fn))
        uniq.append(name)

    if not uniq:
        return "Реестр команд пуст."

    total = len(uniq)
    picks = _rnd.sample(uniq, min(n, total))

    out = [f"🎲 {len(picks)} случайных команд из {total} в реестре:",
           "(это просто выборка, не полный список)", ""]
    for name in picks:
        fn = HANDLERS[name]
        doc = (fn.__doc__ or "").strip().split("\n", 1)[0].strip()
        out.append(f"  • {name}" + (f" — {doc}" if doc else ""))
    out.append("")
    out.append('📚 Помощь по команде: {"помощь": "имя_команды"}')
    out.append('🎲 Случайные: {"список_команд": {"количество": 50}}')
    return "\n".join(out)
