#!/usr/bin/env python3
"""
svg2lottie.py v3.1 — SVG (+ CSS @keyframes) -> Lottie JSON.
Исправлено: animation-delay в shorthand, накопление scale/opacity в циклах,
линейная интерполяция между ключами, group transform (scale/rotate),
_state.FRAME_OP при отсутствии анимации, transform-origin: center, cubic-bezier().
"""
import xml.etree.ElementTree as ET
import json, math, re, sys, pathlib, base64, mimetypes

# Refactored: вынесено в модули
import _state
from _gradient import (
    _split_top_commas, fl_item, _interp_stops,
    _describe_alpha_gradient, _detect_alpha_gradient,
    parse_gradient, parse_gradients, gf_item,
    build_fill, make_gradient_png,
)

# Refactored: вынесено в модули
import _state
from _css import (
    strip_comments, split_css_blocks, parse_props,
    parse_css, css_parse, css_rule_for, get_css_rule,
    parse_anim_shorthand, parse_transform, parse_origin,
)

# Refactored: вынесено в модули
import _state
from _anim import (
    bezier, _val_at, _get_base_at, _shift_pos,
    _transform_point, _apply_group_transform,
    _apply_delta, _interp_series, _lerp_props,
    keyframes_to_points, apply_css_anim,
    apply_animation_to_layers,
    apply_animation_to_layers, parse_tr,
)

NS = "{http://www.w3.org/2000/svg}"
_state.FRAME_OP = 300
ROOT = pathlib.Path(r"E:/Jericho/lottie-demo")
_state.CANVAS_W, _state.CANVAS_H = 800, 500
import sys as _sys
_sys.path.insert(0, str(ROOT))
try:
    from js2lottie import apply_js_canvas as _apply_js_canvas
except Exception as _e:
    _apply_js_canvas = None
DEFAULT_W, DEFAULT_H = 800, 500
_state.FR = 30
VB_W, VB_H = 800, 600
_state.gradients = {}
filters = {}     # id -> stdDeviation
assets = []      # картинки для <image>
layers = []
notes = []

_state.TIMING = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "ease":   (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
}



























# ============================================================
# Геометрия
# ============================================================

def _mime_of(path):
    m, _ = mimetypes.guess_type(path)
    return m or "image/png"


def register_image_asset(href, W_el, H_el):
    """Регистрирует картинку. Возвращает id ассета.
    Если href — data:URI, распаковывает в base64 (e=1).
    Если путь — сохраняет внешним (e=0), для последующей подстановки."""
    if not href:
        return None
    aid = f"img_{len(assets)}"
    if href.startswith("data:"):
        # data:[<mime>][;base64],<data>
        m = re.match(r"data:([^;,]*)(;base64)?,(.*)", href, re.DOTALL)
        if not m:
            return None
        mime = m.group(1) or "image/png"
        is_b64 = bool(m.group(2))
        payload = m.group(3)
        if not is_b64:
            payload = base64.b64encode(payload.encode("latin-1")).decode()
        assets.append({
            "id": aid, "w": int(W_el), "h": int(H_el),
            "u": "", "p": f"data:{mime};base64,{payload}", "e": 1
        })
        return aid
    # внешний файл
    assets.append({
        "id": aid, "w": int(W_el), "h": int(H_el),
        "u": "", "p": href, "e": 0
    })
    return aid


def tr_item():
    return {"ty":"tr","p":{"a":0,"k":[0,0]},"a":{"a":0,"k":[0,0]},
            "s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"o":{"a":0,"k":100},
            "sk":{"a":0,"k":0},"sa":{"a":0,"k":0}}

def make_ks(pos, opacity=100, rot=0):
    return {"o":{"a":0,"k":opacity},"r":{"a":0,"k":rot},
            "p":{"a":0,"k":[pos[0], pos[1], 0]},"a":{"a":0,"k":[0,0,0]},
            "s":{"a":0,"k":[100,100,100]}}


def st_item(color, w, op=100):
    return {"ty":"st","c":{"a":0,"k":color},"o":{"a":0,"k":op},
            "w":{"a":0,"k":w},"lc":2,"lj":2}

def el_item(rx, ry=None):
    if ry is None: ry = rx
    return {"ty":"el","d":1,"s":{"a":0,"k":[rx*2, ry*2]},"p":{"a":0,"k":[0,0]}}

def rc_item(w, h, rx=0):
    return {"ty":"rc","d":1,"s":{"a":0,"k":[w,h]},"p":{"a":0,"k":[0,0]},"r":{"a":0,"k":rx}}

def sh_item(sp_or_pts, closed=True):
    if isinstance(sp_or_pts, dict):
        sp = sp_or_pts
        return {"ty":"sh","ks":{"a":0,"k":{
            "i":[list(t) for t in sp["i"]],
            "o":[list(t) for t in sp["o"]],
            "v":[[p[0],p[1]] for p in sp["v"]],
            "c":sp.get("c", True)}}}
    pts = sp_or_pts
    n = len(pts)
    return {"ty":"sh","ks":{"a":0,"k":{"i":[[0,0]]*n,"o":[[0,0]]*n,
            "v":[[p[0],p[1]] for p in pts],"c":closed}}}

def parse_points(s):
    n = [float(x) for x in re.findall(r"-?\d+\.?\d*", s)]
    return [(n[i], n[i+1]) for i in range(0, len(n), 2)]

