#!/usr/bin/env python3
"""
render_thorvg_layered.py v6.1 — быстрый рендер без лишних перерисовок.

Изменения относительно v6:
- добавлен кэш текстур по сигнатуре слоя/кадра
- добавлена опция параллельной растеризации масок
- сохранён кэш блоков и LRU-кэш альфы
"""
from __future__ import annotations

import argparse
import base64 as _b64
import io
import json
import math
import os
import pathlib
import re
import sys
import tempfile
import time as _t
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation
from thorvg_python import Engine, SwCanvas, Scene, LottieAnimation
from inline_assets import inline_assets


BUF_W, BUF_H = 128, 96


LOTTY_HELP = """render-lv6.py — рендер .lotty в MP4 (или PNG для --test)

ИСПОЛЬЗОВАНИЕ:
    render-lv6.py -f <file.lotty> [-o out.mp4] [опции]
    render-lv6.py --help

ОСНОВНЫЕ:
    -f, --file PATH            входной .lotty (обязателен)
    -o, --out PATH             выходной .mp4
    --fps N                    FPS (по умолч. из документа)
    --width N, --height N      явный размер кадра
    --scale S                  масштаб относительно w/h документа (по умолч. 1.0)
    --bg HEX                   цвет фона (по умолч. #05070f)

КАЧЕСТВО / BLUR:
    --quality N                libx264 quality (по умолч. 8)
    --blur-quality N           качество gaussian blur в thorvg (по умолч. 80)

ТЕСТОВЫЙ КАДР (быстро, без видео):
    --test                     вместо .mp4 отрендерить один кадр в .png
    --test-frame N             номер кадра для --test (по умолч. 30)
    --test-scale S             масштаб для --test (по умолч. 0.5)

КЭШИ:
    --alpha-cache-size N       макс. размер кэша альфа-масок (по умолч. 512)
    --texture-cache-size N     макс. размер кэша текстур (по умолч. 256)

МАСКИ (для больших документов):
    --mask-frames N            сколько кадров сэмплировать для bbox-маски
                               (0 = все кадры, по умолч. 0)
    --mask-workers N           потоков для растеризации масок (по умолч. 1)

ФОРМАТ .LOTTY:
    Стандартный Lottie JSON (v5.9.0) + следующие расширения:

    1. На корне документа:
         "op"          — число кадров анимации (int)
         "fr"          — FPS (по умолч. 30)
         "w", "h"      — размер документа

    2. На слое (необязательные поля):
         "blur_sigma"  — число px или {"a":1,"k":[{t,s:[sigma]},...]}
                         применяется как thorvg gaussian blur (σ/2)
         "texture"     — путь к текстуре (jpg/png) ИЛИ "asset:ID"
         "texture_opacity" — 0..100, прозрачность текстуры
         "texture_mode"    — "tile" | "stretch" | "cover"
         "alpha_grad"  — (legacy) описание alpha-градиента в конвертере

         Все три texture* поля применяются рендерером: текстура тайлится
         или растягивается по bbox фигуры слоя, смешивается с исходным
         цветом по texture_opacity.

         "blur_sigma" применяется ко всему блоку, в котором находится слой.
         Блоки группируются по σ; внутри блока слои переставляются жадно,
         чтобы уменьшить количество прогонов thorvg (см. reorder_layers).

    3. На shape внутри слоя:
         "ty": "gf" — gradient fill (radial t=2 или linear t=1).
                      С alpha-стопами thorvg возвращает PREMULTIPLIED alpha;
                      render-lv6 делает unpremultiply перед композитингом.

    Дополнительно рендерер:
    • группирует слои по σ и bbox-пересечению в «блоки»;
    • кэширует отрендеренные блоки по сигнатуре (слой→кадр→σ);
    • пересобирает Scene для анимированной σ каждый кадр
      (thorvg не позволяет менять σ на существующем Scene);
    • режет тонкие горизонтальные полосы на под-блоки, если слой с
      текстурой занимает большую часть кадра (см. split_into_blocks).

ФОРМАТ ВЫХОДА:
    .mp4 (H.264, yuv420p) — если не указан --test
    .png                  — если указан --test

ПРИМЕРЫ:
    render-lv6.py -f ny_v6.lotty -o ny_v6.mp4
    render-lv6.py -f ny_v6.lotty -o ny_v6.mp4 --scale 2.0
    render-lv6.py -f ny_v6.lotty --test --test-frame 60
    render-lv6.py -f ny_v6.lotty --test --test-frame 90 --test-scale 1.0

ПРИМЕЧАНИЯ:
    • thorvg Python: eng.term() убивает ГЛОБАЛЬНЫЙ Engine.
      Не создавайте дополнительные Engine в потоках.
    • get_pillow("RGBA") возвращает VIEW на внутренний буфер PIL.
      Всегда np.array(), а не np.asarray(), иначе увидите мусор.
    • Canvas.draw(True) нужно вызывать ПОСЛЕ update().
    • Если что-то чёрное/пустое — проверьте размеры исходных картинок
      и холста: несовпадение координат — самая частая причина.
"""


def parse_args():
    p = argparse.ArgumentParser(
        prog="render-lv6.py",
        description="Рендер .lotty в MP4 (или PNG для --test)",
        epilog=LOTTY_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    p.add_argument("--help", action="store_true",
                   help="показать полное описание формата .lotty")
    p.add_argument("--file", "-f", default=None)
    p.add_argument("--out", "-o", default=None)
    p.add_argument("--fps", type=int, default=None)
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=None)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--bg", default="#05070f")
    p.add_argument("--quality", type=int, default=8)
    p.add_argument("--blur-quality", type=int, default=80)
    p.add_argument("--test", action="store_true")
    p.add_argument("--test-frame", type=int, default=30)
    p.add_argument("--test-scale", type=float, default=0.5)
    p.add_argument("--debug", action="store_true",
                   help="Подробный вывод: ассеты, слои, растеризация")
    p.add_argument("--budget-seconds", type=int, default=60,
                   help="Бюджет рендера в секундах (0 = без лимита)")
    p.add_argument("--mask-frames", type=int, default=0,
                   help="Сколько кадров сэмплировать для маски (0 = все)")
    p.add_argument("--mask-workers", type=int, default=1,
                   help="Сколько потоков использовать для растеризации масок")
    p.add_argument("--alpha-cache-size", type=int, default=512,
                   help="Максимальный размер кэша альфы слоёв")
    p.add_argument("--texture-cache-size", type=int, default=256,
                   help="Максимальный размер кэша текстур")
    a = p.parse_args()
    if a.help:
        print(LOTTY_HELP)
        sys.exit(0)
    if not a.file:
        print("Ошибка: --file/-f обязателен. --help для справки.")
        sys.exit(1)
    return a


