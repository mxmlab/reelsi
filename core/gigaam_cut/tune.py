# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пороги нарезки и всё, что их читает.

Модуль собран не по теме, а по ОГРАНИЧЕНИЮ, и это важно понимать, прежде чем
растаскивать его дальше. `apply_speaker` накладывает профиль спикера, записывая
значения прямо в globals() ЭТОГО модуля (см. _CUT_GLOBALS). Функция, читающая
порог по имени из другого модуля, получила бы его через `from .tune import
SNAP_DB` — то есть связанное один раз значение по умолчанию, — и профиль
спикера перестал бы на неё действовать. Без ошибки, без падения: просто чужая
калибровка. Поэтому все тринадцать функций, читающих переписываемые пороги,
лежат здесь, рядом с самими порогами.

Кому порог нужен снаружи — берёт его как `tune.SILENCE_SEC` (через модуль, а не
по имени): так значение читается в момент обращения, уже после профиля.
"""
import os, json
import numpy as np
import soundfile as sf
from core import paths
from core.app_meta import console_emit, wrap_emit


# HERE — корень репозитория, а НЕ папка пакета: соседние модули лежат там.
HERE = paths.ROOT

SR = 16000
CHUNK = 30.0          # сек: окно эмиссий wav2vec2 (память)
SILENCE_SEC = 0.8     # сек: пауза дольше — «полное молчание», всегда режется (как VAD)
MIN_KEEP = 0.6        # сек: однословный островок короче — мусор в таймлайне, выкидываем
MIN_ISLAND = 0.35     # сек: кусок короче — мусор при любом числе слов («а с» = 0.20с)
# --- подгон резов по ЗВУКУ (а не по таймингам слов) ------------------------
# CTC даёт границу слова с точностью ~40мс и ничего не знает про вдохи. Замер на
# C1353/1355/1356: 6-8с тишины ОСТАВАЛОСЬ внутри кусков (паузы 0.2-0.8с, короче
# SILENCE_SEC), а конец куска приходился на громкое место в 8-11 случаях из 15 —
# это и есть «обрезаются немного слова».
# Границы ставим ОТ СЛОВ, а не по «самой тихой точке»: сверка с ручной нарезкой
# юзера (NGAutoCut_out, 3 клипа) показала его почерк — старт за ~25мс до первого
# слова, конец через ~100мс после последнего (слово должно договорить). Поиск
# тихой точки в окне +-150мс вместо этого разжимал плотную нарезку на +2с.
# Отступ НЕ фиксированный: замер по трём клипам — звук тянется после «конца
# слова» по CTC у 62-83% слов (медиана 80-120мс, бывает до 400мс). Поэтому от
# края слова идём ПО ЗВУКУ, пока он не затих: где гласная тянется — доберём,
# где слово оборвалось чётко — не добавим ничего.
EDGE_IN_MAX = 0.15    # сек: максимум добора назад (атака согласного)
EDGE_OUT_MAX = 0.35   # сек: максимум добора вперёд (хвост слова)
EDGE_TAIL = 0.03      # сек: чуть воздуха после затухания, чтобы не рубить на спаде
SNAP_DB = 12.0        # дБ над уровнем шума: ниже — считаем «тихо»
# Один тихий кадр — ещё НЕ конец волны: внутри слова есть провалы (смычка перед
# «п/т/к», пауза между слогами), и рез по первому же кадру ниже порога садился
# в середину волны — на слух это обрубленное слово. Концом волны считаем только
# ЗАТИШЬЕ: тихо подряд не меньше QUIET_RUN. Затишье есть не всегда (следующее
# слово начинается сразу) — тогда работает прежний запасной вариант: самое тихое
# место окна, чтобы рез не сел на атаку чужого слова.
QUIET_RUN = 0.08      # сек: столько тихо подряд = затишье, а не провал в волне
# Замер на C1414 (32 куска): 4 старта сели НЕ в тишину, а прямо на звук. Причина
# одна — CTC-тайминг слова не совпадает с входом волны, причём врёт в ОБЕ стороны:
# у «я» старт опоздал на 160мс (в окне EDGE_IN_MAX одна речь, затишья нет ->
# запасной argmin сажает рез на атаку), у «транболлона» опередил на 130мс — там
# ещё шумел вдох, и рез садился на вдох, хотя настоящая тишина была ПОЗЖЕ старта.
# Поэтому вход слова ищем по звуку, а не по CTC: голос — это громче ONSET_DB над
# полом (вдох/шум комнаты на реальных клипах 12-17 дБ, речь 30-45) и не тише пика
# самого слова минус ONSET_FALL (тихое слово тоже должно опознаться).
ONSET_DB = 20.0       # дБ над полом: тише — ещё не голос (вдох, шум, стук)
ONSET_FALL = 30.0     # дБ ниже пика слова: глухой согласный тише вокала, но не так
ONSET_BACK = 0.45     # сек: насколько раньше CTC-старта ищем вход волны
ONSET_FWD = 0.30      # сек: и насколько позже
ONSET_RUN = 0.03      # сек: столько громко подряд = волна, а не щелчок
START_PAD = 0.02      # сек: столько воздуха перед входом волны (почерк юзера)
ATTACK_MAX = 0.15     # сек: докуда добираем тихую атаку слова назад от волны
# У юзера внутри кусков НЕТ ни одной дыры >=0.20с (на 3 клипах), максимум 1-2
# участка по 0.15-0.19с. Порог 0.15 даёт его же дробность: 23/20/20 кусков против
# его 24/21/23, и длина при этом почти не меняется — значит режутся именно дыры,
# а не живая речь.
HOLE_MIN = 0.15       # сек: дыра без речи длиннее — вырезаем
HOLE_AIR = 0.03       # сек: сколько воздуха оставить по краям вырезанной дыры
# Вдохи, «кхе», чмоканье, стук — GigaAM их НЕ транскрибирует, поэтому опознаются
# просто: внутри куска звучит, а слова на этом месте нет. Признак надёжнее любых
# спектральных порогов (кашель бывает громче речи) и не рискует речью: режем
# только то, что лежит ВНЕ слов, да ещё и с запасом WORD_PAD по краям.
WORD_PAD = 0.08       # сек: запас вокруг слова — CTC подрезает хвосты, отдаём их речи

HEAL_SEG = 1.2        # сек: короче — обрывок фразы, которому нужно продолжение
HEAL_RUN = 4          # слов: столько максимум дорезанного продолжения возвращаем
# Стемы брака: мат и NG-реплики («стоп», «давай заново», «блин забыл»). Если
# модель выкинула кусок с таким словом — она права, и код НЕ возвращает его
# обратно (на C1354 иначе воскресало «блять кепка ебаная падает» в начале ролика).
# Это НЕ badwords.txt: там список для цензуры субтитров — слова, которые надо
# ЗАПИКАТЬ, а не вырезать вместе с куском речи.
NG_MARKERS = ("блят", "бля", "ебан", "ебат", "ёбан", "хуй", "хуе", "хуё", "пизд",
              "сука", "нахуй", "охуе", "стоп", "заново", "дубль", "забыл",
              "переснима", "не то")
# Паразиты: их 27b выкидывает правильно, и «дорастить обрывок» ими нельзя
FILLERS = {"ну", "вот", "короче", "наверное", "типа", "значит", "э", "ээ", "эээ",
           "эм", "мм", "ммм", "кхм", "аа", "ааа"}
REPEAT_N = 10         # слов: максимальная длина n-граммы при поиске повторов/заходов
REPEAT_WIN = 4.0      # сек: дальше этого повтор — уже осмысленный, а не запинка
TAKE_WIN = 12.0       # сек: пересъёмка идёт сразу; дальше — не заход, а другая мысль
DEDUPE = False        # чистка дублей кодом после решения модели (задание CA): сняли —
                      # решает только модель по смыслу. Умолчание False (задание LA,
                      # как в cutstages: умной модели только мешает, режет перечисления и роли).


# --------------------------------------------------------------------------- #
# Профиль спикера
# --------------------------------------------------------------------------- #
# Пороги выше — калибровка по спикеру A: стерильная студия, речь на 39-40 дБ над
# шумом комнаты. У другого спикера другая студия и другой голос, и те же цифры
# начинают врать (замеры и следствия — в docstring speakers.py). Профиль их
# переопределяет: значения кладутся в МОДУЛЬНЫЕ константы один раз на старте
# run(), потому что читают их полтора десятка функций по всему файлу и таскать
# параметр через все не за что.
SPEAKER = None            # профиль, применённый к этому запуску (для лога)
DB_AUTO = False           # пороги громкости считать от запаса речи в клипе
SNAP_FRAC = 0.30
ONSET_FRAC = 0.50
_DB_TUNED = False         # автоподбор делается один раз на клип

_CUT_GLOBALS = {          # ключ профиля -> имя модульной константы
    "db_auto": "DB_AUTO", "snap_frac": "SNAP_FRAC", "onset_frac": "ONSET_FRAC",
    "snap_db": "SNAP_DB", "onset_db": "ONSET_DB", "onset_fall": "ONSET_FALL",
    "quiet_run": "QUIET_RUN", "edge_in_max": "EDGE_IN_MAX",
    "edge_out_max": "EDGE_OUT_MAX", "word_pad": "WORD_PAD",
    "hole_min": "HOLE_MIN", "min_keep": "MIN_KEEP", "min_island": "MIN_ISLAND",
    "silence_sec": "SILENCE_SEC", "dedupe": "DEDUPE",
}
# Снимок умолчаний при импорте модуля (задание LA):
# apply_speaker(name) всегда начинает с чистого листа, а apply_speaker(None)
# полностью возвращает модульные пороги к значениям по умолчанию.
_DEFAULT_CUT_GLOBALS = {gname: globals()[gname] for gname in _CUT_GLOBALS.values()}


def apply_speaker(name, emit=console_emit):
    """Наложить профиль спикера на пороги модуля. name — ключ/label/None.

    Возвращает применённый профиль (или None). Профиль без блока `cut` ничего
    не меняет: дефолты в speakers.CUT_DEFAULTS — те же числа, что и здесь."""
    emit = wrap_emit(emit)
    global SPEAKER, _DB_TUNED
    _DB_TUNED = False
    SPEAKER = None
    g = globals()
    for gname, val in _DEFAULT_CUT_GLOBALS.items():
        g[gname] = val
    if not name:
        return None
    try:
        from core import speakers as _sp
    except Exception as ex:
        emit("  профили спикеров недоступны ({err_type}: {err})",
             err_type=type(ex).__name__, err=str(ex), flush=True)
        return None
    prof = _sp.load(name)
    if prof is None:
        emit("  спикер «{name}» не найден — пороги по умолчанию", name=name, flush=True)
        return None
    cut = _sp.resolve_cut(prof)
    changed = []
    for k, gname in _CUT_GLOBALS.items():
        if k not in cut:
            continue
        if cut[k] != g[gname]:
            changed.append(f"{k}={cut[k]}")
        g[gname] = cut[k]
    SPEAKER = prof
    what = ", ".join(changed) if changed else "пороги по умолчанию"
    if prof.get("breath_p_cut"):
        what += f"; порог вздохов {float(prof['breath_p_cut']):.2f}"
    if (prof.get("hint") or "").strip():
        what += "; своя поправка к промпту решения"
    emit("  спикер: {label} — {what}", label=prof.get('label') or name, what=what, flush=True)
    return prof


def _sys(base):
    """Системный промпт решения + личная поправка спикера (`hint` в профиле).

    Не все ошибки нарезки — пороги. У спикера B, например, пороги не ошибаются ни
    разу на 17 клипах, зато в дублях полно разговоров с оператором («а давай
    чуть помедленнее», «тара тараа»), и выкидывать их приходится руками — это
    решение смысловое, лечится подсказкой модели, а не цифрой. Поправка
    добавляется во ВСЕ три режима решения (разметка/цитаты/индексы)."""
    hint = ((SPEAKER or {}).get("hint") or "").strip()
    if not hint:
        return base
    return base + "\n\nОСОБЕННОСТИ ЭТОГО СПИКЕРА (учитывай при решении):\n" + hint


def breath_model_path(emit=console_emit):
    """Json детектора вздохов для текущего спикера (поле `breath_model`), либо
    None = общая модель.

    ЗАМЕРЕНО: личные модели проигрывают общей. Обучил по каждому отдельно
    (`train_breath.py --speaker`) и сверил leave-one-clip-out на одних и тех же
    44 клипах: общая ловит 25% вздохов при точности 76%, три личные вместе —
    21.8% при 74%. Данных на одного просто мало (906-2725 примеров против 4914),
    а признаки вздоха оказались не такими личными, как звучание голоса. Личным
    должен быть ПОРОГ, а не модель, — см. `breath_p_cut` в профилях.

    Поле оставлено на будущее: когда у спикера накопится столько же клипов,
    сколько сейчас во всей смеси, личная модель начнёт выигрывать. Нет файла —
    молча падаем на общую."""
    emit = wrap_emit(emit)
    name = ((SPEAKER or {}).get("breath_model") or "").strip()
    if not name:
        return None
    path = name if os.path.isabs(name) else paths.root(name)
    if not os.path.exists(path):
        emit("  своей модели вздохов нет ({name}) — беру общую",
             name=os.path.basename(path), flush=True)
        return None
    emit("  вздохи: модель спикера {name}", name=os.path.basename(path), flush=True)
    return path


def _autotune_db(db, floor, emit=console_emit):
    """Пороги громкости от РЕАЛЬНОГО запаса речи в клипе (при db_auto).

    Жёсткие 20/12 дБ над полом — это 50%/30% динамики спикера A (замер: речь у
    него на 39.5 дБ над полом). У кого запас меньше, тому те же 20 дБ отрезают
    не шум, а половину слов. Считаем те же доли от запаса ЭТОГО клипа: на
    материале спикера A формула возвращает 19.9/12.0, то есть его нарезка не
    меняется, а у спикера C (запас 21-34 дБ) пороги опускаются до 10-17 дБ.

    Запас меряем медианой речи над полом, речь берём по текущему SNAP_DB —
    порог грубый, но для оценки МЕДИАНЫ этого хватает: сдвиг порога на пару дБ
    двигает медиану на десятые."""
    emit = wrap_emit(emit)
    global SNAP_DB, ONSET_DB, _DB_TUNED
    if not DB_AUTO or _DB_TUNED:
        return
    _DB_TUNED = True
    sp = db[db > floor + SNAP_DB]
    if sp.size < 10:                       # тишина или битый звук — не трогаем
        emit("  автопороги: речи не нашлось, оставляю жёсткие", flush=True)
        return
    margin = float(np.median(sp)) - floor
    # Границы разумного: 6 дБ — уже уровень вдоха, выше 24 порог съест тихие
    # слова даже у чистой записи.
    snap = min(max(SNAP_FRAC * margin, 4.0), 14.0)
    onset = min(max(ONSET_FRAC * margin, 6.0), 24.0)
    emit("  автопороги: запас речи {margin:.1f}дБ -> тишина {snap:.1f}, "
         "голос {onset:.1f} (было {snap_db:.1f}/{onset_db:.1f})",
         margin=margin, snap=snap, onset=onset, snap_db=SNAP_DB, onset_db=ONSET_DB, flush=True)
    SNAP_DB, ONSET_DB = snap, onset


def _ranges(idx_sorted):
    """Сгруппировать отсортированные индексы в непрерывные диапазоны [a, b]."""
    out, cur = [], []
    for i in idx_sorted:
        if cur and i != cur[-1] + 1:
            out.append((cur[0], cur[-1])); cur = []
        cur.append(i)
    if cur:
        out.append((cur[0], cur[-1]))
    return out


def _silence_bounds(words, thr=None):
    """Индексы i, после которых между словом i и i+1 — пауза > thr (полное
    молчание). Это ЖЁСТКИЕ границы реза: тишина всегда вырезается и никогда
    не восстанавливается самопроверкой (слова тишины не попадают в drop).

    thr=None — взять текущий SILENCE_SEC. Значением по умолчанию его писать
    нельзя: дефолт связывается при импорте, и профиль спикера (apply_speaker,
    он меняет модульные константы) до него уже не дотянется."""
    if thr is None:
        thr = SILENCE_SEC
    return {i for i in range(len(words) - 1)
            if words[i + 1]["start"] - words[i]["end"] > thr}


def keep_segments(words, keep, silence_bounds=None):
    """Прогоны оставленных слов как ДИАПАЗОНЫ ИНДЕКСОВ [a, b] — то, что реально
    станет отдельным куском в таймлайне (прогон дополнительно разрывается на
    границах полного молчания, чтобы тишина всегда была вырезана, как VAD)."""
    out = []
    for a, b in _ranges(sorted(keep)):
        seg_a = a
        for i in range(a, b):
            if silence_bounds is not None and i in silence_bounds:
                out.append((seg_a, i)); seg_a = i + 1
        out.append((seg_a, b))
    return out


def keep_intervals(words, keep, silence_bounds=None):
    """Из множества оставленных слов собрать интервалы (прогоны подряд идущих
    слов). Вырезанные слова = дырки между прогонами -> они и вырезаются."""
    return [(round(words[a]["start"], 3), round(words[b]["end"], 3))
            for a, b in keep_segments(words, keep, silence_bounds)]


def drop_micro_keeps(words, kept, drop, silence_bounds=None, min_keep=None,
                     protect=(), emit=console_emit):

    """Островки короче min_keep — мусор в таймлайне (кадр-другой «а», 0.28с
    «чтобы»): либо от них ничего не слышно, либо это огрызок фразы. В drop.

    Только ОДНОСЛОВНЫЕ: два слова подряд — уже осмысленный кусок («во вторых»
    занимает 0.56с, но выкидывать его нельзя).

    min_keep=None — текущий MIN_KEEP (профиль спикера его меняет; дефолтом
    аргумента писать нельзя, он связывается при импорте)."""
    if min_keep is None:
        min_keep = MIN_KEEP
    removed = []
    changed = True
    while changed:
        changed = False
        for a, b in keep_segments(words, kept, silence_bounds):
            if words[b]["end"] - words[a]["start"] >= min_keep:
                continue
            if any(i in protect for i in range(a, b + 1)):
                continue
            if b > a and words[b]["end"] - words[a]["start"] >= MIN_ISLAND:
                continue                        # 2+ слов и не совсем огрызок — контент
            for i in range(a, b + 1):
                kept.discard(i); drop.add(i)
            removed.extend(range(a, b + 1))
            changed = True
            break
    if removed:
        emit("  микро-островки (код): выкинул < {min_keep:.2f}с — слова {words}",
             min_keep=min_keep, words=sorted(removed), flush=True)
    return removed


# --------------------------------------------------------------------------- #
# Подгон резов по звуку
# --------------------------------------------------------------------------- #
def _envelope(wav_path, hop=0.010, frame=0.025):
    """Огибающая клипа: громкость в дБ + «шумность» каждого кадра (доля энергии
    выше 3 кГц) + уровень шума комнаты. Считается один раз: по ней и снап границ,
    и поиск тихих дыр, и детект вдохов."""
    a, sr = sf.read(wav_path, dtype="float32")
    if a.ndim > 1:
        a = a.mean(1)
    fl, hl = max(1, int(frame * sr)), max(1, int(hop * sr))
    if len(a) < fl:
        return None, None, None, hop
    fr = np.lib.stride_tricks.sliding_window_view(a, fl)[::hl]
    rms = np.sqrt(np.einsum("ij,ij->i", fr, fr) / float(fl) + 1e-10)
    db = 20 * np.log10(rms + 1e-10)
    sp = np.abs(np.fft.rfft(fr * np.hanning(fl), axis=1)) ** 2
    freqs = np.fft.rfftfreq(fl, 1.0 / sr)
    hf = sp[:, freqs > 3000].sum(1) / (sp.sum(1) + 1e-12)
    return db, hf, float(np.percentile(db, 20)), hop


def _hush_edge(quiet, step, run):
    """Край ПЕРВОГО (по ходу `step`) затишья длиной от `run` кадров, либо None.

    Серия короче `run` — это провал внутри волны (смычка, стык слогов), по нему
    резать нельзя. Исключение — серия, упирающаяся в дальний край окна: за окном
    она, скорее всего, продолжается, и обрывать её по длине неправильно."""
    idx = np.flatnonzero(quiet)
    if not idx.size:
        return None
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    for r in (runs if step > 0 else runs[::-1]):
        edge = (r[-1] == len(quiet) - 1) if step > 0 else (r[0] == 0)
        if len(r) >= run or edge:
            return int(r[0] if step > 0 else r[-1])
    return None


def _walk_sound(db, thr, i, step, limit, tail, stop=None, run=1):
    """Куда поставить край куска, отступив от края слова в сторону `step`.

    Ищем в пределах `limit` кадров КОНЕЦ ВОЛНЫ — начало затишья (`run` тихих
    кадров подряд): тянущаяся буква попадёт в кусок целиком, а у чётко
    оборванного слова отступа почти не будет. Если затишья нет (следующее слово
    начинается сразу — например, вырезанный дубль), встаём в САМОЕ ТИХОЕ место
    отрезка: иначе рез сядет на атаку чужого слова и в монтаже будет слышен его
    огрызок. `stop` — граница, дальше которой нельзя (соседнее слово)."""
    n = len(db)
    lo, hi = (i, min(n - 1, i + limit)) if step > 0 else (max(0, i - limit), i)
    if stop is not None:
        lo, hi = (lo, min(hi, stop)) if step > 0 else (max(lo, stop), hi)
    if hi <= lo:
        return min(max(i, 0), n - 1)
    seg = db[lo:hi + 1]
    j = _hush_edge(seg < thr, step, run)
    if j is None:
        j = int(np.argmin(seg))
    return min(max(lo + j + step * tail, 0), n - 1)


def _word_onset(db, floor, i0, i1, hop, stop=None):
    """Кадр, где слово РЕАЛЬНО начинает звучать (CTC-старт врёт в обе стороны).

    Голосом считаем звук громче ONSET_DB над полом и не тише пика самого слова
    минус ONSET_FALL — вдох и шум комнаты под этот уровень не подходят. Берём
    волну, накрывающую CTC-старт (значит старт опоздал — отдаём её начало), а
    если её нет — первую волну ПОСЛЕ старта (старт опередил, между ним и словом
    шумел вдох). Не нашли ничего — отдаём CTC-старт как есть. `stop` — кадр,
    раньше которого нельзя (конец соседнего слова: при сплошной речи волна
    тянется из вырезанного дубля, и её начало нам не принадлежит)."""
    n = len(db)
    i0 = min(max(i0, 0), n - 1)
    i1 = min(max(i1, i0), n - 1)
    peak = float(db[i0:i1 + 1].max())
    lvl = max(floor + ONSET_DB, peak - ONSET_FALL)
    lo = max(0, i0 - int(ONSET_BACK / hop))
    if stop is not None:
        lo = max(lo, stop)
    hi = min(n - 1, i1, i0 + int(ONSET_FWD / hop))
    if hi <= lo:
        return i0
    idx = np.flatnonzero(db[lo:hi + 1] >= lvl)
    if not idx.size:
        return i0
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    runs = [r for r in runs if len(r) >= max(1, int(ONSET_RUN / hop))] or runs
    k = i0 - lo
    for r in runs:
        if r[0] <= k <= r[-1]:
            return lo + int(r[0])
    for r in runs:
        if r[0] > k:
            return lo + int(r[0])
    return i0


def _start_edge(db, thr, floor, i0, i1, hop, stop=None, run=1):
    """Начало куска перед словом [i0..i1] — за START_PAD до ВХОДА ВОЛНЫ.

    Так режет юзер: сверка с его ручной доводкой (C1414/C1400/C1397, 80 кусков)
    даёт старт за 10-20мс до того, как слово реально зазвучало. Прежний вариант
    (затишье в окне EDGE_IN_MAX от CTC-старта) систематически брал на 50-80мс
    больше воздуха, а когда затишья в окне не было — садился прямо на звук
    (вдох перед словом или атака слова). Опора на CTC-старт и была ошибкой:
    он гуляет относительно волны на ±150мс в обе стороны.

    Тихую атаку (глухой согласный тише вокала) добираем: от входа волны идём
    назад, пока звук выше порога тишины, но не дальше ATTACK_MAX. Если тишины
    перед словом нет вообще (склейка посреди сплошной речи — вырезали дубль),
    отступать некуда: возвращаем прежнее «самое тихое место окна»."""
    o = _word_onset(db, floor, i0, i1, hop, stop=stop)
    lim = max(0, o - int(ATTACK_MAX / hop))
    if stop is not None:
        lim = max(lim, stop)
    j = o
    while j > lim and db[j - 1] >= thr:
        j -= 1
    ns = max(0, j - int(START_PAD / hop))
    if db[ns] < thr:
        return ns
    return _walk_sound(db, thr, i0, -1, int(EDGE_IN_MAX / hop),
                       int(EDGE_TAIL / hop), stop=stop, run=run)


def _speech_mask(db, thr, hop, words, n, qrun):
    """Маска речи: кадры, где звучит СЛОВО (с добором волны по краям).

    Всё вне маски — вдох, «кхе», чмоканье, пауза: звук есть, а слова на нём нет
    (GigaAM неречь не транскрибирует). Маску строим от CTC-краёв, а не от входа
    волны, как рез: проверка на 8 клипах — по входу волны маска срезала начало
    тихих слов («клеточная», «поэтому»), и они уходили в дыру как вдох. У маски
    цена ошибки выше, чем у реза: рез двигается, а слово пропадает совсем.

    ТИХИЕ кадры речью не считаем, даже если они попали под маску: и добор волны,
    и WORD_PAD легко уезжают в паузу, а замаскированная пауза переставала быть
    дырой и оставалась в куске. Сверка с ручной доводкой (6 клипов): так
    вырезается 21 из 40 участков, которые юзер убирал руками, ценой 0.22с чужого
    (и то тишины). Хвост волны это не трогает — он выше порога тишины."""
    mask = np.zeros(n, dtype=bool)
    pad = max(1, int(WORD_PAD / hop))
    for w in words:
        a = _walk_sound(db, thr, min(max(int(w["start"] / hop), 0), n - 1), -1,
                        int(EDGE_IN_MAX / hop), pad, run=qrun)
        b = _walk_sound(db, thr, min(max(int(w["end"] / hop), 0), n - 1), +1,
                        int(EDGE_OUT_MAX / hop), pad, run=qrun)
        if b >= a:
            mask[a:b + 1] = True
    # Порогу тишины верим, только если он реально НИЖЕ голоса: `floor` — 20-й
    # перцентиль клипа, и на плотном клипе (речь почти без пауз) он уезжает в саму
    # речь. Тогда гасить маску по нему нельзя — вырежем слова.
    if mask.any() and thr < float(np.median(db[mask])) - 6.0:
        mask &= db >= thr
    return mask


def _cut_breaths(keep, assign, wav_path, words, out, emit=console_emit):
    """Вздохи/«кхе» после подгона резов: уверенные вырезаем, спорные — в сайдкар.

    Отдельным шагом, а не внутри refine_keep: тут работают внешние модели (Silero
    + CED), их может не быть в окружении, и падать из-за этого посреди нарезки
    нельзя. `<stem>.breaths.json` читает редактор нарезки и рисует метки — то, что
    модель не уверена, юзер снимает одним кликом.
    """
    if not os.path.isfile(wav_path):
        # Пропажа звука — отказ (пайплайн перевыпустит WAV или завершится), а
        # «нет внешних моделей» — законный пропуск. Общий except Exception
        # ниже эти два случая не различает, поэтому отсутствие файла отсекаем ДО try.
        raise FileNotFoundError(f"Файл звука не найден для детектора вздохов: {wav_path}")
    marks = []
    try:
        from core import breath
        marks = breath.detect(wav_path, keep, words, emit=emit, path=breath_model_path(emit))
    except Exception as ex:
        emit("  детектор вздохов не отработал ({err_type}: {err})",
             err_type=type(ex).__name__, err=str(ex), flush=True)
        return keep, assign, []
    if not marks:
        return keep, assign, []
    p_cut = float((SPEAKER or {}).get("breath_p_cut") or breath.P_CUT)
    p_mark = float((SPEAKER or {}).get("breath_p_mark") or breath.P_MARK)
    if p_cut != breath.P_CUT:
        emit("  вздохи: порог реза {p_cut:.2f} (общий {p_cut_def:.2f}) — из профиля спикера",
             p_cut=p_cut, p_cut_def=breath.P_CUT, flush=True)
    # min_island — из профиля: у кого-то живой кусок в 0.3с это ещё содержание
    keep2, cut, parents = breath.apply(keep, marks, p_cut=p_cut, min_island=MIN_ISLAND)
    if cut:
        keep = keep2
        if assign is not None:
            assign = [assign[p] for p in parents]
        emit("  вздохи/«кхе»: вырезано {count} ({sec:.1f}с) — {classes}",
             count=len(cut), sec=sum(m["t1"] - m["t0"] for m in cut),
             classes=", ".join(sorted({m["класс"] for m in cut})), flush=True)
    show = [m for m in marks if m["p"] >= p_mark]
    for m in show:
        m["вырезано"] = any(abs(m["t0"] - c["t0"]) < 1e-6 for c in cut)
    try:
        json.dump(show, open(os.path.splitext(out)[0] + ".breaths.json", "w",
                             encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as ex:
        emit("  сайдкар вздохов не записался: {err}", err=str(ex), flush=True)
    if len(show) > len(cut):
        emit("  вздохи/«кхе»: помечено для редактора {count} (спорные)",
             count=len(show) - len(cut), flush=True)
    return keep, assign, show


def refine_keep(keep, wav_path, words=None, emit=console_emit, hole=None,
                air=HOLE_AIR, min_island=None):

    """Подвинуть резы на тихие места и вырезать тишину ВНУТРИ кусков.

    Две задачи, обе про звук, а не про текст:
    1) ГРАНИЦЫ ОТ СЛОВ. Тайминг слова из CTC врёт на кадр-другой, и рез садится на
       хвост слова («обрезаются немного слова»). НАЧАЛО куска ставим за START_PAD
       до ВХОДА ВОЛНЫ первого слова (`_start_edge`): CTC-старт гуляет относительно
       звука на ±150мс, и отсчёт от него оставлял то лишние 50-80мс воздуха, то
       сажал рез прямо на вдох или на атаку слова. КОНЕЦ — через EDGE_OUT после
       последнего слова, до КОНЦА ВОЛНЫ (затишье QUIET_RUN, а не первый тихий
       кадр: провал внутри слова затишьем не считается). Наружу выходим только в
       неречь — в соседнее слово не залезаем.
    2) ДЫРЫ БЕЗ РЕЧИ. Паузы короче SILENCE_SEC остаются внутри куска: на клипе их
       набегает 6-8 секунд. Плюс там же живут вдохи и «кхе» — звук есть, а слова
       нет, потому что GigaAM неречь не транскрибирует. Поэтому дыра = участок,
       где НЕТ слова (с запасом WORD_PAD) дольше `hole`; вырезаем его, оставив по
       `air` с каждой стороны, иначе склейка звучит рвано. Если слов не передали,
       падаем на порог громкости — хуже, но лучше, чем ничего.

    Возвращает (интервалы, родители): кусков становится больше (дыра разрезает
    кусок надвое), и `родители` говорят, из какого ИСХОДНОГО куска получился
    каждый новый. Это не мелочь: камеру назначает assign_cameras и обязательно
    меняет её на соседнем куске — без наследования камера прыгала бы прямо на
    вдохе посреди фразы. Если wav не читается — ошибка поднимается наверх.

    hole/min_island=None — текущие HOLE_MIN/MIN_ISLAND: дефолт аргумента
    связывается при импорте и профиля спикера уже не увидит."""
    emit = wrap_emit(emit)
    if hole is None:
        hole = HOLE_MIN
    if min_island is None:
        min_island = MIN_ISLAND
    db, hf, floor, hop = _envelope(wav_path)
    if db is None:
        return keep, list(range(len(keep)))
    _autotune_db(db, floor, emit=emit)
    thr = floor + SNAP_DB
    n = len(db)
    # затишье в кадрах: одним и тем же числом меряем и края кусков, и хвосты слов
    # в маске речи — иначе добранный по звуку хвост волны маска считала бы дырой
    # и вторая ступень тут же вырезала бы его обратно, посреди спада
    qrun = max(1, int(QUIET_RUN / hop))
    # маска речи по словам: всё вне её — вдох/кашель/пауза, что бы там ни звучало
    speech_mask = None
    if words:
        # Маска строится по словам, РАСШИРЕННЫМ по звуку: тянущаяся буква («давно»
        # звучит ещё 200мс после конца слова по CTC) — это речь, а не дыра. С
        # жёстким WORD_PAD такой хвост считался неречью и вырезался — ровно то,
        # что просили не резать.
        speech_mask = _speech_mask(db, thr, hop, words, n, qrun)

    def idx(t):
        return min(max(int(round(t / hop)), 0), n - 1)

    out, parents, moved, cut_holes, cut_sec, snapped = [], [], 0, 0, 0.0, 0
    for pi, (s, e) in enumerate(keep):
        # --- 1) границы от слов: дать слову начаться и договорить ---
        i0, i1 = idx(s), idx(e)
        if words:
            # «слово этого куска» = его СЕРЕДИНА внутри куска. По касанию нельзя:
            # подтянется соседнее слово, которое юзер намеренно вырезал (сверка с
            # ручной нарезкой: так уезжало до 600мс на старте и 500мс на конце).
            inside = [w for w in words if s <= 0.5 * (w["start"] + w["end"]) <= e]
            if inside:
                # дальше соседнего слова не заходим — иначе утащим вырезанный дубль
                prev = [w for w in words if w["end"] <= inside[0]["start"] - 0.01]
                nxt = [w for w in words if w["start"] >= inside[-1]["end"] + 0.01]
                lo = idx(prev[-1]["end"]) if prev else None
                hi = idx(nxt[0]["start"]) if nxt else None
                ns = _start_edge(db, thr, floor, idx(inside[0]["start"]),
                                 idx(inside[0]["end"]), hop, stop=lo, run=qrun)
                if db[ns] >= thr:
                    snapped += 1        # тишины перед словом не нашлось
                ne = _walk_sound(db, thr, idx(inside[-1]["end"]), +1,
                                 int(EDGE_OUT_MAX / hop), int(EDGE_TAIL / hop),
                                 stop=hi, run=qrun)
                if ns != i0 or ne != i1:
                    moved += 1
                i0, i1 = ns, ne
        if i1 <= i0:
            continue

        # --- 2) вырезать всё, что не речь: паузы, вдохи, «кхе» ---
        seg_db = db[i0:i1 + 1]
        if speech_mask is not None:
            quiet = ~speech_mask[i0:i1 + 1]
        else:
            quiet = seg_db < thr
        seg_start = i0
        k = 0
        while k < len(quiet):
            if not quiet[k]:
                k += 1
                continue
            m = k
            while m < len(quiet) and quiet[m]:
                m += 1
            if (m - k) * hop >= hole:
                lo, hi = i0 + k, i0 + m            # границы дыры
                left = lo + int(air / hop)
                right = hi - int(air / hop)
                if left > seg_start:
                    out.append((round(seg_start * hop, 3), round(left * hop, 3)))
                    parents.append(pi)
                cut_holes += 1
                cut_sec += (right - left) * hop
                seg_start = max(right, seg_start)
            k = m
        if i1 > seg_start:
            out.append((round(seg_start * hop, 3), round(i1 * hop, 3)))
            parents.append(pi)

    keep2, par2 = [], []
    for (a, b), pi in zip(out, parents):
        if b - a >= min_island:
            keep2.append((a, b)); par2.append(pi)
    # Одна строка про цену порога после нарезки: следующая калибровка любого
    # спикера делается по логу, а не замером по сайдкарам (задание BZ). Раньше
    # строка печаталась только когда что-то сдвинули/вырезали — на пустом прогоне
    # порог молчал, и цену его было не увидеть.
    if snapped:
        emit("  подгон по звуку: резов сдвинуто {moved}, дыр вырезано {cut_holes}, "
             "суммарно {cut_sec:.1f} с (hole_min={hole:.2f}), "
             "стартов без тишины перед словом {snapped}",
             moved=moved, cut_holes=cut_holes, cut_sec=cut_sec, hole=hole, snapped=snapped,
             flush=True)
    else:
        emit("  подгон по звуку: резов сдвинуто {moved}, дыр вырезано {cut_holes}, "
             "суммарно {cut_sec:.1f} с (hole_min={hole:.2f})",
             moved=moved, cut_holes=cut_holes, cut_sec=cut_sec, hole=hole,
             flush=True)
    return keep2, par2

