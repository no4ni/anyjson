"""Цвета: hex2rgb и _parse_color. Вынесено из svg2lottie_v6.py."""
import re

_NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "green": "#008000", "lime": "#00ff00", "blue": "#0000ff",
    "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff",
    "fuchsia": "#ff00ff", "aqua": "#00ffff",
    "grey": "#808080", "gray": "#808080",
    "lightgrey": "#d3d3d3", "lightgray": "#d3d3d3",
    "darkgrey": "#a9a9a9", "darkgray": "#a9a9a9",
    "silver": "#c0c0c0", "orange": "#ffa500", "purple": "#800080",
    "pink": "#ffc0cb", "brown": "#a52a2a", "navy": "#000080",
    "teal": "#008080", "olive": "#808000", "maroon": "#800000",
    "lightblue": "#add8e6", "lightgreen": "#90ee90",
    "lightyellow": "#ffffe0", "lightpink": "#ffb6c1",
    "lightcyan": "#e0ffff", "lightgray": "#d3d3d3",
    "darkred": "#8b0000", "darkgreen": "#006400", "darkblue": "#00008b",
    "steelblue": "#4682b4", "skyblue": "#87ceeb", "gold": "#ffd700",
    "ivory": "#fffff0", "beige": "#f5f5dc", "coral": "#ff7f50",
    "salmon": "#fa8072", "tomato": "#ff6347", "khaki": "#f0e68c",
}

def hex2rgb(h):
    """Преобразовать CSS-цвет в [r, g, b] (0..1). None для прозрачных/неизвестных."""
    if not h:
        return None
    h = str(h).strip()
    low = h.lower()
    if low in ("none", "transparent", "currentcolor", "inherit"):
        return None
    if low in _NAMED_COLORS:
        h = _NAMED_COLORS[low]

    def _hsl_to_rgb(hh, ss, ll):
        hh = hh / 360.0
        ss = ss / 100.0
        ll = ll / 100.0
        def hue2rgb(p, q, t):
            if t < 0: t += 1
            if t > 1: t -= 1
            if t < 1/6: return p + (q - p) * 6 * t
            if t < 1/2: return q
            if t < 2/3: return p + (q - p) * (2/3 - t) * 6
            return p
        if ss == 0:
            r = g = b = ll
        else:
            q = ll * (1 + ss) if ll < 0.5 else ll + ss - ll * ss
            p = 2 * ll - q
            r = hue2rgb(p, q, hh + 1/3)
            g = hue2rgb(p, q, hh)
            b = hue2rgb(p, q, hh - 1/3)
        return [r, g, b]

    # rgba / rgb
    _m = re.match(r"rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)", low)
    if _m:
        return [float(_m.group(i)) / 255.0 for i in (1, 2, 3)]

    # hsla / hsl
    _m = re.match(r"hsla?\(\s*([0-9.]+)(?:deg)?\s*,\s*([0-9.]+)%\s*,\s*([0-9.]+)%", low)
    if _m:
        return _hsl_to_rgb(float(_m.group(1)), float(_m.group(2)), float(_m.group(3)))

    if not h.startswith("#"):
        return None
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return None

def _parse_color(c):
    c = c.strip()
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3: h = "".join(x*2 for x in h)
        return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))
    m = re.match(r"rgba?\(([^)]+)\)", c)
    if m:
        nums = [x.strip() for x in m.group(1).split(",")]
        return tuple(int(float(nums[i])) for i in range(3))
    named = {"white":(255,255,255),"black":(0,0,0),"red":(255,0,0),
             "transparent":(0,0,0)}
    return named.get(c.lower())