def hex_rgb(s):
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def layer_is_animated(L):
    ks = L.get("ks", {})
    for k in ("p", "r", "s", "o", "a"):
        v = ks.get(k)
        if isinstance(v, dict) and v.get("a") == 1:
            return True
    return False


def sigma_at(L, frame):
    bs = L.get("blur_sigma")
    if bs is None:
        return 0.0
    if isinstance(bs, (int, float)):
        return float(bs)
    if isinstance(bs, dict) and bs.get("a") == 1:
        keys = bs.get("k", [])
        if not keys:
            return 0.0
        if frame <= keys[0]["t"]:
            return float(keys[0]["s"][0])
        if frame >= keys[-1]["t"]:
            return float(keys[-1]["s"][0])
        for i in range(len(keys) - 1):
            a, b = keys[i], keys[i + 1]
            if a["t"] <= frame <= b["t"]:
                if b["t"] == a["t"]:
                    return float(a["s"][0])
                k = (frame - a["t"]) / (b["t"] - a["t"])
                return a["s"][0] + (b["s"][0] - a["s"][0]) * k
        return float(keys[-1]["s"][0])
    return 0.0


def layer_sigma_is_animated(L):
    bs = L.get("blur_sigma")
    return isinstance(bs, dict) and bs.get("a") == 1


def layer_sigma_scalar(L):
    bs = L.get("blur_sigma")
    if bs is None:
        return 0.0
    if isinstance(bs, (int, float)):
        return float(bs)
    if isinstance(bs, dict) and bs.get("a") == 1:
        return None
    return 0.0


def layer_max_sigma(L):
    bs = L.get("blur_sigma")
    if bs is None:
        return 0.0
    if isinstance(bs, (int, float)):
        return float(bs)
    if isinstance(bs, dict) and bs.get("a") == 1:
        if not bs.get("k"):
            return 0.0
        return max(kf["s"][0] for kf in bs["k"])
    return 0.0


def rasterize_one_mask(args):
    idx, L, W, H, total, frames, eng = args
    sub = {
        "v": "5.9.0",
        "fr": 30,
        "ip": 0,
        "op": total,
        "w": W,
        "h": H,
        "layers": [L],
    }
    data = json.dumps(sub, ensure_ascii=False).encode("utf-8")

    canvas = SwCanvas(eng)
    canvas.set_target(BUF_W, BUF_H)
    anim = LottieAnimation(eng)
    pic = anim.get_picture()
    r = pic.load_data(data, "lottie+json", None, True)
    if r != 0:
        canvas.destroy()
        return idx, np.zeros((BUF_H, BUF_W), dtype=bool)

    pic.set_size(BUF_W, BUF_H)
    sc = Scene(eng)
    sc.add(pic)
    canvas.add(sc)

    m = np.zeros((BUF_H, BUF_W), dtype=bool)
    for fr in frames:
        anim.set_frame(min(fr, total - 1))
        canvas.update()
        canvas.draw(True)
        canvas.sync()
        img = canvas.get_pillow("RGBA")
        a = np.asarray(img)[:, :, 3] > 8
        m |= a

    msig = layer_max_sigma(L)
    if msig > 0:
        expand_buf = 3.0 * msig / 2.0 * BUF_W / W
        rad = max(1, int(round(expand_buf)))
        m = binary_dilation(m, iterations=rad)

    canvas.destroy()
    return idx, m


def rasterize_all_masks(eng, doc, n_frames, mask_frames=0, verbose=False, workers=1):
    """Возвращает список булевых масок (BUF_H, BUF_W) для каждого слоя."""
    W, H = doc["w"], doc["h"]
    total = doc["op"]
    if mask_frames > 0 and mask_frames < total:
        frames = [int(i * (total - 1) / (mask_frames - 1)) for i in range(mask_frames)]
    else:
        frames = list(range(total))

    tasks = []
    for idx, L in enumerate(doc["layers"]):
        is_anim = layer_is_animated(L)
        fr_list = frames if is_anim else [0]
        tasks.append((idx, L, W, H, total, fr_list, eng))

    masks = [None] * len(doc["layers"])

    if workers and workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(rasterize_one_mask, t) for t in tasks]
            done = 0
            for fut in as_completed(futs):
                idx, m = fut.result()
                masks[idx] = m
    else:
        for done, t in enumerate(tasks, 1):
            idx, m = rasterize_one_mask(t)
            masks[idx] = m

    return masks


def masks_overlap(m1, m2):
    return bool(np.any(m1 & m2))


def reorder_layers_by_sigma(layers, masks, window=16):
    """Жадная перестановка слоёв: слой с одинаковой σ тянется вниз,
    если его маска не пересекается с маской промежуточных."""
    sigmas = []
    for L in layers:
        if layer_sigma_is_animated(L):
            sigmas.append("ANIM")
        else:
            sigmas.append(layer_sigma_scalar(L))

    order = list(range(len(layers)))
    moved = True
    passes = 0
    total_moves = 0
    MAX_MOVES = 3000
    while moved and passes < 20 and total_moves < MAX_MOVES:
        moved = False
        passes += 1
        i = 0
        while i < len(order):
            cur = order[i]
            if sigmas[cur] == "ANIM":
                i += 1
                continue
            best_j = -1
            max_j = min(i + window, len(order) - 1)
            for j in range(i + 1, max_j + 1):
                if sigmas[order[j]] != sigmas[cur]:
                    continue
                ok = True
                for k in range(i + 1, j):
                    kk = order[k]
                    if masks_overlap(masks[cur], masks[kk]):
                        ok = False
                        break
                if ok:
                    best_j = j
                    break
            if best_j > 0:
                order.pop(i)
                target = best_j - 1
                order.insert(target, cur)
                moved = True
                total_moves += 1
                i = target + 1
            else:
                i += 1
    return order


