# -*- coding: utf-8 -*-
"""
Команда «подсказать_сокращения» — оценка того, что можно сжать в файле.

Запускает squeeze_report.py во временный файл, сравнивает оригинал
и результат по абзацам (блоки между пустыми строками), показывает
топ-N абзацев по сэкономленным байтам. Ничего не сохраняет.
"""

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _actions import register


BASE_DIR = Path(__file__).resolve().parent.parent
SQUEEZE_DIR = BASE_DIR / "side" / "external" / "squeeze"
SQUEEZE_REPORT = SQUEEZE_DIR / "squeeze_report.py"


def _fmt(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} МБ"
    if n >= 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n} Б"


def _paragraphs(text):
    """Разбить текст на абзацы (блоки между пустыми строками)."""
    blocks = re.split(r"\n[ \t]*\n", text)
    return [b.strip() for b in blocks if b.strip()]


@register(["подсказать_сокращения", "где_сжать",
           "кандидаты_сжатия", "сжатые_абзацы"])
def hint_squeeze(args: dict) -> str:
    """Команда "подсказать_сокращения".

    Прогоняет файл через локальную модель rut5-squeeze и показывает
    топ-N абзацев, которые сжимаются сильнее всего. Никуда не
    сохраняет — временный файл удаляется.

    Полезно для прозы: быстро оценить, есть ли что сокращать, и
    посмотреть примеры перефразирования. На технической документации
    (JSON, код, пути) модель врёт — не доверяй.

    Параметры:
      • вход       — путь к исходному файлу (обязательно)
      • топ        — сколько абзацев показать (по умолчанию 10)
      • мин_длина  — минимальная экономия в байтах для показа
                     (по умолчанию 5)
    """
    inp = args.get("вход") or args.get("путь") or args.get("файл")
    try:
        top = int(args.get("топ") or args.get("количество") or 10)
    except (TypeError, ValueError):
        top = 10
    if top < 1:
        top = 1
    if top > 100:
        top = 100
    try:
        min_delta = int(args.get("мин_длина", 5) or 5)
    except (TypeError, ValueError):
        min_delta = 5
    if not inp:
        return "❌ подсказать_сокращения: не указан вход"

    src = Path(inp)
    if not src.is_file():
        return f"❌ подсказать_сокращения: нет файла {src}"
    if not SQUEEZE_REPORT.is_file():
        return f"❌ подсказать_сокращения: не найден {SQUEEZE_REPORT}"

    src_text = src.read_text(encoding="utf-8", errors="replace")
    src_size = len(src_text.encode("utf-8"))

    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="anyjson_hint_") as td:
        dst = Path(td) / "out.txt"

        cmd = [
            sys.executable, "-u", str(SQUEEZE_REPORT),
            str(src), str(dst),
            "--beams", "8",
            "--no-report",
        ]

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
            return f"❌ подсказать_сокращения: не удалось запустить — {e}"

        try:
            for line in proc.stdout:
                buffer.append(line)
                sys.stdout.write(line)
                sys.stdout.flush()
            proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            return "❌ подсказать_сокращения: таймаут 1 час"
        except KeyboardInterrupt:
            try:
                proc.kill()
            except Exception:
                pass
            return "⛔ подсказать_сокращения: прервано"

        if proc.returncode != 0:
            captured = "".join(buffer).strip()
            head = f"❌ подсказать_сокращения: код {proc.returncode}"
            return head + ("\n\n" + captured if captured else "")
        if not dst.is_file():
            return "❌ подсказать_сокращения: модель не создала результат"

        dst_text = dst.read_text(encoding="utf-8", errors="replace")
        dst_size = len(dst_text.encode("utf-8"))

    dt = time.time() - t0

    src_paras = _paragraphs(src_text)
    dst_paras = _paragraphs(dst_text)

    # Простое выравнивание по индексу: если число абзацев одинаково —
    # значит модель переписала каждый на месте. Если разное — гоняем
    # difflib. Но в обоих случаях показываем только «один абзац →
    # один абзац», не склеиваем разные.
    pairs = []

    if len(src_paras) == len(dst_paras):
        for a, b in zip(src_paras, dst_paras):
            if a == b:
                continue
            delta = len(a.encode("utf-8")) - len(b.encode("utf-8"))
            if delta >= min_delta:
                pairs.append((delta, a, b))
    else:
        import difflib
        sm = difflib.SequenceMatcher(None, src_paras, dst_paras,
                                     autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "replace":
                continue
            if (i2 - i1) == 1 and (j2 - j1) == 1:
                a = src_paras[i1]
                b = dst_paras[j1]
                if a == b:
                    continue
                delta = len(a.encode("utf-8")) - len(b.encode("utf-8"))
                if delta >= min_delta:
                    pairs.append((delta, a, b))

    pairs.sort(key=lambda p: -p[0])

    saved = src_size - dst_size
    pct = (saved / src_size * 100) if src_size else 0.0

    lines = [
        f"📉 Подсказка сокращений: {src.name}",
        f"   {_fmt(src_size)} → можно сжать до ~{_fmt(dst_size)} "
        f"(−{_fmt(saved)}, {pct:.1f}%)  {dt:.1f} сек",
        f"   Абзацев с сокращением ≥{min_delta} Б: {len(pairs)}",
        "",
    ]

    if not pairs:
        lines.append("Ничего заметно не сжимается — текст уже плотный.")
        return "\n".join(lines)

    n_show = min(top, len(pairs))
    for n, (delta, a, b) in enumerate(pairs[:n_show], 1):
        lines.append(f"{n}. −{delta} Б")
        lines.append(f"   Оригинал: {a}")
        lines.append(f"   Например: {b}")
        lines.append("")

    if len(pairs) > n_show:
        lines.append(f"(показано {n_show} из {len(pairs)})")

    return "\n".join(lines)