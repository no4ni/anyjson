"""Stable Diffusion (Forge) — генерация картинок через API."""
import base64
import io
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from _actions import register
from _config import (
    STABLEDIF_DIR, STABLEDIF_RUN,
    STABLEDIF_HOST, STABLEDIF_PORT,
    MY_DIR,
)


_BASE = "http://" + STABLEDIF_HOST + ":" + str(STABLEDIF_PORT)
_LOG  = MY_DIR / "stabledif.log"
_PID  = MY_DIR / "stabledif.pid"
_OUT  = MY_DIR / "stabledif_out"


def _http(method, path, body=None, timeout=60):
    url = _BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.status, json.loads(raw.decode("utf-8"))
        except Exception:
            return r.status, raw


def _is_alive(timeout=2):
    try:
        _http("GET", "/sdapi/v1/options", timeout=timeout)
        return True
    except Exception:
        return False


def _wait_ready(deadline_sec=180, expected_sec=30):
    t0 = time.time()
    last_pct = -1
    while time.time() - t0 < deadline_sec:
        if _is_alive(timeout=2):
            print("\r" + " " * 60 + "\r", end="", flush=True)
            return True
        elapsed = time.time() - t0
        pct = int(elapsed / expected_sec * 100)
        if pct > 99:
            pct = 99
        if pct != last_pct:
            bar = "#" * (pct // 3)
            print("\r⏳ Forge стартует... " + str(pct) + "% " + bar,
                  end="", flush=True)
            last_pct = pct
        time.sleep(0.5)
    print("\r" + " " * 60 + "\r", end="", flush=True)
    return False


def _read_pid():
    try:
        return int(_PID.read_text().strip())
    except Exception:
        return None


def _pid_alive(pid):
    if pid is None:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x101000, False, pid)
        if not h:
            return False
        rc = k.WaitForSingleObject(h, 0)
        k.CloseHandle(h)
        return rc == 0x102
    except Exception:
        return False


def _fmt_eta(secs):
    """Секунды -> '1 ч 5 мин' / '5 мин 30 сек' / '45 сек'."""
    s = int(secs)
    if s < 60:
        return str(max(1, s)) + " сек"
    m, s = divmod(s, 60)
    if m < 60:
        return str(m) + " мин" + (" " + str(s) + " сек" if s else "")
    h, m = divmod(m, 60)
    return str(h) + " ч" + (" " + str(m) + " мин" if m else "")


def _progress_poller(stop_event):
    """Печатает \r-прогресс генерации. Живёт в отдельном потоке.

    ETA в Forge скачет — сглаживаем скользящим средним по последним 5 замерам
    и игнорируем первые 3 замера (прогрев).
    """
    import sys
    from collections import deque

    samples = deque(maxlen=5)
    last_shown = None
    tick = 0

    while not stop_event.is_set():
        try:
            _, p = _http("GET", "/sdapi/v1/progress", timeout=3)
            tick += 1

            raw = p.get("eta_relative")
            if raw is None:
                raw = p.get("eta")
            try:
                eta = float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                eta = 0.0

            samples.append(eta)
            if tick > 3:
                avg = sum(samples) / len(samples)
                if avg > 3600:
                    msg = "Осталось: долго"
                else:
                    msg = "Осталось: " + _fmt_eta(avg)
                if msg != last_shown:
                    sys.stdout.write("\r" + msg + " " * 30)
                    sys.stdout.flush()
                    last_shown = msg
        except Exception:
            pass
        stop_event.wait(1)
    try:
        sys.stdout.write("\r" + " " * 100 + "\r")
        sys.stdout.flush()
    except Exception:
        pass


def _run_with_progress(fn):
    """Запускает fn() в главном потоке, а рядом крутит _progress_poller."""
    stop_event = threading.Event()
    poller = threading.Thread(target=_progress_poller, args=(stop_event,), daemon=True)
    poller.start()
    try:
        return fn()
    finally:
        stop_event.set()
        poller.join(timeout=2)


@register("стабилдиф_запустить")
def sd_start(args):
    """Запустить Forge в фоне (без окна). Ждёт готовности API до 180с."""
    if _is_alive():
        return "Forge уже слушает " + _BASE
    if not STABLEDIF_RUN.exists():
        return "Не найден: " + str(STABLEDIF_RUN)
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(_LOG, "ab")
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    p = subprocess.Popen(
        ["cmd", "/c", str(STABLEDIF_RUN)],
        cwd=str(STABLEDIF_DIR),
        stdout=log_f, stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )
    _PID.write_text(str(p.pid))
    if _wait_ready(180):
        return "Forge запущен (pid " + str(p.pid) + "), API на " + _BASE
    return "pid " + str(p.pid) + ", но API не ответил за 180с. Смотри " + str(_LOG)


