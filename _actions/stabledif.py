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
_STATE = MY_DIR / "stabledif_state.json"


def _load_state():
    if not _STATE.exists():
        return {}
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d):
    try:
        _STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except Exception:
        pass


def _get_last_model():
    return _load_state().get("last_model")


def _set_last_model(name):
    if not name:
        return
    st = _load_state()
    st["last_model"] = name
    _save_state(st)


def _ensure_model(requested):
    """Переключить модель на requested, если задана и отличается от текущей.

    Возвращает None при успехе, строку с ошибкой — при проблеме.

    Перед переключением сверяет имя со списком Forge. Принимает:
      • "name" — как в статусе (канонично);
      • "name.safetensors" — с расширением;
      • "Stable-diffusion/name.safetensors" — путь от корня моделей;
      • подкаталоги.
    Матч — по нормализованному basename (без пути и расширения).
    Если модели нет — возвращает ошибку со списком доступных.
    """
    if not requested:
        return None

    req = str(requested).strip()

    try:
        ms = _sd_models_list()
    except Exception:
        ms = []

    names = []
    for m in ms:
        if not isinstance(m, dict):
            continue
        mn = m.get("model_name")
        tt = m.get("title")
        if mn:
            names.append(str(mn))
        if tt and tt != mn:
            names.append(str(tt))

    def _base(s):
        s = str(s).replace("\\", "/")
        if "/" in s:
            s = s.rsplit("/", 1)[-1]
        for _e in (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx"):
            if s.lower().endswith(_e):
                s = s[: -len(_e)]
                break
        return s.lower()

    lookup = {}
    for n in names:
        lookup.setdefault(_base(n), n)

    if req in names:
        canonical = req
    elif _base(req) in lookup:
        canonical = lookup[_base(req)]
    else:
        _lines = ["\u274c \u041c\u043e\u0434\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430 \u0432 Forge: " + repr(requested)]
        if names:
            _lines.append("")
            _lines.append("\U0001f4c1 \u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435 \u043c\u043e\u0434\u0435\u043b\u0438:")
            _seen = set()
            for n in names:
                if n in _seen:
                    continue
                _seen.add(n)
                _lines.append("  \u2022 " + n)
        else:
            _lines.append("")
            _lines.append("(Forge \u0432\u0435\u0440\u043d\u0443\u043b \u043f\u0443\u0441\u0442\u043e\u0439 "
                          "\u0441\u043f\u0438\u0441\u043e\u043a \u2014 \u043f\u0440\u043e\u0432\u0435\u0440\u044c \u0441\u0442\u0430\u0431\u0438\u043b\u0434\u0438\u0444_\u0441\u0442\u0430\u0442\u0443\u0441)")
        _lines.append("")
        _lines.append("\U0001f4a1 \u0423\u043a\u0430\u0437\u044b\u0432\u0430\u0439 \u0438\u043c\u044f \u0431\u0435\u0437 \u043f\u0440\u0435\u0444\u0438\u043a\u0441\u0430 "
                      "'Stable-diffusion/' \u0438 \u0431\u0435\u0437 '.safetensors'.")
        return "\n".join(_lines)

    try:
        _, opts = _http("GET", "/sdapi/v1/options", timeout=10)
        current = opts.get("sd_model_checkpoint", "")
    except Exception:
        current = ""
    if current == canonical:
        return None

    try:
        _http("POST", "/sdapi/v1/options",
              {"sd_model_checkpoint": canonical}, timeout=120)
    except Exception as e:
        _lines = ["\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u043c\u043e\u0434\u0435\u043b\u044c: "
                  + str(e)]
        if names:
            _lines.append("")
            _lines.append("\U0001f4c1 \u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435 \u043c\u043e\u0434\u0435\u043b\u0438:")
            _seen = set()
            for n in names:
                if n in _seen:
                    continue
                _seen.add(n)
                _lines.append("  \u2022 " + n)
        return "\n".join(_lines)
    _set_last_model(canonical)
    return None


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
            _w = 20
            filled = _w * pct // 100
            if filled >= _w:
                bar = "=" * _w
            else:
                bar = "=" * filled + ">" + "." * (_w - filled - 1)
            print("\r⏳ Forge стартует... " + str(pct) + "% [" + bar + "]",
                  end="", flush=True)
            last_pct = pct
        time.sleep(0.5)
    print("\r" + " " * 60 + "\r", end="", flush=True)
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
                # ETA в Forge сильно завышен на первых шагах.
                # Всё, что больше 3 минут — врёт, режем.
                if avg > 180:
                    msg = "Осталось: 3 мин"
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


def capture_forge_pid():
    """Если Forge жив, а pid-файла нет — найти PID по порту и записать.
    Так anyjson «берёт шефство» над уже запущенным Forge
    и гасит его при выходе.
    """
    pid_file = MY_DIR / "stabledif.pid"
    if pid_file.exists():
        return
    if not _is_alive(timeout=1):
        return
    try:
        import subprocess as _sp
        r = _sp.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
        needle = ":" + str(STABLEDIF_PORT)
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            if "LISTENING" not in parts[3].upper():
                continue
            if needle not in parts[1]:
                continue
            try:
                pid = int(parts[4])
            except (ValueError, IndexError):
                continue
            try:
                pid_file.write_text(str(pid))
            except Exception:
                pass
            return
    except Exception:
        pass


def _ensure_forge():
    """Внутренняя: поднять Forge, если не отвечает. Не команда."""
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


def _sd_models_list(retries=3, delay=1.0):
    """GET /sd-models с ретраем: Forge отдаёт пустой список во время рескана."""
    import time as _t
    for _i in range(retries):
        try:
            _, _ms = _http("GET", "/sdapi/v1/sd-models", timeout=10)
        except Exception as _e:
            if _i == retries - 1:
                raise
            _t.sleep(delay)
            continue
        if _ms:
            return _ms
        _t.sleep(delay)
    return []


@register(["стабилдиф_статус", "форж", "forge", "стабилдиф_инфо"])
def sd_status(args):
    """Статус Forge: текущая модель, путь и список доступных.

    Параметров нет.

    Пример:
      {"стабилдиф_статус": {}}
    """
    if not _is_alive():
        startup = _ensure_forge()
        if not _is_alive():
            return "Не удалось поднять Forge: " + startup
    try:
        _http("POST", "/sdapi/v1/refresh-checkpoints", {}, timeout=30)
    except Exception:
        pass
    try:
        _, opts = _http("GET", "/sdapi/v1/options", timeout=10)
        current = opts.get("sd_model_checkpoint", "?")
    except Exception as e:
        return "ошибка запроса options: " + str(e)
    try:
        ms = _sd_models_list()
    except Exception as e:
        return "ошибка запроса моделей: " + str(e)
    _last = _get_last_model() or "(не задана)"
    if not ms:
        return ("Сейчас: " + str(current) + "\n"
                "По умолчанию: " + _last + "\n"
                "Моделей нет.\n"
                + chr(0x1F4C2) + " Forge: " + str(STABLEDIF_DIR))
    lines = [chr(0x1F3A8) + " Текущая модель: " + str(current),
             chr(0x1F3AF) + " По умолчанию: " + _last,
             chr(0x1F4C1) + " Доступно моделей: " + str(len(ms)),
             chr(0x1F4C2) + " Forge: " + str(STABLEDIF_DIR),
             chr(0x1F4C1) + " Модели: "
             + str(Path(STABLEDIF_DIR) / "webui" / "models"
                   / "Stable-diffusion"),
             chr(0x1F3A8) + " LoRA: "
             + str(Path(STABLEDIF_DIR) / "webui" / "models" / "Lora")]
    for m in ms:
        name = str(m.get("model_name", "?"))
        mark = " \u2190 \u0441\u0435\u0439\u0447\u0430\u0441" if name == current else ""
        lines.append("  - " + name + mark)
    return "\n".join(lines)


_MODEL_CATEGORIES = [
    ("\u0427\u0435\u043a\u043f\u043e\u0439\u043d\u0442\u044b",  "checkpoints",  ["Stable-diffusion"]),
    ("LoRA",          "lora",         ["Lora"]),
    ("ControlNet",    "controlnet",   ["ControlNet"]),
    ("VAE",           "vae",          ["VAE"]),
    ("\u0413\u0438\u043f\u0435\u0440\u0441\u0435\u0442\u0438",     "hypernet",     ["hypernetworks"]),
    ("\u0410\u043f\u0441\u043a\u0435\u0439\u043b\u0435\u0440\u044b",    "upscale",      ["ESRGAN", "GFPGAN", "Codeformer"]),
    ("IP-Adapter",    "ipadapter",    ["ipadapter"]),
    ("CLIP vision",   "clip",         ["clip_vision"]),
    ("SVD",           "svd",          ["svd"]),
]

_MODEL_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".th"}


