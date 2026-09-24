# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Транскрипция аудио: локально Qwen2.5-Omni-7B (bnb 4-bit) — он СЛЫШИТ звук и передаёт
повторы/оговорки дословно (в отличие от Whisper, который их причёсывает), — ИЛИ облачная
аудио-модель, если в ai_config.json выбран Omni-профиль (active_omni != "__local__";
тогда аудио-чанки уходят провайдеру OpenAI-совместимым input_audio; Claude не умеет).

Аудио режется на чанки по паузам VAD (~24с), каждый транскрибируется отдельно.
Тайминги реза потом берём из VAD (границы = тишина), Omni даёт содержание.

    python omni_asr.py <wav16k_int16>   ->  <wav>.omni.json  [{start,end,text}]

Отдельный процесс: тяжёлый torch/bnb, не смешивать с ctranslate2 (Whisper) в одном.
Тяжёлые импорты (torch/transformers) — ТОЛЬКО в локальном пути (облачному не нужны).
"""
from __future__ import annotations
import sys, os, json, base64, io, tempfile, urllib.request, urllib.error
from typing import Any, Sequence, cast

# Импорт до первого try: сторож `except ReelsiError` ниже обязан видеть это имя.
from core.umsg import ReelsiError, cli_error
# Xet-протокол HF (hf_xet) на Windows виснет при скачивании больших весов (xet_get
# застревает, сеть на нуле). Классический HTTP-download надёжнее и докачивает с места.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
try:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    cast(Any, sys.stderr).reconfigure(encoding="utf-8")   # текст ошибки идёт в stderr:
except ReelsiError: raise
except Exception:                              # без utf-8 русский текст превращается в \uXXXX
    pass  # поток без reconfigure — русский текст ошибки и так уходит в stderr
import soundfile as sf
from core import vad
from core import aicut

from core.app_meta import APP_NAME, APP_REFERER, console_emit, http_req, wrap_emit


MODEL = "Qwen/Qwen2.5-Omni-7B"
SR = 16000
SYS = ("Ты — предельно точный транскрибатор русской речи. Запиши ДОСЛОВНО всё, что слышишь, "
       "включая повторы одной фразы, оговорки, брошенные на середине слова/фразы и запинки. "
       "Ничего не исправляй, не сокращай и не убирай повторы. Верни только текст.")


def load_model() -> tuple[Any, Any]:
    import torch
    from transformers import (Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor,  # type: ignore[attr-defined, unused-ignore]  # атрибут есть не во всех версиях пакета; пакет опционален, в CI не установлен
                              BitsAndBytesConfig)
    proc = Qwen2_5OmniProcessor.from_pretrained(MODEL)
    cfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16,
                             bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True)
    m = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        MODEL, torch_dtype=torch.float16, device_map="cuda",
        quantization_config=cfg, attn_implementation="sdpa").eval()
    try:
        m.disable_talker()
    except ReelsiError: raise
    except Exception:
        pass  # версия модели без talker — отключать нечего
    return proc, m


def transcribe_clip(proc: Any, model: Any, arr_int16: Any) -> str:
    import torch
    from qwen_omni_utils import process_mm_info
    tmp = os.path.join(tempfile.gettempdir(), "_omni_clip_%d.wav" % os.getpid())  # pid: два прогона не топчут друг друга
    try:
        sf.write(tmp, arr_int16, SR, subtype="PCM_16")
        conv = [{"role": "system", "content": [{"type": "text", "text": SYS}]},
                {"role": "user", "content": [{"type": "audio", "audio": tmp}]}]
        text = proc.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
        audios, images, videos = process_mm_info(conv, use_audio_in_video=False)
        inputs = proc(text=text, audio=audios, images=images, videos=videos,
                      return_tensors="pt", padding=True, use_audio_in_video=False)
        inputs = inputs.to(model.device).to(model.dtype)
        with torch.inference_mode():
            ids = model.generate(**inputs, return_audio=False, max_new_tokens=420)
        gen = ids[:, inputs["input_ids"].shape[1]:]
        return proc.batch_decode(gen, skip_special_tokens=True,
                                 clean_up_tokenization_spaces=False)[0].strip()
    finally:
        try:
            os.remove(tmp)
        except ReelsiError: raise
        except OSError:
            pass  # временный wav уже убран


def transcribe_clip_gigaam(model: Any, clip_audio: Any) -> str:
    """Транскрибировать чанк через GigaAM. Принимает путь к wav-файлу (грузит через ffmpeg),
    а не numpy-массив. Пишем во временный файл, дескриптор сразу закрываем,
    а сам файл гарантированно удаляем в finally."""
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="_omni_gigaam_")
    os.close(fd)
    try:
        sf.write(tmp, clip_audio, SR, subtype="PCM_16")
        # Короткие чанки (<25с) — transcribe; длинные — transcribe_longform
        # (иначе GigaAM бросает ValueError "Too long wav file").
        if len(clip_audio) > SR * 25:
            res = model.transcribe_longform(tmp)
        else:
            res = model.transcribe(tmp)
        # TranscriptionResult / LongformTranscriptionResult оба отдают текст через .text
        return res.text if hasattr(res, "text") else str(res)
    finally:
        try:
            os.remove(tmp)
        except ReelsiError: raise
        except OSError:
            pass  # временный wav уже убран


def _hf_cache_bytes(repo: str) -> int:
    """Сколько байт весов репо уже лежит в HF-кэше (вкл. недокачанные .incomplete)."""
    d = os.path.expanduser("~/.cache/huggingface/hub/models--"
                           + repo.replace("/", "--") + "/blobs")
    if not os.path.isdir(d):
        return 0
    tot = 0
    for f in os.listdir(d):
        if f.endswith(".lock"):
            continue
        try:
            tot += os.path.getsize(os.path.join(d, f))
        except OSError:
            pass  # файл исчез между обходом и замером — в объём не попадёт
    return tot


def ensure_weights(repo: str, emit: Any = console_emit, retries: int = 8) -> str:
    """Скачать веса repo С ПРОГРЕССОМ (в stdout -> виден в webui-логе) и АВТО-ДОКАЧКОЙ
    при обрыве. snapshot_download resumable: уже скачанное (.incomplete) не теряется,
    докачивается с места. Xet отключён на верхнем уровне модуля (Windows-хэнг)."""
    emit = wrap_emit(emit)
    import threading, time
    from huggingface_hub import snapshot_download
    total = 0
    try:
        from huggingface_hub import HfApi
        info: Any = HfApi().model_info(repo, files_metadata=True)
        total = sum((s.size or 0) for s in info.siblings
                    if (s.rfilename or "").endswith((".safetensors", ".bin")))
    except ReelsiError: raise
    except Exception:
        pass  # HuggingFace недоступен — размер весов не покажем, качаем дальше
    tgb = total / 1e9
    last = None
    for attempt in range(retries + 1):
        holder: dict[str, Any] = {}
        def _dl() -> None:
            try:
                holder["path"] = snapshot_download(repo, max_workers=4)
            except ReelsiError: raise
            except Exception as e:
                holder["err"] = e
        th = threading.Thread(target=_dl, daemon=True)
        th.start()
        while th.is_alive():                              # прогресс раз в ~4с
            th.join(timeout=4)
            got = _hf_cache_bytes(repo) / 1e9
            if tgb:
                emit("  скачивание весов: {got:.1f}/{total:.1f} ГБ ({pct}%)",
                     got=got, total=tgb, pct=min(100, int(100 * got / tgb)))
            else:
                emit("  скачивание весов: {got:.1f} ГБ", got=got)
        if "path" in holder:
            emit("  веса на месте")
            return holder["path"]
        last = holder.get("err")
        emit("  ! обрыв скачивания ({err_type}: {err}) — докачиваю с места ({cur}/{total})…",
             err_type=type(last).__name__, err=str(last)[:80], cur=attempt + 1, total=retries)
        time.sleep(5)
    raise ReelsiError(f"не удалось скачать веса после {retries + 1} попыток: {last}")



# модели, которые оказались чистыми ASR (эндпоинт /audio/transcriptions, не /chat) —
# запоминаем по имени, чтобы следующие чанки сразу шли правильным путём
_ASR_MODELS: set[str] = set()


def _asr_transcribe(prof: dict[str, Any], arr_int16: Any, retries: int = 2) -> str:
    """Чанк -> ASR-модель через OpenAI-совместимый /audio/transcriptions (Whisper-стиль:
    multipart с wav-файлом). Так работают qwen3-asr-flash, parakeet-tdt и пр. на
    OpenRouter — они ТОЧНЕЕ chat-моделей для транскрипции (это их единственная задача)."""
    import time
    buf = io.BytesIO()
    sf.write(buf, arr_int16, SR, format="WAV", subtype="PCM_16")
    wav = buf.getvalue()
    bnd = "----reelsiASRboundary7MA4YWxkTrZu0gW"      # статичный boundary (без random)
    body = b"".join([
        f'--{bnd}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{prof["model"]}\r\n'.encode(),
        f'--{bnd}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'.encode(),
        (f'--{bnd}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
         f'Content-Type: audio/wav\r\n\r\n').encode(), wav, b"\r\n",
        f'--{bnd}--\r\n'.encode()])
    headers = {"Content-Type": f"multipart/form-data; boundary={bnd}"}
    if prof.get("api_key"):
        headers["Authorization"] = "Bearer " + prof["api_key"]
    if prof["provider"] == "openrouter":
        headers["HTTP-Referer"] = APP_REFERER
        headers["X-Title"] = APP_NAME
    headers = aicut.apply_profile_headers(headers, prof)
    url = prof["base_url"].rstrip("/") + "/audio/transcriptions"
    last, attempt, waits429 = None, 0, 0
    while attempt <= retries:
        try:
            with urllib.request.urlopen(
                    http_req(url, data=body, headers=headers), timeout=300) as r:
                resp = json.load(r)
            txt = resp.get("text")
            if txt is None and isinstance(resp.get("results"), list):   # у некоторых иначе
                txt = " ".join(x.get("text", "") for x in resp["results"])
            return (txt or "").strip()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except ReelsiError: raise
            except Exception:
                pass  # тело ответа не прочиталось — код HTTP-ошибки у нас уже есть
            if e.code in (401, 403):
                raise ReelsiError(f"Omni-профиль «{prof['name']}»: API-ключ не принят ({e.code})")
            if e.code == 402:
                raise ReelsiError(f"у провайдера кончились кредиты (402): {detail[:150]}")
            if e.code == 429:
                waits429 += 1
                if waits429 > 5:
                    raise ReelsiError("лимит запросов провайдера (429) не отпускает — подожди и повтори")
                print(f"  ! лимит запросов (429) — жду 20с ({waits429}/5)…", flush=True)
                time.sleep(20)
                continue
            last = f"{e.code}: {detail}"
        except json.JSONDecodeError as e:
            raw = (getattr(e, "doc", "") or "").strip().replace("\r", " ").replace("\n", " ")
            last = f"не-JSON ответ провайдера: {raw[:120]}" if raw else f"не-JSON ответ провайдера ({e})"
            if prof.get("api_key"):
                last = last.replace(prof["api_key"], "***")
        except (urllib.error.URLError, KeyError) as e:
            last = str(e)
        attempt += 1
        print(f"  ! ASR-чанк не расшифровался ({last}) — повтор {attempt}/{retries}", flush=True)
    raise ReelsiError(f"ASR-транскрипция упала после {retries + 1} попыток: {last}")


# фразы «я не получил аудио» — признак ГЛУХОГО эндпоинта: модель числится
# аудио-мультимодальной, но провайдер выкидывает input_audio и до неё доходит
# только текст (так ведёт себя nvidia/nemotron-3-nano-omni на OpenRouter).
# Без этой проверки такие «ответы» молча уезжают в транскрипт как реплики.
_DEAF = ("нет аудио", "не был предоставлен", "не предоставлен", "пришлите аудио",
         "предоставьте аудио", "загрузите аудио", "не вижу приложенн", "не получил аудио",
         "no audio", "audio provided", "didn't receive any audio", "provide the audio")
_deaf_hits = [0]


def _looks_deaf(txt: str | None) -> bool:
    t = (txt or "").lower()
    return len(t) < 400 and any(k in t for k in _DEAF)


def _deaf_guard(prof: dict[str, Any], txt: str) -> str:
    """Глухой ответ -> пустая строка + счётчик. Три подряд = эндпоинт не принимает
    звук, дальше гонять чанки бессмысленно (и дорого по времени)."""
    if not _looks_deaf(txt):
        _deaf_hits[0] = 0
        return txt
    _deaf_hits[0] += 1
    if _deaf_hits[0] >= 3:
        raise ReelsiError(
            f"модель {prof['model']} НЕ СЛЫШИТ аудио: провайдер отвечает «аудио не "
            f"предоставлено» на каждый чанк. Так ведёт себя nemotron-3-nano-omni на "
            f"OpenRouter — в карточке модели аудио заявлено, но эндпоинт его выбрасывает. "
            f"Возьми в Omni-профиле google/gemini-2.5-flash (проверено, транскрибирует) "
            f"или ASR-модель, либо переключи Omni на «Локально»")
    return ""


def transcribe_clip_cloud(prof: dict[str, Any], arr_int16: Any, retries: int = 2) -> str:
    """Чанк -> облачная аудио-модель. Chat-модели (Gemini и т.п.) — через input_audio на
    /chat/completions; чистые ASR (qwen3-asr, parakeet) — через /audio/transcriptions
    (переключаемся автоматически, поймав ответ «is a transcription model»)."""
    if prof["model"] in _ASR_MODELS:                  # уже знаем, что это ASR-модель
        return _asr_transcribe(prof, arr_int16, retries=retries)
    buf = io.BytesIO()
    sf.write(buf, arr_int16, SR, format="WAV", subtype="PCM_16")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    # max_tokens с запасом: reasoning-модели (nemotron-omni и т.п.) тратят токены на
    # размышления ДО ответа — 700 может съесться целиком и вернуть пустой текст
    payload: dict[str, Any] = {"model": prof["model"], "temperature": 0.0, "max_tokens": 1600,
               "messages": [
                   {"role": "system", "content": SYS},
                   {"role": "user", "content": [{"type": "input_audio",
                        "input_audio": {"data": b64, "format": "wav"}}]}]}
    # «Не прислали параметр» != «выключено»: думающие по умолчанию модели жгли весь
    # max_tokens на размышления, content приходил пустым — и ниже в транскрипт уезжали
    # РАССУЖДЕНИЯ модели, по которым потом резался ролик. Провайдер, не понявший
    # параметр, ответит 400 -> шлём повтор без него (см. обработку ниже).
    use_reasoning = prof["provider"] in ("openrouter", "openai")
    if use_reasoning:
        payload["reasoning"] = {"enabled": False}
    headers = {"Content-Type": "application/json"}
    if prof.get("api_key"):
        headers["Authorization"] = "Bearer " + prof["api_key"]
    if prof["provider"] == "openrouter":
        headers["HTTP-Referer"] = APP_REFERER
        headers["X-Title"] = APP_NAME
    headers = aicut.apply_profile_headers(headers, prof)
    url = prof["base_url"].rstrip("/") + "/chat/completions"
    last, attempt, waits429 = None, 0, 0
    while attempt <= retries:
        req = http_req(url, data=json.dumps(payload).encode("utf-8"),
                       headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.load(r)
            if not resp.get("choices"):
                raise RuntimeError("провайдер не вернул ответ: "
                                   + json.dumps(resp, ensure_ascii=False)[:300])
            ch = resp["choices"][0]
            msg = ch["message"]
            # reasoning_content — это МЫСЛИ модели, а не расшифровка. Подставлять их
            # в транскрипт нельзя: они уезжали в .omni.json, и нарезка резалась по ним.
            txt = (msg.get("content") or "").strip()
            if not txt:
                why = ("ответ обрезан по max_tokens" if ch.get("finish_reason") == "length"
                       else "модель вернула пустой текст")
                if attempt < retries:
                    last = why
                    attempt += 1
                    continue                      # повтор вместо мусора в транскрипте
                raise RuntimeError(f"Omni: {why} — расшифровка чанка не получена")
            return _deaf_guard(prof, txt)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except ReelsiError: raise
            except Exception:
                pass  # тело ответа не прочиталось — код HTTP-ошибки у нас уже есть
            if e.code in (401, 403):
                raise ReelsiError(f"Omni-профиль «{prof['name']}»: API-ключ не принят ({e.code})")
            dl = detail.lower()
            if e.code == 400 and use_reasoning and "reason" in dl:
                use_reasoning = False          # провайдер не понял reasoning
                payload.pop("reasoning", None)
                print("  ! провайдер не принял параметр reasoning (400) — повторяю без него",
                      flush=True)
                continue                       # не тратим попытку
            if e.code == 402:
                if "balance for audio" in dl:
                    raise ReelsiError("OpenRouter пускает АУДИО-запросы только при балансе "
                                     "от $0.50 (даже на free-моделях, анти-абьюз). Пополни "
                                     "баланс на openrouter.ai/credits или выбери Omni «Локально»")
                raise ReelsiError(f"у провайдера кончились кредиты (402): {detail[:150]}")
            if e.code == 429:
                # фри-лимит (обычно 20 req/мин) — клип шлёт десятки чанков подряд;
                # ждём и продолжаем, НЕ тратя попытки и не роняя всю нарезку
                import time
                waits429 += 1
                if waits429 > 5:
                    raise ReelsiError("лимит запросов провайдера (429) не отпускает — "
                                     "фри-модель? Подожди минуту-другую и запусти снова")
                print(f"  ! лимит запросов (429) — жду 20с и продолжаю ({waits429}/5)…", flush=True)
                time.sleep(20)
                continue
            # чистая ASR-модель: провайдер просит /audio/transcriptions вместо /chat —
            # переключаемся на ASR-эндпоинт (qwen3-asr, parakeet и т.п.) и запоминаем модель
            if "transcription model" in dl or "audio/transcriptions" in dl:
                print(f"  → {prof['model']} — ASR-модель, перехожу на /audio/transcriptions",
                      flush=True)
                _ASR_MODELS.add(prof["model"])
                return _asr_transcribe(prof, arr_int16, retries=retries)
            # модель без слуха: OpenRouter отвечает 404 «No endpoints found» или 400 про модальности
            if e.code == 404 or (e.code == 400 and any(
                    k in dl for k in ("audio", "modalit", "multimodal", "no endpoints", "support"))):
                raise ReelsiError(f"модель {prof['model']} не принимает аудио — для Omni нужна "
                                 f"аудио-модель (Gemini / ASR qwen3-asr-flash / parakeet) или «Локально». "
                                 f"Ответ провайдера: {detail[:150]}")
            last = f"{e.code}: {detail}"
        except json.JSONDecodeError as e:
            raw = (getattr(e, "doc", "") or "").strip().replace("\r", " ").replace("\n", " ")
            last = f"не-JSON ответ провайдера: {raw[:120]}" if raw else f"не-JSON ответ провайдера ({e})"
            if prof.get("api_key"):
                last = last.replace(prof["api_key"], "***")
        except (urllib.error.URLError, RuntimeError, KeyError) as e:
            last = str(e)
        attempt += 1
        print(f"  ! чанк не расшифровался ({last}) — повтор {attempt}/{retries}", flush=True)
    raise ReelsiError(f"облачная Omni-транскрипция упала после {retries + 1} попыток: {last}")


def group_chunks(intervals: Sequence[Any], max_len: float = 24.0) -> list[tuple[Any, Any]]:
    chunks, start, last = [], None, None
    for s, e in intervals:
        if start is None:
            start = s
        elif e - start > max_len:
            chunks.append((start, last)); start = s
        last = e
    if start is not None:
        chunks.append((start, last))
    return chunks


def main(args: Sequence[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--intervals", help="json [[s,e],...]: транскрибировать КАЖДЫЙ отдельно "
                                        "(для per-interval решения); иначе группировка по VAD ~24с")
    ap.add_argument("--out")
    ap.add_argument("--engine", default=None, choices=["qwen", "gigaam"],
                   help="принудительно использовать этот локальный движок слуха "
                        "(игнорирует выбранный в UI профиль Omni). 'gigaam' — локальная "
                        "GigaAM-v3-CTC как источник транскрипции (для обычной VAD-нарезки).")
    a = ap.parse_args(args)
    audio, sr = sf.read(a.wav, dtype="int16")
    if audio.ndim > 1:
        audio = audio.mean(1).astype("int16")
    if a.intervals:
        chunks = [(float(s), float(e)) for s, e in json.load(open(a.intervals, encoding="utf-8"))]
    else:
        chunks = group_chunks(vad.speech_intervals(a.wav))
    print(f"audio {len(audio)/SR:.1f}s -> {len(chunks)} кусков", flush=True)
    cloud = aicut.resolve_omni_profile()        # None = локальная Qwen2.5-Omni
    if a.engine == "gigaam":
        cloud = None   # принудительно локальный GigaAM (даже если выбран облачный профиль)
    if cloud and cloud["provider"] == "anthropic":
        raise ReelsiError("Claude API не принимает аудио — для Omni выбери аудио-модель "
                         "(например Gemini на OpenRouter) или «Локально»")
    engine = None
    if cloud:
        print(f"Omni ОБЛАКОМ: профиль «{cloud['name']}» ({cloud['provider']}, "
              f"{cloud['model']}) — аудио-чанки уходят провайдеру", flush=True)
        if "nemotron" in cloud["model"] and "omni" in cloud["model"]:
            print("  ! ВНИМАНИЕ: у nemotron-omni на OpenRouter эндпоинт выбрасывает звук — "
                  "модель отвечает «аудио не предоставлено». Рабочие варианты: "
                  "google/gemini-2.5-flash(-lite), gemini-3-flash, ASR qwen3-asr-flash.", flush=True)
        if cloud["provider"] == "lmstudio":
            aicut.ensure_loaded(cloud["model"], cloud["base_url"])
        proc = model = None
    else:
        engine = a.engine if a.engine else aicut.omni_local_engine()  # 'qwen' | 'gigaam'
        if engine == "gigaam":
            import gigaam
            print("Omni ЛОКАЛЬНО: GigaAM-v3-CTC (Загрузка...)", flush=True)
            model = gigaam.load_model("v3_ctc")
            proc = None  # Для GigaAM процессор из transformers не нужен
        else:
            print("Omni ЛОКАЛЬНО: Qwen2.5-Omni", flush=True)
            proc, model = load_model()
        print("model loaded", flush=True)
    dur = len(audio) / SR
    out = []
    for i, (s, e) in enumerate(chunks):
        c0 = max(0.0, s - 0.12); c1 = min(dur, e + 0.12)   # чуть контекста по краям
        clip = audio[int(c0*SR):int(c1*SR)]
        try:
            if cloud:
                txt = transcribe_clip_cloud(cloud, clip)
            elif engine == "gigaam":
                txt = transcribe_clip_gigaam(model, clip)
            else:
                txt = transcribe_clip(proc, model, clip)
        except ReelsiError: raise
        except Exception as exc:
            if engine == "gigaam":
                sys.stderr.write(f"Ошибка GigaAM инференса на куске {s:.1f}–{e:.1f}: {exc}\n")
                sys.stderr.flush()
                raise SystemExit(1)
            raise
        out.append({"start": round(s, 2), "end": round(e, 2), "text": txt})
        print(f"[{i:2d}] {s:6.1f}-{e:6.1f}  {txt}", flush=True)
    dst = a.out or (os.path.splitext(a.wav)[0] + ".omni.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("-> " + dst, flush=True)


if __name__ == "__main__":
    try:
        main()
    except ReelsiError as e:
        cli_error(e)
