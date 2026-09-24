"""Алиас: посмотреть_файл -> прочитать_полностью."""
from _actions import register
from _actions import files as _files


for _name in ("посмотреть_файл", "посмотреть_картинку",
              "рендер", "отрендерить",
              "svg_в_буфер", "рендер_svg"):
    register(_name)(_files.read_full)
