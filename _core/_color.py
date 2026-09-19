"""Цветной вывод в терминал."""
import sys


def color_red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def color_blue(text: str) -> str:
    return f"\033[94m{text}\033[0m"


def color_yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"


def clear_line():
    sys.stdout.write('\033[2K\r')


def move_up(lines=1):
    sys.stdout.write(f'\033[{lines}A')
