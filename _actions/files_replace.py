"""Замена текста в файлах: X → Y."""

import re
from pathlib import Path

from _actions import register
from _actions.files import _FORBIDDEN_WRITE_PREFIXES


def _is_forbidden(path: Path):
    try:
        s = str(path.resolve()).lower()
    except Exception:
        s = str(path).lower()
    for prefix in _FORBIDDEN_WRITE_PREFIXES:
        p = str(prefix).lower().rstrip("\\/")
        if s == p or s.startswith(p + "\\") or s.startswith(p + "/"):
            return prefix
    return None


def _read_text(path: Path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be"), "utf-16-be"
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def _write_text(path: Path, text: str, enc: str) -> None:
    if enc == "utf-8-sig":
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    else:
        # newline="" обязателен: иначе Path.write_text на Windows сам
        # конвертирует \n -> \r\n, а код уже сделал это выше -> двойной CR.
        with open(path, "w", encoding=enc, newline="") as f:
            f.write(text)


def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "нет", "off", ""):
        return False
    if s in ("1", "true", "yes", "да", "on"):
        return True
    return default


def _normalize_for_search(text):
    """Огрубление текста для нестрогого поиска.

    Что делает:
      • CRLF/CR -> LF (на случай, если что-то осталось);
      • NBSP и узкие/широкие пробелы -> обычный пробел;
      • тире (— – − ‒ ―) -> дефис "-";
      • умные кавычки « » „ ‟ ″ -> \", ' ' ‚ ‛ ′ -> ';
      • 3+ пустых строки -> 2.

    Табы НЕ трогает (в коде — значимые).
    """
    NL = chr(10)
    CR = chr(13)
    t = text.replace(CR + NL, NL).replace(CR, NL)
    for u in ("\u00a0", "\u202f", "\u2009", "\u2007",
              "\u2008", "\u200a", "\u2002", "\u2003", "\u3000"):
        t = t.replace(u, " ")
    for u in ("\u2014", "\u2013", "\u2212", "\u2012", "\u2015"):
        t = t.replace(u, "-")
    for u in ("\u201c", "\u201d", "\u201e", "\u2033",
              "\u00ab", "\u00bb"):
        t = t.replace(u, '"')
    for u in ("\u2018", "\u2019", "\u201a", "\u2032"):
        t = t.replace(u, "'")
    # 2+ идущих \n -> ОДНА \n (полностью убираем пустые строки).
    # Для нестрогого поиска это ключевое: 1/2/3 пустых строки между
    # абзацами — разные байты, но ищутся одинаково.
    import re as _re_norm
    t = _re_norm.sub(chr(10) + "{2,}", chr(10), t)
    return t


def _build_diff_out(old_text, new_text, examples, limit=40):
    """Общий diff-хвост для боевого режима и предпросмотра.

    Единый источник формата: и предпросмотр, и запись показывают
    одни и те же @@ -A,B +C,D @@ блоки. Возвращает строку, начинающуюся
    с NL (или пустую, если диффить нечего и examples пуст).
    """
    NL = chr(10)
    out = ""
    try:
        import difflib as _df
        _old_lines = old_text.split(NL)
        _new_lines = new_text.split(NL)
        _diff = list(_df.unified_diff(_old_lines, _new_lines,
                                      lineterm="", n=2))
        # шапки ---/+++ не нужны — путь и так в заголовке
        _body = [_d for _d in _diff
                 if not _d.startswith("---")
                 and not _d.startswith("+++")]
        if _body:
            out += NL
            for _d in _body[:limit]:
                out += NL + "  " + _d
            if len(_body) > limit:
                out += (f"{NL}  … diff обрезан: "
                        f"{len(_body) - limit} строк")
        elif examples:
            # diff пуст (замена == поиск?): fallback на компактный показ
            for i, (b, a) in enumerate(examples, 1):
                out += f"{NL}  {i}) до:    {b}"
                out += f"{NL}     после: {a}"
    except Exception:
        if examples:
            for i, (b, a) in enumerate(examples, 1):
                out += f"{NL}  {i}) до:    {b}"
                out += f"{NL}     после: {a}"
    return out


