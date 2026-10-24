# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""insertlib.py — база использованных вставок (фото/видео) с индексом и автоподбором.

Идея: юзер годами копит удачные вставки в старых проектах. Сканируем прошлые XML
(parse_full -> inserts -> media-пути) и/или просто папки с медиа, строим индекс
insertlib.json (рядом с модулем). После генерации ИИ-вставок (query на английском)
подбираем из базы ближайший файл — семантически (эмбеддер EmbeddingGemma 2 в сервисе
моделей) либо, без сервиса, токенным матчем по имени файла/папке.

Векторы файла: описание (текст) либо, у файла без текста, — его картинка (та же модель
понимает и текст, и картинку в одном пространстве). Запрос всегда текст. Один вектор на файл;
признак — `emb_src` ("text" / "image").

API:
    build_index(dirs, emit=print)      -> dict(count=..., emb=bool)  # скан + сохранение
    match(query, k=5)                  -> [ {path,name,type,score,used}, ... ]
    match_many(queries, k=3)           -> [ [ ... ], ... ]           # 1 batch-запрос эмбеддера
    info()                             -> dict(count, dirs, emb_model, built)
    import_media(dirs, dest, since_ts) -> перенос медиа в СВОЮ папку базы (photos/videos)
    set_desc(path, desc)               -> ручная правка описания (+ пересчёт вектора)
    stats(emit=print)                  -> инвентаризация текущего индекса (только чтение)

CLI:  python insertlib.py scan <dir1> <dir2> ...
      python insertlib.py match "liver 3d icon"
      python insertlib.py import <since YYYY-MM-DD> <dest> <src1> [src2 ...]
      python insertlib.py stats
