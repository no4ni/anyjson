"""Файловые операции и запуск скриптов/команд.

Читает пути через _tools_core (get_base_dir, get_public_tools_dir).
"""
import os
import sys
import time
import subprocess
import tempfile
import shutil
from pathlib import Path

from tqdm import tqdm

from _actions import _tools_core


# ---------- вспомогательные ----------
# ============================================================
#  защита чужих профилей
# ============================================================
def _is_foreign_profile(path_str):
    """True, если путь ведёт в чужой профиль (не текущего агента)."""
    try:
        from _config import PROFILES_DIR, AGENT_NAME
        pd = PROFILES_DIR
        an = AGENT_NAME
        if not pd or not an:
            return False
        _p = str(Path(path_str).resolve()).lower().replace("/", "\\")
        _pd = str(Path(pd).resolve()).lower().replace("/", "\\")
        if not _p.startswith(_pd + "\\"):
            return False
        rel = _p[len(_pd) + 1:]
        agent = rel.split("\\", 1)[0]
        return bool(agent) and agent != str(an).lower()
    except Exception:
        return False


def _foreign_guard(path_str):
    """Возвращает сообщение об ошибке или None."""
    if _is_foreign_profile(path_str):
        return f"\u26d4 \u0417\u0430\u043f\u0438\u0441\u044c \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d\u0430: \u0447\u0443\u0436\u043e\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c ({path_str})"
    return None


def compress_output(text, preserve_ws=False):
    if not isinstance(text, str):
        return text
    import re as _re
    if preserve_ws:
        # Для осмотра: сохраняем ведущие пробелы, табы, выравнивание.
        # Схлопываем только серии пустых строк.
        lines = text.split("\n")
        compressed = []
        prev_empty = False
        for line in lines:
            if line == "":
                if not prev_empty:
                    compressed.append("")
                    prev_empty = True
            else:
                compressed.append(line)
                prev_empty = False
        return "\n".join(compressed).strip()
    lines = [line.rstrip() for line in text.split("\n")]
    compressed = []
    prev_empty = False
    for line in lines:
        if line == "":
            if not prev_empty:
                compressed.append("")
                prev_empty = True
        else:
            compressed.append(line)
            prev_empty = False
    text = "\n".join(compressed)
    text = _re.sub(r" {2,}", " ", text)
    return text.strip()


