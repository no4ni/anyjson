#!/usr/bin/env python3
"""js2lottie.py — этап 5. Печёт JS-canvas эффекты (снег, салют, метеоры)."""
import re, math, random


def _parse_js_const(js, name, default=None):
    m = re.search(rf"(?:const|let|var)\s+{re.escape(name)}\s*=\s*([^;]+);", js)
    if not m:
        return default
    val = m.group(1).strip()
    try:
        return float(val)
    except ValueError:
        pass
    return default


def _parse_palettes(js):
    m = re.search(r"PALETTES\s*=\s*\[(.*?)\]\s*;", js, re.DOTALL)
    if not m:
        return [["#ffffff"]]
    out = []
    for pal in re.finditer(r"\[([^\]]*)\]", m.group(1)):
        cols = re.findall(r"['\"](#[0-9a-fA-F]{3,8})['\"]", pal.group(1))
        if cols:
            out.append(cols)
    return out or [["#ffffff"]]


def _hex_to_lottie(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 8:
        h = h[:6]
    return [int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)]


def gen_snow_layers(js, W, H, op, seed=42):
    flakes_n = int(_parse_js_const(js, "FLAKES", 140))
    rnd = random.Random(seed)
    layers = []
    step = 3
    for i in range(flakes_n):
        x = rnd.random() * W
        y = rnd.random() * H
        r = rnd.random() * 2.6 + 0.6
        vy = rnd.random() * 1.2 + 0.4
        vx = (rnd.random() - 0.5) * 0.6
        a = rnd.random() * 0.6 + 0.4
        ph = rnd.random() * math.pi * 2

        seg_p, seg_o = [], []
        t = 0
        while t <= op:
            seg_p.append({"t": t, "s": [x, y, 0]})
            seg_o.append({"t": t, "s": [a * 100]})
            for _ in range(step):
                ph += 0.02
                x += vx + math.sin(ph) * 0.5
                y += vy
                if y > H + 5:
                    y = -5
                    x = rnd.random() * W
                if x > W + 5: x = -5
                if x < -5: x = W + 5
            t += step
        if seg_p[-1]["t"] < op:
            seg_p.append({"t": op, "s": [x, y, 0]})
            seg_o.append({"t": op, "s": [a * 100]})

        shape = {
            "ty": "gr", "nm": f"flake_{i}", "it": [
                {"ty": "el", "d": 1, "s": {"a": 0, "k": [r*2, r*2]},
                 "p": {"a": 0, "k": [0, 0]}},
                {"ty": "fl", "c": {"a": 0, "k": [1,1,1]},
                 "o": {"a": 0, "k": 100}, "r": 1},
                {"ty": "tr", "p": {"a": 0, "k": [0,0]}, "a": {"a": 0, "k": [0,0]},
                 "s": {"a": 0, "k": [100,100]}, "r": {"a": 0, "k": 0},
                 "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0},
                 "sa": {"a": 0, "k": 0}}
            ]
        }
        L = {
            "ddd": 0, "ind": 0, "ty": 4, "nm": f"snow_{i}", "sr": 1,
            "ks": {
                "o": {"a": 1, "k": seg_o},
                "r": {"a": 0, "k": 0},
                "p": {"a": 1, "k": seg_p},
                "a": {"a": 0, "k": [0, 0, 0]},
                "s": {"a": 0, "k": [100, 100, 100]},
            },
            "ao": 0, "shapes": [shape], "ip": 0, "op": op, "st": 0, "bm": 0,
        }
        layers.append(L)
    return layers