"""
import base64, gc, os, re, json, math, threading, time
from collections import Counter
from typing import Any, Callable, Sequence, cast
import numpy as np
from core.fileio import atomic_bytes_write, atomic_json_dump, quarantine_unreadable
from core.media import probe_duration

from core import device, embedder, model_service, paths, rembg_run
from core.clipstore import TRASH_DIR
from core.app_meta import env, console_emit, wrap_emit
from core.umsg import ReelsiError, cli_error
from core.applog import get_logger

log = get_logger(__name__)

# Индекс базы вставок. REELSI_INSERTLIB — как REELSI_UI_STATE у состояния UI: без него
# тестовый профиль (launch.json, 5098) пересканирует БОЕВОЙ индекс и перепишет его файл,
# хотя заявлен как «рабочее состояние не трогает». 2026-07-28: один клик «Обновить базу»
# на тестовом порту переписал рабочий insertlib.json.
INDEX_PATH = env("INSERTLIB") or paths.root("insertlib.json")

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
# служебное — не вставки: переходы/звуки, рото-кэш, черновики, исходники камер
SKIP_NAME = re.compile(r"(quick\s*\d|whoosh|riser|highlight_pop|\.draft\.|_tmp|^roto_\d+|^mask_\d+"
                       r"|\.nobg\.png$"
                       r"|^(c\d{3,4}|dji_|img_|gx\d{6})[\w-]*\.(mp4|mov|mxf)$)", re.I)
SKIP_DIR = re.compile(r"[\\/](roto|_tmp|\.git|__pycache__|_reelsi_trash)([\\/]|$)", re.I)  # корзина — не материал
MAX_VIDEO_MB = 300                                   # видео-вставка больше — почти наверняка исходник


# ---------- текст из пути ----------
def _norm_text(path: str) -> str:
    """Имя файла + родительская папка -> поисковый текст: разделители/ькамелкейс в пробелы."""
    base = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    s = base + " " + parent
    s = re.sub(r"[\\_\-\.\(\)\[\]{}+,]", " ", s)
    s = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", s)          # camelCase -> camel Case
    s = re.sub(r"(?<=[A-Za-zА-Яа-я])(?=\d)|(?<=\d)(?=[A-Za-zА-Яа-я])", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _norm_filename(path: str | None) -> str:
    """Нормализация имени файла без расширения (разделители и camelCase в пробелы)."""
    base = os.path.splitext(os.path.basename(path or ""))[0]
    s = re.sub(r"[\\_\-\.\(\)\[\]{}+,]", " ", base)
    s = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", s)
    s = re.sub(r"(?<=[A-Za-zА-Яа-я])(?=\d)|(?<=\d)(?=[A-Za-zА-Яа-я])", " ", s)
    return s.lower()


def _tokens(s: str | None) -> set[str]:
    return set(w for w in re.split(r"[^a-zа-яё0-9]+", (s or "").lower()) if len(w) >= 2)


# ---------- сравнение по основе слова ----------
# Слово в речи приходит в падеже («ноутбуком», «кофемашины»), а имя файла и описание —
# в основе («laptop», «кофемашина»). Такие пары сравниваются по ОСНОВЕ: у слова
# срезается падежное окончание, и основы обязаны совпасть ЦЕЛИКОМ. Совпадения по началу
# мало: «кофе» поймало бы «кофемашину». Окончания только русские:
# у латинских слов их нет, и срез латинской «e» слепил бы «machine» с «machin».
STEM_ENDINGS = (
    "ами", "ями", "ах", "ях", "ов", "ев", "ой", "ей", "ом", "ем", "ам", "ям",
    "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ью", "ья", "ию", "ия",
    "а", "я", "о", "е", "у", "ю", "ы", "и", "ь", "й",
)
# Короче основы не бывает: «де» поймало бы «дело», «ро» — «рост».
STEM_MIN = 3


def stem_tokens(text: str | None) -> list[str]:
    """Слова текста для сравнения по основе: нижний регистр, ё=е, разделители — границы."""
    return re.findall(r"[a-zа-я0-9]+", (text or "").lower().replace("ё", "е"))


def word_stem(word: str | None) -> str:
    """Основа ОДНОГО слова: срезано падежное окончание («Кофемашины» -> «кофемашин»).

    Строка из нескольких слов основой не является — вернётся пустая строка: склеивать
    «гормон» и «роста» в одну основу нельзя, форма из нескольких слов сравнивается по
    словам (см. `commands._named_mentions`).
    """
    toks = stem_tokens(word)
    if len(toks) != 1:
        return ""
    w = toks[0]
    for e in STEM_ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= STEM_MIN:
            return w[:-len(e)]
    return w


def stem_match(word: str | None, form: str | None) -> bool:
    """Одно ли и то же слово по основе: «кофемашины» = «кофемашина», «деки» = «дека»."""
    a, b = word_stem(word), word_stem(form)
    return len(a) >= STEM_MIN and a == b


def _norm_look(text: str | None) -> str:
    """Нормализация приписки стиля картинки (поле look): strip, нижний
    регистр, схлопнутые пробелы; из пустого — пустая строка. Единственное место
    нормализации стиля: приписки из image_prompts спикеров отличаются регистром и
    лишними пробелами, и если запись и запрос нормализовать по-разному, одинаковые
    стили не совпадут."""
    return " ".join((text or "").strip().lower().split())


_WORD_RE = re.compile(r"[a-zа-яё]{3,}", re.I)
_SIGNIFICANT_WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.I)


def _field_unfit(value: str | None, path: str | None) -> bool:
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


def _is_refusal(text: str | None) -> bool:
    """Отказ vision-модели: ответ начинается с типовой фразы отказа (без учёта регистра).
    Единственный источник правды для этого понятия. Отказ хуже пустого: он попадает в
    эмбеддинг и тянет к себе чужие запросы, поэтому везде считается отсутствием описания."""
    t = (text or "").strip().lower()
    return any(t.startswith(p) for p in _REFUSAL_PREFIXES)


def needs_text(it: dict[str, Any]) -> bool:
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


def _doc_text(desc: str | None, ru: str | None = "", vis: str | None = "") -> str:
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
# схеме сравнивался бы с документами по старой = мусор). g2 — EmbeddingGemma 2 в сервисе
# моделей и вектор картинки у файлов без текста; q5 — nomic-embed до переезда в сервис моделей.
EMB_TAG = "g2"


def _vector_source(it: dict[str, Any]) -> tuple[str, str]:
    """Чем описан файл для вектора: ("text", предметный текст) или ("image", "") — файл без текста.

    Единственное место этого решения: все двери векторов (`_item_vectors`) спрашивают его.
    «Без текста» — описания нет вовсе: исходной фразы нет (desc_src == "name": описание —
    лишь имя файла) и зрительного описания (vis) тоже нет; либо предметный текст пуст."""
    if it.get("desc_src") == "name" and not _doc_text("", "", it.get("vis")):
        return ("image", "")
    text = _subject_text(_doc_text(it.get("desc"), it.get("ru"), it.get("vis")))
    return ("text", text) if text else ("image", "")

# Вес лексического бонуса по редкости слов (0.35)
LEX_W = 0.35

# Пороги автоподбора вставок (единственный источник правды для UI и API).
# AUTO_COS: 0.83 — замер 2026-10-24, модель google/embeddinggemma-2, 641 пара «запрос → файл владельца»
# (продуктовый match_many; верный = первый кандидат совпал с файлом владельца). Косинусы EmbeddingGemma 2
# лежат высоко: медиана у верного кандидата 0.879, у неверного 0.842 — поэтому порог под шкалу nomic
# (0.30) пропускал 100 % запросов и ставил картинку даже туда, где подходящей нет. Проходит / верных
# среди прошедших: 0.80 — 85 % / 71 %; 0.82 — 79 % / 73 %; 0.83 — 75 % / 73 %; 0.84 — 70 % / 74 %;
# 0.86 — 56 % / 76 %; 0.88 — 42 % / 77 %; 0.90 — 27 % / 77 %. Берём 0.83: доля срабатываний как у nomic
# (73–75 %). Выше не берём: точность растёт на 1–4 п.п., а доля срабатываний падает почти вдвое.
# AUTO_LEX: самостоятельный порог лексического совпадения (0.20). Появился, так как замер на 1317 реальных
# запросах показал: у 39 запросов top-1 имел косинус ниже 0.30 при lex >= 0.20 (буквальные совпадения
# имени файла вроде «revolver cylinder» -> revolver_cylinder.mp4, «broken piggy bank with coins» ->
# broken_piggy_bank_with_coins.mp4, «retatrutide injection pen» -> 20-retatrutide-12mg_2.png, где
# vision-описание сбило косинус, а имя файла говорит ровно то, что просили).
AUTO_COS = 0.83       # порог косинуса при живом эмбеддере (см. замер выше)
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


def _subject_text(text: str | None) -> str:
    """Предметная часть описания (без слов стиля и цвета) для эмбеддера."""
    t = (text or "").strip()
    if not t:
        return ""
    cleaned = " ".join(STYLE_CLEAN_RE.sub(" ", t).split())
    return cleaned or t


def _significant_words(text: str | None) -> set[str]:
    """Значимые слова: [a-zа-яё0-9]{3,}, не из STYLE_WORDS_SET, не чисто цифровые."""
    if not text:
        return set()
    words = set()
    for m in _SIGNIFICANT_WORD_RE.finditer(text):
        w = m.group().lower()
        if w not in STYLE_WORDS_SET and not w.isdigit():
            words.add(w)
    return words


def _cand_words(it: dict[str, Any]) -> set[str]:
    """Множество значимых слов кандидата: имя файла + desc + ru + vis."""
    fn_text = _norm_filename(it.get("path") or "")
    desc_text = it.get("desc") or ""
    ru_text = it.get("ru") or ""
    vis_text = it.get("vis") or ""
    return _significant_words(f"{fn_text} {desc_text} {ru_text} {vis_text}")


def _lex_bonus(q_words: set[str], c_words: set[str], df: dict[str, int] | Counter, n_total: int) -> float:
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


# Поправки РАНГА за стиль картинки (поле look). Правят rank, а не score:
# подбор остаётся «по предмету», стиль лишь двигает порядок в выдаче.
LOOK_MATCH = 0.05
LOOK_MISS = -0.15


def _look_rank(cand_look: str | None, want_look: str | None) -> float:
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


def _q_text(q: str | None) -> str:
    """Текст запроса для эмбеддера: без слов стиля (если после чистки пусто — как было)."""
    return " ".join(_STYLE.sub(" ", q or "").split()) or (q or "")


# ---------- эмбеддер: сервис моделей (EmbeddingGemma 2) ----------
def _emb_model() -> str:
    """Имя модели, которое пишется в индекс (`emb_model`). Сама модель живёт в сервисе моделей:
    имя известно всегда, а доступность проверяет вызов векторов (`_emb_call`)."""
    return embedder.MODEL_ID


# Сколько файлов за один вызов сервиса. Картинки уходят байтами (JPEG до 512 px), тексты —
# строками: больше — тяжёлый ответ по проводу, меньше — лишние вызовы и лишние журнальные строки.
EMB_CHUNK = 64


def _emb_call(fn: Callable[[], list[list[float]]], n: int) -> list[list[float]] | None:
    """Векторы через сервис: None — сервиса нет или он отказал (поиск уйдёт на токенный путь).

    Отказ — не падение: причина пишется в журнал, а вектора у файлов остаются пустыми, как при
    недоступном эмбеддере. Ответ, не совпавший по длине с запросом, тоже отказ: вектор приписали бы
    не тому файлу."""
    if not n:
        return None
    try:
        vecs = fn()
    except ReelsiError: raise
    except Exception as e:
        log.warning("эмбеддинги не получены (%s) — поиск по вставкам без векторов", e)
        return None
    if len(vecs) != n:
        log.warning("эмбеддер вернул %d векторов вместо %d — поиск по вставкам без векторов", len(vecs), n)
        return None
    return vecs


def _emb_docs(texts: Sequence[str]) -> list[list[float]] | None:
    """Векторы ОПИСАНИЙ файлов (сторона документа: модель ставит свой промпт `Document`)."""
    tx = list(texts)
    return _emb_call(lambda: model_service.embed_texts(tx, "document"), len(tx))


def _emb_queries(queries: Sequence[str]) -> list[list[float]] | None:
    """Векторы ЗАПРОСОВ: чистка стиля (`_q_text`) и вид «query» (промпт `SearchQuery`)."""
    qs = [_q_text(q) for q in queries]
    return _emb_call(lambda: model_service.embed_texts(qs, "query"), len(qs))


def _emb_images(blobs: Sequence[bytes]) -> list[list[float]] | None:
    """Векторы КАРТИНОК — для файлов без текста (кадр фото или середина видео)."""
    bl = list(blobs)
    return _emb_call(lambda: model_service.embed_images(bl), len(bl))


def _item_bytes(it: dict[str, Any]) -> bytes | None:
    """Кадр файла для вектора картинки: фото — сам файл (ужат до 512 px), видео — середина ролика."""
    b64 = _thumb_b64(it.get("path") or "")
    return base64.b64decode(b64) if b64 else None


def _item_vectors(items: Sequence[dict[str, Any]]) -> list[tuple[str, list[float] | None]]:
    """ВЕКТОРЫ файлов — единственная дверь, через которую файлу считается вектор.

    Файл с текстом (`_vector_source` -> "text") уходит в `_emb_docs`, файл без текста — картинкой
    (`_item_bytes` -> `_emb_images`). По каждой записи -> (src, vec): src — "text" или "image",
    vec — None, если вектора нет (сервис не ответил, кадр не вытащен). Сетевые вызовы здесь —
    звать ВНЕ `_LOCK`. Сервис упал на пачке текстов — дальше не идём: остальные тоже не получат."""
    kinds = [_vector_source(it) for it in items]
    out: list[tuple[str, list[float] | None]] = [(src, None) for src, _ in kinds]
    text_idx = [i for i, (src, _) in enumerate(kinds) if src == "text"]
    for lo in range(0, len(text_idx), EMB_CHUNK):
        part = text_idx[lo:lo + EMB_CHUNK]
        vecs = _emb_docs([kinds[i][1] for i in part])
        if vecs is None:
            break
        for i, v in zip(part, vecs):
            out[i] = ("text", v)
    img_idx = [i for i, (src, _) in enumerate(kinds) if src == "image"]
    for lo in range(0, len(img_idx), EMB_CHUNK):
        part = img_idx[lo:lo + EMB_CHUNK]
        got = [(i, b) for i, b in ((i, _item_bytes(items[i])) for i in part) if b is not None]
        if not got:
            continue                                  # кадра не вытащили ни у одного — вектора нет
        vecs = _emb_images([b for _, b in got])
        if vecs is None:
            break
        for (i, _), v in zip(got, vecs):
            out[i] = ("image", v)
    return out


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return s / (na * nb)


# ---------- скан ----------
def _media_kind(path: str) -> str | None:
    e = os.path.splitext(path)[1].lower()
    if e in IMG_EXT:
        return "photo"
    if e in VID_EXT:
        return "video"
    return None


def _scan_xml_inserts(xml_path: str) -> list[str]:
    """Медиа вставок из XML прошлого проекта (дорожки над камерами). -> [path, ...]"""
    try:
        from core import xml2ae
        _meta, _cams, _subs, inserts = xml2ae.parse_full(xml_path)
        return [x["media"] for x in (inserts or []) if x.get("media")]
    except ReelsiError: raise
    except Exception as e:
        # XML прошлого проекта не разобран — его вставки не попадут в библиотеку; причину пишем в журнал
        log.warning("XML прошлого проекта не разобран (%s) — его вставки не попали в библиотеку", e)
        return []


def scan(dirs: Sequence[str], emit: Any = None) -> dict[str, dict[str, Any]]:
    """Собрать медиа вставок: из всех .xml в папках (что реально использовалось) и
    медиафайлы, лежащие в папках напрямую (пополняемая библиотека).
    -> dict path -> {type, used(раз использовано в проектах), src}"""
    emit = wrap_emit(emit)
    items: dict[str, dict[str, Any]] = {}

    def _add(p: str, src: str) -> None:
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
            _dn[:] = [x for x in _dn if x != TRASH_DIR]  # удалённое в корзине — не материал и не клип
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


def _merge_prev_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Схлопывание нескольких прежних записей одного пути (после pkey):
    - desc/desc_src: побеждает запись с пригодным desc (см. _field_unfit); если таких
      несколько — та, у которой desc_src != 'name';
    - used: максимум;
    - rej: объединение списков без повторов;
    - mw/mh, sc, x, y, nobg, ru, vis, added: из той записи, где они есть (при споре — из
      победившей по desc); память картинки (см. remember_look) не должна теряться при слиянии;
    - emb: от победившей по desc записи, но только если её desc+ru+vis совпадают с итоговыми;
      иначе None.
    """
    if len(records) == 1:
        return dict(records[0])

    def _desc_rank(r: dict[str, Any]) -> tuple[bool, bool, bool]:
        return (
            not _field_unfit(r.get("desc"), r.get("path") or ""),
            (r.get("desc_src") or "name") != "name",
            bool((r.get("desc") or "").strip()),
        )

    desc_winner = max(records, key=_desc_rank)
    desc = desc_winner.get("desc")
    desc_src = desc_winner.get("desc_src") or "name"

    used = max(int(r.get("used") or 0) for r in records)

    rej: list[str] = []
    for r in records:
        for q in (r.get("rej") or []):
            if q and q not in rej:
                rej.append(q)

    def _field_val(r: dict[str, Any], k: str) -> Any:
        v = r.get(k)
        if v is None:
            return None
        if k in ("ru", "vis"):
            return v if (v or "").strip() else None
        return v

    extra = {}
    for k in ("mw", "mh", "sc", "x", "y", "nobg", "ru", "added", "vis"):
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
    for k in ("mw", "mh", "sc", "x", "y", "nobg", "ru", "added", "vis"):
        if k in extra:
            res[k] = extra[k]
        else:
            res.pop(k, None)
    res["emb"] = emb
    if emb is None:                       # вектора нет — и признака его происхождения не должно остаться
        res.pop("emb_src", None)
    return res


