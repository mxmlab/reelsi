# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Censor platform-unsafe words in subtitles by starring one letter.

Two lists, one stem per line, lowercase. Matching is by substring on the normalised
word, so a stem like `убива` covers its forms. Lines starting with `#` are comments.

A line may be written `=слово` — then it matches the WHOLE word only, never a
substring: `=аст` stars «аст» and leaves «часто» alone, while the bare stem `аст`
would poison both, and an ok-list `=тесто` whitelists «тесто» without disarming
`тест` for «тестостерон». Spaces after `=` do not matter (`= аст` == `=аст`); a lone
`=` is an empty line and is skipped.

The star lands inside the SHORTEST stem that fired, in its middle: a word is usually made
of a stem plus an ending, so `трен` in «тренболон» must read as «тр*нболон» — a star in
the middle of the whole word («трен*олон») would leave the stem readable and the bleep
useless.

- `badwords.txt` — stems to star.
- `okwords.txt` — allow-list. A word that matches a bad stem is left ALONE if it also
  matches an ok-stem. This is how legit words that merely CONTAIN a bad substring
  survive — `бля` would otherwise star `бляшка`, `хуе` would star `страхует`. Add the
  collision to the allow-list to whitelist it.

Both files ship with the app and carry a GENERIC list. What a given channel has to bleep
is its own business, so the lists are edited in the UI (⚙ → «Слова», `/api/censor_words`)
and the edited copy lands NEXT to them: `badwords.user.txt` / `okwords.user.txt`
(gitignored, with their own `REELSI_BADWORDS`/`REELSI_OKWORDS` like the rest of the
personal state). Once a user file exists it REPLACES the shipped one instead of adding
to it — otherwise a stem could never be REMOVED from the UI, and that is half the edits.

