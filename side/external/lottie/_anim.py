"""Анимации: CSS -> keyframes -> слои. Вынесено из svg2lottie_v6.py."""
import math
import re
from _css import parse_transform, parse_origin
import _state


def bezier(timing):
    if timing.startswith("cubic-bezier"):
        m = re.match(r"cubic-bezier\(([^)]+)\)", timing)
        if m:
            try:
                x1, y1, x2, y2 = [float(v.strip()) for v in m.group(1).split(",")]
                return {"i":{"x":[x2],"y":[y2]},"o":{"x":[x1],"y":[y1]}}
            except ValueError:
                pass
    x1, y1, x2, y2 = _state.TIMING.get(timing, _state.TIMING["ease-in-out"])
    return {"i":{"x":[x2],"y":[y2]},"o":{"x":[x1],"y":[y1]}}


def _val_at(keys, t):
    if not keys: return None
    if t <= keys[0]["t"]: return list(keys[0]["s"])
    if t >= keys[-1]["t"]: return list(keys[-1]["s"])
    for i in range(len(keys)-1):
        a, b = keys[i], keys[i+1]
        if a["t"] <= t <= b["t"]:
            if b["t"] == a["t"]: return list(a["s"])
            k = (t - a["t"]) / (b["t"] - a["t"])
            return [a["s"][j] + (b["s"][j]-a["s"][j])*k for j in range(len(a["s"]))]
    return list(keys[-1]["s"])


def _get_base_at(layer, prop, default, t):
    pr = layer["ks"].get(prop)
    if pr is None:
        b = default
        return [b[0], b[1], b[2] if len(b) > 2 else 0] if prop in ("p","s") else b
    if pr.get("a", 0) == 0:
        b = pr["k"]
        if prop == "p": return [b[0], b[1], 0]
        if prop == "s": return [b[0], b[1], 100]
        if prop == "r": return [b]
        return b
    return _val_at(pr["k"], t)


def _shift_pos(layer, dx, dy):
    p = layer["ks"].get("p")
    if p is None: return
    if p.get("a", 0) == 0:
        p["k"] = [p["k"][0]+dx, p["k"][1]+dy, 0]
    else:
        for k in p["k"]:
            k["s"] = [k["s"][0]+dx, k["s"][1]+dy, 0]


def _transform_point(x, y, ctx):
    """Применяет накопленный group transform к точке."""
    tx = ctx.get("tx", 0.0); ty = ctx.get("ty", 0.0)
    sx = ctx.get("sx", 1.0); sy = ctx.get("sy", 1.0)
    rot = math.radians(ctx.get("rot", 0.0))
    # scale + rotate вокруг начала координат SVG (0,0), затем translate
    x2 = x * sx; y2 = y * sy
    x3 = x2 * math.cos(rot) - y2 * math.sin(rot)
    y3 = x2 * math.sin(rot) + y2 * math.cos(rot)
    return x3 + tx, y3 + ty


def _apply_group_transform(ctx, tstr):
    """Накапливает transform группы: translate, scale, rotate."""
    tx, ty, rot, sx, sy = parse_transform(tstr)
    new_ctx = dict(ctx)
    new_ctx["tx"] = ctx.get("tx", 0.0) + tx
    new_ctx["ty"] = ctx.get("ty", 0.0) + ty
    new_ctx["sx"] = ctx.get("sx", 1.0) * sx
    new_ctx["sy"] = ctx.get("sy", 1.0) * sy
    new_ctx["rot"] = ctx.get("rot", 0.0) + rot
    return new_ctx