@register(["стабилдиф_модели", "модели_стабилдиф"])
def sd_models(args):
    """Список файлов моделей на диске (без запросов к Forge).

    Параметры:
      тип    — фильтр категории: checkpoints / lora / controlnet / vae /
               hypernet / upscale / ipadapter / clip / svd / все (по умолч. все)
      пути   — показывать полные пути (по умолч. false)
    """
    args = args or {}
    _filter = str(args.get("\u0442\u0438\u043f") or "\u0432\u0441\u0435").strip().lower()
    show_paths = bool(args.get("\u043f\u0443\u0442\u0438", False))

    base = Path(STABLEDIF_DIR) / "webui" / "models"
    if not base.is_dir():
        return "\u041f\u0430\u043f\u043a\u0430 мо\u0434\u0435\u043b\u0435\u0439 н\u0435 н\u0430\u0439\u0434\u0435\u043d\u0430: " + str(base)

    known = {c[1] for c in _MODEL_CATEGORIES} | {"\u0432\u0441\u0435", "all"}
    if _filter not in known:
        return ("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 \u0442\u0438\u043f: " + _filter
                + "\n\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435: "
                + ", ".join(c[1] for c in _MODEL_CATEGORIES) + ", \u0432\u0441\u0435")

    lines = []
    total = 0
    for label, key, subs in _MODEL_CATEGORIES:
        if _filter not in ("\u0432\u0441\u0435", "all") and _filter != key:
            continue
        files = []
        for sub in subs:
            d = base / sub
            if not d.is_dir():
                continue
            for f in d.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() in _MODEL_EXTS:
                    files.append(f)
        files.sort(key=lambda x: str(x).lower())
        if not files:
            continue
        lines.append(label + " (" + str(len(files)) + "):")
        for f in files:
            rel = f.relative_to(base).as_posix()
            if show_paths:
                lines.append("  - " + f.name + "  [" + f.suffix.lower().lstrip(".") + "]  " + str(f))
            else:
                lines.append("  - " + rel)
        total += len(files)
        lines.append("")

    if not lines:
        return "\u041c\u043e\u0434\u0435\u043b\u0435\u0439 \u043d\u0435\u0442 \u0432: " + str(base)
    lines.insert(0, "\u0412\u0441\u0435\u0433\u043e: " + str(total) + " \u0444\u0430\u0439\u043b\u043e\u0432")
    return chr(10).join(lines).rstrip()