Lists reload when their file changes (by mtime), so edits take effect without a restart.
"""
import os, re
from typing import Any

from core import paths
from core.app_meta import env
from core.fileio import atomic_text_write

BASE_PATHS = {"bad": paths.data("badwords.txt"),
              "ok": paths.data("okwords.txt")}
# REELSI_BADWORDS / REELSI_OKWORDS — как REELSI_TERMS: без своей переменной тестовый
# профиль (порт 5098) правил бы боевые списки.
USER_PATHS = {"bad": env("BADWORDS") or paths.root("badwords.user.txt"),
              "ok": env("OKWORDS") or paths.root("okwords.user.txt")}
DEFAULT_BAD = [
    # starter set — used only if badwords.txt is missing too
    "наркотик", "суицид", "убива", "убить", "насил",
]
DEFAULT_OK: list[str] = []
DEFAULTS = {"bad": DEFAULT_BAD, "ok": DEFAULT_OK}
_norm_re = re.compile(r"[^\w]+", re.UNICODE)
# Тот же класс символов, что в `_norm_re`, но по одной позиции: по нему строится карта
# индексов нормализованного слова в исходном. Один источник, иначе `low` и карта разъедутся.
_word_char = re.compile(r"\w")
_cache: dict[str, tuple[str | None, float | None, list[str]]] = {"bad": (None, None, DEFAULT_BAD), "ok": (None, None, DEFAULT_OK)}


def path(kind: str) -> str:
    """Файл, по которому список работает сейчас: свой (правка из UI), иначе из поставки."""
    p = USER_PATHS[kind]
    return p if os.path.exists(p) else BASE_PATHS[kind]


def is_custom(kind: str) -> bool:
    """Список правили из UI (лежит свой файл)?"""
    return os.path.exists(USER_PATHS[kind])


def _parse(text: str | None) -> list[str]:
    """Stems from the text of a list file (one per line, `#` comments)."""
    return [s for s in (ln.split("#", 1)[0].strip().lower()
                        for ln in (text or "").splitlines()) if s]


def _load(kind: str) -> list[str]:
    """Stems for `kind`, reloading only when the file changes on disk."""
    p = path(kind)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return DEFAULTS[kind]
    if _cache[kind][0] != p or _cache[kind][1] != mt:
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            return DEFAULTS[kind]
        _cache[kind] = (p, mt, _parse(text))
    return _cache[kind][2]


def _bad() -> list[str]:
    """Current bad-word stems (live)."""
    return _load("bad")


def _ok() -> list[str]:
    """Current allow-list stems (live)."""
    return _load("ok")


def _norm(word: str | None) -> str:
    """Слово для сравнения со стемами: lower() и только `\\w` (регистр и пунктуация прочь)."""
    return _norm_re.sub("", (word or "").lower())


def match(low: str, stems: list[str]) -> str | None:
    """Самый короткий сработавший элемент списка для нормализованного слова `low`
    (уже lower() и без не-\\w символов) или None. `=x` срабатывает при low == x,
    обычный стем — при вхождении подстрокой. Возвращает элемент БЕЗ знака '='."""
    best: str | None = None
    for it in stems:
        # У `=x` пробелы после знака не значимы; нормализуем только сам x, чтобы
        # «= узи» и «=узи» вели себя одинаково.
        s = _norm(it[1:]) if it.startswith("=") else it
        if not s:
            continue                              # строка из одного «=» — пустая
        hit = (low == s) if it.startswith("=") else (s in low)
        if hit and (best is None or len(s) < len(best)):
            best = s                              # при равной длине остаётся первый по списку
    return best


def is_bad(word: str | None) -> bool:
    """True if `word` should be censored: matches a bad stem and no ok exception."""
    low = _norm(word)
    if not low:
        return False
    if match(low, _ok()) is not None:
        return False
    return match(low, _bad()) is not None


def censor(word: str) -> str:
    """Star the middle letter of the shortest stem that fired. Case and length preserved.

    Звёздочка встаёт ВНУТРЬ сработавшего стема, а не в середину слова: стем «трен»
    в «тренболон» читался бы целиком как «трен*олон» — цензура, которая не цензурит.
    """
    low = _norm(word)
    if not low:
        return word
    if match(low, _ok()) is not None:
        return word
    s = match(low, _bad())
    if s is None:
        return word
    # Позиции `low` в исходном слове: пунктуация («bpc-157») в них не попадает, и
    # звёздочка ложится на букву, а не на дефис. `low` собирается из этого же каркаса —
    # иначе `low` и карта разъехались бы на первом же расхождении классов.
    idx = [i for i, ch in enumerate(word) if _word_char.match(ch)]
    low = "".join(word[i] for i in idx).lower()
    k = low.find(s) + len(s) // 2                 # для `=x` это 0: low == x
    return word[:idx[k]] + "*" + word[idx[k] + 1:]


# ---- правка списков из настроек (⚙ → «Слова») ----

def read_text(kind: str) -> str:
    """Текст списка для правки в настройках: свой, если заведён, иначе из поставки."""
    try:
        return open(path(kind), encoding="utf-8").read()
    except OSError:
        return "\n".join(DEFAULTS[kind]) + "\n"


def write_text(kind: str, text: str | None) -> int:
    """Сохранить свой список. Пустой текст — это ПУСТОЙ список (ничего не цензурим),
    а не «вернуть как было»: возврат к поставочному — отдельное действие (reset).
    -> сколько стемов получилось."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    # Атомарно (core.fileio): open(...,"w") усекал СПИСОК пользователя до записи, и
    # падение в этот момент оставляло пустой файл, а пустой список в UI — это
    # «ничего не цензурим» (GZ, п. A). newline="\n": список построчный, CRLF в нём
    # не нужен (переводы строк уже нормализованы выше).
    atomic_text_write(USER_PATHS[kind], text + "\n", newline="\n")
    _cache[kind] = (None, None, DEFAULTS[kind])   # mtime сменился — перечитаем с диска
    return len(_parse(text))


def reset(kind: str) -> int:
    """Убрать свой список — вернуться к тому, что идёт в поставке."""
    try:
        os.remove(USER_PATHS[kind])
    except OSError:
        pass                                      # своего файла и не было
    _cache[kind] = (None, None, DEFAULTS[kind])
    return len(_load(kind))


def info() -> dict[str, dict[str, Any]]:
    """Для настроек: тексты обоих списков, свой ли он и сколько в нём стемов."""
    return {k: {"text": read_text(k), "custom": is_custom(k), "count": len(_load(k))}
            for k in ("bad", "ok")}

