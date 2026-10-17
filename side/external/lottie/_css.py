"""CSS-парсеры и таблица правил. Вынесено из svg2lottie_v6.py."""
import re
from _gradient import parse_gradient, _split_top_commas
import _state


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def split_css_blocks(css):
    """Разбивает css на (selector, body) с учётом @keyframes с вложенными {}."""
    out = []
    i = 0
    n = len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0: break
        selector = css[i:brace].strip()
        depth = 1
        j = brace + 1
        while j < n and depth > 0:
            if css[j] == "{": depth += 1
            elif css[j] == "}": depth -= 1
            j += 1
        body = css[brace+1:j-1]
        out.append((selector, body))
        i = j
    return out


def parse_props(body):
    props = {}
    for part in body.split(";"):
        if ":" not in part: continue
        k, v = part.split(":", 1)
        props[k.strip()] = v.strip()
    return props


def parse_css(text):
    m = re.search(r"<style[^>]*>(.*?)</style>", text, re.DOTALL)
    if not m: return {}, {}, {}
    css = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.DOTALL)

    # ---- @keyframes ----
    keyframes = {}
    for km in re.finditer(r"@keyframes\s+(\w+)\s*\{", css):
        name = km.group(1)
        start = km.end() - 1
        depth, i = 0, start
        while i < len(css):
            if css[i] == "{": depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0: break
            i += 1
        block = css[start+1:i]
        keys = []
        for k in re.finditer(r"(\d+)%\s*\{([^}]*)\}", block):
            offset = int(k.group(1)) / 100.0
            props = {}
            for p in k.group(2).split(";"):
                if ":" not in p: continue
                kk, vv = p.split(":", 1)
                props[kk.strip()] = vv.strip()
            keys.append((offset, props))
        keys.sort(key=lambda x: x[0])
        if not keys or keys[0][0] > 0:
            neutral = {}
            if any("transform" in p for _, p in keys):
                neutral["transform"] = "none"
            if any("opacity" in p for _, p in keys):
                neutral["opacity"] = "1"
            keys.insert(0, (0.0, neutral))
        keyframes[name] = keys

    # ---- .class { ... } / .class tag { ... } (без :nth) ----
    rules = {}
    for rm in re.finditer(r"\.([\w-]+)(?:\s+([a-zA-Z][\w-]*))?\s*\{([^}]*)\}", css):
        cls = rm.group(1); tag_ = rm.group(2); body = rm.group(3)
        props = {}
        for p in body.split(";"):
            if ":" not in p: continue
            kk, vv = p.split(":", 1)
            props[kk.strip()] = vv.strip()
        if tag_:
            rules.setdefault(cls + "_child", {}).update(props)
            rules.setdefault(cls + "_child_" + tag_.lower(), {}).update(props)
        else:
            rules[cls] = props

    # ---- .class:nth-child(N) { ... } ----
    nth = {}
    for rm in re.finditer(r"\.([\w-]+):nth-child\((\d+)\)\s*\{([^}]*)\}", css):
        cls, idx, body = rm.group(1), int(rm.group(2)), rm.group(3)
        props = {}
        for p in body.split(";"):
            if ":" not in p: continue
            kk, vv = p.split(":", 1)
            props[kk.strip()] = vv.strip()
        nth.setdefault(cls, {})[idx] = props

    return keyframes, rules, nth


def css_parse(css):
    """Заполняет _state.CSS_RULES, _state.CSS_KEYFRAMES, _state.CSS_NTH, _state.CSS_PSEUDO, _state.CSS_BODY."""
    _state.CSS_RULES, _state.CSS_KEYFRAMES, _state.CSS_NTH, _state.CSS_PSEUDO, _state.CSS_BODY = {}, {}, {}, {}, {}
    css = strip_comments(css)
    for selector, body in split_css_blocks(css):
        sel = selector.strip()
        if sel.startswith("@keyframes"):
            name = sel.split(None, 1)[1].strip() if " " in sel else ""
            keys = []
            for km in re.finditer(r"(from|to|[\d.,\s%]+?)\s*\{([^}]*)\}", body):
                key = km.group(1).strip()
                if key == "from":
                    offs = [0.0]
                elif key == "to":
                    offs = [1.0]
                else:
                    offs = []
                    for x in key.split(","):
                        x = x.strip().rstrip("%")
                        if x:
                            try: offs.append(int(float(x))/100)
                            except ValueError: pass
                props = parse_props(km.group(2))
                for off in offs:
                    keys.append((off, props))
            keys.sort(key=lambda x: x[0])
            _state.CSS_KEYFRAMES[name] = keys
            continue
        # body / html
        if sel in ("body", "html, body", "html,body") or sel == "html, body":
            for p in sel.split(","):
                p = p.strip()
                if p in ("body", "html"):
                    _state.CSS_BODY.update(parse_props(body))
            continue
        # псевдоэлементы
        pm = re.match(r"^(\S+?)::?([a-z-]+)(\(.*\))?$", sel)
        if pm and pm.group(2) in ("before", "after"):
            pseudo = f"{pm.group(1)}::{pm.group(2)}"
            _state.CSS_PSEUDO[pseudo] = parse_props(body)
            continue
        # .class .tag:nth-child(N) или .class :nth-child(N)
        nm = re.search(r":nth-child\((\d+)\)", sel)
        parts = re.findall(r"\.([\w-]+)", sel)
        # есть ли тег после класса? .title span { ... } -> "title_child"
        tag_after = re.search(r"\.[\w-]+\s+([a-zA-Z][\w-]*)", sel)
        if nm and parts:
            idx = int(nm.group(1))
            key = parts[0]
            _state.CSS_NTH.setdefault(key, {})[idx] = parse_props(body)
            continue
        # .class .tag — базовое правило для детей
        if tag_after and parts:
            _tag = tag_after.group(1).lower()
            key = parts[0] + "_child"
            _state.CSS_RULES.setdefault(key, {}).update(parse_props(body))
            _state.CSS_RULES.setdefault(key + "_" + _tag, {}).update(parse_props(body))
            continue
        if parts:
            key = parts[-1]
            _state.CSS_RULES.setdefault(key, {}).update(parse_props(body))
            continue
        # #id
        im = re.search(r"#([\w-]+)", sel)
        if im:
            _state.CSS_RULES.setdefault("#" + im.group(1), {}).update(parse_props(body))


