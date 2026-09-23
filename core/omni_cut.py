# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Omni-нарезка (без forced-align): sync -> VAD -> Omni по интервалам -> 27b keep/drop
-> рез по границам VAD -> Premiere XML. Тайминги из VAD (тишина), решение — Omni+27b.

VRAM: Omni крутится в subprocess (torch, 7.5ГБ) и выходит, освобождая память; затем 27b
(LM Studio) решает. Whisper тут не участвует (субтитры пока не строим — оцениваем рез).

    python omni_cut.py --cam1 A.MP4 --cam2 B.MP4 --out cut.xml
"""
import sys, os, re, json, argparse, tempfile, subprocess

# Импорт до первого try: сторож `except ReelsiError` ниже обязан видеть это имя.
from core.umsg import ReelsiError, cli_error

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")   # текст ошибки идёт в stderr — иначе \uXXXX
except ReelsiError: raise
except Exception:
    pass  # поток без reconfigure — русский текст ошибки и так уходит в stderr
from core import arrowfix  # noqa: F401  # предзагрузка pyarrow до torch во избежание краша arrow.dll, не переставлять ниже
from core import sync
from core import vad
from core import align
from core import xmlbuild
from core import aicut
from core import paths
from core.app_meta import child_env, console_emit, module_cmd, wrap_emit
from core.applog import get_logger
from core.fileio import atomic_json_dump
from core.project_file import read_project, write_project


log = get_logger(__name__)


DECIDE_SYS = (
    "Монтаж talking-head. Даны ПОДРЯД интервалы речи (индекс, тайминг, текст). Верни JSON: "
    "drop = индексы на выброс, notes = кратко.\n"
    "Выкидывай:\n"
    "1) интервал, который почти дословно повторяет СОСЕДНИЙ (idx±1..±4). ЖЕЛЕЗНОЕ ПРАВИЛО: "
    "из повторов ВСЕГДА остаётся ПОСЛЕДНИЙ заход (спикер переснимал, потому что ранние — брак); "
    "в drop идут РАННИЕ индексы пары, НИКОГДА не поздний;\n"
    "2) оборванный/брошенный заход без законченной мысли;\n"
    "3) явную не-речь ([смех]/[звуки]/вздох/охи/пусто/покашливание «кхе»/откашливание);\n"
    "4) интервал ЦЕЛИКОМ — БРАК: NG-пересъёмка («стоп, давай заново», «блин забыл», «не то», "
    "ложный старт), бред/галлюцинация ASR (слова не складываются в осмысленную речь по теме), "
    "кусок ПОЛНОСТЬЮ не по теме. Такой интервал выкидывай ЦЕЛИКОМ, ДАЖЕ ЕСЛИ ОН ДЛИННЫЙ.\n"
    "НЕ выкидывай: интервал с уникальным смыслом; интервал только из-за повтора отдельного слова "
    "где-то ещё в ролике; диапазон интервалов между похожими словами; ПЕРЕЧИСЛЕНИЕ — "
    "одинаковое начало с РАЗНЫМ продолжением, связанное союзом («где он сделал вот это, "
    "а где он сделал другое») — это параллельная конструкция, а не дубль. "
    "Сомневаешься, БРАК ЛИ ЭТО (п.2/п.4) — ВЫКИДЫВАЙ. Сомневаешься, ЦЕННО ЛИ — оставь. "
    "Подсказки в строках: «пауза после N.Nс» — длинная пауза часто стоит МЕЖДУ браком и "
    "пересъёмкой (следующий интервал может быть повтором); «внутри повтор фразы» — акустика "
    "слышит повтор внутри интервала (сам интервал НЕ выкидывай из-за этого — внутренний повтор "
    "вырежется отдельно). Если дан блок «ЮЗЕР РАНЕЕ ВЕРНУЛ» — эти фразы юзер восстанавливал "
    "руками после прошлой нарезки: похожие интервалы НЕ выкидывай. "
    "Думай коротко, не переусложняй."
)
SCHEMA = {"type": "object", "properties": {
    "drop": {"type": "array", "items": {"type": "integer"}},
    "notes": {"type": "string"}},
    "required": ["drop", "notes"], "additionalProperties": False}


FILLERS = {"э", "ээ", "эээ", "эм", "эмм", "м", "мм", "ммм", "хм", "кхм", "ах", "ох", "ух",
           "эх", "ха", "хах", "уф", "ф", "фу", "ага", "угу", "а", "и", "ну", "вот", "так",
           "кхе", "кхи", "кх", "хек", "кек", "хмк"}


# Боилерплейт отказа/служебки ASR-модели: на кашле/вздохе Omni вместо транскрипции
# иногда выдаёт «я не могу…» или мета-текст — это НЕ речь из ролика (реальные случаи
# из .omni.json: «Извините, но я не могу продолжать этот разговор», «Извините, я не
# могу продолжать.», «[Здесь нет текста для транскрипции]»).
_ASR_BOILER = re.compile(
    r"извините,?\s*(но\s*)?я не могу|не могу продолжать|не могу выполнить"
    r"|нет возможности (слышать|воспринимать)"
    r"|нет текста для транскрипци|языковая модель|напишите текст", re.I)

# Словарик фраз-галлюцинаций Omni (нормализованные). Сид — реальные шаблоны из
# .omni.json юзера (Omni на вздохах выдаёт одни и те же «реакции» из чат-приоров);
# самообучение: повтор одинакового текста на коротких интервалах внутри клипа
# дописывает фразу в halluc_phrases.json (в .gitignore) — следующий клип ловит её
# уже с ПЕРВОГО появления.
HALLUC_PHRASES_PATH = paths.root("halluc_phrases.json")
HALLUC_SEED = {
    "это что за шум", "это что за шутка", "это не то что я ожидал", "это не важно",
    "я не могу продолжать", "продолжение следует", "субтитры сделал",
}


def _norm_phrase(t):
    return " ".join(re.findall(r"[а-яёa-z]+", (t or "").lower()))


def _load_halluc():
    try:
        return HALLUC_SEED | set(json.load(open(HALLUC_PHRASES_PATH, encoding="utf-8")))
    except ReelsiError: raise
    except Exception:
        return set(HALLUC_SEED)


def _learn_halluc(phrases):
    if not phrases:
        return
    if not os.path.exists(HALLUC_PHRASES_PATH):
        cur = set()                    # файла ещё нет — начинаем с пустого, как раньше
    else:
        # Файл ЕСТЬ, но не читается (обрезан крахом, чужая кодировка, права): начать с
        # пустого и записать = стереть ВСЁ выученное раньше, а файл ниоткуда не
        # восстанавливается (HALLUC_SEED — только сид). Поэтому обучение пропускаем.
        try:
            data = json.load(open(HALLUC_PHRASES_PATH, encoding="utf-8"))
        except ReelsiError: raise
        except Exception as ex:
            log.warning("halluc_phrases.json не прочитан (%s): %s — обучение пропущено",
                        HALLUC_PHRASES_PATH, ex)
            return
        if not isinstance(data, list):
            log.warning("halluc_phrases.json — не список (%s: %s) — обучение пропущено",
                        HALLUC_PHRASES_PATH, type(data).__name__)
            return
        cur = set(data)
    new = (cur | set(phrases)) - HALLUC_SEED
    if new != cur:
        atomic_json_dump(HALLUC_PHRASES_PATH, sorted(new), indent=1)


def voiced_ratio(seg, sr=16000):
    """Доля фреймов с голосовым тоном (пик автокорреляции в диапазоне F0 60–350 Гц).
    Речь 0.4–0.8; чистый кашель/«кхе»/щелчок — ~0.0 (шум без периодичности).
    ВНИМАНИЕ: вздох С ТОНОМ даёт 0.3–0.55 — акустикой от речи не отличим, его ловят
    правила текста (шаблоны/повторы). Порог использования — < 0.12."""
    import numpy as np
    x = seg.astype("float32")
    fl, hp = int(0.04 * sr), int(0.015 * sr)
    lo, hi = int(sr / 350), int(sr / 60)
    if len(x) < fl:
        return 0.0
    voiced = tot = 0
    for s0 in range(0, len(x) - fl, hp):
        fr = x[s0:s0 + fl]
        fr = fr - fr.mean()
        if float(np.sqrt((fr * fr).mean())) < 120:      # тишина — не считаем
            continue
        ac = np.correlate(fr, fr, "full")[fl - 1:]
        tot += 1
        if float(ac[lo:hi].max() / (ac[0] + 1e-9)) > 0.55:
            voiced += 1
    return voiced / tot if tot else 0.0


def halluc_drop(texts, unvoiced=None, emit=console_emit):
    """Пре-фильтр галлюцинаций Omni ДО LLM. Возвращает set индексов на выброс.
    Три слоя (только для коротких интервалов <3.0с — длинные всегда содержат речь):
    1) акустика: интервал без голосового тона (unvoiced из voiced_ratio) = кашель;
    2) повтор: ОДИНАКОВЫЙ текст на ≥2 коротких интервалах = шаблон из чат-приоров
       (реальная речь не повторяется дословно изолированными огрызками) + самообучение;
    3) словарик известных фраз-галлюцинаций (сид + выученные ранее)."""
    dur = lambda t: float(t.get("end", 0)) - float(t.get("start", 0))
    short = {i for i, t in enumerate(texts) if dur(t) < 3.0}
    drops, why = set(), {}
    known = _load_halluc()
    counts = {}
    for i in short:
        n = _norm_phrase(texts[i]["text"])
        if n:
            counts.setdefault(n, []).append(i)
    learned = []
    for n, idxs in counts.items():
        if len(idxs) >= 2 and len(n) >= 8:              # слой 2: повтор шаблона
            drops.update(idxs)
            for i in idxs:
                why[i] = "повтор-шаблон"
            learned.append(n)
        elif n in known:                                # слой 3: словарик
            drops.update(idxs)
            for i in idxs:
                why[i] = "известная галлюцинация"
    for i in (unvoiced or set()) & short:               # слой 1: акустика
        if i not in drops:
            drops.add(i)
            why[i] = "нет голосового тона (кашель/вздох)"
    _learn_halluc(learned)
    for i in sorted(drops):
        emit("  галлюцинация [{idx}] {start:.1f}с ({why}): «{text}»",
             idx=i, start=texts[i]['start'], why=why[i], text=(texts[i]['text'] or '')[:50])
    return drops


def is_nonspeech(text, dur=None):
    """Очевидная не-речь/филлер — убираем БЕЗ LLM: пометки [смех]/[Звуки воды]/[respiration],
    одиночные буквы «А», вырожденные «Ааааа», пустое, короткие вздохи-охи-филлеры,
    галлюцинации Omni на кашле (невозможная плотность букв, боилерплейт отказа)."""
    t = (text or "").strip()
    if not t:
        return True
    if re.fullmatch(r"\[.*?\]|\(.*?\)|<.*?>", t):
        return True
    letters = re.sub(r"[^а-яёa-z]", "", t.lower())
    if len(letters) <= 1:
        return True
    if len(set(letters)) == 1 and len(letters) >= 4:      # «ааааа»
        return True
    words = re.findall(r"[а-яёa-z]+", t.lower())
    # покашливание/откашливание («кхе», «кхи», «хек») ловится через FILLERS ниже —
    # ТОЛЬКО если весь интервал из таких слов. Отдельного правила «"кхе" где-то в
    # тексте = весь интервал не-речь» НЕТ: одно «кхе» посреди длинной живой фразы
    # убивало бы весь интервал с речью.
    # короткий интервал целиком из вздохов/охов/филлеров («Ох. Эх», «Ну вот») — не-речь
    if words and all(w in FILLERS for w in words) and (dur is None or dur < 1.6):
        return True
    # огрызок-вздох / покашливание: <=4 букв — не речь при ЛЮБОЙ длительности:
    # <0.8с — «кхе»/огрызок (закрыт бывший разрыв 0.5-0.8с); >=0.8с — вздох,
    # на который Omni налепила слово-огрызок («Это.» на 1.0с): реальная речь
    # плотнее ~6 букв/с.
    if dur is not None and len(letters) <= 4:
        return True
    # Галлюцинация Omni на кашле/шуме: столько букв в такой интервал физически не влезает
    # (быстрая русская речь ~15 букв/с; «Это что за шум?» на 0.4с = 27 букв/с)
    if dur is not None and dur > 0 and len(letters) / dur > 25:
        return True
    if _ASR_BOILER.search(t):
        return True
    return False


def _defective(t):
    """Явный брак интервала, который разрешено выкидывать ДАЖЕ если он длинный
    (обходит гард allow_long_drop). NG-пересъёмка, галлюцинация Omni, не-речь."""
    txt = t.get("text", "")
    d = float(t.get("end", 0)) - float(t.get("start", 0))
    if is_nonspeech(txt, d):
        return True
    low = (txt or "").lower()
    # ТОЛЬКО однозначные NG-маркеры, по границам слов. Одиночные «заново»/«сначала»/
    # «не то»/«погоди» НЕ берём: это обычные слова живой речи («давайте сначала
    # разберёмся», «не только…») — на них гард длинных интервалов снимать нельзя.
    return bool(re.search(
        r"\b(давай(те)? заново|давай(те)? по новой|блин,? забыл|стоп,? стоп"
        r"|стоп,? давай|снято,? заново|извините, но я не могу)\b", low))


def _rms_env(af, sr=16000, win=0.02, hop=0.01):
    """Короткие RMS-кадры аудио -> (время, rms). Для привязки резов к тишине."""
    import numpy as np
    fl = max(1, int(win * sr)); hl = max(1, int(hop * sr))
    if len(af) < fl:
        return np.array([0.0]), np.array([0.0])
    fr = np.lib.stride_tricks.sliding_window_view(af, fl)[::hl]
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    t = np.arange(len(rms)) * hop
    return t, rms


def _snap_silence(t_arr, rms_arr, t, win=0.4, quiet_frac=0.3):
    """Ближайшая тишина к t (локальный минимум RMS в окне ±win), либо None,
    если в окне нет заметно тихого места (тогда резать посередине слова опасно)."""
    import numpy as np
    lo = int(np.searchsorted(t_arr, t - win))
    hi = int(np.searchsorted(t_arr, t + win))
    if hi <= lo:
        return None
    seg = rms_arr[lo:hi]
    med = float(np.median(seg)) if seg.size else 0.0
    if med <= 0:
        return None
    k = int(lo + int(np.argmin(seg)))
    if rms_arr[k] > quiet_frac * med:
        return None                      # нет настоящей тишины — не режем посередине слова
    return float(t_arr[k])


def _wordset(text):
    return set(re.findall(r"[а-яёa-z]+", (text or "").lower()))


def _pair_dup(ta, tb, thr=0.6):
    """(Почти-)дубль двух текстов: большое пересечение слов ИЛИ один — подмножество другого."""
    a, b = _wordset(ta), _wordset(tb)
    if not a or not b:
        return False
    inter = len(a & b)
    return inter / len(a | b) >= thr or inter >= 0.85 * min(len(a), len(b))


def _tail_retake(ta, tb):
    """tb — короткий ПЕРЕЗАХОД ХВОСТА длинного ta: слова tb почти целиком внутри ta,
    но tb сильно меньше (< 50% слов). Реальный кейс: 8с вступление «Хотите расти в
    зале … это то что вам нужно» + отдельный пере-заход «это то что вам нужно» —
    это НЕ дубль пары равных, длинный терять нельзя."""
    a, b = _wordset(ta), _wordset(tb)
    if not a or not b or len(b) >= 0.5 * len(a):
        return False
    return len(a & b) >= 0.85 * len(b)


def _dup_of_neighbor(texts, i, span=4, thr=0.6):
    """True if interval i is (near-)duplicate of an interval within ±span. Guards against
    dropping unique content. Короткий перезаход хвоста соседа дублем НЕ считается —
    иначе длинное вступление гибнет из-за пересъёма 5-словного хвоста."""
    if not _wordset(texts[i]["text"]):
        return True
    for j in range(max(0, i - span), min(len(texts), i + span + 1)):
        if j == i or not _pair_dup(texts[i]["text"], texts[j]["text"], thr):
            continue
        if _tail_retake(texts[i]["text"], texts[j]["text"]):
            continue                       # сосед — лишь перезаход НАШЕГО хвоста
        return True
    return False


def _tail_cut_by_words(wav_path, iv, tail_text, emit=console_emit):
    """Где в КОНЦЕ длинного интервала начинается старый хвост (тот же текст, что и
    перезаход)? Пословные тайминги Whisper по хвостовому окну; матчим ПОСЛЕДНЕЕ
    вхождение первых 3 слов перезахода. Возвращает (t0, t1) на вырез или None
    (тогда фолбэк — дроп перезахода). Режем по границе слова с отступом 60мс."""
    from core import transcribe
    tail_words = re.findall(r"[а-яёa-z]+", (tail_text or "").lower())
    if len(tail_words) < 3:
        return None
    s, e = float(iv[0]), float(iv[1])
    win0 = max(s, e - (0.6 * len(tail_words) + 4.0))     # хвост + запас
    words = transcribe.transcribe_segments(wav_path, intervals=[(win0, e)])
    ws = [re.sub(r"[^а-яёa-z]", "", (w["w"] or "").lower()) for w in words]
    key = tail_words[:3]
    hit = None
    for k in range(len(ws) - len(key) + 1):
        if ws[k:k + len(key)] == key:
            hit = k                                       # последнее вхождение
    if hit is None:
        emit("  срез хвоста: Whisper не нашёл «{key}» в конце интервала", key=" ".join(key))
        return None
    t0 = max(s, float(words[hit]["start"]) - 0.06)
    if t0 <= s + 0.5 or e - t0 < 0.2:                     # матч в самом начале/у края — не верим
        return None
    return (t0, e)


def _joint_snippet(a16, keep, t, span=4.0, sr=16000):
    """Склейка звука вокруг стыка t ТОЧНО как в финальном черновике: последние span
    секунд keep-кусков до t + первые span секунд после. Для речека склейки."""
    import numpy as np
    left = [(s, e) for s, e in keep if e <= t + 1e-3]
    right = [(s, e) for s, e in keep if e > t + 1e-3]
    parts, need = [], span
    for s, e in reversed(left):
        take = min(need, e - s)
        parts.insert(0, a16[int((e - take) * sr):int(e * sr)])
        need -= take
        if need <= 0:
            break
    need = span
    for s, e in right:
        s2 = max(s, t)                                    # правая часть куска после реза
        take = min(need, e - s2)
        if take > 0:
            parts.append(a16[int(s2 * sr):int((s2 + take) * sr)])
            need -= take
        if need <= 0:
            break
    return np.concatenate(parts) if parts else None


def _count_key(norm_text, key_words):
    """Сколько раз в нормализованном тексте встречается связка первых слов хвоста."""
    key = " ".join(key_words)
    cnt, start = 0, 0
    while key:
        k = norm_text.find(key, start)
        if k < 0:
            break
        cnt += 1
        start = k + len(key)
    return cnt


def _splice_recheck_omni(a16, keep, tail_list, texts, work, emit=console_emit):
    """РЕЧЕК СКЛЕЕК СЛУХОМ OMNI: склейки стыков (тот же звук, что уйдёт в черновик)
    пишутся в один wav с паузами-разделителями, omni_asr слушает ДОСЛОВНО (повторы
    не причёсывает — в отличие от Whisper, поэтому проверяет именно он). Фраза-хвост
    должна прозвучать ровно один раз; дважды = старый хвост уцелел (тайминги Whisper
    соврали) -> склейку откатываем. Возвращает список проваленных (rng, i, j)."""
    import numpy as np, soundfile as sf
    spans, buf, pos = [], [], 0.0
    gap = np.zeros(int(0.6 * 16000), dtype="int16")
    for rng, i, j in tail_list:
        snip = _joint_snippet(a16, keep, rng[0])
        if snip is None or len(snip) < 16000:
            spans.append(None)
            continue
        buf.append(snip)
        spans.append((pos, pos + len(snip) / 16000.0))
        pos += len(snip) / 16000.0 + 0.6
        buf.append(gap)
    if not buf:
        return []
    wavp = os.path.join(work, "_splice_check.wav")
    sf.write(wavp, np.concatenate(buf), 16000, subtype="PCM_16")
    ivp = os.path.join(work, "_splice_iv.json")
    json.dump([list(s) for s in spans if s], open(ivp, "w"))
    outp = os.path.join(work, "_splice_omni.json")
    r = subprocess.run(module_cmd("omni_asr", wavp,
                                  "--intervals", ivp, "--out", outp),
                       capture_output=True, text=True, encoding="utf-8", timeout=1800,
                       env=child_env())
    if not os.path.exists(outp):
        raise RuntimeError("omni_asr не отработал: " + (r.stderr or r.stdout or "")[-300:])
    res = json.load(open(outp, encoding="utf-8"))
    bad, ri = [], 0
    for (rng, i, j), sp in zip(tail_list, spans):
        if sp is None:
            continue
        heard = _norm_phrase(res[ri]["text"] if ri < len(res) else "")
        ri += 1
        key = re.findall(r"[а-яёa-z]+", (texts[j]["text"] or "").lower())[:3]
        cnt = _count_key(heard, key)
        if cnt >= 2:
            emit("  речек (Omni) склейки [{i}]→[{j}]: фраза звучит {cnt} раза — старый "
                 "хвост уцелел, откатываю на фолбэк", i=i, j=j, cnt=cnt)
            bad.append((rng, i, j))
        elif cnt:
            emit("  речек (Omni) склейки [{i}]→[{j}]: ok (фраза один раз)", i=i, j=j)
        else:
            emit("  речек (Omni) склейки [{i}]→[{j}]: ok (фраза один раз) — Omni фразу не расслышал, оставляю как есть", i=i, j=j)
    return bad


def _splice_recheck(a16, keep, tail_list, texts, work, emit=console_emit):
    """Фолбэк-речек Whisper'ом (если omni_asr упал): та же проверка, но Whisper
    склонен «причёсывать» повторы — может пропустить уцелевший хвост."""
    import soundfile as sf
    from core import transcribe
    bad = []
    for rng, i, j in tail_list:
        snip = _joint_snippet(a16, keep, rng[0])
        if snip is None or len(snip) < 16000:
            continue
        tmpw = os.path.join(work, f"_splice{i}.wav")
        sf.write(tmpw, snip, 16000, subtype="PCM_16")
        words = transcribe.transcribe(tmpw)
        ws = [re.sub(r"[^а-яёa-z]", "", (w["w"] or "").lower()) for w in words]
        key = re.findall(r"[а-яёa-z]+", (texts[j]["text"] or "").lower())[:3]
        cnt, k = 0, 0
        while k <= len(ws) - len(key):
            if ws[k:k + len(key)] == key:
                cnt += 1
                k += len(key)
            else:
                k += 1
        if cnt >= 2:
            emit("  речек склейки [{i}]→[{j}]: фраза звучит {cnt} раза — старый хвост "
                 "уцелел, откатываю на фолбэк", i=i, j=j, cnt=cnt)
            bad.append((rng, i, j))
        elif cnt:
            emit("  речек склейки [{i}]→[{j}]: ok (фраза один раз)", i=i, j=j)
        else:
            emit("  речек склейки [{i}]→[{j}]: ok (фраза один раз) — Whisper фразу не расслышал, оставляю как есть", i=i, j=j)
    return bad


def _wav_duration(path):
    import soundfile as sf
    return float(sf.info(path).duration)


def _frange(start, stop, step):
    x = start
    while x < stop - 0.5:
        yield x
        x += step


def _full_pass(wav_path, a, work, emit=console_emit):
    """«Анализ фулом»: слушаем ролик СПЛОШНЫМИ окнами, тишину НЕ вырезаем.

    Зачем отдельно от нарезки: замерено на _c1295 — в поинтервальном режиме кусок
    46-66с превращается в кашу («и то как и то как на него реагирует ваша сало»),
    а на целом ролике тот же фрагмент понят верно («кожное сало становится густым,
    поры забиваются»). Чанкование само по себе портит понимание.

    Результат — `<out>.full.json`: [{start,end,text}] по окнам. Это КОНТЕКСТ для
    решающей LLM, а не разметка реза: тайминги аудио-LLM врут на секунды,
    поэтому границы реза по-прежнему берутся из VAD (тишина = точная линейка)."""
    dst = os.path.splitext(a.out)[0] + ".full.json"
    if os.path.exists(dst):
        emit("  full-анализ из кэша: {path}", path=dst)
        return json.load(open(dst, encoding="utf-8"))
    dur = _wav_duration(wav_path)
    step = max(30.0, float(a.full_window))
    wins = [(s, min(s + step, dur)) for s in _frange(0.0, dur, step)]
    wins = [(s, e) for s, e in wins if e - s > 1.0]
    emit("  full-анализ: {count} окон по {step:.0f}с (тишина не вырезается)",
         count=len(wins), step=step)
    # iv.json кладём в РАБОЧИЙ каталог нарезки (omnicut_*), а не в отдельный
    # fullpass_*: у того не было чистки ни в finally, ни при «Стоп» — в %TEMP%
    # скапливался мусор после каждой отмены.
    ivf = os.path.join(work, "iv.json")
    json.dump([[s, e] for s, e in wins], open(ivf, "w"))
    proc = subprocess.Popen(
        module_cmd("omni_asr", wav_path, "--intervals", ivf, "--out", dst, unbuffered=True),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=child_env())
    for line in proc.stdout:
        if line.strip():
            emit("  {line}", line=line.rstrip())
    proc.wait()
    if not os.path.exists(dst):
        emit("  full-анализ не удался — продолжаю без него")
        return None
    return json.load(open(dst, encoding="utf-8"))


def _free_vram_for_render(emit=None):
    """Освободить VRAM перед NVENC-рендером черновика.

    `aicut.unload_ours()` выгружает наши модели LM Studio, а если в VRAM висит ещё модель,
    h264_nvenc не находит памяти и draftrender молча уезжает на libx264 (CPU) —
    «черновик рендерит на процессоре».
    Whisper к этому моменту уже выгружен self-check'ом; на всякий случай дожимаем."""
    emit = emit or console_emit
    aicut.unload_ours(emit=emit)
    try:
        from core import transcribe as _tr
        if _tr.release_model():
            emit("  Whisper выгружен")
    except ReelsiError: raise
    except Exception as ex:
        log.warning("Whisper не выгрузился перед рендером: %s — "
                    "видеопамять может остаться занятой", ex)


