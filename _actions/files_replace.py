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
      предпросмотр      — не писать файл, только показать, сколько заменится
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
                        _cap = 2000
                        _shown = _new2 if len(_new2) <= _cap else _new2[:_cap] + NL + "…(обрезано)"
                        return ("👁 [предпросмотр] Заменилось бы " + str(_cnt2)
                                + " вхождений в " + path.name
                                + " (нашлось после нормализации переводов строк):"
                                + NL + NL + _shown)
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
                if _orig_crlf:
                    _new3 = _new3.replace(NL, CR + NL)
                if preview:
                    _cap = 2000
                    _shown = (_new3 if len(_new3) <= _cap
                              else _new3[:_cap] + NL + "…(обрезано)")
                    return ("👁 [предпросмотр, нестрого] Заменилось бы "
                            + str(_cnt3) + " вхождений в " + path.name
                            + " (формат будет нормализован):"
                            + NL + NL + _shown)
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
        out += NL + NL + "🤔 Ближайшие совпадения:"
        for _r, _i, _ls in candidates:
            out += NL + NL
            out += "  📄 стр. " + str(_i + 1) + " — сходство " \
                   + format(_r * 100, ".0f") + "%:"
            # берём столько же строк из файла, сколько в «найти»
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
                _line_show = _d if len(_d) <= 200 else _d[:200] + "…"
                out += NL + "    " + _line_show

            # посимвольное сравнение каждой пары строк
            for _k in range(min(len(_find_lines), len(_file_block))):
                _a = _find_lines[_k]
                _b = _file_block[_k]
                if _a == _b:
                    continue
                for _ci in range(min(len(_a), len(_b))):
                    if _a[_ci] != _b[_ci]:
                        out += NL + "    ⚠️ стр. " + str(_i + 1 + _k) \
                               + " поз. " + str(_ci) + " — первое расхождение:"
                        out += NL + "        ищем:    " + repr(_a[_ci]) \
                               + "  (U+" + format(ord(_a[_ci]), "04X") + ")"
                        out += NL + "        в файле: " + repr(_b[_ci]) \
                               + "  (U+" + format(ord(_b[_ci]), "04X") + ")"
                        break
                else:
                    # одна строка — префикс другой
                    if len(_a) < len(_b):
                        out += NL + "    ⚠️ стр. " + str(_i + 1 + _k) \
                               + " — в файле строка длиннее на " \
                               + str(len(_b) - len(_a)) + " симв."
                        _tail = _b[len(_a):][:30]
                        out += NL + "        хвост в файле: " + repr(_tail)
                    elif len(_a) > len(_b):
                        out += NL + "    ⚠️ стр. " + str(_i + 1 + _k) \
                               + " — в «ищем» строка длиннее на " \
                               + str(len(_a) - len(_b)) + " симв."
                        _tail = _a[len(_b):][:30]
                        out += NL + "        хвост в «ищем»: " + repr(_tail)

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
        _cap = 2000
        _pv = new_text.replace("\n", "\r\n") if _orig_crlf else new_text
        _shown = _pv if len(_pv) <= _cap else _pv[:_cap] + "\n…(обрезано, всего " + str(len(_pv)) + " симв.)"
        return f"👁 [предпросмотр] Заменилось бы {cnt} вхождений в {path.name}:\n\n{_shown}"

    try:
        _new_to_write = new_text.replace("\n", "\r\n") if _orig_crlf else new_text
        _write_text(path, _new_to_write, enc)
    except Exception as e:
        return f"❌ Не удалось записать файл: {e}"

    _out = f"✅ Заменено {cnt} вхождений в {path.name} (кодировка {enc})"
    if examples:
        _out += "\n"
        for i, (b, a) in enumerate(examples, 1):
            _out += f"\n  {i}) до:    {b}"
            _out += f"\n     после: {a}"
        if cnt > len(examples):
            _out += f"\n  … ещё {cnt - len(examples)} (не показаны)"
    return _out