def _arc_to_beziers(x1, y1, rx, ry, phi_deg, large_arc, sweep, x2, y2):
    """SVG arc -> список cubic-bezier сегментов. Возвращает [(c1x,c1y,c2x,c2y,px,py), ...]."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x1, y1, x2, y2, x2, y2)]
    phi = math.radians(phi_deg)
    cosp, sinp = math.cos(phi), math.sin(phi)
    dx = (x1 - x2) / 2.0; dy = (y1 - y2) / 2.0
    x1p = cosp*dx + sinp*dy
    y1p = -sinp*dx + cosp*dy
    rx = abs(rx); ry = abs(ry)
    lam = (x1p*x1p)/(rx*rx) + (y1p*y1p)/(ry*ry)
    if lam > 1:
        s = math.sqrt(lam); rx *= s; ry *= s
    num = rx*rx*ry*ry - rx*rx*y1p*y1p - ry*ry*x1p*x1p
    den = rx*rx*y1p*y1p + ry*ry*x1p*x1p
    coef = math.sqrt(max(0.0, num/den)) if den > 0 else 0.0
    if large_arc == sweep: coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cosp*cxp - sinp*cyp + (x1+x2)/2.0
    cy = sinp*cxp + cosp*cyp + (y1+y2)/2.0
    def angle(ux, uy, vx, vy):
        dot = ux*vx + uy*vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        if norm == 0: return 0.0
        a = math.acos(max(-1.0, min(1.0, dot/norm)))
        if ux*vy - uy*vx < 0: a = -a
        return a
    theta1 = angle(1, 0, (x1p-cxp)/rx, (y1p-cyp)/ry)
    dtheta = angle((x1p-cxp)/rx, (y1p-cyp)/ry,
                   (-x1p-cxp)/rx, (-y1p-cyp)/ry)
    if not sweep and dtheta > 0: dtheta -= 2*math.pi
    elif sweep and dtheta < 0: dtheta += 2*math.pi
    n_seg = max(1, int(math.ceil(abs(dtheta) / (math.pi/2))))
    dt = dtheta / n_seg
    t = (4.0/3.0) * math.tan(dt/4.0)
    segs = []
    for i in range(n_seg):
        th1 = theta1 + i*dt
        th2 = th1 + dt
        cos1, sin1 = math.cos(th1), math.sin(th1)
        cos2, sin2 = math.cos(th2), math.sin(th2)
        def to_user(px_, py_):
            return (cosp*rx*px_ - sinp*ry*py_ + cx,
                    sinp*rx*px_ + cosp*ry*py_ + cy)
        p1 = to_user(cos1, sin1)
        p2 = to_user(cos2, sin2)
        c1 = to_user(cos1 - t*sin1, sin1 + t*cos1)
        c2 = to_user(cos2 + t*sin2, sin2 - t*cos2)
        segs.append((c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))
    return segs


def parse_path(d):
    toks = re.findall(r"[MLQCZTSAmlqcztsa]|-?\d+\.?\d*", d)
    subpaths = []
    cur = {"v":[], "o":[], "i":[], "c":False}
    last_ctrl = [None]
    def flush():
        nonlocal cur
        if len(cur["v"]) >= 2: subpaths.append(cur)
        cur = {"v":[], "o":[], "i":[], "c":False}
        last_ctrl[0] = None
    def add(pt, o=(0,0), i=(0,0)):
        cur["v"].append(pt); cur["o"].append(o); cur["i"].append(i)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in "Mm":
            flush(); add((float(toks[i+1]), float(toks[i+2]))); i += 3
        elif t in "Ll":
            add((float(toks[i+1]), float(toks[i+2]))); i += 3
            last_ctrl[0] = None
        elif t in "Qq":
            Cx, Cy = float(toks[i+1]), float(toks[i+2])
            Px, Py = float(toks[i+3]), float(toks[i+4])
            P0 = cur["v"][-1]
            cur["o"][-1] = (2/3*(Cx-P0[0]), 2/3*(Cy-P0[1]))
            add((Px,Py), o=(0,0), i=(2/3*(Cx-Px), 2/3*(Cy-Py)))
            last_ctrl[0] = (Cx, Cy)
            i += 5
        elif t in "Tt":
            Px, Py = float(toks[i+1]), float(toks[i+2])
            P0 = cur["v"][-1]
            if last_ctrl[0] is None:
                Cx, Cy = P0
            else:
                Cx = 2*P0[0] - last_ctrl[0][0]
                Cy = 2*P0[1] - last_ctrl[0][1]
            cur["o"][-1] = (2/3*(Cx-P0[0]), 2/3*(Cy-P0[1]))
            add((Px,Py), o=(0,0), i=(2/3*(Cx-Px), 2/3*(Cy-Py)))
            last_ctrl[0] = (Cx, Cy)
            i += 3
        elif t in "Cc":
            x1,y1 = float(toks[i+1]), float(toks[i+2])
            x2,y2 = float(toks[i+3]), float(toks[i+4])
            Px,Py = float(toks[i+5]), float(toks[i+6])
            P0 = cur["v"][-1]
            cur["o"][-1] = (x1-P0[0], y1-P0[1])
            add((Px,Py), o=(0,0), i=(x2-Px, y2-Py))
            i += 7
        elif t in "Aa":
            rx_, ry_ = float(toks[i+1]), float(toks[i+2])
            phi_ = float(toks[i+3])
            la_ = int(float(toks[i+4])); sw_ = int(float(toks[i+5]))
            Px, Py = float(toks[i+6]), float(toks[i+7])
            P0 = cur["v"][-1]
            segs = _arc_to_beziers(P0[0], P0[1], rx_, ry_, phi_, la_, sw_, Px, Py)
            for (c1x, c1y, c2x, c2y, px_, py_) in segs:
                cur["o"][-1] = (c1x - cur["v"][-1][0], c1y - cur["v"][-1][1])
                add((px_, py_), o=(0,0), i=(c2x - px_, c2y - py_))
            i += 8
        elif t in "Zz":
            cur["c"] = True; flush(); i += 1
        else:
            i += 1
    flush()
    return subpaths


def collect_animate_transform(el):
    """Собирает <animateTransform> детей. Возвращает список словарей:
    {"type": "rotate|translate|scale", "keys": [{"t":int, "v":[..]}, ...]}"""
    result = []
    for ch in el:
        if not isinstance(ch.tag, str): continue
        if ch.tag.replace(NS, "") != "animateTransform": continue
        typ = (ch.get("type") or "translate").strip().lower()
        vals_str = ch.get("values", "")
        from_str = ch.get("from", "")
        to_str = ch.get("to", "")
        dur = _smil_dur_to_frames(ch.get("dur", "1s"))
        rc = (ch.get("repeatCount") or "1").strip().lower()
        infinite = (rc == "indefinite")
        try:
            n_cycles = 9999 if infinite else max(1, int(float(rc)))
        except ValueError:
            n_cycles = 1

        if vals_str:
            raw_vals = [v.strip() for v in vals_str.split(";") if v.strip()]
            times = _smil_time_list(ch.get("keyTimes", ""), len(raw_vals))
        elif from_str or to_str:
            raw_vals = [from_str or to_str, to_str or from_str]
            times = [0.0, 1.0]
        else:
            continue

        parsed = [_parse_at_vals(typ, v) for v in raw_vals]
        if any(p is None for p in parsed): continue

        keys = []
        for c in range(n_cycles):
            for i, (tr, v) in enumerate(zip(times, parsed)):
                t = c * dur + tr * dur
                if t > _state.FRAME_OP: break
                if c > 0 and t == _state.FRAME_OP: continue
                _vv = list(v)
                if typ == "rotate" and c > 0:
                    _vv[0] = _vv[0] + 360.0 * c
                keys.append({"t": int(round(t)), "v": _vv})
            if (c + 1) * dur > _state.FRAME_OP: break
        if not keys: continue
        if keys[0]["t"] > 0:
            keys.insert(0, {"t": 0, "v": list(parsed[0])})
        if keys[-1]["t"] < _state.FRAME_OP:
            keys.append({"t": _state.FRAME_OP, "v": list(keys[-1]["v"])})
        seen = {}
        for k in keys: seen[k["t"]] = k
        keys = sorted(seen.values(), key=lambda x: x["t"])
        result.append({"type": typ, "keys": keys})
    return result


def _parse_at_vals(typ, s):
    s = (s or "").strip()
    if not s: return None
    parts = [p for p in re.split(r"[\s,]+", s) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if typ == "rotate":
        if len(nums) >= 3: return nums[:3]
        if len(nums) == 1: return [nums[0], 0.0, 0.0]
        return None
    if typ == "translate":
        if len(nums) >= 2: return nums[:2]
        if len(nums) == 1: return [nums[0], 0.0]
        return None
    if typ == "scale":
        if len(nums) >= 2: return nums[:2]
        if len(nums) == 1: return [nums[0], nums[0]]
        return None
    return None


def _apply_smil_at(layer, at, lcx, lcy, gsx, gsy, ctx_rot):
    """Применяет один animateTransform к слою."""
    typ = at["type"]
    keys = at["keys"]
    if not keys: return
    if typ == "rotate":
        v0 = keys[0]["v"]
        cx = v0[1] if len(v0) > 1 else 0.0
        cy = v0[2] if len(v0) > 2 else 0.0
        ax = cx - lcx
        ay = cy - lcy
        if abs(ax) > 1e-6 or abs(ay) > 1e-6:
            old_a = layer["ks"].get("a")
            if old_a is None:
                layer["ks"]["a"] = {"a":0, "k":[ax, ay, 0]}
            elif old_a.get("a", 0) == 0:
                ov = old_a["k"]
                layer["ks"]["a"] = {"a":0, "k":[ov[0]+ax, ov[1]+ay, 0]}
            _shift_pos(layer, ax, ay)
        rk = [{"t": k["t"], "s": [k["v"][0] + ctx_rot]} for k in keys]
        layer["ks"]["r"] = {"a": 1, "k": rk}
    elif typ == "translate":
        pk = layer["ks"].get("p")
        if pk is None: return
        if pk.get("a", 0) == 0:
            base = pk["k"][:2]
        else:
            base = pk["k"][0]["s"][:2]
        kf = []
        for k in keys:
            dx = k["v"][0] * gsx
            dy = (k["v"][1] if len(k["v"]) > 1 else 0.0) * gsy
            kf.append({"t": k["t"], "s": [base[0]+dx, base[1]+dy, 0]})
        layer["ks"]["p"] = {"a": 1, "k": kf}
    elif typ == "scale":
        sk = layer["ks"].get("s")
        if sk is None: return
        if sk.get("a", 0) == 0:
            base = sk["k"][:2]
        else:
            base = sk["k"][0]["s"][:2]
        kf = []
        for k in keys:
            sx = k["v"][0]
            sy = k["v"][1] if len(k["v"]) > 1 else k["v"][0]
            kf.append({"t": k["t"], "s": [base[0]*sx, base[1]*sy, 100]})
        layer["ks"]["s"] = {"a": 1, "k": kf}


def _apply_smil_at_to_last(el, ctx, cx_, cy_):
    """Применяет animateTransform к последнему слою (после build_one/path/line)."""
    if not layers: return
    at_list = collect_animate_transform(el)
    if not at_list: return
    L = layers[-1]
    lcx_ = L.get("_lcx", cx_)
    lcy_ = L.get("_lcy", cy_)
    gsx = ctx.get("sx", 1.0); gsy = ctx.get("sy", 1.0)
    for at in at_list:
        _apply_smil_at(L, at, lcx_, lcy_, gsx, gsy, ctx.get("rot", 0.0))


def collect_smil_anims(el):
    """Собирает <animate> детей элемента в dict: {attr: {'a':1,'k':[...]}}.
    Атрибуты: r, cx, cy, x, y, width, height, opacity."""
    result = {}
    for ch in el:
        if not isinstance(ch.tag, str): continue
        if ch.tag.replace(NS, "") != "animate": continue
        attr = ch.get("attributeName")
        if not attr: continue
        vals = _smil_value_list(ch.get("values", ""))
        if not vals: continue
        times = _smil_time_list(ch.get("keyTimes", ""), len(vals))
        dur = _smil_dur_to_frames(ch.get("dur", "1s"))
        rc = (ch.get("repeatCount") or "1").strip().lower()
        infinite = (rc == "indefinite")
        try:
            n_cycles = 9999 if infinite else max(1, int(float(rc)))
        except ValueError:
            n_cycles = 1

        keys = []
        for c in range(n_cycles):
            for i, (tr, v) in enumerate(zip(times, vals)):
                t = c * dur + tr * dur
                if t > _state.FRAME_OP: break
                entry = {"t": int(round(t)), "s": [v]}
                e = _smil_bezier(ch, i)
                if e:
                    entry["i"] = e["i"]; entry["o"] = e["o"]
                keys.append(entry)
            if c * dur > _state.FRAME_OP: break
        if not keys: continue
        if keys[0]["t"] > 0:
            keys.insert(0, {"t": 0, "s": [vals[0]]})
        if keys[-1]["t"] < _state.FRAME_OP:
            keys.append({"t": _state.FRAME_OP, "s": list(keys[-1]["s"])})
        seen = {}
        for k in keys: seen[k["t"]] = k
        result[attr] = {"a": 1,
                        "k": sorted(seen.values(), key=lambda x: x["t"])}
    return result


def _smil_value_list(vals_str):
    return [float(x.strip()) for x in vals_str.split(";") if x.strip()]


def _smil_time_list(times_str, n):
    if times_str:
        ts = [float(x.strip()) for x in times_str.split(";") if x.strip()]
        if len(ts) == n: return ts
    if n <= 1: return [0.0]
    return [i / (n - 1) for i in range(n)]


def _smil_dur_to_frames(dur_str):
    """'3s' -> 90 кадров при 30fps. '1000ms' -> 30 кадров."""
    if not dur_str: return _state.FR
    dur_str = dur_str.strip().lower()
    try:
        if dur_str.endswith("ms"):
            return max(1, round(float(dur_str[:-2]) / 1000 * _state.FR))
        if dur_str.endswith("s"):
            return max(1, round(float(dur_str[:-1]) * _state.FR))
        return max(1, round(float(dur_str) * _state.FR))
    except ValueError:
        return _state.FR


def _smil_bezier(anim_el, kf_index):
    """Возвращает easing для ключа kf_index из keySplines."""
    if anim_el.get("calcMode") != "spline":
        return None
    ks = anim_el.get("keySplines", "")
    if not ks: return None
    parts = [p.strip() for p in ks.split(";") if p.strip()]
    if kf_index >= len(parts): return None
    try:
        x1, y1, x2, y2 = [float(v) for v in parts[kf_index].split()]
        return {"i":{"x":[x2],"y":[y2]},"o":{"x":[x1],"y":[y1]}}
    except (ValueError, IndexError):
        return None


def _smil_to_blur_keys(base, anim_el):
    """<animate attributeName='stdDeviation' .../> -> {'a':1,'k':[...]}"""
    vals = _smil_value_list(anim_el.get("values", str(base)))
    if not vals: vals = [base]
    times = _smil_time_list(anim_el.get("keyTimes", ""), len(vals))
    dur = _smil_dur_to_frames(anim_el.get("dur", "1s"))
    rc = (anim_el.get("repeatCount") or "1").strip().lower()
    infinite = (rc == "indefinite")
    try:
        n_cycles = 9999 if infinite else max(1, int(float(rc)))
    except ValueError:
        n_cycles = 1

    keys = []
    for c in range(n_cycles):
        for i, (tr, v) in enumerate(zip(times, vals)):
            t = c * dur + tr * dur
            if t > _state.FRAME_OP: break
            entry = {"t": int(round(t)), "s": [v]}
            e = _smil_bezier(anim_el, i)
            if e:
                entry["i"] = e["i"]; entry["o"] = e["o"]
            keys.append(entry)
        if c * dur > _state.FRAME_OP: break

    if not keys or keys[0]["t"] > 0:
        keys.insert(0, {"t": 0, "s": [vals[0]]})
    if keys[-1]["t"] < _state.FRAME_OP:
        keys.append({"t": _state.FRAME_OP, "s": list(keys[-1]["s"])})
    seen = {}
    for k in keys: seen[k["t"]] = k
    return {"a": 1, "k": sorted(seen.values(), key=lambda x: x["t"])}


def parse_filters(root):
    """Собирает filters[id] = float(sigma) либо {'a':1,'k':[...]}."""
    for el in root.iter():
        if not isinstance(el.tag, str): continue
        if el.tag.replace(NS, "") != "filter": continue
        fid = el.get("id")
        if not fid: continue
        for sub in el:
            if not isinstance(sub.tag, str): continue
            if sub.tag.replace(NS, "") == "feGaussianBlur":
                sd = sub.get("stdDeviation", "0")
                base = 0.0
                try: base = float(sd)
                except ValueError: pass
                # ищем <animate attributeName='stdDeviation'>
                anim = None
                for ch in sub:
                    if not isinstance(ch.tag, str): continue
                    if ch.tag.replace(NS, "") != "animate": continue
                    if ch.get("attributeName") == "stdDeviation":
                        anim = ch; break
                if anim is not None:
                    filters[fid] = _smil_to_blur_keys(base, anim)
                else:
                    filters[fid] = base
                break




def bake_alpha_mask(gid, sw_, sh_):
    """Рисует alpha-градиент в grayscale PNG (L) размера sw_ x sh_.
    Возвращает (base64, w, h) или None."""
    import base64, io
    from PIL import Image
    g = _state.gradients.get(gid)
    if not g: return None
    stops = [(s[0], s[2]) for s in g["stops"]]   # (pos, alpha)
    W_, H_ = max(2, int(sw_)), max(2, int(sh_))
    arr = bytearray(W_ * H_)
    if g["type"] == "linear":
        x1, y1 = g.get("s", (0, 0))
        x2, y2 = g.get("e", (1, 0))
        dx, dy = x2 - x1, y2 - y1
        d2 = dx*dx + dy*dy or 1
        for y in range(H_):
            base = y * W_
            for x in range(W_):
                t = ((x - x1*W_)*dx + (y - y1*H_)*dy) / d2
                t = max(0.0, min(1.0, t))
                a = _interp_stops(t, stops)
                arr[base + x] = int(a * 255)
    else:
        cx_, cy_ = g.get("cx", 0.5), g.get("cy", 0.5)
        r_ = g.get("r", 0.5)
        cx_px, cy_px = cx_ * W_, cy_ * H_
        maxr = r_ * max(W_, H_) or 1
        for y in range(H_):
            base = y * W_
            dy_ = y - cy_px
            for x in range(W_):
                t = math.hypot(x - cx_px, dy_) / maxr
                t = max(0.0, min(1.0, t))
                a = _interp_stops(t, stops)
                arr[base + x] = int(a * 255)
    img = Image.frombytes("L", (W_, H_), bytes(arr))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), W_, H_








# ============================================================
# Обход и сборка
# ============================================================

def get_blur(el, ctx_filter):
    f = el.get("filter")
    if f is None:
        f = ctx_filter
    if not f or f == "none": return None
    if f.startswith("url(#"):
        fid = f[5:-1].strip()
        v = filters.get(fid)
        if v is None: return None
        if isinstance(v, dict) and v.get("a") == 1:
            return v
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None






def add_layer(nm, ks, shapes, src_comment, blur_sigma=None):
    L = {"ddd":0,"ind":0,"ty":4,"nm":nm,"sr":1,"ks":ks,"ao":0,
         "shapes":shapes,"ip":0,"op":_state.FRAME_OP,"st":0,"bm":0}
    # alpha-градиент? — помечаем, рендер применит маску сам
    ag = _describe_alpha_gradient(shapes)
    if ag is not None:
        L["alpha_grad"] = ag
    if blur_sigma is not None:
        if isinstance(blur_sigma, (int, float)) and blur_sigma > 0:
            L["blur_sigma"] = float(blur_sigma)
        elif isinstance(blur_sigma, dict) and blur_sigma.get("a") == 1:
            L["blur_sigma"] = blur_sigma
    layers.append(L)
    notes.append({"layer":nm, "source_comment":src_comment,
                  "pos":ks["p"]["k"][:2], "blur_sigma": blur_sigma})


_state.CSS_KF, _state.CSS_RULES, _state.CSS_NTH = {}, {}, {}


def walk_children(parent, ctx, ctx_comment, class_counter):
    pending = None
    batch = []

    def flush():
        nonlocal pending, batch
        if not batch: return
        n = len(batch)
        if pending:
            for i, it in enumerate(batch):
                lbl = f"{pending} #{i+1}" if n > 1 else pending
                walk(it, ctx, lbl, class_counter)
        elif ctx_comment and n > 1:
            for i, it in enumerate(batch):
                walk(it, ctx, f"{ctx_comment} #{i+1}", class_counter)
        else:
            for it in batch: walk(it, ctx, ctx_comment, class_counter)
        batch = []; pending = None

    for c in parent:
        if c.tag is ET.Comment:
            flush(); pending = (c.text or "").strip(); continue
        if not isinstance(c.tag, str): continue
        batch.append(c)
    flush()








def walk(el, ctx, ctx_comment, class_counter):
    def _gf(val, default=0.0):
        if val is None: return default
        try: return float(val)
        except (ValueError, TypeError): return default

    tag = el.tag.replace(NS, "")
    if tag in ("defs","linearGradient","radialGradient","stop","filter",
               "feGaussianBlur","style","title","desc"):
        return

    rule, anim_def, cls = get_css_rule(el, class_counter)

    _style_map = {}
    _style_attr = el.get("style", "") or ""
    if _style_attr:
        for _m in re.finditer(r"([a-zA-Z-]+)\s*:\s*([^;]+)", _style_attr):
            _style_map[_m.group(1).strip()] = re.sub(
                r"\s*!important\s*$", "", _m.group(2).strip())

    def _raw_prop(name, default=None):
        if name in el.attrib: return el.get(name)
        if name in _style_map: return _style_map[name]
        if rule and name in rule: return rule.get(name)
        _pcls = ctx.get("_parent_cls")
        if _pcls:
            _pc = _state.CSS_RULES.get(_pcls + "_child_" + tag)
            if _pc and name in _pc:
                return _pc[name]
        return default

    def _eff_prop(name, ctx_key, default=None):
        v = _raw_prop(name)
        if v is not None: return v
        return ctx.get(ctx_key, default)

    fill = _eff_prop("fill", "fill")
    stroke = _eff_prop("stroke", "stroke")
    sw = _gf(_eff_prop("stroke-width", "sw"), 1.0)
    op = _gf(_eff_prop("opacity", "op"), 1.0)
    fo = _gf(_eff_prop("fill-opacity", "fo"), 1.0)

    if tag == "g":
        new_ctx = dict(ctx)
        if cls:
            new_ctx["_parent_cls"] = cls
        _f = _raw_prop("fill")
        if _f is not None: new_ctx["fill"] = _f
        _s = _raw_prop("stroke")
        if _s is not None: new_ctx["stroke"] = _s
        _sw = _raw_prop("stroke-width")
        if _sw is not None: new_ctx["sw"] = float(_sw)
        _op = _raw_prop("opacity")
        if _op is not None:
            new_ctx["op"] = ctx.get("op", 1.0) * float(_op)
        tstr = el.get("transform", "")
        if tstr:
            new_ctx = _apply_group_transform(new_ctx, tstr)
        if "filter" in el.attrib:
            new_ctx["filter"] = el.get("filter")
        before = len(layers)
        walk_children(el, new_ctx, ctx_comment, class_counter)
        if anim_def:
            origin = parse_origin(rule.get("transform-origin"),
                                  new_ctx.get("bw", 0), new_ctx.get("bh", 0))
            for L in layers[before:]:
                pk = L["ks"]["p"]["k"]
                if not isinstance(pk, list) or not pk: continue
                if isinstance(pk[0], dict):
                    cx_, cy_ = pk[0]["s"][0], pk[0]["s"][1]
                else:
                    cx_, cy_ = pk[0], pk[1]
                if origin:
                    lcx = L.get("_lcx", 0.0); lcy = L.get("_lcy", 0.0)
                    ax = origin[0] - lcx
                    ay = origin[1] - lcy
                    old_a = L["ks"].get("a")
                    if old_a is None:
                        L["ks"]["a"] = {"a":0, "k":[ax, ay, 0]}
                    elif old_a.get("a", 0) == 0:
                        ov = old_a["k"]
                        L["ks"]["a"] = {"a":0, "k":[ov[0]+ax, ov[1]+ay, 0]}
                    _shift_pos(L, ax, ay)
                    cx_ += ax; cy_ += ay
                apply_css_anim(L, rule, anim_def, _state.CSS_KF,
                               cx_, cy_, 0, 0, own_anim=False)
        return

    name = ctx_comment or tag

    def build_one(shape_item, cx_, cy_, bw, bh):
        items = [shape_item]
        f = build_fill(fill, cx_, cy_, bw, bh)
        if f: items.append(f)
        if stroke and stroke != "none":
            sc = hex2rgb(stroke)
            if sc: items.append(st_item(sc, sw))
        items.append(tr_item())
        # применяем group transform к центру
        cxr, cyr = _transform_point(cx_, cy_, ctx)
        # масштаб размеров для Lottie scale
        gsx = ctx.get("sx", 1.0); gsy = ctx.get("sy", 1.0)
        ks = make_ks((cxr, cyr), 100*op)
        if abs(gsx-1.0) > 1e-6 or abs(gsy-1.0) > 1e-6:
            ks["s"] = {"a":0, "k":[gsx*100, gsy*100, 100]}
        if abs(ctx.get("rot", 0.0)) > 1e-6:
            ks["r"] = {"a":0, "k":ctx["rot"]}
        blur = get_blur(el, ctx.get("filter"))
        add_layer(name, ks, [{"ty":"gr","nm":name,"it":items}],
                  ctx_comment, blur)
        layers[-1]["_lcx"] = cx_; layers[-1]["_lcy"] = cy_
        if anim_def:
            apply_css_anim(layers[-1], rule, anim_def, _state.CSS_KF,
                           cxr, cyr, bw, bh, own_anim=True)

    if tag == "image":
        # <image href="..." x=".." y=".." width=".." height=".." opacity=".."/>
        href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href")
        if not href:
            return
        x = _gf(el.get("x")); y = _gf(el.get("y"))
        w = _gf(el.get("width")); h = _gf(el.get("height"))
        if w <= 0 or h <= 0:
            return
        aid = register_image_asset(href, w, h)
        if not aid:
            return
        cx_, cy_ = x + w/2, y + h/2
        cxr, cyr = _transform_point(cx_, cy_, ctx)
        # scale: размер в Lottie = исходный размер картинки в пикселях,
        # поэтому масштабируем: s = (w/asset_w)*100
        asset = next(a for a in assets if a["id"] == aid)
        sw_asset = asset["w"]; sh_asset = asset["h"]
        sx = w / sw_asset * 100
        sy = h / sh_asset * 100
        gsx = ctx.get("sx", 1.0); gsy = ctx.get("sy", 1.0)
        ks = {
            "o": {"a":0, "k":100*op},
            "r": {"a":0, "k":ctx.get("rot", 0.0)},
            "p": {"a":0, "k":[cxr, cyr, 0]},
            "a": {"a":0, "k":[sw_asset/2, sh_asset/2, 0]},
            "s": {"a":0, "k":[sx*gsx, sy*gsy, 100]},
        }
        blur = get_blur(el, ctx.get("filter"))
        L = {"ddd":0,"ind":0,"ty":2,"nm":name,"sr":1,"ks":ks,"ao":0,
             "refId": aid,"ip":0,"op":_state.FRAME_OP,"st":0,"bm":0}
        if blur is not None:
            L["blur_sigma"] = blur
        layers.append(L)
        notes.append({"layer":name, "source_comment":ctx_comment,
                      "pos":[cxr, cyr], "blur_sigma": blur})
        # CSS на image
        if anim_def:
            apply_css_anim(layers[-1], rule, anim_def, _state.CSS_KF,
                           cxr, cyr, w, h, own_anim=True)
        return

    if tag == "foreignObject":
        # mermaid вставляет текст через <foreignObject><div>...</div></foreignObject>
        raw_text = " ".join("".join(el.itertext()).split())
        if not raw_text:
            return
        fx = _gf(el.get("x")); fy = _gf(el.get("y"))
        fw = _gf(el.get("width")); fh = _gf(el.get("height"))
        if fw <= 0 or fh <= 0:
            return
        fs = _gf(ctx.get("fs"), 16.0)
        if fs <= 0: fs = 16.0
        _fc = fill
        if not _fc or _fc == "none":
            _fc = "#000000"
        _crgb = hex2rgb(_fc)
        if _crgb is None:
            _crgb = [0.0, 0.0, 0.0]
        _crgb255 = tuple(int(round(c * 255)) for c in _crgb)
        _tex_png = pathlib.Path(_TEXT_DIR) / f"_text_{len(assets)}.png"
        make_text_png(raw_text, int(fs), _crgb255, str(_tex_png))
        from PIL import Image as _PILimg
        w_px, h_px = _PILimg.open(str(_tex_png)).size
        _aid = register_image_asset(str(_tex_png), w_px, h_px)
        if not _aid:
            return
        cx_local = fx + fw / 2.0
        cy_local = fy + fh / 2.0
        cxr, cyr = _transform_point(cx_local, cy_local, ctx)
        _asset = next((a for a in assets if a["id"] == _aid), None)
        if not _asset:
            return
        _sw = _asset["w"]; _sh = _asset["h"]
        _gsx = ctx.get("sx", 1.0); _gsy = ctx.get("sy", 1.0)
        _ks = {
            "o": {"a": 0, "k": 100 * op},
            "r": {"a": 0, "k": ctx.get("rot", 0.0)},
            "p": {"a": 0, "k": [cxr, cyr, 0]},
            "a": {"a": 0, "k": [_sw / 2.0, _sh / 2.0, 0]},
            "s": {"a": 0, "k": [100 * _gsx, 100 * _gsy, 100]},
        }
        _L = {"ddd": 0, "ind": 0, "ty": 2,
              "nm": raw_text[:24] or "fo-text",
              "sr": 1, "ks": _ks, "ao": 0,
              "refId": _aid, "ip": 0, "op": _state.FRAME_OP, "st": 0, "bm": 0}
        layers.append(_L)
        notes.append({"layer": raw_text[:24] or "fo-text",
                      "source_comment": ctx_comment,
                      "pos": [cxr, cyr], "blur_sigma": None})
        return

    if tag == "text":
        raw_text = " ".join("".join(el.itertext()).split())
        if not raw_text:
            return
        tx = _gf(el.get("x")); ty_b = _gf(el.get("y"))
        fs = _gf(_eff_prop("font-size", "fs"), 16.0)
        if fs <= 0: fs = 16.0
        _anchor = el.get("text-anchor") or ctx.get("text-anchor", "start")
        _dom_base = el.get("dominant-baseline") or ctx.get("dominant-baseline", "")
        _fc = fill
        if not _fc or _fc == "none":
            _fc = "#000000"
        _crgb = hex2rgb(_fc)
        if _crgb is None:
            _crgb = [0.0, 0.0, 0.0]
        _crgb255 = tuple(int(round(c * 255)) for c in _crgb)
        _tex_png = pathlib.Path(_TEXT_DIR) / f"_text_{len(assets)}.png"
        make_text_png(raw_text, int(fs), _crgb255, str(_tex_png))
        from PIL import Image as _PILimg
        w_px, h_px = _PILimg.open(str(_tex_png)).size
        _aid = register_image_asset(str(_tex_png), w_px, h_px)
        if not _aid:
            return
        if _dom_base in ("middle", "central"):
            cy_local = ty_b
        elif _dom_base == "hanging":
            cy_local = ty_b + fs * 0.35
        else:
            cy_local = ty_b - fs * 0.35
        if _anchor == "middle":
            cx_local = tx
        elif _anchor == "end":
            cx_local = tx - w_px / 2.0
        else:
            cx_local = tx + w_px / 2.0
        cxr, cyr = _transform_point(cx_local, cy_local, ctx)
        _asset = next((a for a in assets if a["id"] == _aid), None)
        if not _asset:
            return
        _sw = _asset["w"]; _sh = _asset["h"]
        _gsx = ctx.get("sx", 1.0); _gsy = ctx.get("sy", 1.0)
        _ks = {
            "o": {"a": 0, "k": 100 * op},
            "r": {"a": 0, "k": ctx.get("rot", 0.0)},
            "p": {"a": 0, "k": [cxr, cyr, 0]},
            "a": {"a": 0, "k": [_sw / 2.0, _sh / 2.0, 0]},
            "s": {"a": 0, "k": [100 * _gsx, 100 * _gsy, 100]},
        }
        _L = {"ddd": 0, "ind": 0, "ty": 2,
              "nm": raw_text[:24] or "text",
              "sr": 1, "ks": _ks, "ao": 0,
              "refId": _aid, "ip": 0, "op": _state.FRAME_OP, "st": 0, "bm": 0}
        layers.append(_L)
        notes.append({"layer": raw_text[:24] or "text",
                      "source_comment": ctx_comment,
                      "pos": [cxr, cyr], "blur_sigma": None})
        return

    if tag == "rect":
        x = _gf(el.get("x")); y = _gf(el.get("y"))
        w = _gf(el.get("width")); h = _gf(el.get("height"))
        if w <= 0 or h <= 0: return
        rx = _gf(el.get("rx") or el.get("r") or 0)
        build_one(rc_item(w, h, rx), x+w/2, y+h/2, w, h)
        _apply_smil_at_to_last(el, ctx, x+w/2, y+h/2)
        return
    if tag == "circle":
        cx_ = _gf(el.get("cx")); cy_ = _gf(el.get("cy")); r = _gf(el.get("r"))
        build_one(el_item(r), cx_, cy_, r*2, r*2)
        # SMIL: r -> scale, cx/cy -> p
        smil = collect_smil_anims(el)
        if smil:
            L = layers[-1]
            if "r" in smil:
                # scale = r(t)/r0
                r0 = r if r > 0 else (smil["r"]["k"][0]["s"][0] or 1.0)
                keys = [{"t": kf["t"],
                         "s": [kf["s"][0]/r0*100, kf["s"][0]/r0*100, 100]}
                        for kf in smil["r"]["k"]]
                L["ks"]["s"] = {"a": 1, "k": keys}
            if "cx" in smil or "cy" in smil:
                # собираем совмещённую сетку t
                tcx = {kf["t"]: kf["s"][0] for kf in smil.get("cx", {"k":[]})["k"]} if "cx" in smil else {}
                tcy = {kf["t"]: kf["s"][0] for kf in smil.get("cy", {"k":[]})["k"]} if "cy" in smil else {}
                ts = sorted(set(list(tcx.keys()) + list(tcy.keys())))
                if ts:
                    keys = []
                    for t in ts:
                        xv = tcx.get(t, cx_)
                        yv = tcy.get(t, cy_)
                        keys.append({"t": t, "s": [xv, yv, 0]})
                    L["ks"]["p"] = {"a": 1, "k": keys}
        _apply_smil_at_to_last(el, ctx, cx_, cy_)
        return
    if tag == "ellipse":
        cx_ = _gf(el.get("cx")); cy_ = _gf(el.get("cy"))
        rx = _gf(el.get("rx")); ry = _gf(el.get("ry"))
        build_one(el_item(rx, ry), cx_, cy_, rx*2, ry*2)
        _apply_smil_at_to_last(el, ctx, cx_, cy_)
        return
    if tag == "line":
        x1 = _gf(el.get("x1")); y1 = _gf(el.get("y1"))
        x2 = _gf(el.get("x2")); y2 = _gf(el.get("y2"))
        cx_, cy_ = (x1+x2)/2, (y1+y2)/2
        cxr, cyr = _transform_point(cx_, cy_, ctx)
        local = [(x1-cx_, y1-cy_), (x2-cx_, y2-cy_)]
        sc = hex2rgb(stroke) if stroke and stroke != "none" else [1,1,1]
        items = [sh_item(local, closed=False)]
        items.append(st_item(sc, sw, 100*op))
        items.append(tr_item())
        blur = get_blur(el, ctx.get("filter"))
        ks = make_ks((cxr, cyr), 100)
        gsx = ctx.get("sx", 1.0); gsy = ctx.get("sy", 1.0)
        if abs(gsx-1.0) > 1e-6 or abs(gsy-1.0) > 1e-6:
            ks["s"] = {"a":0, "k":[gsx*100, gsy*100, 100]}
        if abs(ctx.get("rot", 0.0)) > 1e-6:
            ks["r"] = {"a":0, "k":ctx["rot"]}
        add_layer(name, ks,
                  [{"ty":"gr","nm":name,"it":items}], ctx_comment, blur)
        layers[-1]["_lcx"] = cx_; layers[-1]["_lcy"] = cy_
        if anim_def: apply_css_anim(layers[-1], rule, anim_def, _state.CSS_KF,
                                    cxr, cyr, 0, 0, own_anim=True)
        return
    if tag == "polygon":
        pts = parse_points(el.get("points"))
        cx_ = sum(p[0] for p in pts)/len(pts)
        cy_ = sum(p[1] for p in pts)/len(pts)
        local = [(p[0]-cx_, p[1]-cy_) for p in pts]
        bw = max(p[0] for p in pts) - min(p[0] for p in pts)
        bh = max(p[1] for p in pts) - min(p[1] for p in pts)
        build_one(sh_item(local, True), cx_, cy_, bw, bh)
        smil = collect_smil_anims(el)
        if "opacity" in smil:
            L = layers[-1]
            keys = [{"t": kf["t"], "s": [kf["s"][0]*100]} for kf in smil["opacity"]["k"]]
            L["ks"]["o"] = {"a": 1, "k": keys}
        _apply_smil_at_to_last(el, ctx, cx_, cy_)
        return
    if tag == "path":
        sps = parse_path(el.get("d",""))
        if not sps: return
        allpts = [p for sp in sps for p in sp["v"]]
        minx = min(p[0] for p in allpts); maxx = max(p[0] for p in allpts)
        miny = min(p[1] for p in allpts); maxy = max(p[1] for p in allpts)
        cx_ = (minx+maxx)/2; cy_ = (miny+maxy)/2
        bw = maxx-minx; bh = maxy-miny
        cxr, cyr = _transform_point(cx_, cy_, ctx)
        items = []
        for sp in sps:
            if len(sp["v"]) < 2: continue
            local_sp = {"v":[(p[0]-cx_, p[1]-cy_) for p in sp["v"]],
                        "o":sp["o"][:], "i":sp["i"][:], "c":sp.get("c", True)}
            items.append(sh_item(local_sp, True))
        if not items: return
        f = build_fill(fill, cx_, cy_, bw, bh)
        if f: items.append(f)
        if stroke and stroke != "none":
            sc = hex2rgb(stroke)
            if sc: items.append(st_item(sc, sw))
        items.append(tr_item())
        blur = get_blur(el, ctx.get("filter"))
        ks = make_ks((cxr, cyr), 100*op)
        gsx = ctx.get("sx", 1.0); gsy = ctx.get("sy", 1.0)
        if abs(gsx-1.0) > 1e-6 or abs(gsy-1.0) > 1e-6:
            ks["s"] = {"a":0, "k":[gsx*100, gsy*100, 100]}
        if abs(ctx.get("rot", 0.0)) > 1e-6:
            ks["r"] = {"a":0, "k":ctx["rot"]}
        add_layer(name, ks,
                  [{"ty":"gr","nm":name,"it":items}], ctx_comment, blur)
        layers[-1]["_lcx"] = cx_; layers[-1]["_lcy"] = cy_
        if anim_def: apply_css_anim(layers[-1], rule, anim_def, _state.CSS_KF,
                                    cxr, cyr, bw, bh, own_anim=True)
        return


def svg_main(src, dst):
    global VB_W, VB_H, _TEXT_DIR
    src_p = pathlib.Path(src)
    if not src_p.suffix:
        for ext in (".svg", ".SVG"):
            cand = src_p.with_suffix(ext)
            if cand.exists():
                src = str(cand); src_p = cand; break
        else:
            sys.exit(f"Не найден файл: {src}(.svg)")
    dst_p = pathlib.Path(dst)
    if not dst_p.suffix:
        dst_p = dst_p.with_suffix(".lotty")
    dst = str(dst_p)
    _TEXT_DIR = dst_p.parent
    text = pathlib.Path(src).read_text(encoding="utf-8")
    _state.CSS_KF, _state.CSS_RULES, _state.CSS_NTH = parse_css(text)

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(text, parser=parser)
    vb = root.get("viewBox") or ""
    if vb:
        p = [float(x) for x in vb.replace(",", " ").split()]
        if len(p) == 4: VB_W, VB_H = p[2], p[3]
    parse_gradients(root)
    parse_filters(root)

    class_counter = {}
    _initial_ctx = {"op": 1.0}
    _root_id = root.get("id")
    if _root_id and _state.CSS_RULES.get("#" + _root_id):
        _root_rule = _state.CSS_RULES["#" + _root_id]
        for _css_k, _ctx_k in (("fill", "fill"), ("stroke", "stroke"),
                                ("stroke-width", "sw"), ("font-size", "fs")):
            if _css_k in _root_rule:
                _v = _root_rule[_css_k]
                if _css_k in ("stroke-width", "font-size"):
                    try:
                        _v = float(str(_v).replace("px", "").replace("pt", ""))
                    except (TypeError, ValueError):
                        continue
                _initial_ctx[_ctx_k] = _v
    for _attr_k, _ctx_k in (("fill", "fill"), ("stroke", "stroke"),
                             ("stroke-width", "sw"), ("font-size", "fs")):
        if _attr_k in root.attrib:
            _v = root.get(_attr_k)
            if _attr_k in ("stroke-width", "font-size"):
                try:
                    _v = float(str(_v).replace("px", "").replace("pt", ""))
                except (TypeError, ValueError):
                    continue
            _initial_ctx[_ctx_k] = _v
    walk_children(root, _initial_ctx, None, class_counter)

    # Есть ли анимация?
    has_anim = bool(_state.CSS_KF) or any(
        isinstance(el.tag, str) and "animate" in el.tag.lower()
        for el in root.iter()
    )
    if not has_anim:
        _state.FRAME_OP = 1

    # Переустанавливаем op у слоёв, если _state.FRAME_OP изменился
    for L in layers:
        L["op"] = _state.FRAME_OP

    for L in layers:
        L.pop("_lcx", None); L.pop("_lcy", None)

    layers_rev = list(reversed(layers))
    for i, L in enumerate(layers_rev): L["ind"] = i + 1
    notes_rev = list(reversed(notes))
    for i, n in enumerate(notes_rev): n["stack_order"] = i

    doc = {"v":"5.9.0","fr":_state.FR,"ip":0,"op":_state.FRAME_OP,
           "w":int(VB_W),"h":int(VB_H),"nm":pathlib.Path(dst).stem,
           "ddd":0,"assets":assets,"layers":layers_rev,
           "meta":{"source":src,"converter":"svg2lottie.py v3.2 (CSS anim + image)",
                   "notes":notes_rev}}
    pathlib.Path(dst).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    n_anim = sum(1 for L in layers_rev if any(
        isinstance(L["ks"][k], dict) and L["ks"][k].get("a") == 1
        for k in ("p","r","s","o")
    ))
    print(f"OK: {len(layers_rev)} слоёв, {n_anim} анимированы -> {dst}")
    print(f"    _state.FRAME_OP = {_state.FRAME_OP}, keyframes в CSS: {len(_state.CSS_KF)}")


# ============================================================
# HTML/CSS/JS -> Lottie (из html2lottie)
# ============================================================







# ============================================================
# Парсеры CSS
# ============================================================





from _color import _NAMED_COLORS, hex2rgb, _parse_color  # refactored




# ============================================================
# Парсер lengths
# ============================================================

def parse_drop_shadows(filter_str):
    """filter: drop-shadow(0 0 18px rgba(...)) ... -> [dict, ...]
    Возвращает список теней в порядке следования."""
    if not filter_str: return []
    result = []
    for mm in re.finditer(r"drop-shadow\(([^)]+)\)", filter_str):
        args = mm.group(1).strip()
        # разбор токенов верхнего уровня (rgba(...) — один токен)
        parts, cur, depth = [], "", 0
        for ch in args:
            if ch == "(": depth += 1; cur += ch
            elif ch == ")": depth -= 1; cur += ch
            elif ch == " " and depth == 0:
                if cur.strip(): parts.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip(): parts.append(cur.strip())
        if len(parts) < 3: continue
        try:
            dx = float(parts[0].replace("px",""))
            dy = float(parts[1].replace("px",""))
            blur = float(parts[2].replace("px",""))
        except ValueError:
            continue
        color = (0,0,0); op = 1.0
        if len(parts) > 3:
            color = _parse_color(parts[3]) or (0,0,0)
            m2 = re.match(r"rgba\([^)]*,\s*([\d.]+)\)", parts[3])
            if m2: op = float(m2.group(1))
        result.append({"dx": dx, "dy": dy, "blur": blur,
                       "color": color, "opacity": op})
    return result


def _paint_shape_color(item, color):
    """Рекурсивно перекрашивает все fl-заливки в заданный цвет."""
    if isinstance(item, dict):
        if item.get("ty") == "fl":
            item["c"] = {"a": 0, "k": [c/255 for c in color]}
        for v in item.values():
            if isinstance(v, (dict, list)):
                _paint_shape_color(v, color)
    elif isinstance(item, list):
        for v in item:
            _paint_shape_color(v, color)


def clone_with_shadows(layer, shadows):
    """Возвращает [копия1, копия2, ...] — каждая со сдвигом, blur_sigma
    и перекрашенной заливкой в цвет тени."""
    out = []
    for sh in shadows:
        import copy as _copy
        Lc = _copy.deepcopy(layer)
        Lc["nm"] = str(Lc.get("nm","")) + "_glow"
        pk = Lc["ks"].get("p")
        if pk:
            if pk.get("a", 0) == 0:
                pk["k"] = [pk["k"][0]+sh["dx"], pk["k"][1]+sh["dy"], 0]
            else:
                for kf in pk["k"]:
                    kf["s"] = [kf["s"][0]+sh["dx"], kf["s"][1]+sh["dy"], 0]
        Lc["blur_sigma"] = sh["blur"]
        _paint_shape_color(Lc.get("shapes", []), sh["color"])
        # множитель прозрачности
        if sh["opacity"] < 1.0:
            ok = Lc["ks"].get("o", {"a":0,"k":100})
            if ok.get("a", 0) == 0:
                Lc["ks"]["o"] = {"a":0, "k": ok["k"]*sh["opacity"]}
            else:
                for kf in ok["k"]:
                    kf["s"] = [kf["s"][0]*sh["opacity"]]
        # для image-слоёв — если бы были, здесь нужна тонировка. Пока пропускаем
        out.append(Lc)
    return out


def apply_shadows(layers, filter_str, layers_out):
    """Добавляет копии-свечения ПЕРЕД оригиналами."""
    shadows = parse_drop_shadows(filter_str)
    if not shadows:
        layers_out.extend(layers)
        return
    for L in layers:
        layers_out.extend(clone_with_shadows(L, shadows))
        layers_out.append(L)


def h2l_resolve_length(val, base):
    """'8%' -> 0.08*base, '12vw' -> 0.12*_state.CANVAS_W, 'clamp(a,b,c)', '80px'."""
    if val is None: return None
    val = str(val).strip()
    cm = re.match(r"clamp\(([^)]+)\)", val)
    if cm:
        parts = [x.strip() for x in cm.group(1).split(",")]
        vals = [h2l_resolve_length(p, base) for p in parts]
        vals = [v for v in vals if v is not None]
        if len(vals) == 3: return max(vals[0], min(vals[1], vals[2]))
        return vals[0] if vals else None
    m = re.match(r"(-?[\d.]+)(px|%|vw|vh|em|rem)?\s*$", val)
    if not m: return None
    n = float(m.group(1)); unit = m.group(2) or "px"
    if unit == "px": return n
    if unit == "%": return n/100 * base
    if unit == "vw": return n/100 * _state.CANVAS_W
    if unit == "vh": return n/100 * _state.CANVAS_H
    if unit in ("em", "rem"): return n * 16
    return n

# ============================================================
# Парсеры HTML
# ============================================================

def extract_svgs(html):
    """Возвращает [{cls, svg, vb, par, comment}, ...] для всех <svg class="...">.
    comment — ближайший <!-- ... --> перед <svg> (если есть)."""
    out = []
    for mm in re.finditer(r"<svg\s+([^>]*?)>(.*?)</svg>", html, re.DOTALL):
        start = mm.start()
        attrs, body = mm.group(1), mm.group(2)
        prev = html[max(0, start-400):start]
        comment = None
        cm0 = re.findall(r"<!--([^<]*?)-->", prev)
        if cm0:
            comment = cm0[-1].strip()
            comment = re.sub(r"\s*[^\x00-\x7F\u0400-\u04FF]+\s*$",
                             "", comment).strip()
        cm = re.search(r'class\s*=\s*"([^"]+)"', attrs)
        cls = cm.group(1).strip() if cm else ""
        vm = re.search(r'viewBox\s*=\s*"([^"]+)"', attrs)
        vb = vm.group(1) if vm else "0 0 100 100"
        par = re.search(r'preserveAspectRatio\s*=\s*"([^"]+)"', attrs)
        par = par.group(1) if par else None
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + vb + '"'
               + (f' preserveAspectRatio="{par}"' if par else '') + '>'
               + body + '</svg>')
        out.append({"cls": cls, "svg": svg, "vb": vb, "par": par,
                    "comment": comment})
    return out


def extract_divs(html):
    """Найти <div class="X"> с CSS-правилом и фоном (v1: сплошной цвет).
    Возвращает [{cls, props}, ...] — по одному на каждый <div>.
    Ограничения v1: без вложенности, без градиентов, без border-radius.
    """
    out = []
    for mm in re.finditer(r'<div\s+([^>]*?)>', html):
        attrs = mm.group(1)
        cm = re.search(r'class\s*=\s*"([^"]+)"', attrs)
        if not cm:
            continue
        cls = cm.group(1).strip().split()[0]
        if not cls:
            continue
        props = css_rule_for(cls, _state.CSS_RULES)
        if not props:
            continue
        bg = props.get("background") or props.get("background-color")
        if not bg:
            continue
        if "gradient" in bg.lower():
            continue
        out.append({"cls": cls, "props": props})
    return out


def _div_bg_color(props):
    """Из CSS props берём сплошной цвет: background или background-color."""
    bg = (props.get("background-color") or props.get("background") or "").strip()
    m = re.match(r"^(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)|hsla?\([^)]+\)|[a-zA-Z]+)", bg)
    return m.group(1) if m else None


def apply_divs(html, all_layers, all_assets):
    """<div class="X"> с CSS-фоном -> прямоугольный слой (через SVG-rect)."""
    for info in extract_divs(html):
        cls = info["cls"]
        props = info["props"]
        box = compute_box(props)
        if not box or box["w"] is None or box["h"] is None:
            print(f"  [div .{cls}] пропуск: box без w/h")
            continue
        if box["w"] <= 0 or box["h"] <= 0:
            print(f"  [div .{cls}] пропуск: w/h <= 0")
            continue
        color = _div_bg_color(props)
        if not color:
            print(f"  [div .{cls}] пропуск: нет цвета")
            continue
        w, h = box["w"], box["h"]
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
            + str(w) + " " + str(h) + '">'
            + '<rect x="0" y="0" width="' + str(w) + '" height="' + str(h)
            + '" fill="' + color + '"/>'
            + '</svg>'
        )
        sub_layers, sub_assets, _ = svg_to_layers(
            svg, "0 0 " + str(w) + " " + str(h), prefix=cls)
        for L in sub_layers:
            transform_layer(L, box["left"], box["top"], 1.0, 1.0)
        anim = parse_anim_shorthand(props.get("animation", ""))
        if anim:
            apply_animation_to_layers(sub_layers, anim)
        apply_shadows(sub_layers, props.get("filter", ""), all_layers)
        all_assets.extend(sub_assets)
        print(f"  [div .{cls}] {w:.0f}x{h:.0f} -> {len(sub_layers)} слоёв")



def extract_imgs(html):
    """[{cls, src, alt}, ...] для всех <img> с классом."""
    out = []
    for mm in re.finditer(r"<img\s+([^>]*?)/?>", html):
        attrs = mm.group(1)
        cm = re.search(r'class\s*=\s*"([^"]+)"', attrs)
        src = re.search(r'src\s*=\s*"([^"]+)"', attrs)
        alt = re.search(r'alt\s*=\s*"([^"]*)"', attrs)
        if not cm or not src: continue
        out.append({"cls": cm.group(1).strip(),
                    "src": src.group(1).strip(),
                    "alt": alt.group(1) if alt else ""})
    return out


def extract_texts(html):
    """Ищет <h1 class>…</h1> (с <span>), <p class>…</p>."""
    out = []
    # <h1 class="X">...</h1>
    for mm in re.finditer(r"<(h[1-6])\s+([^>]*)>(.*?)</\1>", html, re.DOTALL):
        tag, attrs, body = mm.group(1), mm.group(2), mm.group(3)
        cm = re.search(r'class\s*=\s*"([^"]+)"', attrs)
        if not cm: continue
        cls = cm.group(1).strip()
        # внутри есть <span>?
        spans = re.findall(r"<span[^>]*>(.*?)</span>", body, re.DOTALL)
        # нормализуем: убираем переносы, схлопываем пробелы, но сохраняем
        # границы слов: между span-ами в HTML есть whitespace => пробел
        body_norm = re.sub(r"\s+", " ", body)
        clean = re.sub(r"<[^>]+>", "", body_norm)
        clean = re.sub(r"\s+", " ", clean).strip()
        # пустой span (class="gap") -> добавляем пробел
        spans_clean = []
        for sp in spans:
            t = re.sub(r"<[^>]+>", "", sp)
            if not t.strip():
                spans_clean.append(" ")
            else:
                spans_clean.append(t.strip())
        out.append({"kind": "h", "tag": tag, "cls": cls, "text": clean,
                    "spans": spans_clean})
    # <p class="X">текст</p>
    for mm in re.finditer(r"<p\s+([^>]*)>(.*?)</p>", html, re.DOTALL):
        attrs, body = mm.group(1), mm.group(2)
        cm = re.search(r'class\s*=\s*"([^"]+)"', attrs)
        im = re.search(r'id\s*=\s*"([^"]+)"', attrs)
        if not cm and not im: continue
        cls = cm.group(1).strip() if cm else ""
        pid = im.group(1) if im else ""
        clean = re.sub(r"<[^>]+>", "", body).strip()
        out.append({"kind": "p", "cls": cls, "id": pid, "text": clean})
    return out


def extract_js_year(js_text):
    """Ищет присвоение #year. Понимает как литерал, так и new Date().getFullYear() + N."""
    m = re.search(r"getElementById\(['\"]year['\"]\)[\s\S]{0,200}?"
                  r"(?:textContent|innerText|innerHTML)\s*=\s*([^;]+);",
                  js_text)
    if not m: return None
    expr = m.group(1).strip()
    # литерал
    lit = re.match(r"['\"]([^'\"]+)['\"]$", expr)
    if lit: return lit.group(1)
    # new Date().getFullYear() [+/- N]
    if "getFullYear" in expr:
        import datetime
        base = datetime.date.today().year
        mm = re.search(r"getFullYear\(\)\s*([+-])\s*(\d+)", expr)
        if mm:
            n = int(mm.group(2))
            return str(base + n if mm.group(1) == "+" else base - n)
        return str(base)
    return None


