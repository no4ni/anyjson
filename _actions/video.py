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


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".svg", ".tif", ".tiff")


def _env_with_etalons():
    """os.environ + ANYJSON_ETALONS_DIR -> data/etalons. Для внешних CLI."""
    import os as _os_e
    env = dict(_os_e.environ)
    try:
        from _config import BASE_DIR as _BD
        env["ANYJSON_ETALONS_DIR"] = str(_BD / "data" / "etalons")
    except Exception:
        pass
    return env


def _redirect_if_image(path, args):
    """Если путь — картинка, перенаправить в прочитать_полностью."""
    if Path(path).suffix.lower() not in _IMAGE_EXTS:
        return None
    try:
        from _actions.files import read_full
    except Exception as e:
        return "Ошибка: не удалось вызвать прочитать_полностью: " + str(e)
    return read_full(args)


def _ensure_companion_srt(video_path):
    """Если рядом с видео есть <stem>.<lang>.srt/.vtt (напр. .ru.srt),
    но нет <stem>.srt — создать его.

    .srt — hardlink (fallback — копия).
    .vtt — конвертируется в .srt (формат почти совпадает, разница
    в WEBVTT-шапке и запятой вместо точки в таймстемпах).
    .ass/.ssa — пропускаются (нет конвертера).

    Возвращает Path созданного файла или None.
    """
    v = Path(str(video_path))
    if not v.exists():
        return None
    parent = v.parent
    stem = v.stem
    exact = parent / (stem + ".srt")
    if exact.exists():
        return None

    # 1) .srt-варианты: <stem>.<lang>.srt
    try:
        srt_candidates = sorted(parent.glob(stem + ".*.srt"))
    except Exception:
        srt_candidates = []
    for cand in srt_candidates:
        if cand.name == exact.name:
            continue
        try:
            import os as _os
            _os.link(str(cand), str(exact))
            return exact
        except Exception:
            try:
                import shutil as _sh
                _sh.copy2(str(cand), str(exact))
                return exact
            except Exception:
                continue

    # 2) .vtt-варианты: <stem>.<lang>.vtt → конвертируем в .srt
    try:
        vtt_candidates = sorted(parent.glob(stem + ".*.vtt"))
    except Exception:
        vtt_candidates = []
    for cand in vtt_candidates:
        try:
            _vtt_to_srt(cand, exact)
            return exact
        except Exception:
            continue

    return None


def _vtt_to_srt(src, dst):
    """Простой конвертер WebVTT → SRT.

    Не претендует на полную поддержку спецификации: убирает
    WEBVTT-шапку и STYLE/NOTE-блоки, переводит таймстемпы
    «00:00:01.000» → «00:00:01,000», добавляет номера блоков.
    """
    import re as _re_vtt
    raw = Path(str(src)).read_text(encoding="utf-8-sig")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    # режем WEBVTT-шапку и STYLE/NOTE-секции
    raw = _re_vtt.sub(r"^WEBVTT.*?\n\n", "", raw, count=1, flags=_re_vtt.DOTALL)
    raw = _re_vtt.sub(r"\n(STYLE|NOTE|REGION)\b[^\n]*\n(?:.*?\n\n)", "\n\n",
                       raw, flags=_re_vtt.DOTALL)

    out_blocks = []
    idx = 1
    for block in raw.split("\n\n"):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        # ищем строку с таймстемпом
        ts_pos = -1
        for i, l in enumerate(lines):
            if "-->" in l:
                ts_pos = i
                break
        if ts_pos < 0:
            continue
        ts = lines[ts_pos]
        ts = ts.split(" ")[0] + " --> " + ts.split(" ")[2] if len(ts.split(" ")) >= 3 else ts
        ts = ts.replace(".", ",")
        # текст — всё после таймстемпа
        text_lines = lines[ts_pos + 1:]
        # убрать vtt-теги вроде <c.classname>, <00:00:01.000>
        text_lines = [_re_vtt.sub(r"<[^>]+>", "", t) for t in text_lines]
        out_blocks.append(str(idx) + "\n" + ts + "\n" + "\n".join(text_lines))
        idx += 1

    Path(str(dst)).write_text("\n\n".join(out_blocks) + "\n", encoding="utf-8")


