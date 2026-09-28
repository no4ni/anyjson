"""morph_video - CLI: видео из ключевых кадров с таймингами.

Каждый кадр задаётся парой путь:время (секунды). Между соседними
по времени кадрами выполняется SIFT-морф; до первого и после
последнего - статика / конец.

Пример:
  python morph_video.py --кадр a.jpg:0 --кадр b.jpg:3 --кадр c.jpg:10 --выход out.mp4

Ядро взято из E:\\Jericho\\Private\\tasks\\Morphing\\morph8.py без изменений.
"""

import argparse
import os
import sys
import time as _time

import cv2
import numpy as np
from scipy.spatial import cKDTree


_IS_TTY = sys.stderr.isatty() if hasattr(sys.stderr, "isatty") else False


def _progress(text):
    if _IS_TTY:
        sys.stderr.write("\r" + text + "\x1b[K")
    else:
        sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _fmt_progress(written, total, tag, width=20):
    if total <= 0:
        return f"[{'.' * width}] {written}/{total} {tag}"
    ratio = min(1.0, written / total)
    filled = int(ratio * width)
    if written >= total:
        bar = "=" * width
    elif filled >= width:
        bar = "=" * (width - 1) + ">"
    else:
        bar = "=" * filled + ">" + "." * (width - filled - 1)
    return f"[{bar}] {written}/{total} {tag}"


def imread_unicode(path):
    img = cv2.imread(path)
    if img is not None:
        return img
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Ошибка чтения: {e}", file=sys.stderr)
        return None


def resize_if_large(img, max_height=480):
    h, w = img.shape[:2]
    if h > max_height:
        scale = max_height / h
        new_w = int(w * scale)
        img = cv2.resize(img, (new_w, max_height), interpolation=cv2.INTER_AREA)
    return img


def fit_to_canvas(img, w, h):
    # Вписать в (w, h): масштаб с сохранением пропорций + crop по центру.
    # Лишнее уходит за края, центр остаётся центром.
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


def _polarity_ok(gray1, gray2, p1, p2, radius=6, min_corr=0.0,
                 polarity_threshold=128):
    """True если матч p1<->p2 не «переворачивает» полярность.

    Два независимых фильтра:
      1) Точечный: если gray1[y1,x1] и gray2[y2,x2] лежат по разные
         стороны от polarity_threshold (по умолч. 128) — отбрасываем.
         Т.е. тёмная точка не может матчиться со светлой.
      2) Патчевый: корреляция Пирсона патчей 13x13. Если < min_corr
         (по умолч. 0.0) — форма локального градиента инвертирована,
         отбрасываем.
    """
    h1, w1 = gray1.shape[:2]
    h2, w2 = gray2.shape[:2]
    x1, y1 = int(p1[0]), int(p1[1])
    x2, y2 = int(p2[0]), int(p2[1])

    # --- 1) точечная полярность ---
    if (0 <= x1 < w1 and 0 <= y1 < h1
            and 0 <= x2 < w2 and 0 <= y2 < h2):
        v1 = int(gray1[y1, x1])
        v2 = int(gray2[y2, x2])
        if (v1 < polarity_threshold) != (v2 < polarity_threshold):
            return False

    # --- 2) корреляция патчей ---
    if not (radius <= x1 < w1 - radius and radius <= y1 < h1 - radius):
        return True
    if not (radius <= x2 < w2 - radius and radius <= y2 < h2 - radius):
        return True
    a = gray1[y1 - radius:y1 + radius + 1,
              x1 - radius:x1 + radius + 1].astype(np.float32).ravel()
    b = gray2[y2 - radius:y2 + radius + 1,
              x2 - radius:x2 + radius + 1].astype(np.float32).ravel()
    a = a - a.mean()
    b = b - b.mean()
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-6 or nb < 1e-6:
        return True
    corr = float((a * b).sum() / (na * nb))
    return corr >= min_corr


def find_matches(img1, img2, min_dist, progress_callback=None):
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)

    sift = cv2.SIFT_create()
    keypoints1, descriptors1 = sift.detectAndCompute(gray1, None)
    keypoints2, descriptors2 = sift.detectAndCompute(gray2, None)

    if descriptors1 is None or descriptors2 is None:
        return [], []

    bf = cv2.BFMatcher(cv2.NORM_L2)
    matches = bf.knnMatch(descriptors1, descriptors2, k=2)

    good = []
    for pair in matches:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)

    if not good:
        return [], []

    combined = []
    for m in good:
        p1 = keypoints1[m.queryIdx].pt
        p2 = keypoints2[m.trainIdx].pt
        if not _polarity_ok(gray1, gray2, p1, p2):
            continue
        combined.append((m.distance, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1]))))

    combined.sort(key=lambda x: x[0])

    sel1, sel2 = [], []
    for dist, p1, p2 in combined:
        too_close = False
        for s1 in sel1:
            if np.sqrt((p1[0] - s1[0]) ** 2 + (p1[1] - s1[1]) ** 2) < min_dist:
                too_close = True
                break
        if not too_close:
            sel1.append(p1)
            sel2.append(p2)

    if progress_callback:
        progress_callback(100)

    return sel1, sel2


