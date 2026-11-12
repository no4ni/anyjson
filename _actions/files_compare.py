"""Сравнение двух файлов: одинаковые или разные."""
import hashlib
import difflib
import os
import time
from pathlib import Path

from _actions import register


def _sha256(path, chunk=65536):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _fmt_size(n):
    if n > 1024 * 1024:
        return format(n / (1024 * 1024), ".2f") + " МБ"
    if n > 1024:
        return format(n / 1024, ".1f") + " КБ"
    return str(n) + " Б"


def _fmt_mtime(path):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(os.path.getmtime(path)))
    except Exception:
        return "?"


def _looks_binary(path, sample=4096):
    try:
        with open(path, "rb") as f:
            head = f.read(sample)
    except Exception:
        return True
    if not head:
        return False
    if b"\x00" in head:
        return True
    bad = 0
    for b in head:
        if b < 9 or b in (11, 12) or (13 < b < 32):
            bad += 1
    return (bad / len(head)) > 0.1


def _read_text(path):
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace"), "utf-16-be"
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


@register(["сравнить_файлы", "сравнить", "одинаковые"])
def compare_files(args):
    """Сравнить два файла: одинаковые или разные.

    Параметры:
      путь_а  — первый файл (обязательный), алиасы: a, файл1, первый
      путь_б  — второй файл (обязательный), алиасы: b, файл2, второй
      подробно — true: показать первое расхождение и контекст
                 (работает только для текстовых файлов)

    Что показывает:
      • размер и mtime каждого файла;
      • одинаковые ли (sha256 при совпадении размера);
      • первое расхождение (строка, позиция) — при подробно=true;
      • кодировку, если текстовая.

    Примеры:
      {"сравнить_файлы": {"путь_а": "C:/a.txt", "путь_б": "C:/b.txt"}}
      {"сравнить_файлы": {"a": "...", "b": "...", "подробно": true}}
    """
    a = args or {}
    pa = (a.get("путь_а") or a.get("a") or a.get("файл1")
          or a.get("первый") or a.get("откуда") or "").strip()
    pb = (a.get("путь_б") or a.get("b") or a.get("файл2")
          or a.get("второй") or a.get("куда") or "").strip()
    detailed = bool(a.get("подробно"))

    if not pa or not pb:
        return "Укажи два пути: путь_а и путь_б"

    for p in (pa, pb):
        if not os.path.isfile(p):
            return "Не файл или не найден: " + p

    sa = os.path.getsize(pa)
    sb = os.path.getsize(pb)
    ma = _fmt_mtime(pa)
    mb = _fmt_mtime(pb)

    lines = []

    same_size = (sa == sb)
    same_time = (ma == mb)

    if same_size:
        try:
            ha = _sha256(pa)
            hb = _sha256(pb)
        except Exception as e:
            lines.append("")
            lines.append("Ошибка чтения: " + str(e))
            return chr(10).join(lines)
        if ha == hb:
            lines.append("")
            lines.append("Одинаковые (побайтово, sha256 совпали)")
            lines.append("   sha256: " + ha)
            if not same_time:
                lines.append("   mtime отличается: " + ma + " vs " + mb)
            return chr(10).join(lines)
        else:
            lines.append("")
            lines.append("Разные (размер совпадает, содержимое нет)")
    else:
        lines.append("")
        lines.append("Разные (размер: " + str(sa) + " vs " + str(sb) + " байт)")

    if detailed:
        bin_a = _looks_binary(pa)
        bin_b = _looks_binary(pb)
        if not bin_a and not bin_b:
            try:
                ta, ea = _read_text(pa)
                tb, eb = _read_text(pb)
            except Exception as e:
                lines.append("   Не прочитать как текст: " + str(e))
                return chr(10).join(lines)
            if ea != eb:
                lines.append("   Кодировки: " + ea + " vs " + eb)
            lim = min(len(ta), len(tb))
            diff_pos = None
            for i in range(lim):
                if ta[i] != tb[i]:
                    diff_pos = i
                    break
            if diff_pos is None and len(ta) != len(tb):
                diff_pos = lim
            if diff_pos is not None:
                line_no = ta[:diff_pos].count(chr(10)) + 1
                last_nl = ta[:diff_pos].rfind(chr(10))
                pos_in_line = diff_pos - (last_nl + 1)
                ctx = 40
                seg_a = ta[max(0, diff_pos - ctx):diff_pos + ctx].replace(chr(10), "|")
                seg_b = tb[max(0, diff_pos - ctx):diff_pos + ctx].replace(chr(10), "|")
                lines.append("   Первое расхождение: строка " + str(line_no)
                             + ", поз. " + str(pos_in_line))
                lines.append("     а: ..." + seg_a + "...")
                lines.append("     б: ..." + seg_b + "...")
        else:
            lines.append("   (бинарные: показываю только размер и даты)")

    if not same_time:
        lines.append("   mtime: а=" + ma + ", б=" + mb)

    return chr(10).join(lines)

