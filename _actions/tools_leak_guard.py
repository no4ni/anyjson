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
    # hex-строки (sha256, git-хеши) специально НЕ маскируются:
    # это не секрет, они публично вычислимы.
]

# Длинные строки вида base64url: MEGA-ключи, JWT-сегменты, API-секреты.
# Email — маскируем в выводе, но не блокируем отправку.
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


_SECRET_LONG_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_\-])([A-Za-z0-9_\-]{40,})(?![A-Za-z0-9_\-])"
)


# URL-квери с секретом: ?key=..., &token=..., ?api_key=...
# Именно здесь ключ обычно короткий (32 символа у weatherapi, а не 40+).
# Работает независимо от длины значения.
_URL_KV_PATTERN = re.compile(
    r"(?i)([?&](?:key|api[_\s\-]?key|apikey|token|access[_\s\-]?token"
    r"|secret|password|passwd|pwd|пароль|ключ|секрет|токен)=)([^&\s\"'<>]{6,})"
)


# "пароль=значение", "password: значение", "token: "abc"", "api_key = xyz"
# Маскируем само значение, оставляя ключ и разделитель.
_KV_PATTERN = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|passphrase"
    r"|api[_\s\-]?key|apikey"
    r"|private[_\s\-]?key|secret[_\s\-]?key"
    r"|token|secret|bearer|authorization"
    r"|пароль|ключ|секрет|токен)\b[\"']?\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def _mask_kv(m):
    val = m.group(2)
    if len(val) >= 2 and val[0] in ("\"", "'") and val[-1] == val[0]:
        return m.group(1) + val[0] + "***" + val[-1]
    return m.group(1) + "***"


def _looks_like_secret(s):
    if len(s) < 40:
        return False
    if re.fullmatch(r"[a-fA-F0-9]+", s):
        return False  # чистый hex — sha256, git-хэш
    cats = 0
    if any(c.isupper() for c in s): cats += 1
    if any(c.islower() for c in s): cats += 1
    if any(c.isdigit() for c in s): cats += 1
    if ("-" in s) or ("_" in s): cats += 1
    # длинные серии однотипных букв — часть слова, не токен
    # (Aggressive, Uncensored и т.п. вместо случайной base64url-последовательности)
    if re.search(r"[a-z]{8,}", s):
        return False
    if re.search(r"[A-Z]{6,}", s):
        return False
    return cats >= 3


_VOWELS = set("aeiouyAEIOUYаеёиоуыэюяАЕЁИОУЫЭЮЯ")


def _looks_like_identifier(s):
    """snake_case-идентификатор (имя файла, функции) — не секрет.

    Требует: 2+ части, разделённых "_", каждая часть — слово
    (начинается с буквы, есть гласная, длина <= 20) или число.
    Настоящие base64url-токены так не выглядят.
    """
    parts = s.split("_")
    if len(parts) < 2:
        return False
    words = 0
    for part in parts:
        if not part or len(part) > 20:
            return False
        if part.isdigit():
            continue
        if not part[0].isalpha():
            return False
        if not any(c in _VOWELS for c in part):
            return False
        words += 1
    return words >= 2


def mask_secrets(text):
    """Заменяет секреты в строке на ***. Для вывода в консоль/буфер."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    out = _KV_PATTERN.sub(_mask_kv, out)
    # URL-квери: ?key=... / &token=... — независимо от длины значения.
    out = _URL_KV_PATTERN.sub(lambda m: m.group(1) + "***", out)
    out = _EMAIL_PATTERN.sub("***", out)
    for pat in _TOKEN_PATTERNS:
        out = pat.sub("***", out)
    def _sub(m):
        s_val = m.group(1)
        if _looks_like_identifier(s_val):
            return m.group(0)
        return "***" if _looks_like_secret(s_val) else m.group(0)
    out = _SECRET_LONG_PATTERN.sub(_sub, out)
    return out


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
