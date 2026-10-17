"""Общие глобалы lottie-модулей."""

gradients = {}

# XML namespace (SVG)
NS = "{http://www.w3.org/2000/svg}"

# CSS-глобалы (заполняются css_parse)
CSS_RULES = {}
CSS_KEYFRAMES = {}
CSS_KF = {}
CSS_NTH = {}
CSS_PSEUDO = {}
CSS_BODY = {}

# Анимационные константы
FR = 30
FRAME_OP = 300

TIMING = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "ease":   (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
}

# Canvas (html_main перезаписывает)
CANVAS_W = 800
CANVAS_H = 500