def _probe_video_duration(video_path):
    """(duration_sec, fps) через ffprobe. (None, None) при неудаче."""
    import subprocess as _sp
    try:
        from _config import FFPROBE_EXE as _FP
    except Exception:
        return None, None
    _fp = str(_FP) if _FP else ""
    if not _fp or not Path(_fp).exists():
        return None, None
    try:
        r = _sp.run(
            [_fp, "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "format=duration:stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1",
             str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None, None
    _dur = None
    _fps = None
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("duration="):
            try:
                _dur = float(line.split("=", 1)[1])
            except Exception:
                pass
        elif line.startswith("r_frame_rate="):
            try:
                _num, _den = line.split("=", 1)[1].split("/")
                _fps = float(_num) / float(_den)
            except Exception:
                pass
    return _dur, _fps


def _check_sec_in_range(video, sec, action_label):
    """Текст ошибки, если sec за пределами длительности. Иначе None."""
    _dur, _fps = _probe_video_duration(video)
    if _dur is None:
        return None
    if sec < _dur:
        return None
    _last = _dur - 0.1
    if _fps and _fps > 0:
        _last = _dur - (1.0 / _fps) - 0.001
    if _last < 0:
        _last = 0.0
    return (f"❌ {action_label}: секунда {sec} за пределами длительности "
            f"({_dur:.2f} с). Возьми 0..{_last:.2f}.")


def _run_cli(args_list, timeout=120):
    if not CLI.exists():
        return f"❌ Скрипт не найден: {CLI}"
    cmd = [sys.executable, str(CLI)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=_env_with_etalons(),
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


def _run_cli2(args_list, timeout=120, no_clip=False):
    if not CLI2.exists():
        return f"❌ Скрипт не найден: {CLI2}"
    if no_clip:
        args_list = list(args_list) + ["--no-clip"]
    cmd = [sys.executable, str(CLI2)] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=_env_with_etalons(),
        )
    except subprocess.TimeoutExpired:
        return f"❌ Таймаут {timeout}с"
    out = (r.stdout or "") + (r.stderr or "")
    text = out.strip() or f"Готово (код {r.returncode})"
    if "CLIP:" in text and not no_clip:
        text = "[BUF] " + text
    return text


def _run_cli3(args_list, timeout=None, no_clip=False):
    """Отдельный wrapper для spectro_cli (первый запуск может быть долгим).

    stderr показывается в реальном времени (там прогресс из videohear2),
    stdout копится и возвращается как результат.
    """
    if not CLI3.exists():
        return f"❌ Скрипт не найден: {CLI3}"
    if no_clip:
        args_list = list(args_list) + ["--no-clip"]
    cmd = [sys.executable, str(CLI3)] + args_list

    import threading as _th
    stdout_buf = []
    stderr_buf = []

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
            env=_env_with_etalons(),
        )
    except Exception as e:
        return f"❌ Запуск не удался: {e}"

    _NOISE_SKIP = (
        "FT_New_Face failed with index",
        "pkg_resources is deprecated",
        "from pkg_resources import",
        "DeprecationWarning: pkg_resources",
    )
    def _is_noise(s):
        return any(m in s for m in _NOISE_SKIP)

    def _stream_pump(stream, buf, live):
        try:
            for line in iter(stream.readline, ""):
                buf.append(line)
                if live and not _is_noise(line):
                    try:
                        sys.stderr.write(line)
                        sys.stderr.flush()
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = _th.Thread(target=_stream_pump, args=(proc.stdout, stdout_buf, False), daemon=True)
    t_err = _th.Thread(target=_stream_pump, args=(proc.stderr, stderr_buf, True), daemon=True)
    t_out.start()
    t_err.start()

    _timed_out = False
    if timeout is None:
        proc.wait()
    else:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _timed_out = True
            try:
                proc.kill()
            except Exception:
                pass

    t_out.join(timeout=2)
    t_err.join(timeout=2)

    if _timed_out:
        return f"❌ Таймаут {timeout}с (первый запуск может считать спектрограмму долго)"

    text = "".join(stdout_buf).strip()
    if not text:
        text = f"Готово (код {proc.returncode})"
    if "CLIP:" in text and not no_clip:
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


@register(["смотреть_видео", "посмотреть_движение"])
def watch_video(args):
    """Кадр видео на середине реплики N + субтитр -> буфер.
    Параметры:
      путь     — файл видео (обязательно)
      номер    — индекс реплики (по умолчанию 0)
      стрелки  — маркеры направления градиента яркости движения
                 (не статический градиент — берётся с временного следа).
                 true/false, по умолчанию TRUE. false — отключить
    """
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"смотреть_видео": {"путь": "...", "номер": 0}}'

    _redir = _redirect_if_image(video, args)
    if _redir is not None:
        return _redir

    _ensure_companion_srt(video)

    _tc = args.get("в_контекст")
    if _tc is None:
        _tc = args.get("в_буфер", True)
    if isinstance(_tc, str):
        _tc = _tc.strip().lower() not in ("0", "false", "нет", "off", "")
    if _tc is False:
        return ("❌ смотреть_видео: \"в_контекст\": false не поддерживается — "
                "результат идёт только в контекст. Убери параметр.")

    _rep = args.get("номер")
    if _rep is None:
        _rep = args.get("реплика")
    cmd = [str(video), "--номер", str(int(_rep or 0))]
    if args.get("стрелки") is False or args.get("без_стрелок"):
        cmd.append("--без_стрелок")
    if args.get("высота"):
        cmd += ["--высота", str(int(args["высота"]))]

    result = _run_cli(cmd)

    # Если картинка ушла в буфер — в ответ только короткая строка,
    # без технического дампа CLI.
    if isinstance(result, str) and result.lstrip().startswith("[BUF]"):
        return "[BUF] Картинка в буфере обмена"

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
    _ensure_companion_srt(video)
    out = _run_cli([str(video), "--список"])
    # SUBS: ... убираем — это путь к .srt, не нужен в ответе
    _raw = [ln for ln in out.splitlines()
        if not ln.strip().startswith("SUBS:")]
    out = "\n".join(_raw)
    try:
        limit = int(args.get("лимит", 10))
    except (TypeError, ValueError):
        limit = 10
    # 0/отрицательные/больше 10 — к дефолту 10. Без "0 = все".
    if limit < 1 or limit > 10:
        limit = 10
    
    # делим на шапку (TOTAL) и реплики (начинаются с цифры)
    header, reps = [], []
    for ln in out.splitlines():
        s = ln.strip()
        if s and s[0].isdigit():
            reps.append(ln)
        else:
            header.append(ln)
    
    # с / по — диапазон реплик. По умолч. с=0, по=с+limit-1. Кап 10.
    try:
        since = int(args.get("с", 0))
    except (TypeError, ValueError):
        since = 0
    if since < 0:
        since = 0
    try:
        _upto_raw = args.get("по")
        _upto = int(_upto_raw) if _upto_raw is not None else None
    except (TypeError, ValueError):
        _upto = None
    
    total = len(reps)
    if since >= total:
        return "\n".join(header + ["… реплик с " + str(since)
            + " нет; всего " + str(total)])
    
    _end = since + limit
    if _upto is not None:
        _end = min(_end, _upto + 1)
    _end = min(_end, total)
    _shown = reps[since:_end]
    if len(_shown) == total and since == 0:
        return "\n".join(header + _shown)
    note = ("… (реплики " + str(since) + "–" + str(_end - 1)
        + " из " + str(total) + ")")
    return "\n".join(header + _shown + [note])


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
    _video_stem = None
    if media:
        _sz = media[0].stat().st_size / (1024 * 1024)
        msg += f"🎬 {media[0].name}  ({_sz:.1f} МБ)\n"
        _video_stem = media[0].stem
    else:
        msg += f"⚠️ Видеофайл не найден в {out_dir}\n"
    # Показываем только те .srt/.vtt, чей stem совпадает с видео.
    # Раньше брался самый свежий .srt в папке — в D:\Загрузки всегда
    # что-то лежит, и команда выдавала чужой файл.
    _mine = []
    if _video_stem:
        for _s in subs:
            if _s.stem == _video_stem or _s.stem.startswith(_video_stem + "."):
                _mine.append(_s)
    if _mine:
        msg += f"📄 {_mine[0].name}\n"

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


