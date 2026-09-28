"""Анализ метаданных аудио и видео (через ffprobe)."""
import json
import subprocess
from pathlib import Path

from _actions import register
from _config import FFPROBE_EXE


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".aiff", ".ape")
_VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp")


def _fmt_size(n):
    if n is None:
        return "?"
    n = int(n)
    if n > 1024 * 1024 * 1024:
        return f"{n / (1024**3):.2f} ГБ"
    if n > 1024 * 1024:
        return f"{n / (1024**2):.2f} МБ"
    if n > 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n} Б"


def _fmt_dur(sec):
    if sec is None:
        return "?"
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return "?"
    if sec < 60:
        return f"{sec:.2f} с"
    m, s = divmod(int(sec), 60)
    if m < 60:
        return f"{m} мин {s} с"
    h, m = divmod(m, 60)
    return f"{h} ч {m} мин {s} с"


def _fmt_bitrate(bps):
    if bps is None:
        return "?"
    try:
        bps = int(bps)
    except (TypeError, ValueError):
        return "?"
    if bps > 1_000_000:
        return f"{bps / 1_000_000:.2f} Мбит/с"
    if bps > 1000:
        return f"{bps / 1000:.0f} Кбит/с"
    return f"{bps} бит/с"


def _ffprobe(path):
    if not FFPROBE_EXE.is_file():
        raise FileNotFoundError("ffprobe не найден: " + str(FFPROBE_EXE))
    cmd = [
        str(FFPROBE_EXE),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=30, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("ffprobe код " + str(r.returncode) + ": " + (r.stderr or "")[:300])
    return json.loads(r.stdout or "{}")


def _analyze(path, is_video):
    src = Path(path).expanduser()
    if not src.is_file():
        return "❌ Файл не найден: " + str(src)

    try:
        size_bytes = src.stat().st_size
    except Exception:
        size_bytes = 0

    if is_video and src.suffix.lower() not in _VIDEO_EXTS:
        return "❌ Не похоже на видео: " + src.suffix
    if not is_video and src.suffix.lower() not in _AUDIO_EXTS:
        return "❌ Не похоже на аудио: " + src.suffix

    try:
        data = _ffprobe(src)
    except Exception as e:
        return "❌ ffprobe: " + str(e)

    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    vstream = None
    astreams = []
    sstreams = []
    for st in streams:
        ct = (st.get("codec_type") or "").lower()
        if ct == "video" and vstream is None:
            vstream = st
        elif ct == "audio":
            astreams.append(st)
        elif ct == "subtitle":
            sstreams.append(st)

    head = ("🎬 " if is_video else "🎵 ") + src.name
    lines = [head]
    lines.append("   Размер: " + _fmt_size(size_bytes))

    _dur = fmt.get("duration")
    if _dur:
        lines.append("   Длительность: " + _fmt_dur(_dur))
    _br = fmt.get("bit_rate")
    if _br:
        lines.append("   Битрейт контейнера: " + _fmt_bitrate(_br))

    _fmt_name = fmt.get("format_name") or "?"
    _fmt_long = fmt.get("format_long_name") or ""
    _fmt_str = _fmt_name + ((" (" + _fmt_long + ")") if _fmt_long else "")
    lines.append("   Контейнер: " + _fmt_str)

    if is_video and vstream:
        codec = vstream.get("codec_name", "?")
        profile = vstream.get("profile")
        pix_fmt = vstream.get("pix_fmt", "?")
        w = vstream.get("width")
        h = vstream.get("height")
        _fps_raw = vstream.get("r_frame_rate") or "0/0"
        try:
            num, den = _fps_raw.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0.0
        except Exception:
            fps = 0.0
        lines.append("   Видео-кодек: " + codec
                     + ((" (" + profile + ")") if profile else ""))
        if w and h:
            lines.append("   Разрешение: " + str(w) + "×" + str(h))
        if fps:
            lines.append("   FPS: " + f"{fps:.3f}".rstrip("0").rstrip("."))
        if pix_fmt:
            lines.append("   Цвет: " + pix_fmt)
        _vbr = vstream.get("bit_rate")
        if _vbr:
            lines.append("   Битрейт видео: " + _fmt_bitrate(_vbr))

    if astreams:
        lines.append("   Аудио-дорожек: " + str(len(astreams)))
        for i, a in enumerate(astreams, 1):
            codec = a.get("codec_name", "?")
            sr = a.get("sample_rate", "?")
            ch = a.get("channels", "?")
            _abr = a.get("bit_rate")
            label = "     " + str(i) + ": " + codec + ", " + str(sr) + " Гц, " + str(ch) + " кан."
            if _abr:
                label += ", " + _fmt_bitrate(_abr)
            lang = (a.get("tags") or {}).get("language")
            if lang:
                label += ", язык " + lang
            lines.append(label)

    if is_video and sstreams:
        lines.append("   Субтитров: " + str(len(sstreams)))
        for i, sub in enumerate(sstreams, 1):
            lang = (sub.get("tags") or {}).get("language", "?")
            title = (sub.get("tags") or {}).get("title", "")
            label = "     " + str(i) + ": " + str(lang)
            if title:
                label += " (" + title + ")"
            lines.append(label)

    tags = fmt.get("tags") or {}
    _nice = {"title": "Название", "artist": "Исполнитель", "album": "Альбом",
             "date": "Дата", "genre": "Жанр", "comment": "Комментарий",
             "encoder": "Кодировщик", "creation_time": "Создан"}
    _got = []
    for k, v in tags.items():
        kl = k.lower()
        if kl in _nice:
            _got.append("     " + _nice[kl] + ": " + str(v))
    if _got:
        lines.append("   Теги:")
        lines.extend(_got)

    _p = str(src.resolve()).replace(chr(92), "/")
    cmd_name = "анализ_видео" if is_video else "анализ_аудио"
    lines.append("")
    lines.append('💡 Посмотреть кадр: {"кадр+субтитр": {"путь": "' + _p + '", "секунда": 0}}' if is_video else '💡 Спектрограмма: {"слушать_аудио": {"путь": "' + _p + '", "секунда": 0}}')
    return "\n".join(lines)


@register(["анализ_видео", "видео_инфо", "инфо_видео"])
def analyze_video(args: dict) -> str:
    """Сведения о видеофайле: контейнер, кодеки, разрешение, fps, дорожки, теги.

    Что выводит:
      • размер и длительность
      • контейнер и общий битрейт
      • видео: кодек, профиль, разрешение, fps, цвет, битрейт
      • аудио: список дорожек (кодек, частота, каналы, битрейт, язык)
      • субтитры: список дорожек с языками
      • теги: название, исполнитель, дата, создан, кодировщик

    Поддерживаемые форматы:
      .mp4 .avi .mov .mkv .webm .m4v .flv .wmv .mpg .mpeg .ts .m2ts .3gp

    Параметры:
      путь — путь к видеофайлу (обязательный)

    Примеры:
      {"анализ_видео": {"путь": "C:/rpg/out.mp4"}}
      {"видео_инфо": {"путь": "D:/video/movie.mkv"}}

    Посмотреть кадр: {"кадр+субтитр": {"путь": "...", "секунда": 0}}
    """
    args = args or {}
    p = args.get("путь") or args.get("файл") or ""
    if not p:
        return 'Укажи путь: {"анализ_видео": {"путь": "C:/rpg/out.mp4"}}'
    return _analyze(p, is_video=True)


@register(["анализ_аудио", "аудио_инфо", "инфо_аудио"])
def analyze_audio(args: dict) -> str:
    """Сведения об аудиофайле: контейнер, кодек, частота, каналы, битрейт, теги.

    Что выводит:
      • размер и длительность
      • контейнер и общий битрейт
      • аудио: список дорожек (кодек, частота, каналы, битрейт, язык)
      • теги: название, исполнитель, альбом, дата, жанр, кодировщик

    Поддерживаемые форматы:
      .wav .mp3 .flac .ogg .m4a .aac .opus .wma .aiff .ape

    Параметры:
      путь — путь к аудиофайлу (обязательный)

    Примеры:
      {"анализ_аудио": {"путь": "C:/rpg/voice.wav"}}
      {"аудио_инфо": {"путь": "D:/music/track.flac"}}

    Спектрограмма: {"слушать_аудио": {"путь": "...", "секунда": 0}}
    """
    args = args or {}
    p = args.get("путь") or args.get("файл") or ""
    if not p:
        return 'Укажи путь: {"анализ_аудио": {"путь": "C:/rpg/voice.wav"}}'
    return _analyze(p, is_video=False)