"""Слух: спектрограмма аудио -> буфер."""
import subprocess
import sys
from pathlib import Path

from _actions import register
from _config import HEAR_CLI


@register(["слушать_аудио", "слушать_звук", "аудио"])
def listen_audio(args):
    """Спектрограмма аудио на указанной секунде -> буфер обмена.

    Параметры:
      путь      — файл аудио (.mp3/.wav/.flac)
      секунда   — момент в секундах (по умолчанию 0.0)
      размер    — WxH, по умолчанию 1370x675
      сохранить — путь для дополнительного сохранения PNG
    """
    audio = args.get("путь") or args.get("файл")
    if not audio:
        return 'Укажи путь: {"слушать_аудио": {"путь": "...", "секунда": 30}}'

    if not HEAR_CLI.exists():
        return f"❌ Скрипт не найден: {HEAR_CLI}"

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    cmd = [sys.executable, str(HEAR_CLI), str(audio), "--секунда", str(sec)]

    if args.get("размер"):
        cmd += ["--размер", str(args["размер"])]
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут 600с (первый запуск считает спектрограмму долго)"

    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text

