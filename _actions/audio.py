"""Слух: спектрограмма аудио -> буфер."""
import subprocess
import sys
from pathlib import Path

from _actions import register
from _config import HEAR_CLI, MY_DIR, PIPER_EXE, PIPER_VOICES


@register(["слушать_аудио", "слушать_звук", "аудио"])
def listen_audio(args):
    """Спектрограмма аудио на указанной секунде -> буфер обмена.

    Параметры:
      путь      — файл аудио (.mp3/.wav/.flac)
      секунда   — момент в секундах (по умолчанию 0.0)
      размер    — WxH, по умолчанию 1370x675
      сохранить — путь для дополнительного сохранения PNG
      в_контекст — положить в контекст (по умолч. true)
    """
    audio = args.get("путь") or args.get("файл")
    if not audio:
        return 'Укажи путь: {"слушать_аудио": {"путь": "...", "секунда": 30}}'

    if not HEAR_CLI.exists():
        return f"❌ Скрипт не найден: {HEAR_CLI}"

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)

    to_ctx = args.get("в_контекст")
    if to_ctx is None:
        to_ctx = args.get("в_буфер", True)
    if isinstance(to_ctx, str):
        to_ctx = to_ctx.strip().lower() not in ("0", "false", "нет", "off", "")

    cmd = [sys.executable, str(HEAR_CLI), str(audio), "--секунда", str(sec)]

    if args.get("размер"):
        cmd += ["--размер", str(args["размер"])]
    save_arg = args.get("сохранить")
    if save_arg:
        cmd += ["--сохранить", str(save_arg)]
    elif not to_ctx:
        # некуда девать результат — автосохраняем в MY_DIR/audio_out/
        import time as _time
        ts = _time.strftime("%Y%m%d-%H%M%S")
        auto_dir = MY_DIR / "audio_out"
        auto_dir.mkdir(parents=True, exist_ok=True)
        save_arg = auto_dir / (ts + ".png")
        cmd += ["--сохранить", str(save_arg)]
    if not to_ctx:
        cmd.append("--no-clip")

    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут 600с (первый запуск считает спектрограмму долго)"

    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if to_ctx and "CLIP:" in text:
        text = "[BUF] " + text
    elif not to_ctx and "CLIP:" in text:
        text = text.replace(" -> буфер обмена", "")
    return text

@register(["озвучить", "синтез_речи", "сказать", "tts"])
def say_tts(args: dict) -> str:
    """Озвучить текст (Piper TTS, офлайн).

    Параметры:
      текст    — что сказать (обязательно)
      голос    — irina / denis / ruslan / dmitri (по умолч. irina)
      скорость — length_scale, 1.0 = норма (по умолч. 1.0)
      путь     — куда сохранить WAV (иначе MY_DIR/tts_out/<TS>.wav)
      gpu     — CUDA для генерации (по умолч. true; gpu: false — CPU)
    """
    import os
    import time
    import tempfile

    text = args.get("текст") or args.get("text") or args.get("содержимое")
    if not text:
        return "Ошибка: не указан текст"

    _alias = {
        "ирина": "irina",
        "денис": "denis",
        "руслан": "ruslan",
        "дмитрий": "dmitri",
        "дмитри": "dmitri",
    }
    voice = str(args.get("голос") or "irina").strip().lower()
    voice = _alias.get(voice, voice)
    speed = float(args.get("скорость") or 1.0)
    use_gpu = bool(args.get("gpu", True))
    out_arg = args.get("путь")

    model = PIPER_VOICES / "ru" / "ru_RU" / voice / "medium" / ("ru_RU-" + voice + "-medium.onnx")
    if not model.is_file():
        return "Ошибка: голос не найден: " + str(model)
    cfg = model.parent / (model.name + ".json")
    if not cfg.is_file():
        return "Ошибка: нет config для голоса: " + str(cfg)

    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = MY_DIR / "tts_out" / (ts + ".wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    txt_fd, txt_path = tempfile.mkstemp(suffix=".txt", prefix="piper_")
    os.close(txt_fd)
    try:
        Path(txt_path).write_text(str(text), encoding="utf-8")
        cmd = [str(PIPER_EXE), "-m", str(model), "-c", str(cfg),
               "-i", txt_path, "-f", str(out_path)]
        if speed != 1.0:
            cmd.extend(["--length-scale", str(speed)])
        if use_gpu:
            cmd.append("--cuda")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        if r.returncode != 0:
            err = (r.stderr or "")[-500:]
            return "Ошибка piper (код " + str(r.returncode) + "): " + err
    finally:
        try:
            os.unlink(txt_path)
        except OSError:
            pass

    if not out_path.is_file():
        return "Ошибка: файл не создан: " + str(out_path)
    size = out_path.stat().st_size
    return ("Готово: " + str(out_path)
            + " (" + str(size // 1024) + " КБ, голос " + voice + ")")