@register(["скачать_субтитры", "скачать_сабы", "вытащить_субтитры"])
def download_subtitles(args):
    """Скачать только субтитры видео (yt-dlp, без скачивания самого видео).

    Параметры:
      url        — ссылка на видео (обязательно), алиасы: ссылка, цель
      язык       — код языка (по умолч. "ru"), можно через запятую
      формат     — vtt (по умолч.) / srt / ass / best
      авто       — true: авто-субтитры (по умолч.), false: только ручные
      все_языки  — true: скачать все доступные языки (по умолч. false)
      путь       — папка для сохранения, алиасы: папка, куда
                   (по умолчанию — <профиль>/subtitles)

    Примеры:
      {"скачать_субтитры": {"url": "https://...", "язык": "ru"}}
      {"скачать_субтитры": {"ссылка": "https://...", "формат": "srt",
                             "все_языки": true}}
    """
    import yt_dlp
    from _config import MY_DIR

    url = (args.get("url") or args.get("ссылка")
           or args.get("цель") or args.get("target") or "").strip()
    if not url:
        return "Укажи url: {\"скачать_субтитры\": {\"url\": \"https://...\"}}"

    import re as _re_url
    _is_url = bool(_re_url.match(r"^https?://[^\s]+\.[^\s]+", url, _re_url.IGNORECASE))
    if not _is_url:
        return (
            "❌ Это не похоже на ссылку: " + repr(url) + "\n"
            "   Нужен полный URL вида https://...\n\n"
            "💡 URL можно найти командой:\n"
            "   {\"веб_поиск\": {\"запрос\": \"<что искать>\"}}\n"
            "   Затем повтори: {\"скачать_субтитры\": {\"url\": \"https://...\"}}"
        )

    lang_raw = args.get("язык", "ru") or "ru"
    langs = [s.strip() for s in str(lang_raw).split(",") if s.strip()]
    all_langs = bool(args.get("все_языки"))

    fmt_raw = str(args.get("формат", "vtt") or "vtt").strip().lower()
    fmt_map = {
        "vtt": "vtt", "webvtt": "vtt",
        "srt": "srt", "subrip": "srt",
        "ass": "ass", "ssa": "ass",
        "best": "best", "лучший": "best", "лучшее": "best",
    }
    sub_fmt = fmt_map.get(fmt_raw, "vtt")

    auto = args.get("авто")
    if auto is None:
        auto = True
    if isinstance(auto, str):
        auto = auto.strip().lower() not in ("0", "false", "нет", "off", "")

    _p = args.get("путь") or args.get("папка") or args.get("куда")
    out_dir = Path(_p) if _p else Path(r"D:\Загрузки")
    out_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": auto,
        "subtitlesformat": sub_fmt,
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    if not all_langs:
        ydl_opts["subtitleslangs"] = langs

    def _is_dns_error(exc):
        s = str(exc)
        return ("getaddrinfo" in s
                or "Failed to resolve" in s
                or "Name or service not known" in s
                or "Temporary failure in name resolution" in s)

    import time as _t
    info = None
    last_err = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as e:
            last_err = e
            if _is_dns_error(e) and attempt < 2:
                _t.sleep(1.5 * (attempt + 1))
                continue
            break

    if info is None:
        _emsg = str(last_err)
        _hint = ""
        if _is_dns_error(last_err):
            _hint = ("\n\n💡 Похоже на временный сбой DNS.\n"
                     "   Повтори команду через пару секунд.\n"
                     "   Если повторяется — проверь Resolve-DnsName <домен>.")
        return "❌ yt-dlp: " + _emsg + _hint

    title = info.get("title", "?")

    sub_exts = (".srt", ".vtt", ".ass", ".ssa")
    found = []
    for ext in sub_exts:
        found += list(out_dir.glob("*" + ext))
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    import time as _time
    now = _time.time()
    # Свежие + совпадающие по stem с title (иначе в папке вроде
    # D:\\Загрузки всегда лежат чужие .srt и они попадают в отчёт).
    _title_safe = title.replace(chr(92), "_").replace("/", "_")
    fresh = [p for p in found
             if (now - p.stat().st_mtime) < 600
             and (p.stem == _title_safe
                  or p.stem.startswith(_title_safe + "."))]

    msg = "✅ " + str(title) + "\n"
    msg += "📁 Папка: " + str(out_dir.resolve()) + "\n"
    if fresh:
        msg += "📄 Файлов субтитров: " + str(len(fresh)) + "\n"
        for p in fresh[:20]:
            msg += "   " + p.name + "  (" + str(p.stat().st_size) + " Б)\n"
    else:
        msg += "⚠️ Субтитры не найдены (возможно, у видео их нет или язык недоступен).\n"
        msg += "   Попробуй: \"авто\": true, \"все_языки\": true\n"

    return msg