def build_index(dirs: Sequence[str], emit: Any = None, use_emb: bool = True) -> dict[str, Any]:
    """Скан + эмбеддинги + сохранение insertlib.json. Описания (desc), правленные руками
    в прошлом индексе, сохраняются; эмбеддинги пересчитываются только для новых/правленых."""
    emit = wrap_emit(emit)
    import copy
    with _LOCK:
        old = copy.deepcopy(_load() or {})
    old_grouped: dict[str, list[dict[str, Any]]] = {}
    for it in old.get("items", []):
        p = it.get("path")
        if p:
            k = paths.pkey(os.path.abspath(p))
            old_grouped.setdefault(k, []).append(it)
    old_items = {k: _merge_prev_records(recs) for k, recs in old_grouped.items()}

    stale = old.get("emb_tag") != EMB_TAG        # схема эмбеддинга сменилась -> всё пересчитать
    found = scan(dirs, emit)
    found_nc = {paths.pkey(os.path.abspath(p)) for p in found}
    items: list[dict[str, Any]] = []
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
              "desc": desc, "desc_src": prev.get("desc_src") or "name"}
        # рескан не должен терять брак, свежесть, запомненную форму маски (mw/mh),
        # масштаб, точку покоя и фон (sc/x/y/nobg — память картинки), русскую подпись (ru),
        # зрительное описание (vis) и стиль картинки (look)
        for keep in ("rej", "added", "mw", "mh", "sc", "x", "y", "nobg", "ru", "vis", "look"):
            # nobg=False — тоже память («фон оставить»), поэтому не только проверка на истинность
            if prev.get(keep) or prev.get(keep) is False:
                it[keep] = prev[keep]
        # Вектор переживает рескан, только если схема та же и файл описан тем же видом (текст или
        # картинка): вектор текста не подходит к картинке и наоборот — тогда он пересчитывается.
        # Записи без признака — текстовые: до векторов картинок все вектора были текстовыми.
        if not stale and same_text and prev.get("emb") and (prev.get("emb_src") or "text") == _vector_source(it)[0]:
            it["emb"], it["emb_src"] = prev["emb"], prev.get("emb_src") or "text"
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
                it.pop("emb_src", None)
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
            it.pop("emb_src", None)
    # Векторы — через ту же дверь, что и во всех остальных местах (`_item_vectors`): у файла с
    # текстом — вектор текста, у файла без текста — вектор картинки.
    model = _emb_model() if use_emb else ""
    if use_emb:
        need = [it for it in items if not it.get("emb") and not it.get("gone")]
        if need:
            emit("эмбеддинги: {count} новых файлов через {model}…", count=len(need), model=model)
            got = _item_vectors(need)
            if any(v is not None for _, v in got):
                for it, (src, v) in zip(need, got):
                    if v is not None:
                        it["emb"], it["emb_src"] = v, src
            else:
                emit("⚠ эмбеддер не ответил — работаю токенным матчем по именам")
                model = ""
    else:
        emit("эмбеддинги выключены — токенный матч по именам")

    # Слияние под локом: не затираем параллельные правки (rej, add_generated, set_desc),
    # сделанные другими между началом скана и сохранением
    with _LOCK:
        cur = _load() or {}
        cur_grouped: dict[str, list[dict[str, Any]]] = {}
        for it in cur.get("items", []):
            p = it.get("path")
            if p:
                k = paths.pkey(os.path.abspath(p))
                cur_grouped.setdefault(k, []).append(it)
        cur_items = {k: _merge_prev_records(recs) for k, recs in cur_grouped.items()}

        built_by_k: dict[str, dict[str, Any]] = {}
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
        fields_to_merge = ("rej", "desc", "desc_src", "ru", "vis", "look", "mw", "mh", "sc", "x", "y", "nobg", "added")
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

                # вид вектора (текст или картинка) мог смениться и без смены текста: desc_src с «name» на «user»
                if ((it.get("desc") or "") != comp_desc or (it.get("ru") or "") != comp_ru
                        or (it.get("vis") or "") != comp_vis
                        or it.get("emb_src") != _vector_source(it)[0]):
                    it["emb"] = None
                    it.pop("emb_src", None)

        data = {"dirs": [os.path.abspath(x.strip().strip('"')) for x in dirs if x.strip()],
                "emb_model": model or "", "emb_tag": EMB_TAG, "items": items}
        _save(data)
    emit("индекс: {count} файлов ({photos} фото, {videos} видео)",
         count=len(items),
         photos=sum(1 for i in items if i.get('type') == 'photo'),
         videos=sum(1 for i in items if i.get('type') == 'video'))
    return dict(count=len(items), emb=bool(model))


_CACHE: dict[str, Any] = {"mtime": 0, "data": None, "mat": None, "mat_items": None, "df": None, "cand_words": None, "N": 0}
_LOCK = threading.RLock()        # сериализуем чтение/запись индекса при параллельной генерации


def _seed_index() -> None:
    """Тестовый профиль (AUTOCUT_INSERTLIB) без своего индекса — снять КОПИЮ с рабочего.
    Скан с нуля пересчитал бы эмбеддинги полутора тысяч файлов (минуты и VRAM), а
    работать поверх рабочего файла нельзя — ради этого разделение и заводилось."""
    real = paths.root("insertlib.json")
    if INDEX_PATH == real or os.path.exists(INDEX_PATH) or not os.path.isfile(real):
        return
    try:
        import shutil
        shutil.copyfile(real, INDEX_PATH)
    except ReelsiError: raise
    except Exception as ex:
        log.warning("не скопировал боевой insertlib.json в %s: %s — "
                    "база останется пустой", INDEX_PATH, ex)


