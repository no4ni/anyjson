"""Удаление текста с изображений: DBNet детекция + inpaint (LaMa/SD/гибрид)."""
import base64
import io
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from _actions import register
from _config import MY_DIR, STABLEDIF_HOST, STABLEDIF_PORT


_DBNET_PATH = Path(r"E:\StableDif\models\text_detection\dbnet.onnx")
_DBNET_SESSION = None
_DBNET_INPUT_SHAPE = None

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _get_session():
    global _DBNET_SESSION, _DBNET_INPUT_SHAPE
    if _DBNET_SESSION is not None:
        return _DBNET_SESSION, _DBNET_INPUT_SHAPE
    if not _DBNET_PATH.is_file():
        raise FileNotFoundError("DBNet модель не найдена: " + str(_DBNET_PATH))
    import onnxruntime as ort
    _DBNET_SESSION = ort.InferenceSession(
        str(_DBNET_PATH), providers=["CPUExecutionProvider"]
    )
    inp = _DBNET_SESSION.get_inputs()[0]
    _DBNET_INPUT_SHAPE = inp.shape
    return _DBNET_SESSION, _DBNET_INPUT_SHAPE


def _round32(v):
    return max(32, ((int(v) + 31) // 32) * 32)


def _preprocess(img, target_hw):
    resized = img.resize((target_hw[1], target_hw[0]), Image.BILINEAR)
    arr = np.asarray(resized, dtype=np.float32) / 255.0
    arr = (arr - _MEAN) / _STD
    arr = arr.transpose(2, 0, 1)
    return arr[np.newaxis, ...].astype(np.float32)


def _detect_text_mask(img, threshold, expand, min_area):
    """DBNet -> бинарная маска PIL (L). Белое = затереть."""
    import cv2

    sess, shape = _get_session()
    orig_w, orig_h = img.size

    if isinstance(shape, (list, tuple)) and len(shape) == 4:
        if isinstance(shape[2], int) and isinstance(shape[3], int):
            target_h, target_w = shape[2], shape[3]
        else:
            target_h = _round32(orig_h)
            target_w = _round32(orig_w)
    else:
        target_h = _round32(orig_h)
        target_w = _round32(orig_w)

    max_side = 1600
    if max(target_h, target_w) > max_side:
        k = max_side / max(target_h, target_w)
        target_h = _round32(target_h * k)
        target_w = _round32(target_w * k)

    x = _preprocess(img, (target_h, target_w))
    input_name = sess.get_inputs()[0].name
    output_name = sess.get_outputs()[0].name
    out = sess.run([output_name], {input_name: x})[0]

    prob = np.array(out)
    while prob.ndim > 2:
        prob = prob[0]
    if prob.shape != (target_h, target_w):
        prob = cv2.resize(prob.astype(np.float32), (target_w, target_h),
                          interpolation=cv2.INTER_LINEAR)

    binmask = (prob > threshold).astype(np.uint8) * 255

    if (target_w, target_h) != (orig_w, orig_h):
        binmask = cv2.resize(binmask, (orig_w, orig_h),
                             interpolation=cv2.INTER_NEAREST)

    if min_area > 0:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(binmask, connectivity=8)
        filtered = np.zeros_like(binmask)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= min_area:
                filtered[labels == i] = 255
        binmask = filtered

    if expand > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT,
                                       (expand * 2 + 1, expand * 2 + 1))
        binmask = cv2.dilate(binmask, k, iterations=1)

    return Image.fromarray(binmask, mode="L")


def _b64_png(img, force_rgb=False):
    buf = io.BytesIO()
    if force_rgb:
        img.convert("RGB").save(buf, "PNG")
    elif img.mode == "RGBA":
        img.save(buf, "PNG")
    else:
        img.convert("RGB").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _post_lama(img, mask):
    url = "http://" + STABLEDIF_HOST + ":" + str(STABLEDIF_PORT) + "/cleanup"
    payload = {"input_image": _b64_png(img), "mask": _b64_png(mask)}
    try:
        r = requests.post(url, json=payload, timeout=300)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Forge не отвечает на " + url
            + ". Запусти Forge через E:\\StableDif\\run.bat"
        )
    if r.status_code != 200:
        raise RuntimeError("HTTP " + str(r.status_code) + ": " + r.text[:300])
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError("LaMa вернул code=" + str(j.get("code"))
                            + ": " + str(j.get("message")))
    b64 = j.get("image") or ""
    if not b64:
        raise RuntimeError("Пустой image в ответе /cleanup")
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _sd_progress_poller(stop_event):
    import sys as _sys
    from collections import deque
    samples = deque(maxlen=5)
    last = None
    tick = 0
    while not stop_event.is_set():
        try:
            r = requests.get(
                "http://" + STABLEDIF_HOST + ":" + str(STABLEDIF_PORT)
                + "/sdapi/v1/progress",
                timeout=3,
            )
            pj = r.json()
            tick += 1
            raw = pj.get("eta_relative")
            try:
                eta = float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                eta = 0.0
            samples.append(eta)
            if tick > 2:
                avg = sum(samples) / len(samples)
                if avg > 180:
                    msg = "Осталось: 3 мин"
                elif avg < 60:
                    msg = "Осталось: " + str(max(1, int(avg))) + " сек"
                else:
                    m, sec = divmod(int(avg), 60)
                    msg = "Осталось: " + str(m) + " мин " + str(sec) + " сек"
                if msg != last:
                    _sys.stdout.write("\r" + msg + " " * 40)
                    _sys.stdout.flush()
                    last = msg
        except Exception:
            pass
        stop_event.wait(1)
    try:
        _sys.stdout.write("\r" + " " * 80 + "\r")
        _sys.stdout.flush()
    except Exception:
        pass


