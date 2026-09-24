# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Generate Premiere 'Source Text' FlatBuffer blobs for arbitrary subtitle words,
by reusing real, Premiere-validated blobs harvested from a reference .xml.

Proven facts (see analyze_blobs.py):
  * The embedded Source Text string starts at a FIXED offset (TEXT_OFF) in every blob.
  * Layout at TEXT_OFF: [uint32 little-endian byte-length][utf8 bytes][null pad].
  * Replacing the word with another of the SAME utf8 byte-length yields a byte-identical
    blob outside the text region  -> zero-risk substitution.
  * For other lengths we reuse a LONGER template and rewrite
    [length-prefix=L][word][0x00 terminator]; the FlatBuffer reader uses the length
    prefix, so the unchanged tail keeps every offset valid.

Public API:
    lib = BlobLibrary.from_reference(xml_path)   # harvest templates
    b64 = lib.make(word)                         # -> base64 str for <value>
"""
import re, base64, json, os, struct
from typing import Any, Sequence, cast

from core import paths
from core.umsg import ReelsiError, cli_error

TEXT_OFF = 0x168  # constant offset of the Source Text length-prefix in every blob
# Хвост FlatBuffer после текстовой области идентичен во ВСЕХ реальных блобах — разной
# длины только паддинг перед ним. Значит, под слово длиннее любого собранного шаблона
# блоб можно УДЛИНИТЬ: вставить нули перед хвостом и поправить три смещения (ровно этим
# реальные блобы разной длины и отличаются друг от друга — сверено побайтово).
_OFF_ROOT = 0                 # uint32 LE: корневой uoffset (растёт вместе с блобом)
_OFF_BACK = (280, 288)        # смещения «назад» — уменьшаются на дельту
_OFF_FWD = (356,)             # смещение «вперёд» — растёт на дельту
# Float32 at offset 204: 1067.0 = auto-width ("stretched") box; ~810 = fixed box
# that wraps/mis-centres long words. Force every blob to the stretched value.
AUTO_WIDTH_204 = struct.pack("<f", 1067.0)

_EFFECT_RE = re.compile(
    r'<name>([^<]*)</name>\s*<effectid>GraphicAndType</effectid>'
    r'[\s\S]*?<name>Source Text</name>\s*<hash>[0-9a-f-]+</hash>'
    r'\s*<value>([A-Za-z0-9+/=]+)</value>')


def _read_text(b: bytes | bytearray) -> str | None:
    """Return the embedded Source Text word (str) from a blob, or None."""
    if len(b) < TEXT_OFF + 4:
        return None
    L = int.from_bytes(b[TEXT_OFF:TEXT_OFF + 4], "little")
    if TEXT_OFF + 4 + L > len(b):
        return None
    try:
        return b[TEXT_OFF + 4:TEXT_OFF + 4 + L].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _tail_start(b: bytes | bytearray) -> int:
    """Где кончается текстовая область (текст + нулевой паддинг) и начинается хвост."""
    i = TEXT_OFF + 4 + int.from_bytes(b[TEXT_OFF:TEXT_OFF + 4], "little")
    while i < len(b) and b[i] == 0:
        i += 1
    return i


def _capacity(b: bytes | bytearray) -> int:
    """Сколько БАЙТ текста влезает в блоб (с нулевым терминатором)."""
    return _tail_start(b) - TEXT_OFF - 4 - 1


def _grow(b: bytes | bytearray, delta: int) -> bytes:
    """Копия блоба с текстовой областью, удлинённой на `delta` байт (кратно 4).
    Нули вставляются перед хвостом, три смещения правятся на дельту — так же, как
    отличаются между собой РЕАЛЬНЫЕ блобы разной длины (проверено побайтово)."""
    ts = _tail_start(b)
    out = bytearray(b[:ts]) + bytes(delta) + bytearray(b[ts:])
    for o in (_OFF_ROOT,) + _OFF_FWD:
        struct.pack_into("<I", out, o, struct.unpack_from("<I", out, o)[0] + delta)
    for o in _OFF_BACK:
        struct.pack_into("<I", out, o, struct.unpack_from("<I", out, o)[0] - delta)
    return bytes(out)


class BlobLibrary:
    def __init__(self, by_len: dict[int, bytes]) -> None:
        # by_len: dict[int byte_len] -> blob bytes
        self.by_len = dict(sorted(by_len.items()))
        self.lengths = sorted(self.by_len)
        self.max_len = self.lengths[-1] if self.lengths else 0

    @classmethod
    def from_reference(cls, xml_path: str) -> 'BlobLibrary':
        txt = open(xml_path, encoding="utf-8").read()
        by_len: dict[int, bytes] = {}
        for name, val in _EFFECT_RE.findall(txt):
            b = base64.b64decode(val)
            w = _read_text(b)
            if w is None or w != name.strip():
                continue  # only keep blobs whose embedded text matches the label
            by_len.setdefault(len(w.encode("utf-8")), b)
        if not by_len:
            raise RuntimeError("no usable Source Text blobs found in " + xml_path)
        return cls(by_len)

    def save(self, path: str) -> None:
        json.dump({str(k): base64.b64encode(v).decode() for k, v in self.by_len.items()},
                  open(path, "w"))

    @classmethod
    def load(cls, path: str) -> 'BlobLibrary':
        d = json.load(open(path))
        return cls({int(k): base64.b64decode(v) for k, v in d.items()})

    def make(self, word: str) -> str:
        """Return base64 string of a Source Text blob rendering `word`.
        Every blob is normalised to the auto-width ("stretched") box so long words
        stay on one line and centre correctly (offset 204 = 1067.0)."""
        wb = word.encode("utf-8")
        L = len(wb)
        if L in self.by_len:                      # exact length -> identical structure
            b = bytearray(self.by_len[L])
            b[TEXT_OFF + 4:TEXT_OFF + 4 + L] = wb
        else:                                     # reuse the smallest template that FITS
            bigger = [n for n in self.lengths if _capacity(self.by_len[n]) >= L + 1]
            if bigger:
                b = bytearray(self.by_len[bigger[0]])
            else:                                 # длиннее любого шаблона -> удлинить самый большой
                base = self.by_len[self.max_len]
                need = L + 1 - _capacity(base)
                b = bytearray(_grow(base, 4 * ((need + 3) // 4)))
            b[TEXT_OFF:TEXT_OFF + 4] = L.to_bytes(4, "little")  # set length prefix
            b[TEXT_OFF + 4:TEXT_OFF + 4 + L] = wb               # write word
            b[TEXT_OFF + 4 + L] = 0                             # null terminator
        if len(b) >= 208:
            b[204:208] = AUTO_WIDTH_204           # force auto-width/stretched box
        return base64.b64encode(b).decode()


# --- Coloured Source Text blobs (для ИИ-жёлтых прямо в XML) ---------------------
# Премьер кодирует «покрашенное» слово доп. полем FlatBuffer в хвосте блоба: после
# vtable-якоря идут РОВНО 3 значащих байта. AE-парсер (xml2ae._word_color) читает их
# и считает слово выделенным при ЛЮБОМ не-белом значении. Здесь только собираем такие
# блобы из реальных XML и переписываем в них текст — цвет-объект остаётся валидным,
# т.к. байты не двигаются (тот же length-prefix механизм, что и у белых).
_COLOR_ANCHOR = bytes([0x07, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x00])


def blob_is_coloured(b: bytes) -> bool:
    """Тот же критерий, что у xml2ae._word_color (без импорта xml2ae — избегаем цикла):
    после последнего якоря в rstrip-хвосте ровно 3 не-нулевых байта."""
    s = b.rstrip(b"\x00")
    if s.endswith(_COLOR_ANCHOR):
        return False
    i = s.rfind(_COLOR_ANCHOR)
    if i < 0:
        return False
    rgb = s[i + len(_COLOR_ANCHOR):]
    return len(rgb) == 3 and rgb != b"\x00\x00\x00"


def harvest_coloured(xml_paths: Sequence[str], out_json: str | None = None) -> BlobLibrary:
    """Собрать покрашенные Source Text-блобы из реальных XML, ключ = байт-длина слова.
    Возвращает BlobLibrary; при out_json — сохраняет туда."""
    by_len: dict[int, bytes] = {}
    for xp in xml_paths:
        try:
            txt = open(xp, encoding="utf-8").read()
        except ReelsiError: raise
        except Exception:
            continue
        for name, val in _EFFECT_RE.findall(txt):
            try:
                b = base64.b64decode(val)
            except ReelsiError: raise
            except Exception:
                continue
            w = _read_text(b)
            if w is None or w != name.strip() or not blob_is_coloured(b):
                continue
            by_len.setdefault(len(w.encode("utf-8")), b)   # первый встретившийся на длину
    if not by_len:
        raise RuntimeError("no coloured Source Text blobs found")
    lib = BlobLibrary(by_len)
    if out_json:
        lib.save(out_json)
    return lib


_COLOR_LIB: BlobLibrary | None = None
_WHITE_LIB: BlobLibrary | None = None


def colour_library() -> BlobLibrary:
    """Ленивая загрузка refblobs_color.json (собран из XML пользователя)."""
    global _COLOR_LIB
    if _COLOR_LIB is None:
        _COLOR_LIB = BlobLibrary.load(paths.data("refblobs_color.json"))
    return _COLOR_LIB


def library() -> BlobLibrary:
    """Ленивая загрузка белой библиотеки refblobs.json (обычные, непокрашенные слова)."""
    global _WHITE_LIB
    if _WHITE_LIB is None:
        _WHITE_LIB = BlobLibrary.load(paths.data("refblobs.json"))
    return _WHITE_LIB


if __name__ == "__main__":
    try:
        xml = os.path.join(os.path.dirname(paths.ROOT), "Timeline 2.xml")
        lib = BlobLibrary.from_reference(xml)
        lib.save(paths.data("refblobs.json"))

        log = open(paths.root("blob_selftest.txt"), "w", encoding="utf-8")
        def p(*a: Any) -> None: print(*a, file=log)
        p(f"harvested template lengths (bytes): {lib.lengths}")
        p(f"count: {len(lib.lengths)}  max: {lib.max_len}")

        # 1) Round-trip: every template must reproduce its own word exactly.
        bad = 0
        for L, b in lib.by_len.items():
            w = _read_text(b)
            rt = _read_text(base64.b64decode(lib.make(cast(str, w))))
            if rt != w:
                bad += 1; p(f"  ROUNDTRIP FAIL L={L} {w!r} -> {rt!r}")
        p(f"roundtrip exact-length: {len(lib.by_len)-bad}/{len(lib.by_len)} OK")

        # 2) Arbitrary words incl. lengths NOT present in library.
        tests = ["ПРИВЕТ", "ТЕСТОСТЕРОН", "Я", "ну", "это", "ДЛИННОЕ СЛОВО ТУТ",
                 "qwerty", "АБВ", "12345", "Ё", "переносимость", "X"]
        for w in tests:
            try:
                out = lib.make(w)
                back = _read_text(base64.b64decode(out))
                status = "OK" if back == w else f"MISMATCH->{back!r}"
            except ReelsiError: raise
            except Exception as e:
                status = f"ERR {e}"
            p(f"  make({w!r:24}) L={len(w.encode('utf-8')):2d} -> {status}")
        log.close()
        print("selftest written")
    except ReelsiError as e:
        cli_error(e)
