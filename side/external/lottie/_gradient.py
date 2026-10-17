"""Градиенты: парсинг CSS, сборка Lottie-слоёв, рендер PNG."""
import math
import re
from pathlib import Path

from _color import hex2rgb, _parse_color
import _state


def _split_top_commas(s):
    """Разбивает строку по запятым верхнего уровня (не внутри скобок)."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "(": depth += 1; cur.append(ch)
        elif ch == ")": depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if cur: out.append("".join(cur).strip())
    return out


def fl_item(color, op=100):
    return {"ty":"fl","c":{"a":0,"k":color},"o":{"a":0,"k":op},"r":1}


def _interp_stops(t, stops):
    if not stops: return 1.0
    if t <= stops[0][0]: return stops[0][1]
    if t >= stops[-1][0]: return stops[-1][1]
    for i in range(len(stops)-1):
        a_pos, a_val = stops[i]; b_pos, b_val = stops[i+1]
        if a_pos <= t <= b_pos:
            k = (t - a_pos) / (b_pos - a_pos) if b_pos > a_pos else 0
            return a_val + (b_val - a_val) * k
    return stops[-1][1]


def _describe_alpha_gradient(shapes):
    """Возвращает описание alpha-градиента в слое (без растеризации):
    {"kind": "el|rc|sh", "shape": {...}, "gf": {...},
     "alpha_stops": [(pos, alpha), ...]} или None."""
    ag = _detect_alpha_gradient(shapes)
    if ag is None: return None
    shape = ag["shape"]; gf = ag["gf"]
    t = shape["ty"]
    if t in ("el", "rc"):
        sz = shape["s"]["k"]
        p_ = shape["p"]["k"]
        sx_ = float(sz[0]); sy_ = float(sz[1])
        cx_ = float(p_[0]); cy_ = float(p_[1])
        if t == "el":
            geom = {"ty": "el", "sx": sx_, "sy": sy_, "cx": cx_, "cy": cy_}
        else:
            rr = 0.0
            r_k = shape.get("r", {})
            if isinstance(r_k, dict) and r_k.get("a", 0) == 0:
                rr = float(r_k.get("k", 0))
            geom = {"ty": "rc", "sx": sx_, "sy": sy_, "cx": cx_, "cy": cy_, "r": rr}
    elif t == "sh":
        v = shape["ks"]["k"]
        if isinstance(v, dict): v = v.get("v", [])
        if not v: return None
        geom = {"ty": "sh", "v": [[float(p[0]), float(p[1])] for p in v]}
    else:
        return None

    gf_desc = {"t": int(gf.get("t", 1)),
               "s": [float(gf["s"]["k"][0]), float(gf["s"]["k"][1])],
               "e": [float(gf["e"]["k"][0]), float(gf["e"]["k"][1])]}
    return {"geom": geom, "gf": gf_desc,
            "alpha_stops": [[float(p), float(a)] for (p, a) in ag["alpha_stops"]]}


def _detect_alpha_gradient(shapes):
    """Ищет в дереве первую пару (фигура, gf-с-alpha<1). Возвращает dict или None."""
    def walk(items):
        shape_it = None
        gf_it = None
        for it in items:
            if not isinstance(it, dict): continue
            t = it.get("ty")
            if t in ("el", "rc", "sh"):
                shape_it = it
            elif t == "gf":
                gf_it = it
            elif t == "gr":
                r = walk(it.get("it", []))
                if r: return r
        if shape_it is None or gf_it is None:
            return None
        gf = gf_it
        n = gf.get("g", {}).get("p", 0)
        k = gf.get("g", {}).get("k", {}).get("k", [])
        n_color = n * 4
        if len(k) < n_color + n*2:
            return None
        alphas = k[n_color + 1::2]
        if not any(a < 0.999 for a in alphas):
            return None
        colors = [(k[i*4], k[i*4+1], k[i*4+2], k[i*4+3]) for i in range(n)]
        a_stops = [(k[n_color + i*2], k[n_color + i*2 + 1]) for i in range(n)]
        return {"shape": shape_it, "gf": gf, "colors": colors,
                "alpha_stops": a_stops}
    return walk(shapes)


def parse_gradient(value):
    """CSS gradient -> {type, stops, center?, angle?}.
    Поддерживает radial-gradient и linear-gradient."""
    if not value: return None
    m = re.match(r"(radial|linear)-gradient\((.*)\)\s*$", value, re.DOTALL)
    if not m: return None
    gtype = m.group(1)
    inner = m.group(2)
    # разделим аргументы по запятым верхнего уровня
    parts = _split_top_commas(inner)
    if not parts: return None

    # первая часть — параметры формы/угла
    head = parts[0]
    shape = {"type": gtype, "stops": []}
    if gtype == "radial":
        # radial-gradient([shape] [size] at X Y, stop, stop, ...)
        mm = re.match(r".*?at\s+([\d.]+)%?\s+([\d.]+)%?", head)
        if mm:
            shape["center"] = (float(mm.group(1))/100, float(mm.group(2))/100)
        else:
            shape["center"] = (0.5, 0.5)
        # ellipse / circle
        if "circle" in head: shape["shape"] = "circle"
        else: shape["shape"] = "ellipse"
        # остальное — стопы
        stop_parts = parts[1:]
    else:
        # linear-gradient([angle], stop, stop, ...)
        if re.match(r"^[\d.]+deg\s*$", head) or "deg" in head or "to " in head:
            shape["angle"] = head
            stop_parts = parts[1:]
        else:
            stop_parts = parts

    for sp in stop_parts:
        m2 = re.match(r"([^\s]+)\s*([\d.]+)%?", sp.strip())
        if not m2: continue
        col = _parse_color(m2.group(1))
        if col is None: continue
        pos = float(m2.group(2))/100 if m2.group(2) else None
        shape["stops"].append({"pos": pos, "color": col})

    # нормируем позиции, если их нет
    n = len(shape["stops"])
    for i, st in enumerate(shape["stops"]):
        if st["pos"] is None:
            st["pos"] = i / max(1, n-1)
    return shape


def parse_gradients(root):
    for el in root.iter():
        if not isinstance(el.tag, str): continue
        tag = el.tag.replace(_state.NS, "")
        if tag not in ("linearGradient","radialGradient"): continue
        gid = el.get("id")
        stops = []
        for s_el in el.findall(_state.NS + "stop"):
            off = s_el.get("offset","0")
            off = float(off[:-1])/100 if off.endswith("%") else float(off)
            col = hex2rgb(s_el.get("stop-color","#000")) or [0,0,0]
            sa = s_el.get("stop-opacity")
            sa = float(sa) if sa else 1.0
            stops.append((off, col, sa))
        if tag == "linearGradient":
            def f(v):
                v = (v or "0").strip()
                if v.endswith("%"): return float(v[:-1])/100
                return float(v)
            _state.gradients[gid] = {"type":"linear","stops":stops,
                "s":(f(el.get("x1","0")), f(el.get("y1","0"))),
                "e":(f(el.get("x2","100%")), f(el.get("y2","0")))}
        else:
            def g(v, d):
                v = (v or d)
                if isinstance(v, str) and v.endswith("%"):
                    return float(v[:-1])/100
                try: return float(v)
                except: return 0.5
            _state.gradients[gid] = {"type":"radial","stops":stops,
                "cx": g(el.get("cx","0.5"), 0.5),
                "cy": g(el.get("cy","0.5"), 0.5),
                "r":  g(el.get("r","0.5"), 0.5)}


def gf_item(gid, cx, cy, sw_, sh_):
    g = _state.gradients[gid]
    color_k, opacity_k = [], []
    has_op = False
    for off, col, alpha in g["stops"]:
        color_k += [off, col[0], col[1], col[2]]
        opacity_k += [off, alpha]
        if alpha < 0.999: has_op = True
    k = color_k + (opacity_k if has_op else [])
    item = {"ty":"gf","o":{"a":0,"k":100},"r":1,"bm":0,
            "g":{"p":len(g["stops"]),"k":{"a":0,"k":k}}}
    if g["type"] == "linear":
        item["t"] = 1
        item["s"] = {"a":0,"k":[g["s"][0]*sw_ - sw_/2, g["s"][1]*sh_ - sh_/2]}
        item["e"] = {"a":0,"k":[g["e"][0]*sw_ - sw_/2, g["e"][1]*sh_ - sh_/2]}
    else:
        item["t"] = 2
        gcx = g["cx"]*sw_ - sw_/2
        gcy = g["cy"]*sh_ - sh_/2
        gr  = g["r"]*max(sw_, sh_)
        item["s"] = {"a":0,"k":[gcx, gcy]}
        item["e"] = {"a":0,"k":[gcx+gr, gcy]}
    return item


def build_fill(fill, cx, cy, sw_, sh_):
    if not fill or fill == "none": return None
    if fill.startswith("url(#"):
        gid = fill[5:-1].strip()
        if gid in _state.gradients: return gf_item(gid, cx, cy, sw_, sh_)
        return None
    col = hex2rgb(fill)
    return fl_item(col, 100) if col else None


def make_gradient_png(grad, W, H, path):
    """Рендерит градиент (radial или linear) в PNG."""
    from PIL import Image
    img = Image.new("RGB", (W, H))
    px = img.load()
    stops = grad["stops"]
    cx, cy = grad.get("center", (0.5, 0.5))
    cx_px, cy_px = cx * W, cy * H
    maxr = math.hypot(max(cx_px, W-cx_px), max(cy_px, H-cy_px))
    for y in range(H):
        for x in range(W):
            if grad["type"] == "radial":
                t = math.hypot(x-cx_px, y-cy_px) / maxr if maxr else 0
            else:
                t = ((x/W) + (y/H)) / 2  # упрощённо для linear
            # ищем между стопами
            c = stops[0]["color"]
            for i in range(len(stops)-1):
                a, b = stops[i], stops[i+1]
                if a["pos"] <= t <= b["pos"]:
                    k = (t - a["pos"]) / (b["pos"] - a["pos"]) if b["pos"] > a["pos"] else 0
                    c = tuple(int(a["color"][j] + (b["color"][j]-a["color"][j])*k)
                              for j in range(3))
                    break
            else:
                c = stops[-1]["color"]
            px[x, y] = c
    img.save(path)