def _sd_wh(args):
    w = int(args.get("ширина") or 512)
    h = int(args.get("высота") or 512)
    m = max(w, h)
    if m > 1024:
        k = 1024 / m
        w = max(1, int(round(w * k)))
        h = max(1, int(round(h * k)))
    return {"width": w, "height": h}


def _sd_wht(args):
    """Ширина/высота/шаги с ограничением W*H*S <= 10M.

    Если произведение больше — синхронно меньшаем всё на кубический корень
    отношения. Пропорции W:H сохраняются (с точностью до округления).
    """
    d = _sd_wh(args)
    w, h = d["width"], d["height"]
    try:
        st = int(args.get("шаги") or 25)
    except (TypeError, ValueError):
        st = 25
    st = max(1, min(st, 35))

    LIMIT = 10_000_000
    prod = w * h * st
    if prod > LIMIT:
        k = (LIMIT / prod) ** (1.0 / 3.0)
        w = max(1, int(round(w * k)))
        h = max(1, int(round(h * k)))
        st = max(1, int(round(st * k)))
        # страховка от округления вверх
        while w * h * st > LIMIT and st > 1:
            st -= 1
        while w * h * st > LIMIT and h > 1:
            h -= 1
        while w * h * st > LIMIT and w > 1:
            w -= 1
    return {"width": w, "height": h, "steps": st}


@register(["стабилдиф", "сгенерировать", "нарисуй", "нарисовать", "txt2img"])
def sd_txt2img(args):
    """Сгенерировать картинку через Forge (txt2img).

    Параметры:
      промпт     — что нарисовать (обязательно, СТРОГО НА АНГЛИЙСКОМ!), алиас: запрос
      негатив    — что исключить
      ширина     — по умолчанию 512
      высота     — по умолчанию 512
      шаги       — sampling steps, 1..35 (по умолч. 25)
      жёсткость  — CFG Scale (по умолч. 7.0), алиас: cfg
      зерно      — seed (по умолч. -1 = случайный), алиас: seed
      сэмплер    — DPM++ 2M (по умолч.), Euler a, DPM++ 2M SDE, ...
      шедулер    — Karras (по умолч.), Exponential, Normal, Simple, ...
      модель     — имя чекпойнта; запоминается для следующих запросов
      референс   — блок ControlNet: {поза|референс: путь, препроцессор,
                   модель, сила, начало, конец, режим},
                   алиас: контроль.
                   • «поза»/«референс»/«image» — путь к картинке-контролю
                   • «препроцессор» — имя препроцессора: openpose,
                     canny, depth, lineart, softedge, scribble, mlsd,
                     normal, seg, tile, inpaint, ip2p (или None)
                   • «модель» — файл ControlNet с хешем:
                     "control_v11p_sd15_openpose [abc12345]". Если
                     не задана — подберётся по имени препроцессора.
                     Если в «модель» положить имя препроцессора
                     ("openpose") — код поймёт и переставит.
                   • «сила» — weight 0..2 (по умолч. 1.0)
                   • «начало»/«конец» — guidance_start/end, 0..1
                   • «режим» — "ControlNet is more important"
                     (по умолч.), "... is more important", "Balanced",
                     "My prompt is more important"
      в_контекст — положить результат в контекст (по умолч. true)
      сохранить  — путь для PNG (иначе MY_DIR/stabledif_out/)
    """
    prompt = args.get("промпт") or args.get("запрос")
    if not prompt:
        return 'Укажи промпт: {"нарисуй": {"промпт": "кот в шляпе"}}'
    if not _is_alive():
        # автостарт: поднять Forge и дождаться API
        startup = _ensure_forge()
        if not _is_alive():
            return "Не удалось поднять Forge: " + startup

    # Ограничение W*H*S <= 10M с диагностикой сжатия.
    _wht = _sd_wht(args)
    try:
        _w0 = int(args.get("ширина") or 512)
        _h0 = int(args.get("высота") or 512)
    except (TypeError, ValueError):
        _w0 = _h0 = 512
    try:
        _s0 = max(1, min(int(args.get("шаги") or 25), 35))
    except (TypeError, ValueError):
        _s0 = 25
    body = {
        "prompt": str(prompt),
        "negative_prompt": str(args.get("негатив") or ""),
        **_wht,
        "cfg_scale": float(args.get("жёсткость") or args.get("cfg") or 7.0),
        "seed": int(args.get("зерно") or args.get("seed") or -1),
        "sampler_name": str(args.get("сэмплер") or "DPM++ 2M"),
        "scheduler": str(args.get("шедулер") or "Karras"),
    }
    _ctrl = args.get("референс") or args.get("контроль")
    _cn = _controlnet_block(_ctrl) if _ctrl else None
    if _cn and _cn.get("error"):
        return _cn["error"]
    if _cn:
        _empty = {"enabled": False}
        body["alwayson_scripts"] = {
            "controlnet": {"args": [_cn["unit"], _empty, _empty]}
        }

    model = args.get("модель")
    if model:
        _err = _ensure_model(model)
        if _err:
            return _err
    else:
        _last = _get_last_model()
        if _last:
            _err = _ensure_model(_last)
            if _err:
                return _err

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

    save_arg = args.get("сохранить")
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
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    img.save(str(out_path))

    to_buf = args.get("в_контекст")
    if to_buf is None:
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

    tail = " -> буфер" if to_buf else " -> диск"
    _actual = str(img.size[0]) + "x" + str(img.size[1])
    if (_wht["width"], _wht["height"], _wht["steps"]) != (_w0, _h0, _s0):
        _chain = (str(_w0) + "x" + str(_h0) + "@" + str(_s0)
                  + " → " + str(_wht["width"]) + "x" + str(_wht["height"]) + "@" + str(_wht["steps"])
                  + " → OK " + _actual)
    else:
        _chain = "OK " + _actual
    return prefix + str(out_path) + tail + " [" + _chain + "]"

