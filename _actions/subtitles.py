"""Субтитры softsub: создать .srt/.ass рядом с видео."""
import re
import time
from pathlib import Path

from _actions import register
from _config import MY_DIR


_TS_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+)[,.](\d+)$")


def _to_sec(v):
    """Секунды из: числа / строки SRT / [от, до]."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        m = _TS_RE.match(s)
        if m:
            h = int(m.group(1) or 0)
            mi = int(m.group(2))
            se = int(m.group(3))
            ms = int(m.group(4).ljust(3, "0")[:3])
            return h * 3600 + mi * 60 + se + ms / 1000.0
        return float(s.replace(",", "."))
    raise ValueError("не число: " + repr(v))


def _srt_ts(sec):
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return "{:02d}:{:02d}:{:02d},{:03d}".format(h, m, s, ms)


def _ass_ts(sec):
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs = 99
    return "{}:{:02d}:{:02d}.{:02d}".format(h, m, s, cs)


def _parse_item(item):
    """Из dict/строки -> (start, end, text)."""
    if isinstance(item, str):
        return None
    if not isinstance(item, dict):
        return None
    text = item.get("текст") or item.get("text") or ""
    if not text:
        return None
    if "от" in item and "до" in item:
        start = _to_sec(item["от"])
        end = _to_sec(item["до"])
    elif "тайминг" in item:
        t = item["тайминг"]
        if isinstance(t, (list, tuple)) and len(t) >= 2:
            start = _to_sec(t[0])
            end = _to_sec(t[1])
        else:
            return None
    else:
        return None
    if end <= start:
        end = start + 0.5
    return (start, end, str(text))


def _write_srt(items, path):
    lines = []
    for i, (s, e, t) in enumerate(items, 1):
        lines.append(str(i))
        lines.append(_srt_ts(s) + " --> " + _srt_ts(e))
        for ln in t.split("\n"):
            lines.append(ln)
        lines.append("")
    path.write_text(chr(10).join(lines), encoding="utf-8")


def _write_ass(items, path):
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,60,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    for s, e, t in items:
        t2 = t.replace("\n", "\\N")
        events.append("Dialogue: 0," + _ass_ts(s) + "," + _ass_ts(e) + ",Default,,0,0,0,," + t2)
    path.write_text(chr(10).join(head + events), encoding="utf-8")


@register(["добавить_субтитры", "создать_субтитры", "субтитры"])
def add_subtitles(args: dict) -> str:
    """Создать softsub (.srt/.ass) рядом с видео.

    Параметры:
      видео   — путь к видеофайлу (обязательно для имени .srt)
      набор    — список: [{"текст": "...", "от": 0.0, "до": 3.0}, ...]
      текст    — одиночный субтитр (если нет набора)
      тайминг  — [от, до] для одиночного
      путь     — куда положить (по умолч. рядом с видео, имя видео + .srt)
      формат   — srt (по умолч.) / ass
    """
    args = args or {}
    video = args.get("видео") or args.get("video")
    out_arg = args.get("путь")
    fmt = str(args.get("формат") or "srt").strip().lower()
    if fmt not in ("srt", "ass"):
        return "Ошибка: формат должен быть srt или ass"

    raw = args.get("набор")
    if raw is None:
        # одиночный
        if args.get("текст") is not None and args.get("тайминг") is not None:
            raw = [{"текст": args["текст"], "тайминг": args["тайминг"]}]
        else:
            return "Ошибка: нужен набор или пара текст+тайминг"

    if not isinstance(raw, list) or not raw:
        return "Ошибка: набор должен быть непустым списком"

    items = []
    for i, it in enumerate(raw):
        try:
            p = _parse_item(it)
        except (ValueError, TypeError) as e:
            return "Ошибка в элементе #" + str(i) + ": " + str(e)
        if p is None:
            return "Ошибка: элемент #" + str(i) + " без текста/тайминга"
        items.append(p)

    items.sort(key=lambda x: x[0])

    if out_arg:
        out_path = Path(out_arg)
    elif video:
        vp = Path(video)
        out_path = vp.with_suffix("." + fmt)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "subs_out" / (ts + "." + fmt)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "srt":
            _write_srt(items, out_path)
        else:
            _write_ass(items, out_path)
    except Exception as e:
        return "Ошибка записи: " + str(e)

    size = out_path.stat().st_size
    return ("Готово: " + str(out_path)
            + " (" + str(size) + " байт, " + str(len(items))
            + " субтитров, " + fmt + ")")
