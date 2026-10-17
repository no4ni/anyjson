# -*- coding: utf-8 -*-
"""
Сжимает документ, сохраняя whitespace между предложениями.

Разделители (пробел, \\n, \\n\\n) сохраняются как в оригинале.
Предложения сжимаются через T5.

    py squeeze_document.py input.txt output.txt
"""

import argparse
import re
import sys
import time
from pathlib import Path

DEFAULT_MODEL = str(Path(__file__).parent / "rut5-squeeze")
EMPTY_MARK = "<empty>"

# Разделитель между предложениями: после .!?… идёт whitespace,
# затем заглавная буква / цифра / кавычка.
SENT_SEP = re.compile(r'((?<=[.!?…])[ \t\n]+|\n+)')


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-in", type=int, default=512)
    ap.add_argument("--max-out", type=int, default=128)
    ap.add_argument("--beams", type=int, default=4)
    ap.add_argument("--keep-empty", action="store_true",
                    help="оставлять <empty> вместо удаления предложения")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--fast", action="store_true",
                    help="быстрый режим: рискованные предложения "
                         "не отправляются модели, пост-проверок нет")
    ap.add_argument("--dedup", action="store_true",
                    help="дедуплицировать предложения перед отправкой "
                         "в модель, результат раскладывать по копиям")
    return ap.parse_args()


def tokenize(text):
    """
    Возвращает список (sent, sep_after).
    sep_after — whitespace между этим и следующим предложением.
    Пустые куски текста присоединяются к разделителям.
    """
    items = []
    prev_end = 0
    for m in SENT_SEP.finditer(text):
        sent = text[prev_end:m.start()]
        sep = m.group(0)
        if sent.strip():
            items.append((sent.strip(), sep))
        else:
            # пустой кусок: разделитель идёт в конец предыдущего sep
            if items:
                last_sent, last_sep = items[-1]
                items[-1] = (last_sent, last_sep + sent + sep)
            else:
                items.append(("", sent + sep))
        prev_end = m.end()
    tail = text[prev_end:]
    if tail.strip():
        items.append((tail.strip(), ""))
    return items


# ---------- NUMBERS ----------
import re as _re

# все "числовые токены": цифры + возможные разделители: . , : ; ± — – - / %
_NUM_RE = _re.compile(r"\d+(?:[.,:;±\-–/]\d+)*(?:\s*[%‰°])?")
# ratio a:b -> a к b (для препроцессинга)
_RATIO_RE = _re.compile(r"(\d+):(\d+)")


def preprocess_numbers(text):
    """Готовит числа к подаче в модель."""
    # вики-остатки
    text = _re.sub(r"^thumb\|[^\n]*", lambda m: m.group(0).replace("thumb|", ""), text)
    text = text.replace("thumb|", "")
    # 1:16 -> 1 к 16 (модель легче читает это как текст)
    text = _RATIO_RE.sub(r"\1 к \2", text)
    return text


def postprocess_numbers(text):
    """Возвращает числа к исходному виду."""
    # 1 к 16 -> 1:16 (только если это похоже на ratio)
    text = _re.sub(r"(\d+)\s+к\s+(\d+)", r"\1:\2", text)
    return text


def extract_numbers(text):
    """Множество чисел из строки (без пробелов и разделителей)."""
    nums = set()
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).strip()
        # нормализуем: убираем пробелы, приводим к нижнему
        clean = raw.replace(" ", "").lower()
        nums.add(clean)
    return nums


def numbers_preserved(src, dst):
    """Все числа из src должны присутствовать в dst."""
    src_nums = extract_numbers(src)
    if not src_nums:
        return True
    dst_nums = extract_numbers(dst)
    return src_nums.issubset(dst_nums)


# ---------- PRE-FILTER (быстрый режим) ----------
_PRE_WIN_RE = _re.compile(r"[A-Za-z]:\\[^\s,;)]+")
_PRE_IDENT_RE = _re.compile(r"\b\w+_\w+\b")
_PRE_NEG_RE = _re.compile(r"\b(?:не|нет|ни)\b", _re.IGNORECASE)
_PRE_RARE_CHARS = set("₽≈≤≥±°¹²³éÉáÁíÍóÓúÚñÑçÇß"
                       "─│┌┐└┘├┤┬┴┼"
                       "═║╔╗╚╝╠╣╦╩╬"
                       "▀▄█░▒▓")


