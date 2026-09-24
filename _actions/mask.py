"""Искажение картинки — анти-нарушение правил."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _actions import register

from _config import ANTIVIOLATE_CLI as CLI  # noqa: E402


import re as _re_noise

_NOISE_RE = _re_noise.compile(
    r"FT_New_Face failed"
    r"|QFontDatabase:"
    r"|Note that Qt no longer ships fonts"
    r"|\[ WARN:0@[^\]]+\] global"
)


def _strip_noise(text):
    """Убрать из вывода шум FreeType/Qt/OpenCV, не влияющий на результат."""
    if not text:
        return text
    return "\n".join(
        ln for ln in text.splitlines() if not _NOISE_RE.search(ln)
    )


def _to_clipboard(png_path):
    try:
        from _actions.clipboard import _pil_to_clipboard
        from PIL import Image
        _pil_to_clipboard(Image.open(png_path))
        return True
    except Exception as e:
        print(f"[mask] clipboard fail: {e}", file=sys.stderr)
        return False


@register(["исказить_картинку", "исказить"])
def distort_image(args):
    """Искажает изображение.
    Параметры:
      путь   — входной файл (обязательно)
      сила   — 1..20 (по умолчанию 5)
      выход  — если задан, файл сохраняется
      
    """
    src = args.get("путь")
    if not src:
        return 'Укажи путь: {"исказить_картинку": {"путь": "C:/in.png", "сила": 5}}'

    src_p = Path(src)
    if not src_p.is_file():
        return f"❌ Файл не найден: {src_p}"

    try:
        strength = int(args.get("сила", 5) or 5)
    except (TypeError, ValueError):
        strength = 5
    strength = max(1, min(20, strength))

    out = args.get("выход")
    explicit_save = bool(out)

    if explicit_save:
        out_p = Path(out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
    else:
        fd, tmp_name = tempfile.mkstemp(suffix=".png", prefix="antiviolate_")
        os.close(fd)
        out_p = Path(tmp_name)

    cmd = [sys.executable, str(CLI), str(src_p), str(out_p), "--level", str(strength)]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=int(args.get("таймаут", 300) or 300),
        )
    except subprocess.TimeoutExpired:
        if not explicit_save:
            try: out_p.unlink()
            except Exception: pass
        return "❌ Таймаут"

    out_text = _strip_noise(((r.stdout or "") + (r.stderr or "")).strip())
    if r.returncode != 0:
        if not explicit_save:
            try: out_p.unlink()
            except Exception: pass
        return f"❌ Искажение упало:\n{out_text[-1500:]}"

    # По умолчанию: если не просили сохранить явно — в буфер
    put_to_buf = args.get("буфер", True) if not explicit_save else bool(args.get("буфер", False))

    if put_to_buf:
        ok = _to_clipboard(out_p)
        if not explicit_save:
            try: out_p.unlink()
            except Exception: pass
        if ok:
            return (f"[BUF] 📋 Искажено (сила {strength}) → буфер обмена\n"
                    f"Если не прошло — скопируй агенту: Сила недостаточна")
        return f"⚠️ В буфер не легло (файл: {out_p})"

    return f"✅ Искажено (сила {strength})"
