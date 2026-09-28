r"""Раскодировать literal \uXXXX в файле (только >= --min).

Использование:
  py decode_unicode.py <путь> [--min=0x0400] [--max=0xFFFF] [--dry]

По умолчанию декодирует только кириллицу и выше (>= 0x0400).
Не трогает ASCII-escape типа \u001b (ANSI), \u00d7 (×) и подобные.
"""
import argparse
import pathlib
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="путь к файлу (.py, .txt, ...)")
    ap.add_argument("--min", default="0x0400", type=lambda x: int(x, 0),
                    help="минимальный код символа (по умолч. 0x0400)")
    ap.add_argument("--max", default="0xFFFF", type=lambda x: int(x, 0),
                    help="максимальный код символа (по умолч. 0xFFFF)")
    ap.add_argument("--dry", action="store_true",
                    help="только показать, не записывать")
    args = ap.parse_args()

    p = pathlib.Path(args.path)
    if not p.is_file():
        print("ERR: не файл:", p)
        return 1

    s = p.read_text(encoding="utf-8")
    original = s
    count = [0]

    def repl(m):
        code = int(m.group(1), 16)
        if args.min <= code <= args.max:
            count[0] += 1
            return chr(code)
        return m.group(0)

    s = re.sub(r"\\u([0-9a-fA-F]{4})", repl, s)

    if s == original:
        print("no changes")
        return 0

    if args.dry:
        print("would decode", count[0], "escapes in", p)
        return 0

    p.write_text(s, encoding="utf-8")
    print("OK:", p, "-- decoded", count[0], "escapes")
    return 0


if __name__ == "__main__":
    sys.exit(main())