def _apply_delta(layer, prop, deltas, mode, default):
    """Накладывает дельты на свойство. Линейная интерполяция между точками."""
    if len(deltas) < 2: return
    ts = sorted(set(d[0] for d in deltas))
    if ts[0] > 0: ts = [0] + ts
    if ts[-1] < _state.FRAME_OP: ts = ts + [_state.FRAME_OP]
    delta_by_t = {d[0]: d[1:] for d in deltas}

    new_keys = []
    for t in ts:
        if t in delta_by_t:
            d = delta_by_t[t]
        else:
            prev = max((d[0] for d in deltas if d[0] <= t), default=deltas[0][0])
            nxt = min((d[0] for d in deltas if d[0] >= t), default=deltas[-1][0])
            if prev == nxt:
                d = delta_by_t[prev]
            else:
                k = (t - prev) / (nxt - prev)
                dp = delta_by_t[prev]
                dn = delta_by_t[nxt]
                d = [dp[i] + (dn[i]-dp[i])*k for i in range(len(dp))]
        b = _get_base_at(layer, prop, default, t)
        if mode == "add":
            if prop == "p": v = [b[0]+d[0], b[1]+d[1], 0]
            elif prop == "r": v = [b[0]+d[0]]
            else: v = list(b)
        elif mode == "mul":
            if prop == "s": v = [b[0]*d[0], b[1]*d[1], 100]
            else: v = list(b)
        elif mode == "opacity_mul":
            v = [b * d[0]]
        else:
            v = list(b)
        new_keys.append({"t": t, "s": v})
    layer["ks"][prop] = {"a": 1, "k": new_keys}


def _interp_series(offs, vals, off, default):
    """Линейная интерполяция между определёнными точками (CSS-семантика)."""
    defined = [(o, v) for o, v in zip(offs, vals) if v is not None]
    if not defined:
        return default
    if off <= defined[0][0]: return defined[0][1]
    if off >= defined[-1][0]: return defined[-1][1]
    for i in range(len(defined)-1):
        a_o, a_v = defined[i]; b_o, b_v = defined[i+1]
        if a_o <= off <= b_o:
            if b_o == a_o: return a_v
            k = (off - a_o) / (b_o - a_o)
            return a_v + (b_v - a_v) * k
    return defined[-1][1]


def _lerp_props(pa, pb, k):
    """Линейная интерполяция между двумя наборами CSS-свойств."""
    if k <= 0: return pa
    if k >= 1: return pb
    ta = parse_transform(pa.get("transform", ""))
    tb = parse_transform(pb.get("transform", ""))
    oa = float(pa.get("opacity", 1.0))
    ob = float(pb.get("opacity", 1.0))
    t = [ta[i] + (tb[i]-ta[i])*k for i in range(5)]
    o = oa + (ob-oa)*k
    return {"transform": f"translate({t[0]}px,{t[1]}px) rotate({t[2]}deg) scale({t[3]},{t[4]})",
            "opacity": str(o)}


