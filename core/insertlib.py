# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""insertlib.py — база использованных вставок (фото/видео) с индексом и автоподбором.

Идея: юзер годами копит удачные вставки в старых проектах. Сканируем прошлые XML
(parse_full -> inserts -> media-пути) и/или просто папки с медиа, строим индекс
insertlib.json (рядом с модулем). После генерации ИИ-вставок (query на английском)
подбираем из базы ближайший файл — семантически (embeddings LM Studio, у юзера
nomic-embed-text-v2) либо, без эмбеддера, токенным матчем по имени файла/папке.

API:
    build_index(dirs, emit=print)      -> dict(count=..., emb=bool)  # скан + сохранение
    match(query, k=5)                  -> [ {path,name,type,score,used}, ... ]
    match_many(queries, k=3)           -> [ [ ... ], ... ]           # 1 batch-запрос эмбеддера
    info()                             -> dict(count, dirs, emb_model, built)
    import_media(dirs, dest, since_ts) -> перенос медиа в СВОЮ папку базы (photos/videos)
    auto_describe(emit)                -> vision-описания (LM Studio qwen-vl) для всех без описания
    set_desc(path, desc)               -> ручная правка описания (+ пересчёт эмбеддинга)
    stats(emit=print)                  -> инвентаризация текущего индекса (только чтение)

CLI:  python insertlib.py scan <dir1> <dir2> ...
      python insertlib.py match "liver 3d icon"
      python insertlib.py import <since YYYY-MM-DD> <dest> <src1> [src2 ...]
      python insertlib.py describe
      python insertlib.py stats