def morph_image(img1, img2, points1, points2, t, w, h):
    interp_points = []
    for p1, p2 in zip(points1, points2):
        ix = int((1 - t) * p1[0] + t * p2[0])
        iy = int((1 - t) * p1[1] + t * p2[1])
        interp_points.append((ix, iy))

    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    all_points1 = points1 + corners
    all_points2 = points2 + corners
    all_interp = interp_points + corners

    X, Y = np.meshgrid(np.arange(w), np.arange(h))
    pts_grid = np.column_stack((X.ravel(), Y.ravel()))

    K = min(8, len(all_interp))
    tree = cKDTree(np.array(all_interp))
    dist, idx = tree.query(pts_grid, k=K)

    weights = 1.0 / (dist + 1e-6) ** 2
    w_norm = weights / np.sum(weights, axis=1, keepdims=True)

    dx1 = np.array([p[0] for p in all_points1], dtype=np.float32)
    dy1 = np.array([p[1] for p in all_points1], dtype=np.float32)
    dx2 = np.array([p[0] for p in all_points2], dtype=np.float32)
    dy2 = np.array([p[1] for p in all_points2], dtype=np.float32)
    interp_x = np.array([p[0] for p in all_interp], dtype=np.float32)
    interp_y = np.array([p[1] for p in all_interp], dtype=np.float32)

    offset1_x = np.sum(w_norm * (dx1[idx] - interp_x[idx]), axis=1)
    offset1_y = np.sum(w_norm * (dy1[idx] - interp_y[idx]), axis=1)
    map1_x = (X.ravel() + offset1_x).reshape(h, w).astype(np.float32)
    map1_y = (Y.ravel() + offset1_y).reshape(h, w).astype(np.float32)
    warped1 = cv2.remap(img1, map1_x, map1_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)

    offset2_x = np.sum(w_norm * (dx2[idx] - interp_x[idx]), axis=1)
    offset2_y = np.sum(w_norm * (dy2[idx] - interp_y[idx]), axis=1)
    map2_x = (X.ravel() + offset2_x).reshape(h, w).astype(np.float32)
    map2_y = (Y.ravel() + offset2_y).reshape(h, w).astype(np.float32)
    warped2 = cv2.remap(img2, map2_x, map2_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)

    return cv2.addWeighted(warped1, 1 - t, warped2, t, 0)


def _parse_kadr(s):
    if ":" not in s:
        raise argparse.ArgumentTypeError(f"кадр должен быть путь:время, получено: {s}")
    path, t = s.rsplit(":", 1)
    try:
        return (path, float(t))
    except ValueError:
        raise argparse.ArgumentTypeError(f"время не число: {s}")


def parse_args():
    p = argparse.ArgumentParser(description="Видео из ключевых кадров (SIFT + IDW морфинг)")
    p.add_argument("--кадр", action="append", required=True, type=_parse_kadr,
                   metavar="ПУТЬ:ВРЕМЯ",
                   help="Кадр и его время в секундах. Повторять. Минимум 1.")
    p.add_argument("--выход", required=True, help="Выходной файл .mp4 или .avi")
    p.add_argument("--fps", type=float, default=20.0, help="Кадров в секунду (по умолч. 20)")
    p.add_argument("--макс_высота", type=int, default=480, help="Ограничение высоты (по умолч. 480)")
    p.add_argument("--детект_высота", type=int, default=768, help="Высота для SIFT-детекта (по умолч. 768). 0 = как рендер")
    p.add_argument("--мин_дист", type=int, default=0, help="min_dist NMS (0 = auto: h//12)")
    p.add_argument("--без_морфа", action="store_true",
                   help="Только crossfade, без SIFT-варпа (для всех сегментов)")
    p.add_argument("--без_морфа_сегмент", action="append", type=int, default=[],
                   metavar="N",
                   help="Индекс сегмента (0-based), где не морфить. Повторять.")
    return p.parse_args()


def _open_writer(path, fps, size):
    ext = os.path.splitext(path)[1].lower()
    codec = "XVID" if ext == ".avi" else "mp4v"
    fourcc = cv2.VideoWriter_fourcc(*codec)
    w, h = size
    return cv2.VideoWriter(path, fourcc, fps, (w, h))