# ============================================================
# Позиционирование и анимации
# ============================================================



def compute_box(props):
    """По CSS-свойствам элемента (.class { position, top, left, width, ... })
    вычисляет x, y, w, h на холсте."""
    if not props: return None
    w = h2l_resolve_length(props.get("width"), _state.CANVAS_W)
    h = h2l_resolve_length(props.get("height"), _state.CANVAS_H)
    left = h2l_resolve_length(props.get("left"), _state.CANVAS_W)
    right = h2l_resolve_length(props.get("right"), _state.CANVAS_W)
    top = h2l_resolve_length(props.get("top"), _state.CANVAS_H)
    bottom = h2l_resolve_length(props.get("bottom"), _state.CANVAS_H)

    if left is None and right is not None and w is not None:
        left = _state.CANVAS_W - right - w
    if top is None and bottom is not None and h is not None:
        top = _state.CANVAS_H - bottom - h
    if left is None: left = 0
    if top is None: top = 0
    return {"left": left, "top": top, "w": w, "h": h}











# ============================================================
# SVG -> слои Lottie
# ============================================================

def svg_to_layers(svg_text, vb, prefix=None):
    global VB_W, VB_H
    layers.clear(); assets.clear(); notes.clear()
    _state.gradients.clear(); filters.clear()
    vb_parts = [float(x) for x in vb.replace(",", " ").split()]
    if len(vb_parts) == 4:
        VB_W, VB_H = vb_parts[2], vb_parts[3]
    else:
        VB_W, VB_H = 100, 100
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(svg_text, parser=parser)
    parse_gradients(root)
    parse_filters(root)
    walk_children(root, {"op": 1.0}, prefix, {})
    return list(layers), list(assets), list(notes)