def _load_omni_cache(omf, intervals):
    """Кэш Omni-транскрипта (<out>.omni.json) -> список или None, если кэш не пригоден.

    Пригоден только СОВПАДАЮЩИЙ с этим роликом кэш: та же длина И те же интервалы.
    Одного числа мало — чужой .omni.json с тем же числом интервалов молча подставил
    бы чужой транскрипт; битый JSON (крах при записи) после VAD просто перегенерим.
    """
    try:
        with open(omf, encoding="utf-8") as f:
            cand = json.load(f)
    except ReelsiError: raise
    except Exception:
        return None
    if not isinstance(cand, list) or len(cand) != len(intervals):
        return None
    for t, (s, e) in zip(cand, intervals):
        if not isinstance(t, dict) or not isinstance(t.get("text"), str):
            return None
        try:                                        # нечисловой start/end — тоже битый кэш
            ok = (abs(float(t.get("start", s)) - s) < 0.5
                  and abs(float(t.get("end", e)) - e) < 0.5)
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return None
    return cand


def _guard_keep(keep, intervals):
    """Санитарный гард доли речи: меньше 25% (или пусто) — отказ от перезаписи."""
    kept_s = sum(e - s for s, e in keep)
    src_s = sum(e - s for s, e in intervals)
    if not keep or (src_s > 0 and kept_s < 0.25 * src_s):
        raise ReelsiError(
            f"ИИ вырезал почти весь ролик: осталось {kept_s:.1f}с из {src_s:.1f}с "
            f"({len(keep)} сег.). Ничего не перезаписываю — прошлая нарезка цела. "
            f"Проверь модель и промпт в настройках ⚙ и запусти ещё раз.")