@register(["заменить_текст", "замена_текста", "заменить_в_тексте"])
def replace_text(args: dict) -> str:
    """Заменить X на Y в текстовом файле.

    Параметры:
      путь (обяз.)      — путь к файлу
      найти (обяз.)     — что искать (X)
      заменить (обяз.)  — на что заменить (Y)
      все               — заменить все вхождения (по умолчанию true)
      регулярка         — трактовать «найти» как regex (по умолчанию false)
      регистр           — учитывать регистр (по умолчанию true)
      предпросмотр      — не писать файл, только показать diff и счётчик
      нестрого          — если точный поиск не нашёл, попробовать огрубить
                          текст: NBSP->пробел, тире->дефис, «»->"", 3+ пустых
                          строк->2 (по умолчанию true). При срабатывании
                          в файл пишется НОРМАЛИЗОВАННЫЙ текст.
      показать_близкое  — при «не найдено» показать похожие строки
                          (по умолчанию true)
      порог             — 0..1, минимальное сходство (по умолчанию 0.5)
      строк_в_контексте — сколько похожих строк показать (по умолчанию 3)

    Пример:
      {"заменить_текст": {"путь": "C:/temp/a.txt",
                          "найти": "foo",
                          "заменить": "bar"}}
    """
    file_arg = args.get("путь") or args.get("файл")
    if not file_arg:
        return "❌ Не указан параметр «путь»"

    find_arg = args.get("найти")
    if find_arg is None:
        find_arg = args.get("что") or args.get("старое")
    if find_arg is None:
        return "❌ Не указан параметр «найти» (X)"

    repl_arg = args.get("заменить")
    if repl_arg is None:
        repl_arg = args.get("на") or args.get("новое")
    if repl_arg is None:
        return "❌ Не указан параметр «заменить» (Y)"

    find_s = str(find_arg)
    repl_s = str(repl_arg)
    use_regex = _as_bool(args.get("регулярка", args.get("regex")), False)
    replace_all = _as_bool(args.get("все"), True)
    case_sensitive = _as_bool(args.get("регистр"), True)
    preview = _as_bool(args.get("предпросмотр"), False)

    path = Path(file_arg).expanduser()
    if not path.exists():
        return f"❌ Файл не найден: {path}"
    if not path.is_file():
        return f"❌ Это не файл: {path}"

    forbidden = _is_forbidden(path)
    if forbidden:
        return f"❌ Запись в {forbidden} запрещена"

    try:
        text, enc = _read_text(path)
    except Exception as e:
        return f"❌ Не удалось прочитать файл: {e}"

    # Файл может быть CRLF. Нормализуем до LF — иначе поиск
    # по «найти» из JSON (без \r) не совпадает. При записи вернём обратно.
    _orig_crlf = "\r\n" in text
    if _orig_crlf:
        text = text.replace("\r\n", "\n").replace("\r", "\n")

    flags = 0 if case_sensitive else re.IGNORECASE
    cnt = 0
    examples = []          # (до, после) с контекстом
    CTX = 25               # символов контекста слева/справа
    MAX_SAMPLES = 3        # сколько разных фрагментов показывать

    def _snip(s, pos, length):
        a = max(0, pos - CTX)
        b = min(len(s), pos + length + CTX)
        left = "…" if a > 0 else ""
        right = "…" if b < len(s) else ""
        return left + s[a:b].replace(chr(10), "⏎") + right

    def _ctx_pair(src, pos, match_len, repl_s):
        """Снимки «до» и «после» с CTX символами контекста.

        Оба строятся из СЫРОГО текста — поэтому «после» гарантированно
        отличается от «до» даже когда find/repl содержит переводы строк.
        Подмена \n на ⏎ применяется в самом конце, после нарезки.
        """
        a = max(0, pos - CTX)
        b = min(len(src), pos + match_len + CTX)
        left = "…" if a > 0 else ""
        right = "…" if b < len(src) else ""

        before_raw = src[a:b]
        after_raw = src[a:pos] + repl_s + src[pos + match_len:b]

        def _fmt(x):
            return left + x.replace(chr(10), "⏎") + right

        return _fmt(before_raw), _fmt(after_raw)

    if use_regex:
        try:
            pattern = re.compile(find_s, flags)
        except re.error as e:
            return f"❌ Ошибка в регулярном выражении: {e}"
        _limit = 0 if replace_all else 1
        def _repl(m):
            if len(examples) < MAX_SAMPLES:
                examples.append(_ctx_pair(text, m.start(),
                                          m.end() - m.start(), repl_s))
            return repl_s
        new_text, cnt = pattern.subn(_repl, text, count=_limit)
    else:
        if case_sensitive:
            if replace_all:
                _start = 0
                while len(examples) < MAX_SAMPLES:
                    _i = text.find(find_s, _start)
                    if _i < 0:
                        break
                    examples.append(_ctx_pair(text, _i, len(find_s), repl_s))
                    _start = _i + len(find_s)
                cnt = text.count(find_s)
                new_text = text.replace(find_s, repl_s)
            else:
                _i = text.find(find_s)
                if _i >= 0:
                    examples.append(_ctx_pair(text, _i, len(find_s), repl_s))
                    cnt = 1
                    new_text = text.replace(find_s, repl_s, 1)
                else:
                    cnt = 0
                    new_text = text
        else:
            pattern = re.compile(re.escape(find_s), re.IGNORECASE)
            _limit = 0 if replace_all else 1
            def _repl2(m):
                if len(examples) < MAX_SAMPLES:
                    examples.append(_ctx_pair(text, m.start(),
                                              m.end() - m.start(), repl_s))
                return repl_s
            new_text, cnt = pattern.subn(_repl2, text, count=_limit)

    if cnt == 0:
        # --- попытка 2: нормализация переводов строк (CRLF <-> LF) ---
        NL = chr(10)
        CR = chr(13)
        if NL in find_s:
            _variants = []
            if CR + NL in find_s:
                _variants.append(find_s.replace(CR + NL, NL))
            else:
                _variants.append(find_s.replace(NL, CR + NL))
            for _v in _variants:
                if not _v or _v == find_s:
                    continue
                if _v in text:
                    _cnt2 = text.count(_v) if replace_all else 1
                    if replace_all:
                        _new2 = text.replace(_v, repl_s)
                    else:
                        _new2 = text.replace(_v, repl_s, 1)
                    if preview:
                        _out = ("👁 [предпросмотр] Заменилось бы " + str(_cnt2)
                                + " вхождений в " + path.name
                                + " (нашлось после нормализации переводов строк):")
                        return _out + _build_diff_out(text, _new2, [])
                    try:
                        _write_text(path, _new2, enc)
                    except Exception as e:
                        return "❌ Не удалось записать файл: " + str(e)
                    return ("✅ Заменено " + str(_cnt2) + " вхождений в "
                            + path.name + " (нашлось после нормализации "
                            "переводов строк)")

        # --- попытка 3: нестрогий поиск (нестрого=true по умолчанию) ---
        notstrict = _as_bool(args.get("нестрого"), True)
        if notstrict and not use_regex:
            _tn = _normalize_for_search(text)
            _fn = _normalize_for_search(find_s)
            if _fn and _fn in _tn:
                _cnt3 = _tn.count(_fn) if replace_all else 1
                if replace_all:
                    _new3 = _tn.replace(_fn, repl_s)
                else:
                    _new3 = _tn.replace(_fn, repl_s, 1)
                if preview:
                    _out = ("👁 [предпросмотр, нестрого] Заменилось бы "
                            + str(_cnt3) + " вхождений в " + path.name
                            + " (формат будет нормализован):")
                    return _out + _build_diff_out(text, _new3, [])
                if _orig_crlf:
                    _new3 = _new3.replace(NL, CR + NL)
                try:
                    _write_text(path, _new3, enc)
                except Exception as e:
                    return "❌ Не удалось записать файл: " + str(e)
                return ("✅ Заменено " + str(_cnt3) + " вхождений в "
                        + path.name + " (нестрогий режим — "
                        "NBSP/тире/кавычки/3+ пустых строк нормализованы)")

        # --- попытка 4: показать похожие строки ---
        out = "❌ Совпадений не найдено в " + path.name
        show_close = _as_bool(args.get("показать_близкое"), True)
        if not show_close:
            return out

        try:
            threshold = float(args.get("порог", 0.5))
        except (TypeError, ValueError):
            threshold = 0.5
        try:
            max_n = int(args.get("строк_в_контексте", 3))
        except (TypeError, ValueError):
            max_n = 3
        if max_n < 1:
            max_n = 3

        needle_first = ""
        for _l in find_s.split(NL):
            if _l.strip():
                needle_first = _l.strip()
                break
        if not needle_first:
            return out + NL + NL + "(«найти» пустой)"

        try:
            import difflib as _df
        except Exception:
            return out

        text_lines = text.split(NL)
        candidates = []
        for _i, _line in enumerate(text_lines):
            _ls = _line.strip()
            if not _ls:
                continue
            _r = _df.SequenceMatcher(None, needle_first, _ls).ratio()
            if _r >= threshold:
                candidates.append((_r, _i, _ls))
        candidates.sort(key=lambda x: -x[0])
        candidates = candidates[:max_n]

        if not candidates:
            out += NL + NL + ("(похожих строк не найдено — проверь кодировку "
                              "файла или невидимые символы)")
            return out

        _find_lines = find_s.split(NL)

        def _line_div(a, b):
            """Вернуть (kind, pos, extra) для пары строк или None."""
            _m = min(len(a), len(b))
            for _ci in range(_m):
                if a[_ci] != b[_ci]:
                    return ("char", _ci, None)
            if len(a) > len(b):
                return ("tail_find", _m, a[_m:])
            if len(b) > len(a):
                return ("tail_file", _m, b[_m:])
            return None

        _summary = []
        _r0, _i0, _ls0 = candidates[0]
        _block_end0 = min(len(text_lines), _i0 + len(_find_lines))
        _file_block0 = text_lines[_i0:_block_end0]
        _n_cmp = min(len(_find_lines), len(_file_block0))
        for _k in range(_n_cmp):
            _a = _find_lines[_k]
            _b = _file_block0[_k]
            if _a == _b:
                continue
            _d = _line_div(_a, _b)
            if _d is None:
                continue
            _kind, _pos, _extra = _d
            _ln = _i0 + 1 + _k
            if _kind == "char":
                _summary.append(
                    "🔍 Первое расхождение: стр. " + str(_ln)
                    + ", поз. " + str(_pos))
                _summary.append("    в «ищем»:  " + repr(_a[_pos])
                                + "  (U+" + format(ord(_a[_pos]), "04X") + ")")
                _summary.append("    в файле:   " + repr(_b[_pos])
                                + "  (U+" + format(ord(_b[_pos]), "04X") + ")")
            elif _kind == "tail_find":
                _summary.append(
                    "🔍 Первое расхождение: стр. " + str(_ln)
                    + " — в «ищем» +" + str(len(_extra)) + " симв.")
                _tail = _extra[:80] + ("…" if len(_extra) > 80 else "")
                _summary.append("    лишнее:  " + repr(_tail))
                _head = _b[:40] + ("…" if len(_b) > 40 else "")
                _summary.append("    в файле: " + repr(_head))
            else:
                _summary.append(
                    "🔍 Первое расхождение: стр. " + str(_ln)
                    + " — в файле +" + str(len(_extra)) + " симв.")
                _tail = _extra[:80] + ("…" if len(_extra) > 80 else "")
                _summary.append("    лишнее:  " + repr(_tail))
                _head = _a[:40] + ("…" if len(_a) > 40 else "")
                _summary.append("    в «ищем»: " + repr(_head))
            break
        else:
            if len(_find_lines) > len(_file_block0):
                _k = _n_cmp
                _summary.append(
                    "🔍 В «ищем» есть строка, которой нет в файле "
                    "(после стр. " + str(_i0 + _k) + "):")
                _summary.append("    L" + str(_k + 1) + ": "
                                + repr(_find_lines[_k][:120]))
            elif len(_file_block0) > len(_find_lines):
                _k = _n_cmp
                _summary.append(
                    "🔍 В файле есть строка, которой нет в «ищем» "
                    + "(после стр. " + str(_i0 + _k) + "):")
                _summary.append("    в файле L" + str(_k + 1) + ": "
                                + repr(_file_block0[_k][:120]))

        out += NL + NL
        if _summary:
            for _s in _summary:
                out += NL + _s
            out += NL + NL

        out += "🤔 Ближайшие совпадения:"
        for _r, _i, _ls in candidates:
            out += NL + NL
            out += "  📄 стр. " + str(_i + 1) + ":"
            _block_end = min(len(text_lines), _i + len(_find_lines))
            _file_block = text_lines[_i:_block_end]
            _diff = list(_df.unified_diff(
                _find_lines, _file_block,
                fromfile="ищем", tofile="в файле",
                lineterm="", n=0,
            ))
            _any_diff = False
            for _d in _diff:
                if (_d.startswith("---") or _d.startswith("+++")
                        or _d.startswith("@@")):
                    continue
                _any_diff = True
                if _d.startswith("- ") or _d.startswith("+ "):
                    _prefix = _d[:2]
                    _content = _d[2:]
                elif _d.startswith("  "):
                    _prefix = "  "
                    _content = _d[2:]
                elif _d.startswith("-") or _d.startswith("+"):
                    _prefix = _d[0]
                    _content = _d[1:]
                else:
                    _prefix = ""
                    _content = _d
                if len(_content) <= 200:
                    _line_show = _prefix + repr(_content)
                else:
                    _line_show = _prefix + repr(_content[:200]) + "…"
                out += NL + "    " + _line_show

            if not _any_diff:
                out += NL + "    (строки идентичны по тексту — "
                out += "проверь невидимые символы: NBSP, tab, trailing spaces)"
            # если «найти» длиннее, чем осталось в файле
            if len(_find_lines) > len(_file_block):
                out += NL + ("    ⚠️ в файле после этой строки только "
                             + str(len(_file_block)) + " строк(и), "
                             + "а в «найти» — " + str(len(_find_lines)))
        out += NL + NL + ("💡 «Найти» может отличаться пробелами, тире "
                          "(— vs -), кавычками (« vs \") или переводами строк.")
        return out

    if preview:
        _out = f"👁 [предпросмотр] Заменилось бы {cnt} вхождений в {path.name}:"
        return _out + _build_diff_out(text, new_text, examples)

    try:
        _new_to_write = new_text.replace("\n", "\r\n") if _orig_crlf else new_text
        _write_text(path, _new_to_write, enc)
    except Exception as e:
        return f"❌ Не удалось записать файл: {e}"

    _out = f"✅ Заменено {cnt} вхождений в {path.name} (кодировка {enc})"
    return _out + _build_diff_out(text, new_text, examples)