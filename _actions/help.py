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
      запрос — имя команды
    """
    if isinstance(args, str):
        query = args.strip()
    elif isinstance(args, dict):
        query = (args.get("запрос") or args.get("команда")
                 or args.get("тема") or args.get("имя")
                 or args.get("что") or "").strip()
    else:
        query = ""

    if not query:
        import random as _rnd
        import re as _re_help
        _ru_only = _re_help.compile(r'^[а-яА-ЯёЁ0-9_]+$')
        names = [n for n in HANDLERS if n and _ru_only.match(n)]
        picks = _rnd.sample(names, min(3, len(names))) if names else []
        lines = ['Укажи имя команды: {"помощь": "имя_команды"}']
        lines.append("")
        lines.append("Имя не обязательно точное — если такого нет, "
                     "найдутся похожие и предложатся варианты.")
        if picks:
            lines.append("")
            lines.append("Возможно, тебя интересует:")
            for n in picks:
                lines.append(f'  • "{n}"')
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
        by_param = _find_by_param(query)
        if by_param:
            lines = ["❌ Команды '" + query + "' нет. Но параметр '" + query + "' есть в командах:"]
            for n, d in by_param[:10]:
                lines.append('  • "' + n + '" — ' + d)
            if len(by_param) > 10:
                lines.append("  ... и ещё " + str(len(by_param) - 10))
            lines.append("")
            lines.append("💡 Имя не обязательно точное — можно искать "
                         "по подстроке или по параметру.")
            lines.append('📚 Справка по команде: {"помощь": "имя_команды"}')
            return chr(10).join(lines)
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
