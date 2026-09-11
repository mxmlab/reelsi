# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Словарь «трудных» терминов: названия, которых нет в обычной речи — бренды,
аббревиатуры, имена, латиница (Reelsi, RTX 5090, ARRI Alexa). ASR их не знает и
подставляет ПОХОЖЕЕ слово — юзер правил каждый ролик руками. Здесь список хранится,
применяется к распознанным словам и ДОПОЛНЯЕТСЯ сам, когда правку делают в панели слов.

Две ступени подбора, обе детерминированные (никакого ИИ):

1) ВАРИАНТЫ — точное совпадение нормализованной цепочки слов. Только так ловится
   латиница и цифры: «эр тэ икс» -> «RTX 5090» никакой похожестью не берётся, слова
   вообще другие. Варианты копятся сами из ручных правок (см. learn).
2) ПОХОЖЕСТЬ — SequenceMatcher по самому термину. Ловит промах в паре букв
   («ротоскопинк» -> «ротоскопинг»), ради которого не хочется заводить вариант руками.

Границы жёсткие нарочно: похожесть считается только для слов от MIN_FUZZY_LEN букв и
порога FUZZY_THR. Ошибка тут дороже пропуска — подменённое НЕ ТО слово в субтитрах
юзер увидит уже в After Effects, когда править дорого.