@register(["перерисуй", "нарисуй_по", "img2img", "стилизация", "обработка_картинки"])
def sd_img2img(args):
    """Перерисовать существующую картинку через Forge (img2img).

    Параметры:
      путь       — входная картинка (обязательно)
      промпт     — что должно получиться (обязательно), алиас: запрос
      сила       — denoising_strength, 0.0..1.0 (по умолч. 0.75)
      негатив    — что исключить
      ширина     — если не задано, берётся из исходной
      высота     — если не задано, берётся из исходной
      короткая   — меньшая сторона при автоподборе размера (по умолч. 512)
      шаги       — sampling steps, 1..35 (по умолч. 25)
      жёсткость  — CFG Scale (по умолч. 7.0), алиас: cfg
      зерно      — seed (по умолч. -1 = случайный), алиас: seed
      сэмплер    — DPM++ 2M (по умолч.), Euler a, DPM++ 2M SDE, ...
      шедулер    — Karras (по умолч.), Exponential, Normal, Simple, ...
      модель     — имя чекпойнта; запоминается для следующих запросов
      референс   — блок ControlNet: {поза|референс: путь, препроцессор,
                   модель, сила, начало, конец, режим},
                   алиас: контроль.
                   • «поза»/«референс»/«image» — путь к картинке-контролю
                   • «препроцессор» — имя препроцессора: openpose,
                     canny, depth, lineart, softedge, scribble, mlsd,
                     normal, seg, tile, inpaint, ip2p (или None)
                   • «модель» — файл ControlNet с хешем:
                     "control_v11p_sd15_openpose [abc12345]". Если
                     не задана — подберётся по имени препроцессора.
                     Если в «модель» положить имя препроцессора
                     ("openpose") — код поймёт и переставит.
                   • «сила» — weight 0..2 (по умолч. 1.0)
                   • «начало»/«конец» — guidance_start/end, 0..1
                   • «режим» — "ControlNet is more important"
                     (по умолч.), "... is more important", "Balanced",
                     "My prompt is more important"
      в_контекст — положить результат в контекст (по умолч. true)
      сохранить  — путь для PNG (иначе MY_DIR/stabledif_out/)
    """
    src = args.get("путь")
    if not src:
        return 'Укажи путь: {"перерисуй": {"путь": "C:/in.png", "промпт": "oil painting"}}'
    prompt = args.get("промпт") or args.get("запрос")
    if not prompt:
        return 'Укажи промпт: {"перерисуй": {"путь": "C:/in.png", "промпт": "oil painting"}}'

    src_p = Path(src).expanduser()
    if not src_p.is_file():
        return "Файл не найден: " + str(src_p)

    if not _is_alive():
        startup = _ensure_forge()
        if not _is_alive():
            return "Не удалось поднять Forge: " + startup

    # SVG не читается PIL — растеризуем во временный PNG
    _tmp_svg_png = None
    if src_p.suffix.lower() == ".svg":
        try:
            from PIL import Image as _ImgSvg
            from _config import MY_DIR as _MYSvg
            _svg_img = _svg_to_alpha(src_p).convert("RGB")
            _tmp_svg_png = _MYSvg / ("_svg_in_" + src_p.stem + ".png")
            _svg_img.save(str(_tmp_svg_png))
            src_p = _tmp_svg_png
        except Exception as e:
            return "Не удалось растеризовать SVG: " + str(e)

    try:
        import base64 as _b64
        with open(src_p, "rb") as f:
            init_b64 = _b64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return "не удалось прочитать исходник: " + str(e)

    body = {
        "init_images": [init_b64],
        "prompt": str(prompt),
        "negative_prompt": str(args.get("негатив") or ""),
        "denoising_strength": float(args.get("сила") or 0.75),
        "steps": max(1, min(int(args.get("шаги") or 25), 35)),
        "cfg_scale": float(args.get("жёсткость") or args.get("cfg") or 7.0),
        "seed": int(args.get("зерно") or args.get("seed") or -1),
        "sampler_name": str(args.get("сэмплер") or "DPM++ 2M"),
        "scheduler": str(args.get("шедулер") or "Karras"),
    }
    # размеры: из аргументов, иначе меньшая сторона = 512, пропорции сохранены
    try:
        from PIL import Image as _Img
        with _Img.open(str(src_p)) as _im:
            src_w, src_h = _im.size
    except Exception as e:
        return "не удалось прочитать размер исходника: " + str(e)

    w_arg = args.get("ширина")
    h_arg = args.get("высота")
    if w_arg and h_arg:
        w, h = int(w_arg), int(h_arg)
    else:
        short = int(args.get("короткая") or 512)
        k = short / float(min(src_w, src_h))
        w = int(src_w * k)
        h = int(src_h * k)
        w = max(64, (w // 8) * 8)
        h = max(64, (h // 8) * 8)

    # Ограничение W*H*S <= 10M (синхронно, с сохранением пропорций).
    _wht_limited = False
    try:
        _s0 = int(body.get("steps") or 25)
    except (TypeError, ValueError):
        _s0 = 25
    _LIMIT = 10_000_000
    _prod = w * h * _s0
    if _prod > _LIMIT:
        _k = (_LIMIT / _prod) ** (1.0 / 3.0)
        w = max(64, (int(round(w * _k)) // 8) * 8)
        h = max(64, (int(round(h * _k)) // 8) * 8)
        _s = max(1, int(round(_s0 * _k)))
        while w * h * _s > _LIMIT and _s > 1:
            _s -= 1
        while w * h * _s > _LIMIT and h > 64:
            h -= 8
        while w * h * _s > _LIMIT and w > 64:
            w -= 8
        body["steps"] = _s
        _wht_limited = True
    body["width"] = w
    body["height"] = h

    _ctrl = args.get("референс") or args.get("контроль")
    _cn = _controlnet_block(_ctrl) if _ctrl else None
    if _cn and _cn.get("error"):
        return _cn["error"]
    if _cn:
        _empty = {"enabled": False}
        body["alwayson_scripts"] = {
            "controlnet": {"args": [_cn["unit"], _empty, _empty]}
        }

    model = args.get("модель")
    if model:
        _err = _ensure_model(model)
        if _err:
            return _err
    else:
        _last = _get_last_model()
        if _last:
            _err = _ensure_model(_last)
            if _err:
                return _err

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

    save_arg = args.get("сохранить")
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
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    img.save(str(out_path))

    to_buf = args.get("в_контекст")
    if to_buf is None:
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

    tail = " -> буфер" if to_buf else " -> диск"
    _note = ""
    if _wht_limited:
        _note = (" [сжато: " + str(w) + "x" + str(h)
                 + "@" + str(body.get("steps")) + "]")
    return prefix + "OK " + str(img.size[0]) + "x" + str(img.size[1]) + " -> " + str(out_path) + tail + _note

def _svg_to_alpha(svg_path):
    """SVG -> PIL.Image (L mode, 255 = непрозрачно)."""
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM
    from io import BytesIO as _BIO
    from PIL import Image as _I, ImageChops as _IC
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError("svglib вернул None (пустой SVG?)")
    buf_w = _BIO()
    renderPM.drawToFile(drawing, buf_w, fmt="PNG", bg=0xFFFFFF)
    buf_w.seek(0)
    img_w = _I.open(buf_w).convert("L")
    buf_c = _BIO()
    renderPM.drawToFile(drawing, buf_c, fmt="PNG", bg=0x000000)
    buf_c.seek(0)
    img_c = _I.open(buf_c).convert("L")
    diff = _IC.difference(img_w, img_c)
    return _IC.invert(diff)


def _load_mask_image(mask_path, target_size):
    """Загрузить маску. .svg через svglib+reportlab, .png/.jpg как есть."""
    from PIL import Image
    p = Path(mask_path).expanduser()
    if not p.is_file():
        return None, "Файл маски не найден: " + str(p)
    ext = p.suffix.lower()
    if ext == ".svg":
        try:
            mask = _svg_to_alpha(p)
        except ImportError:
            return None, ("Для SVG-маски нужны svglib+reportlab. "
                          "Установи: py -m pip install svglib reportlab")
        except Exception as e:
            return None, "Не удалось растеризовать SVG: " + str(e)
    else:
        try:
            mask = Image.open(str(p)).convert("L")
        except Exception as e:
            return None, "Не удалось открыть маску: " + str(e)
    if mask.size != target_size:
        mask = mask.resize(target_size, Image.LANCZOS)
    return mask, None


@register(["дорисуй", "закрась", "inpaint", "дорисовать"])
def sd_inpaint(args):
    """Дорисовать/закрасить область картинки через Forge (inpaint).

    Перерисовывает только пиксели, где маска белая. Чёрные пиксели
    маски остаются нетронутыми.

    Параметры:
      путь       — входная картинка (обязательно)
      маска      — файл-маска (обязательно):
                   .png/.jpg — белое = перерисовать, чёрное = оставить;
                   .svg — непрозрачная область = перерисовать
      промпт     — что должно получиться (обязательно)
      негатив    — что исключить
      сила       — denoising_strength, 0.0..1.0 (по умолч. 0.75)
      ширина/высота — по умолч. из исходной
      короткая   — если размеры не заданы: меньшая сторона (по умолч. 512)
      шаги       — sampling steps, 1..35 (по умолч. 25)
      жёсткость  — CFG Scale (по умолч. 7.0), алиас: cfg
      зерно      — seed (по умолч. -1 = случайный), алиас: seed
      сэмплер    — DPM++ 2M (по умолч.), Euler a, DPM++ 2M SDE, ...
      шедулер    — Karras (по умолч.), Exponential, Normal, Simple, ...
      модель     — имя чекпойнта; запоминается для следующих запросов
      референс   — блок ControlNet: {поза|референс: путь, препроцессор,
                   модель, сила, начало, конец, режим},
                   алиас: контроль.
                   • «поза»/«референс»/«image» — путь к картинке-контролю
                   • «препроцессор» — имя препроцессора: openpose,
                     canny, depth, lineart, softedge, scribble, mlsd,
                     normal, seg, tile, inpaint, ip2p (или None)
                   • «модель» — файл ControlNet с хешем:
                     "control_v11p_sd15_openpose [abc12345]". Если
                     не задана — подберётся по имени препроцессора.
                     Если в «модель» положить имя препроцессора
                     ("openpose") — код поймёт и переставит.
                   • «сила» — weight 0..2 (по умолч. 1.0)
                   • «начало»/«конец» — guidance_start/end, 0..1
                   • «режим» — "ControlNet is more important"
                     (по умолч.), "... is more important", "Balanced",
                     "My prompt is more important"
      в_контекст — положить результат в контекст (по умолч. true)
      сохранить  — путь для PNG (иначе MY_DIR/stabledif_out/)

    Примеры:
      {"дорисуй": {"путь": "C:/in.png", "маска": "C:/mask.png",
                   "промпт": "sunny sky"}}
      {"дорисуй": {"путь": "C:/in.png", "маска": "C:/mask.svg",
                   "промпт": "oil painting", "сила": 0.85}}
    """
    src = args.get("путь")
    if not src:
        return ('Укажи путь: {"дорисуй": {"путь": "C:/in.png", '
                '"маска": "C:/mask.png", "промпт": "..."}}')
    mask_path = args.get("маска")
    if not mask_path:
        return ('Укажи маску: {"дорисуй": {"путь": "...", '
                '"маска": "C:/mask.png", "промпт": "..."}}')
    prompt = args.get("промпт")
    if not prompt:
        return ('Укажи промпт: {"дорисуй": {"путь": "...", '
                '"маска": "...", "промпт": "sunny sky"}}')

    src_p = Path(src).expanduser()
    if not src_p.is_file():
        return "Файл не найден: " + str(src_p)

    if not _is_alive():
        startup = _ensure_forge()
        if not _is_alive():
            return "Не удалось поднять Forge: " + startup

    try:
        import base64 as _b64
        with open(src_p, "rb") as f:
            init_b64 = _b64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return "не удалось прочитать исходник: " + str(e)

    try:
        from PIL import Image as _Img
        with _Img.open(str(src_p)) as _im:
            src_w, src_h = _im.size
    except Exception as e:
        return "не удалось прочитать размер исходника: " + str(e)

    w_arg = args.get("ширина")
    h_arg = args.get("высота")
    if w_arg and h_arg:
        w, h = int(w_arg), int(h_arg)
    else:
        short = int(args.get("короткая") or 512)
        k = short / float(min(src_w, src_h))
        w = max(64, (int(src_w * k) // 8) * 8)
        h = max(64, (int(src_h * k) // 8) * 8)

    mask_img, err = _load_mask_image(mask_path, (w, h))
    if err:
        return err
    from io import BytesIO as _BIO
    _mb = _BIO()
    mask_img.save(_mb, format="PNG")
    mask_b64 = _b64.b64encode(_mb.getvalue()).decode("ascii")

    body = {
        "init_images": [init_b64],
        "mask": mask_b64,
        "prompt": str(prompt),
        "negative_prompt": str(args.get("негатив") or ""),
        "denoising_strength": float(args.get("сила") or 0.75),
        "steps": max(1, min(int(args.get("шаги") or 25), 35)),
        "cfg_scale": float(args.get("жёсткость") or args.get("cfg") or 7.0),
        "seed": int(args.get("зерно") or args.get("seed") or -1),
        "sampler_name": str(args.get("сэмплер") or "DPM++ 2M"),
        "scheduler": str(args.get("шедулер") or "Karras"),
        "width": w,
        "height": h,
        "inpainting_fill": 1,
        "inpaint_full_res": True,
        "inpaint_full_res_padding": 32,
        "mask_blur": 4,
        "resize_mode": 0,
    }

    _ctrl = args.get("референс") or args.get("контроль")
    _cn = _controlnet_block(_ctrl) if _ctrl else None
    if _cn and _cn.get("error"):
        return _cn["error"]
    if _cn:
        _empty = {"enabled": False}
        body["alwayson_scripts"] = {
            "controlnet": {"args": [_cn["unit"], _empty, _empty]}
        }

    model = args.get("модель")
    if model:
        _err = _ensure_model(model)
        if _err:
            return _err
    else:
        _last = _get_last_model()
        if _last:
            _err = _ensure_model(_last)
            if _err:
                return _err

    def _do():
        return _http("POST", "/sdapi/v1/img2img", body, timeout=600)

    try:
        _, resp = _run_with_progress(_do)
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

    save_arg = args.get("сохранить")
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
        out_path = _OUT / (ts + "_inpaint_seed" + str(seed) + ".png")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    img.save(str(out_path))

    to_buf = args.get("в_контекст")
    if to_buf is None:
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

    tail = " -> буфер" if to_buf else " -> диск"
    return (prefix + "OK " + str(img.size[0]) + "x" + str(img.size[1])
            + " -> " + str(out_path) + tail)


_pose_aliases = {
    "голова": ["голова", "head", "nose", "0"],
    "шея": ["шея", "neck", "1"],
    "правое_плечо": ["правое_плечо", "right_shoulder", "2"],
    "правый_локоть": ["правый_локоть", "right_elbow", "3"],
    "правая_кисть": ["правая_кисть", "right_wrist", "right_hand", "4"],
    "левое_плечо": ["левое_плечо", "left_shoulder", "5"],
    "левый_локоть": ["левый_локоть", "left_elbow", "6"],
    "левая_кисть": ["левая_кисть", "left_wrist", "left_hand", "7"],
    "правый_таз": ["правый_таз", "right_hip", "8"],
    "правое_колено": ["правое_колено", "right_knee", "9"],
    "правая_стопа": ["правая_стопа", "right_ankle", "right_foot", "10"],
    "левый_таз": ["левый_таз", "left_hip", "11"],
    "левое_колено": ["левое_колено", "left_knee", "12"],
    "левая_стопа": ["левая_стопа", "left_ankle", "left_foot", "13"],
}

_pose_colors = {
    "голова": (255, 255, 255),
    "шея": (255, 255, 255),
    "правое_плечо": (255, 0, 0), "правый_локоть": (255, 0, 0),
    "правая_кисть": (255, 0, 0),
    "левое_плечо": (255, 0, 255), "левый_локоть": (255, 0, 255),
    "левая_кисть": (255, 0, 255),
    "правый_таз": (255, 128, 0), "правое_колено": (255, 128, 0),
    "правая_стопа": (255, 128, 0),
    "левый_таз": (0, 128, 255), "левое_колено": (0, 128, 255),
    "левая_стопа": (0, 128, 255),
}

_pose_bones = [
    ("голова", "шея"),
    ("шея", "правое_плечо"),
    ("правое_плечо", "правый_локоть"),
    ("правый_локоть", "правая_кисть"),
    ("шея", "левое_плечо"),
    ("левое_плечо", "левый_локоть"),
    ("левый_локоть", "левая_кисть"),
    ("шея", "правый_таз"),
    ("правый_таз", "правое_колено"),
    ("правое_колено", "правая_стопа"),
    ("шея", "левый_таз"),
    ("левый_таз", "левое_колено"),
    ("левое_колено", "левая_стопа"),
]


def _draw_pose_skeleton(points, size):
    from PIL import Image, ImageDraw
    W, H = int(size[0]), int(size[1])
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    P = {}
    for canon, keys in _pose_aliases.items():
        for k in keys:
            v = points.get(k)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    P[canon] = (int(v[0]), int(v[1]))
                except (TypeError, ValueError):
                    pass
                break
    line_w = max(3, W // 200)
    dot_r = max(3, W // 150)
    for a, b in _pose_bones:
        if a in P and b in P:
            draw.line([P[a], P[b]],
                      fill=_pose_colors.get(b, (255, 255, 255)),
                      width=line_w)
    for canon, xy in P.items():
        c = _pose_colors.get(canon, (255, 255, 255))
        draw.ellipse([xy[0] - dot_r, xy[1] - dot_r,
                      xy[0] + dot_r, xy[1] + dot_r], fill=c)
    return img


@register(["поза", "скелет", "openpose"])
def sd_pose(args):
    """Нарисовать скелет OpenPose по координатам (для ControlNet).

    Параметры:
      размер    — [ширина, высота] (по умолч. [768, 512])
      точки     — словарь {имя: [x, y]} — координаты суставов
      сохранить — путь к PNG
      в_контекст — положить в контекст (по умолч. true)

    Имена суставов (можно латиницей или цифрой 0..13):
      голова, шея, правое_плечо, правый_локоть, правая_кисть,
      левое_плечо, левый_локоть, левая_кисть,
      правый_таз, правое_колено, правая_стопа,
      левый_таз, левое_колено, левая_стопа.

    Пример:
      {"поза": {"размер": [768, 512],
                "точки": {"голова": [384, 130], "шея": [384, 175]},
                "сохранить": "C:/rpg/pose_kay.png"}}
    """
    size = args.get("размер") or [768, 512]
    points = args.get("точки") or {}
    if not points:
        return "Укажи точки — координаты суставов."
    img = _draw_pose_skeleton(points, size)
    out_path = None
    save_arg = args.get("сохранить")
    if save_arg:
        out_path = Path(save_arg).expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        img.save(str(out_path))
    to_buf = args.get("в_контекст")
    if to_buf is None:
        to_buf = args.get("в_буфер", True)
    if isinstance(to_buf, str):
        to_buf = to_buf.strip().lower() not in ("0", "false", "нет", "off", "")
    prefix = ""
    if to_buf:
        try:
            from _actions.clipboard import _pil_to_clipboard
            _pil_to_clipboard(img)
            prefix = "[BUF] "
        except Exception:
            pass
    info = "Скелет " + str(img.size[0]) + "x" + str(img.size[1])
    if out_path:
        info += " -> " + str(out_path)
    return prefix + info


def _controlnet_block(ctrl, default_prefix="openpose"):
    if isinstance(ctrl, str):
        ctrl = {"референс": ctrl}
    if not isinstance(ctrl, dict):
        return None
    pose_path = (ctrl.get("поза") or ctrl.get("референс")
                 or ctrl.get("image"))
    if not pose_path:
        return None
    p = Path(pose_path).expanduser()
    if not p.is_file():
        return {"error": "Не найден файл контроля: " + str(p)}
    try:
        with open(p, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return {"error": "Не удалось прочитать файл контроля: " + str(e)}
    preproc = (ctrl.get("препроцессор") or ctrl.get("preprocessor")
               or "None")
    # Если в "модель" подсунули имя препроцессора (openpose, canny, ...),
    # а препроцессор не задан — переставляем. Классическая путаница:
    # модель ControlNet = файл с хешем ("control_v11p_sd15_openpose [cab7...]"),
    # препроцессор = короткое имя ("openpose", "canny", "depth", ...).
    if (isinstance(preproc, str) and preproc.lower() in ("none", "null", "")
            and isinstance(ctrl.get("модель") or ctrl.get("model"), str)):
        _m_low = (ctrl.get("модель") or ctrl.get("model") or "").strip().lower()
        _known_preprocs = {
            "openpose", "openpose_hand", "openpose_face", "openpose_full",
            "openpose_hand_full", "canny", "depth", "depth_midas",
            "depth_zoe", "depth_leres", "lineart", "lineart_anime",
            "lineart_coarse", "softedge", "scribble", "scribble_xdog",
            "mlsd", "normal", "seg", "tile", "inpaint", "ip2p",
            "shuffle", "pidinet", "pidinet_scribble", "binary",
        }
        if _m_low in _known_preprocs:
            preproc = ctrl.get("модель") or ctrl.get("model")
            # prefix для поиска модели — первое слово до "_"
            # (openpose_hand → openpose, lineart_anime → lineart)
            default_prefix = str(preproc).lower().replace("-", "_").split("_")[0]
            ctrl = dict(ctrl)
            ctrl.pop("модель", None)
            ctrl.pop("model", None)
    # Forge ищет preprocessor по имени через global_state.get_preprocessor().
    # Если передать "none" строчными — вернёт None и упадёт дальше.
    # Нормализуем к "None" (с большой).
    if isinstance(preproc, str) and preproc.lower() in ("none", "null", ""):
        preproc = "None"
    preproc_low = str(preproc).lower().replace("-", "_")
    _no_model_prefixes = ("reference_only", "reference_adain",
                          "reference_adain+attn")
    _no_model = any(preproc_low.startswith(x) for x in _no_model_prefixes)

    _dbg = ""
    model = ctrl.get("модель") or ctrl.get("model")
    if not model and not _no_model:
        try:
            _st, ml = _http("GET", "/controlnet/model_list", timeout=15)
            if isinstance(ml, dict):
                lst = ml.get("model_list", [])
                _dbg = (" [status=" + str(_st) + ", keys="
                        + str(list(ml.keys())) + ", N=" + str(len(lst)) + "]")
                for m in lst:
                    if m and default_prefix in m.lower():
                        model = m
                        break
            else:
                _dbg = (" [status=" + str(_st) + ", type="
                        + type(ml).__name__ + ", head="
                        + str(ml)[:80] + "]")
        except Exception as e:
            _dbg = " [http error: " + type(e).__name__ + ": " + str(e) + "]"
    if not model and not _no_model:
        return {"error": "Не указана модель контроля (нет модели с '"
                + default_prefix + "' в списке)." + _dbg
                + "\n💡 Как правильно: передай имя препроцессора в "
                + "'препроцессор' (openpose, canny, depth, lineart, ...) "
                + "и НЕ указывай 'модель' — она подберётся автоматически. "
                + "Либо 'модель' = полный файл "
                + "'control_v11p_sd15_openpose [abc12345]'."}
    if not model:
        model = "None"
    # Forge ищет модель по ПОЛНОМУ ключу с хешем: "name [abc12345]".
    # Если пользователь прислал короткое имя — ищем полное в списке.
    if model and model != "None" and " [" not in str(model):
        try:
            _st2, _ml2 = _http("GET", "/controlnet/model_list", timeout=15)
            _lst2 = _ml2.get("model_list", []) if isinstance(_ml2, dict) else []
            for _m in _lst2:
                if not _m:
                    continue
                if _m == model or _m.startswith(str(model) + " ["):
                    model = _m
                    break
        except Exception:
            pass
    try:
        weight = float(ctrl.get("сила") or ctrl.get("weight") or 1.0)
    except (TypeError, ValueError):
        weight = 1.0
    try:
        start = float(ctrl.get("начало") or 0.0)
    except (TypeError, ValueError):
        start = 0.0
    try:
        end = float(ctrl.get("конец") or 1.0)
    except (TypeError, ValueError):
        end = 1.0
    ctrl_mode = (ctrl.get("режим") or ctrl.get("mode")
                 or "ControlNet is more important")
    unit = {
        "enabled": True,
        "image": img_b64,
        "input_image": img_b64,
        "module": preproc,
        "model": model,
        "weight": weight,
        "resize_mode": "Crop and Resize",
        "control_mode": ctrl_mode,
        "pixel_perfect": False,
        "processor_res": 512,
        "threshold_a": 64,
        "threshold_b": 64,
        "guidance_start": start,
        "guidance_end": end,
    }
    return {"unit": unit}

@register(["моя_галерея", "галерея"])
def my_gallery(args: dict) -> str:
    """Все сгенерированные картинки в профиле.

    Параметры:
      сортировка — "новые" (по умолч.) или "старые"
      расширение — "png" (по умолч.) / "jpg" / "все"
      лимит — сколько последних вернуть (0 = все)
    """
    order = str(args.get("сортировка") or "новые").strip().lower()
    ext = str(args.get("расширение") or "png").strip().lower().lstrip(".")
    limit = int(args.get("лимит") or 0)

    if ext in ("все", "all", "*"):
        pattern = "*"
    else:
        pattern = "*." + ext

    if not _OUT.exists():
        return "Галерея пуста: " + str(_OUT)

    files = [f for f in _OUT.glob(pattern) if f.is_file()]
    if not files:
        return "Галерея пуста: " + str(_OUT)

    reverse = order not in ("старые", "old", "asc")
    files.sort(key=lambda f: f.stat().st_mtime, reverse=reverse)

    if limit > 0:
        files = files[:limit]

    lines = ["Галерея (" + str(len(files)) + "):"]
    for f in files:
        lines.append(str(f))
    return chr(10).join(lines)