@register(["субтитры_в_текст", "сабы_в_текст"])
def subs_to_text(args):
    """Превратить файл субтитров в чистый текст (без номеров и таймстемпов).

    Параметры:
      путь      — файл .srt / .vtt / .ass / .ssa (обязательно)
      сохранить — путь к .txt для сохранения. Без него — текст в ответе
                  (постранично, см. ниже)

    Примеры:
      {"субтитры_в_текст": {"путь": "C:/in.srt"}}
      {"субтитры_в_текст": {"путь": "C:/in.srt", "сохранить": "C:/out.txt"}}
    """
    import re as _re

    src_path = args.get("путь") or args.get("файл")
    if not src_path:
        return 'Укажи путь: {"субтитры_в_текст": {"путь": "C:/in.srt"}}'
    p = Path(str(src_path))
    if not p.exists():
        return "❌ Файл не найден: " + str(p)
    if not p.is_file():
        return "❌ Это не файл: " + str(p)

    try:
        raw = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = p.read_text(encoding="cp1251", errors="replace")

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    out = []
    prev = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if "-->" in line:
            j = i + 1
            while j < n and lines[j].strip():
                t = lines[j].strip()
                t = _re.sub(r"<[^>]+>", "", t)
                t = _re.sub(r"\{[^}]*\}", "", t).strip()
                if t and t != prev:
                    out.append(t)
                    prev = t
                j += 1
            i = j
            continue
        i += 1

    text = "\n".join(out)
    if not text:
        return "⚠️ В файле не найдено ни одной реплики: " + str(p)

    save = args.get("сохранить")
    if save:
        outp = Path(str(save))
        if outp.suffix.lower() != ".txt":
            outp = outp.with_suffix(".txt")
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text + "\n", encoding="utf-8")
        lines_n = text.count("\n") + 1
        return ("✅ Сохранено: " + str(outp.resolve()) + "\n"
                + "📄 Строк: " + str(lines_n)
                + ", символов: " + str(len(text)))

    return text


