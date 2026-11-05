# -*- coding: utf-8 -*-
"""
Команда «сжать_текст» — обёртка над side/external/squeeze/squeeze_report.py.

Скрипты squeeze_report.py и squeeze_document.py — копия из
E:\\Jericho\\reducersize\\. Модель rut5-squeeze лежит рядом
(side/external/squeeze/rut5-squeeze/).

Вывод squeeze_report.py транслируется в консоль anyjson построчно
(subprocess.Popen, bufsize=1), параллельно копится в буфер и
возвращается из команды.

При заданном «до» — итеративное сжатие: повторяем, пока файл больше
цели и сжатие за итерацию не упало ниже 1%.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

from _actions import register


BASE_DIR = Path(__file__).resolve().parent.parent
SQUEEZE_DIR = BASE_DIR / "side" / "external" / "squeeze"
SQUEEZE_REPORT = SQUEEZE_DIR / "squeeze_report.py"


_SIZE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([кkмmКKМM]?)\s*[бbБB]?$")
_PROGRESS_RE = re.compile(r"^\[i\] \d+/\d+ \([\d.]+ предл/сек\)$")


def _parse_size(s):
    """'100КБ' → 102400, '1МБ' → 1048576, '50000' → 50000.
    Принимает числа, К/K, М/M, опционально Б/B в конце.
    Возвращает int (байт) или None."""
    if isinstance(s, (int, float)):
        return int(s)
    t = str(s).strip().replace(" ", "")
    if not t:
        return None
    m = _SIZE_RE.match(t)
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    mult = 1
    if unit in ("к", "k"):
        mult = 1024
    elif unit in ("м", "m"):
        mult = 1024 * 1024
    return int(n * mult)


def _fmt(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} МБ"
    if n >= 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n} Б"


def _run_once(inp, out, debug, no_report):
    """Один прогон squeeze_report.py. Возвращает (ok, captured, dt).
    Стримит stdout в консоль всегда."""
    cmd = [
        sys.executable, "-u", str(SQUEEZE_REPORT),
        str(inp), str(out),
        "--beams", "8",
    ]
    if debug:
        cmd.append("--debug")
    if no_report:
        cmd.append("--no-report")

    t0 = time.time()
    buffer = []
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(SQUEEZE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        return False, f"❌ не удалось запустить — {e}", 0.0

    try:
        for line in proc.stdout:
            if not _PROGRESS_RE.match(line.strip()):
                buffer.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait(timeout=60)
    except KeyboardInterrupt:
        try:
            proc.kill()
        except Exception:
            pass
        return False, "⛔ сжать_текст: прервано пользователем", time.time() - t0
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return False, "❌ сжать_текст: не завершился после stdout", time.time() - t0

    dt = time.time() - t0
    ok = (proc.returncode == 0)
    return ok, "".join(buffer).strip(), dt


@register(["сжать_текст", "сжать_документ"])
def squeeze_text(args: dict) -> str:
    """Команда "сжать_текст".

    Сжимает текстовый документ локальной моделью rut5-squeeze (T5).
    Каждое предложение перефразируется короче; разделители (пробелы,
    переводы строк) сохраняются. Числа, редкие символы, парные
    скобки/кавычки проверяются — плохие сжатия откатываются.

    Прогресс идёт в консоль в реальном времени, полный вывод
    возвращается из команды.

    Параметры:
      • вход  — путь к исходному файлу (обязательно)
      • выход — путь к результату (обязательно)
      • до    — целевой размер файла ("100КБ", "1МБ", 50000).
                Итеративно сжимает, пока файл не станет меньше
                или пока сжатие за итерацию не упадёт ниже 1%.
      • отладка — true: показать выбор авто-подбора качества
    """
    inp = args.get("вход") or args.get("путь") or args.get("файл")
    out = args.get("выход") or args.get("сохранить") or args.get("куда")
    debug = bool(args.get("отладка", False))
    do_raw = args.get("до")

    if not inp:
        return "❌ сжать_текст: не указан вход"
    if not out:
        return "❌ сжать_текст: не указан выход"

    src = Path(inp)
    dst = Path(out)

    if not src.is_file():
        return f"❌ сжать_текст: нет файла {src}"
    if not SQUEEZE_REPORT.is_file():
        return f"❌ сжать_текст: не найден {SQUEEZE_REPORT}"

    target = None
    if do_raw is not None:
        target = _parse_size(do_raw)
        if target is None:
            return f"❌ сжать_текст: не разобрать «до» = {do_raw!r} (пример: 100КБ)"
        if target <= 0:
            return f"❌ сжать_текст: «до» должно быть > 0, получено {target}"
        if src.resolve() == dst.resolve():
            return ("❌ сжать_текст: при заданном «до» вход и выход "
                    "должны различаться")

    dst.parent.mkdir(parents=True, exist_ok=True)

    if target is None:
        # --- одиночный прогон ---
        ok, captured, dt = _run_once(src, dst, debug, no_report=False)
        if not ok:
            return captured or "❌ сжать_текст: ошибка"
        if not dst.is_file():
            head = f"❌ сжать_текст: файл {dst} не создан ({dt:.1f} сек)"
            return head + ("\n\n" + captured if captured else "")
        src_b = src.stat().st_size
        dst_b = dst.stat().st_size
        saved = src_b - dst_b
        pct = (saved / src_b * 100) if src_b else 0.0
        head = (
            f"✅ сжать_текст: {src.name} → {dst.name}\n"
            f"   {src_b} → {dst_b} байт  (−{saved}, {pct:.1f}%)  {dt:.1f} сек"
        )
        if captured:
            return head + "\n\n" + captured
        return head

    # --- итеративный режим ---
    orig_size = src.stat().st_size
    prev_size = orig_size
    total_t0 = time.time()
    all_captured = []
    max_iter = 20
    iteration = 0
    stop_reason = ""

    sys.stdout.write(
        f"📉 Итеративное сжатие: {_fmt(orig_size)} → "
        f"цель {_fmt(target)}\n"
    )
    sys.stdout.flush()
    all_captured.append(
        f"📉 Итеративное сжатие: {_fmt(orig_size)} → цель {_fmt(target)}"
    )

    while True:
        iteration += 1
        if iteration > max_iter:
            stop_reason = f"достигнут предел {max_iter} итераций"
            break

        in_path = src if iteration == 1 else dst
        ok, captured, dt = _run_once(
            in_path, dst, debug, no_report=True
        )
        all_captured.append(captured)
        if not ok:
            stop_reason = "ошибка на итерации"
            break
        if not dst.is_file():
            stop_reason = "файл не создан"
            break

        cur_size = dst.stat().st_size
        saved = prev_size - cur_size
        comp_pct = (saved / prev_size * 100) if prev_size else 0.0

        line = (f"[i] итерация {iteration}: "
                f"{_fmt(prev_size)} → {_fmt(cur_size)} "
                f"(−{comp_pct:.2f}%, {dt:.1f} сек)")
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        all_captured.append(line)

        if cur_size <= target:
            stop_reason = f"цель {_fmt(target)} достигнута"
            break
        if comp_pct < 1.0:
            stop_reason = f"сжатие < 1% ({comp_pct:.2f}%), остановка"
            break

        prev_size = cur_size

    total_dt = time.time() - total_t0
    final_size = dst.stat().st_size if dst.is_file() else prev_size
    total_saved = orig_size - final_size
    total_pct = (total_saved / orig_size * 100) if orig_size else 0.0

    head_lines = [
        f"✅ сжать_текст: {src.name} → {dst.name}",
        f"   {_fmt(orig_size)} → {_fmt(final_size)}  "
        f"(−{_fmt(total_saved)}, {total_pct:.1f}%)  "
        f"итераций: {iteration}  {total_dt:.1f} сек",
        f"   {stop_reason}",
    ]
    head = "\n".join(head_lines)

    tail = "\n".join(l for l in all_captured if l)
    if tail:
        return head + "\n\n" + tail
    return head