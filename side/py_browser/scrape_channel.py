import asyncio
import json
import logging
import random
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright
import aiofiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

_arg = sys.argv[1] if len(sys.argv) > 1 else "macreative"
if _arg.startswith(("http://", "https://")):
    CHANNEL_URL = _arg
else:
    CHANNEL_URL = "https://dzen.ru/" + _arg.lstrip("/")
CHANNEL_SLUG = CHANNEL_URL.rstrip("/").split("/")[-1] or "channel"
OUTPUT_DIR = Path("output") / CHANNEL_SLUG
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = OUTPUT_DIR / "_index.json"


def _norm(u):
    """Нормализация URL: отбрасываем query и fragment."""
    import urllib.parse as _up
    parsed = _up.urlparse(u)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def load_index():
    if not INDEX_FILE.exists():
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_index(index):
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def next_number(index):
    nums = []
    for fn in index.values():
        m = re.match(r"(\d+)_", fn)
        if m:
            nums.append(int(m.group(1)))
    for p in OUTPUT_DIR.glob("*.txt"):
        m = re.match(r"(\d+)_", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


async def scrape_channel():
    index = load_index()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        logger.info(f"Открываем канал: {CHANNEL_URL}")
        await page.goto(CHANNEL_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_selector('article, [data-testid="card-link"], .card', timeout=60000)

        # Умный скролл: останавливаемся, если 2 прокрутки подряд не дают новых URL
        index_keys = set(index.keys())

        async def collect_current_links():
            raw = await page.evaluate("""
                () => {
                    const links = new Set();
                    document.querySelectorAll('a[href*="/a/"]').forEach(a => {
                        const href = a.getAttribute('href');
                        if (href && href.includes('/a/')) links.add(href);
                    });
                    return Array.from(links);
                }
            """)
            norm = set()
            for u in raw:
                full = "https://dzen.ru" + u if u.startswith("/") else u
                norm.add(_norm(full))
            return norm

        last_height = await page.evaluate("document.body.scrollHeight")
        scroll_attempts = 0
        no_new_streak = 0
        while scroll_attempts < 10:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("document.body.scrollHeight")

            current = await collect_current_links()
            new_found = current - index_keys
            if new_found:
                no_new_streak = 0
            else:
                no_new_streak += 1

            scroll_attempts += 1
            logger.info(f"Прокрутка {scroll_attempts}... (новых: {len(new_found)})")

            if no_new_streak >= 2:
                logger.info("Две прокрутки без новых URL — стоп")
                break

            if new_height == last_height:
                break
            last_height = new_height

        article_links = await page.evaluate('''
            () => {
                const links = new Set();
                document.querySelectorAll('a[href*="/a/"]').forEach(a => {
                    const href = a.getAttribute('href');
                    if (href && href.includes('/a/')) {
                        links.add(href);
                    }
                });
                return Array.from(links);
            }
        ''')

        full_links = ["https://dzen.ru" + link if link.startswith('/') else link for link in article_links]

        # Нормализация: отрезаем query и fragment, чтобы /a/XXX?... и /a/XXX#... были одним URL
        norm_map = {}
        for u in full_links:
            nu = _norm(u)
            if nu not in norm_map:
                norm_map[nu] = u

        full_links = list(norm_map.keys())
        logger.info(f"Найдено уникальных статей: {len(full_links)}")

        new_links = [u for u in full_links if u not in index]
        logger.info(f"Новых для скачивания: {len(new_links)}")

        if not new_links:
            logger.info("Нечего качать - все статьи уже в индексе.")
            await browser.close()
            return

        n = next_number(index)

        for i, url in enumerate(new_links):
            logger.info(f"[{i+1}/{len(new_links)}] Обработка: {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded")
                # ждём появления контента статьи
                try:
                    await page.wait_for_selector(
                        '[data-testid="article-body"], [data-testid="article-render__block"]',
                        timeout=15000,
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(1500)

                title_elem = await page.query_selector('h1')
                title = await title_elem.inner_text() if title_elem else f"article_{n}"
                clean_title = re.sub(r'[\\/*?:"<>|]', '', title).strip()[:60]

                # перебираем селекторы — берём первый, который даёт результаты
                paragraphs = []
                used_sel = None
                for sel in (
                    '[data-testid="article-render__block"] p span',
                    '[data-testid="article-render__block"].content--common-block__block-3U span',
                    '[data-testid="article-render__block"] span',
                    '[data-testid="article-body"] p span',
                    '[data-testid="article-body"] p',
                    '[data-testid="article-body"] span',
                    'article p',
                    'article span',
                ):
                    found = await page.query_selector_all(sel)
                    if found and len(found) > 2:
                        paragraphs = found
                        used_sel = sel
                        break

                logger.info(f"  селектор: {used_sel or 'НЕ НАЙДЕН'}, элементов: {len(paragraphs)}")

                content = []
                seen = set()
                for p_elem in paragraphs:
                    text = await p_elem.inner_text()
                    text = text.strip()
                    if text and len(text) > 5 and text not in seen:
                        seen.add(text)
                        content.append(text)

                full_text = '\n\n'.join(content)

                if len(full_text.strip()) < 100:
                    logger.warning(f"Текст не извлёкся ({len(full_text)} симв.), пропускаю: {url}")
                    continue

                filename = f"{n:03d}_{clean_title}.txt"
                clean_url = _norm(url)
                async with aiofiles.open(OUTPUT_DIR / filename, 'w', encoding='utf-8') as f:
                    await f.write(f"URL: {clean_url}\n\n{full_text}")

                index[url] = filename
                save_index(index)
                n += 1
                logger.info(f"Сохранено: {filename}")

                await asyncio.sleep(random.uniform(1.0, 2.5))

            except Exception as e:
                logger.error(f"Ошибка при обработке {url}: {e}")

        await browser.close()
        logger.info(f"Готово! Новых статей: {len(new_links)}. Сохранено в {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(scrape_channel())