def decide(texts, emit=console_emit, model=None, ssm_flags=None, overrides=None, halluc=None,

           full_map=None, allow_long_drop=False):
    """ssm_flags: {idx: True} — внутри интервала акустика слышит повтор фразы.
    overrides: user_overrides из .project.json прошлого прогона — юзер руками вернул
    вырезанное («не выкидывай похожее»). halluc: set индексов от halluc_drop()
    (галлюцинации Omni на кашлях) — в auto_drop, LLM их не видит.
    full_map: `<out>.full.json` — разбор ролика ЦЕЛИКОМ (--full-audio). Даётся как
    КОНТЕКСТ («о чём ролик по порядку»), чтобы модель не выкидывала уникальную мысль,
    видя только соседние интервалы. Тайминги там ±1-5с — на них НЕ резать."""
    # содержательные интервалы (с исходными индексами) — только их отдаём LLM (меньше reasoning)
    dur = lambda t: float(t.get("end", 0)) - float(t.get("start", 0))
    halluc = halluc or set()
    content = [(i, t) for i, t in enumerate(texts)
               if i not in halluc and not is_nonspeech(t["text"], dur(t))]
    auto_drop = halluc | {i for i, t in enumerate(texts) if is_nonspeech(t["text"], dur(t))}

    def _line(i, t):
        hints = []
        gap = (float(texts[i + 1]["start"]) - float(t["end"])) if i + 1 < len(texts) else None
        if gap is not None and gap >= 0.8:               # значимая пауза = сигнал стыка дублей
            hints.append(f"пауза после {gap:.1f}с")
        if ssm_flags and ssm_flags.get(i):
            hints.append("внутри повтор фразы")
        h = f"  ({'; '.join(hints)})" if hints else ""
        return f"[{i}] {t['start']:.1f}-{t['end']:.1f}с{h}: {t['text']}"

    lines = "\n".join(_line(i, t) for i, t in content)
    user = ""
    if full_map:
        # Целый ролик модель понимает лучше, чем нарезанный на интервалы, — даём этот
        # разбор ПЕРЕД интервалами как канву смысла (не как разметку реза).
        canvas = "\n".join(f"{w['start']:.0f}-{w['end']:.0f}с: {w['text']}"
                           for w in full_map if (w.get("text") or "").strip())
        if canvas:
            user += ("О ЧЁМ РОЛИК ЦЕЛИКОМ (канва смысла; тайминги здесь ПРИБЛИЗИТЕЛЬНЫЕ, "
                     "резать по ним НЕЛЬЗЯ — режем только по интервалам ниже):\n"
                     + canvas + "\n\n")
    user += "Интервалы речи:\n" + lines
    restored = [o for o in ((overrides or {}).get("restored") or []) if (o.get("text") or "").strip()]
    if restored:
        user += "\n\nЮЗЕР РАНЕЕ ВЕРНУЛ (не выкидывай похожие):\n" + "\n".join(
            f"- «{(o['text'] or '')[:80]}»" for o in restored[:12])
    _lvl = aicut.step_reasoning("cut")           # уровень «ума» шага «Нарезка» из UI
    data = aicut._ask_json(DECIDE_SYS, user, SCHEMA, model=model,   # None -> активный профиль
                           max_tokens=aicut.reason_budget(12000, _lvl), emit=emit,
                           temperature=0.2, reasoning=_lvl,
                           profile=aicut.step_profile("cut"), step="cut")   # модель шага
    idxset = {i for i, _ in content}
    # Ответ модели — недоверенный: `{"drop": ["a"]}` или `[{}]` роняли джоб трейсбеком уже
    # ПОСЛЕ оплаченного вызова, поэтому разбираем через as_ints, а не голым int().
    llm_drop = set(aicut.as_ints(data.get("drop"), lo=0)) & idxset
    # Санитарный гард доли речи: проверяем решение модели ДО возврата длинных
    # интервалов (иначе вето длинных интервалов возвращает вырезанное и маскирует сбой).
    src_intervals = [(float(t["start"]), float(t["end"])) for t in texts]
    model_keep = [src_intervals[i] for i in range(len(texts)) if i not in (llm_drop | auto_drop)]
    _guard_keep(model_keep, src_intervals)
    # Защита от «схлопывания куска таймлайна»: длинный содержательный интервал НЕ выкидываем,
    # если он не дубль соседа (±2). Короткие (<2.5с, брошенные заходы) LLM резать разрешаем.
    # Для GigaAM (allow_long_drop=True) эту защиту ОТКЛЮЧАЕМ — там фразы режутся по паузам
    # на sentence-уровень и 27b с полным контекстом сам решает, что лишнее. Qwen
    # (умолчание False) НЕ трогаем — КРОМЕ явного брака: долгий NG-дубль / галлюцинация /
    # не-речь всё равно выкидываем (иначе 100% брак остаётся в ролике).
    vetoed = []
    if not allow_long_drop:
        for i in list(llm_drop):
            long = (texts[i]["end"] - texts[i]["start"]) >= 2.5
            if long and not _dup_of_neighbor(texts, i) and not _defective(texts[i]):
                llm_drop.discard(i)
                vetoed.append(i)
        if vetoed:
            emit("  guard: вернул {count} длинных интервал(ов) — не дубли соседей: {vetoed}",
                 count=len(vetoed), vetoed=vetoed)
    # перезаход хвоста: длинный [i] + следом короткий [j] с теми же словами хвоста.
    # НЕ дропаем здесь — возвращаем пары наверх: main попробует срезать СТАРЫЙ хвост
    # внутри [i] по пословным таймингам Whisper (идеал: начало [i] + новый хвост [j]);
    # не выйдет — фолбэк там же (дроп [j], [i] целиком)
    tail_pairs = []
    dropped = llm_drop | auto_drop
    for i in range(len(texts)):
        if i in dropped:
            continue
        for j in range(i + 1, min(len(texts), i + 3)):
            if j not in dropped and _tail_retake(texts[i]["text"], texts[j]["text"]):
                tail_pairs.append((i, j))
                emit("  хвост-перезаход: [{i}]…[{j}] «{text}» — "
                     "попробуем срезать старый хвост и слепить с новым",
                     i=i, j=j, text=(texts[j]['text'] or '')[:40])
    # keep-last: если LLM выкинул ПОЗДНИЙ из пары почти-дублей и оставил ранний — меняем местами
    # (при переснятии верный заход обычно ПОСЛЕДНИЙ; юзер: «оставлен первый, хотя верный последний»)
    # перезаходы хвоста не трогаем — там «ранний» это длинный интервал с контентом
    swapped = []
    for j in sorted(llm_drop):
        for i in range(max(0, j - 4), j):
            if i in llm_drop or i in auto_drop:
                continue
            if _pair_dup(texts[i]["text"], texts[j]["text"]) \
                    and not _tail_retake(texts[i]["text"], texts[j]["text"]) \
                    and not _tail_retake(texts[j]["text"], texts[i]["text"]):
                llm_drop.discard(j)
                llm_drop.add(i)
                swapped.append(f"{j}→{i}")
                break
    if swapped:
        emit("  keep-last: у повторов оставляю поздний заход, в drop ранний: {swapped}",
             swapped=", ".join(swapped))
    return auto_drop, llm_drop, data.get("notes", ""), tail_pairs