"""
import os, re, json, math, threading, time, urllib.request
from collections import Counter
import numpy as np
from core.fileio import atomic_json_dump

from core import paths
from core.app_meta import env, console_emit, http_req, wrap_emit

# Индекс базы вставок. REELSI_INSERTLIB — как REELSI_UI_STATE у состояния UI: без него
# тестовый профиль (launch.json, 5098) пересканирует БОЕВОЙ индекс и перепишет его файл,
# хотя заявлен как «рабочее состояние не трогает». 2026-07-28: один клик «Обновить базу»
# на тестовом порту переписал рабочий insertlib.json.
INDEX_PATH = env("INSERTLIB") or paths.root("insertlib.json")

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1").rstrip("/")
# точное имя ключа модели в LM Studio может отличаться — ищем по подстроке 'embed' в /models
EMB_MODEL_ENV = os.environ.get("LMSTUDIO_EMB_MODEL", "")

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
# служебное — не вставки: переходы/звуки, рото-кэш, черновики, исходники камер
SKIP_NAME = re.compile(r"(quick\s*\d|whoosh|riser|highlight_pop|\.draft\.|_tmp|^roto_\d+|^mask_\d+"
                       r"|^(c\d{3,4}|dji_|img_|gx\d{6})[\w-]*\.(mp4|mov|mxf)$)", re.I)
SKIP_DIR = re.compile(r"[\\/](roto|_tmp|\.git|__pycache__)([\\/]|$)", re.I)
MAX_VIDEO_MB = 300                                   # видео-вставка больше — почти наверняка исходник


# ---------- текст из пути ----------
def _norm_text(path):
    """Имя файла + родительская папка -> поисковый текст: разделители/ькамелкейс в пробелы."""
    base = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    s = base + " " + parent
    s = re.sub(r"[\\_\-\.\(\)\[\]{}+,]", " ", s)
    s = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", s)          # camelCase -> camel Case
    s = re.sub(r"(?<=[A-Za-zА-Яа-я])(?=\d)|(?<=\d)(?=[A-Za-zА-Яа-я])", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _norm_filename(path):
    """Нормализация имени файла без расширения (разделители и camelCase в пробелы)."""
    base = os.path.splitext(os.path.basename(path or ""))[0]
    s = re.sub(r"[\\_\-\.\(\)\[\]{}+,]", " ", base)
    s = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", s)
    s = re.sub(r"(?<=[A-Za-zА-Яа-я])(?=\d)|(?<=\d)(?=[A-Za-zА-Яа-я])", " ", s)
    return s.lower()


def _tokens(s):
    return set(w for w in re.split(r"[^a-zа-яё0-9]+", (s or "").lower()) if len(w) >= 2)


def _norm_look(text):
    """Нормализация приписки стиля картинки (поле look, задание ET1): strip, нижний
    регистр, схлопнутые пробелы; из пустого — пустая строка. Единственное место
    нормализации стиля: приписки из image_prompts спикеров отличаются регистром и
    лишними пробелами, и если запись и запрос нормализовать по-разному, одинаковые
    стили не совпадут."""
    return " ".join((text or "").strip().lower().split())


_WORD_RE = re.compile(r"[a-zа-яё]{3,}", re.I)
_SIGNIFICANT_WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.I)


def _field_unfit(value, path):
    """Пригодность ОДНОГО текстового поля (desc/vis) для поиска — правила те же, что
    были у desc: пусто, равно имени файла (_norm_text), меньше двух значимых слов
    (слово = [a-zа-яё]{3,} без учёта регистра). True = поле непригодно."""
    text = (value or "").strip()
    if not text:
        return True
    if text == _norm_text(path or ""):
        return True
    if len(_WORD_RE.findall(text)) < 2:
        return True
    return False


_REFUSAL_PREFIXES = ("none of", "none of the above", "i cannot", "i can't",
                     "unable to", "sorry", "no objects", "there is no", "there are no")


def _is_refusal(text) -> bool:
    """Отказ vision-модели: ответ начинается с типовой фразы отказа (без учёта регистра).
    Единственный источник правды для этого понятия. Отказ хуже пустого: он попадает в
    эмбеддинг и тянет к себе чужие запросы, поэтому везде считается отсутствием описания."""
    t = (text or "").strip().lower()
    return any(t.startswith(p) for p in _REFUSAL_PREFIXES)


def needs_text(it) -> bool:
    """Предикат пригодности ТЕКСТА записи для поиска (единственный источник правды).
    Истина, если запись нуждается в описании: непригодны ОБА поля — и desc (что
    задумано), и vis (что видно на картинке). Исключение: desc_src == 'user' — ВСЕГДА
    пригодно (False): человек правил руками, не переписывать.
    """
    if it.get("desc_src") == "user":
        return False
    return (_field_unfit(it.get("desc"), it.get("path"))
            and _field_unfit(it.get("vis"), it.get("path")))


# Старое имя предиката — после EP («два поля вместо одного») семантически это needs_text.
needs_desc = needs_text


def needs_vis(it) -> bool:
    """У записи нет пригодного vis (что ВИДНО на картинке). По нему
    auto_describe(only_missing=True) отбирает работу: vision дописывает зрительное
    описание даже тем файлам, у которых уже есть исходная фраза (desc). Отказ модели
    в vis — тоже отсутствие описания: запись вернётся в работу обычного «Обновить базу»."""
    v = it.get("vis")
    return _field_unfit(v, it.get("path")) or _is_refusal(v)


def _doc_text(desc, ru="", vis=""):
    """Текст документа для эмбеддера: непустые из desc, ru, vis через пробел в порядке
    «что задумано — первым», лишние пробелы схлопнуты. vis участвует, только если это
    не отказ модели: мусор не должен попадать в вектор до переописания."""
    parts = [p for p in ((desc or "").strip(), (ru or "").strip()) if p]
    v = (vis or "").strip()
    if v and not _is_refusal(v):
        parts.append(v)
    return " ".join(" ".join(parts).split())


# ---------- как именно текст едет в эмбеддер ----------
# Версия схемы эмбеддинга. Меняется -> индекс переэмбеддится (иначе запрос по новой
# схеме сравнивался бы с документами по старой = мусор).
EMB_TAG = "q5"

# Вес лексического бонуса по редкости слов (0.35)
LEX_W = 0.35

# Пороги автоподбора вставок (единственный источник правды для UI и API).
# AUTO_COS: 0.30 подобран замером на 194 реальных запросах под схему эмбеддинга q2+
# (nomic-префиксы + чистка стиля из запроса): косинусы стали ниже и честнее, >=0.30 проходит 73%,
# и почти весь мусор («chicken breast» -> бургер) остаётся ниже 0.28.
# AUTO_LEX: самостоятельный порог лексического совпадения (0.20). Появился, так как замер на 1317 реальных
# запросах показал: у 39 запросов top-1 имел косинус ниже 0.30 при lex >= 0.20 (буквальные совпадения
# имени файла вроде «revolver cylinder» -> revolver_cylinder.mp4, «broken piggy bank with coins» ->
# broken_piggy_bank_with_coins.mp4, «retatrutide injection pen» -> 20-retatrutide-12mg_2.png, где
# vision-описание сбило косинус, а имя файла говорит ровно то, что просили).
AUTO_COS = 0.30       # порог косинуса при живом эмбеддере
AUTO_COS_TOK = 0.5    # он же для токенного фолбэка (эмбеддера нет)
AUTO_LEX = 0.20       # самостоятельный порог лексического совпадения

# Слова СТИЛЯ, а не предмета. У юзера ~все запросы вида «broken eyeglasses 3d icon»,
# а описания в базе — «3D render of ...». Стиль есть с обеих сторон, поэтому вектор
# запроса на треть состоит из «3d icon» — и «fried egg 3d icon» находил «broken mirror
# 3d icon» с 0.67 (проверено на реальной базе). Из ЗАПРОСА стиль вырезаем: искать надо
# предмет.
_STYLE = re.compile(r"\b(3d|icon|render|rendering|stock|photo|footage|illustration|"
                    r"closeup|close-up|graphic|style|image)\b", re.I)

# Полный стилевой и цветовой список для предметной части описания и фильтрации значимых слов
STYLE_WORDS_LIST = [
    "3d", "icon", "render", "rendering", "stock", "photo", "photography",
    "footage", "illustration", "image", "closeup", "close", "up", "view",
    "macro", "microscopic", "detailed", "medical", "style", "tones", "tone",
    "glowing", "background", "backdrop", "surface", "white", "black", "dark",
    "bright", "light", "blue", "red", "green", "yellow", "orange", "purple",
    "pink", "brown", "golden", "gold", "grey", "gray", "silver", "warm",
    "cold", "neutral", "colorful", "vibrant", "clean", "modern", "minimal",
    "realistic", "professional",
]
STYLE_WORDS_SET = {w.lower() for w in STYLE_WORDS_LIST}
STYLE_CLEAN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(STYLE_WORDS_LIST, key=len, reverse=True)) + r")\b",
    re.I,
)


def _subject_text(text):
    """Предметная часть описания (без слов стиля и цвета) для эмбеддера."""
    t = (text or "").strip()
    if not t:
        return ""
    cleaned = " ".join(STYLE_CLEAN_RE.sub(" ", t).split())
    return cleaned or t


def _significant_words(text):
    """Значимые слова: [a-zа-яё0-9]{3,}, не из STYLE_WORDS_SET, не чисто цифровые."""
    if not text:
        return set()
    words = set()
    for m in _SIGNIFICANT_WORD_RE.finditer(text):
        w = m.group().lower()
        if w not in STYLE_WORDS_SET and not w.isdigit():
            words.add(w)
    return words


def _cand_words(it):
    """Множество значимых слов кандидата: имя файла + desc + ru + vis."""
    fn_text = _norm_filename(it.get("path") or "")
    desc_text = it.get("desc") or ""
    ru_text = it.get("ru") or ""
    vis_text = it.get("vis") or ""
    return _significant_words(f"{fn_text} {desc_text} {ru_text} {vis_text}")


def _lex_bonus(q_words, c_words, df, n_total):
    """Вычисление лексического бонуса для кандидата:
    idf(t) = ln(1 + N / (1 + df(t)))
    bonus = LEX_W * (сумма idf совпавших слов запроса) / (сумма idf всех значимых слов запроса)
    """
    if not q_words or n_total <= 0:
        return 0.0
    q_idfs = {w: math.log(1.0 + n_total / (1.0 + df.get(w, 0))) for w in q_words}
    denom = sum(q_idfs.values())
    if denom <= 0:
        return 0.0
    matched = sum(q_idfs[w] for w in q_words if w in c_words)
    return LEX_W * (matched / denom)


# Поправки РАНГА за стиль картинки (поле look, задание ET1). Правят rank, а не score:
# подбор остаётся «по предмету», стиль лишь двигает порядок в выдаче.
LOOK_MATCH = 0.05
LOOK_MISS = -0.15


def _look_rank(cand_look, want_look):
    """Поправка ранга за стиль. want_look — уже нормализованный стиль текущего спикера
    (в записи индекса look хранится тоже нормализованным, через _norm_look). Совпал ->
    +0.05, у кандидата пусто -> 0 (общая картинка со стока годится всем), чужой непустой
    -> -0.15. Это поправка ПОРЯДКА, а не фильтр: кандидат с чужим стилем обязан остаться
    в выдаче — жёсткий фильтр пробовали для типа вставки 2026-07-30 и откатили,
    карточки оставались пустыми."""
    if not want_look:
        return 0.0
    cand_look = (cand_look or "").strip()
    if cand_look == want_look:
        return LOOK_MATCH
    return 0.0 if not cand_look else LOOK_MISS


def _q_text(q):
    """Текст запроса для эмбеддера: без слов стиля (если после чистки пусто — как было)."""
    return " ".join(_STYLE.sub(" ", q or "").split()) or (q or "")


def _is_nomic(model):
    return "nomic" in (model or "").lower()


def _emb_docs(texts, model):
    """Эмбеддинги ОПИСАНИЙ (сторона документа)."""
    if _is_nomic(model):                       # nomic-embed обучен на этих префиксах:
        texts = ["search_document: " + (t or "") for t in texts]   # без них асимметричный
    return _embed(texts, model)                # поиск «короткий запрос -> описание» плывёт


def _emb_queries(queries, model):
    """Эмбеддинги ЗАПРОСОВ: чистка стиля + свой префикс nomic."""
    qs = [_q_text(q) for q in queries]
    if _is_nomic(model):
        qs = ["search_query: " + q for q in qs]
    return _embed(qs, model)


# ---------- LM Studio embeddings ----------
def _http_json(url, payload=None, timeout=60):
    req = http_req(url, headers={"Content-Type": "application/json"},
                   data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _emb_model():
    """Ключ embedding-модели: env или первый /models с 'embed' в id. None = эмбеддера нет."""
    if EMB_MODEL_ENV:
        return EMB_MODEL_ENV
    try:
        d = _http_json(LMSTUDIO_URL + "/models", timeout=5)
        for m in d.get("data", []):
            if "embed" in (m.get("id") or "").lower():
                return m["id"]
    except Exception:
        pass
    return None


def _embed(texts, model):
    """Эмбеддинги батчем. -> list[list[float]] | None (эмбеддер недоступен)."""
    if not model or not texts:
        return None
    out = []
    try:
        for i in range(0, len(texts), 64):
            d = _http_json(LMSTUDIO_URL + "/embeddings",
                           {"model": model, "input": texts[i:i + 64]}, timeout=120)
            rows = sorted(d.get("data", []), key=lambda x: x.get("index", 0))
            if len(rows) != len(texts[i:i + 64]):
                return None
            out.extend(r["embedding"] for r in rows)
        return out
    except Exception:
        return None


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return s / (na * nb)


# ---------- скан ----------
def _media_kind(path):
    e = os.path.splitext(path)[1].lower()
    if e in IMG_EXT:
        return "photo"
    if e in VID_EXT:
        return "video"
    return None


def _scan_xml_inserts(xml_path):
    """Медиа вставок из XML прошлого проекта (дорожки над камерами). -> [path, ...]"""
    try:
        from core import xml2ae
        _meta, _cams, _subs, inserts = xml2ae.parse_full(xml_path)
        return [x["media"] for x in (inserts or []) if x.get("media")]
    except Exception:
        return []


def scan(dirs, emit=None):
    """Собрать медиа вставок: из всех .xml в папках (что реально использовалось) и
    медиафайлы, лежащие в папках напрямую (пополняемая библиотека).
    -> dict path -> {type, used(раз использовано в проектах), src}"""
    emit = wrap_emit(emit)
    items = {}

    def _add(p, src):
        p = os.path.abspath(p)
        k = _media_kind(p)
        if not k or SKIP_NAME.search(os.path.basename(p)) or SKIP_DIR.search(p) \
           or not os.path.isfile(p):
            return
        if k == "video":
            try:
                if os.path.getsize(p) > MAX_VIDEO_MB * 1024 * 1024:
                    return
            except OSError:
                return
        it = items.setdefault(p, {"type": k, "used": 0, "src": src})
        if src == "xml":
            it["used"] += 1

    for d in dirs:
        d = (d or "").strip().strip('"')
        if not os.path.isdir(d):
            emit("⚠ нет папки: {dir}", dir=d)
            continue
        nx = nm = 0
        for root, _dn, fns in os.walk(d):
            for fn in fns:
                p = os.path.join(root, fn)
                if fn.lower().endswith(".xml"):
                    for mp in _scan_xml_inserts(p):
                        _add(mp, "xml")
                    nx += 1
                else:
                    _add(p, "dir")
                    nm += 1
        emit("{dir}: XML {nx}, медиа-файлов рядом {nm}", dir=d, nx=nx, nm=nm)
    return items


def _merge_prev_records(records):
    """Схлопывание нескольких прежних записей одного пути (после pkey):
    - desc/desc_src: побеждает запись с пригодным desc (см. _field_unfit); если таких
      несколько — та, у которой desc_src != 'name';
    - used: максимум;
    - rej: объединение списков без повторов;
    - mw/mh, ru, vis, added: из той записи, где они непустые (при споре — из победившей по desc);
    - emb: от победившей по desc записи, но только если её desc+ru+vis совпадают с итоговыми;
      иначе None.
    """
    if len(records) == 1:
        return dict(records[0])

    def _desc_rank(r):
        return (
            not _field_unfit(r.get("desc"), r.get("path") or ""),
            (r.get("desc_src") or "name") != "name",
            bool((r.get("desc") or "").strip()),
        )

    desc_winner = max(records, key=_desc_rank)
    desc = desc_winner.get("desc")
    desc_src = desc_winner.get("desc_src") or "name"

    used = max(int(r.get("used") or 0) for r in records)

    rej = []
    for r in records:
        for q in (r.get("rej") or []):
            if q and q not in rej:
                rej.append(q)

    def _field_val(r, k):
        v = r.get(k)
        if v is None:
            return None
        if k in ("ru", "vis"):
            return v if (v or "").strip() else None
        return v

    extra = {}
    for k in ("mw", "mh", "ru", "added", "vis"):
        v = _field_val(desc_winner, k)
        if v is not None:
            extra[k] = v
        else:
            for r in records:
                rv = _field_val(r, k)
                if rv is not None:
                    extra[k] = rv
                    break

    final_ru = extra.get("ru") or ""
    final_vis = extra.get("vis") or ""
    winner_ru = desc_winner.get("ru") or ""
    winner_vis = desc_winner.get("vis") or ""
    same_text = (
        (desc_winner.get("desc") or "") == (desc or "")
        and winner_ru == final_ru
        and winner_vis == final_vis
    )
    emb = desc_winner.get("emb") if same_text else None

    res = dict(desc_winner)
    res["desc"] = desc
    res["desc_src"] = desc_src
    res["used"] = used
    if rej:
        res["rej"] = rej
    else:
        res.pop("rej", None)
    for k in ("mw", "mh", "ru", "added", "vis"):
        if k in extra:
            res[k] = extra[k]
        else:
            res.pop(k, None)
    res["emb"] = emb
    return res


def build_index(dirs, emit=None, use_emb=True):
    """Скан + эмбеддинги + сохранение insertlib.json. Описания (desc), правленные руками
    в прошлом индексе, сохраняются; эмбеддинги пересчитываются только для новых/правленых."""
    emit = wrap_emit(emit)
    import copy
    with _LOCK:
        old = copy.deepcopy(_load() or {})
    old_grouped = {}
    for it in old.get("items", []):
        p = it.get("path")
        if p:
            k = paths.pkey(os.path.abspath(p))
            old_grouped.setdefault(k, []).append(it)
    old_items = {k: _merge_prev_records(recs) for k, recs in old_grouped.items()}

    stale = old.get("emb_tag") != EMB_TAG        # схема эмбеддинга сменилась -> всё пересчитать
    found = scan(dirs, emit)
    found_nc = {paths.pkey(os.path.abspath(p)) for p in found}
    items = []
    # 1. Живые файлы, найденные сканом
    for p, meta in sorted(found.items()):
        k = paths.pkey(os.path.abspath(p))
        prev = old_items.get(k) or {}
        if prev.get("desc"):
            desc = prev["desc"]
        elif prev.get("desc_src") == "name":
            desc = ""       # после миграции EP исходной фразы нет — имя файла в desc не подставляем
        else:
            desc = _norm_text(p)
        ru = prev.get("ru") or ""
        same_text = prev.get("desc") == desc and (prev.get("ru") or "") == ru
        it = {"path": p, "name": os.path.basename(p), "type": meta["type"],
              "used": max(meta["used"], int(prev.get("used") or 0)),  # переживает перенос файла
              "desc": desc, "desc_src": prev.get("desc_src") or "name",
              "emb": None if stale else (prev.get("emb") if same_text else None)}
        # рескан не должен терять брак, свежесть, запомненную форму маски (mw/mh),
        # русскую подпись (ru), зрительное описание (vis) и стиль картинки (look)
        for keep in ("rej", "added", "mw", "mh", "ru", "vis", "look"):
            if prev.get(keep):
                it[keep] = prev[keep]
        # файл найден на диске — если был gone, он снимается (в it поля gone нет)
        items.append(it)
    # 2. Прежние записи, которых скан не нашёл -> помечаем gone (не удаляем!)
    for k, prev in old_items.items():
        if k not in found_nc:
            it = dict(prev)
            if not it.get("gone"):
                it["gone"] = time.time()
            if stale:
                it["emb"] = None
            items.append(it)
    # Миграция EP/ES, одноразовая и идемпотентная: vision-описания прошлых прогонов жили
    # в desc с desc_src='ai'. Их место в vis, а desc честно пустой — исходной фразы у этих
    # файлов не было никогда. EP переносил desc->vis только когда vis пуст; на боевой базе
    # vision прогнали раньше скана, и у desc_src='ai' в desc остался текст слабой модели —
    # оба поля идут в эмбеддинг, значит desc чистим ВСЕГДА. После первого же прохода
    # desc_src == 'name', так что повторный скан ничего не меняет.
    for it in items:
        if it.get("desc_src") == "ai":
            if not (it.get("vis") or "").strip():
                it["vis"] = it.get("desc") or ""
            it["desc"] = ""
            it["desc_src"] = "name"
            it["emb"] = None
    model = _emb_model() if use_emb else None
    if model:
        need = [it for it in items if not it.get("emb") and not it.get("gone")]
        if need:
            emit("эмбеддинги: {count} новых текстов через {model}…", count=len(need), model=model)
            vecs = _emb_docs([_subject_text(_doc_text(it["desc"], it.get("ru"), it.get("vis"))) for it in need], model)
            if vecs:
                for it, v in zip(need, vecs):
                    it["emb"] = v
            else:
                emit("⚠ эмбеддер не ответил — работаю токенным матчем по именам")
                model = None
    else:
        emit("эмбеддер не найден (LM Studio /models без 'embed') — токенный матч по именам")

    # Слияние под локом: не затираем параллельные правки (rej, add_generated, set_desc),
    # сделанные другими между началом скана и сохранением
    with _LOCK:
        cur = _load() or {}
        cur_grouped = {}
        for it in cur.get("items", []):
            p = it.get("path")
            if p:
                k = paths.pkey(os.path.abspath(p))
                cur_grouped.setdefault(k, []).append(it)
        cur_items = {k: _merge_prev_records(recs) for k, recs in cur_grouped.items()}

        built_by_k = {}
        for it in items:
            p = it.get("path")
            if p:
                k = paths.pkey(os.path.abspath(p))
                built_by_k[k] = it

        # Записи, удалённые из cur за время скана (были в old, нет в cur), не воскрешать.
        # Если скан нашёл их заново на диске — они остаются как найденные.
        to_drop = {k for k in old_items if k not in cur_items and k not in found_nc}
        if to_drop:
            items = [it for it in items if paths.pkey(os.path.abspath(it.get("path") or "")) not in to_drop]
            built_by_k = {k: it for k, it in built_by_k.items() if k not in to_drop}

        # Записи cur, которых не было в old (добавлены за время скана, например add_generated)
        for k, cur_it in cur_items.items():
            if k not in old_items:
                if k in built_by_k:
                    it = built_by_k[k]
                    merged = dict(cur_it)
                    merged["used"] = max(int(it.get("used") or 0), int(cur_it.get("used") or 0))
                    if not os.path.isfile(merged.get("path") or ""):
                        if not merged.get("gone"):
                            merged["gone"] = time.time()
                    idx = items.index(it)
                    items[idx] = merged
                    built_by_k[k] = merged
                else:
                    new_it = dict(cur_it)
                    if not os.path.isfile(new_it.get("path") or ""):
                        if not new_it.get("gone"):
                            new_it["gone"] = time.time()
                    items.append(new_it)
                    built_by_k[k] = new_it

        # Для записей, которые есть и в old, и в cur: обновляем изменённые поля,
        # used берем как максимум из трёх; если текст изменился — сбрасываем emb
        fields_to_merge = ("rej", "desc", "desc_src", "ru", "vis", "look", "mw", "mh", "added")
        for k in old_items:
            if k in cur_items and k in built_by_k:
                it = built_by_k[k]
                old_it = old_items[k]
                cur_it = cur_items[k]
                comp_desc = it.get("desc") or ""
                comp_ru = it.get("ru") or ""
                comp_vis = it.get("vis") or ""

                for f in fields_to_merge:
                    if cur_it.get(f) != old_it.get(f):
                        if f in cur_it and cur_it[f] is not None:
                            it[f] = cur_it[f]
                        else:
                            it.pop(f, None)

                it["used"] = max(int(it.get("used") or 0), int(old_it.get("used") or 0), int(cur_it.get("used") or 0))

                if (it.get("desc") or "") != comp_desc or (it.get("ru") or "") != comp_ru or (it.get("vis") or "") != comp_vis:
                    it["emb"] = None

        data = {"dirs": [os.path.abspath(x.strip().strip('"')) for x in dirs if x.strip()],
                "emb_model": model or "", "emb_tag": EMB_TAG, "items": items}
        _save(data)
    emit("индекс: {count} файлов ({photos} фото, {videos} видео)",
         count=len(items),
         photos=sum(1 for i in items if i.get('type') == 'photo'),
         videos=sum(1 for i in items if i.get('type') == 'video'))
    return dict(count=len(items), emb=bool(model))


_CACHE = {"mtime": 0, "data": None, "mat": None, "mat_items": None, "df": None, "cand_words": None, "N": 0}
_LOCK = threading.RLock()        # сериализуем чтение/запись индекса при параллельной генерации


def _seed_index():
    """Тестовый профиль (AUTOCUT_INSERTLIB) без своего индекса — снять КОПИЮ с рабочего.
    Скан с нуля пересчитал бы эмбеддинги полутора тысяч файлов (минуты и VRAM), а
    работать поверх рабочего файла нельзя — ради этого разделение и заводилось."""
    real = paths.root("insertlib.json")
    if INDEX_PATH == real or os.path.exists(INDEX_PATH) or not os.path.isfile(real):
        return
    try:
        import shutil
        shutil.copyfile(real, INDEX_PATH)
    except Exception:
        pass


def _load():
    with _LOCK:
        _seed_index()
        try:
            st = os.stat(INDEX_PATH)
            mt = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        if _CACHE["data"] is None or _CACHE.get("mtime") != mt:
            try:
                _CACHE["data"] = json.load(open(INDEX_PATH, encoding="utf-8"))
                _CACHE["mtime"] = mt
                _CACHE["mat"] = None
                _CACHE["mat_items"] = None
                _CACHE["df"] = None
                _CACHE["cand_words"] = None
                _CACHE["N"] = 0
            except Exception:
                return None
        return _CACHE["data"]


def _matrix():
    """Строит из индекса (np.ndarray [N, D] нормализованных float32, list записей).
    Только записи, у которых есть emb и нет gone.
    Результат кэшируется и сбрасывается по mtime индекса и в _save().
    """
    with _LOCK:
        d = _load()
        if not d:
            return None, []
        if _CACHE.get("mat") is not None and _CACHE.get("mat_items") is not None:
            return _CACHE["mat"], _CACHE["mat_items"]
        items = [it for it in d.get("items", []) if it.get("emb") and not it.get("gone")]
        if not items:
            mat = np.empty((0, 0), dtype=np.float32)
            _CACHE["mat"] = mat
            _CACHE["mat_items"] = []
            _CACHE["df"] = Counter()
            _CACHE["cand_words"] = []
            _CACHE["N"] = 0
            return mat, []
        arr = np.asarray([it["emb"] for it in items], dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-9, norms)
        mat = (arr / norms).astype(np.float32)

        cand_words_list = [_cand_words(it) for it in items]
        df = Counter()
        for w_set in cand_words_list:
            for w in w_set:
                df[w] += 1

        _CACHE["mat"] = mat
        _CACHE["mat_items"] = items
        _CACHE["df"] = df
        _CACHE["cand_words"] = cand_words_list
        _CACHE["N"] = len(items)
        return mat, items


def info():
    d = _load()
    if not d:
        return dict(count=0, dirs=[], emb_model="", built=False)
    return dict(count=len(d.get("items", [])), dirs=d.get("dirs", []),
                emb_model=d.get("emb_model", ""), built=True)


def _score_tokens(q_toks, it):
    # Слова кандидата — по всем трём полям (desc + ru + vis), как в _cand_words: после
    # миграции EP vision-описания живут в vis, и у записей с пустым desc фолбэк
    # должен находить их по тому, что ВИДНО на картинке.
    toks = _tokens(" ".join(p for p in (it.get("desc"), it.get("ru"), it.get("vis")) if p))
    if not q_toks or not toks:
        return 0.0
    inter = len(q_toks & toks)
    return inter / max(1, len(q_toks))


def match_many(queries, k=5, type_hint=None, look=None):
    """Подбор по нескольким запросам сразу (эмбеддинги запросов — одним батчем).
    -> список результатов на каждый query: [{path,name,type,score,used,look}, ...].
    type_hint: 'photo'|'video'|None — МЯГКИЙ приоритет (+0.05 к score), не фильтр.
    Заявленный тип — пожелание, а не требование: если под запрос заметно лучше подходит
    видео, пусть едет видео (тип карточки чинится по файлу в insSetMedia). Жёсткий фильтр
    тут пробовали 2026-07-30 и откатили: он же резал и ручную выдачу 📚 — в вариантах не
    оставалось ничего, кроме одного типа.
    look: уже нормализованный стиль текущего спикера (см. _norm_look). Правит РАНГ, поле
    score не меняется вовсе: совпал +0.05, у кандидата пусто +0, чужой непустой -0.15."""
    d = _load()
    if not d or not d.get("items"):
        return [[] for _ in queries]
    _ensure_emb_tag()
    d = _load()
    items_all = d.get("items", [])
    model = d.get("emb_model") or None
    qvecs = _emb_queries(queries, model) if model else None
    pos = {id(it): n for n, it in enumerate(items_all)}        # порядок в индексе = «когда добавлен»
    mat, mat_items = _matrix() if qvecs else (None, [])
    out = []
    for qi, q in enumerate(queries):
        qn = _norm_q(q)
        scored = []
        used_emb = False
        if qvecs and mat is not None and len(mat_items) > 0:
            qv = qvecs[qi]
            if qv:
                qv_arr = np.asarray(qv, dtype=np.float32)
                q_norm = float(np.linalg.norm(qv_arr))
                qv_norm = qv_arr / (q_norm if q_norm > 0 else 1e-9)
                scores = mat.dot(qv_norm)
                df = _CACHE.get("df") or Counter()
                cand_words_list = _CACHE.get("cand_words") or [_cand_words(it) for it in mat_items]
                n_total = _CACHE.get("N") or len(mat_items)
                q_sig_words = _significant_words(q)
                for s, it, c_words in zip(scores, mat_items, cand_words_list):
                    if qn not in (it.get("rej") or []):
                        cos_sim = float(s)
                        bonus = _lex_bonus(q_sig_words, c_words, df, n_total)
                        rank = cos_sim + bonus
                        scored.append((rank, it, cos_sim, bonus))
                if scored:
                    used_emb = True
        if not scored:                                     # фолбэк — токены
            q_toks = _tokens(q)
            pool = [it for it in items_all if not it.get("gone") and qn not in (it.get("rej") or [])]
            for it in pool:
                s = _score_tokens(q_toks, it)
                if s > 0:
                    scored.append((s, it, s, 0.0))
            used_emb = False
        if type_hint:                                      # мягкий приоритет типа
            scored = [((rank + (0.05 if (it.get("type") or _media_kind(it["path"])) == type_hint
                                else 0.0)), it, cos_sim, lex) for rank, it, cos_sim, lex in scored]
        if look:                                           # мягкий приоритет стиля (ET1)
            scored = [((rank + _look_rank(it.get("look"), look)), it, cos_sim, lex)
                      for rank, it, cos_sim, lex in scored]
        # При РАВНОМ score/rank берём САМЫЙ СВЕЖИЙ: перегенерил тот же запрос -> в базе два
        # файла с одинаковым desc, а значит и одинаковым эмбеддингом (cos=1.0 между собой).
        # Без этого ключа побеждал добавленный первым — то есть ровно тот, который юзер
        # только что забраковал перегенерацией («выбирает старую фотку»).
        scored.sort(key=lambda x: (-x[0], -x[1].get("used", 0),
                                   -(x[1].get("added") or 0), -pos[id(x[1])]))
        # mw/mh — форма маски, с которой этот файл в последний раз УШЁЛ В ПРОЕКТ (см.
        # adopt). Отдаём вместе с путём: картинка из базы должна приезжать сразу
        # обрезанной как надо, а не подгоняться скрабберами заново в каждом ролике.
        # Проверка диска подряд до набора k живых записей
        cos_thr = AUTO_COS if used_emb else AUTO_COS_TOK
        res = []
        for rank, it, cos_sim, lex in scored:
            if os.path.isfile(it["path"]):
                is_auto = bool((cos_sim >= cos_thr) or (lex >= AUTO_LEX))
                res.append(dict(path=it["path"], name=it["name"], type=it["type"],
                                used=it.get("used", 0), score=round(cos_sim, 3),
                                lex=round(lex, 3),
                                auto=is_auto,
                                mw=it.get("mw"), mh=it.get("mh"), look=it.get("look")))
                if len(res) == k:
                    break
        out.append(res)
    return out


def _norm_q(q):
    return " ".join((q or "").lower().split())


def _ensure_emb_tag(emit=None):
    """Индекс собран по СТАРОЙ схеме эмбеддинга -> пересчитать описания одним батчем
    (~40 с на 1000 файлов, один раз). Полный рескан диска для этого не нужен: тексты
    описаний уже лежат в индексе. Молча выходим, если эмбеддера нет — тогда работает
    токенный фолбэк, а тег не трогаем (пересчитаем, когда эмбеддер поднимут)."""
    emit = wrap_emit(emit)
    with _LOCK:
        d = _load()
        if not d or d.get("emb_tag") == EMB_TAG:
            return False
        model = d.get("emb_model") or ""
        pairs = [(it.get("path"), it.get("desc") or "", it.get("ru") or "", it.get("vis") or "",
                  _subject_text(_doc_text(it.get("desc"), it.get("ru"), it.get("vis"))))
                 for it in d.get("items", [])
                 if _subject_text(_doc_text(it.get("desc"), it.get("ru"), it.get("vis")))]
    # эмбеддинг — вне лока (задание BU): сетевой вызов под _LOCK вешал бы всю базу
    # на 120 с, пока LM Studio занят
    if not model:
        model = _emb_model()
    if not model or not pairs:
        return False
    emit("база: схема эмбеддинга сменилась — пересчёт {count} описаний…", count=len(pairs))
    vecs = _emb_docs([t for _, _, _, _, t in pairs], model)
    if not vecs:
        return False
    with _LOCK:
        d = _load()
        if not d or d.get("emb_tag") == EMB_TAG:
            return False
        by_path = {paths.pkey(os.path.abspath(it.get("path") or "")): it
                   for it in d.get("items", [])}
        # между тактами индекс мог измениться — ищем запись заново по пути, и только
        # если описание/ru/vis не поменялись (иначе припишем чужой свежий вектор к новому тексту)
        for (p, dsc, ru, vis, _txt), v in zip(pairs, vecs):
            it = by_path.get(paths.pkey(os.path.abspath(p)))
            if it is not None and (it.get("desc") or "") == dsc and (it.get("ru") or "") == ru \
               and (it.get("vis") or "") == vis:
                it["emb"] = v
        d["emb_model"], d["emb_tag"] = model, EMB_TAG
        _save(d)
    emit("база: эмбеддинги пересчитаны")
    return True


def reject(path, query, on=True):
    """«Эта картинка не под этот запрос» — жмётся неявно, когда юзер перегенеривает
    поверх автоподбора/генерации. Файл остаётся в базе и доступен руками через 📚,
    но автоподбор по ЭТОМУ запросу его больше не предложит (под другие темы — сколько
    угодно, поэтому бракуем пару файл+запрос, а не файл целиком)."""
    qn = _norm_q(query)
    if not qn:
        return False
    # весь load-modify-save под локом (как add_generated/embed_items): _load() отдаёт
    # ОДИН и тот же закэшированный dict, и два параллельных запроса из UI (📚 + генерация)
    # затирали правку друг друга
    with _LOCK:
        data = _load()
        if not data:
            return False
        path = os.path.abspath(path)
        for it in data.get("items", []):
            if os.path.abspath(it["path"]) == path:
                rej = [r for r in (it.get("rej") or []) if r != qn]
                if on:
                    rej.append(qn)
                if rej:
                    it["rej"] = rej
                else:
                    it.pop("rej", None)
                _save(data)
                return True
    return False


def match(query, k=5, type_hint=None, look=None):
    return match_many([query], k=k, type_hint=type_hint, look=look)[0]


# ---------- импорт в СВОЮ папку базы ----------
def _save(data):
    with _LOCK:
        atomic_json_dump(INDEX_PATH, data)
        _CACHE["data"] = None                            # сбросить кэш
        _CACHE["mat"] = None
        _CACHE["mat_items"] = None
        _CACHE["df"] = None
        _CACHE["cand_words"] = None
        _CACHE["N"] = 0


_RB_SESSION = None                                       # сессия rembg (модель грузится 1 раз)
_RB_LOCK = threading.Lock()                              # создание сессии — под локом (1 раз)


def remove_bg(img_bytes, trim=True, emit=None):
    """«Remove Background» как в фотошопе: PNG с настоящей альфой вместо белого фона.
    Модель u2net (onnx, CPU ~1–2 с/шт) качается один раз в ~/.u2net при первом вызове.
    trim — обрезать полностью прозрачные поля (генератор оставляет широкие пустые
    рамки, а вставка в AE масштабируется по кадру — без обрезки предмет мелкий).
    Если модель съела всё (пустая альфа) — возвращаем исходник, лучше фон чем дырка."""
    emit = wrap_emit(emit)
    import io
    try:
        from rembg import remove, new_session
    except ImportError:
        raise SystemExit("для снятия фона нужен пакет rembg — «pip install rembg» "
                         "(или сними галку «убирать фон» в ⚙)")
    global _RB_SESSION
    if _RB_SESSION is None:
        with _RB_LOCK:                                   # double-checked locking
            if _RB_SESSION is None:
                _RB_SESSION = new_session()              # u2net по умолчанию (1 раз)
    from PIL import Image
    im = Image.open(io.BytesIO(remove(img_bytes, session=_RB_SESSION))).convert("RGBA")
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box is None:                                      # альфа пустая — фон не сняли
        emit("  фон снять не вышло (пустая альфа) — оставляю как есть")
        return img_bytes
    if trim and box != (0, 0, im.width, im.height):
        im = im.crop(box)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    emit("  фон убран, прозрачный PNG {width}x{height}", width=im.width, height=im.height)
    return buf.getvalue()


# --- Что читает After Effects. ЕДИНСТВЕННОЕ место: verify_jsx.py импортирует эти три
# набора отсюда, чтобы верификатор и конвертер to_ae_image не разъехались. ---
# Форматы, которые After Effects НЕ импортирует: webp и avif ему неизвестны совсем
# (без сторонних плагинов), и слой в композиции просто не создаётся — .jsx собирается,
# а картинки в проекте нет. Поэтому в базу и в сборку они попадают уже перекодированными.
AE_UNSUPPORTED = {".webp", ".avif"}

# А эти форматы AE открывает — но упирается не в контейнер, а в цветовую модель.
# CMYK-JPEG (Adobe, обычная штука на стоках и в типографских картинках) роняет весь
# скрипт прямо на importFile: «Unsupported video bit depth in source file», проект не
# собирается вовсе — это хуже пропавшего слоя. 2026-07-27: такой .jpg приехал в базу
# вставок и убил сборку 09_ng18.jsx. Проверяем режим, а не расширение.
AE_RASTER = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}
AE_OK_MODES = {"RGB", "RGBA", "L", "LA", "P", "PA", "1"}
AE_EXT_FORMAT = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


def image_real_format(path):
    """Реальный формат растровой картинки (PIL im.format), если он не совпадает
    с ожидаемым по расширению AE_EXT_FORMAT. При совпадении, неизвестном расширении
    или нечитаемом файле -> None."""
    ext = os.path.splitext(path)[1].lower()
    expected = AE_EXT_FORMAT.get(ext)
    if not expected:
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:               # только заголовок, пиксели не грузим
            fmt = im.format
            if fmt and fmt != expected:
                return fmt
    except Exception:
        return None
    return None


def to_ae_image(path, emit=None):
    """Перекодировать картинку в PNG, если её не понимает After Effects.
    Рядом с исходником кладём <имя>.png / <имя>-rgb.png (исходник не трогаем — он мог
    прийти из базы, и на него ссылаются прошлые проекты). Альфа сохраняется: webp с
    прозрачностью (типовой случай для вставок) остаётся прозрачным. -> путь, годный
    для AE. Читаемое остаётся как есть — конвертация не бесплатна."""
    emit = wrap_emit(emit)
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in AE_UNSUPPORTED:
        return _repng(path, os.path.splitext(path)[0] + ".png",
                      f"After Effects не читает {ext}", emit)
    if ext not in AE_RASTER:
        return path
    real_fmt = image_real_format(path)
    if real_fmt:
        return _repng(path, os.path.splitext(path)[0] + "-png.png",
                      f"расширение {ext}, а внутри {real_fmt}", emit)
    try:
        from PIL import Image
        with Image.open(path) as im:               # только заголовок, пиксели не грузим
            mode = im.mode
    except Exception:                              # битый файл — пусть ругается AE
        return path
    if mode in AE_OK_MODES:
        return path
    return _repng(path, os.path.splitext(path)[0] + "-rgb.png",
                  f"After Effects не читает {mode}", emit)


def _repng(src, dst, why, emit):
    """src -> PNG в dst с сохранением альфы. -> dst, а при неудаче src (ругнувшись)."""
    emit = wrap_emit(emit)
    if os.path.exists(dst):                        # уже перекодировали раньше
        return dst
    try:
        from PIL import Image
        with Image.open(src) as im:
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "PA", "P") else "RGB").save(dst, "PNG")
    except Exception as e:
        emit("  ⚠ {name}: не перекодировал в PNG ({err}) — {why}",
             name=os.path.basename(src), err=str(e), why=why)
        return src
    emit("  {name} → PNG ({why})", name=os.path.basename(src), why=why)
    return dst


# --- Видео AE тоже читает не всякое. Контейнер .mp4 ему знаком, а поток внутри — нет:
# AV1 (типовой для скачанного с YouTube) роняет importFile с «The source compression type
# is not supported» и обрывает ВЕСЬ .jsx на первом же импорте — ровно как CMYK-JPEG у
# картинок. 2026-08-01: `videoplayback (12).mp4` (av1) убил сборку AutoCut_all.jsx.
# Расширение тут не помогает — смотреть надо ВНУТРЬ файла, ffprobe'ом. ---
AE_BAD_VCODEC = {"av1", "vp8", "vp9", "theora"}


def _vcodec(path):
    """Кодек первой видеодорожки (ffprobe). -> имя в нижнем регистре | None если нечем."""
    import subprocess
    try:
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                            capture_output=True, text=True, timeout=60)
        return ((pr.stdout or "").strip().split(",")[0] or "").lower() or None
    except Exception:                                  # нет ffprobe/битый файл — пусть ругается AE
        return None


def to_ae_video(path, emit=None):
    """Перекодировать видео в H.264, если кодек не по зубам After Effects.
    Рядом с исходником кладём <имя>-h264.mp4 (исходник не трогаем — на него могли
    сослаться прошлые проекты и индекс базы). Читаемое возвращаем как есть:
    перекодировка дорогая. -> путь, годный для AE."""
    emit = wrap_emit(emit)
    path = os.path.abspath(path)
    if _media_kind(path) != "video":
        return path
    codec = _vcodec(path)
    if not codec or codec not in AE_BAD_VCODEC:
        return path
    dst = os.path.splitext(path)[0] + "-h264.mp4"
    if os.path.exists(dst):                            # уже перекодировали раньше
        return dst
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                        "-map", "0:v:0", "-map", "0:a?",
                        # crf 20: h264 сильно многословнее av1 (8 мин 1080p: 66 МБ -> 190),
                        # а вставка идёт 2-3 секунды и ужата в кадре — 18 тут только жрёт диск
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                        dst], check=True, capture_output=True, timeout=3600)
    except Exception as e:
        try:
            os.remove(dst)                             # недописанный огрызок хуже отсутствия
        except OSError:
            pass
        emit("  ⚠ {name}: не перекодировал из {codec} в H.264 ({err})",
             name=os.path.basename(path), codec=codec, err=str(e))
        return path
    emit("  {name} → H.264 (After Effects не читает {codec})",
         name=os.path.basename(path), codec=codec)
    return dst


def to_ae_media(path, emit=None):
    """Один вход для сборки: картинку чинит to_ae_image, видео — to_ae_video."""
    return to_ae_video(path, emit) if _media_kind(path) == "video" else to_ae_image(path, emit)


def strip_bg_file(path, dest_dir=None, emit=None):
    """Убрать фон у УЖЕ лежащего файла (кнопка на карточке вставки). Исходник не трогаем.
    dest_dir задан -> прозрачный <имя>-nobg.png кладём сразу в базу (<dest>/photos), а не
    рядом с исходником в «Скаченное»: чистая картинка нужна в базе, мусор в Downloads — нет.
    Если файл уже с альфой — вернём его же (нечего снимать)."""
    emit = wrap_emit(emit)
    path = os.path.abspath(path)
    if _media_kind(path) == "video":
        raise SystemExit("это видео — фон снимается только у фото")
    from PIL import Image
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 250:
            emit("  у файла уже есть прозрачность — пропуск")
            return path
    out = remove_bg(open(path, "rb").read(), emit=emit)
    stem = os.path.splitext(os.path.basename(path))[0]
    if dest_dir:
        outdir = os.path.join(os.path.abspath(dest_dir), "photos")
        os.makedirs(outdir, exist_ok=True)
    else:
        outdir = os.path.dirname(path)
    dst, n = os.path.join(outdir, stem + "-nobg.png"), 2
    while os.path.exists(dst) and os.path.abspath(dst) != path:
        dst = os.path.join(outdir, f"{stem}-nobg_{n}.png")
        n += 1
    open(dst, "wb").write(out)
    return os.path.abspath(dst)


def add_generated(img_bytes, query, dest_dir, emit=None, embed=True, ru="", look=""):
    """Сгенерённая картинка-вставка: сохранить в <dest_dir>/generated/ и дописать
    в индекс БЕЗ полного рескана (desc = query, ru = русская подпись, эмбеддинг сразу если эмбеддер жив) —
    следующие ролики найдут её автоподбором бесплатно. Возвращает путь.
    embed=False — не считать эмбеддинг сейчас (пакетная генерация делает его одним
    вызовом через embed_items после того, как сгенерятся все картинки).
    look — приписка стиля (image_prompts.a.extra спикера): пишется в запись индекса
    нормализованной (_norm_look), чтобы подбор отдавал спикерам картинки в их стиле."""
    emit = wrap_emit(emit)
    import hashlib, re as _re
    gen = os.path.join(dest_dir, "generated")
    os.makedirs(gen, exist_ok=True)
    # Имя всегда .png, поэтому и содержимое должно быть PNG: часть моделей отдаёт
    # JPEG (у них нет параметра output_format — например Nano Banana), а JPEG под
    # именем .png — мина для всего, что читает по расширению.
    if not img_bytes.startswith(b"\x89PNG"):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.open(io.BytesIO(img_bytes)).save(buf, format="PNG")
        img_bytes = buf.getvalue()
    slug = _re.sub(r"[^\w]+", "-", (query or "img").lower()).strip("-")[:48] or "img"
    h8 = hashlib.sha1(img_bytes).hexdigest()[:8]
    path = os.path.abspath(os.path.join(gen, f"{slug}-{h8}.png"))
    open(path, "wb").write(img_bytes)
    desc = query or slug
    ru = (ru or "").strip()
    doc_t = _subject_text(_doc_text(desc, ru))
    vec = None
    if embed:                                               # эмбеддинг — вне лока (задание BU)
        with _LOCK:                                         # короткое чтение модели под локом
            d0 = _load()
            model = (d0.get("emb_model") or "") if d0 else ""
        if model:
            vs = _emb_docs([doc_t], model)                   # сетевой вызов может висеть до 120 с
            if vs:
                vec = vs[0]
    with _LOCK:                                             # атомарно load+modify+save под локом
        data = _load()
        if data:
            items = data.setdefault("items", [])
            if not any(it["path"] == path for it in items):
                it = {"path": path, "name": os.path.basename(path), "type": "photo",
                      "used": 0, "desc": desc, "desc_src": "generated", "emb": vec,
                      "added": time.time()}   # свежесть: при равном score побеждает новый
                if ru:
                    it["ru"] = ru
                lk = _norm_look(look)
                if lk:
                    it["look"] = lk
                items.append(it)
                _save(data)
                emit("база: +{name} (generated)", name=os.path.basename(path))
        else:
            emit("база не построена — файл сохранён, в индекс попадёт при скане")
    return path


def embed_items(items, emit=None):
    """Дозаполнить эмбеддинги сгенерённым картинкам ОДНИМ вызовом _embed (вместо
    по одному на картинку). items: [(path, desc), ...] или [(path, desc, ru), ...].
    Возвращает число обновлённых."""
    emit = wrap_emit(emit)
    norm_items = [(os.path.abspath(elem[0]), elem[1], (elem[2] if len(elem) > 2 else ""))
                  for elem in (items or []) if elem and elem[0]]
    if not norm_items:
        return 0
    with _LOCK:
        data = _load()
        if not data:
            return 0
        model = data.get("emb_model") or ""
        by_p = {paths.pkey(os.path.abspath(it.get("path") or "")): it
                for it in data.get("items", [])}
        doc_texts = []
        for p, d, r in norm_items:
            cur = by_p.get(paths.pkey(p)) or {}
            cur_ru = r or (cur.get("ru") or "")
            cur_vis = cur.get("vis") or ""
            doc_texts.append(_subject_text(_doc_text(d or "", cur_ru, cur_vis)))
    if not model:
        return 0
    vecs = _emb_docs(doc_texts, model)   # сетевой вызов — вне лока (задание BU)
    if not vecs:
        return 0
    with _LOCK:
        data = _load()
        if not data:
            return 0
        # между тактами индекс мог измениться — ищем по пути заново
        idx = {os.path.abspath(it["path"]): it for it in data.get("items", [])}
        n = 0
        for (p, _, _), v in zip(norm_items, vecs):
            it = idx.get(p)
            if it is not None:
                it["emb"] = v
                n += 1
        if n:
            _save(data)
            emit("база: эмбеддинги дозаполнены для {count} сгенерённых картинок (пакетно)", count=n)
        return n


def _real_case(p):
    r"""Реальный регистр пути на диске (Windows case-insensitive ФС). Без \\?\ префикса."""
    try:
        r = os.path.realpath(p)
    except Exception:
        return p
    if r.startswith("\\\\?\\"):
        r = r[4:]
    return r


def _build_base_index(dest):
    """basename(lower) -> [абс. пути] для всех медиафайлов в базе (photos/videos, рекурсивно)."""
    idx = {}
    for sub in ("photos", "videos"):
        d = os.path.join(dest, sub)
        if not os.path.isdir(d):
            continue
        for root, _, fns in os.walk(d):
            for fn in fns:
                idx.setdefault(fn.lower(), []).append(os.path.join(root, fn))
    return idx


def _resolve_by_basename(name, base_idx, kind=None):
    """Найти файл по имени (без учёта регистра) внутри базы. Путь или None."""
    for cand in (base_idx.get((name or "").lower()) or []):
        ck = _media_kind(cand)
        if kind is None or ck is None or ck == kind:
            return cand
    return None


def _crop_of(it):
    """Форма маски вставки (ширина/высота кропа в % от авторасчёта) -> (mw, mh) или None.
    100/100 — это «как считает JSX сам», запоминать нечего."""
    try:
        mw = int(round(float(it.get("mw"))))
        mh = int(round(float(it.get("mh"))))
    except (TypeError, ValueError):
        return None
    if not (20 <= mw <= 300 and 20 <= mh <= 300) or (mw == 100 and mh == 100):
        return None
    return mw, mh


def adopt(items, dest_dir, emit=None):
    """Прибрать в базу файлы, которые РЕАЛЬНО ушли в проект (вызывается на сборке .jsx).
    items = [{"path":..., "desc":..., "mw":..., "mh":...}], desc = запрос вставки: с ним
    файл найдётся автоподбором в следующих роликах, без прогона «Описать через ИИ»;
    mw/mh — форма маски, с которой файл ушёл в проект (её же вернёт match_many).
    Файл переезжает в <dest>/photos|videos; уже лежащий в базе НЕ трогаем (ни в коем
    случае не создаём _2-дубли). Возвращает {старый_путь: новый_путь} для ВСЕХ файлов,
    чей путь изменился/нормализован (фронт по нему чинит media в своём состоянии).

    Важно (Windows): ФС case-insensitive (desktop==Desktop), а Python-сравнения — нет.
    Поэтому всюду сравниваем через paths.pkey, а итоговые пути отдаём в реальном
    регистре диска (через _real_case), чтобы .jsx/AE открывал файл."""
    emit = wrap_emit(emit)
    import shutil
    dest = _real_case(os.path.abspath(dest_dir))          # реальный регистр папки базы
    dest_nc = paths.pkey(dest)
    base_idx = _build_base_index(dest)                    # basename(lower) -> [пути в базе]
    mapping, seen, transferred, crops = {}, {}, {}, {}
    for it in items:
        p = (it.get("path") or "").strip().strip('"')
        if not p:
            continue
        p = os.path.abspath(p)
        kind = _media_kind(p)
        desc = (it.get("desc") or "").strip()
        ru = (it.get("ru") or "").strip()
        crop = _crop_of(it)
        p_nc = paths.pkey(p)
        # 1) уже в базе (без учёта регистра)? — НЕ двигаем, только нормализуем путь.
        # В mapping кладём ТОЛЬКО если путь реально изменился (регистр/слеши) —
        # контракт: mapping = «старый путь -> новый», identity-записей не плодим.
        if (p_nc == dest_nc) or p_nc.startswith(dest_nc + os.sep):
            real = _real_case(p) or p
            if real != p:
                mapping[p] = real                           # media -> реальный регистр
            seen[real] = {"desc": desc, "ru": ru}
            if crop:
                crops[real] = crop
            continue
        # 2) файл есть по точному пути? — переезжаем
        if kind and os.path.isfile(p):
            src = p
        else:
            # 3) файл куда-то перенесён: ищем по имени внутри базы (рекурсивно)
            src = _resolve_by_basename(os.path.basename(p), base_idx, kind)
            if src is None:
                emit("файл не найден (ни по точному пути, ни по имени в базе): {path}", path=p)
                continue
            src = os.path.abspath(src)
        # переезд в базу
        sk = _media_kind(src) or kind or "photo"
        sub = os.path.join(dest, "photos" if sk == "photo" else "videos")
        os.makedirs(sub, exist_ok=True)
        fn = os.path.basename(src)
        stem, ext = os.path.splitext(fn)
        tgt, n = os.path.join(sub, fn), 2
        # коллизия имён — БЕЗ учёта регистра: если файл с таким именем уже есть
        # (в т.ч. это тот же самый файл), _2 не плодим, просто указываем на него
        while True:
            if not os.path.exists(tgt):
                break
            if paths.pkey(tgt) == paths.pkey(src):
                break
            tgt = os.path.join(sub, "%s_%d%s" % (stem, n, ext))
            n += 1
        try:
            shutil.move(src, tgt)                          # move, не copy: Downloads чистится
        except Exception as e:
            emit("{name}: {err}", name=fn, err=str(e))
            continue
        real = _real_case(tgt) or tgt
        mapping[p] = real                                   # старый путь -> новый (реальный регистр)
        transferred[p] = real
        seen[real] = {"desc": desc, "ru": ru}
        if crop:
            crops[real] = crop
        emit("в базу: {name}", name=fn)
    if transferred:                                         # лог переносов (как у import_media)
        os.makedirs(dest, exist_ok=True)
        logp = os.path.join(dest, "_import_log.json")
        try:
            old = json.load(open(logp, encoding="utf-8"))
        except Exception:
            old = {}
        old.update(transferred)
        atomic_json_dump(logp, old, indent=1)
    if seen:
        _index_adopt(mapping, seen, emit, crops)
    if transferred:
        emit("прибрано в базу: {count} файлов -> {dest}", count=len(transferred), dest=dest)
    return mapping


def _index_adopt(mapping, seen, emit=None, crops=None):
    """Индекс после adopt: переехавшим чиним path (used/desc сохраняются), новых
    дописываем с desc=запрос и ru=русская подпись. Полного рескана не делаем.
    Сравнения — БЕЗ учёта регистра (Windows case-insensitive ФС: desktop==Desktop).

    crops {путь: (mw, mh)} — форма маски, с которой файл ушёл в проект. Пишем ПОВЕРХ
    прошлой: последняя ручная подгонка и есть правильная (юзер её только что видел
    в предпросмотре)."""
    emit = wrap_emit(emit)
    # правки путей и новых записей — под локом (как reject/add_generated): _load() отдаёт
    # общий закэшированный dict, и параллельный /api/rembg или генерация затирали правку.
    # Эмбеддинги новых описаний — ВТОРЫМ тактом, вне лока (задание BU): между тактами
    # индекс мог измениться, поэтому под финальным локом записи ищутся заново по пути.
    with _LOCK:
        data = _load()
        if not data:
            emit("база не построена — файлы перенесены, в индекс попадут при скане")
            return
        items = data.setdefault("items", [])
        # mapping: старый_путь(lower) -> новый_путь; индекс по path(lower) -> entry
        map_nc = {paths.pkey(k): v for k, v in mapping.items()}
        # 1) чиним path у переехавших (по старому пути, без учёта регистра)
        for it in items:
            newp = map_nc.get(paths.pkey(it.get("path") or ""))
            if newp:
                it["path"] = newp
                it["name"] = os.path.basename(newp)
        # 2) новые/обновляемые из seen (ключи — реальный регистр новых путей).
        #    by_nc строим ПОСЛЕ шага 1: построенный до него индексировал бы по СТАРЫМ
        #    путям, шаг 2 искал по новым -> промах -> на один файл дописывалась
        #    дубликат-запись с расщеплённым used (аудит, adopt)
        by_nc = {paths.pkey(it.get("path") or ""): it for it in items}
        model = data.get("emb_model") or ""
        fresh = []
        for path, sval in seen.items():
            desc = (sval.get("desc") if isinstance(sval, dict) else sval) or ""
            ru = (sval.get("ru") if isinstance(sval, dict) else "") or ""
            key = paths.pkey(path)
            it = by_nc.get(key)
            if it is None:
                it = {"path": path, "name": os.path.basename(path), "type": _media_kind(path),
                      "used": 0, "desc": desc, "desc_src": "adopted", "emb": None,
                      "added": __import__("time").time()}
                if ru:
                    it["ru"] = ru
                items.append(it)
                by_nc[key] = it
                doc_t = _subject_text(_doc_text(desc, ru))
                if doc_t:
                    fresh.append((path, desc, ru, doc_t))
            else:
                updated = False
                if desc and not (it.get("desc") or "").strip():
                    it["desc"], it["desc_src"] = desc, "adopted"   # был без описания — теперь с запросом
                    updated = True
                if ru and not (it.get("ru") or "").strip():
                    it["ru"] = ru
                    updated = True
                if updated:
                    doc_t = _subject_text(_doc_text(it.get("desc") or "", it.get("ru") or "",
                                                    it.get("vis") or ""))
                    if doc_t:
                        fresh.append((path, it.get("desc") or "", it.get("ru") or "", doc_t))
            cr = (crops or {}).get(path)
            if cr:
                it["mw"], it["mh"] = cr                        # форма маски на будущее
        _save(data)
    if model and fresh:                                    # эмбеддинги одним батчем, вне лока
        vecs = _emb_docs([t for _, _, _, t in fresh], model)
        if vecs:
            with _LOCK:
                data = _load()
                if data:
                    by_p = {paths.pkey(it.get("path") or ""): it
                            for it in data.get("items", [])}
                    for (path, dsc, ru, _txt), v in zip(fresh, vecs):
                        it = by_p.get(paths.pkey(path))
                        if it is not None and (it.get("desc") or "") == dsc and (it.get("ru") or "") == ru:
                            it["emb"] = v
                    _save(data)



def import_media(dirs, dest, since_ts=0.0, move=True, emit=None, recursive=False):
    """Перенести (move) медиа-вставки из папок-источников в СВОЮ папку базы:
    dest/photos и dest/videos. Берутся только файлы наших типов (фильтры как в scan)
    с mtime >= since_ts; по умолчанию БЕЗ подпапок (recursive=False — чтобы из Downloads
    не уехали вложенные Telegram Desktop и пр.). Пишется лог переносов dest/_import_log.json
    (old->new), пути в insertlib.json обновляются (used/desc сохраняются). -> dict(count, dest)."""
    emit = wrap_emit(emit)
    dest = os.path.abspath(dest)
    dest_nc = paths.pkey(dest)
    moved, mapping = 0, {}
    for d in dirs:
        d = (d or "").strip().strip('"')
        if not os.path.isdir(d):
            emit("⚠ нет папки: {dir}", dir=d)
            continue
        for root, dns, fns in os.walk(d):
            if not recursive:
                dns[:] = []
            root_nc = paths.pkey(os.path.abspath(root))
            if SKIP_DIR.search(root) or root_nc == dest_nc or root_nc.startswith(dest_nc + os.sep):
                dns[:] = []
                continue
            for fn in fns:
                p = os.path.join(root, fn)
                k = _media_kind(p)
                if not k or SKIP_NAME.search(fn):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < since_ts:
                    continue
                if k == "video" and st.st_size > MAX_VIDEO_MB * 1024 * 1024:
                    continue
                sub = os.path.join(dest, "photos" if k == "photo" else "videos")
                os.makedirs(sub, exist_ok=True)
                tgt = os.path.join(sub, fn)
                stem, ext = os.path.splitext(fn)
                n = 2
                while os.path.exists(tgt):                       # коллизия имён
                    if os.path.abspath(tgt) == os.path.abspath(p):
                        break
                    tgt = os.path.join(sub, f"{stem}_{n}{ext}")
                    n += 1
                if os.path.abspath(tgt) == os.path.abspath(p):
                    continue                                     # уже в базе
                import shutil
                try:
                    (shutil.move if move else shutil.copy2)(p, tgt)
                except Exception as e:
                    emit("⚠ {name}: {err}", name=fn, err=str(e))
                    continue
                mapping[os.path.abspath(p)] = tgt
                moved += 1
    if mapping:                                                  # лог + обновить пути в индексе
        os.makedirs(dest, exist_ok=True)
        logp = os.path.join(dest, "_import_log.json")
        try:
            old = json.load(open(logp, encoding="utf-8"))
        except Exception:
            old = {}
        old.update(mapping)
        atomic_json_dump(logp, old, indent=1)
        # весь load-modify-save под локом (как reject/add_generated): _load() отдаёт
        # ОБЩИЙ закэшированный dict, и параллельная правка описания затиралась бы
        with _LOCK:
            d = _load()
            if d:
                for it in d.get("items", []):
                    if it["path"] in mapping:
                        it["path"] = mapping[it["path"]]
                        it["name"] = os.path.basename(it["path"])
                _save(d)
    emit("перенесено {count} файлов -> {dest}", count=moved, dest=dest)
    return dict(count=moved, dest=dest, log=os.path.join(dest, "_import_log.json"))


# ---------- vision-описания (LM Studio, qwen-vl) ----------
# Замер на 40 файлах с известным ответом показал: прежняя gemma-4-e2b узнаёт предмет
# в 16 % случаев при ЛЮБОМ промпте (дело было в размере модели, не в формулировке),
# а qwen3-vl-8b даёт 26 % и читает названия прямо с упаковки. Модель выбирать не надо:
# _vision_model() уже отдаёт qwen3-vl-8b-instruct-abliterated (паттерн 'vl' идёт первым).
# Запрет называть стиль и цвета нужен потому, что эти слова засоряли описания
# ('photo' 486 раз на 1373 описания, цвета 964) и склеивали документы между собой
# в семантическом поиске.
# Строку про бренды убрали: она уводила модель в рассуждение о торговых марках, и на
# боевой базе вышло 11 % отказов вида «None of the objects...». Такой ответ хуже пустого —
# он попадает в эмбеддинг и тянет чужие запросы, поэтому запрет отказа обязателен.
_DESCRIBE_PROMPT = ("Name the MAIN OBJECT in this image as precisely as you can, in 4-10 English words. "
                    "Be specific: \"glucometer\", not \"device\"; \"blister pack of tablets\", not \"packaging\". "
                    "Always name what you see, even if it is abstract, a 3D render or an illustration. "
                    "Never answer that objects cannot be identified. Output ONLY the object phrase.")


VISION_MODEL_ENV = os.environ.get("LMSTUDIO_VISION_MODEL", "")


def _vision_model():
    """Ключ vision-модели: env, иначе первая подходящая из /models (vl/vision/gemma/llava…).
    Проверено: google/gemma-4-e2b видит изображения. Это reasoning-модель, поэтому в
    describe_file шлём явный запрет размышлений — иначе content приходил пустым."""
    if VISION_MODEL_ENV:
        return VISION_MODEL_ENV
    try:
        d = _http_json(LMSTUDIO_URL + "/models", timeout=5)
        ids = [m.get("id") or "" for m in d.get("data", [])]
        for pat in ("vl", "vision", "llava", "pixtral", "minicpm", "google/gemma", "gemma"):
            for i in ids:
                if pat in i.lower() and "embed" not in i.lower():
                    return i
    except Exception:
        pass
    return None


def _thumb_b64(path):
    """Кадр для vision: фото/гиф — сам файл, видео — кадр из середины; всё ужато до 512px
    (ffmpeg). -> base64 jpeg | None."""
    import subprocess, base64, tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    try:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if _media_kind(path) == "video":
            try:                                                # середина ролика
                pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
                mid = max(0.0, float((pr.stdout or "0").strip() or 0) / 2)
            except Exception:
                mid = 1.0
            cmd += ["-ss", f"{mid:.2f}", "-i", path, "-frames:v", "1"]
        else:
            cmd += ["-i", path, "-frames:v", "1"]
        cmd += ["-vf", "scale='min(512,iw)':-2", "-q:v", "5", tmp.name]
        subprocess.run(cmd, capture_output=True, timeout=60)
        with open(tmp.name, "rb") as f:
            b = f.read()
        return base64.b64encode(b).decode() if b else None
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def describe_file(path, model):
    """Одно vision-описание файла через LM Studio chat. -> str | None."""
    b64 = _thumb_b64(path)
    if not b64:
        return None
    try:
        d = _http_json(LMSTUDIO_URL + "/chat/completions", {
            "model": model, "max_tokens": 900, "temperature": 0.2,
            # «не прислали параметр» != «выключено»: без явного запрета reasoning-модель
            # (gemma и пр.) тратила весь бюджет на размышления и возвращала пустой content
            "reasoning": {"enabled": False},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _DESCRIBE_PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]},
            timeout=300)
        ch = ((d.get("choices") or [{}])[0])
        msg = (ch.get("message") or {})
        txt = msg.get("content") or ""
        # reasoning_content — МЫСЛИ модели, а не описание. Раньше сюда падала последняя
        # строка размышлений («Итак, судя по форме, это может быть…»), по ней считался
        # эмбеддинг, и файл навсегда всплывал в автоподборе не по теме: auto_describe
        # с only_missing=True его больше не трогает. Лучше вернуть None и переспросить.
        if not txt.strip():
            return None
        txt = " ".join(txt.replace("\n", " ").split()).strip().strip('."\'' )
        return txt[:220] or None
    except Exception:
        return None


def auto_describe(emit=None, only_missing=True, progress=None):
    """Vision-описания для всех файлов индекса (отбирает по needs_vis(it) при only_missing).
    Пишет ТОЛЬКО в vis: desc (исходная фраза/запрос) и desc_src не трогаются. Сбрасывает emb
    (пересчёт батчем в конце). progress(done,total)."""
    emit = wrap_emit(emit)
    with _LOCK:
        d = _load()
        if not d or not d.get("items"):
            emit("индекс пуст — сначала скан")
            return dict(count=0)
        todo = [(it["path"], it.get("name") or os.path.basename(it["path"]))
                for it in d["items"]
                if os.path.isfile(it.get("path") or "") and (not only_missing or needs_vis(it))]
    model = _vision_model()
    if not model:
        emit("⚠ vision-модель не найдена в LM Studio (нужна qwen-vl или похожая)")
        return dict(count=0, error="no vision model")
    try:                                                        # выгрузить прочие LLM (VRAM 16ГБ)
        from core import aicut
        aicut.ensure_loaded(model)
    except Exception:
        pass
    emit("vision-описания: {count} файлов через {model}…", count=len(todo), model=model)

    pending = {}

    def _flush_pending():
        if not pending:
            return
        with _LOCK:
            cur = _load()
            if not cur:
                return
            by_p = {paths.pkey(os.path.abspath(it.get("path") or "")): it
                    for it in cur.get("items", [])}
            applied = []
            for k, txt in pending.items():
                it = by_p.get(k)
                if it is not None:
                    it["vis"] = txt
                    it["emb"] = None
                    applied.append(k)
            if applied:
                _save(cur)
            for k in applied:
                pending.pop(k, None)

    done = 0
    for path, name in todo:
        txt = describe_file(path, model)
        if txt:
            pending[paths.pkey(os.path.abspath(path))] = txt
            emit("  {name}: {vis}", name=name[:48], vis=txt[:80])
        else:
            emit("  ⚠ {name}: vision не ответил", name=name[:60])
        done += 1
        if progress:
            progress(done, len(todo))
        if done % 10 == 0:
            _flush_pending()                                    # чекпойнт
    _flush_pending()

    emb_model = _emb_model()
    if emb_model:
        with _LOCK:
            cur = _load()
            need = []
            if cur:
                for it in cur.get("items", []):
                    if not it.get("emb"):
                        p = it.get("path")
                        if p:
                            doc_t = _subject_text(_doc_text(it.get("desc"), it.get("ru"), it.get("vis")))
                            if doc_t:
                                need.append((p, it.get("desc") or "", it.get("ru") or "", it.get("vis") or "", doc_t))
        if need:
            emit("эмбеддинги: {count} описаний…", count=len(need))
            vecs = _emb_docs([t for _, _, _, _, t in need], emb_model)
            if vecs:
                with _LOCK:
                    cur = _load()
                    if cur:
                        by_p = {paths.pkey(os.path.abspath(it.get("path") or "")): it
                                for it in cur.get("items", [])}
                        for (p, dsc, ru, vis, _txt), v in zip(need, vecs):
                            it = by_p.get(paths.pkey(os.path.abspath(p)))
                            if it is not None and (it.get("desc") or "") == dsc and (it.get("ru") or "") == ru \
                               and (it.get("vis") or "") == vis:
                                it["emb"] = v
                        cur["emb_model"], cur["emb_tag"] = emb_model, EMB_TAG
                        _save(cur)
    emit("готово: описано {count}", count=done)
    return dict(count=done)


def set_desc(path, desc):
    """Ручная правка описания файла (+эмбеддинг). -> dict(ok=True)|dict(error=...)."""
    desc = " ".join((desc or "").split()).strip()
    path = os.path.abspath(path)
    # правка desc под локом (как reject/add_generated): _load() отдаёт общий закэшированный
    # dict, и параллельная генерация/режет-запросы затирали правку. Эмбеддинг — вторым
    # тактом, вне лока (задание BU): и _emb_model(), и _emb_docs() — сетевые вызовы, под
    # _LOCK они вешали бы всю базу, пока LM Studio занят.
    with _LOCK:
        d = _load()
        if not d:
            return dict(error="индекс не построен")
        target = new_desc = cur_ru = None
        m = ""
        for it in d.get("items", []):
            if paths.pkey(it["path"]) == paths.pkey(path):
                it["desc"] = desc or _norm_text(it["path"])
                it["desc_src"] = "user" if desc else "name"
                it["emb"] = None
                new_desc = it["desc"]
                cur_ru = it.get("ru") or ""
                cur_vis = it.get("vis") or ""
                m = d.get("emb_model") or ""
                target = it["path"]
                _save(d)
                break
    if target is None:
        return dict(error="файла нет в индексе")
    if not m:
        m = _emb_model()
    if m:
        doc_t = _subject_text(_doc_text(new_desc, cur_ru, cur_vis))
        v = _emb_docs([doc_t], m)
        if v:
            with _LOCK:
                d = _load()
                if d:
                    for it in d.get("items", []):
                        if paths.pkey(it["path"]) == paths.pkey(path):
                            # описание/ru/vis могли смениться между тактами — не приписывать
                            # вектор, посчитанный по старому тексту
                            if (it.get("desc") or "") == new_desc and (it.get("ru") or "") == cur_ru \
                               and (it.get("vis") or "") == cur_vis:
                                it["emb"] = v[0]
                                d["emb_model"] = m
                            _save(d)
                            break
    return dict(ok=True, desc=new_desc)


def items_list(q="", offset=0, limit=50):
    """Список для UI: фильтр по подстроке (имя/desc/ru/vis). -> dict(total, items=[...])."""
    d = _load()
    if not d:
        return dict(total=0, items=[])
    qs = (q or "").lower().split()
    rows = [it for it in d.get("items", [])
            if all(t in (it["name"] + " " + it.get("desc", "") + " " + it.get("ru", "")
                         + " " + it.get("vis", "")).lower() for t in qs)]
    rows.sort(key=lambda x: (-int(x.get("used") or 0), x["name"].lower()))
    return dict(total=len(rows),
                items=[{k: it[k] for k in ("path", "name", "type", "used", "desc")}
                       | {"desc_src": it.get("desc_src") or "name", "ru": it.get("ru") or "",
                          "vis": it.get("vis") or "", "look": it.get("look") or ""}
                       for it in rows[offset:offset + limit]])


def stats(emit=print):
    """Инвентаризация текущего индекса (только чтение)."""
    out = emit or print
    d = _load()
    if not d:
        out("индекс пуст или не найден")
        return {}
    items = d.get("items", [])
    total = len(items)
    gone_count = sum(1 for it in items if it.get("gone"))
    on_disk = [it for it in items if os.path.isfile(it.get("path") or "")]
    missing = total - len(on_disk)
    missing_gone = sum(1 for it in items if not os.path.isfile(it.get("path") or "") and it.get("gone"))

    empty = sum(1 for it in on_disk if it.get("desc_src") != "user" and not (it.get("desc") or "").strip())
    name_match = sum(1 for it in on_disk if it.get("desc_src") != "user" and (it.get("desc") or "").strip()
                     and (it.get("desc") or "").strip() == _norm_text(it.get("path") or ""))
    short_words = sum(1 for it in on_disk if it.get("desc_src") != "user" and (it.get("desc") or "").strip()
                      and (it.get("desc") or "").strip() != _norm_text(it.get("path") or "")
                      and len(_WORD_RE.findall(it.get("desc") or "")) < 2)
    unfit = sum(1 for it in on_disk if needs_text(it))
    fit = len(on_disk) - unfit
    with_ru = sum(1 for it in on_disk if (it.get("ru") or "").strip())
    with_desc = sum(1 for it in on_disk if (it.get("desc") or "").strip())
    with_vis = sum(1 for it in on_disk if (it.get("vis") or "").strip())
    with_both = sum(1 for it in on_disk if (it.get("desc") or "").strip() and (it.get("vis") or "").strip())
    with_neither = sum(1 for it in on_disk
                       if not (it.get("desc") or "").strip() and not (it.get("vis") or "").strip())

    out(f"Всего записей: {total}")
    out(f"Помечено gone: {gone_count}")
    hint = ", запусти скан, чтобы пометить" if missing_gone < missing else ""
    out(f"Файлов на диске: {len(on_disk)} (нет на диске: {missing}, из них помечено gone: {missing_gone}{hint})")
    out("Среди живых:")
    out(f"  пригодных для поиска: {fit}")
    out(f"  непригодных: {unfit} (пусто: {empty}, desc==имя: {name_match}, меньше двух слов: {short_words})")
    out(f"  с русской подписью (ru): {with_ru}")
    out(f"  с непустым desc: {with_desc}")
    out(f"  с непустым vis: {with_vis}")
    out(f"  с обоими полями: {with_both}")
    out(f"  без обоих: {with_neither}")
    return dict(total=total, gone=gone_count, on_disk=len(on_disk), missing=missing,
                missing_gone=missing_gone, fit=fit, unfit=unfit, empty=empty,
                name_match=name_match, short_words=short_words, with_ru=with_ru,
                with_desc=with_desc, with_vis=with_vis, with_both=with_both,
                with_neither=with_neither)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) >= 2 and sys.argv[1] == "scan":
        build_index(sys.argv[2:], emit=console_emit)
    elif len(sys.argv) >= 3 and sys.argv[1] == "match":
        for r in match(" ".join(sys.argv[2:]), k=8):
            print(f"{r['score']:.3f}  [{r['type']}] used={r['used']}  {r['path']}")
    elif len(sys.argv) >= 5 and sys.argv[1] == "import":
        import datetime as _dt
        since = _dt.datetime.strptime(sys.argv[2], "%Y-%m-%d").timestamp()
        import_media(sys.argv[4:], sys.argv[3], since_ts=since, emit=console_emit)
    elif len(sys.argv) >= 2 and sys.argv[1] == "describe":
        auto_describe(emit=console_emit, only_missing="--all" not in sys.argv)
    elif len(sys.argv) >= 2 and sys.argv[1] == "stats":
        stats()
    else:
        print(__doc__)