@register(["дифф", "разница", "что_нового"])
def diff_files(args):
    """Показать разницу между двумя файлами построчно.

    Параметры:
      путь_а (a) — первый файл (старый)
      путь_б (b) — второй файл (новый)
      режим      — "только_в_б" (по умолч.), "только_в_а", "оба"
      игнорировать_пробелы — не учитывать пробелы в начале/конце
      игнорировать_регистр — не учитывать регистр
      лимит      — сколько строк показать (100)

    Примеры:
      {"дифф": {"a": "C:/old.txt", "b": "C:/new.txt"}}
      {"дифф": {"a": "C:/old.txt", "b": "C:/new.txt", "режим": "оба"}}
    """
    a = args or {}
    pa = (a.get("путь_а") or a.get("a") or a.get("старый") or a.get("файл1") or "").strip()
    pb = (a.get("путь_б") or a.get("b") or a.get("новый") or a.get("файл2") or "").strip()
    mode = str(a.get("режим") or "только_в_б").lower()
    ign_space = bool(a.get("игнорировать_пробелы"))
    ign_case = bool(a.get("игнорировать_регистр"))
    limit = int(a.get("лимит") or 100)

    if not pa or not pb:
        return "Укажи два пути: путь_а и путь_б"
    for p in (pa, pb):
        if not os.path.isfile(p):
            return "Не файл или не найден: " + p
    if _looks_binary(pa) or _looks_binary(pb):
        return "Один из файлов бинарный. Дифф поддерживается только для текста."

    ta, ea = _read_text(pa)
    tb, eb = _read_text(pb)

    lines_a = ta.splitlines()
    lines_b = tb.splitlines()

    def prep(lines):
        res = []
        for line in lines:
            if ign_space:
                line = line.strip()
            if ign_case:
                line = line.lower()
            res.append(line)
        return res

    d_a = prep(lines_a)
    d_b = prep(lines_b)

    if mode in ("только_в_б", "только_в_b", "b"):
        diff = list(difflib.ndiff(d_a, d_b))
        adds = [line[2:] for line in diff if line.startswith('+ ')]
        out = [f"📄 A: {pa} ({len(lines_a)} строк)", f"📄 B: {pb} ({len(lines_b)} строк)", f"🔍 Только в B ({len(adds)} строк):"]
        for ln in adds[:limit]:
            out.append(f"  + {ln}")
        if len(adds) > limit:
            out.append(f"  ... и ещё {len(adds) - limit} строк (увеличь лимит)")
        return "\n".join(out)

    elif mode in ("только_в_а", "только_в_a", "a"):
        diff = list(difflib.ndiff(d_a, d_b))
        dels = [line[2:] for line in diff if line.startswith('- ')]
        out = [f"📄 A: {pa} ({len(lines_a)} строк)", f"📄 B: {pb} ({len(lines_b)} строк)", f"🔍 Только в A ({len(dels)} строк):"]
        for ln in dels[:limit]:
            out.append(f"  - {ln}")
        if len(dels) > limit:
            out.append(f"  ... и ещё {len(dels) - limit} строк (увеличь лимит)")
        return "\n".join(out)

    else:
        diff = list(difflib.unified_diff(d_a, d_b, fromfile=pa, tofile=pb, lineterm=''))
        if not diff:
            return "Файлы идентичны (с учётом выбранных настроек)."
        out = [f"📄 A: {pa} ({len(lines_a)} строк)", f"📄 B: {pb} ({len(lines_b)} строк)"]
        for ln in diff[:limit]:
            out.append(ln)
        if len(diff) > limit:
            out.append(f"... diff обрезан ({len(diff)} строк всего). Увеличь лимит.")
        return "\n".join(out)


def _list_files(root, mask_list):
    """Список (путь, размер) всех файлов в root, с фильтром по маскам."""
    import fnmatch
    out = []
    if os.path.isfile(root):
        try:
            out.append((root, os.path.getsize(root)))
        except OSError:
            pass
        return out
    skip = {"__pycache__", ".git", "node_modules", "venv", ".venv", ".cache"}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip]
        for f in fns:
            if mask_list:
                low = f.lower()
                if not any(fnmatch.fnmatch(low, m) for m in mask_list):
                    continue
            fp = os.path.join(dp, f)
            try:
                out.append((fp, os.path.getsize(fp)))
            except OSError:
                pass
    return out