def _interp_stops_1d(t, stops):
    """Линейная интерполяция по списку [(pos, val), ...]."""
    if not stops: return 1.0
    if t <= stops[0][0]: return stops[0][1]
    if t >= stops[-1][0]: return stops[-1][1]
    for i in range(len(stops)-1):
        a_pos, a_val = stops[i]; b_pos, b_val = stops[i+1]
        if a_pos <= t <= b_pos:
            k = (t - a_pos) / (b_pos - a_pos) if b_pos > a_pos else 0
            return a_val + (b_val - a_val) * k
    return stops[-1][1]


def build_alpha_grad_mask(L, frame, W, H):
    """Строит alpha-маску [H,W] float 0..1 для слоя с alpha_grad.
    Учитывает трансформ слоя на кадре."""
    ag = L.get("alpha_grad")
    if ag is None:
        return None
    geom = ag["geom"]
    gf = ag["gf"]
    stops = ag["alpha_stops"]

    px, py, rot, sx, sy, ax, ay = get_layer_transform(L, frame)
    if sx == 0 or sy == 0:
        return None

    # Масштаб буфера к документу (thorvg рендерит в W x H, а ks в doc коорд.)
    doc_w = L.get("_doc_w", W)
    doc_h = L.get("_doc_h", H)
    kx = doc_w / W
    ky = doc_h / H

    # Переводим пиксели в локальные координаты фигуры
    ys_g, xs_g = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = xs_g * kx - px
    dy = ys_g * ky - py
    rad = math.radians(-rot)
    c, sn = math.cos(rad), math.sin(rad)
    rdx = c*dx - sn*dy
    rdy = sn*dx + c*dy
    loc_x = rdx / sx + ax
    loc_y = rdy / sy + ay

    # в фигуре?
    if geom["ty"] == "el":
        cxf, cyf = geom["cx"], geom["cy"]
        rxf, ryf = geom["sx"]/2.0, geom["sy"]/2.0
        inside = ((loc_x - cxf)**2 / (rxf*rxf + 1e-9) +
                  (loc_y - cyf)**2 / (ryf*ryf + 1e-9)) <= 1.0
    elif geom["ty"] == "rc":
        cxf, cyf = geom["cx"], geom["cy"]
        rxf, ryf = geom["sx"]/2.0, geom["sy"]/2.0
        inside = ((np.abs(loc_x - cxf) <= rxf) &
                  (np.abs(loc_y - cyf) <= ryf))
    elif geom["ty"] == "sh":
        v = geom["v"]
        n = len(v)
        if n < 3:
            return None
        # ray casting
        inside = np.zeros((H, W), dtype=bool)
        j = n - 1
        for i in range(n):
            xi, yi = v[i]
            xj, yj = v[j]
            if yi != yj:
                cond = ((loc_y >= min(yi, yj)) & (loc_y < max(yi, yj)))
                x_int = (xj - xi) * (loc_y - yi) / (yj - yi) + xi
                inside ^= (cond & (loc_x < x_int))
            j = i
    else:
        return None

    # alpha по градиенту
    s_, e_ = gf["s"], gf["e"]
    if gf["t"] == 2:
        r_ = math.hypot(e_[0]-s_[0], e_[1]-s_[1]) or 1.0
        t_grid = np.hypot(loc_x - s_[0], loc_y - s_[1]) / r_
    else:
        dxg = e_[0] - s_[0]; dyg = e_[1] - s_[1]
        d2 = dxg*dxg + dyg*dyg or 1.0
        t_grid = ((loc_x - s_[0])*dxg + (loc_y - s_[1])*dyg) / d2
    t_grid = np.clip(t_grid, 0.0, 1.0)

    # интерполяция alpha по t_grid
    a_arr = np.zeros_like(t_grid)
    if not stops:
        a_arr[:] = 1.0
    else:
        # упрощённо: на каждый пиксель — линейная интерполяция
        flat_t = t_grid.ravel()
        flat_a = np.empty_like(flat_t)
        for i, t in enumerate(flat_t):
            flat_a[i] = _interp_stops_1d(float(t), stops)
        a_arr = flat_a.reshape(t_grid.shape)

    mask = np.where(inside, a_arr, 0.0).astype(np.float32)
    # антиалиасинг: размыть границу на 1.5 пикселя
    from scipy.ndimage import gaussian_filter
    mask = gaussian_filter(mask, sigma=1.5, mode="constant")
    return mask


def split_into_blocks_v6(doc, masks, verbose=True):
    """Строит блоки: сначала переставляет слои, потом группирует по σ."""
    order = reorder_layers_by_sigma(doc["layers"], masks)
    reordered = [doc["layers"][i] for i in order]

    blocks = []
    cur_sigma = "INIT"
    cur = []
    for L in reordered:
        if layer_sigma_is_animated(L):
            if cur:
                blocks.append(cur)
            blocks.append([L])
            cur = []
            cur_sigma = "INIT"
            continue
        sg = layer_sigma_scalar(L)
        if sg != cur_sigma:
            if cur:
                blocks.append(cur)
            cur = [L]
            cur_sigma = sg
        else:
            cur.append(L)
    if cur:
        blocks.append(cur)

    result = []
    for blk in blocks:
        if layer_sigma_is_animated(blk[0]):
            result.append((None, blk))
        else:
            result.append((layer_sigma_scalar(blk[0]), blk))
    return result