def transform_layer(L, left, top, sx, sy):
    pk = L["ks"]["p"]
    if pk.get("a", 0) == 0:
        pk["k"] = [left + pk["k"][0]*sx, top + pk["k"][1]*sy, 0]
    else:
        for kf in pk["k"]:
            kf["s"] = [left + kf["s"][0]*sx, top + kf["s"][1]*sy, 0]
    sk = L["ks"]["s"]
    if sk.get("a", 0) == 0:
        sk["k"] = [sk["k"][0]*sx, sk["k"][1]*sy, 100]
    else:
        for kf in sk["k"]:
            kf["s"] = [kf["s"][0]*sx, kf["s"][1]*sy, 100]




# ============================================================
# Фон: body { background } + body::before { звёзды }
# ============================================================

def apply_body_background(layers_out, assets_out):
    """Если _state.CSS_BODY['background'] — парсим градиент и создаём image-слой."""
    bg = _state.CSS_BODY.get("background") or _state.CSS_BODY.get("background-image")
    if not bg: return
    grad = parse_gradient(bg)
    if not grad: return
    png = ROOT / "_bg.png"
    make_gradient_png(grad, _state.CANVAS_W, _state.CANVAS_H, str(png))
    asset, layer = make_image_layer(str(png), _state.CANVAS_W/2, _state.CANVAS_H/2,
                                    "bg", "img_bg",
                                    scale_x=_state.CANVAS_W, scale_y=_state.CANVAS_H)
    layers_out.append(layer)
    assets_out.append(asset)




