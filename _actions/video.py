import threading
"""Видео: смотреть кадр из видео + буфер, скачать с ютуба, список реплик."""
import pathlib
import subprocess
import sys
from pathlib import Path

from _actions import register

from _config import (
    WATCHTOGETHER_CLI as CLI,
    VIDEOHEAR_CLI as CLI2,
    SPECTRO_CLI as CLI3,
)


def _run_cli(args_list, timeout=120):
    if not CLI.exists():
        return f"❌ Скрипт не найден: {CLI}"
    cmd = [sys.executable, str(CLI)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    # если скрипт положил картинку в буфер — маркируем,
    # чтобы _command.py НЕ перезаписал буфер текстом
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text


def _run_cli2(args_list, timeout=120):
    if not CLI2.exists():
        return f"❌ Скрипт не найден: {CLI2}"
    cmd = [sys.executable, str(CLI2)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text


def _run_cli3(args_list, timeout=600):
    """Отдельный wrapper для spectro_cli (первый запуск может быть долгим)."""
    if not CLI3.exists():
        return f"❌ Скрипт не найден: {CLI3}"
    cmd = [sys.executable, str(CLI3)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с (первый запуск может считать спектрограмму долго)"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if "CLIP:" in text:
        text = "[BUF] " + text
    return text


_ZAPRET_DIR = r"D:\Progs\zapret"
_ZAPRET_BIN = _ZAPRET_DIR + r"\bin"
_ZAPRET_LISTS = _ZAPRET_DIR + r"\lists"
_ZAPRET_EXE = _ZAPRET_BIN + r"\winws.exe"
_ZAPRET_PROC = "winws.exe"


def _zapret_args():
    """Полный набор аргументов winws.exe из general (ALT11).bat.

    GameFilterTCP/UDP = 12 (последнее значение из service.bat load_game_filter).
    """
    B = _ZAPRET_BIN
    L = _ZAPRET_LISTS
    return [
        "--wf-tcp=80,443,2053,2083,2087,2096,8443,12",
        "--wf-udp=443,19294-19344,50000-50100,12",
        # фильтр 1: UDP 443
        "--filter-udp=443",
        "--hostlist=" + L + r"\list-general.txt",
        "--hostlist=" + L + r"\list-general-user.txt",
        "--hostlist-exclude=" + L + r"\list-exclude.txt",
        "--hostlist-exclude=" + L + r"\list-exclude-user.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude-user.txt",
        "--dpi-desync=fake",
        "--dpi-desync-repeats=11",
        "--dpi-desync-fake-quic=" + B + r"\quic_initial_www_google_com.bin",
        "--new",
        # фильтр 2: UDP discord/stun
        "--filter-udp=19294-19344,50000-50100",
        "--filter-l7=discord,stun",
        "--dpi-desync=fake",
        "--dpi-desync-fake-discord=" + B + r"\quic_initial_dbankcloud_ru.bin",
        "--dpi-desync-fake-stun=" + B + r"\quic_initial_dbankcloud_ru.bin",
        "--dpi-desync-repeats=6",
        "--new",
        # фильтр 3: TCP discord.media
        "--filter-tcp=2053,2083,2087,2096,8443",
        "--hostlist-domains=discord.media",
        "--dpi-desync=fake,multisplit",
        "--dpi-desync-split-seqovl=681",
        "--dpi-desync-split-pos=1",
        "--dpi-desync-fooling=ts",
        "--dpi-desync-repeats=8",
        "--dpi-desync-split-seqovl-pattern=" + B + r"\tls_clienthello_www_google_com.bin",
        "--dpi-desync-fake-tls=" + B + r"\tls_clienthello_www_google_com.bin",
        "--new",
        # фильтр 4: TCP 443 google
        "--filter-tcp=443",
        "--hostlist=" + L + r"\list-google.txt",
        "--ip-id=zero",
        "--dpi-desync=fake,multisplit",
        "--dpi-desync-split-seqovl=681",
        "--dpi-desync-split-pos=1",
        "--dpi-desync-fooling=ts",
        "--dpi-desync-repeats=8",
        "--dpi-desync-split-seqovl-pattern=" + B + r"\tls_clienthello_www_google_com.bin",
        "--dpi-desync-fake-tls=" + B + r"\tls_clienthello_www_google_com.bin",
        "--new",
        # фильтр 5: TCP 80,443 general
        "--filter-tcp=80,443",
        "--hostlist=" + L + r"\list-general.txt",
        "--hostlist=" + L + r"\list-general-user.txt",
        "--hostlist-exclude=" + L + r"\list-exclude.txt",
        "--hostlist-exclude=" + L + r"\list-exclude-user.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude-user.txt",
        "--dpi-desync=fake,multisplit",
        "--dpi-desync-split-seqovl=664",
        "--dpi-desync-split-pos=1",
        "--dpi-desync-fooling=ts",
        "--dpi-desync-repeats=8",
        "--dpi-desync-split-seqovl-pattern=" + B + r"\tls_clienthello_max_ru.bin",
        "--dpi-desync-fake-tls=" + B + r"\stun.bin",
        "--dpi-desync-fake-tls=" + B + r"\tls_clienthello_max_ru.bin",
        "--dpi-desync-fake-http=" + B + r"\tls_clienthello_max_ru.bin",
        "--new",
        # фильтр 6: UDP 443 ipset
        "--filter-udp=443",
        "--ipset=" + L + r"\ipset-all.txt",
        "--hostlist-exclude=" + L + r"\list-exclude.txt",
        "--hostlist-exclude=" + L + r"\list-exclude-user.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude-user.txt",
        "--dpi-desync=fake",
        "--dpi-desync-repeats=11",
        "--dpi-desync-fake-quic=" + B + r"\quic_initial_www_google_com.bin",
        "--new",
        # фильтр 7: TCP 80,443,8443 ipset
        "--filter-tcp=80,443,8443",
        "--ipset=" + L + r"\ipset-all.txt",
        "--hostlist-exclude=" + L + r"\list-exclude.txt",
        "--hostlist-exclude=" + L + r"\list-exclude-user.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude-user.txt",
        "--dpi-desync=fake,multisplit",
        "--dpi-desync-split-seqovl=664",
        "--dpi-desync-split-pos=1",
        "--dpi-desync-fooling=ts",
        "--dpi-desync-repeats=8",
        "--dpi-desync-split-seqovl-pattern=" + B + r"\tls_clienthello_max_ru.bin",
        "--dpi-desync-fake-tls=" + B + r"\stun.bin",
        "--dpi-desync-fake-tls=" + B + r"\tls_clienthello_max_ru.bin",
        "--dpi-desync-fake-http=" + B + r"\tls_clienthello_max_ru.bin",
        "--new",
        # фильтр 8: TCP GameFilter (12)
        "--filter-tcp=12",
        "--ipset=" + L + r"\ipset-all.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude-user.txt",
        "--dpi-desync=fake,multisplit",
        "--dpi-desync-any-protocol=1",
        "--dpi-desync-cutoff=n4",
        "--dpi-desync-split-seqovl=664",
        "--dpi-desync-split-pos=1",
        "--dpi-desync-fooling=ts",
        "--dpi-desync-repeats=8",
        "--dpi-desync-split-seqovl-pattern=" + B + r"\tls_clienthello_max_ru.bin",
        "--dpi-desync-fake-tls=" + B + r"\stun.bin",
        "--dpi-desync-fake-tls=" + B + r"\tls_clienthello_max_ru.bin",
        "--dpi-desync-fake-http=" + B + r"\tls_clienthello_max_ru.bin",
        "--new",
        # фильтр 9: UDP GameFilter (12)
        "--filter-udp=12",
        "--ipset=" + L + r"\ipset-all.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude.txt",
        "--ipset-exclude=" + L + r"\ipset-exclude-user.txt",
        "--dpi-desync=fake",
        "--dpi-desync-repeats=10",
        "--dpi-desync-any-protocol=1",
        "--dpi-desync-fake-unknown-udp=" + B + r"\quic_initial_dbankcloud_ru.bin",
        "--dpi-desync-cutoff=n4",
    ]


_zapret_lock = threading.Lock()
_zapret_started = False


_ZAPRET_BAT = r"D:\Progs\zapret\anyjson_zapret_start.bat"
_ZAPRET_PROC = "winws.exe"

_zapret_lock = threading.Lock()
_zapret_started = False


_ZAPRET_BAT = r"D:\Progs\zapret\anyjson_zapret_start.bat"
_ZAPRET_PROC = "winws.exe"

_zapret_lock = threading.Lock()
_zapret_started = False


def _zapret_pid_file():
    from _config import MY_DIR as _M
    return _M / "zapret.pid"


def _read_zapret_pid():
    """PID из файла. None если файла нет или он битый."""
    try:
        f = _zapret_pid_file()
    except Exception:
        return None
    if not f.exists():
        return None
    try:
        return int(f.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_zapret_pid(pid):
    try:
        _zapret_pid_file().write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


def _clear_zapret_pid():
    try:
        _zapret_pid_file().unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid):
    """True если процесс с этим PID жив И это winws.exe."""
    if not pid:
        return False
    import subprocess as _sp
    import csv as _csv
    import io as _io
    try:
        r = _sp.run(
            ["tasklist", "/FI", "PID eq " + str(pid), "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
        out = (r.stdout or "").strip()
        if not out:
            return False
        for row in _csv.reader(_io.StringIO(out)):
            if len(row) >= 2 and row[0].lower() == _ZAPRET_PROC.lower():
                return True
    except Exception:
        return False
    return False


def _find_winws_pid():
    """PID первого живого winws.exe или None."""
    import subprocess as _sp
    import csv as _csv
    import io as _io
    try:
        r = _sp.run(
            ["tasklist", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
        out = (r.stdout or "").strip()
        if not out or "No tasks" in out or "нет задач" in out.lower():
            return None
        for row in _csv.reader(_io.StringIO(out)):
            if len(row) >= 2 and row[0].lower() == "winws.exe":
                try:
                    return int(row[1])
                except (ValueError, IndexError):
                    continue
    except Exception:
        return None
    return None


def _ensure_zapret():
    """Запустить zapret с UAC-запросом.

    Использует ShellExecuteW(runas) \u2014 Windows покажет окно UAC.
    Если winws.exe уже работает \u2014 ничего не делает.

    Параллельные вызовы защищены lock-ом.
    """
    global _zapret_started
    with _zapret_lock:
        if _zapret_started:
            return None

        import subprocess as _sp
        import time as _t
        import os as _os
        import ctypes

        _nw = getattr(_sp, "CREATE_NO_WINDOW", 0)

        def _proc_alive():
            try:
                r = _sp.run(
                    ["tasklist", "/FI", "IMAGENAME eq " + _ZAPRET_PROC, "/NH"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=_nw,
                )
                return _ZAPRET_PROC.lower() in (r.stdout or "").lower()
            except Exception:
                return False

        if _proc_alive():
            _zapret_started = True
            return None

        if not _os.path.isfile(_ZAPRET_BAT):
            return "\u274c bat не найден: " + _ZAPRET_BAT

        # ShellExecuteW с "runas" — Windows покажет UAC.
        try:
            _rc = ctypes.windll.shell32.ShellExecuteW(
                None,               # hwnd
                "runas",            # verb
                _ZAPRET_BAT,        # file
                None,               # parameters
                _os.path.dirname(_ZAPRET_BAT),  # working dir
                0,                  # SW_HIDE — без окна, но UAC показать
            )
            # ShellExecuteW возвращает >32 при успехе
            if _rc <= 32:
                return ("\u274c UAC отказан или ошибка запуска (код " + str(_rc) + ").\n"
                        "   Подтверди запрос администратора.")
        except Exception as e:
            return "\u274c Не удалось вызвать ShellExecuteW: " + str(e)

        # Ждём, пока UAC подтвердят и winws.exe стартует
        for _ in range(60):   # до 30 сек (UAC может думать)
            _t.sleep(0.5)
            if _proc_alive():
                _zapret_started = True
                return None

        return ("\u26a0\ufe0f winws.exe не появился за 30 сек.\n"
                "   Возможно, UAC-запрос не подтверждён.")


def _stop_zapret_if_started():
    """Если anyjson сам поднимал winws.exe — убить по PID из zapret.pid.

    Вызывается из anyjson._shutdown_children при выходе.
    """
    global _zapret_started
    _pid = _read_zapret_pid()
    if _pid is None and not _zapret_started:
        return
    import ctypes as _ct
    _target = str(_pid) if _pid else "/IM winws.exe"
    _params = ("/F /PID " + _target) if _pid else "/F /IM winws.exe"
    try:
        _ct.windll.shell32.ShellExecuteW(
            None, "runas", "taskkill.exe", _params, None, 0,
        )
    except Exception:
        pass
    _clear_zapret_pid()
    _zapret_started = False


@register("смотреть_видео")
def watch_video(args):
    """Кадр видео на середине реплики N + субтитр -> буфер.
    Параметры:
      путь     — файл видео (обязательно)
      номер    — индекс реплики (по умолчанию 0)
      стрелки  — true/false, по умолчанию TRUE. false — отключить
    """
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"смотреть_видео": {"путь": "...", "номер": 0}}'

    cmd = [str(video), "--номер", str(int(args.get("номер", 0) or 0))]
    if args.get("стрелки") is False or args.get("без_стрелок"):
        cmd.append("--без_стрелок")
    if args.get("высота"):
        cmd += ["--высота", str(int(args["высота"]))]

    result = _run_cli(cmd)

    # Если субтитров нет — подсказать кадр+субтитр с ручной секундой
    _low = (result or "").lower()
    if "субтитр" in _low and ("не найден" in _low or "not found" in _low):
        _v = str(video).replace(chr(92), "/")
        return (
            f"ℹ️ Субтитры не найдены рядом с {video}.\n"
            f"Кадр по секунде — командой «кадр+субтитр»:\n"
            f'{{"кадр+субтитр": {{"путь": "{_v}", "секунда": 1}}}}'
        )

    return result


@register("список_реплик")
def list_replicas(args):
    """Показать все реплики видео (индекс, время, текст)."""
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"список_реплик": {"путь": "..."}}'
    return _run_cli([str(video), "--список"])


@register("скачать_видео")
def download_video(args):
    """Скачать видео с YouTube/VK с русскими субтитрами (yt-dlp).

    Параметры:
      url       — ссылка на видео (обязательный), алиасы: ссылка, цель
      качество  — число ("720") или "лучшее"/"худшее" (по умолч. 720)
      язык      — код языка субтитров (по умолч. "ru")
      путь      — папка для сохранения, алиасы: папка, куда
      смотреть_после_скачивания — true: сразу открыть кадр (по умолч. false)

    Примеры:
      {"скачать_видео": {"url": "https://...", "качество": 720}}
      {"скачать_видео": {"цель": "https://...", "качество": "лучшее",
                          "папка": "./downloads",
                          "смотреть_после_скачивания": true}}
    """
    # zapret — обход DPI для YouTube/VK. Поднимаем только если winws.exe
    # ещё не запущен, чтобы не плодить процессы.
    _z = _ensure_zapret()
    if _z:
        return _z

    import yt_dlp

    url = (args.get("url") or args.get("ссылка")
           or args.get("цель") or args.get("target") or "").strip()
    if not url:
        return 'Укажи url: {"скачать_видео": {"url": "https://..."}}'

    # простая проверка: похоже ли на URL
    import re as _re_url
    _is_url = bool(_re_url.match(r"^https?://[^\s]+\.[^\s]+", url, _re_url.IGNORECASE))
    if not _is_url:
        return (
            f"❌ Это не похоже на ссылку: {url!r}\n"
            "   Нужен полный URL вида https://...\n\n"
            "💡 URL можно найти командой:\n"
            '   {"веб_поиск": {"запрос": "<что искать>"}}\n'
            '   Затем повтори: {"скачать_видео": {"url": "https://..."}}'
        )

    # качество: число ("720") или слово ("лучшее", "худшее", "среднее")
    _q_raw = args.get("качество", 720)
    if _q_raw is None or _q_raw == "":
        _q_raw = 720
    _q_low = str(_q_raw).strip().lower()
    if _q_low in ("лучшее", "лучший", "best", "макс", "максимум", "max", "высшее"):
        fmt = "bestvideo+bestaudio/best"
    elif _q_low in ("худшее", "худший", "worst", "мин", "минимум", "min"):
        fmt = "worstvideo+worstaudio/worst"
    else:
        try:
            height = int(_q_raw)
        except (ValueError, TypeError):
            return (f'❌ Не понял качество: {_q_raw!r}. '
                    'Укажи число (720), либо "лучшее" / "худшее".')
        fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"

    lang = args.get("язык", "ru") or "ru"
    out_dir = Path(args.get("путь") or args.get("папка") or args.get("куда") or r"D:\Загрузки")
    out_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": fmt,
        "merge_output_format": "mkv",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "srt",
        "embedsubtitles": False,
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "?")
    except Exception as e:
        return f"❌ yt-dlp: {e}"

    # ищем свежие файлы любого формата, который мог выдать yt-dlp
    video_exts = (".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v")
    sub_exts = (".srt", ".vtt", ".ass", ".ssa")

    def _newest(exts):
        found = []
        for ext in exts:
            found += list(out_dir.glob("*" + ext))
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found

    media = _newest(video_exts)
    subs = _newest(sub_exts)

    msg = f"✅ Скачано: {title}\n"
    msg += f"📁 Папка: {out_dir.resolve()}\n"
    if media:
        _sz = media[0].stat().st_size / (1024 * 1024)
        msg += f"🎬 {media[0].name}  ({_sz:.1f} МБ)\n"
    else:
        msg += f"⚠️ Видеофайл не найден в {out_dir}\n"
    if subs:
        msg += f"📄 {subs[0].name}\n"

    # автосмотр первого кадра, если попросили
    if args.get("смотреть_после_скачивания") and media:
        try:
            from _actions.video import watch_video
            _w = watch_video({"путь": str(media[0]), "номер": 0})
            # watch_video ругается "субтитры не найдены" — если файла субтитров
            # нет, это уже сказано выше, дублировать не надо
            if _w and "Субтитры не найдены" not in _w:
                msg += "\n" + _w
        except Exception as _e:
            msg += f"\n(автосмотр не удался: {_e})"

    return msg

@register("кадр+субтитр")
def frame_with_subtitle(args):
    """Простой кадр видео на указанной секунде + субтитр -> буфер обмена.
    Параметры:
      путь      — файл видео (обязательно)
      секунда   — момент в секундах (по умолчанию 0.0)
      slit_scan — true/false, применить slit-scan (по умолч. FALSE)
      окно      — ширина окна slit-scan в сек (по умолч. 3.0)
      субтитр   — true/false, накладывать субтитр (по умолч. TRUE)
      сохранить — путь для дополнительного сохранения PNG
    """
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"кадр+субтитр": {"путь": "...", "секунда": 12.5}}'

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    cmd = [str(video), "--секунда", str(sec)]

    if args.get("щелевой_скан"):
        cmd.append("--slit-scan")
        if args.get("окно"):
            cmd += ["--окно", str(float(args["окно"]))]
    if args.get("субтитр") is False or args.get("без_субтитра"):
        cmd.append("--без-субтитра")
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    return _run_cli2(cmd)


@register("слушать_видео")
def listen_video(args):
    """Полный layout плеера (спектрограмма + линия нот + подписи + видео-кадр)
    на указанной секунде.
    Параметры:
      путь      — файл видео (обязательно)
      секунда   — момент в секундах (по умолчанию 0.0)
      размер    — WxH, по умолчанию 1366x768
      сохранить — путь для дополнительного сохранения PNG
    """
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"слушать_видео": {"путь": "...", "секунда": 30}}'

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    cmd = [str(video), "--секунда", str(sec)]

    if args.get("размер"):
        cmd += ["--размер", str(args["размер"])]
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    return _run_cli3(cmd)


@register(["каталог_видео", "видео_список"])
def video_catalog(args):
    """Список видеофайлов с полными путями.
    Параметры: путь (по умолч. D:\\Загрузки), рекурсивно (по умолч. true).
    """
    from pathlib import Path

    root = Path(args.get("путь") or r"D:\\Загрузки")
    if not root.exists():
        return f"❌ Папка не найдена: {root}"

    exts = {".mp4",".mkv",".avi",".mov",".webm",".flv",
            ".wmv",".m4v",".mpg",".mpeg",".3gp"}
    recurse = args.get("рекурсивно", True)

    files = root.rglob("*") if recurse else root.glob("*")
    found = []
    for f in files:
        try:
            if f.is_file() and f.suffix.lower() in exts:
                found.append((f.stat().st_size, str(f)))
        except Exception:
            continue

    if not found:
        return f"📭 В {root} нет видеофайлов."

    found.sort(key=lambda x: -x[0])
    lines = [f"🎬 {root} — {len(found)} видео:"]
    for size, path in found:
        mb = size / (1024 * 1024)
        lines.append(f"  {mb:8.1f} МБ  {path}")
    return "\n".join(lines)