@register(["уникальные", "уникальное", "что_только_в_а", "что_есть_только_в_а"])
def unique_files(args):
    """Показать, что есть в A, но нет в B (рекурсивно, по содержимому).

    Параметры:
      путь_а (a) — исходная папка/файл (обязательный)
      путь_б (b) — с чем сравнивать (обязательный)
      маска      — фильтр расширений через "|" ("*.py|*.md")
      лимит      — сколько показать в списке (50)
      размер     — true: только общий объём, без списка имён

    Примеры:
      {"уникальные": {"a": "D:/backup", "b": "E:/Jericho"}}
      {"уникальные": {"a": "D:/backup", "b": "E:/Jericho", "маска": "*.py|*.md"}}
    """
    a = args or {}
    pa = (a.get("путь_а") or a.get("a") or a.get("файл1") or a.get("откуда") or "").strip()
    pb = (a.get("путь_б") or a.get("b") or a.get("файл2") or a.get("куда") or "").strip()
    if not pa or not pb:
        return "Укажи два пути: путь_а и путь_б"
    if not os.path.exists(pa):
        return "Не найден путь A: " + pa
    if not os.path.exists(pb):
        return "Не найден путь B: " + pb

    mask_str = (a.get("маска") or "").strip()
    mask_list = [m.strip().lower() for m in mask_str.split("|") if m.strip()] if mask_str else []
    limit = int(a.get("лимит") or 50)
    size_only = bool(a.get("размер"))

    print("Сканирую A...")
    files_a = _list_files(pa, mask_list)
    print("A: " + str(len(files_a)) + " файлов")

    print("Сканирую B...")
    files_b = _list_files(pb, mask_list)
    print("B: " + str(len(files_b)) + " файлов")

    # Фильтр по (имя, размер) — резко сокращает хэширование
    _names_sizes_b = {(os.path.basename(p).lower(), sz) for p, sz in files_b}
    _names_sizes_a = {(os.path.basename(p).lower(), sz) for p, sz in files_a}

    unique_no_hash = [(p, sz) for p, sz in files_a
                      if (os.path.basename(p).lower(), sz) not in _names_sizes_b]
    candidates = [(p, sz) for p, sz in files_a
                  if (os.path.basename(p).lower(), sz) in _names_sizes_b]

    unique_no_hash.sort(key=lambda x: -x[1])
    candidates.sort(key=lambda x: -x[1])

    _skip_below = None
    if len(unique_no_hash) >= limit:
        _skip_below = unique_no_hash[limit - 1][1]

    if _skip_below is not None:
        _cand_active = [(p, sz) for p, sz in candidates if sz >= _skip_below]
    else:
        _cand_active = candidates
    _skipped_by_size = len(candidates) - len(_cand_active)

    print("Хэшируем B (только по активным кандидатам)...")
    hashes_b = set()
    _cand_keys_a = {(os.path.basename(p).lower(), sz) for p, sz in _cand_active}
    for p, sz in files_b:
        if (os.path.basename(p).lower(), sz) in _cand_keys_a:
            try:
                hashes_b.add(_sha256(p))
            except OSError:
                pass

    print("Хэшируем " + str(len(_cand_active)) + " файлов A "
          + (f"(отсечено {_skipped_by_size} мелких)"
             if _skipped_by_size else "") + "...")
    import time
    from tqdm import tqdm
    unique = list(unique_no_hash)
    _start = time.time()
    _timeout = int(a.get("таймаут") or 30)
    _early_stopped = False

    if _cand_active:
        with tqdm(total=len(_cand_active), desc="Хэширование", unit="файл") as pbar:
            for i, (p, sz) in enumerate(_cand_active, 1):
                if _timeout > 0 and (time.time() - _start) > _timeout:
                    _early_stopped = True
                    break
                try:
                    h = _sha256(p)
                    if h not in hashes_b:
                        unique.append((p, sz))
                except OSError:
                    unique.append((p, sz))
                pbar.update(1)
                if _early_stopped:
                    break
    if _early_stopped:
        print(f"⚠️ Ирлистоп: прошло {_timeout} сек, обработано {i} из {len(_cand_active)}")

    if not unique:
        return "В A нет ничего уникального (все " + str(len(files_a)) + " файлов есть в B)"

    unique.sort(key=lambda x: -x[1])
    total_sz = sum(sz for _, sz in unique)
    _approx = ""
    if _skipped_by_size or _early_stopped:
        _notes = []
        if _skipped_by_size:
            _notes.append(f"{_skipped_by_size} мелких (< top-{limit})")
        if _early_stopped:
            _notes.append("ирлистоп по таймауту")
        _approx = " ≥ (не хэшировано: " + "; ".join(_notes) + ")"
    result = [
        "A: " + pa,
        "B: " + pb,
        "",
        "Уникальных в A:" + _approx + " " + str(len(unique)) + " из " + str(len(files_a)) + " (" + _fmt_size(total_sz) + ")",
    ]
    if size_only:
        return "\n".join(result)
    result.append("")
    is_dir_a = os.path.isdir(pa)
    for p, sz in unique[:limit]:
        rel = os.path.relpath(p, pa) if is_dir_a else p
        result.append("  " + _fmt_size(sz).rjust(10) + "  " + rel)
    if len(unique) > limit:
        result.append("  ... и ещё " + str(len(unique) - limit) + " (увеличь лимит)")
    return "\n".join(result)