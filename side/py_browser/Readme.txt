py_browser - скрапер статей Дзена для anyjson
=============================================

ОПИСАНИЕ
--------
Playwright-скрапер статей с dzen.ru. Два режима:
  1. Весь канал (инкрементально, по _index.json)
  2. Одна статья по URL

Используется командами anyjson:
  {"обновить_дзен": {"канал": "macreative"}}
  {"дзен_статья": {"url": "https://dzen.ru/a/xxxxx"}}

ФАЙЛЫ
-----
scrape_channel.py  - скрапер (режим канала + режим одной статьи)
requirements.txt   - playwright, aiofiles
output/<slug>/     - статьи канала (NNN_название.txt + _index.json)
output/_single/    - одиночные статьи по URL

ЗАПУСК НАПРЯМУЮ
---------------
```
cd E:/Jericho/Private/anyjson/side/py_browser
python scrape_channel.py macreative                       # весь канал
python scrape_channel.py https://dzen.ru/macreative       # весь канал (URL)
python scrape_channel.py https://dzen.ru/a/aqq7hdEaxmO6oEwN  # одна статья
```

УСТАНОВКА ЗАВИСИМОСТЕЙ
----------------------
```
pip install -r requirements.txt
playwright install chromium
```

КАК РАБОТАЕТ
------------
- headless=True (без окна браузера)
- Умный скролл: останавливается после 2 прокруток без новых URL
- Нормализация URL (без query/fragment) для дедупликации
- Перебор 8 селекторов для извлечения текста статьи
- Сохранение: URL + текст, нумерация NNN_ из _index.json

ИСТОРИЯ
-------
2026-09  создан, режим канала
2026-10-30  добавлен режим одной статьи (по URL), команда дзен_статья