def main(work):
    """Собственно нарезка. `work` — рабочий каталог (создаёт обёртка в
    `__main__`, он же чистит в finally; при «Стоп» его удаляет сервер в
    `_kill_curproc` по маркеру WORK_DIR= из stdout)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam1")
    ap.add_argument("--cam2")
    ap.add_argument("--cam", action="append", default=[],
                    help="камера (можно повторять для 3-4 камер; аудио ВСЕГДА с первой)")
    ap.add_argument("--model", default=None, help="модель LM Studio для keep/drop (деф. 27b)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=50.4)
    ap.add_argument("--vad_thresh", type=float, default=18.0)
    ap.add_argument("--min_silence", type=float, default=0.30)
    ap.add_argument("--pad", type=float, default=0.08)
    ap.add_argument("--cam_return", type=int, default=2)
    ap.add_argument("--ssm", action="store_true", help="акустический рез внутри-фразовых повторов (SSM+энергия)")
    ap.add_argument("--refine", action="store_true", help="forced-align внутри интервалов (экспер., режет окончания)")
    ap.add_argument("--no-selfcheck", action="store_true",
                    help="без самопроверки стыков (Whisper по склейке keep-аудио)")
    ap.add_argument("--no-draft", action="store_true", help="без чернового .draft.mp4")
    ap.add_argument("--full-audio", action="store_true",
                    help="слушать ролик СПЛОШНЫМИ окнами, не по VAD (тишина не вырезается). "
                         "аудио-модель понимает целое лучше, чем чанки; резать всё равно по VAD")
    ap.add_argument("--full-window", type=float, default=120.0,
                    help="длина окна в full-режиме, сек (деф. 120)")
    ap.add_argument("--omni-review", action="store_true",
                    help="ЭКСПЕРИМЕНТ: Omni смотрит черновик и пишет замечания (.review.json); "
                         "минуты + 7.5 ГБ VRAM свопом — по умолчанию ВЫКЛ")
    ap.add_argument("--mode", dest="mode", choices=["old", "gigaam"], default="gigaam",
                    help="режим нарезки (флоу): «gigaam» — основной (цельный файл "
                         "GigaAM + LLM решает рез), «old» — прежний (Qwen/VAD).")
    ap.add_argument("--selfcheck-model", "--selfcheck-engine", dest="selfcheck_model",
                    default="whisper:large-v3",
                    help="движок самопроверки стыков: whisper:large-v3|medium|small, "
                         "gigaam, ctc:<язык> из asr_engines.json (старые значения "
                         "«large-v3»/«medium»/«small» тоже понимаются)")
    ap.add_argument("--speaker", default=None,
                    help="профиль спикера (speakers/*.json): пороги нарезки под "
                         "его студию и говор; работает в режиме gigaam")
    ap.add_argument("--dedupe", dest="dedupe", action=argparse.BooleanOptionalAction,
                    default=None,
                    help="чистка дублей кодом. Явный флаг (пришёл с галки "
                         "шага 1) перекрывает профиль спикера; без флага — профиль "
                         "либо дефолт False")
    ap.add_argument("--no-sense", action="store_true",
                    help="без ИИ-разметки смысловых кусков")
    ap.add_argument("--no-refine", action="store_true",
                    help="без подгона резов по звуку")
    ap.add_argument("--no-breath", action="store_true",
                    help="без вырезания вздохов")
    ap.add_argument("--no-pauses", action="store_true",
                    help="без вырезания пауз")
    a = ap.parse_args()
    cams = [c for c in ([a.cam1, a.cam2] + list(a.cam)) if c]
    if not cams:
        ap.error("нужна хотя бы одна камера (--cam1 или --cam)")
    N = len(cams)
    wavs = [os.path.join(work, f"a{k}.wav") for k in range(N)]
    print("извлекаю аудио...", flush=True)
    sync.extract_audio(cams[0], wavs[0])
    offsets = [0.0]
    for k in range(1, N):
        sync.extract_audio(cams[k], wavs[k])
        off, conf = sync.find_offset(wavs[0], wavs[k])
        offsets.append(off)
        print(f"синк К{k+1}: {off:+.3f}s (увер. {conf:.2f})", flush=True)

    # === GigaAM whole-file путь (отдельный пакет gigaam_cut/) ===
    # Qwen идёт НИЖЕ по старому VAD-пути (не трогаем).
    # Путь определяется флагом --mode (дефолт gigaam), движок нарезки резолвится из active_cut_asr.
    gigaam_path = (a.mode == "gigaam")
    if a.speaker and not gigaam_path:
        # Пороги профиля живут в gigaam_cut.tune; старый VAD-путь про них не знает.
        print(f"! Спикер «{a.speaker}» задан, но нарезка идёт старым VAD-путём — "
              "его пороги НЕ применятся (папка и стиль работают как обычно).",
              flush=True)
    if gigaam_path:
        from core.gigaam_cut import run as _gc_run
        stages = {}
        # Явный draft: в CLI без флага --no-draft черновик включён; при --omni-review черновик обязателен (как на сервере)
        stages["draft"] = not a.no_draft or bool(a.omni_review)
        if a.dedupe is not None:
            stages["dedupe"] = a.dedupe
        if a.no_sense:
            stages["sense"] = False
        if a.no_refine:
            stages["refine"] = False
        if a.no_breath:
            stages["breath"] = False
        if a.no_pauses:
            stages["pauses"] = "off"
        keep, cutlog, draft_path, info = _gc_run(
            wavs[0], cams, offsets, a.out, a.scale, model=a.model,
            cam_return=a.cam_return, speaker=a.speaker, stages=stages,
            emit=lambda *a, **k: print(*a, flush=True))
        assign = (align.assign_cameras(keep, N, return_every=a.cam_return, big_chunk_sec=6.0)
                  if N > 1 else None)
        # project.json (сайдкар редактора нарезки). speaker кладём сюда, чтобы шаг
        # AE подхватил его стиль, не спрашивая заново.
        proj = {"cams": cams, "offsets": offsets, "fps": 60, "cam_return": a.cam_return,
                "scale": a.scale, "keep": [[round(s, 3), round(e, 3)] for s, e in keep]}
        if a.speaker:
            proj["speaker"] = a.speaker
        write_project(os.path.splitext(a.out)[0] + ".project.json", proj)
        # cut-log: что именно и почему убрано
        cutlog.sort(key=lambda c: c["t0"])
        logf = os.path.splitext(a.out)[0] + ".cuts.json"
        atomic_json_dump(logf, cutlog, indent=1)
        print(f"\n=== ЧТО УБРАНО ({len(cutlog)}) — решения ИИ ===", flush=True)
        for c in cutlog:
            print(f"  {c['t0']:6.1f}-{c['t1']:6.1f}  [{c['source']}]  «{c['text'][:70]}»  — {c['reason'][:70]}", flush=True)
        print(f"\n-> {a.out}  ({info.get('total_s', 0):.0f}s, {info.get('segments')} сег., {N} кам.)  + {os.path.basename(logf)}", flush=True)
        return

    intervals = vad.speech_intervals(wavs[0], thresh_db=a.vad_thresh,
                                     min_silence=a.min_silence, pad=a.pad)
    print(f"VAD: {len(intervals)} интервалов речи", flush=True)
    # ВАЖНО: intervals (VAD) остаются основой РЕЗА — из них ниже собирается keep.
    # full-режим добавляет ОТДЕЛЬНЫЙ слой понимания, а не подменяет нарезку.
    full_map = None
    if a.full_audio:
        # wrap_emit: _full_pass зовёт emit шаблоном (`emit("…{path}", path=dst)`), а
        # голая лямбда `lambda *x: print(*x)` именованных аргументов не принимает —
        # TypeError ронял весь прогон с --full-audio (GZ, п. B).
        full_map = _full_pass(wavs[0], a, work,
                              emit=wrap_emit(lambda m: print(m, flush=True)))

    omf = os.path.splitext(a.out)[0] + ".omni.json"     # кэш рядом с выходом (не гонять Omni повторно)
    texts = _load_omni_cache(omf, intervals)
    if texts is not None:
        print(f"Omni-транскрипт из кэша: {len(texts)}", flush=True)
    if texts is None:
        ivf = os.path.join(work, "iv.json")
        json.dump([[s, e] for s, e in intervals], open(ivf, "w"))
        _omni_cloud = aicut.resolve_omni_profile()      # None = локальная Omni
        if _omni_cloud is None:
            aicut.unload_ours()                         # освободить VRAM под локальную Omni
            aicut.warn_foreign_models()
            print("Omni транскрибирует по интервалам (subprocess, ~5-8 мин)...", flush=True)
        else:
            print(f"Omni облаком: «{_omni_cloud['name']}» ({_omni_cloud['model']}) — "
                  f"VRAM не трогаем", flush=True)
        # СТРИМИМ вывод omni_asr построчно (не capture_output) — прогресс скачивания
        # весов/загрузки идёт в наш stdout сразу, а api/jobs.py гонит его в webui-лог живьём
        omni_asr_cmd = module_cmd("omni_asr", wavs[0], "--intervals", ivf, "--out", omf,
                                  unbuffered=True)
        if aicut.omni_local_engine() == "gigaam":
            omni_asr_cmd += ["--engine", "gigaam"]
        proc = subprocess.Popen(
            omni_asr_cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=child_env())
        errtail = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print("  " + line, flush=True)
                errtail.append(line)
                del errtail[:-8]
        proc.wait()
        if not os.path.exists(omf):
            print("Omni FAIL:\n" + "\n".join(errtail)[-800:]); sys.exit(1)
        texts = json.load(open(omf, encoding="utf-8"))
    print(f"Omni дал {len(texts)} расшифровок", flush=True)

    # SSM-преданализ ДО LLM: акустический детект повторов внутри КАЖДОГО интервала.
    # Двойная польза: подсказка LLM («внутри повтор фразы») + готовые резы для kept
    # (второй раз не считаем). Аудио грузим один раз здесь.
    af, ssm_pre = None, {}
    import soundfile as sf, numpy as np
    a16, _ = sf.read(wavs[0], dtype="int16")
    if a16.ndim > 1:
        a16 = a16.mean(1).astype("int16")
    if a.ssm or a.refine:
        af = a16.astype(np.float32) / 32768.0

    # Акустический детект кашлей: короткие интервалы без голосового тона -> в halluc_drop
    # (Omni на них галлюцинирует «речь», текстовым правилам не всегда видно)
    unvoiced = set()
    for i, (s, e) in enumerate(intervals):
        if (e - s) < 3.0 and voiced_ratio(a16[int(s * 16000):int(e * 16000)]) < 0.12:
            unvoiced.add(i)
    halluc = halluc_drop(texts, unvoiced=unvoiced)
    if halluc:
        print(f"фильтр галлюцинаций: -{len(halluc)} интервалов ДО LLM", flush=True)

    if a.ssm:
        from core import ssm as ssmmod
        for i, (s, e) in enumerate(intervals):
            try:
                ssm_pre[i] = ssmmod.repeat_cut_ranges(af[int(s*16000):int(e*16000)],
                                                      text=texts[i]["text"], off=s)
            except ReelsiError: raise
            except Exception:
                ssm_pre[i] = []

    # память правок: юзер после ПРОШЛОЙ нарезки возвращал вырезанное в редакторе —
    # передаём LLM («не выкидывай похожее») + жёстко защищаем эти куски после решения
    prev_overrides = None
    pj = os.path.splitext(a.out)[0] + ".project.json"
    if os.path.exists(pj):
        try:
            _prev = read_project(pj) or {}
            prev_overrides = _prev.get("user_overrides")
        except ReelsiError: raise
        except Exception:
            prev_overrides = None

    _prof = aicut.resolve_profile(a.model)
    print(f"LLM решает keep/drop: профиль «{_prof['name']}» ({_prof['provider']}, "
          f"{_prof['model']})...", flush=True)
    auto_drop, llm_drop, notes, tail_pairs = decide(
        texts, model=a.model, ssm_flags={i: bool(r) for i, r in ssm_pre.items()},
        overrides=prev_overrides, halluc=halluc, full_map=full_map)
    if prev_overrides:
        prot = []
        for o in (prev_overrides.get("restored") or []):
            try:
                prot.append((float(o["t0"]), float(o["t1"])))
            except ReelsiError: raise
            except Exception as ex:
                log.warning("защищённый кусок %r из .project.json не разобран: "
                            "%s — под защиту он не попадёт", o, ex)
        saved = [i for i in sorted(llm_drop)
                 if any(intervals[i][0] < b0 and intervals[i][1] > a0 for a0, b0 in prot)]
        for i in saved:
            llm_drop.discard(i)
        if saved:
            print(f"  память правок: защитил интервалы {saved} — юзер возвращал этот кусок руками", flush=True)
    # перезаход хвоста: идеал — начало длинного [i] + новый хвост [j]; старый хвост
    # внутри [i] режем по пословным таймингам Whisper, затем РЕЧЕК: слушаем реальную
    # склейку стыка (тот же звук, что уйдёт в черновик) — фраза должна прозвучать
    # ровно один раз, иначе откат. Не нашлось точки / речек провален — фолбэк:
    # перезаход [j] в drop (фраза не должна прозвучать дважды), [i] целиком.
    tail_cuts, tail_info = [], []
    if tail_pairs:
        aicut.unload_ours()                              # VRAM под Whisper (для облака no-op)
        aicut.warn_foreign_models()
    for i, j in tail_pairs:
        rng = None
        try:
            rng = _tail_cut_by_words(wavs[0], intervals[i], texts[j]["text"])
        except ReelsiError: raise
        except Exception as ex:
            print(f"  срез хвоста [{i}] не вышел ({type(ex).__name__}: {ex})", flush=True)
        if rng:
            tail_cuts.append(rng)
            tail_info.append((rng, i, j))
            print(f"  перезаход хвоста: старый хвост [{i}] {rng[0]:.2f}-{rng[1]:.2f}с срезан, "
                  f"слеплено с новым заходом [{j}]", flush=True)
        else:
            llm_drop.add(j)
            print(f"  перезаход хвоста: точку среза не нашли — [{j}] в drop, [{i}] целиком", flush=True)
    if tail_pairs:
        try:
            from core import transcribe
            transcribe.release_model()                   # VRAM вернуть (дальше — Omni)
        except ReelsiError: raise
        except Exception as ex:
            log.warning("Whisper не выгрузился перед Omni-проходом: %s — "
                        "видеопамять остаётся занятой", ex)
    if tail_info:
        # речек склеек по факту звука. Слушает OMNI (дословный слух, повторы не
        # причёсывает — Whisper'у считать повторы нельзя, он их склеивает);
        # Whisper-речек — только фолбэк, если omni_asr упал.
        # wrap_emit: и Omni-речек, и Whisper-фолбэк зовут emit с i/j/cnt, а голая
        # лямбда `lambda m: print(m)` падала TypeError — исключение глоталось, и
        # проверка склеек в --mode old не работала никогда (GZ, п. B).
        _emit = wrap_emit(lambda m: print(m, flush=True))
        keep_pre = align.subtract_ranges(
            [intervals[k] for k in range(len(intervals))
             if k not in (auto_drop | llm_drop)], tail_cuts)
        bad = None
        try:
            print("  речек склеек слухом Omni…", flush=True)
            bad = _splice_recheck_omni(a16, keep_pre, tail_info, texts, work, emit=_emit)
        except ReelsiError: raise
        except Exception as ex:
            print(f"  Omni-речек не удался ({type(ex).__name__}: {ex}) — фолбэк на Whisper",
                  flush=True)
            try:
                bad = _splice_recheck(a16, keep_pre, tail_info, texts, work, emit=_emit)
                from core import transcribe
                transcribe.release_model()
            except ReelsiError: raise
            except Exception as ex2:
                print(f"  речек склеек не удался ({type(ex2).__name__}: {ex2}) — "
                      f"оставляю срезы как есть", flush=True)
        for rng, i, j in (bad or []):
            tail_cuts.remove(rng)
            llm_drop.add(j)
            print(f"  откат склейки: [{j}] в drop, [{i}] целиком", flush=True)

    drop = auto_drop | llm_drop
    cutlog = []                                          # что убрано: {t0,t1,text,reason,source}
    for t0, t1 in tail_cuts:
        cutlog.append({"t0": round(t0, 2), "t1": round(t1, 2), "text": "(старый хвост)",
                       "reason": "хвост переснят отдельно — оставлен новый заход",
                       "source": "перезаход"})
    for i in sorted(drop):
        src = "не-речь" if i in auto_drop else "27b (интервал)"
        cutlog.append({"t0": round(intervals[i][0], 2), "t1": round(intervals[i][1], 2),
                       "text": texts[i]["text"], "reason": "интервал целиком", "source": src})
    print(f"ВЫКИНУТЬ интервалов: {len(drop)} (не-речь {len(auto_drop)} + 27b {len(llm_drop)}). notes: {notes}", flush=True)

    kept_idx = [i for i in range(len(intervals)) if i not in drop]
    keep = [intervals[i] for i in kept_idx]
    if tail_cuts:
        keep = align.subtract_ranges(keep, tail_cuts)     # срез старых хвостов (перезаходы)
    if a.ssm:
        # SSM режет внутри-фразовый повтор (резы уже посчитаны преданализом до LLM)
        from core import ssm as ssmmod
        ssm_ranges, n_breath = [], 0
        for i in kept_idx:
            s, e = intervals[i]
            r = ssm_pre.get(i) or []
            if r:
                phrase = ssmmod.repeated_phrase(texts[i]["text"]) or texts[i]["text"][:40]
                for a0, b0 in r:
                    cutlog.append({"t0": round(a0, 2), "t1": round(b0, 2), "text": phrase,
                                   "reason": "повтор фразы (оставлен последний заход)", "source": "SSM-повтор"})
                ssm_ranges += r
            clip = af[int(s*16000):int(e*16000)]
            for a0, b0 in ssmmod.breath_cut_ranges(clip, off=s):    # вздохи/паузы внутри
                cutlog.append({"t0": round(a0, 2), "t1": round(b0, 2), "text": "(тишина/вздох)",
                               "reason": "пауза/вздох внутри фразы", "source": "вздох"})
                ssm_ranges.append((a0, b0)); n_breath += 1
        print(f"  SSM-резов повторов + вздохов: {len(ssm_ranges)-n_breath} + {n_breath}", flush=True)
        keep = align.subtract_ranges(keep, ssm_ranges)
        # Микро-осколки (<0.25с) после вычитания — это «кхе»/щелчки у краёв интервалов:
        # интервал начинается с кхе+вздоха, вздохо-рез убирает паузу, но его keep_pad
        # (0.12с) оставляет кусочек самого кхе. Осмысленной речи в <0.25с не бывает.
        shards = [(s0, e0) for s0, e0 in keep if e0 - s0 < 0.25]
        if shards:
            for s0, e0 in shards:
                cutlog.append({"t0": round(s0, 2), "t1": round(e0, 2), "text": "(кхе/щелчок)",
                               "reason": "микро-осколок после чистки (<0.25с)", "source": "вздох"})
            keep = [(s0, e0) for s0, e0 in keep if e0 - s0 >= 0.25]
            print(f"  выброшено микро-осколков (кхе/щелчки): {len(shards)}", flush=True)
    if a.refine:
        # ОПЦИЯ (по умолчанию ВЫКЛ): forced-align текста Omni для внутри-фразовой чистки.
        # ⚠ На практике режет окончания слов, т.к. Omni местами ослышивается и выравнивание
        # неверного текста даёт мусорные времена. Оставлено для экспериментов.
        print("forced-align оставленных интервалов (внутри-фразовая чистка)...", flush=True)
        import soundfile as sf
        from core import falign
        aicut.unload_ours()
        aicut.warn_foreign_models()
        a16, _ = sf.read(wavs[0], dtype="int16")
        if a16.ndim > 1:
            a16 = a16.mean(1)
        af = a16.astype("float32") / 32768.0
        cut_ranges, n_rep, n_breath, n_nosil = [], 0, 0, 0
        t_env, rms_env = _rms_env(af)
        for i in kept_idx:
            s, e = intervals[i]
            words = falign.align_text(af[int(s*16000):int(e*16000)], texts[i]["text"])
            if not words:
                continue
            for w in words:
                w["start"] += s; w["end"] += s
            rng, _log = align.find_restarts(words)   # _log: имя log занято логгером модуля
            for (a0, a1) in rng:
                # привязываем границы реза к тишине: режем ТОЛЬКО если с обеих сторон есть
                # пауза. Иначе forced-align мог ошибиться на повторе слова («был») и рез
                # пришёлся бы посередине слова («Я бы» + «ыл…»). Нет тишины — не режем.
                sa = _snap_silence(t_env, rms_env, a0)
                sb = _snap_silence(t_env, rms_env, a1)
                if sa is None or sb is None or sb - sa < 0.05:
                    n_nosil += 1                 # найден, но не вырезан (нет тишины)
                    continue
                cut_ranges.append((sa, sb)); n_rep += 1
            for wa, wb in zip(words, words[1:]):
                if wb["start"] - wa["end"] > 0.5:
                    cut_ranges.append((wa["end"] + 0.12, wb["start"] - 0.10)); n_breath += 1
            if words[0]["start"] - s > 0.4:
                cut_ranges.append((s, words[0]["start"] - 0.10)); n_breath += 1
            if e - words[-1]["end"] > 0.4:
                cut_ranges.append((words[-1]["end"] + 0.12, e)); n_breath += 1
        falign.release_model()
        cut_ranges = [(x, y) for x, y in cut_ranges if y - x > 0.03]
        print(f"  внутри-фразовых повторов: {n_rep}"
              + (f" (+{n_nosil} пропущено — нет тишины по краям)" if n_nosil else "")
              + f", вздохов/пауз: {n_breath}", flush=True)
        keep = align.subtract_ranges(keep, cut_ranges)
    keep = [(s, e) for s, e in keep if round(e*60) - round(s*60) > 0]

    # Санитарный гард: модель могла вернуть drop на все индексы, а is_nonspeech —
    # съесть тихий/шумный исходник. Без гарда xmlbuild спокойно писал ПУСТОЙ
    # таймлайн поверх out.xml и затирал .project.json — прошлая нарезка терялась.
    # Лучше упасть с внятным текстом и не трогать готовые файлы.
    _guard_keep(keep, intervals)

    # self-check стыков (уроки video-use): LM Studio выгружаем ДО Whisper (16 ГБ VRAM),
    # склейка keep-аудио -> Whisper -> сомнительное слово у стыка = обрезано катом ->
    # расширить границу (+1 контрольная проверка). Результат — в .project.json.
    aicut.unload_ours()
    aicut.warn_foreign_models()
    screport = None
    if not a.no_selfcheck and keep:
        try:
            from core import selfcheck
            from core import draftrender
            print(f"self-check стыков ({a.selfcheck_model} по склейке)...", flush=True)
            keep, screport = selfcheck.check_and_fix(
                wavs[0], keep, emit=lambda *x: print(*x, flush=True),
                engine=a.selfcheck_model,
                tmp_dir=draftrender.tmp_dir(a.out))
            keep = [(s, e) for s, e in keep if round(e*60) - round(s*60) > 0]
        except ReelsiError: raise
        except Exception as ex:
            print(f"self-check пропущен: {ex}", flush=True)

    assign = align.assign_cameras(keep, N, return_every=a.cam_return, big_chunk_sec=6.0) if N > 1 else None
    info = xmlbuild.build(cams, keep, offsets, a.out, assign=assign,
                          scale=a.scale, sub_words=None, music_path=None)

    # сайдкар для редактора нарезки: как пересобрать XML из отредактированных блоков
    proj = {"cams": cams, "offsets": offsets, "fps": 60, "cam_return": a.cam_return,
            "scale": a.scale, "keep": [[round(s, 3), round(e, 3)] for s, e in keep]}
    if screport and (screport.get("fixed") or screport.get("left")):
        proj["selfcheck"] = screport
    if prev_overrides:
        proj["user_overrides"] = prev_overrides   # память правок переживает пере-нарезку
    # Повторная запись тех же сайдкаров, что уже положил pipeline (он пишет их до
    # чернового рендера): здесь они дополняются selfcheck/user_overrides. Пишем тем же
    # атомарным способом — open(...,"w") усекал готовую разметку до сериализации (GZ, п. A).
    write_project(os.path.splitext(a.out)[0] + ".project.json", proj)

    # cut-log: что именно и почему убрано (ничего молча) — рядом с XML + на экран
    cutlog.sort(key=lambda c: c["t0"])
    logf = os.path.splitext(a.out)[0] + ".cuts.json"
    atomic_json_dump(logf, cutlog, indent=1)
    print(f"\n=== ЧТО УБРАНО ({len(cutlog)}) — решения ИИ ===", flush=True)
    for c in cutlog:
        print(f"  {c['t0']:6.1f}-{c['t1']:6.1f}  [{c['source']}]  «{c['text'][:70]}»  — {c['reason'][:70]}", flush=True)
    _free_vram_for_render()                             # освободить VRAM после нарезки
    draft_path = None
    if not a.no_draft:
        try:
            from core import draftrender
            draft_path = draftrender.render_draft(a.out, emit=lambda *x: print(*x, flush=True))
        except ReelsiError: raise
        except Exception as ex:
            print(f"черновик не собрался: {ex}", flush=True)
    if a.omni_review and draft_path:
        # Omni-ревью черновика (эксперимент): отдельный процесс, 7.5 ГБ VRAM свопом
        # (LM Studio уже выгружен). Только советы в .review.json — монтаж не трогает.
        try:
            subprocess.run(module_cmd("omni_review", draft_path), env=child_env(),
                           timeout=1800)
        except ReelsiError: raise
        except Exception as ex:
            print(f"Omni-ревью не удалось: {ex}", flush=True)
    print(f"\n-> {a.out}  ({info.get('total_s', 0):.0f}s, {info.get('segments')} сег., {N} кам.)  + {os.path.basename(logf)}", flush=True)


if __name__ == "__main__":
    try:
        # Рабочий каталог камер не переживает «Стоп»: taskkill /F убивает процесс без
        # атекситов, и omnicut_* с WAV целой камеры остаётся в %TEMP% (до ~230 МБ за
        # отмену). Маркер WORK_DIR= печатается сразу — сервер (api/jobs.py) читает его
        # из stdout и чистит каталог в _kill_curproc. finally здесь — для штатного
        # выхода и SystemExit («вырезал почти всё»).
        import tempfile, shutil
        _work = tempfile.mkdtemp(prefix="omnicut_")
        print(f"WORK_DIR={_work}", flush=True)
        try:
            main(_work)
        finally:
            shutil.rmtree(_work, ignore_errors=True)
    except ReelsiError as e:
        cli_error(e)