def css_rule_for(cls, css_dict):
    """Возвращает {prop: val} по имени класса из _state.CSS_RULES."""
    if not cls: return {}
    return css_dict.get(cls, {})


def get_css_rule(el, class_counter):
    cls_attr = el.get("class")
    if not cls_attr: return None, None, None
    classes = cls_attr.split()
    if not classes: return None, None, None
    cls = classes[0]
    # Собираем свойства со всех классов. Поздние перезаписывают ранние
    # (в mermaid .flowchart-link может быть не первым в списке).
    rule = {}
    for c in classes:
        r = _state.CSS_RULES.get(c)
        if r:
            rule.update(r)
    if cls in _state.CSS_NTH:
        class_counter[cls] = class_counter.get(cls, 0) + 1
        idx = class_counter[cls]
        if idx in _state.CSS_NTH[cls]:
            rule.update(_state.CSS_NTH[cls][idx])
    anim_def = None
    if "animation" in rule:
        anim_def = parse_anim_shorthand(rule["animation"])
    if "animation-delay" in rule and anim_def is not None:
        v = rule["animation-delay"]
        if v.endswith("ms"): anim_def["delay"] = round(float(v[:-2])/1000*FR)
        elif v.endswith("s"): anim_def["delay"] = round(float(v[:-1])*FR)
    return rule, anim_def, cls


def parse_anim_shorthand(s):
    """'name 2s ease 0.5s infinite alternate' -> dict."""
    if not s: return None
    parts = s.split()
    if not parts: return None
    name = parts[0]
    dur = None; delay = 0.0; timing = "ease"; n_iter = 1; direction = "normal"
    time_seen = 0
    FILL_MODES = {"forwards","backwards","both","none",
                  "step-start","step-end","paused","running"}
    for p in parts[1:]:
        if p in FILL_MODES:
            continue
        if p.endswith("ms") and not p.startswith("cubic"):
            tf = float(p[:-2]) / 1000.0
            if time_seen == 0: dur = tf
            else: delay = tf
            time_seen += 1
        elif p.endswith("s") and not p.startswith("cubic"):
            tf = float(p[:-1])
            if time_seen == 0: dur = tf
            else: delay = tf
            time_seen += 1
        elif p == "infinite":
            n_iter = "infinite"
        elif p in ("normal","reverse","alternate","alternate-reverse"):
            direction = p
        elif p in ("linear","ease","ease-in","ease-out","ease-in-out") \
                or p.startswith("cubic-bezier"):
            timing = p
        elif p in ("forwards", "backwards", "both", "none",
                   "step-start", "step-end"):
            pass
    return {"name": name, "dur": dur or 1.0, "delay": delay,
            "timing": timing, "iteration": n_iter, "direction": direction}


def parse_transform(tstr):
    """Возвращает (tx, ty, rot_deg, sx, sy)."""
    tx = ty = 0.0
    rot = 0.0
    sx = sy = 1.0
    if not tstr or tstr.strip() == "none": return tx, ty, rot, sx, sy
    for m in re.finditer(r"(\w+)\s*\(([^)]*)\)", tstr):
        fn, args = m.group(1), [x.strip() for x in m.group(2).split(",")]
        try:
            if fn == "translate":
                tx += float(args[0].replace("px",""))
                if len(args) > 1: ty += float(args[1].replace("px",""))
            elif fn == "translateY":
                ty += float(args[0].replace("px",""))
            elif fn == "translateX":
                tx += float(args[0].replace("px",""))
            elif fn == "rotate":
                rot += float(args[0].replace("deg",""))
            elif fn == "scale":
                sx *= float(args[0])
                sy *= float(args[0]) if len(args) == 1 else float(args[1])
        except (ValueError, IndexError):
            pass
    return tx, ty, rot, sx, sy


def parse_origin(s, bw=0, bh=0):
    """transform-origin: '50% 50%', 'center', '10px 20px' -> (x, y) в px."""
    if not s: return None
    s = s.strip().lower()
    if s == "center": return (bw/2, bh/2)
    if s == "top left" or s == "left top": return (0, 0)
    if s == "top right" or s == "right top": return (bw, 0)
    if s == "bottom left" or s == "left bottom": return (0, bh)
    if s == "bottom right" or s == "right bottom": return (bw, bh)
    parts = s.split()
    if len(parts) < 2: return None
    def conv(v, total):
        v = v.strip()
        if v.endswith("%"): return float(v[:-1])/100 * total
        return float(v.replace("px",""))
    try:
        return (conv(parts[0], bw), conv(parts[1], bh))
    except ValueError:
        return None