@register(["кадр+субтитр", "кадр"])
def frame_with_subtitle(args):
    """Простой кадр видео на указанной секунде + субтитр -> буфер обмена.
    Параметры:
      путь      — файл видео (обязательно)
      секунда   — момент в секундах (по умолчанию 0.0)
      slit_scan — true/false, применить slit-scan (по умолч. FALSE)
      окно      — ширина окна slit-scan в сек (по умолч. 3.0)
      субтитр   — true/false, накладывать субтитр (по умолч. TRUE
                  для "кадр+субтитр" / FALSE для алиаса "кадр")
      сохранить — путь для сохранения PNG. Если задан и "в_контекст"
                  не указан явно — буфер не трогаем, результат только
                  в файле.
      в_контекст — положить в контекст (по умолч. true, если "сохранить"
                   не задан; иначе false). Явное true — и файл, и буфер.
    """
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"кадр+субтитр": {"путь": "...", "секунда": 12.5}}'

    _redir = _redirect_if_image(video, args)
    if _redir is not None:
        return _redir

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    _ensure_companion_srt(video)
    _sec_err = _check_sec_in_range(video, sec, "кадр+субтитр")
    if _sec_err:
        return _sec_err
    cmd = [str(video), "--секунда", str(sec)]

    if args.get("щелевой_скан"):
        cmd.append("--slit-scan")
        if args.get("окно"):
            cmd += ["--окно", str(float(args["окно"]))]
    if args.get("субтитр") is False or args.get("без_субтитра"):
        cmd.append("--без-субтитра")
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    to_ctx = args.get("в_контекст")
    if to_ctx is None:
        to_ctx = args.get("в_буфер")
    if to_ctx is None:
        # есть "сохранить" — по умолчанию не трогаем буфер,
        # только файл. Явный в_контекст: true — и то, и другое.
        to_ctx = not bool(args.get("сохранить"))
    if isinstance(to_ctx, str):
        to_ctx = to_ctx.strip().lower() not in ("0", "false", "нет", "off", "")

    if to_ctx is False and not args.get("сохранить"):
        return ("❌ кадр+субтитр: \"в_контекст\": false требует \"сохранить\". "
                "Без сохранения результат некуда девать. "
                "Либо убери в_контекст, либо задай \"сохранить\": \"путь.png\".")

    return _run_cli2(cmd, no_clip=not to_ctx)