def _post_sd_inpaint(img, mask, prompt, negative, strength, steps, cfg):
    """POST /sdapi/v1/img2img (inpaint). Возвращает PIL.Image."""
    url = "http://" + STABLEDIF_HOST + ":" + str(STABLEDIF_PORT) + "/sdapi/v1/img2img"

    W, H = img.size
    W = max(64, (W // 8) * 8)
    H = max(64, (H // 8) * 8)
    if img.size != (W, H):
        img_r = img.resize((W, H), Image.LANCZOS)
        mask_r = mask.resize((W, H), Image.NEAREST)
    else:
        img_r = img
        mask_r = mask

    body = {
        "init_images": [_b64_png(img_r, force_rgb=True)],
        "mask": _b64_png(mask_r, force_rgb=True),
        "prompt": prompt,
        "negative_prompt": negative,
        "denoising_strength": float(strength),
        "steps": int(steps),
        "cfg_scale": float(cfg),
        "seed": -1,
        "sampler_name": "DPM++ 2M",
        "scheduler": "Karras",
        "width": W,
        "height": H,
        "inpainting_fill": 1,
        "inpaint_full_res": True,
        "inpaint_full_res_padding": 32,
        "mask_blur": 4,
        "resize_mode": 0,
    }
    import threading as _th
    _stop = _th.Event()
    _poll = _th.Thread(target=_sd_progress_poller, args=(_stop,), daemon=True)
    _poll.start()
    try:
        r = requests.post(url, json=body, timeout=600)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Forge не отвечает на " + url
            + ". Запусти Forge через E:\\StableDif\\run.bat"
        )
    finally:
        _stop.set()
        _poll.join(timeout=2)
    if r.status_code != 200:
        raise RuntimeError("HTTP " + str(r.status_code) + ": " + r.text[:300])
    j = r.json()
    imgs = j.get("images") or []
    if not imgs:
        raise RuntimeError("SD вернул без images")
    raw = base64.b64decode(imgs[0])
    out = Image.open(io.BytesIO(raw))
    if out.size != (W, H):
        out = out.resize((W, H), Image.LANCZOS)
    return out


def _cleanup_ready(timeout=2):
    """Проверяет, что /cleanup зарегистрирован (расширение forge2_cleaner)."""
    import urllib.request
    url = "http://" + STABLEDIF_HOST + ":" + str(STABLEDIF_PORT) + "/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = r.read().decode("utf-8", errors="replace")
        return "/cleanup" in data
    except Exception:
        return False


def _ensure_forge_alive():
    """Поднять Forge, если не отвечает. Ждёт и /cleanup (расширение).

    Возвращает None при успехе или строку ошибки.
    """
    import time as _t
    try:
        from _actions.stabledif import _is_alive, _ensure_forge
    except Exception as e:
        return "Не удалось импортировать stabledif: " + str(e)

    if _is_alive(timeout=2) and _cleanup_ready(timeout=3):
        return None

    if not _is_alive(timeout=2):
        startup = _ensure_forge()
        if not _is_alive(timeout=3):
            return "Не удалось поднять Forge: " + str(startup)

    t0 = _t.time()
    while _t.time() - t0 < 60:
        if _cleanup_ready(timeout=2):
            return None
        _t.sleep(1)
    return ("Forge работает, но /cleanup не зарегистрирован "
            "(forge2_cleaner не загрузился). Проверь Extensions в UI Forge.")


@register(["убрать_текст", "стереть_текст", "удалить_текст", "detext"])
def remove_text(args: dict) -> str:
    """Удалить текст с картинки: DBNet детекция + inpaint.

    Параметры:
      путь        — входной файл (обязательно)
      метод       — "гибрид" (по умолч.: LaMa убирает грубо,
                    SD дорисовывает текстуру — лучшее качество),
                    "lama" (быстро, но шумно на сложных текстурах),
                    "sd" (SD-inpaint без LaMa)
      порог       — чувствительность детектора 0..1 (по умолч. 0.5)
      расширить   — радиус дилатации маски в пикселях (по умолч. 24)
      мин_площадь — минимальная площадь пятна в пикселях (по умолч. 40)
      сохранить   — путь к новому файлу. Если не задан — перезапись
                    исходника и в буфер
      только_маска — true: сохранить маску как PNG и вернуть путь

    Только для метода "sd" и "гибрид":
      промпт      — что должно получиться
      негатив     — что исключить
      сила        — denoising_strength, 0.1..1.0 (по умолч. 0.85;
                    для гибрида 0.55)
      шаги        — sampling steps, 1..50 (по умолч. 25)
      жёсткость   — CFG Scale (по умолч. 7.0)

    Примеры:
      {"убрать_текст": {"путь": "C:/rpg/scene_01.png"}}
      {"убрать_текст": {"путь": "C:/in.png", "метод": "гибрид",
                        "промпт": "clean skin, no text",
                        "сохранить": "C:/out.png"}}
    """
    args = args or {}
    src = args.get("путь") or args.get("файл")
    if not src:
        return 'Укажи путь: {"убрать_текст": {"путь": "C:/pic.png"}}'

    method = str(args.get("метод") or "гибрид").strip().lower()
    if method in ("гибрид", "hybrid", "both"):
        method = "гибрид"
    if method not in ("lama", "sd", "гибрид"):
        return ('Ошибка: метод должен быть "lama", "sd" или "гибрид"')

    try:
        threshold = float(args.get("порог") if args.get("порог") is not None else 0.5)
    except (TypeError, ValueError):
        threshold = 0.5
    threshold = max(0.05, min(0.95, threshold))

    try:
        expand = int(args.get("расширить") if args.get("расширить") is not None else 24)
    except (TypeError, ValueError):
        expand = 24
    expand = max(0, min(expand, 200))

    try:
        min_area = int(args.get("мин_площадь") or 40)
    except (TypeError, ValueError):
        min_area = 40
    min_area = max(0, min_area)

    src_p = Path(src).expanduser()
    if not src_p.is_file():
        return "❌ Файл не найден: " + str(src_p)

    is_jpeg = src_p.suffix.lower() in (".jpg", ".jpeg")

    try:
        img = Image.open(str(src_p))
        img.load()
    except Exception as e:
        return "❌ Не открыть: " + str(e)

    has_alpha = img.mode == "RGBA"
    alpha = img.split()[-1] if has_alpha else None
    rgb = img.convert("RGB")
    W, H = rgb.size

    try:
        mask = _detect_text_mask(rgb, threshold, expand, min_area)
    except FileNotFoundError as e:
        return "❌ " + str(e)
    except Exception as e:
        return "❌ Детектор: " + str(e)

    arr = np.array(mask)
    if (arr > 128).sum() == 0:
        return ("ℹ️ Текст не найден (порог " + str(threshold)
                + ", расширить " + str(expand) + "). Картинка не тронута.")

    if args.get("только_маска"):
        out_arg = args.get("сохранить")
        if out_arg:
            mp = Path(out_arg).expanduser()
        else:
            ts = time.strftime("%Y%m%d-%H%M%S")
            mp = MY_DIR / "text_removal" / (ts + "_mask.png")
        mp.parent.mkdir(parents=True, exist_ok=True)
        mask.save(str(mp))
        white_pct = (arr > 128).mean() * 100
        return ("✅ Маска: " + str(mp)
                + " (" + str(W) + "x" + str(H)
                + ", белого " + format(white_pct, ".1f") + "%)")

    # Поднять Forge, если не отвечает (нужен для lama/sd/гибрид)
    _fe = _ensure_forge_alive()
    if _fe:
        return "❌ " + _fe

    prompt = (args.get("промпт")
              or "clean natural skin texture, smooth background, no text")
    negative = (args.get("негатив")
                or "text, letters, words, watermark, writing, blurry, distorted")
    try:
        strength = float(args.get("сила") if args.get("сила") is not None else 0.85)
    except (TypeError, ValueError):
        strength = 0.85
    strength = max(0.1, min(1.0, strength))
    try:
        steps = int(args.get("шаги") or 25)
    except (TypeError, ValueError):
        steps = 25
    steps = max(1, min(50, steps))
    try:
        cfg = float(args.get("жёсткость") or args.get("cfg") or 7.0)
    except (TypeError, ValueError):
        cfg = 7.0

    if method == "lama":
        try:
            cleaned = _post_lama(rgb, mask)
        except Exception as e:
            return "❌ " + str(e)
    elif method == "sd":
        try:
            cleaned = _post_sd_inpaint(rgb, mask, prompt, negative,
                                        strength, steps, cfg)
        except Exception as e:
            return "❌ SD inpaint: " + str(e)
    else:
        try:
            lama_out = _post_lama(rgb, mask)
        except Exception as e:
            return "❌ LaMa (гибрид, шаг 1): " + str(e)
        if args.get("сила") is None:
            strength_hybrid = 0.55
        else:
            strength_hybrid = strength
        try:
            cleaned = _post_sd_inpaint(lama_out, mask, prompt, negative,
                                        strength_hybrid, steps, cfg)
        except Exception as e:
            return "❌ SD (гибрид, шаг 2): " + str(e)

    if cleaned.size != (W, H):
        cleaned = cleaned.resize((W, H), Image.LANCZOS)

    if has_alpha and alpha is not None:
        out_img = cleaned.convert("RGBA")
        out_img.putalpha(alpha)
    elif is_jpeg:
        out_img = cleaned.convert("RGB")
    else:
        out_img = cleaned

    save_arg = args.get("сохранить")
    prefix = ""

    if save_arg:
        out_path = Path(save_arg).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_img.save(str(out_path))
        except Exception as e:
            return "❌ Ошибка сохранения: " + str(e)
        return ("✅ " + str(out_path)
                + " (" + str(W) + "x" + str(H)
                + ", метод " + method
                + ", расширить " + str(expand) + ")")

    try:
        out_img.save(str(src_p))
    except Exception as e:
        return "❌ Ошибка перезаписи: " + str(e)

    try:
        from _actions.clipboard import _pil_to_clipboard
        _pil_to_clipboard(out_img.convert("RGB"))
        prefix = "[BUF] "
    except Exception:
        pass

    return (prefix + "✅ " + str(src_p)
            + " (" + str(W) + "x" + str(H)
            + ", метод " + method
            + ", расширить " + str(expand) + ")")