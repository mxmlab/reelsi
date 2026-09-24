# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Авто-ротоскоп человека через Robust Video Matting (RVM) — локально на GPU.

Отдаёт АЛЬФА-МАСКУ (grayscale видео): в After Effects вешается luma-матте на копию
клипа камеры — человек оказывается перед вставкой, без ручного Roto Brush.

Зачем маска, а не вырезанный персонаж: цвет/экспозиция берутся с самого клипа камеры.

Конвейер ПОТОКОВЫЙ, весь на GPU (v2, ускорение ~10x против v1):
    ffmpeg NVDEC-декод + даунскейл -> pipe -> RVM fp16 (CUDA) -> pipe -> ffmpeg NVENC.
Без промежуточных файлов сегментов и без PNG-секвенций (в v1 на них уходило ~80%
времени: 4K PNG писались на CPU по одному, плюс два прохода libx264 по 4K).

Маска считается в ПОЛОВИННОМ разрешении для 4K-исходников (1080p): для luma-матте
этого достаточно, а пикселей в 4 раза меньше. Коэффициент возвращается в поле "f"
каждой маски — AE-слой маски масштабируется на scale*f (см. xml2ae). Отключить:
env REELSI_ROTO_FULLRES=1.

Первый запуск качает модель RVM (torch.hub, ~интернет один раз). Нужны:
    pip install torch torchvision   (CUDA-сборка под твою карту)
ffmpeg — уже используется проектом (NVENC/NVDEC подхватываются автоматически,
при их отсутствии тихий откат на CPU-декод/libx264).

CLI (тест качества на одном видео/диапазоне):
    python reelsi/roto.py "C:/.../C1247.MP4" --out _roto_test
    python reelsi/roto.py "C:/.../C1247.MP4" --ranges 0-2.6,5.3-8.5 --out _roto_test

Программно:
    from core import roto
    masks = roto.alpha_for_ranges(video, [(0,2.6),(5.3,8.5)], out_dir, emit=print)
    # -> [{"start":0.0,"end":2.6,"mask": ".../roto_000.mp4","f":2.0}, ...]