def gen_salute_layers(js, W, H, op, seed=42):
    rnd = random.Random(seed + 1)
    palettes = _parse_palettes(js)
    layers = []
    t = 0
    b = 0
    step = 3
    while t < op:
        cx = W * (0.15 + rnd.random() * 0.7)
        cy = H * (0.10 + rnd.random() * 0.45)
        palette = palettes[rnd.randrange(len(palettes))]
        n = 70 + int(rnd.random() * 60)
        speed = 2.8 + rnd.random() * 3.2
        groups = []
        for i in range(n):
            ang = (math.pi * 2 * i) / n + rnd.random() * 0.1
            sp = speed * (0.4 + rnd.random() * 0.9)
            px = math.cos(ang) * sp
            py = math.sin(ang) * sp
            decay = 0.007 + rnd.random() * 0.010
            color = palette[rnd.randrange(len(palette))]
            r = 1.6 + rnd.random() * 2.0
            life_frames = min(op - t, int(1.0 / max(decay, 1e-3)))
            kp, ko, ks = [], [], []
            x, y = cx, cy
            for ft in range(0, life_frames + 1, step):
                lf = max(0.0, 1.0 - decay * ft)
                if lf <= 0: break
                kp.append({"t": t + ft, "s": [x, y, 0]})
                ko.append({"t": t + ft, "s": [lf * 100]})
                ks.append({"t": t + ft, "s": [lf * 100, lf * 100]})
                for _ in range(step):
                    x += px; y += py
                    py += 0.035
                    px *= 0.99; py *= 0.99
            if not kp: continue
            c_l = _hex_to_lottie(color)
            g = {"ty": "gr", "nm": f"p{i}", "it": [
                {"ty": "el", "d": 1, "s": {"a": 0, "k": [r*2, r*2]},
                 "p": {"a": 0, "k": [0, 0]}},
                {"ty": "fl", "c": {"a": 0, "k": c_l}, "o": {"a": 0, "k": 100}, "r": 1},
                {"ty": "tr",
                 "p": {"a": 1, "k": kp},
                 "a": {"a": 0, "k": [0, 0]},
                 "s": {"a": 1, "k": ks},
                 "r": {"a": 0, "k": 0},
                 "o": {"a": 1, "k": ko},
                 "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}}
            ]}
            groups.append(g)
        if not groups:
            break
        L = {
            "ddd": 0, "ind": 0, "ty": 4, "nm": f"salute_{b}", "sr": 1,
            "ks": {
                "o": {"a": 0, "k": 100},
                "r": {"a": 0, "k": 0},
                "p": {"a": 0, "k": [0, 0, 0]},
                "a": {"a": 0, "k": [0, 0, 0]},
                "s": {"a": 0, "k": [100, 100, 100]},
            },
            "ao": 0, "shapes": groups, "ip": 0, "op": op, "st": 0, "bm": 0,
        }
        layers.append(L)
        b += 1
        t += int((800 + rnd.random() * 1400) / 1000.0 * 30)
    return layers


def gen_meteor_layers(js, W, H, op, seed=42):
    rnd = random.Random(seed + 2)
    layers = []
    t = int(1.5 * 30)
    i = 0
    while t < op:
        x = rnd.random() * W * 0.8 + W * 0.2
        y = rnd.random() * H * 0.4 - 50
        length = 90 + rnd.random() * 130
        speed = 7 + rnd.random() * 6
        angle = math.pi * 0.75 + (rnd.random() - 0.5) * 0.2
        dx = math.cos(angle); dy = math.sin(angle)
        life_frames = min(op - t, 60)
        kp, ko = [], []
        cx, cy = x, y
        for ft in range(0, life_frames + 1, 3):
            lf = max(0.0, 1.0 - ft / life_frames)
            kp.append({"t": t + ft, "s": [cx, cy, 0]})
            ko.append({"t": t + ft, "s": [lf * 100]})
            for _ in range(3):
                cx += dx * speed
                cy += dy * speed
        if not kp: break
        shape = {"ty": "gr", "nm": f"meteor_{i}", "it": [
            {"ty": "rc", "d": 1, "s": {"a": 0, "k": [length, 2]},
             "p": {"a": 0, "k": [0, 0]}, "r": {"a": 0, "k": 0}},
            {"ty": "fl", "c": {"a": 0, "k": [1, 1, 1]}, "o": {"a": 0, "k": 100}, "r": 1},
            {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]},
             "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": math.degrees(angle)},
             "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}}
        ]}
        L = {
            "ddd": 0, "ind": 0, "ty": 4, "nm": f"meteor_{i}", "sr": 1,
            "ks": {
                "o": {"a": 1, "k": ko},
                "r": {"a": 0, "k": 0},
                "p": {"a": 1, "k": kp},
                "a": {"a": 0, "k": [0, 0, 0]},
                "s": {"a": 0, "k": [100, 100, 100]},
            },
            "ao": 0, "shapes": [shape], "ip": 0, "op": op, "st": 0, "bm": 0,
        }
        layers.append(L)
        i += 1
        t += int((2000 + rnd.random() * 3500) / 1000.0 * 30)
    return layers


def apply_js_canvas(html, js_text, layers_out, W, H, op):
    canvas_ids = re.findall(r'<canvas\s+[^>]*id="([^"]+)"', html)
    if not canvas_ids:
        return
    print(f"  [js-canvas] найдено: {canvas_ids}")
    if "snow" in canvas_ids and "makeFlake" in js_text:
        snow = gen_snow_layers(js_text, W, H, op)
        layers_out.extend(snow)
        print(f"  [js] снег: {len(snow)} слоёв")
    if "salute" in canvas_ids and "burst" in js_text:
        sal = gen_salute_layers(js_text, W, H, op)
        layers_out.extend(sal)
        print(f"  [js] салют: {len(sal)} слоёв")
    if "spawnMeteor" in js_text:
        met = gen_meteor_layers(js_text, W, H, op)
        layers_out.extend(met)
        print(f"  [js] метеоры: {len(met)} слоёв")