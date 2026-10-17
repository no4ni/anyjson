# -*- coding: utf-8 -*-
"""
Запускает squeeze_document.py, транслирует прогресс в реальном времени,
затем показывает:
  - процент сжатия по байтам
  - первые 10 сжатых абзацев

    py squeeze_report.py input.txt output.txt --beams 8

Таймаут на весь процесс (жёсткий, wall-clock):
  beams == 1  → без лимита (быстрый режим)
  beams == 2  → 60 сек
  beams >= 3  → 30 сек
"""

import argparse
import io
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# принудительный UTF-8 для нашего вывода
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
SQUEEZE = HERE / "squeeze_document.py"

SKIP_PREFIXES = (
    "[i] Размер входного файла:",
    "[i] Размер выходного:",
    "[i] Сжатие:",
    "[i] Отклонено сжатий:",
    "[i]   - ",
    "[i]   (перефразы",
    "[i] Токенов ",
    "[OK] Результат:",
    "[OK] Готово за",
    "[i] Устройство:",
    "[i] Загружаю токенизатор:",
    "[i] Загружаю модель",
    "[i] GPU:",
    "[i] Предложений:",
    "Loading weights:",
    "[transformers]",
)


def _timeout_for(beams: int) -> int:
    """Wall-clock лимит на весь процесс. 0 = без лимита."""
    if beams <= 1:
        return 0
    if beams == 2:
        return 60
    return 30


def _hard_kill(p):
    """Жёстко убить процесс и всех его детей. На Windows — taskkill /T /F,
    чтобы снять дерево и освободить CUDA-контекст torch."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            p.kill()
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


_SPEED_TABLE = {
    (1, False): 48, (1, True): 60,
    (2, False): 30, (2, True): 32,
    (3, False): 22, (3, True): 25,
    (4, False): 15, (4, True): 17,
    (5, False): 12, (5, True): 14,
    (6, False): 10, (6, True): 12,
    (7, False): 9,  (7, True): 10,
    (8, False): 9,  (8, True): 9,
}
_LOAD_TIME = 13.0


def _count_sentences(path):
    """Оценить число предложений в файле."""
    try:
        size = Path(path).stat().st_size
    except OSError:
        return 0
    if size > 10 * 1024 * 1024:
        return max(1, size // 66)
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    parts = re.split(r"(?<=[.!?…])[ \t\n]+|\n+", text)
    return sum(1 for p in parts if p.strip())


def _count_unique_sentences(path):
    """Число уникальных предложений (для оценки при дедупликации)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    parts = re.split(r"(?<=[.!?…])[ \t\n]+|\n+", text)
    seen = set()
    for p in parts:
        s = p.strip()
        if s:
            seen.add(s)
    return len(seen)


def _estimate_seconds(n, quality, fast):
    """Оценка времени работы squeeze_document.py, сек (с загрузкой)."""
    speed = _SPEED_TABLE.get((quality, fast))
    if speed is None:
        for q in range(quality, 0, -1):
            speed = _SPEED_TABLE.get((q, fast))
            if speed is not None:
                break
        if speed is None:
            speed = 15
    return _LOAD_TIME + n / speed


def _auto_tune(n, quality, fast, timeout_for):
    """Подобрать (quality, fast), чтобы уложиться в таймаут.
    Сначала включаем fast, потом понижаем качество."""
    notes = []
    q, f = quality, fast
    while True:
        timeout = timeout_for(q)
        if timeout == 0:
            break
        est = _estimate_seconds(n, q, f)
        if est <= timeout:
            break
        if not f:
            f = True
            notes.append("включён быстрый режим")
            continue
        if q <= 1:
            break
        q -= 1
        notes.append("качество -> " + str(q))
    return q, f, "; ".join(notes)