def apply_body_stars(layers_out, assets_out):
    """body::before { background-image: N×radial-gradient(at X Y) }
    + animation: twinkle -> N кружков с opacity-keyframes."""
    pseudo = _state.CSS_PSEUDO.get("body::before")
    if not pseudo: return
    bgi = pseudo.get("background-image", "")
    if not bgi: return
    anim = parse_anim_shorthand(pseudo.get("animation", ""))

    positions = []
    for mm in re.finditer(r"radial-gradient\([^)]*?at\s+([\d.]+)%\s+([\d.]+)%", bgi):
        positions.append((float(mm.group(1))/100, float(mm.group(2))/100))
    if not positions: return

    pts = None
    if anim:
        pts = keyframes_to_points(anim["name"], anim["dur"], anim["delay"],
                                  anim["iteration"], anim["direction"])

    for i, (fx, fy) in enumerate(positions):
        x, y = fx*_state.CANVAS_W, fy*_state.CANVAS_H
        r = 2.0  # дефолт
        if pts:
            kf = [{"t": p[0], "s": [p[4]*100]} for p in pts]
        else:
            kf = [{"t": 0, "s": [100]}, {"t": _state.FRAME_OP, "s": [100]}]
        shapes = [{"ty":"gr","nm":f"star{i}","it":[
            {"ty":"el","d":1,"s":{"a":0,"k":[r*2, r*2]},"p":{"a":0,"k":[0,0]}},
            {"ty":"fl","c":{"a":0,"k":[1,1,1]},"o":{"a":0,"k":100},"r":1},
            {"ty":"tr","p":{"a":0,"k":[0,0]},"a":{"a":0,"k":[0,0]},
             "s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},
             "o":{"a":0,"k":100},"sk":{"a":0,"k":0},"sa":{"a":0,"k":0}}
        ]}]
        L = {"ddd":0,"ind":0,"ty":4,"nm":f"star{i}","sr":1,
             "ks":{"o":{"a":1,"k":kf},"r":{"a":0,"k":0},
                   "p":{"a":0,"k":[x,y,0]},"a":{"a":0,"k":[0,0,0]},
                   "s":{"a":0,"k":[100,100,100]}},
             "ao":0,"shapes":shapes,"ip":0,"op":_state.FRAME_OP,"st":0,"bm":0}
        layers_out.append(L)