@register("стабилдиф_остановить")
def sd_stop(args):
    """Остановить Forge."""
    if _is_alive():
        try:
            _http("POST", "/sdapi/v1/server-stop", timeout=10)
        except Exception:
            pass
        time.sleep(3)
    if not _is_alive():
        return "Forge остановлен."
    pid = _read_pid()
    if pid and _pid_alive(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        time.sleep(2)
    if not _is_alive():
        return "Forge убит (pid " + str(pid) + ")."
    return "не удалось остановить"


@register("стабилдиф_статус")
def sd_status(args):
    """Статус Forge."""
    if not _is_alive():
        pid = _read_pid()
        if pid and _pid_alive(pid):
            return "процесс жив (pid " + str(pid) + "), но API ещё не отвечает"
        return "Forge не запущен."
    try:
        _, opts = _http("GET", "/sdapi/v1/options", timeout=5)
        model = opts.get("sd_model_checkpoint", "?")
        return "Forge жив на " + _BASE + " | модель: " + str(model)
    except Exception as e:
        return "Forge жив на " + _BASE + " (модель: " + str(e) + ")"


@register(["стабилдиф_модели", "модели_стабилдиф"])
def sd_models(args):
    """Список моделей."""
    if not _is_alive():
        return "Forge не запущен."
    try:
        _, ms = _http("GET", "/sdapi/v1/sd-models", timeout=10)
    except Exception as e:
        return "ошибка запроса: " + str(e)
    if not ms:
        return "моделей нет"
    lines = ["Моделей: " + str(len(ms))]
    for m in ms:
        lines.append("  - " + str(m.get("model_name", "?")) + "  [" + str(m.get("title", "?")) + "]")
    return "\n".join(lines)


@register(["стабилдиф", "сгенерировать", "нарисуй", "txt2img"])
def sd_txt2img(args):
    """Сгенерировать картинку через Forge (txt2img).

    Параметры:
      промпт     — что нарисовать (обязательно)
      негатив    — что исключить
      ширина     — по умолчанию 512
      высота     — по умолчанию 512
      шаги       — sampling steps (по умолч. 20)
      cfg        — CFG Scale (по умолч. 7.0)
      seed       — по умолч. -1
      сэмплер    — Euler a по умолчанию
      модель     — имя чекпойнта
      в_буфер    — положить PNG в буфер (по умолч. true)
      сохранить  — путь для PNG
    """
    prompt = args.get("промпт") or args.get("prompt") or args.get("запрос")
    if not prompt:
        return 'Укажи промпт: {"нарисуй": {"промпт": "кот в шляпе"}}'
    if not _is_alive():
        # автостарт: поднять Forge и дождаться API
        startup = sd_start({})
        if not _is_alive():
            return "Не удалось поднять Forge: " + startup

    body = {
        "prompt": str(prompt),
        "negative_prompt": str(args.get("негатив") or args.get("negative") or ""),
        "width": int(args.get("ширина") or args.get("width") or 512),
        "height": int(args.get("высота") or args.get("height") or 512),
        "steps": int(args.get("шаги") or args.get("steps") or 20),
        "cfg_scale": float(args.get("cfg") or 7.0),
        "seed": int(args.get("seed") or -1),
        "sampler_name": str(args.get("сэмплер") or args.get("sampler") or "Euler a"),
    }
    model = args.get("модель") or args.get("model")
    if model:
        try:
            _http("POST", "/sdapi/v1/options", {"sd_model_checkpoint": model}, timeout=120)
        except Exception as e:
            return "не удалось переключить модель: " + str(e)

    def _do_txt2img():
        return _http("POST", "/sdapi/v1/txt2img", body, timeout=600)

    try:
        _, resp = _run_with_progress(_do_txt2img)
    except urllib.error.HTTPError as e:
        return "HTTP " + str(e.code) + ": " + str(e.read()[:300])
    except Exception as e:
        return "ошибка запроса: " + str(e)

    images = resp.get("images") or []
    if not images:
        return "API вернул без images"

    raw = base64.b64decode(images[0])
    from PIL import Image
    img = Image.open(io.BytesIO(raw))

    save_arg = args.get("сохранить") or args.get("save")
    if save_arg:
        out_path = Path(save_arg).expanduser()
    else:
        _OUT.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        info = resp.get("info") or "{}"
        try:
            info_j = json.loads(info) if isinstance(info, str) else info
            seed = info_j.get("seed", "x")
        except Exception:
            seed = "x"
        out_path = _OUT / (ts + "_seed" + str(seed) + ".png")
    img.save(str(out_path))

    to_buf = args.get("в_буфер", True)
    if isinstance(to_buf, str):
        to_buf = to_buf.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_buf:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(img)
            prefix = "[BUF] "
        except Exception as e:
            return "PNG: " + str(out_path) + " (в буфер не попал: " + str(e) + ")"

    return prefix + "OK " + str(img.size[0]) + "x" + str(img.size[1]) + " -> " + out_path.name + " -> буфер"

@register(["перерисуй", "нарисуй_по", "img2img", "стилизация", "обработка_картинки"])
def sd_img2img(args):
    """Перерисовать существующую картинку через Forge (img2img).

    Параметры:
      путь       — входная картинка (обязательно)
      промпт     — что должно получиться (обязательно)
      сила       — denoising_strength, 0.0..1.0 (по умолч. 0.75)
      негатив    — что исключить
      ширина     — если не задано, берётся из исходной
      высота     — если не задано, берётся из исходной
      шаги       — sampling steps (по умолч. 20)
      cfg        — CFG Scale (по умолч. 7.0)
      seed       — по умолч. -1
      сэмплер    — Euler a по умолчанию
      модель     — имя чекпойнта
      в_буфер    — положить PNG в буфер (по умолч. true)
      сохранить  — путь для PNG
    """
    src = args.get("путь") or args.get("path") or args.get("вход")
    if not src:
        return 'Укажи путь: {"перерисуй": {"путь": "C:/in.png", "промпт": "oil painting"}}'
    prompt = args.get("промпт") or args.get("prompt") or args.get("запрос")
    if not prompt:
        return 'Укажи промпт: {"перерисуй": {"путь": "C:/in.png", "промпт": "oil painting"}}'

    src_p = Path(src).expanduser()
    if not src_p.is_file():
        return "Файл не найден: " + str(src_p)

    if not _is_alive():
        startup = sd_start({})
        if not _is_alive():
            return "Не удалось поднять Forge: " + startup

    try:
        import base64 as _b64
        with open(src_p, "rb") as f:
            init_b64 = _b64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return "не удалось прочитать исходник: " + str(e)

    body = {
        "init_images": [init_b64],
        "prompt": str(prompt),
        "negative_prompt": str(args.get("негатив") or args.get("negative") or ""),
        "denoising_strength": float(args.get("сила") or args.get("strength") or 0.75),
        "steps": int(args.get("шаги") or args.get("steps") or 20),
        "cfg_scale": float(args.get("cfg") or 7.0),
        "seed": int(args.get("seed") or -1),
        "sampler_name": str(args.get("сэмплер") or args.get("sampler") or "Euler a"),
    }
    # размеры: из аргументов, иначе меньшая сторона = 512, пропорции сохранены
    try:
        from PIL import Image as _Img
        with _Img.open(str(src_p)) as _im:
            src_w, src_h = _im.size
    except Exception as e:
        return "не удалось прочитать размер исходника: " + str(e)

    w_arg = args.get("ширина") or args.get("width")
    h_arg = args.get("высота") or args.get("height")
    if w_arg and h_arg:
        w, h = int(w_arg), int(h_arg)
    else:
        short = int(args.get("короткая") or 512)
        k = short / float(min(src_w, src_h))
        w = int(src_w * k)
        h = int(src_h * k)
        w = max(64, (w // 8) * 8)
        h = max(64, (h // 8) * 8)
    body["width"] = w
    body["height"] = h

    model = args.get("модель") or args.get("model")
    if model:
        try:
            _http("POST", "/sdapi/v1/options", {"sd_model_checkpoint": model}, timeout=120)
        except Exception as e:
            return "не удалось переключить модель: " + str(e)

    def _do_img2img():
        return _http("POST", "/sdapi/v1/img2img", body, timeout=600)

    try:
        _, resp = _run_with_progress(_do_img2img)
    except urllib.error.HTTPError as e:
        return "HTTP " + str(e.code) + ": " + str(e.read()[:300])
    except Exception as e:
        return "ошибка запроса: " + str(e)

    images = resp.get("images") or []
    if not images:
        return "API вернул без images"

    raw = base64.b64decode(images[0])
    from PIL import Image
    img = Image.open(io.BytesIO(raw))

    save_arg = args.get("сохранить") or args.get("save")
    if save_arg:
        out_path = Path(save_arg).expanduser()
    else:
        _OUT.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        info = resp.get("info") or "{}"
        try:
            info_j = json.loads(info) if isinstance(info, str) else info
            seed = info_j.get("seed", "x")
        except Exception:
            seed = "x"
        out_path = _OUT / (ts + "_i2i_seed" + str(seed) + ".png")
    img.save(str(out_path))

    to_buf = args.get("в_буфер", True)
    if isinstance(to_buf, str):
        to_buf = to_buf.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_buf:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(img)
            prefix = "[BUF] "
        except Exception as e:
            return "PNG: " + str(out_path) + " (в буфер не попал: " + str(e) + ")"

    return prefix + "OK " + str(img.size[0]) + "x" + str(img.size[1]) + " -> " + out_path.name + " -> буфер"