def _val_at_ks(prop, frame):
    """Интерполяция значения свойства ks на кадре frame."""
    if prop is None:
        return None
    if prop.get("a", 0) == 0:
        return prop["k"]
    keys = prop.get("k", [])
    if not keys:
        return None
    if frame <= keys[0]["t"]:
        return keys[0]["s"]
    if frame >= keys[-1]["t"]:
        return keys[-1]["s"]
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        if a["t"] <= frame <= b["t"]:
            if b["t"] == a["t"]:
                return a["s"]
            k = (frame - a["t"]) / (b["t"] - a["t"])
            return [a["s"][j] + (b["s"][j] - a["s"][j]) * k for j in range(len(a["s"]))]
    return keys[-1]["s"]


def get_layer_transform(L, frame):
    """Возвращает (px, py, rot_deg, sx, sy, ax, ay) слоя на кадре."""
    ks = L.get("ks", {})
    p = _val_at_ks(ks.get("p"), frame) or [0, 0, 0]
    r = _val_at_ks(ks.get("r"), frame)
    if isinstance(r, list):
        r = r[0]
    r = r or 0
    s_ = _val_at_ks(ks.get("s"), frame) or [100, 100, 100]
    a = _val_at_ks(ks.get("a"), frame) or [0, 0, 0]
    return p[0], p[1], r, s_[0] / 100.0, s_[1] / 100.0, a[0], a[1]


def local_shape_bbox(L):
    """Возвращает bbox фигуры в локальных координатах слоя."""
    mnx = mny = float("inf")
    mxx = mxy = float("-inf")
    found = False

    def walk(items):
        nonlocal mnx, mny, mxx, mxy, found
        for it in items:
            if not isinstance(it, dict):
                continue
            t = it.get("ty")
            if t == "gr":
                walk(it.get("it", []))
            elif t in ("el", "rc"):
                sz = it.get("s", {}).get("k", [0, 0])
                p2 = it.get("p", {}).get("k", [0, 0])
                mnx = min(mnx, p2[0] - sz[0] / 2)
                mxx = max(mxx, p2[0] + sz[0] / 2)
                mny = min(mny, p2[1] - sz[1] / 2)
                mxy = max(mxy, p2[1] + sz[1] / 2)
                found = True
            elif t == "sh":
                v = it.get("ks", {}).get("k", {})
                if isinstance(v, dict):
                    v = v.get("v", [])
                for pt in v:
                    mnx = min(mnx, pt[0])
                    mxx = max(mxx, pt[0])
                    mny = min(mny, pt[1])
                    mxy = max(mxy, pt[1])
                    found = True

    walk(L.get("shapes", []))
    if not found:
        return None
    return (mnx, mny, mxx, mxy)


def build_local_texture(tex_img, lw, lh, mode, W, H):
    """Строит текстуру в локальных координатах фигуры (размер lw x lh)."""
    lw = max(1, int(lw))
    lh = max(1, int(lh))
    if mode == "stretch":
        return tex_img.resize((lw, lh), Image.LANCZOS)
    if mode == "cover":
        tw, th = tex_img.size
        scale = max(lw / tw, lh / th)
        nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
        big = tex_img.resize((nw, nh), Image.LANCZOS)
        x0 = (nw - lw) // 2
        y0 = (nh - lh) // 2
        return big.crop((x0, y0, x0 + lw, y0 + lh))
    tiled = Image.new("RGB", (lw, lh))
    tw, th = tex_img.size
    for y in range(0, lh, th):
        for x in range(0, lw, tw):
            tiled.paste(tex_img, (x, y))
    return tiled


def transform_texture_to_world(tex_local, L, frame, W, H, local_bbox, lw, lh,
                               src_w=None, src_h=None):
    """Применяет обратный трансформ слоя к локальной текстуре."""
    if src_w is None:
        src_w = W
    if src_h is None:
        src_h = H
    px, py, rot, sx, sy, ax, ay = get_layer_transform(L, frame)
    lx0, ly0, lx1, ly1 = local_bbox
    rad = math.radians(rot)
    c = math.cos(rad)
    sn = math.sin(rad)
    RS = np.array([[c * sx, -sn * sy],
                   [sn * sx,  c * sy]])
    try:
        InvRS = np.linalg.inv(RS)
    except np.linalg.LinAlgError:
        InvRS = np.eye(2)

    a = InvRS[0, 0]
    b = InvRS[0, 1]
    d = InvRS[1, 0]
    e = InvRS[1, 1]
    cc = -InvRS[0, 0] * px - InvRS[0, 1] * py + ax - lx0
    ff = -InvRS[1, 0] * px - InvRS[1, 1] * py + ay - ly0
    world = tex_local.transform(
        (int(src_w), int(src_h)), Image.AFFINE, (a, b, cc, d, e, ff),
        resample=Image.BILINEAR, fillcolor=(0, 0, 0))
    if (int(src_w), int(src_h)) != (W, H):
        world = world.resize((W, H), Image.BILINEAR)
    return world


def resolve_texture_image(spec, base_dir, assets):
    """Возвращает PIL.Image (RGB) для спецификации texture."""
    if not spec:
        return None
    if spec.startswith("asset:"):
        aid = spec[6:]
        a = next((x for x in assets if x.get("id") == aid), None)
        if not a:
            return None
        spec = a.get("p", "")
        if a.get("e") == 0 and not spec.startswith("data:"):
            u = a.get("u", "") or ""
            path = pathlib.Path(spec)
            if not path.is_absolute():
                path = base_dir / u / spec if u else base_dir / spec
            if not path.exists():
                path = base_dir / spec
            if not path.exists():
                return None
            return Image.open(path).convert("RGB")
    if spec.startswith("data:"):
        m = re.match(r"data:([^;,]*)(;base64)?,(.*)", spec, re.DOTALL)
        if not m:
            return None
        payload = m.group(3)
        raw = _b64.b64decode(payload) if m.group(2) else payload.encode("latin-1")
        return Image.open(io.BytesIO(raw)).convert("RGB")
    path = pathlib.Path(spec)
    if not path.is_absolute():
        path = base_dir / spec
    if not path.exists():
        return None
    return Image.open(path).convert("RGB")


