# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Censor platform-unsafe words in subtitles by starring one letter.

Two lists, one stem per line, lowercase. Matching is by substring on the normalised
word, so a stem like `убива` covers its forms. Lines starting with `#` are comments.

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
DEFAULT_OK = []
DEFAULTS = {"bad": DEFAULT_BAD, "ok": DEFAULT_OK}
_norm_re = re.compile(r"[^\w]+", re.UNICODE)
_cache = {"bad": (None, None, DEFAULT_BAD), "ok": (None, None, DEFAULT_OK)}


def path(kind):
    """Файл, по которому список работает сейчас: свой (правка из UI), иначе из поставки."""
    p = USER_PATHS[kind]
    return p if os.path.exists(p) else BASE_PATHS[kind]


def is_custom(kind):
    """Список правили из UI (лежит свой файл)?"""
    return os.path.exists(USER_PATHS[kind])


def _parse(text):
    """Stems from the text of a list file (one per line, `#` comments)."""
    return [s for s in (ln.split("#", 1)[0].strip().lower()
                        for ln in (text or "").splitlines()) if s]


def _load(kind):
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


def _bad():
    """Current bad-word stems (live)."""
    return _load("bad")


def _ok():
    """Current allow-list stems (live)."""
    return _load("ok")


def is_bad(word):
    """True if `word` should be censored: matches a bad stem and no ok exception."""
    low = _norm_re.sub("", (word or "").lower())
    if not low:
        return False
    if any(o in low for o in _ok()):
        return False
    return any(b in low for b in _bad())


def censor(word):
    """Star one (middle) letter if the word should be censored. Case preserved."""
    if is_bad(word):
        m = max(1, len(word) // 2)     # у 1-буквенного слова не стирать единственную букву
        return word[:m] + "*" + word[m + 1:]
    return word


# ---- правка списков из настроек (⚙ → «Слова») ----

def read_text(kind):
    """Текст списка для правки в настройках: свой, если заведён, иначе из поставки."""
    try:
        return open(path(kind), encoding="utf-8").read()
    except OSError:
        return "\n".join(DEFAULTS[kind]) + "\n"


def write_text(kind, text):
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


def reset(kind):
    """Убрать свой список — вернуться к тому, что идёт в поставке."""
    try:
        os.remove(USER_PATHS[kind])
    except OSError:
        pass                                      # своего файла и не было
    _cache[kind] = (None, None, DEFAULTS[kind])
    return len(_load(kind))


def info():
    """Для настроек: тексты обоих списков, свой ли он и сколько в нём стемов."""
    return {k: {"text": read_text(k), "custom": is_custom(k), "count": len(_load(k))}
            for k in ("bad", "ok")}