"""
from __future__ import annotations
import os, subprocess, time
from typing import Any, Callable, Sequence, cast

from core import paths
from core.app_meta import env, console_emit, wrap_emit
from core.device import pick_device, autocast_dtype
from core.umsg import ReelsiError, cli_error

_MODEL: tuple[Any, str, Any] | None = None          # (model, dev, dtype) кэш
_VARIANT = "mobilenetv3"   # быстрее; "resnet50" — качественнее/медленнее
# Фиксация коммита (голова master от 2023-03-13) для воспроизводимости и безопасности
RVM_REPO = "PeterL1n/RobustVideoMatting:53d74c6826735f01f4406b5ca9075eee27bec094"
# seq_chunk = сколько кадров прогоняем через модель разом. В fp16 на половинном
# разрешении 8 кадров ~ 2 ГБ VRAM; на CPU/fp32 движок сам ужмёт до 2.
SEQ_CHUNK = 8
_INTERNAL_PX = 960     # внутреннее разрешение RVM по длинной стороне (как v1: 4K*0.25)
_HW: dict[str, Any] = {"dec": {}, "enc": None}     # кэш доступности NVDEC (по кодеку исходника) / NVENC


def _pick_device(force: str | None = None) -> str:
    """cuda / mps / cpu. force имеет приоритет, затем env REELSI_ROTO_DEVICE, затем авто."""
    return pick_device(force or env("ROTO_DEVICE"))


def _is_oom(ex: BaseException) -> bool:
    """Нехватка видеопамяти? torch.cuda.OutOfMemoryError есть не во всех сборках,
    плюс OOM прилетает и текстом из ffmpeg/драйвера — проверяем и то, и другое."""
    try:
        import torch
        if isinstance(ex, getattr(torch.cuda, "OutOfMemoryError", ())):
            return True
    except ReelsiError: raise
    except Exception:
        pass  # torch нет — OOM распознаем по тексту ошибки ниже
    s = str(ex).lower()
    return ("out of memory" in s or "cuda error" in s
            or "cublas_status_alloc_failed" in s or "cudnn_status_alloc_failed" in s)


def release(emit: Any = console_emit) -> bool:
    """Выгрузить модель RVM из памяти/VRAM (звать после сборки, чтобы не держать
    видеопамять, пока веб-интерфейс простаивает). По образцу transcribe.release_model."""
    emit = wrap_emit(emit)
    global _MODEL
    if _MODEL is None:
        return False
    _MODEL = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except ReelsiError: raise
    except Exception:
        pass  # torch/GPU нет — чистить нечего, модель уже выгружена
    emit("  RVM выгружен из памяти")
    return True


def _load(force_device: str | None = None) -> tuple[Any, str, Any]:
    """Загрузить модель RVM (torch.hub, с кэшем). На CUDA — fp16 (2x скорость, 1/2 VRAM).
    Если запрошено другое устройство, чем в кэше — перезагрузить (и освободить старое)."""
    global _MODEL
    dev = _pick_device(force_device)
    if _MODEL is not None:
        if _MODEL[1] == dev:
            return _MODEL
        release()                                     # сменили cpu<->cuda — перегрузить
    try:
        import torch
    except Exception as e:
        raise RuntimeError("Нужен PyTorch: pip install torch torchvision (CUDA). " + str(e))
    # RVM грузим с закреплённого коммита (голова master от 2023-03-13): ветка — это
    # «какой код попадёт к нам сегодня», у чужого репозитория так нельзя.
    # skip_validation=True — нарочно. В torch/hub.py (2.5.1) ref без этого флага уходит в
    # _validate_not_a_forked_repo, а та принимает его, только если он совпал с именем
    # ветки/тега или с sha ИХ вершины (br["commit"]["sha"].startswith(ref)), иначе
    # ValueError: проверка сломала бы загрузку, как только в master появится новый коммит.
    # Плюс это два неавторизованных запроса к api.github.com (лимит 60/час на IP).
    model = torch.hub.load(RVM_REPO, _VARIANT, skip_validation=True)
    dtype = autocast_dtype(dev)
    model = model.to(dev, dtype).eval()
    _MODEL = (model, dev, dtype)
    return _MODEL


MIN_SEG_SEC = 0.15   # короче — не рото-ить (микро-вставки/каты дают вырез без кадров -> падение RVM)


def _has_frames(path: str) -> bool:
    """Проверить, что в файле есть декодируемый видеопоток хотя бы с 1 кадром."""
    if not os.path.isfile(path):
        return False
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "0", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30).stdout.strip()
        return out.isdigit() and int(out) >= 1
    except ReelsiError: raise
    except Exception:
        return False


def _probe(video: str) -> tuple[int, int, float, str]:
    """(width, height, fps_float, fps_raw) видеопотока — размеры КАК НА ЭКРАНЕ.
    Вертикальные исходники (side data rotation=±90) ffmpeg авто-поворачивает при
    декоде, поэтому для конвейера w/h меняем местами (иначе кадр сплющит)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "0", "-select_streams", "v:0", "-of", "default=noprint_wrappers=1",
             "-show_entries", "stream=width,height,r_frame_rate:stream_side_data=rotation", video],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30).stdout.strip()
    except ReelsiError: raise
    except Exception:            # недоступный файл/зависший I/O -> дефолты, а не висящий поток
        return 0, 0, 60.0, "60"
    try:
        kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
        w, h = int(kv.get("width") or 0), int(kv.get("height") or 0)
        fr = kv.get("r_frame_rate") or "60"
        if "/" in fr:
            a, b = fr.split("/"); fps = float(a) / float(b)
        else:
            fps = float(fr or 60)
        if abs(int(float(kv.get("rotation") or 0))) % 180 == 90:
            w, h = h, w
        return w, h, fps, fr
    except ReelsiError: raise
    except Exception:
        return 0, 0, 60.0, "60"


def _fps(video: str) -> float:
    return _probe(video)[2]


