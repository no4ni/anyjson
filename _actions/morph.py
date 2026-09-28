"""Команда создать_видео - видео из ключевых кадров с таймингами.

Внешний CLI: side/external/morph_video.py (SIFT + IDW морфинг,
ядро из Morphing/morph8.py). Обёртка собирает аргументы, оценивает
похожесть соседних кадров и решает, какие сегменты морфить.

Логика в_морф:
  - в_морф: true в кадре -> сегмент ОТ него К следующему морфится.
  - в_морф: false       -> crossfade.
  - Если кадры похожи (метрика >= 1.0), сегмент с false может
    быть оверрайднут в true, пока суммарное время морф-переходов
    не превысило бюджет (кламп 300000 / (max_h * fps) секунд).
    Оверрайд идёт по убыванию похожести.
"""

import os
import subprocess
import sys

import _config
from _actions import register


def _cli_path():
    return os.path.join(_config.BASE_DIR, "side", "external", "morph_video.py")


def _imread_unicode(path):
    import cv2
    import numpy as np
    _p = str(path)
    # cv2.imread не понимает не-ASCII пути на Windows — идёт сразу в imdecode
    try:
        _p.encode("ascii")
        _ascii = True
    except UnicodeEncodeError:
        _ascii = False
    if _ascii:
        img = cv2.imread(_p)
        if img is not None:
            return img
    try:
        data = np.fromfile(_p, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _resize_if_large(img, max_height):
    import cv2
    h, w = img.shape[:2]
    if h > max_height:
        scale = max_height / h
        new_w = int(w * scale)
        img = cv2.resize(img, (new_w, max_height), interpolation=cv2.INTER_AREA)
    return img


def _fit_to_canvas(img, w, h):
    """Та же логика, что в CLI: вписать в (w,h), лишнее за края по центру."""
    import cv2
    ih, iw = img.shape[:2]
    if (iw, ih) == (w, h):
        return img
    scale = max(w / iw, h / ih)
    nw = max(w, int(round(iw * scale)))
    nh = max(h, int(round(ih * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(img, (nw, nh), interpolation=interp)
    x0 = (nw - w) // 2
    y0 = (nh - h) // 2
    return resized[y0:y0 + h, x0:x0 + w]


def _frames_similarity(img1, img2):
    """Похожесть кадров (одинаковый размер). 1.0 = на пороге "похожи".

    Кривая (tolerance_RGB, min_match_ratio):
      +-0  -> 0.10
      +-1  -> 0.20
      +-3  -> 0.25
      +-10 -> 0.30
      +-30 -> 0.40
      +-50 -> 0.50
    score = max(match_ratio / min_ratio). >= 0.7 -> похожи.
    """
    import numpy as np
    diff = np.max(np.abs(img1.astype(np.int16) - img2.astype(np.int16)), axis=2)
    n = diff.size
    if n == 0:
        return 0.0
    refs = [(0, 0.1), (1, 0.2), (3, 0.25), (10, 0.3), (30, 0.4), (50, 0.5)]
    best = 0.0
    for t, ref in refs:
        match = float((diff <= t).sum()) / n
        score = match / ref
        if score > best:
            best = score
    return best


def _load_canvas(frames, max_h):
    """Загрузить все кадры и привести к одному размеру (эталон -- первый).

    Возвращает (imgs, (w0, h0)) или (None, error_msg).
    """
    import cv2
    imgs = []
    first_shape = None
    for i, it in enumerate(frames):
        p = it.get("путь")
        im = _imread_unicode(p)
        if im is None:
            return None, None, f"Ошибка: не открыть {p}"
        if im.ndim == 3 and im.shape[2] == 4:
            im = cv2.cvtColor(im, cv2.COLOR_BGRA2BGR)
        im = _resize_if_large(im, max_h)
        if first_shape is None:
            first_shape = im.shape[:2]
        imgs.append(im)
    h0, w0 = first_shape
    imgs = [_fit_to_canvas(im, w0, h0) for im in imgs]
    return imgs, (w0, h0), None


@register(["создать_видео"])
def create_video(args: dict) -> str:
    """Создать видео из ключевых кадров.

    Параметры:
      выход — выходной файл .mp4 или .avi (обязательно)
      ключевые_кадры — список кадров, алиас: кадры.
                       Каждый кадр:
                         {"путь": "img.png", "время": <сек>,
                          "в_морф": true/false (по умолч. true)}
                       или: {"секунда": <сек>} + параметр видео/путь —
                       кадр вырезается из видео через ffmpeg.
                       Флаг в_морф определяет переход ОТ кадра К следующему.
      видео — исходное видео для вырезки кадров по секундам,
                       алиасы: путь, источник
      fps — кадров в секунду (по умолч. 20)
      в_морф — глобальный fallback для кадров без явного в_морф
      макс_высота — ограничение высоты кадра (по умолч. 480)
      мин_дист — минимальное расстояние между точками (0 = авто)
      порог_похожести — насколько кадры должны быть похожи, чтобы
                       оверрайдить в_морф: false в true (по умолч. 1.0):
                       0.7 — мягко (серые сцены тоже)
                       1.0 — близкие варианты и дубли (по умолч.)
                       1.5 — только дубли
                       2.0 — только точные копии

    Оверрайд: если кадры похожи (метрика >= порог_похожести), сегмент с
    в_морф: false может стать морф-сегментом, пока суммарное время
    морф-переходов не превышает бюджет клампа (300000 / (max_h*fps)
    секунд). Приоритет -- сегменты с наибольшей похожестью.

    Кламп: если суммарное время морф-переходов превышает бюджет,
    max_h и fps синхронно уменьшаются, чтобы H*fps*T <= 300000.
    """
    frames = args.get("ключевые_кадры") or args.get("кадры")
    out_path = args.get("выход")
    src_video = (args.get("видео") or args.get("источник")
                 or args.get("путь"))
    fps = float(args.get("fps", 20.0))
    use_morph = bool(args.get("в_морф", True))
    max_h = int(args.get("макс_высота", 480))
    min_dist = int(args.get("мин_дист", 0))
    try:
        sim_threshold = float(args.get("порог_похожести") if args.get("порог_похожести") is not None else 1.0)
    except (TypeError, ValueError):
        sim_threshold = 1.0

    if not frames:
        return "Ошибка: не указан ключевые_кадры"
    if not out_path:
        return "Ошибка: не указан путь"
    if not isinstance(frames, list) or len(frames) < 1:
        return "Ошибка: ключевые_кадры должен быть непустым списком"

    cli = _cli_path()
    if not os.path.isfile(cli):
        return f"Ошибка: не найден CLI {cli}"

    def _t_out_of(it, idx):
        """Итоговое время в выходном видео. Если нет — idx+1 (1, 2, 3...)."""
        if isinstance(it, dict):
            for _k in ("время", "итог_секунда", "когда"):
                _v = it.get(_k)
                if _v is not None:
                    try:
                        return float(_v)
                    except (TypeError, ValueError):
                        pass
        return float(idx + 1)

    def _t_src_of(it):
        """Секунда в исходном видео (откуда брать). None если не задана."""
        if isinstance(it, dict):
            for _k in ("секунда", "откуда", "источник_секунда"):
                _v = it.get(_k)
                if _v is not None:
                    try:
                        return float(_v)
                    except (TypeError, ValueError):
                        pass
        return None


    def _t_of(it):
        """Время кадра в выходном видео (после подготовки). 0.0 если нет."""
        if isinstance(it, dict):
            try:
                return float(it.get("время", 0) or 0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    # --- подготовка кадров: путь задан или вырезаем из src_video ---
    _tmp_dir = None
    _prepared = []
    for _i, _item in enumerate(frames):
        if not isinstance(_item, dict):
            _prepared.append(_item)
            continue
        _t_out = _t_out_of(_item, _i)
        _p = _item.get("путь")
        if _p:
            _prepared.append({**_item, "путь": _p, "время": _t_out})
            continue
        _t_src = _t_src_of(_item)
        if _t_src is None or not src_video:
            _prepared.append({**_item, "время": _t_out})
            continue
        if _tmp_dir is None:
            try:
                _tmp_dir = _config.MY_DIR / "_morph_frames"
                _tmp_dir.mkdir(parents=True, exist_ok=True)
            except Exception as _e:
                return f"Ошибка: не создать папку кадров: {_e}"
        _out_png = _tmp_dir / f"frame_{_i:04d}_src{_t_src:.3f}.png"
        try:
            _ff = str(_config.FFMPEG_EXE)
            _cmd = [_ff, "-ss", f"{_t_src:.3f}", "-i", str(src_video),
                "-frames:v", "1", "-y", str(_out_png)]
            _rr = subprocess.run(_cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120)
            if _rr.returncode != 0 or not _out_png.exists():
                _err = ((_rr.stderr or "").strip() or "нет stderr")[:200]
                return f"Ошибка: не вырезать кадр на {_t_src}s: {_err}"
        except Exception as _e:
            return f"Ошибка: ffmpeg на {_t_src}s: {_e}"
        _prepared.append({"путь": str(_out_png), "время": _t_out})

    frames = _prepared

    for i, item in enumerate(frames):
        if not isinstance(item, dict):
            return (f"Ошибка: кадры[{i}] должен быть словарём, "
                f"получено {type(item).__name__}")
        p = item.get("путь")
        t = item.get("время")
        if p is None or t is None:
            return (f"Ошибка: кадры[{i}] требует: путь+время, "
                f"либо секунда+видео (вырезать исходник)")

    sorted_frames = sorted(frames, key=lambda _x: _x.get("время", 0))

    n_seg = max(0, len(sorted_frames) - 1)
    if n_seg == 0:
        return "Ошибка: нужно минимум 2 кадра"

    # --- флаг в_морф для каждого кадра ---
    def _morph_of(it):
        v = it.get("в_морф")
        if v is None:
            return use_morph
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "нет", "off", "no", "")
        return bool(v)

    explicit_morph = []
    for seg in range(n_seg):
        explicit_morph.append(_morph_of(sorted_frames[seg]))

    # --- загрузка кадров в том виде, в каком они пойдут в видео ---
    imgs, _wh, err = _load_canvas(sorted_frames, max_h)
    if err:
        return err
    w0, h0 = _wh

    # --- similarity для "false" сегментов (кандидаты на оверрайд) ---
    candidates = []  # (similarity, seg, dur)
    for seg in range(n_seg):
        if explicit_morph[seg]:
            continue
        try:
            sim = _frames_similarity(imgs[seg], imgs[seg + 1])
        except Exception:
            sim = 0.0
        if sim >= sim_threshold:
            dur = _t_of(sorted_frames[seg + 1]) - _t_of(sorted_frames[seg])
            if dur > 0:
                candidates.append((sim, seg, dur))

    # --- бюджет морф-секунд ---
    base_secs = 0.0
    for seg in range(n_seg):
        if explicit_morph[seg]:
            dur = _t_of(sorted_frames[seg + 1]) - _t_of(sorted_frames[seg])
            if dur > 0:
                base_secs += dur

    if max_h > 0 and fps > 0:
        max_secs = 300000.0 / (max_h * fps)
    else:
        max_secs = float("inf")

    # --- жадный оверрайд ---
    candidates.sort(key=lambda x: (-x[0], x[1]))  # по sim убыв, потом по индексу
    morph_segments = list(explicit_morph)
    used_secs = base_secs
    overridden = []
    for sim, seg, dur in candidates:
        if used_secs + dur <= max_secs:
            morph_segments[seg] = True
            used_secs += dur
            overridden.append(seg)

    # --- финальный список crossfade-сегментов ---
    disabled_segments = [seg for seg in range(n_seg) if not morph_segments[seg]]
    all_no_morph = (len(disabled_segments) == n_seg)

    # --- кламп, если суммарное время морф-сегментов превышает бюджет ---
    adjust_note = ""
    if not all_no_morph and used_secs > max_secs and max_secs > 0:
        k = (max_secs / used_secs) ** 0.5
        new_h = max(1, int(max_h * k))
        new_fps = max(1.0, int(fps * k * 100) / 100.0)
        adjust_note = (" [кламп: " + str(max_h) + "p*" + str(fps)
                       + "fps*" + format(used_secs, ".2f") + "с="
                       + str(round(max_h * fps * used_secs))
                       + " > 300000 -> " + str(new_h) + "p, "
                       + str(new_fps) + "fps]")
        max_h = new_h
        fps = new_fps

    # --- команда ---
    cmd = [sys.executable, cli, "--выход", out_path, "--fps", str(fps),
           "--макс_высота", str(max_h)]

    for item in sorted_frames:
        p = item.get("путь")
        t = item.get("время")
        cmd.extend(["--кадр", f"{p}:{t}"])

    if min_dist > 0:
        cmd.extend(["--мин_дист", str(min_dist)])

    if all_no_morph:
        cmd.append("--без_морфа")
        adjust_note = ""
    else:
        for seg in disabled_segments:
            cmd.extend(["--без_морфа_сегмент", str(seg)])

    # --- запуск ---
    _env = dict(os.environ)
    _env.setdefault("OPENCV_LOG_LEVEL", "SILENT")
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=sys.stderr,
                           text=True, encoding="utf-8", timeout=3600,
                           env=_env)
    except subprocess.TimeoutExpired:
        return "Ошибка: таймаут 1 час"
    except Exception as e:
        return f"Ошибка: {e}"

    if r.returncode != 0:
        return f"Ошибка (код {r.returncode}): подробности в консоли выше"

    if not os.path.isfile(out_path):
        return f"Ошибка: файл не создан: {out_path}"

    size = os.path.getsize(out_path)
    ovr_note = ""
    if overridden:
        ovr_note = " [оверрайд: сегменты " + ", ".join(str(s) for s in overridden) + "]"
    return f"Видео создано: {out_path} ({size // 1024} КБ)" + adjust_note + ovr_note