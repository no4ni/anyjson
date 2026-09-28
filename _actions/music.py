"""Синтез музыки: ноты -> WAV (полифония, волны, ADSR)."""
import re
import time
import wave
from pathlib import Path

import numpy as np

from _actions import register
from _config import MY_DIR


_LETTER_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d+)$")


def note_to_freq(name):
    m = _NOTE_RE.match(name.strip())
    if not m:
        raise ValueError("Плохое имя ноты: " + name)
    letter = m.group(1).upper()
    acc = m.group(2)
    octv = int(m.group(3))
    semi = _LETTER_SEMI[letter]
    if acc == "#":
        semi += 1
    elif acc == "b":
        semi -= 1
    midi = (octv + 1) * 12 + semi
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _wave(kind, t, freq):
    phase = 2.0 * np.pi * freq * t
    if kind == "sine":
        return np.sin(phase)
    if kind == "square":
        return np.sign(np.sin(phase))
    if kind == "triangle":
        return 2.0 / np.pi * np.arcsin(np.sin(phase))
    if kind == "saw":
        x = t * freq
        return 2.0 * (x - np.floor(x + 0.5))
    return np.sin(phase)


def _adsr(n, sr, a, d, s, r):
    a_s = int(a * sr)
    d_s = int(d * sr)
    r_s = int(r * sr)
    min_s = max(0, int(0.02 * n))
    total_adr = a_s + d_s + r_s
    if total_adr + min_s > n:
        k = (n - min_s) / total_adr if total_adr > 0 else 0.0
        a_s = int(a_s * k)
        d_s = int(d_s * k)
        r_s = int(r_s * k)
    s_s = n - a_s - d_s - r_s
    env = np.zeros(n, dtype=np.float64)
    pos = 0
    if a_s > 0:
        env[pos:pos + a_s] = np.linspace(0.0, 1.0, a_s, endpoint=False)
        pos += a_s
    if d_s > 0:
        env[pos:pos + d_s] = np.linspace(1.0, s, d_s, endpoint=False)
        pos += d_s
    if s_s > 0:
        env[pos:pos + s_s] = s
        pos += s_s
    if r_s > 0:
        env[pos:pos + r_s] = np.linspace(s, 0.0, r_s)
    return env


def _synth(freq, duration_sec, sr, kind, adsr):
    n = int(duration_sec * sr)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / sr
    w = _wave(kind, t, freq)
    env = _adsr(n, sr, adsr[0], adsr[1], adsr[2], adsr[3])
    return w * env


def _parse_compact(s):
    out = []
    for tok in s.split():
        if "/" in tok:
            name, _, dur_s = tok.rpartition("/")
            try:
                dur = float(dur_s)
            except ValueError:
                name, dur = tok, 1.0
        else:
            name, dur = tok, 1.0
        if name.startswith("[") and name.endswith("]"):
            notes = [x.strip() for x in name[1:-1].split("+") if x.strip()]
        elif name in ("-", "R", "r", "~", "_"):
            notes = None
        else:
            notes = [name]
        out.append({"нота": notes, "длительность": dur})
    return out


@register(["синтез_музыки", "сыграть", "мелодия", "music"])
def synth_music(args: dict) -> str:
    """Синтезировать мелодию в WAV (полифония, волны, ADSR).

    Параметры:
      ноты       — список {"нота": "C4" или ["C4","E4"], "длительность": <доли>}
                   или строка "C4/1 E4/1 [C4+E4+G4]/2 -/1"
      темп       — BPM (по умолч. 120)
      волна      — sine / square / triangle / saw (по умолч. sine)
      атака      — сек (по умолч. 0.01)
      затухание  — сек (по умолч. 0.10)
      поддержка  — уровень 0..1 (по умолч. 0.7)
      отпускание — сек (по умолч. 0.20)
      частота    — sample rate (по умолч. 44100)
      путь       — куда сохранить WAV (иначе MY_DIR/music_out/<TS>.wav)
    """
    raw = args.get("ноты") or args.get("notes")
    if not raw:
        raw = "C4/1 D4/1 E4/1 F4/1 G4/1 A4/1 B4/1 C5/2"

    if isinstance(raw, str):
        try:
            notes = _parse_compact(raw)
        except Exception as e:
            return "Ошибка разбора нот: " + str(e)
    elif isinstance(raw, list):
        if not raw:
            return "Ошибка: список нот пуст"
        if isinstance(raw[0], str):
            notes = [{"нота": n, "длительность": 1.0} for n in raw]
        else:
            notes = raw
    else:
        return "Ошибка: ноты должны быть строкой или списком"

    if not notes:
        return "Ошибка: список нот пуст"

    bpm = float(args.get("темп") or args.get("bpm") or 120.0)
    beat_sec = 60.0 / bpm if bpm > 0 else 0.5

    kind = str(args.get("волна") or "sine").strip().lower()
    if kind not in ("sine", "square", "triangle", "saw"):
        return "Ошибка: волна должна быть sine / square / triangle / saw"

    try:
        a = float(args.get("атака", 0.01))
        d = float(args.get("затухание", 0.10))
        s = float(args.get("поддержка", 0.7))
        r = float(args.get("отпускание", 0.20))
    except (TypeError, ValueError):
        return "Ошибка: атака/затухание/поддержка/отпускание — числа"

    s = max(0.0, min(1.0, s))

    sr = int(args.get("частота") or 44100)
    if sr < 8000:
        return "Ошибка: частота слишком низкая (мин 8000)"

    segments = []
    pos_sec = 0.0
    for i, item in enumerate(notes):
        if not isinstance(item, dict):
            return "Ошибка: нота #" + str(i) + " должна быть словарём"
        dur_beats = float(item.get("длительность") or item.get("duration") or 1.0)
        dur_sec = dur_beats * beat_sec
        n_notes = item.get("нота", item.get("note"))
        segments.append((pos_sec, dur_sec, n_notes))
        pos_sec += dur_sec

    total_sec = pos_sec
    total_n = int(total_sec * sr)
    if total_n <= 0:
        return "Ошибка: длительность мелодии 0"

    buf = np.zeros(total_n, dtype=np.float64)
    used_notes = 0
    for start_sec, dur_sec, n_notes in segments:
        if n_notes is None:
            continue
        if isinstance(n_notes, str):
            n_notes = [n_notes]
        if not isinstance(n_notes, list):
            continue
        start_n = int(start_sec * sr)
        for name in n_notes:
            try:
                freq = note_to_freq(str(name))
            except ValueError as e:
                return str(e)
            w = _synth(freq, dur_sec, sr, kind, (a, d, s, r))
            end_n = min(start_n + len(w), total_n)
            if end_n > start_n:
                buf[start_n:end_n] += w[:end_n - start_n]
                used_notes += 1

    if used_notes == 0:
        return "Ошибка: не найдено ни одной ноты"

    peak = float(np.max(np.abs(buf)))
    if peak > 0:
        buf = buf / peak * 0.9

    pcm = (buf * 32767.0).astype(np.int16)

    out_arg = args.get("путь")
    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "music_out" / (ts + ".wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
    except Exception as e:
        return "Ошибка записи WAV: " + str(e)

    size = out_path.stat().st_size
    dur = total_n / sr
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, "
            + str(round(dur, 2)) + " с, волна " + kind + ")")
