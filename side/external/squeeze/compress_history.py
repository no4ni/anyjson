# -*- coding: utf-8 -*-
"""
compress_history.py v13 — сжатие истории диалога (JSON-лог) моделью rut5-squeeze.

Конвейер для первых 75% сообщений (хвост 25% не трогается, кроме image_url):
  1. system — оставляем только первый.
  2. assistant с tool_calls — удаляем только при content=null; с текстом в content — сохраняем целиком.
  3. tool "🔎 Найдено команд по..." — удаляем.
  4. tool "Ничего не найдено... 💡 Используй \"запомнить\"..." — удаляем.
  5. tool с 📁 — обрезаем наполовину + "...".
  6. image_url / base64 — выкидываем.
  7. дедуп tool ДО сжатия.
  8. сжатие rut5-squeeze (чанки по 2 предложения, fallback если не короче).
  9. дедуп tool ПОСЛЕ сжатия.

Запуск:
  python compress_history.py log.json [out.json] [--fp16] [--порог 0.75]
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

# Фикс кодировки вывода под Windows-консоль (кракозябры)
sys.stdout.reconfigure(encoding='utf-8')

HERE = Path(__file__).resolve().parent
_LOCAL_MODEL = HERE / "rut5-squeeze-fp16"
MODEL_PATH = _LOCAL_MODEL if _LOCAL_MODEL.exists() else "MACreative/rut5-squeeze-fp16"

# Разделитель предложений: после .!?… идёт whitespace (как в squeeze_document.py)
SENT_SEP = re.compile(r'((?<=[.!?…])[ \t\n]+|\n+)')

# Защита от искажения данных моделью: пути и идентификаторы должны сохраниться
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s,;)]+")
_IDENT_RE = re.compile(r"\b\w+_\w+\b")


def tokenize(text):
    """Разбивает текст на (sent, sep_after), разделители сохраняются."""
    items = []
    prev_end = 0
    for m in SENT_SEP.finditer(text):
        sent = text[prev_end:m.start()]
        sep = m.group(0)
        if sent.strip():
            items.append((sent.strip(), sep))
        else:
            if items:
                last_sent, last_sep = items[-1]
                items[-1] = (last_sent, last_sep + sent + sep)
            else:
                items.append(("", sent + sep))
        prev_end = m.end()
    tail = text[prev_end:]
    if tail.strip():
        items.append((tail.strip(), ""))
    return items


def get_content_length(content):
    """Длина content: для списков — полная JSON-сериализация (учитывает base64)."""
    if isinstance(content, list):
        return len(json.dumps(content, ensure_ascii=False))
    return len(str(content)) if content else 0


def get_msg_length(msg):
    """Полный размер сообщения: content + tool_calls (аргументы команд тоже вес)."""
    total = get_content_length(msg.get('content', ''))
    tc = msg.get('tool_calls')
    if tc:
        total += len(json.dumps(tc, ensure_ascii=False))
    return total


def extract_text_from_content(content):
    """Текстовая часть content для сжатия; image_url игнорируется."""
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
        return " ".join(texts)
    return str(content) if content else ""


def get_size(messages):
    """Суммарный размер списка сообщений (content + tool_calls)."""
    return sum(get_msg_length(m) for m in messages)


def should_remove_tool(content):
    """Фильтры для tool-сообщений (нормализуем \n, т.к. в логе они внутри текста)."""
    if not content:
        return False
    normalized = content.replace("\n", " ").strip()
    if "🔎 Найдено команд по" in normalized:
        return True
    if "Ничего не найдено" in normalized and "💡 Используй" in normalized and "запомнить" in normalized:
        return True
    return False


def data_preserved(src, dst):
    """Пути и идентификаторы из src должны остаться в dst (защита от галлюцинаций модели)."""
    for m in _WIN_PATH_RE.finditer(src):
        if m.group(0) not in dst:
            return False
    for m in _IDENT_RE.finditer(src):
        if m.group(0) not in dst:
            return False
    return True


def compress_batch_with_fallback(texts, tok, model, device, max_in=512, max_out=128, beams=1):
    """Сжатие батча. Жесткие откаты к оригиналу:
       - результат не короче оригинала;
       - потеряны пути/идентификаторы."""
    if not texts:
        return []
    inputs = tok(texts, return_tensors="pt", max_length=max_in, truncation=True, padding=True).to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_out, num_beams=beams, no_repeat_ngram_size=3, do_sample=False)
    results = []
    for src, ids in zip(texts, out):
        s = tok.decode(ids, skip_special_tokens=True).strip()
        # Откат: пусто / не короче / потеряны данные
        if not s or len(s) >= len(src) or not data_preserved(src, s):
            results.append(src)
        else:
            results.append(s)
    return results


def clean_images(msg):
    """Физическое удаление image_url из content (список) и base64 (строка)."""
    c = msg.get('content', '')
    if isinstance(c, list):
        cleaned = [item for item in c if not (isinstance(item, dict) and item.get('type') == 'image_url')]
        msg['content'] = cleaned if cleaned else [{"type": "text", "text": ""}]
    elif isinstance(c, str):
        msg['content'] = re.sub(r'data:image/[a-z]+;base64,[A-Za-z0-9+/=]+', '[image]', c, flags=re.IGNORECASE)


def compress_history(input_path, output_path, use_fp16=False, threshold=0.75, max_in=512, max_out=128, beams=1):
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    messages = data.get('messages', [])
    if not messages:
        print("Пустая история")
        return

    total_len = get_size(messages)
    if total_len == 0:
        print("Нет содержимого")
        return

    # Точка отсечки: после какого сообщения превышено threshold символов
    cut_idx = 0
    accum = 0
    threshold_chars = total_len * threshold
    for i, msg in enumerate(messages):
        accum += get_msg_length(msg)
        if accum >= threshold_chars:
            cut_idx = i
            break

    head = messages[:cut_idx + 1]
    tail = messages[cut_idx + 1:]

    print(f"Всего сообщений: {len(messages)}")
    print(f"Точка отсечки: {cut_idx + 1} (первые {threshold * 100:.0f}%)")
    print(f"Хвост: {len(tail)} сообщений\n")

    # Хвост: чистим только image_url/base64, остальное не трогаем
    tail_size_before = get_size(tail)
    for msg in tail:
        clean_images(msg)
    tail_size_after = get_size(tail)

    # Загрузка модели (FP16 через torch_dtype — нативно, без ручной конвертации входов)
    print("Загрузка rut5-squeeze...")
    tok = AutoTokenizer.from_pretrained(MODEL_PATH, use_fast=True)
    # Модель уже сохранена в FP16 на диске — грузим нативно, без конвертации
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_PATH)
    model.config.tie_word_embeddings = False  # убираем warning о tied weights
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device).eval()


    # Статистика по этапам
    stats = []
    def log_stat(name, current_msgs):
        stats.append((name, get_size(current_msgs)))

    log_stat("Начало (голова 75%)", head)
    working = list(head)

    # 1. system — только первый
    seen_system = False
    filtered = []
    for m in working:
        if m.get('role') == 'system':
            if not seen_system:
                filtered.append(m)
                seen_system = True
            continue
        filtered.append(m)
    working = filtered
    log_stat("1. Удалены лишние system", working)

    # 2. assistant с tool_calls — удаляем ТОЛЬКО при content is null; с текстом сохраняем целиком (иначе потеря текста и сироты role:tool)
    working = [m for m in working if not (m.get('role') == 'assistant' and m.get('tool_calls') and m.get('content') is None)]
    log_stat("2. Удалены assistant-обёртки (tool_calls, content=null)", working)

    # 3. tool "🔎 Найдено команд по..."
    working = [m for m in working if not (m.get('role') == 'tool' and '🔎 Найдено команд по' in (m.get('content', '') or ''))]
    log_stat("3. Удалены tool '🔎 Найдено...'", working)

    # 4. tool "Ничего не найдено... 💡 Используй \"запомнить\"..."
    working = [m for m in working if not (m.get('role') == 'tool' and should_remove_tool(m.get('content', '') or ''))]
    log_stat("4. Удалены tool 'Ничего не найдено...'", working)

    # 5. tool с 📁 — обрезаем наполовину
    for m in working:
        c = m.get('content', '') or ''
        if m.get('role') == 'tool' and isinstance(c, str) and c.startswith('📁'):
            m['content'] = c[:len(c)//2] + '...'
    log_stat("5. Обрезаны tool '📁' на 50%", working)

    # 6. image_url / base64 из головы
    for m in working:
        clean_images(m)
    log_stat("6. Удалены image_url / base64 (голова)", working)

    # 7. дедуп tool ДО сжатия
    seen_tools_pre = set()
    deduped = []
    for m in working:
        if m.get('role') == 'tool':
            h = hash(json.dumps(m.get('content', ''), ensure_ascii=False))
            if h in seen_tools_pre:
                continue
            seen_tools_pre.add(h)
        deduped.append(m)
    working = deduped
    log_stat("7. Дедупликация tool (до сжатия)", working)

    # 8. Сжатие моделью: все предложения в один батч-лист
    # Дедупликация чанков по 2 предложения ДО отправки модели
    unique_chunks = []
    chunk_hash_to_idx = {}
    msg_chunk_info = []  # (msg, items, chunk_indices, chunk_sizes)

    for msg in working:
        content = extract_text_from_content(msg.get('content', ''))
        items = tokenize(content)
        to_squeeze = [s for s, _ in items if s]

        chunk_indices = []
        chunk_sizes = []  # сколько предложений в каждом чанке (1 или 2)
        for i in range(0, len(to_squeeze), 2):
            chunk = ' '.join(to_squeeze[i:i+2])
            if not chunk.strip():
                continue
            size = len(to_squeeze[i:i+2])
            h = hash(chunk)
            if h not in chunk_hash_to_idx:
                chunk_hash_to_idx[h] = len(unique_chunks)
                unique_chunks.append(chunk)
            chunk_indices.append(chunk_hash_to_idx[h])
            chunk_sizes.append(size)

        msg_chunk_info.append((msg, items, chunk_indices, chunk_sizes))

    # Сжимаем только уникальные чанки
    batch_size = 32
    squeezed_chunks = []
    next_print = 3
    t0 = time.time()
    for i in range(0, len(unique_chunks), batch_size):
        batch = unique_chunks[i:i+batch_size]
        squeezed_chunks.extend(compress_batch_with_fallback(batch, tok, model, device, max_in, max_out, beams))
        pct = int(min(i + batch_size, len(unique_chunks)) / max(len(unique_chunks), 1) * 100)
        if pct >= next_print:
            print(f"[i] {min(pct, 100)}%", flush=True)
            sys.stdout.flush()
            next_print += 3
    dt = time.time() - t0
    total_chunks_sent = sum(len(c) for _, _, c, _ in msg_chunk_info)
    print(f"[i] Сжатие завершено за {dt:.1f}с ({len(unique_chunks)/max(dt,0.1):.1f} чанков/сек)")
    print(f"[i] Уникальных чанков: {len(unique_chunks)} из {total_chunks_sent} (дедуп: {total_chunks_sent - len(unique_chunks)})")

    # Восстановление сообщений из сжатых чанков
    compressed_msgs = []
    for msg, items, chunk_indices, chunk_sizes in msg_chunk_info:
        new_items = []
        chunk_idx = 0
        sent_in_chunk = 0

        for sent, sep in items:
            if sent:
                chunk_size = chunk_sizes[chunk_idx]
                if sent_in_chunk == 0:
                    # Первое предложение чанка — сюда кладём сжатый текст
                    new_items.append((squeezed_chunks[chunk_indices[chunk_idx]], sep))
                else:
                    # Второе предложение чанка — удаляем (чанк сжат в одно)
                    new_items.append(("", sep))

                sent_in_chunk += 1
                if sent_in_chunk >= chunk_size:
                    sent_in_chunk = 0
                    chunk_idx += 1
            else:
                new_items.append((sent, sep))

        parts = []
        for s, sep in new_items:
            if s:
                parts.append(s)
                parts.append(sep)
        new_content = ''.join(parts).strip()

        new_msg = msg.copy()
        if isinstance(msg.get('content'), list):
            new_msg['content'] = [{"type": "text", "text": new_content}]
        else:
            new_msg['content'] = new_content
        compressed_msgs.append(new_msg)
    log_stat("8. Сжатие моделью (rut5-squeeze, дедуп чанков)", compressed_msgs)

    # 9. дедуп tool ПОСЛЕ сжатия
    seen_tools_post = set()
    deduped = []
    for m in compressed_msgs:
        if m.get('role') == 'tool':
            h = hash(json.dumps(m.get('content', ''), ensure_ascii=False))
            if h in seen_tools_post:
                continue
            seen_tools_post.add(h)
        deduped.append(m)
    log_stat("9. Дедупликация tool (после сжатия)", deduped)

    # Склейка: обработанная голова + нетронутый хвост
    final_msgs = deduped + tail

    # === РЕАЛЬНОЕ СОХРАНЕНИЕ ФАЙЛА (в v12 эта строка терялась) ===
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({'messages': final_msgs}, f, ensure_ascii=False, indent=2)

    # Статистика
    print("\n--- СТАТИСТИКА ЭКОНОМИИ ---")
    print(f"Исходный размер всего лога : {total_len} симв.")
    print(f"Хвост (25%): image_url/base64 выиграл {tail_size_before - tail_size_after} симв.")
    print(f"Размер головы (75%) до обр.: {stats[0][1]} симв.\n")
    for i in range(1, len(stats)):
        name, size = stats[i]
        prev_size = stats[i-1][1]
        print(f"{name:35} : {size:6} симв. (выиграно: {prev_size - size:5} симв.)")

    final_size = get_size(final_msgs)
    print(f"\n✅ Файл записан: {output_path}")
    print(f"Исходный размер: {total_len} символов")
    print(f"Новый размер:    {final_size} символов ({final_size * 100 // max(total_len, 1)}%)")
    if final_size >= total_len:
        print("⚠️ ВНИМАНИЕ: размер не уменьшился!")


def main():
    ap = argparse.ArgumentParser(description="Сжатие истории диалога v13")
    ap.add_argument("вход", help="Путь к JSON-логу")
    ap.add_argument("выход", nargs='?', help="Путь к результату")
    ap.add_argument("--fp16", action="store_true", help="Загрузить модель в FP16 (быстрее, меньше VRAM)")
    ap.add_argument("--порог", type=float, default=0.75, help="Доля первых сообщений для обработки (0.75 = 75%%)")
    ap.add_argument("--max-in", type=int, default=512)
    ap.add_argument("--max-out", type=int, default=128)
    ap.add_argument("--beams", type=int, default=1, help="Количество лучей (1 = быстро, 4 = медленно")
    a = ap.parse_args()
    output_path = a.выход or a.вход.replace('.json', '_compressed.json')
    compress_history(a.вход, output_path, use_fp16=a.fp16, threshold=a.порог,
                     max_in=a.max_in, max_out=a.max_out, beams=a.beams)


if __name__ == "__main__":
    main()