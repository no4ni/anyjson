# -*- coding: utf-8 -*-
"""
Диагностика сжатия: показывает вклад каждого этапа.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

def extract_text(content):
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
        return " ".join(texts)
    return str(content) if content else ""

def total_size(messages):
    return sum(len(extract_text(m.get('content', '')) or '') for m in messages)

def main():
    log_path = sys.argv[1]
    with open(log_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    messages = data.get('messages', [])
    total_msgs = len(messages)
    
    # Точка отсечки 75%
    total_len = total_size(messages)
    cut_idx = 0
    accum = 0
    for i, msg in enumerate(messages):
        accum += len(extract_text(msg.get('content', '')) or '')
        if accum >= total_len * 0.75:
            cut_idx = i
            break
    
    head = messages[:cut_idx + 1]
    tail = messages[cut_idx + 1:]
    
    print(f"Исходно: {total_msgs} сообщений, {total_len} символов")
    print(f"Голова (75%): {len(head)} сообщений")
    print(f"Хвост (25%): {len(tail)} сообщений, {total_size(tail)} символов (не трогаем)")
    print()
    
    # Этап 0: только голова
    working = list(head)
    size = total_size(working)
    print(f"[0] Голова до обработки: {len(working)} сообщ., {size} симв.")
    
    # Этап 1: system - только первый
    before = len(working)
    before_size = size
    seen_sys = False
    new_working = []
    for msg in working:
        if msg.get('role') == 'system':
            if not seen_sys:
                new_working.append(msg)
                seen_sys = True
            continue
        new_working.append(msg)
    working = new_working
    size = total_size(working)
    removed = before - len(working)
    saved = before_size - size
    print(f"[1] system (только первый): удалено {removed} сообщ., экономия {saved} симв. -> {len(working)} сообщ., {size} симв.")
    
    # Этап 2: tool "🔎 Найдено команд по"
    before = len(working)
    before_size = size
    working = [m for m in working if not (m.get('role') == 'tool' and '🔎 Найдено команд по' in (m.get('content', '') or ''))]
    size = total_size(working)
    removed = before - len(working)
    saved = before_size - size
    print(f"[2] tool '🔎 Найдено команд по': удалено {removed} сообщ., экономия {saved} симв. -> {len(working)} сообщ., {size} симв.")
    
    # Этап 3: tool "Ничего не найдено. 💡 Используй \"запомнить\""
    before = len(working)
    before_size = size
    def is_nothing_found(m):
        if m.get('role') != 'tool':
            return False
        c = (m.get('content', '') or '').replace('\n', ' ')
        return 'Ничего не найдено' in c and '💡 Используй' in c and 'запомнить' in c
    working = [m for m in working if not is_nothing_found(m)]
    size = total_size(working)
    removed = before - len(working)
    saved = before_size - size
    print(f"[3] tool 'Ничего не найдено...': удалено {removed} сообщ., экономия {saved} симв. -> {len(working)} сообщ., {size} симв.")
    
    # Этап 4: 📁 обрезать наполовину
    before_size = size
    for msg in working:
        if msg.get('role') == 'tool' and (msg.get('content', '') or '').startswith('📁'):
            c = msg['content']
            msg['content'] = c[:len(c)//2] + '...'
    size = total_size(working)
    saved = before_size - size
    print(f"[4] tool '📁' обрезка: экономия {saved} симв. -> {len(working)} сообщ., {size} симв.")
    
    # Этап 5: image_url выкинуть
    before_size = size
    for msg in working:
        c = msg.get('content', '')
        if isinstance(c, list):
            msg['content'] = [item for item in c if not (isinstance(item, dict) and item.get('type') == 'image_url')]
        elif isinstance(c, str):
            msg['content'] = re.sub(r'data:image/[a-z]+;base64,[A-Za-z0-9+/=]+', '[image]', c, flags=re.IGNORECASE)
    size = total_size(working)
    saved = before_size - size
    print(f"[5] image_url: экономия {saved} симв. -> {len(working)} сообщ., {size} симв.")
    
    # Этап 6: дедуп tool ДО сжатия
    before = len(working)
    before_size = size
    seen = set()
    deduped = []
    for msg in working:
        if msg.get('role') == 'tool':
            h = hash(msg.get('content', ''))
            if h in seen:
                continue
            seen.add(h)
        deduped.append(msg)
    working = deduped
    size = total_size(working)
    removed = before - len(working)
    saved = before_size - size
    print(f"[6] дедуп tool (до сжатия): удалено {removed} сообщ., экономия {saved} симв. -> {len(working)} сообщ., {size} симв.")
    
    # Этап 7: сжатие rut5-squeeze (здесь только оценка - реальный прогон через модель)
    # Для оценки возьмём коэффициент из предыдущего прогона: 67023/87304 ≈ 0.768
    # Но это для всего пайплайна. Для головы после фильтров - примерно тот же коэффициент.
    # Точную цифру даст только реальный прогон.
    print(f"[7] сжатие rut5-squeeze: (см. реальный прогон)")
    
    # Этап 8: дедуп tool ПОСЛЕ сжатия
    print(f"[8] дедуп tool (после сжатия): (см. реальный прогон)")
    
    print()
    print(f"Итого голова после фильтров (без сжатия): {len(working)} сообщ., {size} симв.")
    print(f"Хвост (не тронут): {len(tail)} сообщ., {total_size(tail)} симв.")
    print(f"Сумма без сжатия: {size + total_size(tail)} симв.")
    print(f"Реальный итог (со сжатием): 67023 симв.")
    print(f"Экономия от сжатия головы: {(size + total_size(tail)) - 67023} симв.")

if __name__ == "__main__":
    main()