def _is_risky(text):
    """В быстром режиме такое предложение не отдаём модели:
    пути, идентификаторы с _, отрицания, числа, редкие символы.
    Модель может их исказить, а пост-проверок в быстром режиме нет."""
    if _PRE_WIN_RE.search(text):
        return True
    if _PRE_IDENT_RE.search(text):
        return True
    if _PRE_NEG_RE.search(text):
        return True
    if _NUM_RE.search(text):
        return True
    for ch in _PRE_RARE_CHARS:
        if ch in text:
            return True
    return False


def load_model(model_dir, device):
    import torch
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    print(f"[i] Загружаю токенизатор: {model_dir}")
    tok = AutoTokenizer.from_pretrained(model_dir)
    print(f"[i] Загружаю модель")
    model = AutoModelForSeq2SeqLM.from_pretrained(model_dir)
    model = model.to(device).eval()
    if device == "cuda":
        print(f"[i] GPU: {torch.cuda.get_device_name(0)}")
    return tok, model


def _squeeze_fast(texts, tok, model, device, args):
    """Быстрый режим: рискованные предложения не отправляем модели,
    результат = оригинал. Пост-проверок нет."""
    import torch
    results = list(texts)
    safe_idx = []
    safe_texts = []
    for i, t in enumerate(texts):
        if _is_risky(t):
            continue
        safe_idx.append(i)
        safe_texts.append(t)
    n_skipped = len(texts) - len(safe_texts)
    n_accepted = 0

    if safe_texts:
        pre_texts = [preprocess_numbers(t) for t in safe_texts]
        inputs = tok(
            pre_texts,
            return_tensors="pt",
            max_length=args.max_in,
            truncation=True,
            padding=True,
        ).to(device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=args.max_out,
                num_beams=args.beams,
                no_repeat_ngram_size=3,
            )
        for src, ids, idx in zip(safe_texts, out, safe_idx):
            s = tok.decode(ids, skip_special_tokens=True).strip()
            s = postprocess_numbers(s)
            if s and s != EMPTY_MARK and len(s) < len(src):
                results[idx] = s
                n_accepted += 1

    if not hasattr(squeeze_batch, "_stats"):
        squeeze_batch._stats = {
            "in": 0, "out": 0, "rejected": 0, "rejected_sym": 0,
            "by_reason": {},
        }
    st = squeeze_batch._stats
    st["in"] = st.get("in", 0) + len(texts)
    st["out"] = st.get("out", 0) + n_accepted
    st["rejected"] = st.get("rejected", 0) + n_skipped
    st["rejected_sym"] = st.get("rejected_sym", 0)
    st["same_length"] = st.get("same_length", 0)
    br = st.setdefault("by_reason", {})
    br["fast_skip"] = br.get("fast_skip", 0) + n_skipped
    return results


