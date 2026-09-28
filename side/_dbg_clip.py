import sys
try:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        for name, fmt in [("CF_HDROP", win32clipboard.CF_HDROP), ("CF_DIB", win32clipboard.CF_DIB), ("CF_UNICODETEXT", win32clipboard.CF_UNICODETEXT)]:
            print(name, "available:", win32clipboard.IsClipboardFormatAvailable(fmt))
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
            print("HDROP:", win32clipboard.GetClipboardData(win32clipboard.CF_HDROP))
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            txt = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            print("TEXT:", repr(txt[:300]))
    finally:
        win32clipboard.CloseClipboard()
except Exception as e:
    print("ERR:", type(e).__name__, e)