def _load() -> dict[str, Any] | None:
    with _LOCK:
        _seed_index()
        try:
            st = os.stat(INDEX_PATH)
            mt = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        if _CACHE["data"] is None or _CACHE.get("mtime") != mt:
            try:
                # with, а не голый open: при битом файле дескриптор остался бы открытым через
                # traceback, и на Windows отложить файл (os.replace) не удалось бы.
                with open(INDEX_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                # Не объект (список, строка) — тот же битый индекс: его методы читают .get().
                # Считаем нечитаемым, чтобы запись ниже откладывала файл, а не затирала его.
                if not isinstance(data, dict):
                    raise ValueError("индекс — не объект JSON")
                _CACHE["data"] = data
                _CACHE["mtime"] = mt
                _CACHE["mat"] = None
                _CACHE["mat_items"] = None
                _CACHE["df"] = None
                _CACHE["cand_words"] = None
                _CACHE["N"] = 0
            except ReelsiError: raise
            except Exception as e:
                # индекс не читается — библиотека пуста, пока файл не починен; это должно быть видно в журнале.
                # Прошлые данные кэша к битому файлу не относятся: сбрасываем их, иначе _save
                # решил бы, что читать было нечего, и затёр бы файл вместо того, чтобы отложить его.
                log.warning("индекс вставок не читается (%s) — библиотека пуста, пока файл не починен; "
                            "при следующей записи он будет отложен в %s.bad-…", e, INDEX_PATH)
                _CACHE["data"] = None
                return None
        return _CACHE["data"]


def _matrix() -> tuple[Any, list[dict[str, Any]]]:
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
        df: Counter[str] = Counter()
        for w_set in cand_words_list:
            for w in w_set:
                df[w] += 1

        _CACHE["mat"] = mat
        _CACHE["mat_items"] = items
        _CACHE["df"] = df
        _CACHE["cand_words"] = cand_words_list
        _CACHE["N"] = len(items)
        return mat, items


def info() -> dict[str, Any]:
    d = _load()
    if not d:
        return dict(count=0, dirs=[], emb_model="", built=False)
    return dict(count=len(d.get("items", [])), dirs=d.get("dirs", []),
                emb_model=d.get("emb_model", ""), built=True)


def _score_tokens(q_toks: set[str], it: dict[str, Any]) -> float:
    # Слова кандидата — по всем трём полям (desc + ru + vis), как в _cand_words: после
    # миграции EP vision-описания живут в vis, и у записей с пустым desc фолбэк
    # должен находить их по тому, что ВИДНО на картинке.
    toks = _tokens(" ".join(p for p in (it.get("desc"), it.get("ru"), it.get("vis")) if p))
    if not q_toks or not toks:
        return 0.0
    inter = len(q_toks & toks)
    return inter / max(1, len(q_toks))


def match_many(queries: Sequence[str], k: int = 5, type_hint: str | None = None, look: str | None = None) -> list[Any]:
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
    d = cast(dict[str, Any], _load())
    items_all = d.get("items", [])
    # Вектор запроса — только если векторы индекса в ТЕКУЩЕЙ схеме: вектор запроса из одной модели
    # против векторов другой дал бы мусор (пересчёт `_ensure_emb_tag` не прошёл — ищем токенами).
    has_vecs = any(it.get("emb") for it in items_all)
    qvecs = _emb_queries(queries) if (has_vecs and d.get("emb_tag") == EMB_TAG) else None
    pos = {id(it): n for n, it in enumerate(items_all)}        # порядок в индексе = «когда добавлен»
    mat, mat_items = _matrix() if qvecs else (None, [])
    out: list[list[dict[str, Any]]] = []
    for qi, q in enumerate(queries):
        qn = _norm_q(q)
        scored: list[tuple[float, dict[str, Any], float, float]] = []
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
        if look:                                           # мягкий приоритет стиля
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
        res: list[dict[str, Any]] = []
        for rank, it, cos_sim, lex in scored:
            if os.path.isfile(it["path"]):
                is_auto = bool((cos_sim >= cos_thr) or (lex >= AUTO_LEX))
                res.append(dict(path=it["path"], name=it["name"], type=it["type"],
                                used=it.get("used", 0), score=round(cos_sim, 3),
                                lex=round(lex, 3),
                                auto=is_auto,
                                mw=it.get("mw"), mh=it.get("mh"), look=it.get("look"),
                                sc=it.get("sc"), x=it.get("x"), y=it.get("y"), nobg=it.get("nobg")))
                if len(res) == k:
                    break
        out.append(res)
    return out


def _norm_q(q: str | None) -> str:
    return " ".join((q or "").lower().split())


def _ensure_emb_tag(emit: Any = None) -> bool:
    """Индекс собран по СТАРОЙ схеме эмбеддинга -> пересчитать векторы всей базы одним батчем:
    тексты — текстом, файлы без текста — картинками (та же дверь `_item_vectors`, что и везде).
    Полный рескан диска не нужен: тексты и пути уже в индексе. Сервиса нет — выходим молча и тег
    не трогаем (пересчёт пойдёт, когда сервис поднимется), а токенный фолбэк работает и сейчас.
    Старые вектора другой схемы не остаются ни у кого: записи, которым вектор не досталось, — без него."""
    emit = wrap_emit(emit)
    with _LOCK:
        d = _load()
        if not d or d.get("emb_tag") == EMB_TAG:
            return False
        snap = [dict(it) for it in d.get("items", []) if not it.get("gone")]
    if not snap:
        return False
    # векторы — вне лока: сетевой вызов под _LOCK повесил бы всю базу, пока сервис занят
    emit("база: схема эмбеддинга сменилась — пересчёт {count} файлов…", count=len(snap))
    got = _item_vectors(snap)
    if not any(v is not None for _, v in got):
        return False
    with _LOCK:
        d = _load()
        if not d or d.get("emb_tag") == EMB_TAG:
            return False
        for it in d.get("items", []):
            it["emb"] = None
            it.pop("emb_src", None)
        by_path = {paths.pkey(os.path.abspath(it.get("path") or "")): it
                   for it in d.get("items", [])}
        # между тактами индекс мог измениться — запись ищем заново по пути и приписываем вектор,
        # только если файл описан тем же (иначе вектор старого текста достался бы новому)
        for snap_it, (src, v) in zip(snap, got):
            if v is None:
                continue
            it = by_path.get(paths.pkey(os.path.abspath(snap_it.get("path") or "")))
            if it is not None and _vector_source(it) == _vector_source(snap_it):
                it["emb"], it["emb_src"] = v, src
        d["emb_model"], d["emb_tag"] = _emb_model(), EMB_TAG
        _save(d)
    emit("база: эмбеддинги пересчитаны")
    return True


def reject(path: str, query: str, on: bool = True) -> bool:
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


def match(query: str, k: int = 5, type_hint: str | None = None, look: str | None = None) -> list[dict[str, Any]]:
    return match_many([query], k=k, type_hint=type_hint, look=look)[0]


# Слова из личного словаря (`named_inserts.json`, поля `_prefer` и `_avoid`) — разбор
# один раз на вызов, сам список живёт в файле, а не в коде: какие слова нужны картинке
# и какие её портят, знает только владелец словаря.
def _word_rules(words: Sequence[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Слова списка -> (целые слова, начала слов).

    Запись с «*» на конце — по началу слова («vial*» ловит «vials»). Без «*» — только
    целым словом: короткое «pen» не должно ловить «pending», «pill» — «pillow».
    Регистр и ё приводятся как у слов записи (`stem_tokens`).
    """
    exact: set[str] = set()
    starts: list[str] = []
    for raw in words:
        if not isinstance(raw, str):
            continue
        w = raw.strip().lower().replace("ё", "е")
        if w.endswith("*"):
            w = w[:-1].strip()
            if w:
                starts.append(w)
        elif w:
            exact.add(w)
    return frozenset(exact), tuple(starts)


def _has_word(toks: set[str], rules: tuple[frozenset[str], tuple[str, ...]]) -> bool:
    exact, starts = rules
    return any(t in exact or (starts and t.startswith(starts)) for t in toks)


def _name_stems(terms: Sequence[str]) -> list[str]:
    """Основы названий для сравнения с записями базы: короче STEM_MIN и числа не берём."""
    return [w for t in terms for w in stem_tokens(t) if len(w) >= STEM_MIN and not w.isdigit()]


def find_named(terms: Sequence[str], kind: str = "photo", secondary: Sequence[str] = (),
               prefer: Sequence[str] = (), avoid: Sequence[str] = ()) -> dict[str, Any] | None:
    """Лучшая живая запись базы, которую `terms` называют ПО ИМЕНИ (сравнение по основе).

    Порядок выбора (ключ сортировки, меньше — лучше):
      1. уровень совпадения: 0 — основное название (`terms`: канон и прямые формы),
         1 — вторичная форма (`secondary`: эфир, вариант написания…). Вторичная
         ищет картинку, только если по основным совпадений в базе нет;
      2. название найдено в ИМЕНИ ФАЙЛА (0), а не только в описании (1);
      3. число слов из `avoid` в записи — слова, которые портят картинку (сюжет, брак);
      4. короче имя файла;
      5. порядок в базе.
    `prefer` — слова, без которых картинка не годится: запись обязана содержать хотя бы
    одно (`_word_rules`: «vial*» — по началу слова). Пустой `prefer` — обязательного
    слова нет. Без этого правила брали первую запись по порядку, и картинкой становился
    бланк или ценник рядом с предметом, а не сам предмет.
    Записей с обязательным словом нет — возвращаем None: вставки по названию нет, в лог
    «картинки нет».

    Нужна детерминированному подбору по названию (вставки по названиям): подбор «по смыслу
    фразы» (`match`) тут не годится — картинка обязана быть именно этого предмета, а не
    похожего по описанию. Смотрим имя файла и все текстовые поля записи (desc, ru, vis):
    база называет предмет и в имени файла, и в описании, и во зрительном описании (vis).
    kind — тип записи ('photo'/'video'), None — любой.
    Записи с пропавшим файлом (gone) и ссылки в никуда пропускаем: такой путь уехал бы в
    .jsx пустым кадром. Порядок — как в индексе (когда добавлен).
    """
    d = _load()
    if not d:
        return None
    main = _name_stems(terms)
    sec = _name_stems(secondary)
    if not main and not sec:
        return None
    prefer_rules = _word_rules(prefer)
    avoid_exact = _word_rules(avoid)[0]
    best: tuple[tuple[int, int, int, int, int], dict[str, Any]] | None = None
    for idx, it in enumerate(d.get("items", [])):
        if it.get("gone"):
            continue
        path = str(it.get("path") or "")
        if kind and (_media_kind(path) or it.get("type")) != kind:
            continue
        text = " ".join([str(it.get(f) or "") for f in ("name", "desc", "ru", "vis")]
                        + [_norm_filename(path)])
        if not os.path.isfile(path):
            continue
        toks = set(stem_tokens(text))
        # 1) уровень: основное название важнее эфира/вторичной формы
        if any(stem_match(tok, s) for tok in toks for s in main):
            level, want = 0, main
        elif any(stem_match(tok, s) for tok in toks for s in sec):
            level, want = 1, sec
        else:
            continue
        # Обязательное слово из словаря (`_prefer`): без него запись не годится вовсе —
        # картинка должна показывать предмет, а не только упоминать его в тексте.
        if prefer_rules[0] or prefer_rules[1]:
            if not _has_word(toks, prefer_rules):
                continue
        # 2) название в имени файла (0) важнее, чем только в описании (1)
        name_toks = set(stem_tokens(os.path.basename(path) + " " + str(it.get("name") or "")))
        in_name = 0 if any(stem_match(tok, s) for tok in name_toks for s in want) else 1
        # 3)–5) слова из `avoid`, короче имя файла, порядок в базе
        key = (level, in_name, len(toks & avoid_exact), len(os.path.basename(path)), idx)
        if best is None or key < best[0]:
            best = (key, {"path": path, "name": it.get("name") or os.path.basename(path),
                          "type": it.get("type") or _media_kind(path),
                          "mw": it.get("mw"), "mh": it.get("mh"), "sc": it.get("sc"),
                          "x": it.get("x"), "y": it.get("y"), "nobg": it.get("nobg")})
    return best[1] if best else None


# ---------- импорт в СВОЮ папку базы ----------
def _save(data: dict[str, Any]) -> None:
    with _LOCK:
        # Битый индекс откладываем ДО записи: иначе одна новая запись стёрла бы всю личную
        # базу вставок (тысячи записей, эмбеддинги не пересчитать). Проверяем, только если
        # кэш пуст — то есть успешного чтения не было: все пишущие пути зовут _load() прямо
        # перед записью, и прочитанный им файл битым быть не может. Так обычная запись
        # не парсит индекс лишний раз (чекпойнты авто-описаний пишут часто).
        if _CACHE["data"] is None:
            bad = quarantine_unreadable(INDEX_PATH, valid=lambda d: isinstance(d, dict))
            if bad:
                log.warning("индекс вставок не читается — отложен в %s, записан заново", bad)
        atomic_json_dump(INDEX_PATH, data)
        _CACHE["data"] = None                            # сбросить кэш
        _CACHE["mat"] = None
        _CACHE["mat_items"] = None
        _CACHE["df"] = None
        _CACHE["cand_words"] = None
        _CACHE["N"] = 0


_RB_SESSION = None                                       # сессия rembg (модель грузится 1 раз на модель)
_RB_SESSION_NAME = None                                  # имя модели, под которую создана сессия
_RB_RUN_LOCK = threading.Lock()                          # прогоны фона по одному — как `model_service._rembg_run_lock`
_RB_LOCK = threading.Lock()                              # создание сессии — под локом
# Метка модели в PNG-вырезке (tEXt). По ней кэш <стем>.nobg.png различает модели: имя
# файла оставляем прежним (его читают интерфейс и список базы), а смена модели в
# настройках пересчитывает кэш, а не отдаёт прошлую вырезку. Кэш без метки — u2net:
# до выбора модели была только она, такие кэши не пересчитываем.
REMBG_TAG = "reelsi_rembg_model"

# Простой сессии фона — и на видеокарте, и на процессоре: сессия на видеокарте занимает
# ~10 ГБ VRAM (замер — в докстринге `core/rembg_run.py`), а на Windows переполнение VRAM
# вешает машину (правило проекта: держать запас). Поэтому простой дольше `rembg_run.IDLE_S`
# выгружает сессию; следующая очистка откроет её заново и снова спросит видеокарту.
_RB_SESSION_GPU = False                                  # сессия открыта на видеокарте, а не на процессоре
_RB_IDLE_TIMER: threading.Timer | None = None            # таймер выгрузки сессии по простою
_RB_IDLE_GEN = 0                                         # поколение таймера: сработавший устаревший молчит


def _rb_arm_idle_timer() -> None:
    """Перезапустить таймер выгрузки сессии по простою. Вызывать под `_RB_LOCK`.
    Старый таймер отменяем; поколение отсекает его, если он уже сработал и ждёт лок."""
    global _RB_IDLE_TIMER, _RB_IDLE_GEN
    _RB_IDLE_GEN += 1
    if _RB_IDLE_TIMER is not None:
        _RB_IDLE_TIMER.cancel()
    timer = threading.Timer(rembg_run.IDLE_S, _rb_idle_unload, args=(_RB_IDLE_GEN,))
    timer.daemon = True
    _RB_IDLE_TIMER = timer
    timer.start()


def _rb_idle_unload(gen: int) -> None:
    """Выгрузить сессию фона после простоя (с видеокарты — чтобы отдать VRAM). Сработавший таймер
    устаревшего поколения (вызов успел перезапустить таймер, пока он ждал лок) ничего не делает."""
    global _RB_SESSION, _RB_SESSION_NAME, _RB_SESSION_GPU, _RB_IDLE_TIMER
    with _RB_LOCK:
        if gen != _RB_IDLE_GEN:
            return
        _RB_IDLE_TIMER = None
        _RB_SESSION = None
        _RB_SESSION_NAME = None
        _RB_SESSION_GPU = False
        gc.collect()


def _rb_local_png(img_bytes: bytes, name: str, emit: Any) -> bytes:
    """Снять фон в ЭТОМ процессе — запасной путь, когда сервиса моделей нет.

    Сессия держится здесь же (кэш `_RB_SESSION`), простой выгружает её по `rembg_run.IDLE_S`.
    Открытие сессии — в `rembg_run.open_session`: одно правило и для этого пути, и для сервиса.

    Открытие и прогон — под `_RB_RUN_LOCK`, по одному на процесс. Замер на железе: один прогон
    BiRefNet берёт ~8.5–9 ГБ арены, два разом в потолок 10 ГиБ не помещаются (падают «Available
    memory … smaller than requested»). Открытие второй модели при идущем прогоне — то же
    переполнение, поэтому открываем под тем же замком. `_RB_LOCK` — только на подмену сессии.
    """
    global _RB_SESSION, _RB_SESSION_NAME, _RB_SESSION_GPU
    with _RB_RUN_LOCK:
        with _RB_LOCK:                                   # одна сессия на модель
            if _RB_SESSION is None or _RB_SESSION_NAME != name:
                _RB_SESSION = None                       # старую отпускаем до загрузки новой
                # Устройство то же, что у сервиса моделей («Где считать модели»). Свободную VRAM
                # меряем только когда на видеокарту и правда идём: nvidia-smi — отдельный процесс.
                want_gpu = model_service._service_device() != "cpu"
                free = device.nvidia_free_mib() if want_gpu else None
                _RB_SESSION, _RB_SESSION_GPU, why = rembg_run.open_session(name, want_gpu, free)
                _RB_SESSION_NAME = name
                if why:
                    emit("  фон снимается процессором: {why}", why=why)
            session = _RB_SESSION
            _rb_arm_idle_timer()                         # простой у любой сессии: и у процессорной тоже
        return rembg_run.predict(session, img_bytes)


def remove_bg(img_bytes: bytes, trim: bool = True, emit: Any = None) -> bytes:
    """«Remove Background» как в фотошопе: PNG с настоящей альфой вместо белого фона.
    Модель — из общих настроек (`rembg_model`: u2net или birefnet-general), onnx на видеокарте
    (если стоит onnxruntime-gpu), иначе на CPU;
    качается один раз в ~/.u2net при первом вызове этой модели.
    trim — обрезать полностью прозрачные поля (генератор оставляет широкие пустые
    рамки, а вставка в AE масштабируется по кадру — без обрезки предмет мелкий).
    Если модель съела всё (пустая альфа) — возвращаем исходник, лучше фон чем дырка."""
    emit = wrap_emit(emit)
    import io
    from core import aicut                               # ленивый: aicut тянет настройки ИИ, ядру вставок не нужны
    name = aicut.rembg_model()
    try:
        # Сервис моделей первым: одна копия модели на машину, сверка VRAM, выгрузка по простою.
        got: dict[str, Any] | None = model_service.remove_bg(img_bytes, name)
    except model_service.ServiceUnavailable:
        # Сервиса нет (не поднялся, не ответил) — считаем здесь. Отказ живого сервиса — `ReelsiError`,
        # сюда он не попадает: вторая копия модели рядом с занятой картой хуже ошибки.
        got = None
    if got is None:
        raw = _rb_local_png(img_bytes, name, emit)
    else:
        if got["note"]:                                  # сервис открыл сессию процессором — та же строка в журнал
            emit("  фон снимается процессором: {why}", why=got["note"])
        raw = got["png"]
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box is None:                                      # альфа пустая — фон не сняли
        emit("  фон снять не вышло (пустая альфа) — оставляю как есть")
        return img_bytes
    if trim and box != (0, 0, im.width, im.height):
        im = im.crop(box)
    buf = io.BytesIO()
    meta = PngInfo()
    meta.add_text(REMBG_TAG, name)
    im.save(buf, "PNG", pnginfo=meta)
    emit("  фон убран, прозрачный PNG {width}x{height}", width=im.width, height=im.height)
    return buf.getvalue()


def _nobg_model(path: str) -> str:
    """Модель, которой сделан кэш вырезки: метка REMBG_TAG в PNG. Без метки — u2net
    (кэш сделан до выбора модели). Файл не читается как картинка — пустая строка:
    она не совпадёт ни с одной моделью, и кэш пересчитается."""
    from PIL import Image
    from core import aicut
    try:
        with Image.open(path) as im:
            return im.info.get(REMBG_TAG) or aicut.REMBG_DEFAULT
    except Exception:  # не картинка (видео, битый файл): пустая метка не совпадёт ни с одной моделью, кэш пересчитается, а не упадёт вставка
        return ""


def nobg_path(media: str, emit: Any = None) -> str:
    """Путь к PNG с уже снятым фоном РЯДОМ с исходником: <папка>/<стем>.<расш>.nobg.png.

    Галка стиля «без фона»: и сборка (.jsx), и предпросмотр (/api/media?nobg=1)
    ходят сюда — снятие фона одно на оба, второй копии правила нет. Кэш обязателен: rembg
    это onnx-модель: на видеокарте при onnxruntime-gpu ~0.3 с на картинку, на процессоре — секунды, а вставок в ролике десятки.

    Кэш НОВЕЕ исходника — берём его. Исходник перегенерировали/поправили — считаем заново:
    иначе на экране осталась бы прошлая картинка. Кэш сделан ДРУГОЙ моделью, чем выбрана
    в настройках, — тоже считаем заново (см. REMBG_TAG). Не картинка (видео) или rembg
    упал — возвращаем ИСХОДНЫЙ путь: вставка с фоном лучше пропавшей.
    """
    emit = wrap_emit(emit)
    p = os.path.abspath(media or "")
    if not p:
        return media
    if os.path.basename(p).lower().endswith(".nobg.png"):
        return media
    if _media_kind(p) != "photo":
        return media
    dst = os.path.join(os.path.dirname(p),
                       os.path.basename(p) + ".nobg.png")
    from core import aicut
    try:
        if (os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(p)
                and _nobg_model(dst) == aicut.rembg_model()):
            return dst
    except OSError:
        pass                                             # mtime не прочитался — считаем заново
    try:
        with open(p, "rb") as f:
            data = f.read()
        try:
            # SystemExit отдельно и ТОЛЬКО тут: без пакета rembg/onnxruntime remove_bg
            # не бросает исключение, а выходит из процесса — сборка со вставкой «на
            # подложке» падала целиком вместо отката на исходник.
            out = remove_bg(data, trim=True, emit=emit)
        except (ReelsiError, SystemExit) as e:
            emit("  фон у {name} не убран ({err}) — вставка как есть",
                 name=os.path.basename(p), err=e)
            return media
        atomic_bytes_write(dst, out)                     # «Стоп»/сбой не оставит обгрызок кэша
    except ReelsiError: raise
    except Exception as e:
        emit("  фон у {name} не убран ({err}) — вставка как есть",
             name=os.path.basename(p), err=e)
        return media
    return dst


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


def image_real_format(path: str) -> str | None:
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
    except ReelsiError: raise
    except Exception:
        # PIL не открыл картинку (битая или неизвестная) — формат не определяем: None, как в докстроке
        return None
    return None


def to_ae_image(path: str, emit: Any = None) -> str:
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
    except ReelsiError: raise
    except Exception:                              # битый файл — пусть ругается AE
        return path
    if mode in AE_OK_MODES:
        return path
    return _repng(path, os.path.splitext(path)[0] + "-rgb.png",
                  f"After Effects не читает {mode}", emit)


def _repng(src: str, dst: str, why: str, emit: Any) -> str:
    """src -> PNG в dst с сохранением альфы. -> dst, а при неудаче src (ругнувшись)."""
    emit = wrap_emit(emit)
    if os.path.exists(dst):                        # уже перекодировали раньше
        return dst
    try:
        from PIL import Image
        with Image.open(src) as im:
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "PA", "P") else "RGB").save(dst, "PNG")
    except ReelsiError: raise
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


def _vcodec(path: str) -> str | None:
    """Кодек первой видеодорожки (ffprobe). -> имя в нижнем регистре | None если нечем."""
    import subprocess
    try:
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                            capture_output=True, text=True, timeout=60)
        return ((pr.stdout or "").strip().split(",")[0] or "").lower() or None
    except ReelsiError: raise
    except Exception:                                  # нет ffprobe/битый файл — пусть ругается AE
        return None


def to_ae_video(path: str, emit: Any = None) -> str:
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
    except ReelsiError: raise
    except Exception as e:
        try:
            os.remove(dst)                             # недописанный огрызок хуже отсутствия
        except OSError:
            pass  # огрызок уже убран — о сбое сказали в emit выше
        emit("  ⚠ {name}: не перекодировал из {codec} в H.264 ({err})",
             name=os.path.basename(path), codec=codec, err=str(e))
        return path
    emit("  {name} → H.264 (After Effects не читает {codec})",
         name=os.path.basename(path), codec=codec)
    return dst


def to_ae_media(path: str, emit: Any = None) -> str:
    """Один вход для сборки: картинку чинит to_ae_image, видео — to_ae_video."""
    return to_ae_video(path, emit) if _media_kind(path) == "video" else to_ae_image(path, emit)


def strip_bg_file(path: str, dest_dir: str | None = None, emit: Any = None) -> str:
    """Убрать фон у УЖЕ лежащего файла (кнопка на карточке вставки). Исходник не трогаем.
    dest_dir задан -> прозрачный <имя>-nobg.png кладём сразу в базу (<dest>/photos), а не
    рядом с исходником в «Скаченное»: чистая картинка нужна в базе, мусор в Downloads — нет.
    Если файл уже с альфой — вернём его же (нечего снимать)."""
    emit = wrap_emit(emit)
    path = os.path.abspath(path)
    if _media_kind(path) == "video":
        raise ReelsiError("это видео — фон снимается только у фото")
    from PIL import Image
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 250:  # type: ignore[operator]  # PIL getextrema returns tuple[int, int]
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


def add_file(path: str, desc: str, kind: str, src: str = "stock", emit: Any = console_emit,
             ru: str = "", look: str = "", embed: bool = True) -> str:
    """Дописать ГОТОВЫЙ файл в индекс базы БЕЗ полного рескана: эмбеддинг описания
    и запись под локом. Общая часть генерации (add_generated) и стока
    (core/stock.py): файл уже лежит в папке базы, нужен вектор по описанию — и
    следующие ролики найдут его автоподбором бесплатно. `src` уезжает в desc_src
    (generated/stock): по нему видно, откуда файл взялся. embed=False — эмбеддинг
    сейчас не считаем (пакетная генерация делает его одним вызовом через
    embed_items после того, как сгенерятся все картинки).
    Возвращает путь записи (тот же, что приняли)."""
    emit = wrap_emit(emit)
    path = os.path.abspath(path)
    desc = desc or os.path.splitext(os.path.basename(path))[0]
    ru = (ru or "").strip()
    new: dict[str, Any] = {"path": path, "name": os.path.basename(path), "type": kind,
           "used": 0, "desc": desc, "desc_src": src, "emb": None,
           "added": time.time()}   # свежесть: при равном score побеждает новый
    if ru:
        new["ru"] = ru
    lk = _norm_look(look)
    if lk:
        new["look"] = lk
    if embed:
        with _LOCK:                                         # короткое чтение: есть ли база вообще
            indexed = _load() is not None
        if indexed:                                         # вектор — вне лока: сетевой вызов может висеть
            vec_src, vec = _item_vectors([new])[0]          # та же дверь, что у скана: текст или картинка
            if vec is not None:
                new["emb"], new["emb_src"] = vec, vec_src
    with _LOCK:                                             # атомарно load+modify+save под локом
        data = _load()
        if data:
            items = data.setdefault("items", [])
            if not any(it["path"] == path for it in items):
                items.append(new)
                _save(data)
                emit("база: +{name} ({src})", name=os.path.basename(path), src=src)
        else:
            emit("база не построена — файл сохранён, в индекс попадёт при скане")
    return path


def add_generated(img_bytes: bytes, query: str, dest_dir: str, emit: Any = None, embed: bool = True, ru: str = "", look: str = "") -> str:
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
    # «эмбеддинг + запись в индекс» — общая часть со стоком, живёт в add_file
    return add_file(path, query or slug, "photo", src="generated", emit=emit,
                    ru=ru, look=look, embed=embed)


def embed_items(items: Sequence[Sequence[str]], emit: Any = None) -> int:
    """Дозаполнить векторы сгенерённым картинкам ОДНИМ пакетом сервиса (вместо по одному
    на картинку). items: [(path, desc), ...] или [(path, desc, ru), ...].
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
        by_p = {paths.pkey(os.path.abspath(it.get("path") or "")): it
                for it in data.get("items", [])}
        probes: list[dict[str, Any]] = []
        for p, d, r in norm_items:
            cur = by_p.get(paths.pkey(p)) or {}
            probes.append({"path": p, "desc": d or "", "desc_src": cur.get("desc_src") or "generated",
                           "ru": r or (cur.get("ru") or ""), "vis": cur.get("vis") or ""})
    vecs = _item_vectors(probes)         # сетевой вызов — вне лока; та же дверь, что у скана
    if not any(v is not None for _, v in vecs):
        return 0
    with _LOCK:
        data = _load()
        if not data:
            return 0
        # между тактами индекс мог измениться — ищем по пути заново
        idx = {os.path.abspath(it["path"]): it for it in data.get("items", [])}
        n = 0
        for (p, _, _), (src, v) in zip(norm_items, vecs):
            it = idx.get(p)
            if it is not None and v is not None:
                it["emb"], it["emb_src"] = v, src
                n += 1
        if n:
            _save(data)
            emit("база: эмбеддинги дозаполнены для {count} сгенерённых картинок (пакетно)", count=n)
        return n


def _real_case(p: str) -> str:
    r"""Реальный регистр пути на диске (Windows case-insensitive ФС). Без \\?\ префикса."""
    try:
        r = os.path.realpath(p)
    except ReelsiError: raise
    except Exception:
        # realpath не справился (битая ссылка и т.п.) — путь берём как был; регистр тогда не исправлен, это не критично
        return p
    if r.startswith("\\\\?\\"):
        r = r[4:]
    return r


def _build_base_index(dest: str) -> dict[str, list[str]]:
    """basename(lower) -> [абс. пути] для всех медиафайлов в базе (photos/videos, рекурсивно)."""
    idx: dict[str, list[str]] = {}
    for sub in ("photos", "videos"):
        d = os.path.join(dest, sub)
        if not os.path.isdir(d):
            continue
        for root, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x != TRASH_DIR]  # удалённое в корзине — не база вставок
            for fn in fns:
                idx.setdefault(fn.lower(), []).append(os.path.join(root, fn))
    return idx


def _resolve_by_basename(name: str, base_idx: dict[str, list[str]], kind: str | None = None) -> str | None:
    """Найти файл по имени (без учёта регистра) внутри базы. Путь или None."""
    for cand in (base_idx.get((name or "").lower()) or []):
        ck = _media_kind(cand)
        if kind is None or ck is None or ck == kind:
            return cand
    return None


# Границы формы маски вставки, % от авторасчёта. JS-скраббер маски (static/app/20-widgets.js,
# scrubMask) держит те же числа; сторож — тест в tests/test_build_and_prompts.py. Шире кадра
# маска и так упирается в край кадра, поэтому верхняя граница — предел ручки, а не размер кадра.
MASK_PCT_MIN = 20
MASK_PCT_MAX = 1000


# Память картинки — настройка, которую юзер подбирает под КАРТИНКУ, а не под ролик: маска
# (mw/mh), масштаб (sc), точка покоя (x/y), снятый фон (nobg). Лежит за файлом в записи
# items[] индекса, поэтому подобранное один раз приезжает в следующий ролик. Значения по
# умолчанию (100/100, 100, 0) не пишем — запоминать нечего, как и у mw/mh.
MEMO_KEYS = ("mw", "mh", "sc", "x", "y", "nobg")
SC_MIN, SC_MAX = 10, 400         # пределы скраббера масштаба (static/app/20-widgets.js, scrubScale)
MEMO_XY_MAX = 10000              # |x|, |y| в px: дальше любого кадра — уже мусор


def _memo_num(v: Any) -> float | None:
    """Число из поля памяти или None. bool — не число: True не должен стать «1 %»."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _memo_apply(it: dict[str, Any], fields: dict[str, Any]) -> None:
    """Записать поля памяти в запись `it` (на месте). Битое значение — поле пропускаем
    без ошибки; значение по умолчанию — поле УБИРАЕМ из записи.
    mw/mh берём парой: одна без другой кропа не задаёт. Единственное место, где проверяются
    поля памяти, — и через него пишут и правка (remember_look), и сборка (adopt)."""
    w, h = _memo_num(fields.get("mw")), _memo_num(fields.get("mh"))
    if w is not None and h is not None:
        mw, mh = int(round(w)), int(round(h))
        if MASK_PCT_MIN <= mw <= MASK_PCT_MAX and MASK_PCT_MIN <= mh <= MASK_PCT_MAX:
            if mw == 100 and mh == 100:
                it.pop("mw", None)
                it.pop("mh", None)
            else:
                it["mw"], it["mh"] = mw, mh
    sc = _memo_num(fields.get("sc"))
    if sc is not None and SC_MIN <= sc <= SC_MAX:
        sc = round(sc, 2)
        if sc == 100:
            it.pop("sc", None)
        else:
            it["sc"] = sc
    for k in ("x", "y"):
        v = _memo_num(fields.get(k))
        if v is not None and abs(v) <= MEMO_XY_MAX:
            v = round(v, 2)
            if v == 0:
                it.pop(k, None)
            else:
                it[k] = v
    nobg = fields.get("nobg")
    if isinstance(nobg, bool):                 # и True, и False — оба память («фон оставить»)
        it["nobg"] = nobg


def _memo_of(it: dict[str, Any]) -> dict[str, Any]:
    """Поля памяти картинки из элемента переноса (adopt): MEMO_KEYS, что пришли. nobg=False —
    тоже значение (не None), поэтому проверка именно на None."""
    return {k: it[k] for k in MEMO_KEYS if it.get(k) is not None}


def remember_look(path: str, fields: dict[str, Any]) -> bool:
    """Запомнить настройку картинки за файлом базы (поля MEMO_KEYS).

    True — запись найдена (и сохранена, если что-то изменилось). False — файла нет в
    индексе: картинка ещё не в базе. Это не ошибка: память придёт при сборке, когда файл
    переедет в базу (adopt), а до того её просто некуда записать. Чужие ключи и битые
    значения пропускаем молча.

    Load-modify-save под _LOCK, как reject: _load() отдаёт ОДИН закэшированный dict, и
    параллельная правка затёрла бы эту запись."""
    if not path or not isinstance(fields, dict):
        return False
    key = paths.pkey(os.path.abspath(path))
    with _LOCK:
        data = _load()
        if not data:
            return False
        for it in data.get("items", []):
            if paths.pkey(os.path.abspath(it.get("path") or "")) == key:
                before = {k: it[k] for k in MEMO_KEYS if k in it}
                _memo_apply(it, fields)
                if {k: it[k] for k in MEMO_KEYS if k in it} != before:
                    _save(data)
                return True
    return False


def look_of(path: str) -> dict[str, Any]:
    """Запомненная настройка картинки: поля MEMO_KEYS, что стоят в записи. Нет записи — {}."""
    if not path:
        return {}
    key = paths.pkey(os.path.abspath(path))
    with _LOCK:
        data = _load()
        if not data:
            return {}
        for it in data.get("items", []):
            if paths.pkey(os.path.abspath(it.get("path") or "")) == key:
                return {k: it[k] for k in MEMO_KEYS if k in it}
    return {}


def _read_import_log(logp: str) -> dict[str, Any] | None:
    """Прошлый лог переносов `_import_log.json` (путь -> файл) или None.

    None — файл ЕСТЬ, но не читается или не словарь: перезаписать его значило бы стереть
    все прошлые переносы. Файла нет — пустой словарь: начинаем с чистого."""
    if not os.path.exists(logp):
        return {}
    try:
        data = json.load(open(logp, encoding="utf-8"))
    except ReelsiError: raise
    except Exception as ex:
        log.warning("_import_log.json не прочитан (%s): %s", logp, ex)
        return None
    if not isinstance(data, dict):
        log.warning("_import_log.json — не словарь (%s: %s)", logp, type(data).__name__)
        return None
    return data


def adopt(items: Sequence[dict[str, Any]], dest_dir: str, emit: Any = None) -> dict[str, str]:
    """Прибрать в базу файлы, которые РЕАЛЬНО ушли в проект (вызывается на сборке .jsx).
    items = [{"path":..., "desc":..., "mw":..., "mh":..., "sc":..., "x":..., "y":..., "nobg":...}],
    desc = запрос вставки: с ним файл найдётся автоподбором в следующих роликах, без прогона
    «Описать через ИИ»; поля памяти (MEMO_KEYS) — то, как картинка стояла в ролике в момент
    сборки. Пишутся в запись базы теми же правилами, что правка (_memo_apply), — и для
    переехавшего файла, и для уже лежащего в базе.
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
    mapping: dict[str, str]
    seen: dict[str, Any]
    transferred: dict[str, str]
    memos: dict[str, dict[str, Any]]
    mapping, seen, transferred, memos = {}, {}, {}, {}
    for it in items:
        p = (it.get("path") or "").strip().strip('"')
        if not p:
            continue
        p = os.path.abspath(p)
        kind = _media_kind(p)
        desc = (it.get("desc") or "").strip()
        ru = (it.get("ru") or "").strip()
        memo = _memo_of(it)
        p_nc = paths.pkey(p)
        # 1) уже в базе (без учёта регистра)? — НЕ двигаем, только нормализуем путь.
        # В mapping кладём ТОЛЬКО если путь реально изменился (регистр/слеши) —
        # контракт: mapping = «старый путь -> новый», identity-записей не плодим.
        if (p_nc == dest_nc) or p_nc.startswith(dest_nc + os.sep):
            real = _real_case(p) or p
            if real != p:
                mapping[p] = real                           # media -> реальный регистр
            seen[real] = {"desc": desc, "ru": ru}
            if memo:
                memos[real] = memo
            continue
        # 2) файл есть по точному пути? — переезжаем
        src: str | None
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
        except ReelsiError: raise
        except Exception as e:
            emit("{name}: {err}", name=fn, err=str(e))
            continue
        real = _real_case(tgt) or tgt
        mapping[p] = real                                   # старый путь -> новый (реальный регистр)
        transferred[p] = real
        seen[real] = {"desc": desc, "ru": ru}
        if memo:
            memos[real] = memo
        emit("в базу: {name}", name=fn)
    if transferred:                                         # лог переносов (как у import_media)
        os.makedirs(dest, exist_ok=True)
        logp = os.path.join(dest, "_import_log.json")
        old = _read_import_log(logp)                        # None — файл есть, но не читается
        if old is None:                                     # не перезаписываем: стёрли бы прошлое
            emit("⚠ лог переносов не прочитан: {path} — не перезаписываю, файлы уже перенесены",
                 path=logp)
        else:
            old.update(transferred)
            atomic_json_dump(logp, old, indent=1)
    if seen:
        _index_adopt(mapping, seen, emit, memos)
    if transferred:
        emit("прибрано в базу: {count} файлов -> {dest}", count=len(transferred), dest=dest)
    return mapping


def _index_adopt(mapping: dict[str, str], seen: dict[str, Any], emit: Any = None,
                 memos: dict[str, dict[str, Any]] | None = None) -> None:
    """Индекс после adopt: переехавшим чиним path (used/desc сохраняются), новых
    дописываем с desc=запрос и ru=русская подпись. Полного рескана не делаем.
    Сравнения — БЕЗ учёта регистра (Windows case-insensitive ФС: desktop==Desktop).

    memos {путь: {поля памяти}} — как картинка стояла в ролике в момент сборки. Пишем ПОВЕРХ
    прошлой памяти: последняя ручная подгонка и есть правильная (юзер её только что видел
    в предпросмотре). Пишет тот же _memo_apply, что правка: 100/0 — поле убирается."""
    emit = wrap_emit(emit)
    # правки путей и новых записей — под локом (как reject/add_generated): _load() отдаёт
    # общий закэшированный dict, и параллельный /api/rembg или генерация затирали правку.
    # Эмбеддинги новых описаний — ВТОРЫМ тактом, вне лока: между тактами
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
        fresh: list[dict[str, Any]] = []        # снимки записей для векторов (вне лока)
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
                fresh.append(dict(it))          # без текста тоже — у него вектор картинки
            else:
                updated = False
                if desc and not (it.get("desc") or "").strip():
                    it["desc"], it["desc_src"] = desc, "adopted"   # был без описания — теперь с запросом
                    updated = True
                if ru and not (it.get("ru") or "").strip():
                    it["ru"] = ru
                    updated = True
                if updated:
                    fresh.append(dict(it))
            mm = (memos or {}).get(path)
            if mm:
                _memo_apply(it, mm)                            # память картинки — та же дверь, что у правки
        _save(data)
    if fresh:                                              # векторы одним пакетом, вне лока
        got = _item_vectors(fresh)
        found = [(f, src, v) for f, (src, v) in zip(fresh, got) if v is not None]
        if found:
            with _LOCK:
                data = _load()
                if data:
                    by_p = {paths.pkey(it.get("path") or ""): it
                            for it in data.get("items", [])}
                    for f, src, v in found:
                        it = by_p.get(paths.pkey(f.get("path") or ""))
                        # между тактами описание могли поменять — вектор чужого текста не приписываем
                        if it is not None and (it.get("desc") or "") == (f.get("desc") or "") \
                           and (it.get("ru") or "") == (f.get("ru") or ""):
                            it["emb"], it["emb_src"] = v, src
                    _save(data)



def import_media(dirs: Sequence[str], dest: str, since_ts: float = 0.0, move: bool = True, emit: Any = None, recursive: bool = False) -> dict[str, Any]:
    """Перенести (move) медиа-вставки из папок-источников в СВОЮ папку базы:
    dest/photos и dest/videos. Берутся только файлы наших типов (фильтры как в scan)
    с mtime >= since_ts; по умолчанию БЕЗ подпапок (recursive=False — чтобы из Downloads
    не уехали вложенные Telegram Desktop и пр.). Пишется лог переносов dest/_import_log.json
    (old->new), пути в insertlib.json обновляются (used/desc сохраняются). -> dict(count, dest)."""
    emit = wrap_emit(emit)
    dest = os.path.abspath(dest)
    dest_nc = paths.pkey(dest)
    moved, mapping = 0, {}
    d: Any
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
                except ReelsiError: raise
                except Exception as e:
                    emit("⚠ {name}: {err}", name=fn, err=str(e))
                    continue
                mapping[os.path.abspath(p)] = tgt
                moved += 1
    if mapping:                                                  # лог + обновить пути в индексе
        os.makedirs(dest, exist_ok=True)
        logp = os.path.join(dest, "_import_log.json")
        old = _read_import_log(logp)                             # None — файл есть, но не читается
        if old is None:                                          # не перезаписываем: стёрли бы прошлое
            emit("⚠ лог переносов не прочитан: {path} — не перезаписываю, файлы уже перенесены",
                 path=logp)
        else:
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


def _thumb_b64(path: str) -> str | None:
    """Кадр файла: фото/гиф — сам файл, видео — кадр из середины; всё ужато до 512px
    (ffmpeg). -> base64 jpeg | None."""
    import subprocess, base64, tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    try:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if _media_kind(path) == "video":
            dur = probe_duration(path)                    # общая проба: кэш + таймаут
            mid = max(0.0, dur / 2) if dur else 1.0       # середина ролика (не прочли — 1 с)
            cmd += ["-ss", f"{mid:.2f}", "-i", path, "-frames:v", "1"]
        else:
            cmd += ["-i", path, "-frames:v", "1"]
        cmd += ["-vf", "scale='min(512,iw)':-2", "-q:v", "5", tmp.name]
        subprocess.run(cmd, capture_output=True, timeout=60)
        with open(tmp.name, "rb") as f:
            b = f.read()
        return base64.b64encode(b).decode() if b else None
    except ReelsiError: raise
    except Exception as e:
        # кадр не вытащен (ffmpeg нет или не запустился) — None; файл останется без вектора картинки
        log.warning("кадр не вытащен (%s)", e)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass  # временный файл уже убран


def set_desc(path: str, desc: str | None) -> dict[str, Any]:
    """Ручная правка описания файла (+эмбеддинг). -> dict(ok=True)|dict(error=...)."""
    desc = " ".join((desc or "").split()).strip()
    path = os.path.abspath(path)
    # правка desc под локом (как reject/add_generated): _load() отдаёт общий закэшированный
    # dict, и параллельная генерация/режет-запросы затирали правку. Вектор — вторым тактом,
    # вне лока: сетевой вызов сервиса под _LOCK повесил бы всю базу, пока сервис занят.
    with _LOCK:
        d = _load()
        if not d:
            return dict(error="индекс не построен")
        target = new_desc = cur_ru = None
        probe: dict[str, Any] = {}
        for it in d.get("items", []):
            if paths.pkey(it["path"]) == paths.pkey(path):
                it["desc"] = desc or _norm_text(it["path"])
                it["desc_src"] = "user" if desc else "name"
                it["emb"] = None
                it.pop("emb_src", None)
                new_desc = it["desc"]
                cur_ru = it.get("ru") or ""
                cur_vis = it.get("vis") or ""
                probe = {"path": it["path"], "desc": new_desc, "desc_src": it["desc_src"],
                         "ru": cur_ru, "vis": cur_vis}
                target = it["path"]
                _save(d)
                break
    if target is None:
        return dict(error="файла нет в индексе")
    src, v = _item_vectors([probe])[0]      # сетевой вызов — вне лока; та же дверь, что у скана
    if v is not None:
        with _LOCK:
            d = _load()
            if d:
                for it in d.get("items", []):
                    if paths.pkey(it["path"]) == paths.pkey(path):
                        # описание/ru/vis могли смениться между тактами — не приписывать
                        # вектор, посчитанный по старому тексту
                        if (it.get("desc") or "") == new_desc and (it.get("ru") or "") == cur_ru \
                           and (it.get("vis") or "") == cur_vis:
                            it["emb"], it["emb_src"] = v, src
                            d["emb_model"] = _emb_model()
                        _save(d)
                        break
    return dict(ok=True, desc=new_desc)


def items_list(q: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
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


def stats(emit: Any = print) -> dict[str, Any]:
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
    try:
        import sys
        cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
        if len(sys.argv) >= 2 and sys.argv[1] == "scan":
            build_index(sys.argv[2:], emit=console_emit)
        elif len(sys.argv) >= 3 and sys.argv[1] == "match":
            for r in match(" ".join(sys.argv[2:]), k=8):
                print(f"{r['score']:.3f}  [{r['type']}] used={r['used']}  {r['path']}")
        elif len(sys.argv) >= 5 and sys.argv[1] == "import":
            import datetime as _dt
            since = _dt.datetime.strptime(sys.argv[2], "%Y-%m-%d").timestamp()
            import_media(sys.argv[4:], sys.argv[3], since_ts=since, emit=console_emit)
        elif len(sys.argv) >= 2 and sys.argv[1] == "stats":
            stats()
        else:
            print(__doc__)
    except ReelsiError as e:
        cli_error(e)