def keyframes_to_points(kf_name, duration, delay, iteration, direction,
                       base_props=None):
    """Преобразует @keyframes имя в список точек (t_frame, tx, ty, rot, op).
    base_props — CSS-состояние элемента (from по умолчанию)."""
    kf = _state.CSS_KEYFRAMES.get(kf_name)
    if not kf: return None
    # дополняем нулевой точкой, если нет
    if kf[0][0] > 0:
        if base_props:
            neutral = {}
            if any("transform" in p for _, p in kf):
                neutral["transform"] = base_props.get("transform", "none")
            if any("opacity" in p for _, p in kf):
                neutral["opacity"] = base_props.get("opacity", "1")
        else:
            neutral = {}
            if any("transform" in p for _, p in kf): neutral["transform"] = "none"
            if any("opacity" in p for _, p in kf): neutral["opacity"] = "1"
        kf = [(0.0, neutral)] + kf
    # alternate -> проходим туда-обратно
    if direction in ("alternate", "alternate-reverse"):
        base = list(kf) if direction == "alternate" else list(reversed(kf))
        back = [(base[-1][0]*2 - o, pp) for o, pp in reversed(base[:-1])]
        cycle = base + back
        cycle_len = 2.0
    elif direction == "reverse":
        cycle = list(reversed(kf))
        cycle_len = cycle[-1][0] if cycle[-1][0] else 1.0
    else:
        cycle = list(kf)
        cycle_len = cycle[-1][0] if cycle[-1][0] else 1.0

    dur_frames = max(1, round(duration * _state.FR))
    del_frames = max(0, round(delay * _state.FR))
    total_needed = _state.FRAME_OP
    if iteration == "infinite":
        n_cycles = max(1, int((total_needed - del_frames) / dur_frames) + 2)
    else:
        try: n_cycles = int(iteration)
        except (TypeError, ValueError): n_cycles = 1

    # собираем плоские списки
    offs, txs, tys, rots, ops, sxs, sys_ = [], [], [], [], [], [], []
    for c in range(n_cycles):
        for off, props in cycle:
            t = del_frames + c*dur_frames + round(off * dur_frames)
            if t > _state.FRAME_OP: continue
            tx0, ty0, rot0, sx0, sy0 = parse_tr(props.get("transform", "none"))
            end_tf = parse_tr(cycle[-1][1].get("transform", "none"))
            start_tf = parse_tr(cycle[0][1].get("transform", "none"))
            base_shift = c * (end_tf[0] - start_tf[0])
            base_shift_y = c * (end_tf[1] - start_tf[1])
            base_shift_r = c * (end_tf[2] - start_tf[2])
            offs.append(t)
            txs.append(tx0 + base_shift)
            tys.append(ty0 + base_shift_y)
            rots.append(rot0 + base_shift_r)
            sxs.append(sx0)
            sys_.append(sy0)
            op = props.get("opacity")
            ops.append(float(op) if op is not None else None)
    if not offs: return None
    if offs[0] > 0:
        offs.insert(0, 0)
        txs.insert(0, txs[0]); tys.insert(0, tys[0])
        rots.insert(0, rots[0])
        sxs.insert(0, sxs[0]); sys_.insert(0, sys_[0])
        ops.insert(0, ops[0])
    if offs[-1] < _state.FRAME_OP:
        offs.append(_state.FRAME_OP)
        txs.append(txs[-1]); tys.append(tys[-1]); rots.append(rots[-1])
        sxs.append(sxs[-1]); sys_.append(sys_[-1]); ops.append(ops[-1])
    all_t = sorted(set(offs))
    points = []
    for t in all_t:
        tx = _interp_series(offs, txs, t, 0.0)
        ty = _interp_series(offs, tys, t, 0.0)
        rot = _interp_series(offs, rots, t, 0.0)
        op = _interp_series(offs, ops, t, 1.0)
        sx = _interp_series(offs, sxs, t, 1.0)
        sy = _interp_series(offs, sys_, t, 1.0)
        points.append((t, tx, ty, rot, op, sx, sy))
    return points