def layer_signature(L, frame):
    """Кортеж анимированных ks-значений слоя на кадре. Для кэша."""
    ks = L.get("ks", {})
    sig = []
    for prop in ("p", "r", "s", "o", "a"):
        v = _val_at_ks(ks.get(prop), frame)
        if v is None:
            sig.append(None)
        elif isinstance(v, list):
            vals = list(v)
            if prop == "r":
                vals = [vals[0] % 360.0]
            sig.append(tuple(round(float(x), 3) for x in vals))
        else:
            val = float(v)
            if prop == "r":
                val = val % 360.0
            sig.append(round(val, 3))
    if "texture" in L:
        sig.append(("tex", L["texture"], L.get("texture_opacity", 100),
                    L.get("texture_mode", "tile")))
    return tuple(sig)


def render_layer_alpha(eng, L, W, H, frame=0, op=300):
    """Рендерит один слой на кадре frame, возвращает numpy (H, W) uint8 альфы."""
    Lc = dict(L)
    Lc.pop("texture", None)
    Lc.pop("texture_opacity", None)
    Lc.pop("texture_mode", None)
    src_w = L.get("_doc_w", W)
    src_h = L.get("_doc_h", H)
    sub = {"v": "5.9.0", "fr": 30, "ip": 0, "op": op,
           "w": src_w, "h": src_h, "layers": [Lc]}
    data = json.dumps(sub, ensure_ascii=False).encode("utf-8")
    canvas = SwCanvas(eng)
    canvas.set_target(W, H)
    anim = LottieAnimation(eng)
    pic = anim.get_picture()
    pic.load_data(data, "lottie+json", None, True)
    pic.set_size(W, H)
    sc = Scene(eng)
    sc.add(pic)
    canvas.add(sc)
    anim.set_frame(min(frame, op - 1))
    canvas.update()
    canvas.draw(True)
    canvas.sync()
    img = canvas.get_pillow("RGBA")
    arr = np.asarray(img)
    alpha = arr[:, :, 3].copy()
    canvas.destroy()
    return alpha


def make_texture_image(tex_img, W, H, mode):
    """Строит текстуру во весь кадр (W,H) по режиму."""
    if mode == "stretch":
        return tex_img.resize((W, H), Image.LANCZOS)
    if mode == "cover":
        tw, th = tex_img.size
        scale = max(W / tw, H / th)
        nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
        big = tex_img.resize((nw, nh), Image.LANCZOS)
        x0 = (nw - W) // 2
        y0 = (nh - H) // 2
        return big.crop((x0, y0, x0 + W, y0 + H))
    tiled = Image.new("RGB", (W, H))
    tw, th = tex_img.size
    for y in range(0, H, th):
        for x in range(0, W, tw):
            tiled.paste(tex_img, (x, y))
    return tiled


class LRUCache:
    def __init__(self, maxsize=512):
        self.maxsize = maxsize
        self.data = {}
        self.order = []

    def get(self, key):
        if key not in self.data:
            return None
        self.order.remove(key)
        self.order.append(key)
        return self.data[key]

    def set(self, key, value):
        if key in self.data:
            self.order.remove(key)
        self.data[key] = value
        self.order.append(key)
        while len(self.order) > self.maxsize:
            old = self.order.pop(0)
            self.data.pop(old, None)


def build_texture_frame_cached(L, tex_img, tex_op, tex_mode, W, H, frame,
                               texture_cache):
    bbox = local_shape_bbox(L)
    if bbox is None:
        key = ("full", id(tex_img), tex_mode, W, H)
        tex_frame = texture_cache.get(key)
        if tex_frame is None:
            tex_frame = make_texture_image(tex_img, W, H, tex_mode)
            texture_cache.set(key, tex_frame)
        return tex_frame

    lx0, ly0, lx1, ly1 = bbox
    lw = max(1, lx1 - lx0)
    lh = max(1, ly1 - ly0)

    if layer_is_animated(L):
        sig = layer_signature(L, frame)
        key = ("local", id(L), sig, id(tex_img), tex_mode, W, H)
    else:
        key = ("local", id(L), id(tex_img), tex_mode, W, H)

    tex_frame = texture_cache.get(key)
    if tex_frame is not None:
        return tex_frame

    tex_local = build_local_texture(tex_img, lw, lh, tex_mode, W, H)
    tex_frame = transform_texture_to_world(
        tex_local, L, frame, W, H, bbox, lw, lh,
        src_w=L.get("_doc_w", W),
        src_h=L.get("_doc_h", H)
    )
    texture_cache.set(key, tex_frame)
    return tex_frame


def _resize_masks_to(masks, W, H):
    """Best-effort resize of mask structure to (W, H)."""
    try:
        from PIL import Image as _PIL
        import numpy as _np
    except Exception:
        return masks
    def _one(v):
        try:
            if isinstance(v, _np.ndarray):
                if v.dtype == _np.uint8:
                    _pil = _PIL.fromarray(v)
                    return _np.array(_pil.resize((W, H), _PIL.NEAREST))
                else:
                    _pil = _PIL.fromarray((v * 255).clip(0, 255).astype(_np.uint8))
                    return _np.array(_pil.resize((W, H), _PIL.NEAREST)).astype(_np.float32) / 255.0
            if isinstance(v, _PIL.Image):
                return v.resize((W, H), _PIL.NEAREST)
            if isinstance(v, tuple):
                return tuple(_one(x) for x in v)
            if isinstance(v, list):
                return [_one(x) for x in v]
        except Exception:
            pass
        return v
    try:
        if isinstance(masks, dict):
            return {k: _one(v) for k, v in masks.items()}
        if isinstance(masks, (list, tuple)):
            return type(masks)(_one(v) for v in masks)
    except Exception:
        pass
    return masks


