"""Хардкод: запуск файла из TEST_PATH (см. ниже) с живым выводом в консоль."""
import os
import subprocess
import sys
import threading
import time

from _actions import register
from _utils import truncate_output


TEST_PATH = r"C:\Temp\test.py"


@register(["запустить_тест", "тест"])
def run_test(args):
    """Запустить файл TEST_PATH и вернуть вывод.

    Параметров нет. Дополнительно (необязательно):
      таймаут — секунд до kill (по умолчанию 60)
      тишина  — секунд без вывода до kill (по умолчанию 30)

    Пример:
      {"запустить_тест": {}}
    """
    timeout = args.get("таймаут", 60)
    silence_timeout = args.get("тишина", 30)

    if not os.path.exists(TEST_PATH):
        return f"Ошибка: файл не найден: {TEST_PATH}"

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    cmd = [sys.executable, '-u', TEST_PATH]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            env=env, bufsize=1,
        )
    except Exception as e:
        return f"Ошибка запуска: {e}"

    output_lines = []
    last_output = [time.time()]
    killed_silence = [False]
    killed_timeout = [False]
    start_time = time.time()

    def reader():
        try:
            for line in proc.stdout:
                last_output[0] = time.time()
                output_lines.append(line.rstrip('\n'))
                try:
                    print(line, end='')
                    sys.stdout.flush()
                except Exception:
                    pass
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    while proc.poll() is None:
        time.sleep(0.5)
        now = time.time()
        if now - last_output[0] > silence_timeout:
            killed_silence[0] = True
            try:
                proc.kill()
            except Exception:
                pass
            break
        if timeout and now - start_time > timeout:
            killed_timeout[0] = True
            try:
                proc.kill()
            except Exception:
                pass
            break

    t.join(timeout=2)

    output = '\n'.join(output_lines)
    if killed_silence[0]:
        return (f"⛔ Нет вывода ({silence_timeout} сек, процесс убит).\n"
                f"Строк: {len(output_lines)}.\n{output}")
    if killed_timeout[0]:
        return f"⏱ Таймаут ({timeout} сек).\n{output}"
    return truncate_output(output) if output else "Скрипт выполнен без вывода."