def safe_append_to_file(path, content):
    """Безопасное добавление в файл без вызова внешних функций."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content + "\n")
        return f"OK: {path}"
    except Exception as e:
        return f"ERROR: {e}"


# ---------- PowerShell ----------
def execute_command(cmd_line, timeout=30, preserve_ws=False):
    """Выполнить PowerShell-команду (через -EncodedCommand)."""
    import base64
    encoded_cmd = base64.b64encode(cmd_line.encode("utf-16-le")).decode("ascii")
    # Добавлен -ExecutionPolicy Bypass для гарантии запуска скриптов (как в ручном тесте)
    ps_command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_cmd]
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(
            ps_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        try:
            stdout_str = stdout.decode("utf-8")
        except UnicodeDecodeError:
            stdout_str = stdout.decode("cp1251", errors="replace")
        try:
            stderr_str = stderr.decode("utf-8")
        except UnicodeDecodeError:
            stderr_str = stderr.decode("cp1251", errors="replace")
        output = stdout_str + "\n" + stderr_str
        output = compress_output(output, preserve_ws=preserve_ws)
        return output.strip() if output else "Команда выполнена, вывод пуст."
    except subprocess.TimeoutExpired:
        process.kill()
        return f"Команда превысила время ожидания ({timeout} сек)."
    except Exception as e:
        return f"Ошибка выполнения: {e}"


# ---------- Python-скрипт ----------
def execute_python_script(script, timeout=30):
    import re as _re
    dangerous_patterns = [
        r"import\s+subprocess\s*;", r"import\s+socket\s*;", r"import\s+requests\s*;",
        r"import\s+urllib\s*;", r"eval\s*\(", r"shutil\s*\.", r"pathlib\s*\.",
        r"\b(?:import|from)\s+(?:win32com|pythoncom|comtypes)\b",
    ]
    for pattern in dangerous_patterns:
        if _re.search(pattern, script, _re.IGNORECASE):
            return (f"⚠️ Опасный паттерн заблокирован: {pattern}\n"
                    f"Сообщи Пользователю что ты делаешь и для чего")
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                         delete=False, encoding="utf-8") as f:
            f.write(script)
            temp_path = f.name

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        process = subprocess.Popen(
            ["python", temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )

        output_lines = []
        start_time = time.time()
        while True:
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                if timeout and (time.time() - start_time) > timeout:
                    process.kill()
                    return f"⚠️ Скрипт превысил время ожидания ({timeout} сек)."
                continue
            print(line, end="")
            output_lines.append(line)
            sys.stdout.flush()

        process.wait(timeout=timeout if timeout > 0 else None)
        output = "".join(output_lines)
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return output if output else "Скрипт выполнен без вывода."
    except subprocess.TimeoutExpired:
        return f"⚠️ Скрипт превысил время ожидания ({timeout} сек). Выполнение прервано."
    except UnicodeDecodeError as e:
        return f"Ошибка кодировки в выводе скрипта: {e}"
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return f"Ошибка выполнения скрипта: {e}"


# ---------- чтение / запись ----------
def read_file(path, encoding=None, scroll=None):
    p = Path(path)
    if not p.exists():
        return f"Путь не существует: {path}"
    if p.is_dir():
        files = list(p.iterdir())
        if not files:
            return "Папка пуста"
        return "\n".join(str(f) for f in files)

    MAX_SIZE = 10 * 1024 * 1024
    try:
        if p.stat().st_size > MAX_SIZE:
            # fallback: простой байтовый метод
            return _read_file_bytes(p, encoding, scroll)
    except Exception:
        pass

    block_size = 1024

    encodings = []
    if encoding:
        enc_lower = encoding.lower().replace("-", "").replace("_", "")
        mapping = {
            "windows1251": "cp1251", "win1251": "cp1251", "ansi": "cp1251",
            "utf8": "utf-8", "utf16": "utf-16",
        }
        encodings.append(mapping.get(enc_lower, encoding))
    encodings.extend(["utf-8", "cp1251", "cp866", "latin-1"])

    content = None
    for enc in encodings:
        try:
            with open(p, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if content is None:
        return "Ошибка: не удалось прочитать файл в известных кодировках"

    if scroll is not None:
        try:
            scroll = int(scroll)
            if scroll < 1:
                return "Ошибка: 'скролл' должен быть ≥ 1"
        except (ValueError, TypeError):
            return "Ошибка: 'скролл' должен быть целым числом"
        total_chars = len(content)
        start_char = (scroll - 1) * block_size
        if start_char >= total_chars:
            return f"Ошибка: скролл {scroll} выходит за пределы файла (всего {total_chars} символов)."
        end_char = min(start_char + block_size, total_chars)
        return content[start_char:end_char]
    else:
        if len(content) <= 1024:
            return content
        return content[:1024] + "..."


def _read_file_bytes(p, encoding=None, scroll=None):
    """Резервное чтение для очень больших файлов."""
    block_size = 1024
    try:
        size = p.stat().st_size
        total_blocks = max(1, (size + block_size - 1) // block_size)
        if scroll is not None:
            try:
                s = int(scroll)
                if s < 1:
                    return "Ошибка: 'скролл' должен быть ≥ 1"
            except (ValueError, TypeError):
                return "Ошибка: 'скролл' должен быть целым числом"
            if s > total_blocks:
                return f"Ошибка: скролл {s} вне диапазона (всего блоков {total_blocks})"
            with open(p, "rb") as f:
                f.seek((s - 1) * block_size)
                data = f.read(block_size)
        else:
            with open(p, "rb") as f:
                data = f.read(block_size)
        return data.decode(encoding or "utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения: {e}"


def write_file(path, content):
    _guard = _foreign_guard(path)
    if _guard:
        return _guard
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Файл перезаписан: {path}"
    except Exception as e:
        return f"Ошибка записи файла: {e}"


def append_to_file(path, content):
    _guard = _foreign_guard(path)
    if _guard:
        return _guard
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return (f"Содержимое добавлено в конец файла: {path}\n\n"
                f"💡 Если ты хотел перезаписать файл целиком, используй команду \"переписать\".")
    except Exception as e:
        return f"Ошибка добавления в файл: {e}"


def create_dir(path: str) -> str:
    p = Path(path)
    if p.exists():
        return f"Папка уже существует: {path}"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"Папка создана: {path}"
    except Exception as e:
        return f"Ошибка создания папки: {e}"


# ---------- удаление / перемещение ----------
def safe_delete_file(path: str) -> str:
    _guard = _foreign_guard(path)
    if _guard:
        return _guard
    p = Path(path)
    if not p.exists():
        return f"Файл не существует: {path}"
    if p.is_dir():
        return f"Это папка, а не файл: {path}. Используйте 'удалить_папку'."
    if p.suffix.lower() in (".sys", ".dll", ".exe"):
        return f"⛔ Удаление системных файлов запрещено: {p.name}"
    try:
        p.unlink()
        return f"Файл удалён: {path}"
    except Exception as e:
        return f"Ошибка удаления файла: {e}"


def move_item(source: str, destination: str) -> str:
    _guard1 = _foreign_guard(source)
    if _guard1:
        return _guard1
    _guard2 = _foreign_guard(destination)
    if _guard2:
        return _guard2
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.exists():
        return f"❌ Источник не существует: {source}"

    # нормализуем пути для сравнения
    try:
        src_r = src.resolve()
        dst_r = dst.resolve()
    except Exception:
        src_r = src
        dst_r = dst

    # 1. Источник и назначение совпадают — no-op
    if src_r == dst_r:
        return f"❌ Источник и назначение совпадают: {src}"

    # 2. dst — существующая папка: кладём src ВНУТРЬ, если только
    #    dst не является родителем src (тогда это та же папка)
    if dst.is_dir():
        # если dst — родитель src: dst / src.name == src → no-op
        try:
            _would_be = (dst / src.name).resolve()
        except Exception:
            _would_be = dst / src.name
        if _would_be == src_r:
            return f"❌ Папка уже находится здесь: {src}"
        dst = dst / src.name
        try:
            dst_r = dst.resolve()
        except Exception:
            dst_r = dst

    # 3. Проверка «внутрь себя»
    try:
        if dst_r == src_r or dst_r.is_relative_to(src_r):
            return f"❌ Нельзя переместить папку {src} внутрь самой себя ({dst})"
    except (ValueError, AttributeError):
        pass

    # 4. Цель уже существует
    if dst.exists():
        return f"❌ Цель уже существует: {dst}. Удалите её сначала или укажите другой путь."

    try:
        if src.is_dir():
            # СНАЧАЛА собираем список файлов, ПОТОМ создаём dst.
            # Иначе dst появится внутри src и попадёт в собственный обход.
            all_items = list(src.rglob("*"))
            total_files = sum(1 for _ in all_items if _.is_file())

            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.mkdir(parents=True, exist_ok=True)

            with tqdm(total=total_files, desc=f"Перемещение {src.name}",
                      unit="файл") as pbar:
                for item in all_items:
                    try:
                        rel = item.relative_to(src)
                    except ValueError:
                        continue
                    target = dst / rel
                    if item.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(item), str(target))
                        pbar.update(1)
                    elif item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
            # удаляем пустой src
            try:
                src.rmdir()
            except OSError:
                shutil.rmtree(src, ignore_errors=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        return f"✅ Перемещено: {source} → {dst}"
    except Exception as e:
        return f"❌ Ошибка перемещения: {e}"


def _move_dir_with_progress(src_dir: Path, dst_dir: Path, pbar):
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        target = dst_dir / item.name
        if item.is_file():
            shutil.move(str(item), str(target))
            pbar.update(1)
        elif item.is_dir():
            _move_dir_with_progress(item, target, pbar)
    try:
        src_dir.rmdir()
    except OSError:
        shutil.rmtree(src_dir)


# ---------- сенсор / форматирование ----------
def run_sensor(file_path, config, lang="rus+eng", nolimit=False,
               output=None, timeout=30):
    try:
        base_dir = _tools_core.get_base_dir()
        public_tools_dir = _tools_core.get_public_tools_dir()
        if not os.path.isabs(file_path) or (os.name == "nt" and not file_path[1:2] == ":"):
            abs_path = base_dir / file_path.lstrip("/\\")
        else:
            abs_path = Path(file_path)
        if not abs_path.exists():
            return f"Ошибка: файл {abs_path} не найден."
        sensor_script = public_tools_dir / "sensor.py"
        cmd = f'python "{sensor_script}" "{abs_path}" {lang}'
        if nolimit:
            cmd += " --nolimit"
        if config:
            cmd += f' --config "{config}"'
        if output:
            cmd += f' --output "{output}"'
        return execute_command(cmd, timeout)
    except Exception as e:
        return f"Ошибка при запуске сенсора: {e}"
