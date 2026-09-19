"""Штатный log-sink для UNaIVERSE: пишет входящие в unaiverse_inbox.jsonl.

Активируется через env NODE_LOG_SINK_MODULE=unaiverse_sink
(UNaIVERSE сам вызовет attach(log)).
"""
import json
import os
import time
from pathlib import Path

_MY = Path(os.environ.get("ANYJSON_MY_DIR", "."))
_INBOX = _MY / "unaiverse_inbox.jsonl"


def _write(obj):
    try:
        with open(_INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass


def attach(log):
    """Вызывается UNaIVERSE автоматически."""
    orig_user = getattr(log, "user", None)
    if not callable(orig_user):
        print("[unaiverse_sink] log.user не callable, пропускаю", flush=True)
        return

    def _hooked_user(msg, *a, **kw):
        try:
            orig_user(msg, *a, **kw)
        except Exception:
            pass
        if msg and any(s in str(msg) for s in ("💬", "🖼️", "🗂️")):
            _write({"ts": time.time(), "text": str(msg)})

    try:
        log.user = _hooked_user
        print(f"[unaiverse_sink] attached, inbox={_INBOX}", flush=True)
    except Exception as e:
        print(f"[unaiverse_sink] attach failed: {e}", flush=True)
