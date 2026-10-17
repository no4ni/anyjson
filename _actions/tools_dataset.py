"""Датасет самообучения + поиск файлов.

Пути — через _tools_core (get_my_dir).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from tqdm import tqdm

from _actions import _tools_core
from _actions import register


# ---------- датасет ----------
def get_dataset_path():
    return _tools_core.get_my_dir() / "dataset.jsonl"


def add_example(instruction, output):
    dataset_path = get_dataset_path()
    try:
        with open(dataset_path, "a", encoding="utf-8") as f:
            example = {"instruction": instruction, "output": output}
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
        return f"Пример добавлен: {instruction[:50]}..."
    except Exception as e:
        return f"Ошибка при добавлении примера: {e}"


def add_dialog_example(messages):
    dataset_path = get_dataset_path()
    try:
        with open(dataset_path, "a", encoding="utf-8") as f:
            example = {"messages": messages}
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
        return f"Диалог добавлен (сообщений: {len(messages)})"
    except Exception as e:
        return f"Ошибка при добавлении диалога: {e}"


def get_examples(limit=100):
    dataset_path = get_dataset_path()
    if not dataset_path.exists():
        return "Датасет пуст."
    examples = []
    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    examples.append(json.loads(line))
        if not examples:
            return "Датасет пуст."
        total = len(examples)
        result = f"Всего примеров: {total}\nПоследние {min(limit, total)}:\n"
        for ex in examples[-limit:]:
            if "messages" in ex:
                result += "Диалог:\n"
                for msg in ex["messages"]:
                    result += f"  {msg['role']}: {msg['content'][:100]}\n"
                result += "---\n"
            elif "instruction" in ex and "output" in ex:
                result += f"Вопрос: {ex['instruction'][:100]}\nОтвет: {ex['output'][:100]}\n---\n"
        return result
    except Exception as e:
        return f"Ошибка чтения датасета: {e}"


def clear_dataset():
    dataset_path = get_dataset_path()
    if dataset_path.exists():
        try:
            dataset_path.unlink()
        except Exception as e:
            return f"Ошибка при очистке датасета: {e}"
    return "Датасет очищен."


def is_praise(text):
    praise_keywords = ["хорошо", "молодец", "отлично", "класс", "супер",
                       "спасибо", "👍", "лайк", "+"]
    return any(kw in text.lower() for kw in praise_keywords)


def extract_last_dialog_segment(history):
    if not history:
        return []
    last_assistant_idx = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "assistant":
            last_assistant_idx = i
            break
    if last_assistant_idx == -1:
        return []
    start_idx = last_assistant_idx
    for i in range(last_assistant_idx - 1, -1, -1):
        if history[i].get("role") == "user":
            start_idx = i
            break
    if history[start_idx].get("role") == "user" and start_idx == len(history) - 1:
        prev_user_idx = -1
        for i in range(start_idx - 1, -1, -1):
            if history[i].get("role") == "user":
                prev_user_idx = i
                break
        if prev_user_idx != -1:
            start_idx = prev_user_idx
        else:
            return []
    return history[start_idx:last_assistant_idx + 1]


# ============================================================
#  Кэш обхода директорий (с поддержкой resume и продления TTL)
# ============================================================
import time as _time_cache
from collections import deque as _deque

_DIR_CACHE = {}
_DIR_CACHE_TTL = 300
_DIR_CACHE_MAX_ROOTS = 8

# последний критерий поиска — чтобы "номер: 2" работал без повторения маски
_LAST_SEARCH = {"root": "", "name": "", "content": "",
                "all": "", "mask": "", "only_files": None}


def _cache_get(root):
    """Возвращает состояние кэша и ПРОДЛЕВАЕТ TTL на 5 минут."""
    e = _DIR_CACHE.get(str(root))
    if not e:
        return None
    age = _time_cache.time() - e["ts"]
    if age > _DIR_CACHE_TTL:
        del _DIR_CACHE[str(root)]
        return None
    # продление при обращении
    e["ts"] = _time_cache.time()
    return e


def _cache_set(root, entry):
    entry["ts"] = _time_cache.time()
    _DIR_CACHE[str(root)] = entry
    if len(_DIR_CACHE) > _DIR_CACHE_MAX_ROOTS:
        oldest = min(_DIR_CACHE.items(), key=lambda kv: kv[1]["ts"])
        del _DIR_CACHE[oldest[0]]


def _walk_step(skip_dirs, deadline, pbar, state):
    """Обход до exhaustion или deadline. Изменяет state in-place.
    Возвращает True если обход завершён, False если timed_out.
    """
    queue = state["queue"]
    dirs_list = state["dirs"]
    files_list = state["files"]
    scanned = state["scanned"]
    timed_out = False

    while queue:
        if deadline and time.perf_counter() > deadline:
            timed_out = True
            break
        try:
            dirpath = queue.popleft()
        except IndexError:
            break
        try:
            with os.scandir(dirpath) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in skip_dirs:
                                continue
                            dirs_list.append(entry.path)
                            queue.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            files_list.append(entry.path)
                            scanned += 1
                            if pbar is not None and (scanned & 0xF) == 0:
                                pbar.update(16)
                                if deadline and (scanned & 0xFF) == 0:
                                    if time.perf_counter() > deadline:
                                        timed_out = True
                                        break
                    except OSError:
                        continue
        except (PermissionError, OSError):
            continue
        if timed_out:
            break

    state["scanned"] = scanned
    return not timed_out


def _find_in_single_file(path, search_name, search_content, search_all,
                         search_mask, encoding="utf-8"):
    """Поиск по содержимому одного файла (когда 'путь' — файл, а не папка)."""
    _NL = chr(10)
    _keywords = []
    for _raw in (search_all, search_content, search_name, search_mask):
        if not _raw:
            continue
        if isinstance(_raw, list):
            _items = _raw
        elif isinstance(_raw, str) and "|" in _raw:
            _items = _raw.split("|")
        else:
            _items = [_raw]
        for _k in _items:
            _s = str(_k).replace("*", "").replace("?", "").strip()
            if _s:
                _keywords.append(_s)

    if not _keywords:
        _p = str(path).replace(chr(92), "/")
        return (
            "❌ Для одиночного файла нужен хотя бы один критерий "
            "поиска (содержание/имя/всё/маска)." + _NL
            + "  " + path.name + " — файл, поиск идёт по содержимому." + _NL
            + "💡 Пример:" + _NL
            + "```text" + _NL
            + '{"найти_файл": {"путь": "' + _p + '", '
            + '"содержание": "подстрока"}}' + _NL
            + "```"
        )

    _kws_lower = [k.lower() for k in _keywords]

    try:
        with open(path, "r", encoding=encoding, errors="ignore") as f:
            _lines = f.readlines()
    except Exception as e:
        return "❌ Не удалось прочитать файл: " + str(e)

    _matches = []
    for _i, _line in enumerate(_lines, 1):
        _ll = _line.lower()
        if any(_k in _ll for _k in _kws_lower):
            _matches.append((_i, _line.rstrip(chr(10)).rstrip(chr(13))))

    if not _matches:
        return (
            "❌ Совпадений не найдено в " + path.name + _NL
            + "  искал: " + ", ".join(_keywords) + _NL
            + "  строк в файле: " + str(len(_lines))
        )

    _MAX_SHOW = 100
    _shown = _matches[:_MAX_SHOW]
    _word = "совпадение" if len(_matches) == 1 else "совпадений"
    out = "✅ Найдено " + str(len(_matches)) + " " + _word + " в " + path.name
    if len(_matches) > _MAX_SHOW:
        out += " (показаны первые " + str(_MAX_SHOW) + ")"
    out += _NL + "  искал: " + ", ".join(_keywords) + _NL
    for _ln, _txt in _shown:
        _t = _txt if len(_txt) <= 200 else _txt[:200] + "…"
        out += "  " + str(_ln) + ": " + _t + _NL
    if len(_matches) > _MAX_SHOW:
        out += "  … и ещё " + str(len(_matches) - _MAX_SHOW) + " совпадений" + _NL
    return out.rstrip(_NL)

# ---------- поиск файлов ----------
def find_file(root_path: str, search_name: str = "", search_content: str = "",
              search_all: str = "", encoding: str = "utf-8",
              max_file_size: int = 1_000_000, text_extensions: set = None,
              timeout: int = 30, target_num: int = 1,
              only_files: bool = False, search_mask: str = "",
              ignore_cache: bool = False) -> str:
    if text_extensions is None:
        text_extensions = {".txt", ".py", ".md", ".json", ".log", ".csv",
                           ".ini", ".cfg", ".xml", ".html", ".css", ".js"}

    root = Path(root_path).resolve()
    # Если путь — файл (а не папка): поиск по содержимому этого файла.
    if root.is_file():
        return _find_in_single_file(
            root, search_name, search_content, search_all, search_mask,
            encoding
        )

    if not root.is_dir():
        return f"Ошибка: корневая папка {root} не существует."

    search_name = (search_name or "").replace("*", "")
    search_content = (search_content or "").replace("*", "")
    search_all = (search_all or "").replace("*", "")
    search_mask = search_mask or ""

    # Если критерий пуст, но есть прошлый поиск и номер > 1 — берём из памяти.
    if not (search_name or search_content or search_all or search_mask):
        _ls = _LAST_SEARCH
        if (_ls.get("root") and str(root) == _ls["root"]
                and target_num > 1
                and (_ls["name"] or _ls["content"]
                     or _ls["all"] or _ls["mask"])):
            search_name = _ls["name"]
            search_content = _ls["content"]
            search_all = _ls["all"]
            search_mask = _ls["mask"]
            if _ls["only_files"] is not None:
                only_files = bool(_ls["only_files"])
        else:
            return ("Ошибка: укажите 'имя', 'содержание', 'всё' или 'маска' "
                    "для поиска.\n"
                    "💡 Если это продолжение прошлого поиска — "
                    "проверь, что 'корень' совпадает с прошлым.")

    # Запоминаем критерий — пригодится для 'номер: N+1' без повторения
    _LAST_SEARCH.update({
        "root": str(root),
        "name": search_name or "",
        "content": (search_content if isinstance(search_content, str)
                    else "|".join(search_content or [])),
        "all": search_all or "",
        "mask": search_mask or "",
        "only_files": bool(only_files),
    })

    if search_all:
        target = search_all.lower()
        check_name = True
        check_content = True
    else:
        target = None
        check_name = bool(search_name)
        check_content = bool(search_content)

    if not check_content:
        max_file_size = None

    # glob-маска (например *mega*, src/*.py) — через fnmatch
    import fnmatch as _fnm
    _mask = (search_mask or "").strip().lower()
    check_mask = bool(_mask)

    if isinstance(search_content, str) and "|" in search_content:
        search_content = [kw.strip() for kw in search_content.split("|") if kw.strip()]

    deadline = (time.perf_counter() + float(timeout)) if timeout and timeout > 0 else None
    start_time = time.perf_counter()
    target_num = max(1, int(target_num))
    _MAX_FOUND = 10000
    _CONTENT_GRACE = 5.0  # сек: сколько ещё искать содержание после первого матча
    _content_deadline = None

    skip_dirs = {
        "__pycache__", "node_modules", ".git", ".vs", "site-packages",
        ".venv", "venv", "$RECYCLE.BIN", "System Volume Information",
        ".idea", "dist", "build", ".mypy_cache", ".pytest_cache",
        ".cache", ".gradle", ".cargo", ".npm", ".nuget", "target",
    }

    if ignore_cache:
        _DIR_CACHE.pop(str(root), None)

    cache = _cache_get(root)
    if cache:
        state = {
            "queue": _deque(cache["queue"]),
            "dirs": list(cache["dirs"]),
            "files": list(cache["files"]),
            "scanned": cache["scanned"],
        }
        from_cache = True
        was_complete = cache.get("complete", False)
    else:
        state = {
            "queue": _deque([str(root)]),
            "dirs": [],
            "files": [],
            "scanned": 0,
        }
        from_cache = False
        was_complete = False

    need_walk = (not was_complete) and (len(state["queue"]) > 0)
    if need_walk:
        pbar = tqdm(desc="🔍 Обход", unit="файл", dynamic_ncols=True,
                    mininterval=0.3, file=sys.stderr)
        complete = _walk_step(skip_dirs, deadline, pbar, state)
        pbar.update(state["scanned"] - pbar.n)
        pbar.close()
    else:
        complete = was_complete

    _cache_set(root, {
        "queue": list(state["queue"]),
        "dirs": state["dirs"],
        "files": state["files"],
        "scanned": state["scanned"],
        "complete": complete,
    })

    found_entries = []

    def _mask_match(name):
        """fnmatch по маске, плюс fallback — как подстрока (удобно)."""
        if not check_mask:
            return True
        nl = name.lower()
        if _fnm.fnmatch(nl, _mask):
            return True
        # если маска без * — сравнение как подстрока
        _bare = _mask.replace("*", "").replace("?", "")
        if _bare and _bare in nl:
            return True
        return False

    # Папки проверяем, только если есть имя/маска/всё.
    # Если ищем ТОЛЬКО по содержанию — папки не считаем совпадениями.
    _check_dirs = check_name or check_mask or bool(search_all)
    if not only_files and _check_dirs:
        for dir_full in state["dirs"]:
            dirname = os.path.basename(dir_full)
            if search_all:
                m = target in dirname.lower()
            elif check_name:
                m = search_name.lower() in dirname.lower()
            else:
                m = True
            if m and _mask_match(dirname):
                found_entries.append((dir_full, "directory"))
                if len(found_entries) >= _MAX_FOUND:
                    break

    if len(found_entries) < _MAX_FOUND:
        for file_path in state["files"]:
            file = os.path.basename(file_path)
            ext = os.path.splitext(file)[1].lower()
            if check_content and ext not in text_extensions:
                continue

            name_match = True
            if check_name:
                if search_all:
                    name_match = target in file.lower()
                else:
                    name_match = search_name.lower() in file.lower()
            if not _mask_match(file):
                continue

            content_match = True
            if check_content:
                content_match = False
                if search_all and name_match:
                    content_match = True
                else:
                    try:
                        if max_file_size is not None and os.path.getsize(file_path) > max_file_size:
                            continue
                        with open(file_path, "r", encoding=encoding, errors="ignore") as f:
                            content = f.read(65536)
                        content_lower = content.lower()
                        if search_all:
                            content_match = target in content_lower
                        elif isinstance(search_content, list):
                            content_match = any(kw.lower() in content_lower
                                                for kw in search_content)
                        else:
                            content_match = search_content.lower() in content_lower
                    except Exception:
                        continue

            _final_match = (search_all and (name_match or content_match)) or (name_match and content_match)
            # Только маска (без имени и без контента) — матч по маске
            if check_mask and not check_name and not check_content:
                _final_match = True
            if _final_match:
                found_entries.append((file_path, "file"))
                if check_content and _content_deadline is None:
                    _content_deadline = time.perf_counter() + _CONTENT_GRACE
                if len(found_entries) >= _MAX_FOUND:
                    break
            if (check_content and _content_deadline is not None
                    and time.perf_counter() > _content_deadline):
                break

    elapsed = time.perf_counter() - start_time
    _mm = int(elapsed // 60)
    _ss = elapsed - _mm * 60
    _rate = (state["scanned"] / elapsed) if elapsed > 0 else 0

    _found = len(found_entries)
    _cache_mark = " (кэш)" if from_cache else ""

    if _found == 0:
        _progress_line = f"❌ Ничего не найдено{_cache_mark}"
    elif _found >= _MAX_FOUND:
        _progress_line = (f"✅ Найдено ≥{_found} "
                          f"(достигнут потолок {_MAX_FOUND}){_cache_mark}")
    elif complete:
        _word = "совпадение" if _found == 1 else "совпадений"
        _progress_line = f"✅ Найдено {_found} {_word} (все){_cache_mark}"
    else:
        _progress_line = (f"✅ Найдено ≥{_found} "
                          f"(в обойдённых){_cache_mark}")

    prefix = ""
    if not complete and deadline:
        prefix = (f"⛔ Лимит {timeout} сек на этот вызов. "
                  f"Очередь: {len(state['queue'])} папок.\n")

    _prog = _progress_line + "\n"

    if not found_entries:
        return f"{_progress_line}{prefix}  (корень: {root})"

    if len(found_entries) < target_num:
        _found_count = len(found_entries)
        _pick_idx = _found_count - 1
        _real_num = _found_count
        _num_header = (f"⚠️ Запрошен результат #{target_num}, "
                       f"но найдено всего {_found_count}. "
                       f"Показываю последний найденный (#{_real_num}).\n")
    else:
        _pick_idx = target_num - 1
        _real_num = target_num
        _num_header = ""

    found_entry, found_type = found_entries[_pick_idx]
    target_num = _real_num

    if found_type == "directory":
        try:
            items = os.listdir(found_entry)
            preview = "\n".join(items[:10])
            if len(items) > 10:
                preview += f"\n... и ещё {len(items)-10} элементов"
            return f"{_prog}{_num_header}{prefix}📁 #{target_num}. Папка: {found_entry}\nСодержимое:\n{preview}"
        except Exception as e:
            return f"{_prog}{_num_header}{prefix}📁 #{target_num}. Папка: {found_entry}\n(не удалось прочитать: {e})"
    else:
        content_preview = None
        _is_binary = False
        try:
            with open(found_entry, "rb") as f:
                head = f.read(1024)
            if b"\x00" in head:
                _is_binary = True
            else:
                for _enc in (encoding or "utf-8", "utf-8", "cp1251",
                             "cp866", "latin-1"):
                    try:
                        content_preview = head.decode(_enc, errors="strict")
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                if content_preview is None:
                    content_preview = head.decode("utf-8", errors="replace")
        except Exception as e:
            content_preview = f"(ошибка чтения: {e})"

        if _is_binary or content_preview is None:
            _size = 0
            try:
                _size = os.path.getsize(found_entry)
            except Exception:
                _size = 0
            _size_str = f"{_size} байт" if _size else "неизвестен"
            content_preview = (f"(бинарный файл — превью не показано, "
                               f"размер: {_size_str})")
            _footer = ""
        else:
            _path_fwd = str(found_entry).replace("\\", "/")
            _size = 0
            try:
                _size = os.path.getsize(found_entry)
            except Exception:
                _size = 0
            _total_blocks = max(1, (_size + 1023) // 1024) if isinstance(_size, int) else 1
            _has_more = _total_blocks > 1

            _search_args = []
            if search_name:
                _search_args.append(f'"имя": "{search_name}"')
            if search_content:
                _search_args.append(f'"содержание": "{search_content}"')
            if search_all:
                _search_args.append(f'"всё": "{search_all}"')
            _search_args.append(f'"корень": "{str(root).replace(chr(92), "/")}"')
            _search_args_str = ", ".join(_search_args)

            _cmd_name = "найти_файл" if only_files else "найти_всё"
            _next_hint = (
                '{"' + _cmd_name + '": {' + _search_args_str
                + ', "номер": ' + str(target_num + 1) + '}}'
                + '  — следующий результат (если есть)'
            )

            _parts = []
            if _has_more:
                _parts.append("\n\n▶ Дальше (скролл 1): ```text")
                _parts.append(
                    f'{{"читать_по_кб": {{"путь": "{_path_fwd}", "кб": 1, "скролл": 1}}}}'
                )
                _parts.append("```")
                _parts.append(f"   (показан блок 1/{_total_blocks})")
            _parts.append("\n💡 Другие команды:")
            _parts.append("```text")
            _parts.append(
                f'{{"читать": {{"путь": "{_path_fwd}"}}}}  — начало файла'
            )
            _parts.append(
                f'{{"осмотреть": {{"путь": "{_path_fwd}", "ключевые_слова": "..."}}}}  — поиск по содержимому'
            )
            if _next_hint:
                _parts.append(_next_hint)
            _parts.append("```")
            _footer = "\n".join(_parts)

        return f"{_prog}{_num_header}{prefix}📄 #{target_num}. {found_entry}:\n{content_preview}{_footer}"


@register("сбросить_кэш_поиска")
def clear_search_cache(args):
    """Сбросить кэш обхода директорий (используется find_file).

    Полезно после изменения файловой системы, чтобы не искать по устаревшему списку.
    """
    n = len(_DIR_CACHE)
    _DIR_CACHE.clear()
    return f"Кэш поиска очищен ({n} записей)."