Список правится в ⚙ → «Слова» (там же, где списки цензуры).
"""
import os, json, re, threading
from collections import Counter
from difflib import SequenceMatcher
from core.fileio import atomic_json_dump

from core import paths
from core.app_meta import env, wrap_emit
# REELSI_TERMS — как REELSI_UI_STATE/REELSI_INSERTLIB: без своей переменной тестовый
# профиль (порт 5098) правил бы боевой словарь.
TERMS_PATH = env("TERMS") or paths.root("terms.json")

FUZZY_THR = 0.82          # ниже — начинают цепляться обычные слова («монтаж»/«монтажа»)
MIN_FUZZY_LEN = 6         # короткие слова похожи друг на друга слишком легко
MAX_VARIANTS = 40         # на термин; копятся сами, без крышки файл растёт бесконечно
CORE_THR = 0.6            # защита learn: Dice по множеству букв. Полный SequenceMatcher
                          # до FUZZY_THR тут не дотягивает: «модси» против транслита
                          # «MOTS-C» даёт Dice 0.6, а коллизия это именно она. Ниже 0.6
                          # лезут обычные двухбуквенные совпадения коротких аббревиатур
                          # («GHQ» против «GHRP-6» делит только «гх»), выше — настоящие
                          # перестановки-ослышки. Буквы, а не подпоследовательность:
                          # общие «1»/«0» в названиях с цифрами иначе дают ложные
                          # срабатывания («К110» против «BPC-157»).

# Транслит латиницы → кириллица ПО ЗВУЧАНИЮ, для защиты learn от коллизий (см. ниже).
# Побуквенно, без двухбуквенных сочетаний: «MOTS-C» читается «мотс-к», как его и
# слышит ASR, — если склеивать «TS» в «ц», из слова выпадает буква «с» и ослышка
# «модси» перестаёт на него похожа.
_TRANSLIT = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "y": "и", "z": "з",
}


def _translit(s):
    """Латинский текст -> кириллица по звучанию. Дефисы и пробелы выбрасываются,
    цифры остаются как есть («RTX 5090» -> «рткс5090»)."""
    return "".join(_TRANSLIT.get(ch, ch if ch.isdigit() else "")
                   for ch in str(s or "").lower())


def _similar(a, b, thr=FUZZY_THR, core=CORE_THR):
    """Похожи ли строки: полный SequenceMatcher ИЛИ Dice по множеству букв (доля
    общих букв от суммы длин). Второй критерий — потому что коллизии ослышек бывают
    на пару букв: «модси» против «мотск» даёт ratio 0.6, а Dice 0.6, и это уже надо
    ловить; «диссип» против «мотск» — Dice 0.18, не коллизия."""
    if not a or not b:
        return False
    if SequenceMatcher(None, a, b).ratio() >= thr:
        return True
    ca, cb = Counter(a), Counter(b)
    common = sum((ca & cb).values())
    return 2 * common / (len(a) + len(b)) >= core

# Пусто нарочно: какие названия у юзера трудные — знает только он, а неверный термин
# здесь хуже отсутствующего (подменит НЕ ТО слово в субтитрах). Список набирается в UI.
DEFAULT_TERMS = []

_LOCK = threading.RLock()
_CACHE = {"mtime": -1, "data": None}


def _norm(s):
    """Ключ сравнения: регистр, «ё», дефисы и знаки не считаются. «TB-500», «tb 500»
    и «ТБ500» должны попадать в один ключ — иначе вариантов пришлось бы заводить по
    десятку на термин."""
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я\s]+", "", s)
    return " ".join(s.split())


def _norm_tight(s):
    """То же без пробелов: «ти би 500» и «тиби500» — одно и то же."""
    return _norm(s).replace(" ", "")


def load():
    """{"terms": [{"term": str, "variants": [str]}]}. Файла нет — стартовый список."""
    with _LOCK:
        try:
            mt = os.path.getmtime(TERMS_PATH)
        except OSError:
            return {"terms": [{"term": t, "variants": []} for t in DEFAULT_TERMS]}
        if _CACHE["data"] is None or _CACHE["mtime"] != mt:
            try:
                d = json.load(open(TERMS_PATH, encoding="utf-8"))
            except Exception:
                return {"terms": []}
            items = []
            for it in (d.get("terms") or []):
                if isinstance(it, str):
                    items.append({"term": it, "variants": []})
                elif isinstance(it, dict) and (it.get("term") or "").strip():
                    items.append({"term": it["term"].strip(),
                                  "variants": [str(v).strip() for v in (it.get("variants") or [])
                                               if str(v).strip()]})
            _CACHE["data"], _CACHE["mtime"] = {"terms": items}, mt
        return _CACHE["data"]


def save(data):
    with _LOCK:
        atomic_json_dump(TERMS_PATH, {"terms": data.get("terms") or []}, indent=1)
        _CACHE["data"] = None            # перечитаем с диска (mtime сменился)


def set_terms(items, emit=None):
    """Записать список целиком (правка из UI). items: [{term, variants}] или [str].
    Варианты, уже накопленные обучением, сохраняем: юзер редактирует НАЗВАНИЯ, а не
    список ослышек — стерев их случайно, он потерял бы всю память правок."""
    emit = wrap_emit(emit)
    old = {_norm(it["term"]): it.get("variants") or [] for it in load()["terms"]}
    out, seen = [], set()
    for it in (items or []):
        term = (it if isinstance(it, str) else (it.get("term") or "")).strip()
        if not term:
            continue
        key = _norm(term)
        if key in seen:
            continue
        seen.add(key)
        given = [] if isinstance(it, str) else [str(v).strip() for v in (it.get("variants") or [])
                                                if str(v).strip()]
        keep = given or old.get(key) or []
        out.append({"term": term, "variants": keep[:MAX_VARIANTS]})
    save({"terms": out})
    if emit:
        emit("словарь терминов: {count}", count=len(out))
    return out


MAX_NGRAM = 4             # длиннее цепочки слов под один термин не бывает


def _index():
    """(варианты {слитный ключ: термин}, термины [(ключ, tight, термин)], окно поиска).

    Ключ СЛИТНЫЙ (без пробелов): ASR разбивает название пробелами где попало — «ГХК-ЦУ»
    приезжает как «гхк цу». Иначе на каждый термин пришлось бы заводить вариант на любую
    расстановку пробелов."""
    var, terms, maxn = {}, [], 1
    for it in load()["terms"]:
        term = it["term"]
        for k in [term] + list(it.get("variants") or []):
            nk = _norm_tight(k)
            if not nk:
                continue
            var[nk] = term
            maxn = max(maxn, len(_norm(k).split()))
        terms.append((_norm(term), _norm_tight(term), term))
    # окно всегда не меньше 3 слов: у ключа из одного «слова» («гхкцу») в ленте может
    # оказаться два-три токена, и по числу слов в ключе это не угадать
    return var, terms, min(MAX_NGRAM, max(3, maxn))


def _fuzzy(nw, terms):
    """Самый похожий термин на нормализованное слово или None. Сравниваем и по
    «слитному» ключу: ASR любит разбивать «GHK Cu» пробелом там, где его нет."""
    if len(nw) < MIN_FUZZY_LEN:
        return None
    tw = nw.replace(" ", "")
    best, best_s = None, FUZZY_THR
    for nt, tt, term in terms:
        if not nt:
            continue
        s = max(SequenceMatcher(None, nw, nt).ratio(),
                SequenceMatcher(None, tw, tt).ratio())
        if s >= best_s:
            best, best_s = term, s
    return best


_TAIL = re.compile(r"[.,!?;:…»)\"']+$")


def _tail_of(w):
    m = _TAIL.search(str(w or ""))
    return m.group(0) if m else ""


def fix_words(words, emit=None):
    """Пословную ленту ASR ([{w,start,end}, ...]) прогнать через словарь. Меняет ТЕКСТ,
    тайминги не трогает; при склейке нескольких слов в один термин берём start первого
    и end последнего. Возвращает НОВЫЙ список (входной не мутируем — его кэшируют)."""
    emit = wrap_emit(emit)
    var, terms, maxn = _index()
    if not var or not words:
        return list(words or [])
    out, i, n_fix = [], 0, 0
    while i < len(words):
        hit = None
        via_variant = False
        # длинные цепочки вперёд коротких: «ти би пятьсот» должно выиграть у «ти»
        for n in range(min(maxn, len(words) - i), 0, -1):
            chunk = words[i:i + n]
            key = _norm_tight(" ".join(str(w.get("w") or "") for w in chunk))
            if key and key in var:
                hit = (var[key], n, chunk)
                via_variant = True
                break
        if hit is None:                                  # ступень 2 — похожесть, по одному слову
            w0 = words[i]
            nw = _norm(w0.get("w"))
            t = _fuzzy(nw, terms) if nw else None
            if t and _norm(t) != nw:
                hit = (t, 1, [w0])
        if hit:
            term, n, chunk = hit
            was = " ".join(str(w.get("w") or "") for w in chunk)
            new = dict(chunk[0])
            new["w"] = term + _tail_of(chunk[-1].get("w"))
            new["start"] = chunk[0].get("start")
            new["end"] = chunk[-1].get("end")
            out.append(new)
            i += n
            if new["w"] != was:            # термин уже написан верно — это не правка
                n_fix += 1
                if emit:
                    # «(вариант)» — подмена по запомненной ослышке, а не по похожести:
                    # такие строки пользователь может спутать с правкой панели слов
                    if via_variant:
                        emit("  термин: «{was}» -> «{new}» (вариант)", was=was, new=new['w'])
                    else:
                        emit("  термин: «{was}» -> «{new}»", was=was, new=new['w'])
            continue
        out.append(dict(words[i]))
        i += 1
    if n_fix and emit:
        emit("словарь терминов: исправлено слов — {count}", count=n_fix)
    return out


def _collides(nw, term_item):
    """Похожа ли ослышка на ЧУЖОЙ термин: на его имя, на его варианты или на
    транслит его имени. Имя в латинице с кириллической ослышкой буквенно не совпадает
    никогда, поэтому транслит обязателен: «модси» против «MOTS-C» -> «мотск» — общие
    буквы «м,о,с», и это коллизия (модси — ослышка MOTS-C, а не ДСИП)."""
    if _similar(nw, _norm(term_item["term"])):
        return True
    if any(_similar(nw, _norm(v)) for v in term_item["variants"]):
        return True
    t = _translit(term_item["term"])
    return bool(t) and _similar(nw, t)


def learn(wrong, right):
    """Ручная правка слова -> запомнить ослышку вариантом термина.
    Учимся ТОЛЬКО когда исправленный текст — уже известный термин: иначе в словарь
    поедут обычные опечатки, а он должен оставаться списком названий.
    -> имя термина, если что-то запомнили, иначе None."""
    nw, nr = _norm(wrong), _norm(right)
    if not nw or not nr or nw == nr:
        return None
    with _LOCK:
        data = load()
        items = [dict(it, variants=list(it.get("variants") or [])) for it in data["terms"]]
        tgt = next((it for it in items
                    if _norm(it["term"]) == nr
                    or nr in {_norm(v) for v in it["variants"]}), None)
        if tgt is None:
            return None
        if nw in {_norm(v) for v in tgt["variants"]}:
            return None                                  # уже запомнили эту ослышку
        # ослышка не должна принадлежать другому термину — иначе один термин начнёт
        # затирать другой (частая пара: «модси» — ослышка MOTS-C, и у ДСИП ей делать
        # нечего). Правило одностороннее: сомневаешься — не запоминай, ложный вариант
        # подменит НЕ ТО слово в субтитрах.
        for o in items:
            if o is not tgt and _collides(nw, o):
                return None
        tgt["variants"] = ([str(wrong).strip()] + tgt["variants"])[:MAX_VARIANTS]
        save({"terms": items})
        return tgt["term"]
