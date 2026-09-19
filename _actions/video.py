"""Видео: смотреть кадр из видео + буфер, скачать с ютуба, список реплик."""
import subprocess
import sys
from pathlib import Path

from _actions import register

from _config import (
    WATCHTOGETHER_CLI as CLI,
    VIDEOHEAR_CLI as CLI2,
    SPECTRO_CLI as CLI3,
)


def _run_cli(args_list, timeout=120):
    if not CLI.exists():
        return f"❌ Скрипт не найден: {CLI}"
    cmd = [sys.executable, str(CLI)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    # если скрипт положил картинку в буфер — маркируем,
    # чтобы _command.py НЕ перезаписал буфер текстом
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text


def _run_cli2(args_list, timeout=120):
    if not CLI2.exists():
        return f"❌ Скрипт не найден: {CLI2}"
    cmd = [sys.executable, str(CLI2)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text


def _run_cli3(args_list, timeout=600):
    """Отдельный wrapper для spectro_cli (первый запуск может быть долгим)."""
    if not CLI3.exists():
        return f"❌ Скрипт не найден: {CLI3}"
    cmd = [sys.executable, str(CLI3)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с (первый запуск может считать спектрограмму долго)"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text


@register("смотреть_видео")
def watch_video(args):
    """Кадр видео на середине реплики N + субтитр -> буфер.
    Параметры:
      путь     — файл видео (обязательно)
      номер    — индекс реплики (по умолчанию 0)
      стрелки  — true/false, по умолчанию TRUE. false — отключить
    """
    video = args.get("путь") or args.get("path") or args.get("видео")
    if not video:
        return 'Укажи путь: {"смотреть_видео": {"путь": "...", "номер": 0}}'

    cmd = [str(video), "--номер", str(int(args.get("номер", 0) or 0))]
    if args.get("стрелки") is False or args.get("без_стрелок"):
        cmd.append("--без_стрелок")
    if args.get("высота"):
        cmd += ["--высота", str(int(args["высота"]))]

    result = _run_cli(cmd)

    # Если субтитров нет — подсказать кадр+субтитр с ручной секундой
    _low = (result or "").lower()
    if "субтитр" in _low and ("не найден" in _low or "not found" in _low):
        _v = str(video).replace(chr(92), "/")
        return (
            f"ℹ️ Субтитры не найдены рядом с {video}.\n"
            f"Кадр по секунде — командой «кадр+субтитр»:\n"
            f'{{"кадр+субтитр": {{"путь": "{_v}", "секунда": 1}}}}'
        )

    return result


@register("список_реплик")
def list_replicas(args):
    """Показать все реплики видео (индекс, время, текст)."""
    video = args.get("путь") or args.get("path") or args.get("видео")
    if not video:
        return 'Укажи путь: {"список_реплик": {"путь": "..."}}'
    return _run_cli([str(video), "--список"])


@register("скачать_видео")
def download_video(args):
    """Скачать видео с YouTube/VK с русскими субтитрами (yt-dlp).
    Параметры: url, качество (по умолч. 720), язык (по умолч. ru),
               путь (куда, по умолч. E:/Jericho/video_download)
    """
    import yt_dlp

    url = args.get("url") or args.get("ссылка")
    if not url:
        return 'Укажи url: {"скачать_видео": {"url": "https://..."}}'

    height = int(args.get("качество", 720) or 720)
    lang = args.get("язык", "ru") or "ru"
    out_dir = Path(args.get("путь") or r"D:\Загрузки")
    out_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
        "merge_output_format": "mkv",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "srt",
        "embedsubtitles": False,
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "?")
    except Exception as e:
        return f"❌ yt-dlp: {e}"

    media = sorted(out_dir.glob("*.mkv"), key=lambda p: p.stat().st_mtime, reverse=True)
    srt = sorted(out_dir.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)

    msg = f"✅ Скачано: {title}\n"
    if media:
        msg += f"🎬 {media[0].name}\n"
    if srt:
        msg += f"📄 {srt[0].name}\n"
    return msg

@register("кадр+субтитр")
def frame_with_subtitle(args):
    """Простой кадр видео на указанной секунде + субтитр -> буфер обмена.
    Параметры:
      путь      — файл видео (обязательно)
      секунда   — момент в секундах (по умолчанию 0.0)
      slit_scan — true/false, применить slit-scan (по умолч. FALSE)
      окно      — ширина окна slit-scan в сек (по умолч. 3.0)
      субтитр   — true/false, накладывать субтитр (по умолч. TRUE)
      сохранить — путь для дополнительного сохранения PNG
    """
    video = args.get("путь") or args.get("path") or args.get("видео")
    if not video:
        return 'Укажи путь: {"кадр+субтитр": {"путь": "...", "секунда": 12.5}}'

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    cmd = [str(video), "--секунда", str(sec)]

    if args.get("slit_scan") or args.get("slitscan"):
        cmd.append("--slit-scan")
        if args.get("окно"):
            cmd += ["--окно", str(float(args["окно"]))]
    if args.get("субтитр") is False or args.get("без_субтитра"):
        cmd.append("--без-субтитра")
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    return _run_cli2(cmd)


@register("слушать_видео")
def listen_video(args):
    """Полный layout плеера (спектрограмма + линия нот + подписи + видео-кадр)
    на указанной секунде -> буфер обмена.
    Параметры:
      путь      — файл видео (обязательно)
      секунда   — момент в секундах (по умолчанию 0.0)
      размер    — WxH, по умолчанию 1366x768
      сохранить — путь для дополнительного сохранения PNG
    """
    video = args.get("путь") or args.get("path") or args.get("видео")
    if not video:
        return 'Укажи путь: {"слушать_видео": {"путь": "...", "секунда": 30}}'

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    cmd = [str(video), "--секунда", str(sec)]

    if args.get("размер"):
        cmd += ["--размер", str(args["размер"])]
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    return _run_cli3(cmd)


@register(["каталог_видео", "видео_список"])
def video_catalog(args):
    """Список видеофайлов с полными путями.
    Параметры: путь (по умолч. D:\\Загрузки), рекурсивно (по умолч. true).
    """
    from pathlib import Path

    root = Path(args.get("путь") or r"D:\\Загрузки")
    if not root.exists():
        return f"❌ Папка не найдена: {root}"

    exts = {".mp4",".mkv",".avi",".mov",".webm",".flv",
            ".wmv",".m4v",".mpg",".mpeg",".3gp"}
    recurse = args.get("рекурсивно", True)

    files = root.rglob("*") if recurse else root.glob("*")
    found = []
    for f in files:
        try:
            if f.is_file() and f.suffix.lower() in exts:
                found.append((f.stat().st_size, str(f)))
        except Exception:
            continue

    if not found:
        return f"📭 В {root} нет видеофайлов."

    found.sort(key=lambda x: -x[0])
    lines = [f"🎬 {root} — {len(found)} видео:"]
    for size, path in found:
        mb = size / (1024 * 1024)
        lines.append(f"  {mb:8.1f} МБ  {path}")
    return "\n".join(lines)