def apply_css_anim(layer, rule, anim_def, keyframes, cx, cy, bw=0, bh=0,
                    own_anim=True):
    """Применяет CSS-анимацию к слою (аддитивно, с продолжением infinite)."""
    if not anim_def or not anim_def.get("name"): return False
    kf = keyframes.get(anim_def["name"])
    if not kf: return False

    dur = anim_def["dur"]
    delay = anim_def["delay"]
    direction = anim_def["direction"]
    infinite = anim_def["iteration"] == "infinite"

    origin = parse_origin(rule.get("transform-origin"), bw, bh)
    if origin is None or bw == 0 or bh == 0:
        anc = (0.0, 0.0)
    else:
        anc = (origin[0] - bw/2, origin[1] - bh/2)

    # --- Один цикл CSS ---
    if direction in ("alternate", "alternate-reverse"):
        fwd = list(kf) if direction == "alternate" else list(reversed(kf))
        back = [(fwd[-1][0]*2 - off, pp) for off, pp in reversed(fwd[:-1])]
        cycle = fwd + back
        cycle_len = 2.0
    elif direction == "reverse":
        cycle = list(reversed(kf))
        cycle_len = cycle[-1][0]
    else:
        cycle = list(kf)
        cycle_len = cycle[-1][0]

    # --- Значения в начале и конце цикла ---
    start_props = cycle[0][1]
    s_tx, s_ty, s_rot, s_sx, s_sy = parse_transform(start_props.get("transform", ""))
    s_op = float(start_props.get("opacity", 1.0))

    end_props = cycle[-1][1]
    e_tx, e_ty, e_rot, e_sx, e_sy = parse_transform(end_props.get("transform", ""))
    e_op = float(end_props.get("opacity", 1.0))

    def props_at(off):
        if off <= cycle[0][0]: return cycle[0][1]
        if off >= cycle[-1][0]: return cycle[-1][1]
        for k in range(len(cycle)-1):
            a_off, a_pp = cycle[k]
            b_off, b_pp = cycle[k+1]
            if a_off <= off <= b_off:
                if b_off == a_off: return a_pp
                return _lerp_props(a_pp, b_pp, (off-a_off)/(b_off-a_off))
        return cycle[-1][1]

    # --- Циклы ---
    if infinite:
        total_time = _state.FRAME_OP - delay
        n_cycles = max(1, int(total_time / dur) + 1)
    else:
        n_cycles = 1

    # --- Собираем плоский список точек ---
    points = []
    for n in range(n_cycles):
        for off, props in cycle:
            t = delay + n * dur + off * (dur / cycle_len)
            if t > _state.FRAME_OP: continue
            tx, ty, rot, sx, sy = parse_transform(props.get("transform", ""))
            op = float(props.get("opacity", 1.0))
            # накопление: n * (end - start) + (value - start) + start = n*(end-start) + value
            # но для аддитивных: n*(end-start) + value
            # для мультипликативных (scale, opacity): value * (end/start)^n ≈ value + n*(end-start) для малых изменений
            # точнее для scale: sx_total = sx * (e_sx/s_sx)^n, для rotate/translate: + n*(e-s)
            abs_tx = n * (e_tx - s_tx) + tx
            abs_ty = n * (e_ty - s_ty) + ty
            abs_rot = n * (e_rot - s_rot) + rot
            if abs(s_sx) > 1e-6:
                abs_sx = sx * ((e_sx / s_sx) ** n)
            else:
                abs_sx = sx
            if abs(s_sy) > 1e-6:
                abs_sy = sy * ((e_sy / s_sy) ** n)
            else:
                abs_sy = sy
            if abs(s_op) > 1e-6:
                abs_op = op * ((e_op / s_op) ** n)
            else:
                abs_op = op
            points.append((int(round(t)), abs_tx, abs_ty, abs_rot,
                            abs_sx, abs_sy, abs_op))

    if not points or points[0][0] > 0:
        points.insert(0, (0, s_tx, s_ty, s_rot, s_sx, s_sy, s_op))
    if points[-1][0] < _state.FRAME_OP:
        last = points[-1]
        points.append((_state.FRAME_OP, last[1], last[2], last[3], last[4],
                        last[5], last[6]))

    seen = {}
    for pt in points: seen[pt[0]] = pt
    points = sorted(seen.values(), key=lambda x: x[0])

    p_deltas = [(pt[0], pt[1], pt[2]) for pt in points]
    r_deltas = [(pt[0], pt[3]) for pt in points]
    s_deltas = [(pt[0], pt[4], pt[5]) for pt in points]
    o_deltas = [(pt[0], pt[6]) for pt in points]

    def is_anim(deltas):
        if len(deltas) < 2: return False
        first = deltas[0][1:]
        return any(d[1:] != first for d in deltas[1:])

    if any(abs(v) > 1e-6 for v in anc):
        old_a = layer["ks"].get("a")
        if old_a is None:
            layer["ks"]["a"] = {"a":0, "k":[anc[0], anc[1], 0]}
        elif old_a.get("a", 0) == 0:
            ov = old_a["k"]
            layer["ks"]["a"] = {"a":0, "k":[ov[0]+anc[0], ov[1]+anc[1], 0]}
        else:
            for k in old_a["k"]:
                k["s"] = [k["s"][0]+anc[0], k["s"][1]+anc[1], 0]
        _shift_pos(layer, anc[0], anc[1])
        cx += anc[0]; cy += anc[1]

    if is_anim(p_deltas):
        _apply_delta(layer, "p", p_deltas, "add", [cx, cy, 0])
    if is_anim(r_deltas):
        _apply_delta(layer, "r", r_deltas, "add", [0])
    if is_anim(s_deltas):
        _apply_delta(layer, "s", s_deltas, "mul", [100, 100, 100])
    if is_anim(o_deltas):
        _apply_delta(layer, "o", o_deltas, "opacity_mul", [100])

    return True