@register("слушать_видео")
def listen_video(args):
    """Полный layout плеера (спектрограмма + линия нот + подписи + видео-кадр)
    на указанной секунде.
    Параметры:
      путь      — файл видео (обязательно)
      секунда   — момент в секундах (по умолчанию 0.0)
      размер    — WxH, по умолчанию 1366x768
      сохранить — путь для сохранения PNG. Если задан и "в_контекст"
                  не указан явно — буфер не трогаем, результат только
                  в файле.
      в_контекст — положить в контекст (по умолч. true, если "сохранить"
                   не задан; иначе false). Явное true — и файл, и буфер.
    """
    video = args.get("путь") or args.get("видео")
    if not video:
        return 'Укажи путь: {"слушать_видео": {"путь": "...", "секунда": 30}}'

    _redir = _redirect_if_image(video, args)
    if _redir is not None:
        return _redir

    sec = float(args.get("секунда", args.get("sec", 0.0)) or 0.0)
    _ensure_companion_srt(video)
    _sec_err = _check_sec_in_range(video, sec, "слушать_видео")
    if _sec_err:
        return _sec_err
    cmd = [str(video), "--секунда", str(sec)]

    if args.get("размер"):
        cmd += ["--размер", str(args["размер"])]
    if args.get("сохранить"):
        cmd += ["--сохранить", str(args["сохранить"])]

    to_ctx = args.get("в_контекст")
    if to_ctx is None:
        to_ctx = args.get("в_буфер")
    if to_ctx is None:
        # есть "сохранить" — по умолчанию только файл, без буфера.
        to_ctx = not bool(args.get("сохранить"))
    if isinstance(to_ctx, str):
        to_ctx = to_ctx.strip().lower() not in ("0", "false", "нет", "off", "")

    if to_ctx is False and not args.get("сохранить"):
        return ("❌ слушать_видео: \"в_контекст\": false требует \"сохранить\". "
                "Без сохранения результат некуда девать. "
                "Либо убери в_контекст, либо задай \"сохранить\": \"путь.png\".")

    out = _run_cli3(cmd, no_clip=not to_ctx)
    if isinstance(out, str) and out.lstrip().startswith("[BUF]"):
        return "[BUF] Картинка в буфере обмена"
    return out



_VIDEO_COMMANDS_HINT = """📺 Встроенные видео-команды:

Внутри ||| SOLO-команды (смотреть_видео, кадр+субтитр,
слушать_видео) — только последними. Кадр+субтитр и слушать_видео
можно ставить в середину с "в_контекст": false — тогда картинка
не идёт в контекст, в ответе только отчёт.

Просмотр:

  • смотреть_видео / посмотреть_движение — кадр на середине
    реплики N + субтитр + стрелки. SOLO, в ||| только последним.
    {"смотреть_видео": {"путь": "D:/v.mp4", "реплика": 5}}

  • кадр+субтитр — кадр на указанной секунде + субтитр.
    {"кадр+субтитр": {"путь": "D:/v.mp4", "секунда": 12.5}}
    в ||| в середине — добавь "в_контекст": false

  • слушать_видео — спектрограмма + линия нот + кадр видео.
    {"слушать_видео": {"путь": "D:/v.mp4", "секунда": 30}}
    в ||| в середине — добавь "в_контекст": false

  • список_реплик — первые 10 реплик (индекс, время, текст).
    {"список_реплик": {"путь": "D:/v.mp4", "лимит": 10}}

  • анализ_видео / видео_инфо — метаданные через ffprobe.
    {"видео_инфо": {"путь": "D:/v.mp4"}}

Скачивание:

  • скачать_видео — yt-dlp, видео (+ опц. субтитры).
    {"скачать_видео": {"url": "https://...", "качество": 720}}

  • скачать_субтитры / скачать_сабы — только субтитры.
    {"скачать_субтитры": {"url": "https://...", "язык": "ru"}}

Субтитры:

  • субтитры_в_текст / сабы_в_текст — .srt/.vtt → чистый текст.
    {"субтитры_в_текст": {"путь": "D:/v.srt"}}

  • добавить_субтитры — наложить .srt/.ass на видео.

Файлы и монтаж:

  • каталог_видео / видео_список — список видеофайлов.
    {"каталог_видео": {"путь": "D:/video"}}

  • создать_видео — из ключевых кадров с таймингами.
  • наложить_звук / наложить_аудио — приклеить аудио.
  • грейд — цветокоррекция видео."""


def play_video(args):
    """Показать все встроенные видео-команды anyjson.

    Примеры:
      {"воспроизвести_видео": {}}
      {"видео_команды": {}}
    """
    return _VIDEO_COMMANDS_HINT


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

