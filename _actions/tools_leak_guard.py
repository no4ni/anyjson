"""Защита от утечки секретов во внешние сервисы.

Сканирует исходящие данные (JSON-тела, текст, URL) на:
  • ключевые слова: password, pwd, api_key, token, пароль, ключ и т.п.
  • форматы токенов: sk-..., xoxb-..., ghp_..., Bearer ..., длинные hex
"""
import re


_KEYWORD_PATTERNS = [
    re.compile(r"\b(password|passwd|pwd|passphrase|pass)\b", re.IGNORECASE),
    re.compile(r"\b(api[_\s\-]?key|apikey)\b", re.IGNORECASE),
    re.compile(r"\b(private[_\s\-]?key|secret[_\s\-]?key|priv[_\s\-]?key)\b", re.IGNORECASE),
    re.compile(r"\b(token|secret|bearer|authorization)\b", re.IGNORECASE),
]


_TOKEN_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxoxb-[A-Za-z0-9\-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-_.~+/=]{20,}", re.IGNORECASE),
    re.compile(r"\b[a-fA-F0-9]{40,}\b"),
]


class LeakDetected(RuntimeError):
    """Обнаружен потенциальный секрет в исходящих данных."""
    pass


def _walk_strings(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")


def scan_for_secrets(obj):
    """Возвращает список (путь, что_нашли, фрагмент). Пусто = чисто."""
    hits = []
    for path, s in _walk_strings(obj):
        if not s:
            continue
        matched = False
        for pat in _KEYWORD_PATTERNS:
            m = pat.search(s)
            if m:
                idx = m.start()
                frag = s[max(0, idx-15):m.end()+15]
                hits.append((path, f"слово «{m.group(0)}»", frag))
                matched = True
                break
        if matched:
            continue
        for pat in _TOKEN_PATTERNS:
            m = pat.search(s)
            if m:
                hits.append((path, "формат токена", m.group(0)[:50]))
                break
    return hits


def format_report(hits):
    lines = ["⛔ Заблокирована отправка во внешний сервис — возможна утечка секрета."]
    for pth, what, frag in hits[:5]:
        lines.append(f"  • {pth or '<без поля>'}: {what}")
        if frag:
            lines.append(f"    фрагмент: {frag!r}")
    if len(hits) > 5:
        lines.append(f"  ... и ещё {len(hits)-5}")
    return "\n".join(lines)


def guard(obj):
    """Проверить данные. Если есть подозрение — raise LeakDetected."""
    if not obj:
        return
    hits = scan_for_secrets(obj)
    if hits:
        raise LeakDetected(format_report(hits))