def apply_animation_to_layers(layers, anim, nth_applied=None, base_props=None):
    """Накладывает CSS-анимацию на список слоёв (общая для группы)."""
    if not anim: return
    pts = keyframes_to_points(anim["name"], anim["dur"], anim["delay"],
                              anim["iteration"], anim["direction"],
                              base_props=base_props)
    if not pts: return
    has_rot = any(abs(p[3]) > 1e-6 for p in pts)
    has_op = any(abs(p[4] - 1.0) > 1e-6 for p in pts)
    has_scale = any(abs(p[5] - 1.0) > 1e-6 or abs(p[6] - 1.0) > 1e-6
                    for p in pts)

    for L in layers:
        pk = L["ks"]["p"]
        if pk.get("a", 0) == 0:
            base = [pk["k"][0], pk["k"][1]]
        else:
            base = [pk["k"][0]["s"][0], pk["k"][0]["s"][1]]
        L["ks"]["p"] = {"a": 1, "k": [
            {"t": p[0], "s": [base[0] + p[1], base[1] + p[2], 0]}
            for p in pts
        ]}
        if has_rot:
            L["ks"]["r"] = {"a": 1, "k": [
                {"t": p[0], "s": [p[3]]} for p in pts
            ]}
        if has_op:
            L["ks"]["o"] = {"a": 1, "k": [
                {"t": p[0], "s": [p[4]*100]} for p in pts
            ]}
        if has_scale:
            sk = L["ks"].get("s", {"a":0, "k":[100,100,100]})
            if sk.get("a", 0) == 0:
                base_s = sk["k"]
            else:
                base_s = sk["k"][0]["s"]
            L["ks"]["s"] = {"a": 1, "k": [
                {"t": p[0], "s": [base_s[0]*p[5], base_s[1]*p[6], 100]}
                for p in pts
            ]}


def parse_tr(tstr):
    """transform: translate(...) rotate(...) scale(...) -> (tx, ty, rot, sx, sy)."""
    tx = ty = rot = 0.0
    sx = sy = 1.0
    if not tstr or tstr.strip() == "none":
        return tx, ty, rot, sx, sy
    def _n(v):
        v = v.strip()
        if v.endswith("vw"): return float(v[:-2])/100 * _state.CANVAS_W
        if v.endswith("vh"): return float(v[:-2])/100 * _state.CANVAS_H
        if v.endswith("%"): return float(v[:-1]) / 100
        return float(v.replace("px", "").replace("deg",""))
    for m in re.finditer(r"(\w+)\s*\(([^)]*)\)", tstr):
        fn, args = m.group(1), [x.strip() for x in m.group(2).split(",")]
        try:
            if fn == "translate":
                tx += _n(args[0])
                if len(args) > 1: ty += _n(args[1])
            elif fn == "translateX": tx += _n(args[0])
            elif fn == "translateY": ty += _n(args[0])
            elif fn == "rotate": rot += _n(args[0])
            elif fn == "scale":
                s = _n(args[0])
                sx *= s
                sy *= _n(args[1]) if len(args) > 1 else s
        except (ValueError, IndexError):
            pass
    return tx, ty, rot, sx, sy