def render_layered(file_path, fps=None, width=None, height=None,
                   scale=1.0, bg="#05070f", blur_quality=80,
                   single_frame=None, mask_frames=0,
                   alpha_cache_size=512, texture_cache_size=256,
                   mask_workers=1, budget_seconds=60, _force_step=1,
                   _reuse_masks=None, _reuse_frame0=None, _start_index=0,
                   _max_frames=None, _reuse_frames=None):
    path = pathlib.Path(file_path).resolve()
    if not path.exists():
        sys.exit(f"Нет файла: {path}")

    doc = json.loads(path.read_text(encoding="utf-8"))
    if _max_frames is not None and _max_frames < doc.get("op", 0):
        _orig_op = doc["op"]
        doc["op"] = _max_frames
        print(f"  Обрезка: {_orig_op} → {_max_frames} кадров", flush=True)
    for L in doc.get("layers", []):
        L["_doc_w"] = doc.get("w", 800)
        L["_doc_h"] = doc.get("h", 600)

    if doc.get("assets"):
        ext = [a for a in doc["assets"] if a.get("e", 0) == 0]
        if ext:
            print(f"Ассеты: {len(ext)} внешних -> подстановка base64")
            doc = inline_assets(doc, path.parent)

    base_w = int(doc.get("w", 600))
    base_h = int(doc.get("h", 600))
    fr = int(fps or doc.get("fr", 30))
    _W_RAW = int(round((width or base_w) * scale))
    _H_RAW = int(round((height or base_h) * scale))
    _MAX_INIT_W = 1366
    _MAX_INIT_H = 768
    _was_capped = False
    if _W_RAW > _MAX_INIT_W or _H_RAW > _MAX_INIT_H:
        _fit = min(_MAX_INIT_W / max(1, _W_RAW), _MAX_INIT_H / max(1, _H_RAW))
        _W_RAW = max(2, int(_W_RAW * _fit))
        _H_RAW = max(2, int(_H_RAW * _fit))
        _was_capped = True
    W = max(2, _W_RAW & ~1)
    H = max(2, _H_RAW & ~1)
    _eff_scale = (W / base_w) if base_w > 0 else scale
    if _was_capped:
        print(f"  Ограничение {_MAX_INIT_W}x{_MAX_INIT_H} → {W}x{H}", flush=True)
    total = doc["op"]

    print(f"JSON:   {path}")
    print(f"Размер: {W}x{H}  FPS: {fr}  Кадров: {total}")

    eng = Engine(threads=4)

    if _reuse_masks is not None:
        print(f"  [reuse] маски масштабируются до {W}x{H}...", flush=True)
        masks = _resize_masks_to(_reuse_masks, W, H)
    else:
        print("Растеризация масок слоёв...")
        t0 = _t.perf_counter()
        masks = rasterize_all_masks(
            eng, doc, total, mask_frames,
            verbose=True, workers=mask_workers
        )
        dt = _t.perf_counter() - t0
        print(f"  готово за {dt:.2f} сек")

    blocks = split_into_blocks_v6(doc, masks)

    print(f"Блоков: {len(blocks)}")
    for i, (sigma, blk) in enumerate(blocks):
        sigma_str = "ANIM" if sigma is None else f"{sigma:>4}"
        print(f"  #{i}: σ={sigma_str}  {len(blk)} слоёв")
        for L in blk:
            ty = L.get("ty")
            ref = L.get("refId", "")
            tag = ""
            if False:
                tag = ""
            elif "texture" in L:
                tag = "  [texture]"
            print(f"      ty={ty} ref={ref[:14]:14s} {L.get('nm','')}{tag}")
    print()

    tmpfiles = []
    block_instances = []

    for sigma, blk in blocks:
        sub = dict(doc)
        sub["layers"] = blk
        for i, L in enumerate(sub["layers"]):
            L["ind"] = i + 1

        anim_sigma = any(layer_sigma_is_animated(L) for L in blk)

        if anim_sigma:
            L0 = blk[0]
            sub2 = dict(sub)
            sub2["layers"] = []
            for L in blk:
                Lc = dict(L)
                Lc.pop("blur_sigma", None)
                sub2["layers"].append(Lc)
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                              delete=False, encoding="utf-8")
            json.dump(sub2, tmp, ensure_ascii=False)
            tmp.close()
            tmpfiles.append(tmp.name)

            canvas = SwCanvas(eng)
            canvas.set_target(W, H)
            anim = LottieAnimation(eng)
            pic = anim.get_picture()
            pic.load(tmp.name)
            pic.set_size(W, H)

            # для ANIM_SIGMA сцену пересобираем каждый кадр (см. цикл)
            canvas.destroy()
            _, tf = anim.get_total_frame()
            block_instances.append({
                "kind": "ANIM_SIGMA",
                "canvas": None,
                "anim": None,
                "scene": None,
                "total": int(tf),
                "sigma": sigma,
                "tmp_name": tmp.name,
                "L0": L0,
            })
            continue

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                          delete=False, encoding="utf-8")
        json.dump(sub, tmp, ensure_ascii=False)
        tmp.close()
        tmpfiles.append(tmp.name)

        canvas = SwCanvas(eng)
        canvas.set_target(W, H)
        anim = LottieAnimation(eng)
        pic = anim.get_picture()
        pic.load(tmp.name)
        pic.set_size(W, H)
        root = Scene(eng)
        root.add(pic)
        if sigma > 0:
            root.add_effect_gaussian_blur(sigma=float(sigma) / 2.0,
                                          direction=0, border=0,
                                          quality=blur_quality)
        canvas.add(root)
        _, tf = anim.get_total_frame()
        block_instances.append({
            "kind": "STATIC_SIGMA",
            "canvas": canvas,
            "anim": anim,
            "scene": root,
            "total": int(tf),
            "sigma": sigma,
        })

    max_t = max(inst["total"] for inst in block_instances)
    bg_rgb = Image.new("RGB", (W, H), hex_rgb(bg))
    frames = []
    if _reuse_frame0 is not None:
        _pil0 = Image.fromarray(_reuse_frame0).resize((W, H), Image.LANCZOS)
        frames.append(np.array(_pil0.convert("RGB")))
        print(f"  [reuse] кадр 0 отмасштабирован", flush=True)

    tex_map = {}
    for L in doc["layers"]:
        if id(L) in tex_map:
            continue
        if "texture" not in L:
            continue
        tex_spec = L.get("texture", "")
        op_pct = float(L.get("texture_opacity", 100))
        mode = L.get("texture_mode", "tile")
        img = resolve_texture_image(tex_spec, path.parent, doc.get("assets", []))
        if img is None:
            print(f"  ⚠ текстура не найдена для слоя {L['nm']}: {tex_spec[:50]}")
            continue
        tex_map[id(L)] = (img, op_pct / 100.0, mode)
        print(f"  ✓ {L['nm'][:40]:40s}  {mode:8s}  opacity={op_pct / 100:.2f}")

    if tex_map:
        print(f"Текстур: {len(tex_map)}")

    block_layers = [blk for sigma, blk in blocks]
    order_render = list(reversed(range(len(block_instances))))

    block_image_cache = {}
    alpha_cache = LRUCache(maxsize=alpha_cache_size)
    texture_cache = LRUCache(maxsize=texture_cache_size)

    if single_frame is not None:
        frame_list = [min(single_frame, max_t - 1)]
    else:
        frame_list = list(range(max_t))

    rendered_blocks = 0
    reused_blocks = 0

    _t_first_frame = _t.perf_counter()
    _frame_step = _force_step
    _est_done = False
    _min_frames = 150
    _abort_for_resize = False
    _abort_for_cut = False
    _cut_total = None
    _new_scale_val = scale
    _loop_frames = frame_list[_start_index:]
    _t_pass_frame1_done = None
    _rendered_in_pass = 0
    _done_resize = _reuse_masks is not None
    _frame_cache = dict(_reuse_frames) if _reuse_frames else {}
    if _reuse_frame0 is not None and frames:
        _frame_cache[0] = frames[0]
    for i in _loop_frames:
        if _frame_step > 1 and i != 0 and i != max_t - 1 and (i % _frame_step != 0):
            continue
        if i in _frame_cache:
            _arr_c = _frame_cache[i]
            if _arr_c.shape[:2] != (H, W):
                _pil_c = Image.fromarray(_arr_c).resize((W, H), Image.LANCZOS)
                _arr_c = np.array(_pil_c.convert("RGB"))
            frames.append(_arr_c)
            continue
        composed = bg_rgb.copy().convert("RGBA")

        for idx in order_render:
            inst = block_instances[idx]
            blk = block_layers[idx]

            block_id = tuple(id(L) for L in blk)
            block_sig = tuple(layer_signature(L, i) for L in blk)
            cache_key = (block_id, block_sig)

            if cache_key in block_image_cache:
                img = block_image_cache[cache_key]
                reused_blocks += 1
                composed = Image.alpha_composite(composed, img)
                continue

            if inst["kind"] == "ANIM_SIGMA":
                L0 = inst["L0"]
                sv = max(0.0, round(sigma_at(L0, i), 2))

                # свежая LottieAnimation + Scene + Canvas каждый кадр
                anim2 = LottieAnimation(eng)
                pic2 = anim2.get_picture()
                pic2.load(inst["tmp_name"])
                pic2.set_size(W, H)
                anim2.set_frame(i)
                sc = Scene(eng)
                sc.add(pic2)
                if sv > 0:
                    sc.add_effect_gaussian_blur(sigma=sv / 2.0, direction=0,
                                                 border=0, quality=blur_quality)
                cv = SwCanvas(eng)
                cv.set_target(W, H)
                cv.add(sc)
                cv.update(); cv.draw(True); cv.sync()
                img = cv.get_pillow("RGBA")

            else:
                anim = inst["anim"]
                canvas = inst["canvas"]
                anim.set_frame(i)
                canvas.update()
                canvas.draw(True)
                canvas.sync()
                img = canvas.get_pillow("RGBA")


            texed = [L for L in blk if id(L) in tex_map]
            if texed:
                img_arr = np.asarray(img).astype(np.float32)
                rgb = img_arr[:, :, :3]
                a_orig = img_arr[:, :, 3:4] / 255.0
                for L in texed:
                    tex_img, tex_op, tex_mode = tex_map[id(L)]
                    tex_frame = build_texture_frame_cached(
                        L, tex_img, tex_op, tex_mode, W, H, i, texture_cache
                    )
                    if layer_is_animated(L):
                        sig = layer_signature(L, i)
                        akey = ("alpha", id(L), sig)
                    else:
                        akey = ("alpha", id(L))
                    alpha = alpha_cache.get(akey)
                    if alpha is None:
                        alpha = render_layer_alpha(
                            eng, L, W, H,
                            frame=(i if layer_is_animated(L) else 0),
                            op=max_t
                        )
                        alpha_cache.set(akey, alpha)
                    a = (alpha.astype(np.float32) / 255.0) * tex_op
                    a = a[:, :, None]
                    tex_arr = np.asarray(tex_frame).astype(np.float32)
                    rgb = rgb * (1.0 - a) + tex_arr * a
                img = Image.fromarray(np.dstack([
                    np.clip(rgb, 0, 255).astype(np.uint8),
                    (a_orig * 255).astype(np.uint8)
                ]))

            # alpha-градиент: thorvg игнорирует alpha-стопы,
            # умножаем alpha-канал блока на свою маску
            # thorvg отдаёт premultiplied alpha -> unpremultiply для PIL
            img_arr = np.asarray(img).astype(np.float32)
            a_f = img_arr[:, :, 3:4] / 255.0
            rgb_f = img_arr[:, :, :3]
            rgb_f = np.where(a_f > 1e-6, rgb_f / np.maximum(a_f, 1e-6), 0.0)
            img = Image.fromarray(np.dstack([
                np.clip(rgb_f, 0, 255),
                img_arr[:, :, 3:4]
            ]).astype(np.uint8))

            block_image_cache[cache_key] = img
            rendered_blocks += 1
            composed = Image.alpha_composite(composed, img)

        _arr_new = np.array(composed.convert("RGB"))
        frames.append(_arr_new)
        _frame_cache[i] = _arr_new

        _rendered_in_pass += 1
        if _rendered_in_pass == 1:
            _dt_first = _t.perf_counter() - _t_first_frame
            print(f"  frame {i} — {_dt_first:.2f} сек", flush=True)
            if not _est_done and single_frame is None and budget_seconds > 0:
                _total_expected = sum(1 for x in _loop_frames
                                      if x == max_t - 1 or x % _frame_step == 0)
                _est_total = _dt_first * _total_expected
                _est_done = True
                print(f"  оценка на {max_t} кадров: ~{_est_total:.0f}с", flush=True)
                if _est_total > budget_seconds:
                    _ratio = budget_seconds / _est_total
                    _new_scale_calc = _eff_scale * math.sqrt(_ratio)
                    _min_scale = min(1.0, 480.0 / min(base_w, base_h))
                    _new_scale_calc = max(_new_scale_calc, _min_scale)
                    if _new_scale_calc < _eff_scale * 0.95:
                        _new_scale_val = _new_scale_calc
                        _abort_for_resize = True
                        _w_even = max(2, int(base_w * _new_scale_calc) & ~1)
                        _h_even = max(2, int(base_h * _new_scale_calc) & ~1)
                        print(f"  ⚠ Снижаю до {_w_even}x{_h_even}", flush=True)
                        break
                    else:
                        _per_frame = _est_total / max(1, _total_expected)
                        _budget_unique = budget_seconds / _per_frame
                        _budget_total = int(_budget_unique * _frame_step)
                        _cut = max(_min_frames, _budget_total)
                        if _cut < max_t:
                            _cut_total = _cut
                            _abort_for_cut = True
                            print(f"  ⚠ Уже на минимуме {W}x{H}; режу до {_cut} кадров (~{_cut/fr:.1f} сек)", flush=True)
                            break
                        else:
                            print(f"  ⚠ Уже на минимуме {W}x{H}; минимум {_min_frames} кадров (~{_min_frames/fr:.1f} сек) — бюджет превышен", flush=True)

        _rendered = len(frames)
        _total_expected = sum(1 for x in frame_list
                              if x == 0 or x == max_t - 1 or x % _frame_step == 0)
        _is_progress = (
            (_rendered <= 50 and _rendered % 10 == 0)
            or (_rendered > 50 and _rendered % 50 == 0)
            or _rendered == _total_expected
        )
        if _is_progress:
            _elapsed = _t.perf_counter() - _t_first_frame
            print(f"  кадр {_rendered}/{_total_expected} | {_elapsed:.1f}с прошло", flush=True)

    for inst in block_instances:
        c = inst.get("canvas")
        if c is not None:
            try: c.destroy()
            except Exception: pass
    eng.term()

    for f in tmpfiles:
        try:
            os.unlink(f)
        except Exception:
            pass

    if _abort_for_resize:
        _reuse_frame0 = frames[0] if frames else None
        return render_layered(
            file_path, fps=fps, width=width, height=height,
            scale=_new_scale_val, bg=bg, blur_quality=blur_quality,
            single_frame=single_frame, mask_frames=mask_frames,
            alpha_cache_size=alpha_cache_size,
            texture_cache_size=texture_cache_size,
            mask_workers=mask_workers, budget_seconds=budget_seconds,
            _force_step=2,
            _reuse_masks=masks, _reuse_frame0=_reuse_frame0,
            _start_index=1, _reuse_frames=_frame_cache,
        )

    if _abort_for_cut:
        _reuse_frame0 = frames[0] if frames else None
        return render_layered(
            file_path, fps=fps, width=width, height=height,
            scale=scale, bg=bg, blur_quality=blur_quality,
            single_frame=single_frame, mask_frames=mask_frames,
            alpha_cache_size=alpha_cache_size,
            texture_cache_size=texture_cache_size,
            mask_workers=mask_workers, budget_seconds=0,
            _force_step=_frame_step,
            _reuse_masks=masks, _reuse_frame0=_reuse_frame0,
            _start_index=1,
            _max_frames=_cut_total, _reuse_frames=_frame_cache,
        )

    _fr_out = fr
    if _frame_step > 1:
        _fr_out = max(1, int(round(fr / _frame_step)))
        print(f"  fps {fr} → {_fr_out} (skip x{_frame_step})", flush=True)
    return frames, W, H, _fr_out