def make_image_layer(png_path, cx, cy, name, asset_id,
                     scale_x=None, scale_y=None):
    """Создаёт image-слой + asset. Если scale_x/y не None — размер в пикселях."""
    import base64
    from PIL import Image
    data = pathlib.Path(png_path).read_bytes()
    b64 = base64.b64encode(data).decode()
    w, h = Image.open(png_path).size
    asset = {"id": asset_id, "w": w, "h": h, "u": "",
             "p": f"data:image/png;base64,{b64}", "e": 1}
    if scale_x is not None and scale_y is not None:
        sx = scale_x / w * 100
        sy = scale_y / h * 100
    else:
        sx = sy = 100
    layer = {"ddd":0,"ind":0,"ty":2,"nm":name,"sr":1,
             "ks":{"o":{"a":0,"k":100},"r":{"a":0,"k":0},
                   "p":{"a":0,"k":[cx, cy, 0]},
                   "a":{"a":0,"k":[w/2, h/2, 0]},
                   "s":{"a":0,"k":[sx, sy, 100]}},
             "ao":0,"refId":asset_id,"ip":0,"op":_state.FRAME_OP,"st":0,"bm":0}
    return asset, layer


# ============================================================
# <img src="...svg">
# ============================================================



# ============================================================
# Flex-раскладка (для .postcard и подобных)
# ============================================================

from html.parser import HTMLParser as _HTMLParser


class _HTMLNode:
    __slots__ = ("tag", "attrs", "children", "text", "parent")
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.text = ""
        self.parent = parent