def _codec(video: str) -> str:
    """codec_name видеопотока (для кэша NVDEC: h264 может уметь, а prores/vp9 — нет)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "0", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name", "-of", "csv=p=0", video],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30).stdout.strip()
    except ReelsiError: raise
    except Exception:
        out = ""
    return out or os.path.splitext(video)[1].lower()   # кодек не распознан — хотя бы расширение


def _nvdec_ok(video: str) -> bool:
    """NVDEC-декод доступен для этого исходника? Кэш ПО КОДЕКУ: в наборе камеры могут
    быть в разных кодеках — глобальный кэш по первому видео молча ронял декод остальных."""
    key = _codec(video)
    if key not in _HW["dec"]:
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-hwaccel", "cuda", "-i", video,
                                "-frames:v", "1", "-f", "null", "-"],
                               capture_output=True, timeout=60)
            _HW["dec"][key] = (r.returncode == 0)
        except ReelsiError: raise
        except Exception:
            _HW["dec"][key] = False        # проба зависла/упала -> CPU-декод
    return cast(bool, _HW["dec"][key])


def _nvenc_ok() -> bool:
    """h264_nvenc доступен? (проверка один раз на сессию)

    Кадр пробы — 256x256, НЕ меньше: на 64x64 NVENC отвечает «Frame Dimension less
    than the minimum supported value», проба всегда падала и рото молча уезжало
    на libx264 (CPU) даже при свободной GPU."""
    if _HW["enc"] is None:
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                "color=black:s=256x256:d=0.1", "-c:v", "h264_nvenc",
                                "-f", "null", "-"], capture_output=True, timeout=60)
            _HW["enc"] = (r.returncode == 0)
        except ReelsiError: raise
        except Exception:
            _HW["enc"] = False             # проба зависла/упала -> libx264
    return cast(bool, _HW["enc"])


def _read_exact(pipe: Any, n: int) -> bytes:
    """Дочитать ровно n байт из pipe (read может отдать меньше). b'' на EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = pipe.read(n - len(buf))
        if not chunk:
            return bytes(buf) if buf else b""
        buf += chunk
    return bytes(buf)


def _mask_scale_div(src_h: int) -> int:
    """Во сколько раз ужимать маску. 4K -> половина (1080p, для luma-матте хватает),
    FHD и меньше — как есть. env REELSI_ROTO_FULLRES=1 = всегда полное разрешение."""
    if env("ROTO_FULLRES"):
        return 1
    return 2 if src_h >= 1600 else 1