def squeeze_batch(texts, tok, model, device, args):
    import torch
    if getattr(args, "fast", False):
        return _squeeze_fast(texts, tok, model, device, args)
    # пре-процессинг чисел
    pre_texts = [preprocess_numbers(t) for t in texts]

    inputs = tok(
        pre_texts,
        return_tensors="pt",
        max_length=args.max_in,
        truncation=True,
        padding=True,
    ).to(device)

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=args.max_out,
            num_beams=args.beams,
            no_repeat_ngram_size=3,
        )

    results = []
    n_rejected = 0
    n_rejected_sym = 0
    reject_stats = {"length": 0, "numbers": 0, "rare_symbol": 0, "pairs": 0,
                    "bad_phrase": 0, "paths": 0, "identifiers": 0, "negations": 0}
    n_same_length = 0

    # символы, которых нет в датасете — модель их галлюцинирует
    RARE_CHARS = set("₽≈≤≥±°¹²³éÉáÁíÍóÓúÚñÑçÇß"
                     "─│┌┐└┘├┤┬┴┼"
                     "═║╔╗╚╝╠╣╦╩╬"
                     "▀▄█░▒▓")
    # пары для проверки баланса
    PAIRS = [("«", "»"), ("(", ")"), ("[", "]"), ("{", "}"),
             ('"', '"'), ("`", "`")]

    BAD_PHRASES = [
        # грамматические ошибки
        "состав частью", "состав части",
        "находит в состав", "находиться в состав",
        "входит в состав частью",
        # галлюцинации-связки
        "можно сказать",
        "можно отметить",
        "стоит сказать",
        "учёные оценили", "ученые оценили",
        "исследователи установили",
        "было установлено",
        # подмена смысла
        "частная зависимость", "частной зависимости",
        # битые согласования
        "мозг построена", "мозг построен из",
    ]

    def _bad_phrase(dst):
        low = dst.lower()
        return any(bp in low for bp in BAD_PHRASES)

    def _rare_lost(src, dst):
        """Если в оригинале есть редкий символ, а в сжатии его нет — откат."""
        for ch in RARE_CHARS:
            if ch in src and ch not in dst:
                return True
        return False

    def _pairs_broken(src, dst):
        """Проверяем, что баланс парных символов не ухудшился."""
        for op, cl in PAIRS:
            if src.count(op) != dst.count(op):
                return True
            if src.count(cl) != dst.count(cl):
                return True
        return False

    _WIN_PATH_RE = _re.compile(r"[A-Za-z]:\\[^\s,;)]+")
    _IDENT_RE = _re.compile(r"\b\w+_\w+\b")
    _NEG_RE = _re.compile(r"\b(?:не|нет|ни)\b", _re.IGNORECASE)

    def _paths_lost(src, dst):
        """Windows-пути из src должны сохраниться в dst."""
        for m in _WIN_PATH_RE.finditer(src):
            if m.group(0) not in dst:
                return True
        return False

    def _identifiers_lost(src, dst):
        """Идентификаторы с подчёркиванием (_core, _ps, _actions) не теряем."""
        for m in _IDENT_RE.finditer(src):
            if m.group(0) not in dst:
                return True
        return False

    def _negations_lost(src, dst):
        """Число отрицаний в dst не должно быть меньше, чем в src."""
        src_n = len(_NEG_RE.findall(src))
        if src_n == 0:
            return False
        return len(_NEG_RE.findall(dst)) < src_n

    for src, ids in zip(texts, out):
        s = tok.decode(ids, skip_special_tokens=True).strip()
        # вернуть числа к исходному виду
        s = postprocess_numbers(s)
        reject_reason = None

        if not s or s == EMPTY_MARK:
            results.append(s)
            continue

        if len(s) >= len(src):
            reject_reason = "length"
        # len(s) < len(src) — единственный случай, когда принимаем правку
        elif not numbers_preserved(src, s):
            reject_reason = "numbers"
        elif _rare_lost(src, s):
            reject_reason = "rare_symbol"
        elif _pairs_broken(src, s):
            reject_reason = "pairs"
        elif _paths_lost(src, s):
            reject_reason = "paths"
        elif _identifiers_lost(src, s):
            reject_reason = "identifiers"
        elif _negations_lost(src, s):
            reject_reason = "negations"
        elif _bad_phrase(s):
            reject_reason = "bad_phrase"

        if not reject_reason and len(s) == len(src) and s != src:
            n_same_length += 1

        if reject_reason:
            results.append(src)
            n_rejected += 1
            if reject_reason in reject_stats:
                reject_stats[reject_reason] += 1
            if reject_reason == "rare_symbol":
                n_rejected_sym += 1
        else:
            results.append(s)

    if not hasattr(squeeze_batch, "_stats"):
        squeeze_batch._stats = {
            "in": 0, "out": 0, "rejected": 0, "rejected_sym": 0,
            "by_reason": {"length": 0, "numbers": 0, "rare_symbol": 0, "pairs": 0, "bad_phrase": 0, "paths": 0, "identifiers": 0, "negations": 0},
        }
    st = squeeze_batch._stats
    st.setdefault("in", 0)
    st.setdefault("out", 0)
    st.setdefault("rejected", 0)
    st.setdefault("rejected_sym", 0)
    st.setdefault("by_reason", {"length": 0, "numbers": 0, "rare_symbol": 0, "pairs": 0, "bad_phrase": 0, "paths": 0, "identifiers": 0, "negations": 0})
    st["in"] += int(inputs["input_ids"].numel())
    st["out"] += int(out.numel())
    st["rejected"] += n_rejected
    st["rejected_sym"] += n_rejected_sym
    st["same_length"] = st.get("same_length", 0) + n_same_length
    for k, v in reject_stats.items():
        st["by_reason"][k] = st["by_reason"].get(k, 0) + v
    return results


def assemble(items, keep_empty=False):
    """
    items: список (squeezed_text, sep_after).
    sep_after вставляется между этим предложением и следующим непустым.
    """
    parts = []
    pending_sep = ""   # whitespace, накопленный после последнего сжатого
    for squeezed, sep in items:
        is_empty_marker = (squeezed == EMPTY_MARK)
        is_dropped = (not squeezed or is_empty_marker)

        if is_dropped and not keep_empty:
            # предложение удалено — разделитель переходит к следующему
            pending_sep += sep
            continue

        if not parts:
            # первое непустое: leading whitespace отбрасываем
            parts.append("")
        else:
            parts.append(pending_sep)

        if is_empty_marker and keep_empty:
            parts.append(EMPTY_MARK)
        else:
            parts.append(squeezed)
        pending_sep = sep

    return "".join(parts)