def run_squeeze(inp, out, beams, fast=False, dedup=False):
    cmd = [
        sys.executable, "-u", str(SQUEEZE),
        str(inp), str(out),
        "--beams", str(beams),
        "--print",
    ]
    if fast:
        cmd.append("--fast")
    if dedup:
        cmd.append("--dedup")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    t_start = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    done = threading.Event()

    def _reader():
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                stripped = line.lstrip()
                if any(stripped.startswith(p) for p in SKIP_PREFIXES):
                    continue
                print(line, flush=True)
        finally:
            done.set()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    try:
        while not done.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        _hard_kill(proc)
        t.join(timeout=5)
        sys.exit(130)

    t.join(timeout=5)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _hard_kill(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    if proc.returncode != 0:
        print(f"[!] squeeze_document.py упал с кодом {proc.returncode}",
              file=sys.stderr)
        sys.exit(proc.returncode)


def report(src_path, dst_path):
    src = Path(src_path)
    dst = Path(dst_path)

    sb = src.stat().st_size
    db = dst.stat().st_size
    saved = sb - db
    pct = (saved / sb * 100) if sb else 0

    print()
    print("=" * 80)
    print(f"РАЗМЕР:   {sb} -> {db} байт")
    print(f"СЖАТИЕ:   -{saved} байт  ({pct:.1f}%)")
    print(f"ИТОГО:    {100 - pct:.1f}% от исходного")
    print("=" * 80)
    print()

    import difflib

    src_lines = src.read_text(encoding="utf-8", errors="replace").split("\n")
    dst_lines = dst.read_text(encoding="utf-8", errors="replace").split("\n")

    compressed = []
    sm = difflib.SequenceMatcher(None, src_lines, dst_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        a_block = src_lines[i1:i2]
        b_block = dst_lines[j1:j2]
        if len(a_block) == len(b_block):
            for k, (a, b) in enumerate(zip(a_block, b_block)):
                a_s, b_s = a.strip(), b.strip()
                if not a_s or not b_s or a_s == b_s:
                    continue
                delta = len(a_s) - len(b_s)
                if delta >= 5:
                    compressed.append((i1 + k + 1, a_s, b_s, delta))
        else:
            a_join = " ".join(x.strip() for x in a_block if x.strip())
            b_join = " ".join(x.strip() for x in b_block if x.strip())
            if a_join and b_join and a_join != b_join:
                delta = len(a_join) - len(b_join)
                if delta >= 5:
                    compressed.append((i1 + 1, a_join, b_join, delta))

    print(f"Сжатых строк: {len(compressed)}")
    print()

    if not compressed:
        print("Ничего не сжалось.")
        return

    n_show = min(10, len(compressed))
    print(f"Первые {n_show} сжатых абзацев:")
    print()

    for n, (ln, a, b, d) in enumerate(compressed[:n_show], 1):
        print(f"--- {n}. строка {ln}  (экономия {d} байт) ---")
        print(f"IN :  {a[:300]}{'...' if len(a) > 300 else ''}")
        print(f"OUT:  {b[:300]}{'...' if len(b) > 300 else ''}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--beams", type=int, default=4)
    ap.add_argument("--fast", action="store_true",
                    help="быстрый режим: рискованные предложения "
                         "не отправляются модели")
    ap.add_argument("--no-auto-tune", action="store_true",
                    help="отключить авто-подбор качества под таймаут")
    ap.add_argument("--debug", action="store_true",
                    help="печатать выбор авто-подбора качества")
    ap.add_argument("--no-report", action="store_true",
                    help="не печатать финальный отчёт (для итераций)")
    args = ap.parse_args()

    if not Path(args.input).exists():
        print(f"[!] Нет файла: {args.input}", file=sys.stderr)
        sys.exit(1)

    args.dedup = False
    if not args.no_auto_tune:
        n = _count_sentences(args.input)
        if n > 0:
            orig_q, orig_f = args.beams, args.fast
            q, f, note = _auto_tune(n, orig_q, orig_f, _timeout_for)
            dedup = False
            n_eff = n
            n_uniq = None
            if f:
                n_uniq = _count_unique_sentences(args.input)
                if 0 < n_uniq < n:
                    q2, f2, _ = _auto_tune(n_uniq, orig_q, orig_f, _timeout_for)
                    q, f = q2, f2
                    dedup = True
                    n_eff = n_uniq
            est = _estimate_seconds(n_eff, q, f)
            if args.debug:
                extra = f", уникальных={n_uniq}" if n_uniq is not None else ""
                print(f"[i] авто: beams={q}, fast={f}, "
                      f"предложений={n}{extra}, оценка ~{est:.0f} сек",
                      flush=True)
            else:
                print(f"[i] оценка ~{est:.0f} сек", flush=True)
            args.beams = q
            args.fast = f
            args.dedup = dedup

    run_squeeze(args.input, args.output, args.beams, fast=args.fast, dedup=args.dedup)
    if not args.no_report:
        report(args.input, args.output)


if __name__ == "__main__":
    main()