class _HTMLTree(_HTMLParser):
    VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base",
            "col", "embed", "source", "track", "wbr"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("__root__", [])
        self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        node = _HTMLNode(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        node = _HTMLNode(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
    def handle_data(self, data):
        self.stack[-1].text += data


def _node_class(node):
    return (node.attrs.get("class") or "").strip()


def _node_id(node):
    return (node.attrs.get("id") or "").strip()


def _find_node_by_class(node, cls):
    if cls in _node_class(node).split():
        return node
    for ch in node.children:
        r = _find_node_by_class(ch, cls)
        if r is not None:
            return r
    return None


def _find_node_by_id(node, nid):
    if _node_id(node) == nid:
        return node
    for ch in node.children:
        r = _find_node_by_id(ch, nid)
        if r is not None:
            return r
    return None


def _find_node_by_tag_class(node, tag, cls):
    if node.tag == tag and cls in _node_class(node).split():
        return node
    for ch in node.children:
        r = _find_node_by_tag_class(ch, tag, cls)
        if r is not None:
            return r
    return None


def _estimate_child_height(node, css_dict, html_dir):
    """Оценка высоты элемента в пикселях холста."""
    cls = _node_class(node)
    props = css_dict.get(cls, {}) if cls else {}
    h = h2l_resolve_length(props.get("height"), _state.CANVAS_H)
    if h is not None and h > 0:
        return h

    tag = node.tag
    if tag == "img":
        src = node.attrs.get("src", "")
        w = h2l_resolve_length(props.get("width"), _state.CANVAS_W)
        if w is None:
            w = _state.CANVAS_W * 0.22
        aspect = 1.15
        svg_path = html_dir / src
        if svg_path.exists():
            try:
                txt = svg_path.read_text(encoding="utf-8")[:2000]
                m = re.search(r'viewBox="([^"]+)"', txt)
                if m:
                    pv = [float(x) for x in m.group(1).replace(",", " ").split()]
                    if len(pv) == 4 and pv[2] > 0:
                        aspect = pv[3] / pv[2]
            except Exception:
                pass
        return w * aspect

    if tag in ("h1","h2","h3","h4","h5","h6","p"):
        fs = h2l_resolve_length(props.get("font-size"), _state.CANVAS_H)
        if fs is None: fs = int(_state.CANVAS_H * 0.06)
        lh = h2l_resolve_length(props.get("line-height"), _state.CANVAS_H)
        return (lh if lh else fs * 1.3)

    if tag in ("div", "main", "section"):
        gap = h2l_resolve_length(props.get("gap"), _state.CANVAS_H) or 0
        total = 0.0
        n = 0
        for ch in node.children:
            if ch.tag in ("style","script"): continue
            total += _estimate_child_height(ch, css_dict, html_dir)
            if n > 0: total += gap
            n += 1
        return total

    return 0.0


def compute_flex_layout(html, css_dict, html_dir):
    """Считает позиции детей flex-контейнеров (только column).
    Возвращает {key: {"cx": x, "cy": y, "h": h}},
    где key = "id:ID" или "cls:NAME" (по классу ребёнка)."""
    tree = _HTMLTree()
    try:
        tree.feed(html)
    except Exception:
        return {}

    layout = {}
    # найти все классы с display:flex
    flex_classes = [c for c, p in css_dict.items()
                    if isinstance(p, dict) and p.get("display") == "flex"]
    for fcls in flex_classes:
        node = _find_node_by_class(tree.root, fcls)
        if node is None:
            continue
        props = css_dict.get(fcls, {})
        if props.get("flex-direction", "row") != "column":
            continue
        children = [c for c in node.children if c.tag not in ("style","script")]
        if not children:
            continue

        justify = props.get("justify-content", "flex-start")
        align = props.get("align-items", "stretch")
        gap = h2l_resolve_length(props.get("gap"), _state.CANVAS_H) or 0.0
        padding = h2l_resolve_length(props.get("padding"), _state.CANVAS_H) or 0.0
        pad_y = h2l_resolve_length(props.get("padding-top"), _state.CANVAS_H) or padding
        pad_x = h2l_resolve_length(props.get("padding-left"), _state.CANVAS_W) or padding

        cont_x = pad_x
        cont_y = pad_y
        cont_w = _state.CANVAS_W - 2 * pad_x
        cont_h = _state.CANVAS_H - 2 * pad_y

        heights = [_estimate_child_height(c, css_dict, html_dir) for c in children]
        total_h = sum(heights) + gap * max(0, len(children) - 1)

        if justify == "center":
            y = cont_y + (cont_h - total_h) / 2.0
        elif justify == "flex-end":
            y = cont_y + cont_h - total_h
        else:
            y = cont_y

        for i, ch in enumerate(children):
            h = heights[i]
            if align == "flex-end":
                cx = cont_x + cont_w
            elif align in ("flex-start",):
                cx = cont_x
            else:
                cx = cont_x + cont_w / 2.0
            cy = y + h / 2.0
            nid = _node_id(ch)
            ncls = _node_class(ch)
            entry = {"cx": cx, "cy": cy, "h": h}
            if nid:
                layout["id:" + nid] = entry
            if ncls:
                first_cls = ncls.split()[0]
                layout.setdefault("cls:" + first_cls, entry)
            y += h + gap
    return layout


def apply_images(html, html_dir, layers_out, assets_out, layout=None):
    """Все <img class="X" src="Y.svg"> — вставляем как SVG-слои.
    Размер берём из CSS .X { width }; анимация — из CSS .X { animation }
    или у родителя .parent { animation }."""
    for img in extract_imgs(html):
        cls = img["cls"]
        src = img["src"]
        if not src.lower().endswith(".svg"):
            continue
        svg_path = html_dir / src
        if not svg_path.exists(): continue
        svg_text = svg_path.read_text(encoding="utf-8")
        props = css_rule_for(cls, _state.CSS_RULES)
        w = h2l_resolve_length(props.get("width"), _state.CANVAS_W)
        if w is None: w = _state.CANVAS_W * 0.22  # fallback
        vb_m = re.search(r'viewBox="([^"]+)"', svg_text)
        vb = vb_m.group(1) if vb_m else "0 0 100 100"
        vb_p = [float(x) for x in vb.split()]
        vbw, vbh = vb_p[2], vb_p[3]
        sx = w / vbw
        h = vbh * sx
        cx = _state.CANVAS_W / 2
        cy = _state.CANVAS_H / 2
        if layout:
            ent = layout.get("cls:" + cls)
            if ent:
                cx = ent["cx"]
                cy = ent["cy"]
                h = ent.get("h")
                if h and h > 0:
                    sx = h / vbh
                    w = vbw * sx
        sub_layers, sub_assets, _ = svg_to_layers(svg_text, vb, prefix=cls)
        for L in sub_layers:
            transform_layer(L, cx - w/2, cy - h/2, sx, sx)
        anim = parse_anim_shorthand(props.get("animation", ""))
        if anim:
            apply_animation_to_layers(sub_layers, anim)
        apply_shadows(sub_layers, props.get("filter", ""), layers_out)
        assets_out.extend(sub_assets)
        n_shadow = len(parse_drop_shadows(props.get("filter","")))
        print(f"  [img .{cls}] {src} {w:.0f}x{h:.0f} -> {len(sub_layers)} слоёв"
              + (f" (+{n_shadow} тени)" if n_shadow else ""))


# ============================================================
# Тексты: <h1>, <p>, JS-год
# ============================================================

def apply_texts(html, js_text, layers_out, assets_out, layout=None):
    """<h1 class>…</h1> и <p class>…</p> рендерим в PNG.
    Для h1 со <span> — каждая буква отдельным слоем + stagger из nth-child."""
    from PIL import Image as _PILImg
    from PIL import ImageFont
    for item in extract_texts(html):
        cls = item.get("cls", "")
        props = css_rule_for(cls, _state.CSS_RULES) if cls else {}
        pid = item.get("id", "")
        if pid:
            props = {**_state.CSS_RULES.get("#" + pid, {}), **props}

        text = item["text"]
        if item["kind"] == "p" and pid == "year":
            fromjs = extract_js_year(js_text) if js_text else None
            if fromjs: text = fromjs
        if not text: continue

        fs = h2l_resolve_length(props.get("font-size"), _state.CANVAS_H)
        if fs is None: fs = int(_state.CANVAS_H * 0.06)
        color_css = props.get("color", "#ffffff")
        col = _parse_color(color_css) or (255,255,255)

        # позиция: если есть top/bottom/left — вычисляем; иначе центр
        cx = None; cy = None
        # сначала layout
        if layout:
            key = "id:" + pid if pid else ("cls:" + cls if cls else None)
            ent = layout.get(key) if key else None
            if ent:
                cx = ent["cx"]; cy = ent["cy"]
        # потом CSS box (position:fixed и т.п.)
        if cx is None:
            box = compute_box(props)
            if box and box.get("w") is not None:
                cx = box["left"] + box["w"] / 2
                cy = box["top"] + (box["h"] or fs) / 2
            elif box and (box["top"] or box["left"]):
                cx = _state.CANVAS_W / 2
                cy = box["top"] + fs / 2
        if cx is None:
            cx = _state.CANVAS_W / 2
            cy = _state.CANVAS_H * (0.10 if item["kind"] == "h" else 0.80)

        anim = parse_anim_shorthand(props.get("animation", ""))

        if item["kind"] == "h" and item.get("spans"):
            _render_h1_spans(item, cls, props, fs, col, cx, cy,
                             anim, layers_out, assets_out)
        else:
            glow = None
            if item["kind"] == "h":
                ts = props.get("text-shadow", "")
                gm = re.search(r"0\s+0\s+(\d+)px\s+(#[0-9a-fA-F]+)", ts)
                if gm:
                    glow = _parse_color(gm.group(2))
            png = ROOT / f"_{cls or pid or 'text'}.png"
            make_text_png(text, int(fs), col, str(png), glow=glow)
            w, h = _PILImg.open(png).size
            asset, layer = make_image_layer(str(png), cx, cy,
                                            cls or pid or "text",
                                            f"img_{cls or pid or 'text'}")
            if anim:
                apply_animation_to_layers([layer], anim)
            layers_out.append(layer); assets_out.append(asset)
            print(f"  [text .{cls or pid}] '{text[:24]}' {w}x{h}")


def _render_h1_spans(item, cls, props, fs, col, cx, cy, anim,
                     layers_out, assets_out):
    """Каждая буква — отдельный image-слой со stagger из nth-child."""
    from PIL import Image as _PILImg
    from PIL import ImageFont
    font = _find_font(int(fs))
    dummy = _PILImg.new("RGBA", (10,10))
    d0 = _PILImgDraw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(dummy)

    # измеряем каждую букву и gap
    widths = []
    for sp in item["spans"]:
        if sp == " ":
            widths.append(fs * 0.4)
        else:
            bbox = d0.textbbox((0,0), sp, font=font)
            widths.append(bbox[2] - bbox[0])
    total_w = sum(widths) + (len(widths)-1) * fs * 0.05
    x0 = cx - total_w / 2
    y0 = cy

    nth = _state.CSS_NTH.get(cls, {})
    has_stagger = bool(nth)
    n = len(item["spans"])
    for idx, sp in enumerate(item["spans"]):
        if sp == " ":
            x0 += fs * 0.4 + fs*0.05
            continue
        bbox = d0.textbbox((0,0), sp, font=font)
        bw = bbox[2] - bbox[0]
        # PNG для буквы
        pad = max(6, int(fs // 4))
        W, H = bw + 2*pad, (bbox[3] - bbox[1]) + 2*pad
        png = ROOT / f"_letter_{cls}_{idx}.png"
        img = _PILImg.new("RGBA", (W, H), (0,0,0,0))
        d = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(img)
        d.text((pad - bbox[0], pad - bbox[1]), sp, font=font,
               fill=col + (255,))
        img.save(png)

        # центр буквы
        bx = x0 + bw/2
        by = y0
        asset, layer = make_image_layer(str(png), bx, by,
                                        f"letter_{cls}_{idx}",
                                        f"img_letter_{cls}_{idx}")
        # базовое правило для букв из .title span { animation: drop ... }
        base_props = css_rule_for(cls + "_child", _state.CSS_RULES) or props
        base_anim = parse_anim_shorthand(
            base_props.get("animation", "")) or anim
        anim_here = base_anim
        if has_stagger:
            rule = nth.get(idx + 1)
            if rule and "animation-delay" in rule:
                delay_str = rule["animation-delay"]
                dm = re.match(r"([\d.]+)s", delay_str)
                if dm and base_anim:
                    anim_here = dict(base_anim)
                    anim_here["delay"] = float(dm.group(1))
        if anim_here:
            apply_animation_to_layers([layer], anim_here, base_props=base_props)
        layers_out.append(layer); assets_out.append(asset)
        x0 += bw + fs*0.05
    print(f"  [h1 .{cls}] {n} букв (stagger={has_stagger})")


def make_text_png(text, size, color, path, glow=None):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    font = _find_font(size)
    dummy = Image.new("RGBA", (10,10))
    d0 = ImageDraw.Draw(dummy)
    bbox = d0.textbbox((0,0), text, font=font)
    tw = bbox[2] - bbox[0]; th = bbox[3] - bbox[1]
    pad = max(4, size // 4)
    W, H = tw + 2*pad, th + 2*pad
    img = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(img)
    ox, oy = pad - bbox[0], pad - bbox[1]
    if glow:
        g = Image.new("RGBA", (W, H), (0,0,0,0))
        gd = ImageDraw.Draw(g)
        gd.text((ox, oy), text, font=font, fill=glow + (255,))
        g = g.filter(ImageFilter.GaussianBlur(max(1, size // 8)))
        img = Image.alpha_composite(img, g)
        d = ImageDraw.Draw(img)
    d.text((ox, oy), text, font=font, fill=color + (255,))
    img.save(path)
    return path


def _find_font(size):
    from PIL import ImageFont
    for c in [r"C:/Windows/Fonts/georgia.ttf",
              r"C:/Windows/Fonts/Georgia.ttf",
              r"C:/Windows/Fonts/times.ttf",
              r"C:/Windows/Fonts/constan.ttf",
              r"C:/Windows/Fonts/DejaVuSerif.ttf"]:
        try: return ImageFont.truetype(c, size)
        except Exception: pass
    return ImageFont.load_default()


# ============================================================
# main
# ============================================================

def _resolve_url(url, html_dir):
    """Превращает href в локальный путь, если возможно."""
    if not url: return None
    url = url.strip()
    if url.startswith(("http://","https://","//","data:","#","mailto:")):
        return None
    # убираем query/fragment
    url = url.split("#",1)[0].split("?",1)[0]
    p = (html_dir / url).resolve()
    return p if p.exists() else None


def load_html_resources(html, html_dir):
    """Собирает весь CSS и JS из HTML:
    <link rel=stylesheet>, <script src>, <style>, inline style="""
    css_parts, js_parts = [], []
    seen_css, seen_js = set(), set()

    # 1) <link rel="stylesheet" href="...">
    for m in re.finditer(
        r"<link\b[^>]*>", html, re.IGNORECASE | re.DOTALL
    ):
        tag = m.group(0)
        if not re.search(r'rel\s*=\s*["\']stylesheet["\']', tag, re.I):
            continue
        hm = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag)
        if not hm: continue
        p = _resolve_url(hm.group(1), html_dir)
        if p and p not in seen_css:
            seen_css.add(p)
            try:
                css_parts.append(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [res] не прочитал CSS: {p} ({e})")

    # 2) <style>...</style> inline
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style>",
                         html, re.IGNORECASE | re.DOTALL):
        css_parts.append(m.group(1))

    # 4) <script src="..."> (внешние)
    for m in re.finditer(r"<script\b[^>]*\bsrc\s*=\s*[\"\']([^\"\']+)[\"\'][^>]*>",
                         html, re.IGNORECASE):
        p = _resolve_url(m.group(1), html_dir)
        if p and p not in seen_js:
            seen_js.add(p)
            try:
                js_parts.append(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [res] не прочитал JS: {p} ({e})")

    # 5) <script>...</script> inline
    for m in re.finditer(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
                         html, re.IGNORECASE | re.DOTALL):
        js_parts.append(m.group(1))

    # 6) <basename>.js если нет <script src>
    if not js_parts or all(p.strip()=="" for p in js_parts):
        cand = sorted(html_dir.glob("*.js"))
        # отсекаем lottie.min.js и прочее
        cand = [c for c in cand if "lottie" not in c.name.lower()]
        if len(cand) == 1:
            js_parts.append(cand[0].read_text(encoding="utf-8"))

    return "\n".join(css_parts), "\n".join(js_parts)


def apply_css_urls(css, html_dir, layers_out, assets_out):
    """Находит url(...) в CSS-правилах .class { background: url(...) }
    и вставляет как слои на позицию селектора."""
    css_norm = strip_comments(css)
    for selector, body in split_css_blocks(css_norm):
        sel = selector.strip()
        # только .class или #id (простые)
        cls_match = re.match(r"^\.([\w-]+)\s*$", sel)
        id_match = re.match(r"^#([\w-]+)\s*$", sel)
        if not (cls_match or id_match): continue
        cls = cls_match.group(1) if cls_match else None
        nid = id_match.group(1) if id_match else None
        props = parse_props(body)
        # ищем url() в background, background-image, mask, mask-image
        for prop in ("background", "background-image", "mask", "mask-image"):
            val = props.get(prop)
            if not val: continue
            for um in re.finditer(r"url\(\s*[\"']?([^\"')\s]+)[\"']?\s*\)", val):
                href = um.group(1)
                p = _resolve_url(href, html_dir)
                if not p: continue
                # позиция по CSS-классу
                target_props = _state.CSS_RULES.get(cls or ("#"+nid if nid else ""), {})
                box = compute_box(target_props) if target_props else None
                if not box: continue
                if href.lower().endswith(".svg"):
                    svg_text = p.read_text(encoding="utf-8")
                    vb_m = re.search(r'viewBox="([^"]+)"', svg_text)
                    vb = vb_m.group(1) if vb_m else "0 0 100 100"
                    vbp = [float(x) for x in vb.split()]
                    vbw, vbh = vbp[2], vbp[3]
                    sx = (box["w"] or _state.CANVAS_W) / vbw
                    sy = (box["h"] or vbh) / vbh if box["h"] else sx
                    sub_layers, sub_assets, _ = svg_to_layers(svg_text, vb, prefix=cls)
                    for L in sub_layers:
                        transform_layer(L, box["left"], box["top"], sx, sy)
                    layers_out.extend(sub_layers)
                    assets_out.extend(sub_assets)
                    print(f"  [css-url .{cls or nid}] {href} -> {len(sub_layers)} слоёв")
                else:
                    # PNG/JPG — image-слой
                    aid = f"img_css_{len(assets_out)}"
                    w_img = box["w"] or _state.CANVAS_W
                    h_img = box["h"] or _state.CANVAS_H
                    cx = box["left"] + w_img/2
                    cy = box["top"] + h_img/2
                    asset, layer = make_image_layer(str(p), cx, cy,
                        f"css_{cls or nid}", aid,
                        scale_x=w_img, scale_y=h_img)
                    layers_out.append(layer)
                    assets_out.append(asset)
                    print(f"  [css-url .{cls or nid}] {href} -> image")


def apply_js_animations(js_text, html, layers_out):
    if not js_text: return
    js_kf = {}
    for m in re.finditer(
        r"['\"`]@keyframes\s+(\w+)\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}\s*['\"`]",
        js_text, re.DOTALL
    ):
        name = m.group(1)
        body = m.group(2)
        keys = []
        for km in re.finditer(r"(from|to|[\d.,\s%]+?)\s*\{([^}]*)\}", body):
            key = km.group(1).strip()
            if key == 'from': offs = [0.0]
            elif key == 'to': offs = [1.0]
            else:
                offs = []
                for x in key.split(','):
                    x = x.strip().rstrip('%')
                    if x:
                        try: offs.append(int(float(x))/100)
                        except ValueError: pass
            props = parse_props(km.group(2))
            for off in offs:
                keys.append((off, props))
        keys.sort(key=lambda x: x[0])
        js_kf[name] = keys
    if not js_kf:
        return
    for m in re.finditer(
        r"querySelectorAll\(\s*['\"`]([^'\"`]+)['\"`]\s*\)"
        r"(?:[\s\S]{0,400}?style\.animation\s*=\s*[`'\"]([^`'\"]+)[`'\"])?",
        js_text, re.DOTALL
    ):
        sel = m.group(1)
        anim_str = m.group(2)
        if not anim_str: continue
        bm = re.match(r"@?\s*(\w+)\s+(?:\$\{[^}]+\}|[\d.]+)s", anim_str)
        if not bm: continue
        kf_name = bm.group(1)
        if kf_name not in js_kf: continue
        matched = []
        for L in layers_out:
            if L.get('ty') != 4: continue
            shapes = L.get('shapes', [])
            ok = False
            for g in shapes:
                if g.get('ty') != 'gr': continue
                for it in g.get('it', []):
                    if it.get('ty') == 'rc':
                        sz = it.get('s', {}).get('k', [0,0])
                        if 10 < sz[0] < 40 and 10 < sz[1] < 40:
                            ok = True; break
                if ok: break
            if ok and (L.get('blur_sigma') or 0) >= 2.0:
                matched.append(L)
        if not matched: continue
        for i, L in enumerate(matched):
            dur = 1.5 + (i * 0.37) % 3.0
            delay = (i * 0.53) % 3.0
            pts = keyframes_to_points(kf_name, dur, delay, 'infinite', 'normal')
            if not pts: continue
            op_keys = [{'t': p[0], 's': [p[4]*100]} for p in pts]
            L['ks']['o'] = {'a': 1, 'k': op_keys}
        print(f'  [js-anim] {sel} -> {len(matched)} окон')

def apply_use_refs(html, html_dir, layers_out, assets_out):
    """<use href="foo.svg#id"> — вставить содержимое <symbol id> из foo.svg."""
    for m in re.finditer(
        r"<use\b[^>]*\b(?:href|xlink:href)\s*=\s*[\"']([^#\"']+)\.svg#([\w-]+)[\"']",
        html, re.IGNORECASE
    ):
        svg_rel, sym_id = m.group(1), m.group(2)
        p = _resolve_url(svg_rel + ".svg", html_dir)
        if not p: continue
        svg_text = p.read_text(encoding="utf-8")
        # ищем <symbol id="..."> ... </symbol> или <g id="...">
        sym_m = re.search(
            rf"<symbol\b[^>]*\bid=[\"']{re.escape(sym_id)}[\"'][^>]*>(.*?)</symbol>",
            svg_text, re.DOTALL
        )
        if not sym_m:
            sym_m = re.search(
                rf"<g\b[^>]*\bid=[\"']{re.escape(sym_id)}[\"'][^>]*>(.*?)</g>",
                svg_text, re.DOTALL
            )
        if not sym_m: continue
        inner = sym_m.group(1)
        wrapper = (f'<svg xmlns="http://www.w3.org/2000/svg" '
                   f'viewBox="0 0 {_state.CANVAS_W} {_state.CANVAS_H}">{inner}</svg>')
        sub_layers, sub_assets, _ = svg_to_layers(wrapper, f"0 0 {_state.CANVAS_W} {_state.CANVAS_H}",
                                                  prefix=f"use_{sym_id}")
        layers_out.extend(sub_layers)
        assets_out.extend(sub_assets)
        print(f"  [use] {svg_rel}.svg#{sym_id} -> {len(sub_layers)} слоёв")


def html_main(src_html, dst, canvas_w=None, canvas_h=None):
    if canvas_w: _state.CANVAS_W = int(canvas_w)
    if canvas_h: _state.CANVAS_H = int(canvas_h)

    src_html = str(src_html)
    html_dir = pathlib.Path(src_html).parent
    html = pathlib.Path(src_html).read_text(encoding="utf-8")
    css, js_text = load_html_resources(html, html_dir)
    print(f"  [res] css: {len(css)} символов, js: {len(js_text)} символов")

    css_parse(css)

    all_layers, all_assets = [], []

    # 1) фон
    apply_body_background(all_layers, all_assets)

    # 2) звёзды body::before
    apply_body_stars(all_layers, all_assets)

    # 3) <svg class="..."> (луна, солнце, ДНК, самолётик, город)
    for info in extract_svgs(html):
        cls = info["cls"]
        if not cls: continue
        props = css_rule_for(cls, _state.CSS_RULES)
        box = compute_box(props)
        if not box or box["w"] is None:
            continue
        vb_p = [float(x) for x in info["vb"].split()]
        vbw, vbh = vb_p[2], vb_p[3]
        sx = box["w"] / vbw
        sy = (box["h"] / vbh) if box["h"] else sx
        prefix = info.get("comment") or cls or None
        sub_layers, sub_assets, _ = svg_to_layers(info["svg"], info["vb"], prefix=prefix)
        for L in sub_layers:
            transform_layer(L, box["left"], box["top"], sx, sy)
        anim = parse_anim_shorthand(props.get("animation", ""))
        if anim:
            apply_animation_to_layers(sub_layers, anim)
        apply_shadows(sub_layers, props.get("filter", ""), all_layers)
        all_assets.extend(sub_assets)
        n_shadow = len(parse_drop_shadows(props.get("filter","")))
        print(f"  [svg .{cls}] {box['w']:.0f}x{box['h'] or 0:.0f} -> {len(sub_layers)} слоёв"
              + (f" (+{n_shadow} тени)" if n_shadow else ""))

    # 3б) <div class> с CSS-фоном
    apply_divs(html, all_layers, all_assets)

    # 4) flex-layout
    layout = compute_flex_layout(html, _state.CSS_RULES, html_dir)
    if layout:
        print(f"  [flex] размерено {len(layout)} элементов")

    # 5) <img src=".svg">
    apply_images(html, html_dir, all_layers, all_assets, layout)

    # 5а) url(...) в CSS
    apply_css_urls(css, html_dir, all_layers, all_assets)

    # 5б) <use href="...#id">
    apply_use_refs(html, html_dir, all_layers, all_assets)

    # 6) тексты
    apply_texts(html, js_text, all_layers, all_assets, layout)

    # 7) JS-анимации на слоях
    apply_js_animations(js_text, html, all_layers)

    # 8) JS-canvas (снег, салют, метеоры)
    if _apply_js_canvas is not None:
        _apply_js_canvas(html, js_text, all_layers, _state.CANVAS_W, _state.CANVAS_H, _state.FRAME_OP)

    # индексы
    for i, L in enumerate(reversed(all_layers)):
        L["ind"] = i + 1
    doc = {
        "v": "5.9.0", "fr": _state.FR, "ip": 0, "op": _state.FRAME_OP,
        "w": _state.CANVAS_W, "h": _state.CANVAS_H, "nm": pathlib.Path(dst).stem,
        "ddd": 0, "assets": all_assets,
        "layers": list(reversed(all_layers)),
        "meta": {"source": src_html, "converter": "html2lottie.py v1.0"}
    }
    pathlib.Path(dst).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(f"OK: {len(all_layers)} слоёв -> {dst}")




# ============================================================
# Универсальный вход
# ============================================================

def _detect_mode(path):
    """'.html' -> html, '.svg' -> svg. Если X.svg и рядом X.html — html."""
    p = pathlib.Path(path)
    if p.suffix.lower() == ".html":
        return "html", p
    if p.suffix.lower() == ".svg":
        sibling = p.with_suffix(".html")
        if sibling.exists():
            return "html", sibling
        return "svg", p
    # без расширения — попробуем найти .html / .svg
    for ext in (".html", ".svg"):
        cand = p.with_suffix(ext)
        if cand.exists():
            return ("html" if ext == ".html" else "svg"), cand
    return None, p


def main(input_path, output_path=None, canvas_w=None, canvas_h=None):
    mode, src = _detect_mode(input_path)
    if mode is None:
        raise SystemExit(f"Не найден файл: {input_path}")
    if output_path is None:
        output_path = str(pathlib.Path(src).with_suffix(".lotty"))
    if mode == "svg":
        print(f"[mode=svg] {src}")
        svg_main(str(src), str(output_path))
    else:
        print(f"[mode=html] {src}")
        html_main(str(src), str(output_path), canvas_w, canvas_h)


def _build_help():
    return """svg2lottie_v6.py — конвертер SVG/HTML/CSS/JS в Lottie (.lotty)

ИСПОЛЬЗОВАНИЕ:
    svg2lottie_v6.py <вход> [выход.lotty] [ширина высота]
    svg2lottie_v6.py --help

ВХОД:
    foo.svg     — векторный SVG
    foo.html    — полный HTML-макет (или X.svg при наличии X.html рядом)

    В HTML-режиме автоматически подхватываются все ресурсы, на которые
    есть ссылки в самом HTML:
      <link rel="stylesheet" href="...">   — все внешние CSS (по порядку)
      <style>...</style>                    — inline CSS
      <script src="...">...</script>        — все внешние JS (по порядку)
      <script>...</script>                  — inline JS
      <svg class="...">...</svg>            — все inline SVG
      <img src="...svg">                    — внешние SVG через <img>
      <img src="...png|jpg">                — растровые картинки
      <use href="sprite.svg#id">            — SVG-спрайты
      url(...) в CSS                        — background / mask

ЧТО ПОДДЕРЖИВАЕТСЯ:
  SVG:
    • фигуры: <rect>, <circle>, <ellipse>, <line>, <polygon>, <polyline>, <path>
    • пути: M, L, H, V, C, S, Q, T, A (arc→bezier), Z
    • градиенты: linearGradient, radialGradient (с alpha-стопами)
    • фильтры: feGaussianBlur → blur_sigma
    • клип-маски
    • <image href> с data:URI и внешними файлами
    • <animate attributeName="..."> для r, cx, cy, x, y, width, height,
      opacity, stdDeviation
    • <animateTransform type="rotate|translate|scale"> — SMIL

  CSS:
    • @keyframes с % и from/to
    • animation shorthand: name dur [timing] [delay] [iteration] [direction]
    • animation-delay, animation-direction (normal|reverse|alternate|...)
    • .class, #id, .parent .child, .class:nth-child(N)
    • transform: translate/translateX/translateY/rotate/scale
    • transform-origin: center / X% Y% / Xpx Ypx
    • opacity, background, background-image
    • radial-gradient(), linear-gradient() — с stop-opacity
    • display:flex (только flex-direction: column)
      → раскладка детей по вертикали с gap / justify-content / padding
    • filter: drop-shadow(dx dy blur color) → копия слоя со смещением и blur
    • position: fixed / absolute с top/left/right/bottom/width/height
    • единицы: px, %, vw, vh, em/rem, clamp(a,b,c)

  HTML:
    • <h1>…<h6> с вложенными <span> — каждая буква отдельным слоем
      с индивидуальным animation-delay из :nth-child
    • <p> — простой текстовый слой
    • <img> — image-слой

  JS (эвристически):
    • document.getElementById("year").textContent = new Date().getFullYear()+N
      → год подставляется вычисленным (2026 и т.п.)
    • querySelectorAll(sel).forEach(el => el.style.animation = `...`)
      → применяется как opacity-keyframes к найденным слоям
    • <canvas id="snow|salute"> + формулы в JS:
      → генераторы снега / салюта / метеоров (детерминированные, seed=42)

ВЫХОД:
    .lotty — JSON Lottie с расширениями (см. --help в render-lv6.py)

ПРИМЕРЫ:
    svg2lottie_v6.py ny.svg
    svg2lottie_v6.py ny.html
    svg2lottie_v6.py ny.html ny.lotty 800 500
    svg2lottie_v6.py deepseek_svg.svg out.lotty

ОГРАНИЧЕНИЯ:
    • внешние ресурсы (CSS/JS/SVG/PNG) читаются только из локальной папки
      рядом с HTML; http(s):// и data: для CSS/JS не поддерживаются
    • несколько <link>/<script> объединяются в один каскад
    • text через <h1> — растр (PNG на букву), не глифы Lottie
    • background-clip: text (градиентный текст) — упрощается до белого
    • JS-анимации: только паттерн querySelectorAll(...).forEach(...)
"""


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(_build_help())
        sys.exit(0 if len(sys.argv) >= 2 else 1)
    args = sys.argv[1:]
    src = args[0]
    dst = args[1] if len(args) > 1 else None
    w = args[2] if len(args) > 2 else None
    h = args[3] if len(args) > 3 else None
    main(src, dst, w, h)