def alpha_for_video(video: str, out_mask: str, downsample_ratio: float | None = None, bottom_pct: float = 0.0,
                    device: str | None = None, seq_chunk: int | None = None, emit: Any = console_emit,
                    start: float = 0.0, n_frames: int | None = None) -> tuple[str, float] | None:
    """RVM по `video` -> grayscale альфа-маска в `out_mask` (.mp4). Потоково:
    ffmpeg-декод (NVDEC если есть) -> RVM на GPU -> ffmpeg-энкод (NVENC если есть).
    start/n_frames: окно исходника (сек / кадров) — без промежуточного файла.
    bottom_pct (0..1): нижняя доля кадра всегда белая (стол/статичный передний план).
    device: 'cuda'|'cpu'|None(авто/env); seq_chunk: кадров за раз (память↔скорость).
    Возвращает (out_mask, f) где f = во сколько раз маска мельче исходника, или None."""
    emit = wrap_emit(emit)
    model, dev, dtype = _load(device)
    import torch
    if dev == "cpu" and not (device or env("ROTO_DEVICE")):
        emit("⚠ CUDA недоступна (torch.cuda.is_available()=False) — считаю на CPU, медленно. "
             "Нужна CUDA-сборка torch: pip uninstall -y torch torchvision, затем установка с --index-url .../cu121")
    w, h, fps, fps_raw = _probe(video)
    if not w or not h:
        raise RuntimeError("ffprobe не отдал размеры видео: " + video)
    div = _mask_scale_div(h)
    ow, oh = (w // div) // 2 * 2, (h // div) // 2 * 2
    if downsample_ratio is None:                 # внутреннее разрешение RVM как в v1 (~960px)
        downsample_ratio = min(1.0, float(_INTERNAL_PX) / max(ow, oh))
    chunk = int(seq_chunk or (SEQ_CHUNK if dev == "cuda" else 2))

    dec = ["ffmpeg", "-v", "error"]
    if dev == "cuda" and _nvdec_ok(video):
        dec += ["-hwaccel", "cuda"]
    if start:
        dec += ["-ss", f"{start:.3f}"]           # seek ДО -i: не декодируем всё с нуля
    dec += ["-i", video]
    if n_frames:
        dec += ["-frames:v", str(int(n_frames))]
    dec += ["-an", "-vf", f"scale={ow}:{oh}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

    vf = "format=yuv420p"
    bp = max(0.0, min(1.0, float(bottom_pct)))
    if bp > 0:                                    # залить нижнюю полосу белым (включить стол)
        vf += f",drawbox=x=0:y=ih*{1 - bp:.4f}:w=iw:h=ih:color=white:t=fill"
    enc = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{ow}x{oh}", "-framerate", fps_raw, "-i", "pipe:0", "-vf", vf]
    if dev == "cuda" and _nvenc_ok():
        enc += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    else:
        enc += ["-c:v", "libx264", "-crf", "16", "-preset", "veryfast"]
    enc += [out_mask]

    dev_label = f"{dev} fp16" if dtype == torch.float16 else dev
    emit("RVM ({variant}, {dev_label}, {ow}x{oh}, seq={chunk}) -> {name}",
         variant=_VARIANT, dev_label=dev_label, ow=ow, oh=oh, chunk=chunk,
         name=os.path.basename(out_mask))
    frame_bytes = ow * oh * 3
    p_dec: Any = subprocess.Popen(dec, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             bufsize=frame_bytes * (chunk + 1))
    p_enc: Any = subprocess.Popen(enc, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    import numpy as np
    done, t0, t_rep, last = 0, time.time(), time.time(), cast(Any, None)
    try:
        with torch.inference_mode():
            rec = [None] * 4
            while True:
                frames = []
                for _ in range(chunk):
                    raw = _read_exact(p_dec.stdout, frame_bytes)
                    if len(raw) < frame_bytes:
                        break
                    frames.append(np.frombuffer(raw, np.uint8).reshape(oh, ow, 3))
                if not frames:
                    break
                src = torch.from_numpy(np.stack(frames)).to(dev)          # [T,H,W,3] u8
                src = src.permute(0, 3, 1, 2).unsqueeze(0).to(dtype).div_(255)  # [1,T,3,H,W]
                fgr, pha, *rec = model(src, *rec, downsample_ratio)
                a8 = pha[0, :, 0].mul_(255).byte().cpu().numpy()          # [T,H,W] u8
                p_enc.stdin.write(a8.tobytes())
                last = a8[-1]
                done += len(frames)
                if time.time() - t_rep > 5:
                    if n_frames:
                        emit("  · rvm {done}/{total} кадров ({fps:.0f} fps)",
                             done=done, total=int(n_frames), fps=done / max(time.time() - t0, 0.01))
                    else:
                        emit("  · rvm {done} кадров ({fps:.0f} fps)",
                             done=done, fps=done / max(time.time() - t0, 0.01))
                    t_rep = time.time()
        # исходник кончился раньше запрошенного окна — доложить хвост последним кадром,
        # чтобы длина маски совпала со вставкой по кадрам
        if n_frames and done and done < int(n_frames):
            miss = int(n_frames) - done
            if miss > max(2, int(n_frames) * 0.05):   # большой недобор ≠ хвост исходника
                emit("  ⚠ rvm: декод дал {done}/{total} кадров — маска в конце "
                     "замрёт ({miss} кадров доложено последним; возможно, оборвался декод)",
                     done=done, total=int(n_frames), miss=miss)
            else:
                emit("  · rvm: {done}/{total} кадров, хвост доложен последним кадром",
                     done=done, total=int(n_frames))
            for _ in range(miss):
                p_enc.stdin.write(last.tobytes())
        p_enc.stdin.close()
        p_enc.wait()
    finally:
        for p in (p_dec, p_enc):                      # без wait() на Windows висят хендлы
            try:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=10)
            except ReelsiError: raise
            except Exception:
                pass  # процесс уже мёртв — wait не нужен
        for f in (p_dec.stdout, p_enc.stdin):
            try:
                f.close()
            except ReelsiError: raise
            except OSError:
                pass  # поток уже закрыт
    if not done:
        raise RuntimeError("RVM не выдал кадры альфы (декод пуст?)")
    emit("  · rvm готово: {done} кадров за {sec:.1f}с ({fps:.0f} fps)",
         done=done, sec=time.time() - t0, fps=done / max(time.time() - t0, 0.01))
    return (out_mask, float(div)) if _has_frames(out_mask) else None


def _mask_key(video: str, s: float, e: float, bottom_pct: float, div: int) -> str:
    """Стабильный ключ маски по СОДЕРЖИМОМУ: pkey-путь, mtime в наносекундах и размер
    (st_mtime_ns + st_size) + границы + низ + делитель разрешения + модель/px RVM.
    st_mtime_ns и st_size защищают от подмены видео на том же пути в ту же секунду
    (перезапись экспорта): целые секунды mtime давали ложное попадание в кэш со старой
    маской. pkey на Windows и macOS учитывает регистронезависимость ФС, а на Linux
    сохраняет регистрозависимость (два разных файла A.mp4 и a.mp4 не сливаются).
    Переход на наносекунды и размер одноразово обесценивает старый кэш масок — так
    задумано ради надёжности (одна пересчитанная сборка)."""
    import hashlib
    try:
        st = os.stat(video)
        mt_ns, sz = st.st_mtime_ns, st.st_size
    except OSError:
        mt_ns, sz = 0, 0
    raw = (f"{paths.pkey(os.path.abspath(video))}|{mt_ns}|{sz}|{round(s, 3)}|{round(e, 3)}|"
           f"{round(float(bottom_pct), 4)}|d{div}|m{_VARIANT}|px{_INTERNAL_PX}")
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def alpha_for_ranges(video: str, ranges: Sequence[Any], out_dir: str, bottom_pct: float = 0.0,
                     device: str | None = None, seq_chunk: int | None = None, emit: Any = console_emit, cache_dir: str | None = None,
                     cancel: Callable[[], bool] | None = None, failures: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Для каждого (start,end) сек исходного видео камеры получить альфа-маску его
    сегмента. Возвращает [{start,end,mask,f}] (mask = grayscale .mp4, f = во сколько
    раз маска мельче исходника — AE-слой маски масштабировать на scale*f).

    Кэш: имя маски = хэш(исходник+mtime+размер+границы+низ+делитель). Если такой файл уже
    есть (в cache_dir или out_dir) — переиспользуем, RVM не гоняем заново (тот же
    камера+фрагмент = та же маска). cache_dir=None -> кэшируем в out_dir.

    failures — необязательный список, куда дописываются {"start", "end", "error"}
    для каждого не посчитанного куска при сбое сегмента, OOM или пустой маске.

    cancel — колбэк «нажали Стоп?»: проверяется ПЕРЕД каждым куском. Рото — самый
    долгий этап сборки (сотни кусков на шестиминутном ролике), и без этой проверки
    «Стоп» не давал ничего: джоб смотрел на флаг только между ФАЙЛАМИ. Уже
    посчитанные маски остаются в кэше и переиспользуются на следующей сборке."""
    emit = wrap_emit(emit)
    cancel = cancel or (lambda: False)
    if not os.path.isfile(video):
        raise FileNotFoundError(video)
    cache_dir = cache_dir or out_dir
    os.makedirs(cache_dir, exist_ok=True)
    _, h, fps, _ = _probe(video)
    div = _mask_scale_div(h or 0)
    # Чанк разрешаем ЗДЕСЬ, тем же правилом, что и alpha_for_video: основной путь
    # (xml2ae/build.py) seq_chunk не передаёт вовсе, и по None нельзя было понять,
    # с каким чанком мы реально идём. Раньше это стоило ретрая по OOM: сравнение
    # `seq_chunk > 2` падало TypeError прямо из except-блока, а после `or 2` —
    # тихо не срабатывало, хотя фактический чанк на CUDA равен SEQ_CHUNK.
    # _pick_device — тот же резолвер, что внутри _load, только без загрузки модели.
    chunk = int(seq_chunk or (SEQ_CHUNK if _pick_device(device) == "cuda" else 2))
    res, n_cache = cast(list[dict[str, Any]], []), 0
    for i, (s, e) in enumerate(ranges):
        if cancel():
            emit("  ⏹ рото прервано на {cur}/{total} — посчитанные маски в кэше",
                 cur=i + 1, total=len(ranges))
            break
        if e - s < MIN_SEG_SEC:                   # микро-диапазон — рото не нужен и ломает вырез
            if e > s:
                emit("  · пропуск рото {s:.2f}-{e:.2f} (короче {min_sec:g}с)",
                     s=s, e=e, min_sec=MIN_SEG_SEC)
            continue
        mask = os.path.join(cache_dir, f"roto_{_mask_key(video, s, e, bottom_pct, div)}.mp4")
        if _has_frames(mask):                     # уже считали этот камера+фрагмент — реюз
            res.append({"start": float(s), "end": float(e), "mask": mask, "f": float(div)})
            n_cache += 1
            continue
        n = max(1, int(round((e - s) * fps)))     # ровно столько кадров, сколько у вставки
        try:
            out = alpha_for_video(video, mask, bottom_pct=bottom_pct, device=device,
                                  seq_chunk=chunk, emit=emit, start=s, n_frames=n)
            if out:
                res.append({"start": float(s), "end": float(e), "mask": out[0], "f": out[1]})
            else:
                if failures is not None:
                    failures.append({"start": float(s), "end": float(e), "error": "маска пустая"})
        except ReelsiError: raise
        except Exception as ex:
            # CUDA OOM — особый случай: рото теперь сплошное на весь хрон, у 6-минутного
            # ролика это сотни диапазонов. Раньше цикл ловил ЛЮБУЮ ошибку и шёл дальше,
            # держа модель в VRAM: при зажатой памяти (рядом LM Studio) OOM повторялся
            # на каждом сегменте, каждый раз поднимая и убивая два ffmpeg. На Windows
            # это не «упало с OOM», а повисшая машина. Освобождаем память и один раз
            # пробуем меньшим чанком; не помогло — выходим с внятным сообщением.
            if _is_oom(ex):
                emit("  ! видеопамять кончилась на {s:.2f}-{e:.2f} — освобождаю и пробую мельче",
                     s=s, e=e)
                release(emit=emit)
                if chunk > 2:
                    try:
                        out = alpha_for_video(video, mask, bottom_pct=bottom_pct, device=device,
                                              seq_chunk=2, emit=emit, start=s, n_frames=n)
                        if out:
                            res.append({"start": float(s), "end": float(e),
                                        "mask": out[0], "f": out[1]})
                            chunk = 2                # дальше идём мельче, не упираясь снова
                            continue
                        else:
                            if failures is not None:
                                failures.append({"start": float(s), "end": float(e),
                                                 "error": "маска пустая"})
                            chunk = 2
                            continue
                    except ReelsiError: raise
                    except Exception as ex2:
                        ex = ex2
                release(emit=emit)
                emit("  ! рото прервано: не хватает видеопамяти. Закрой LM Studio/другие "
                     "модели и собери заново (готовые маски переиспользуются из кэша).")
                if failures is not None:
                    for s_rem, e_rem in ranges[i:]:
                        if e_rem - s_rem >= MIN_SEG_SEC:
                            failures.append({"start": float(s_rem), "end": float(e_rem),
                                             "error": "не хватает видеопамяти"})
                break
            emit("  ! RVM ошибка на {s:.2f}-{e:.2f}: {err}", s=s, e=e, err=str(ex))
            if failures is not None:
                failures.append({"start": float(s), "end": float(e), "error": str(ex)})
    if n_cache:
        emit("  · рото из кэша: {count} сегм. (не пересчитывал)", count=n_cache)
    return res


if __name__ == "__main__":
    try:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("video")
        ap.add_argument("--ranges", help="сек: 0-2.6,5.3-8.5 (по умолчанию — всё видео)")
        ap.add_argument("--bottom", type=float, default=0.0, help="нижняя доля кадра в маску (0..1), напр. 0.18 = стол")
        ap.add_argument("--device", choices=["cuda", "cpu"], help="принудительно cuda/cpu (иначе авто/env REELSI_ROTO_DEVICE)")
        ap.add_argument("--seq", type=int, help=f"кадров за раз (по умолчанию {SEQ_CHUNK}; меньше = меньше VRAM)")
        ap.add_argument("--out", default=paths.root("_roto_out"))
        a = ap.parse_args()
        if a.ranges:
            rr = []
            for part in a.ranges.split(","):
                s, e = part.split("-"); rr.append((float(s), float(e)))
            out = alpha_for_ranges(a.video, rr, a.out, bottom_pct=a.bottom,
                                   device=a.device, seq_chunk=a.seq)
            print("маски:", [os.path.basename(m["mask"]) for m in out])
        else:
            os.makedirs(a.out, exist_ok=True)
            m = alpha_for_video(a.video, os.path.join(a.out, "roto_full.mp4"),
                                bottom_pct=a.bottom, device=a.device, seq_chunk=a.seq)
            print("маска:", m and m[0])
    except ReelsiError as e:
        cli_error(e)