def main():
    args = parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    if not src.exists():
        print(f"[!] Нет файла: {src}", file=sys.stderr)
        sys.exit(1)

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[i] Устройство: {device}")

    tok, model = load_model(args.model, device)

    text = src.read_text(encoding="utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    items = tokenize(text)
    to_squeeze = [s for s, _ in items if s]
    total = len(to_squeeze)
    print(f"[i] Предложений: {total}")

    if total == 0:
        print("[!] Предложений не нашлось")
        sys.exit(1)

    dedup_map = None
    if getattr(args, "dedup", False):
        seen = {}
        unique = []
        for s in to_squeeze:
            if s not in seen:
                seen[s] = len(unique)
                unique.append(s)
        dedup_map = seen
        to_model = unique
        print(f"[i] Уникальных: {len(unique)}")
    else:
        to_model = to_squeeze

    model_total = len(to_model)
    squeezed_unique = []
    batch_size = max(1, args.batch)
    t0 = time.time()
    _last_print = 0
    _raw = max(1, model_total // 10)
    _step = max(10, min(100, (_raw // 10) * 10))
    for i in range(0, model_total, batch_size):
        chunk = to_model[i:i+batch_size]
        outs = squeeze_batch(chunk, tok, model, device, args)
        squeezed_unique.extend(outs)
        done = min(i + batch_size, model_total)
        if done - _last_print >= _step or done == model_total:
            dt = time.time() - t0
            print(f"[i] {done}/{model_total} ({done/dt:.1f} предл/сек)")
            _last_print = done

    if dedup_map is not None:
        # Дедуп: одна копия на каждое уникальное предложение.
        seen_out = set()
        filtered = []
        for sent, sep in items:
            if not sent:
                filtered.append((sent, sep))
                continue
            if sent in seen_out:
                continue
            seen_out.add(sent)
            filtered.append((sent, sep))
        items = filtered
        squeezed_list = squeezed_unique
    else:
        squeezed_list = squeezed_unique

    # подставляем сжатия
    out_items = []
    idx = 0
    for sent, sep in items:
        if sent:
            out_items.append((squeezed_list[idx], sep))
            idx += 1
        else:
            out_items.append((sent, sep))

    result = assemble(out_items, keep_empty=args.keep_empty)

    # нормализация: убрать двойные пробелы и пробелы перед знаками
    import re as _re
    result = _re.sub(r"[ \t]{2,}", " ", result)
    result = _re.sub(r" +([,.:;!?])", r"\1", result)

    dst.write_text(result, encoding="utf-8")

    # размеры файлов
    src_bytes = src.stat().st_size
    dst_bytes = dst.stat().st_size
    src_kb = src_bytes / 1024
    dst_kb = dst_bytes / 1024
    saved = src_bytes - dst_bytes
    ratio = (dst_bytes / src_bytes * 100) if src_bytes else 0
    print(f"[i] Размер входного файла: {src_kb:.1f} КБ ({src_bytes} байт)")
    print(f"[i] Размер выходного:      {dst_kb:.1f} КБ ({dst_bytes} байт)")
    print(f"[i] Сжатие: -{saved} байт ({100 - ratio:.1f}%), итоговый размер = {ratio:.1f}% от исходного")

    dt = time.time() - t0
    st = getattr(squeeze_batch, "_stats", {"in": 0, "out": 0})
    print(f"[OK] Готово за {dt:.1f}с, {total/dt:.1f} предл/сек")
    print(f"[i] Отклонено сжатий: {st.get('rejected', 0)}")
    by = st.get("by_reason", {})
    if by:
        print(f"[i]   - length:      {by.get('length', 0)}")
        print(f"[i]   - numbers:     {by.get('numbers', 0)}")
        print(f"[i]   - rare_symbol: {by.get('rare_symbol', 0)}")
        print(f"[i]   - pairs:       {by.get('pairs', 0)}")
        print(f"[i]   - bad_phrase:  {by.get('bad_phrase', 0)}")
        print(f"[i]   - paths:       {by.get('paths', 0)}")
        print(f"[i]   - identifiers: {by.get('identifiers', 0)}")
        print(f"[i]   - negations:   {by.get('negations', 0)}")
        print(f"[i]   - fast_skip:   {by.get('fast_skip', 0)}")
    print(f"[i]   (перефразы той же длины: {st.get('same_length', 0)})")
    print(f"[i] Токенов вход : {st['in']}  ({st['in']/dt:.0f} ток/сек)")
    print(f"[i] Токенов выход: {st['out']}  ({st['out']/dt:.0f} ток/сек)")
    print(f"[OK] Результат: {dst}")


if __name__ == "__main__":
    main()