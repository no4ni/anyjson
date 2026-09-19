"""Алиас: рендер → прочитать_полностью."""
from _actions import register
from _actions.files import read_full


for _name in ("рендер", "отрендерить", "svg_в_буфер", "рендер_svg"):
    register(_name)(read_full)