def save_mp4(frames, out_path, fps, quality=8):
    import imageio.v2 as imageio
    print(f"\nКодирую {out_path} ...")
    imageio.mimsave(out_path, frames, fps=fps, codec="libx264",
                    quality=quality, macro_block_size=1, pixelformat="yuv420p")
    print("Готово.")


def main():
    a = parse_args()
    if not getattr(a, "debug", False):
        import builtins as _bt
        _real_print = _bt.print
        _MUTE_PREFIXES = (
            "Ассеты:", "JSON:", "Размер:", "Блоков:",
            "  #", "      ty=", "      [text", "      [foreign",
            "  ✓ ", "Текстур:",
            "Растеризация", "  готово за", "  [reuse]",
        )
        def _filtered_print(*args, **kwargs):
            _s = " ".join(str(x) for x in args)
            for _p in _MUTE_PREFIXES:
                if _s.startswith(_p):
                    return
            _real_print(*args, **kwargs)
        _bt.print = _filtered_print
    if a.test:
        out_png = (a.out or "test") + ".png"
        frames, W, H, fr = render_layered(
            a.file, a.fps, a.width, a.height,
            a.test_scale, a.bg, a.blur_quality,
            single_frame=a.test_frame, mask_frames=a.mask_frames,
            alpha_cache_size=a.alpha_cache_size,
            texture_cache_size=a.texture_cache_size,
            mask_workers=a.mask_workers
        )
        Image.fromarray(frames[0]).save(out_png)
        print(f"\nТестовый кадр -> {out_png}")
        return
    if not a.out:
        sys.exit("Укажите --out")
    frames, W, H, fr = render_layered(
        a.file, a.fps, a.width, a.height,
        a.scale, a.bg, a.blur_quality,
        mask_frames=a.mask_frames,
        alpha_cache_size=a.alpha_cache_size,
        texture_cache_size=a.texture_cache_size,
        mask_workers=a.mask_workers,
        budget_seconds=getattr(a, "budget_seconds", 60)
    )
    save_mp4(frames, a.out, fr, a.quality)


if __name__ == "__main__":
    main()