def main():
    args = parse_args()

    items = list(args.кадр)
    items.sort(key=lambda x: x[1])

    if not items:
        print("Ошибка: не задан ни один кадр", file=sys.stderr)
        return 2

    orig_imgs = []
    times = []
    for p, t in items:
        if not os.path.isfile(p):
            print(f"Ошибка: нет файла {p}", file=sys.stderr)
            return 2
        im = imread_unicode(p)
        if im is None:
            print(f"Ошибка: не удалось прочитать {p}", file=sys.stderr)
            return 2
        if im.ndim == 3 and im.shape[2] == 4:
            im = cv2.cvtColor(im, cv2.COLOR_BGRA2BGR)
        orig_imgs.append(im)
        times.append(t)

    h_nat, w_nat = orig_imgs[0].shape[:2]
    if h_nat > args.макс_высота:
        h0 = args.макс_высота
        w0 = int(round(w_nat * args.макс_высота / h_nat))
    else:
        h0 = h_nat
        w0 = w_nat

    imgs = [fit_to_canvas(im, w0, h0) for im in orig_imgs]

    max_h_natural = max(im.shape[0] for im in orig_imgs)
    detect_h = min(args.детект_высота, max_h_natural) if args.детект_высота else 0

    detect_scale = 1.0
    det_imgs = imgs
    if detect_h > h0:
        detect_scale = detect_h / h0
        det_w = int(round(w0 * detect_scale))
        det_imgs = [fit_to_canvas(im, det_w, detect_h) for im in orig_imgs]
        print(f"Детект SIFT на {det_w}x{detect_h}, рендер на {w0}x{h0} (масштаб {detect_scale:.3f})",
              file=sys.stderr)

    out_path = os.path.abspath(args.выход)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    writer = _open_writer(out_path, args.fps, (w0, h0))
    if not writer.isOpened():
        print(f"Ошибка: не удалось открыть VideoWriter для {out_path}", file=sys.stderr)
        return 3

    _no_morph_set = set(int(x) for x in (args.без_морфа_сегмент or []))

    n_seg = len(imgs) - 1
    seg_plan = []
    for seg in range(n_seg):
        dur = times[seg + 1] - times[seg]
        if dur <= 0:
            print(f"Пропуск: время не растёт ({times[seg]} -> {times[seg + 1]})", file=sys.stderr)
            continue
        seg_plan.append((seg, max(1, int(round(args.fps * dur)))))

    n_start = int(round(args.fps * times[0])) if times[0] > 0 else 0
    total_frames = n_start + sum(n for _, n in seg_plan) + 1

    t_start = _time.time()
    written = 0

    static_label = f"статика {os.path.basename(items[0][0])}"

    try:
        if n_start:
            _progress(_fmt_progress(0, total_frames, static_label))
            for _ in range(n_start):
                writer.write(imgs[0])
                written += 1
                _progress(_fmt_progress(written, total_frames, static_label))

        for seg, n in seg_plan:
            img1 = imgs[seg]
            img2 = imgs[seg + 1]
            label = f"{os.path.basename(items[seg][0])} -> {os.path.basename(items[seg + 1][0])}"

            if args.без_морфа or seg in _no_morph_set:
                pts1, pts2 = [], []
            else:
                det_h_nms = det_imgs[0].shape[0]
                min_dist = args.мин_дист or max(5, det_h_nms // 12)
                _progress(_fmt_progress(written, total_frames, f"детект {label}"))
                pts1, pts2 = find_matches(det_imgs[seg], det_imgs[seg + 1], min_dist=min_dist)
                if detect_scale != 1.0:
                    inv = 1.0 / detect_scale
                    pts1 = [(int(round(x * inv)), int(round(y * inv))) for x, y in pts1]
                    pts2 = [(int(round(x * inv)), int(round(y * inv))) for x, y in pts2]
                label += f" (точек: {len(pts1)})"

            for j in range(n):
                t = j / n
                if len(pts1) >= 3:
                    frame = morph_image(img1, img2, pts1, pts2, t, w0, h0)
                else:
                    frame = cv2.addWeighted(img1, 1 - t, img2, t, 0)
                writer.write(frame)
                written += 1
                _progress(_fmt_progress(written, total_frames, label))

        writer.write(imgs[-1])
        written += 1
        _progress(_fmt_progress(written, total_frames, "финал"))
    finally:
        writer.release()
        if _IS_TTY:
            sys.stderr.write("\n")

    dt = _time.time() - t_start
    print(f"Готово: {out_path} - {written} кадров за {dt:.1f} с", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())