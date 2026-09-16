# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Build subtitle GraphicAndType clipitems by cloning a real template clip and
swapping the word text, its Source Text FlatBuffer blob, ids and timing."""
import os, re

from core import paths
from core.fileio import atomic_text_write
from core.subtitle_blobs import BlobLibrary
from core.xmltext import xml_text as _xml_escape
TICKS_PER_FRAME = 4233600000
GFX_IN = 216000  # fixed in-point into the graphic media (mirrors reference)


PHRASE_GAP = 0.30  # сек: пауза между словами, закрывающая строку субтитров раньше лимита


def build_sub_rows(words, per_row=1, max_rows=1, cut_bounds=None, word_timings=None):
    """Сгруппировать слова субтитров в строки (задания CH, CJ, CR, CV).

    words — список кортежей (start, end, word) либо словарей {"w", "start", "end"}.
    per_row — максимум слов в ОДНОЙ строке (дефолт 1).
    max_rows — максимум строк в реплике (дефолт 1; при >1 реплика вмещает per_row * max_rows слов).
    cut_bounds — кадры cut-таймлайна, где кончаются/начинаются куски монтажа (реплика не
    переносится через границу склейки).
    word_timings — необязательный список настоящих таймингов [{"w", "start", "end"}, ...]
    в секундах на cut-таймлайне (сайдкар <стем>.words.json, задания CR, CV).
    Возвращает список строк [
        {"text": str, "start": int, "end": int, "row": int, "repl": int,
         "words": [{"w", "start", "end", "idx"}]}
    ].
    """
    if isinstance(max_rows, (list, set, tuple)):
        cut_bounds = max_rows
        max_rows = 1
    per_row = max(1, int(per_row or 1))
    max_rows = max(1, int(max_rows or 1))
    bounds = sorted(set(int(b) for b in (cut_bounds or [])))
    if per_row <= 1 and max_rows <= 1:
        out = []
        for i, item in enumerate(words):
            if isinstance(item, dict):
                s, e, w = int(item["start"]), int(item["end"]), item["w"]
            else:
                s, e, w = int(item[0]), int(item[1]), str(item[2])
            out.append({
                "text": w,
                "start": s,
                "end": e,
                "row": 0,
                "repl": i,
                "words": [{"w": w, "start": s, "end": e, "idx": i}],
            })
        return out

    # При группировке в строки разворачиваем элементы с несколькими словами
    flat_words = []
    for i, item in enumerate(words):
        if isinstance(item, dict):
            s, e, w = int(item["start"]), int(item["end"]), str(item.get("w", "")).strip()
            item_idx = item.get("idx", i)
        else:
            s, e, w = int(item[0]), int(item[1]), str(item[2]).strip()
            item_idx = i
        parts = w.split()
        if len(parts) <= 1:
            clean_w = w.strip(".,!?;:…«»\"'()[]-‐‑ ").strip()
            flat_words.append({"w": clean_w, "raw_w": w, "start": s, "end": e, "idx": item_idx, "is_last": True})
        else:
            n = len(parts)
            dur = e - s
            for pi, part in enumerate(parts):
                ps = int(s + pi * dur / n)
                pe = int(s + (pi + 1) * dur / n) if pi < n - 1 else e
                clean_p = part.strip(".,!?;:…«»\"'()[]-‐‑ ").strip()
                flat_words.append({"w": clean_p, "raw_w": part, "start": ps, "end": pe, "idx": item_idx, "is_last": (pi == n - 1)})

    if not flat_words:
        return []

    # Разбираем word_timings
    wt_list = []
    if word_timings:
        if isinstance(word_timings, dict) and "words" in word_timings:
            wt_list = word_timings["words"]
        elif isinstance(word_timings, list):
            wt_list = word_timings

    words_per_replica = per_row * max_rows
    rows, cur = [], []
    repl_idx = 0

    def flush():
        nonlocal cur, repl_idx
        if not cur:
            return
        r_start = cur[0]["start"]
        r_end = cur[-1]["end"]
        # Раскладываем слова реплики по строкам ровно по per_row слов (задание CJ)
        for row_num in range(max_rows):
            chunk = cur[row_num * per_row : (row_num + 1) * per_row]
            if not chunk:
                break
            r_text = " ".join(x["w"] for x in chunk)
            rows.append({
                "text": r_text,
                "start": r_start,
                "end": r_end,
                "row": row_num,
                "repl": repl_idx,
                "words": list(chunk),
            })
        repl_idx += 1
        cur = []

    def _has_cut_after(k):
        if not bounds or k >= len(flat_words) - 1:
            return False
        s_next = flat_words[k + 1]["start"]
        e_cur = flat_words[k]["end"]
        return any(e_cur <= b <= s_next or abs(s_next - b) <= 1 for b in bounds)

    def _raw_word_for(k):
        if k < 0 or k >= len(flat_words):
            return ""
        item = flat_words[k]
        if not item.get("is_last", True):
            return ""
        i = item["idx"]
        if wt_list and 0 <= i < len(wt_list):
            raw = str(wt_list[i].get("w") or "").strip()
            if raw:
                return raw
        return item.get("raw_w") or item.get("w") or ""

    def _punct_type(k):
        raw = _raw_word_for(k)
        if not raw:
            return None
        s = raw.rstrip(" \t\r\n\"'»”)›”’]")
        if not s:
            return None
        for p in ('.', '!', '?', '…'):
            if s.endswith(p):
                return "sent"
        for p in (',', ';', ':', '—', '–', '-'):
            if s.endswith(p):
                return "phrase"
        return None

    def _has_pause_after(k):
        if not wt_list or k >= len(flat_words) - 1:
            return False
        item, nxt = flat_words[k], flat_words[k + 1]
        if item["idx"] == nxt["idx"]:
            return False
        i1, i2 = item["idx"], nxt["idx"]
        if 0 <= i1 < len(wt_list) and 0 <= i2 < len(wt_list):
            t1_end = float(wt_list[i1].get("end") or 0)
            t2_start = float(wt_list[i2].get("start") or 0)
            return (t2_start - t1_end >= PHRASE_GAP)
        return False

    for k, item in enumerate(flat_words):
        if cur and _has_cut_after(k - 1):
            flush()

        cur.append(item)

        ptype = _punct_type(k)
        is_sent = (ptype == "sent")
        is_phrase = (ptype == "phrase" and len(cur) >= per_row / 2.0)
        is_pause = _has_pause_after(k)

        if is_sent:
            # 1. слово кончается на ., !, ?, … — конец предложения, строку закрыть ВСЕГДА (задание CV)
            flush()
        elif is_phrase or is_pause:
            # 2/3. конец фразы (не меньше половины per_row) или пауза: проверка сирот
            cnt = 0
            for nxt_k in range(k + 1, len(flat_words)):
                cnt += 1
                nxt_ptype = _punct_type(nxt_k)
                nxt_sent = (nxt_ptype == "sent")
                nxt_phrase = (nxt_ptype == "phrase" and cnt >= per_row / 2.0)
                nxt_pause = _has_pause_after(nxt_k)
                if _has_cut_after(nxt_k - 1) or nxt_sent or nxt_phrase or nxt_pause or cnt >= per_row:
                    break
            if cnt == 1:
                # Следующая строка осталась бы сиротой из 1 слова
                if len(cur) + cnt <= words_per_replica:
                    pass  # текущая строка вмещает остаток — не рвём заранее
                elif len(cur) >= 3:
                    popped = cur.pop()
                    flush()
                    cur.append(popped)
                else:
                    flush()
            else:
                flush()

        if len(cur) >= words_per_replica:
            flush()

    flush()
    return rows


def format_srt_time(sec):
    """Секунды -> время SRT `ЧЧ:ММ:СС,ммм`. Единственная точка перевода времени в
    SRT на весь проект (её же берёт core/align._ts).

    Через целые миллисекунды и divmod: ручной перенос `ms >= 1000 -> s += 1` не
    переносился дальше, и 59.9996 давало «00:00:60,000» — невалидный SRT."""
    total_ms = round(max(0.0, float(sec)) * 1000)
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(rows, srt_path):
    """Выгрузка готовых строк субтитров из плана сцены в стандартный .srt файл."""
    grouped = []
    for r in rows:
        repl_id = r.get("repl")
        if repl_id is not None and grouped and grouped[-1].get("repl") == repl_id:
            prev = grouped[-1]
            prev_text = str(prev.get("w") or "").strip()
            cur_text = str(r.get("w") or "").strip()
            if prev_text and cur_text:
                prev["w"] = prev_text + "\n" + cur_text
            elif cur_text:
                prev["w"] = cur_text
            prev["e"] = max(float(prev["e"]), float(r["e"]))
        else:
            grouped.append(dict(r))

    lines = []
    for i, r in enumerate(grouped, 1):
        s_sec = float(r["s"])
        e_sec = float(r["e"])
        if e_sec <= s_sec:
            e_sec = s_sec + 0.1
        t0_str = format_srt_time(s_sec)
        t1_str = format_srt_time(e_sec)
        text = str(r.get("w") or "").strip()
        lines.append(f"{i}\n{t0_str} --> {t1_str}\n{text}\n")
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    # атомарно: SRT — результат шага, пустой файл на месте живого хуже отсутствия (IB, п. 2)
    atomic_text_write(srt_path, content)


class SubtitleBuilder:
    def __init__(self, template_path=None, refblobs_path=None, ref_xml=None):
        template_path = template_path or paths.data("sub_template.xml")
        self.template = open(template_path, encoding="utf-8").read()
        # exact blob string in the template, to be replaced per word
        m = re.search(r'<name>Source Text</name>\s*<hash>[0-9a-f-]+</hash>\s*<value>([A-Za-z0-9+/=]+)</value>',
                      self.template)
        self.tmpl_blob = m.group(1)
        # the word currently embedded in the template effect <name>
        m2 = re.search(r'<name>([^<]*)</name>\s*<effectid>GraphicAndType</effectid>', self.template)
        self.tmpl_word = m2.group(1)
        # ids are template-specific; detect them so any source template works
        self.tmpl_clip = re.search(r'<clipitem id="(clipitem-\d+)">', self.template).group(1)
        self.tmpl_master = re.search(r'<masterclipid>(masterclip-\d+)</masterclipid>', self.template).group(1)
        self.tmpl_file = re.search(r'<file id="(file-\d+)"', self.template).group(1)
        refblobs_path = refblobs_path or paths.data("refblobs.json")
        if os.path.exists(refblobs_path):
            self.lib = BlobLibrary.load(refblobs_path)   # harvested from good files
        else:
            ref_xml = ref_xml or os.path.join(os.path.dirname(paths.ROOT), "Timeline 2.xml")
            self.lib = BlobLibrary.from_reference(ref_xml)

    def clip(self, word, start, end, cid, uid, scale=100.0, fps=60.0):
        """Return one subtitle <clipitem> xml. start/end are sequence output frames.
        scale (%) shrinks the whole graphic so long words fit with margins."""
        length = max(1, end - start)
        out = GFX_IN + length
        blob = self.lib.make(word)
        s = self.template
        if abs(scale - 100.0) > 0.01:             # font reduction for long words
            sstr = f"{scale:g}"
            if "." not in sstr:
                sstr += "."                        # Premiere writes integers as "100."
            s = re.sub(r'(<name>Scale</name>.*?<value>-?\d+,)[0-9.]+(,0,0,0,0,0,0</value>)',
                       rf'\g<1>{sstr}\g<2>', s, count=1, flags=re.S)
        # ids (detected from the template, so any source file works)
        s = s.replace(f'<clipitem id="{self.tmpl_clip}">', f'<clipitem id="clipitem-{cid}">', 1)
        s = s.replace(self.tmpl_master, f"masterclip-{uid}")
        s = s.replace(self.tmpl_file, f"file-{uid}")
        # timing — value-agnostic so the template's own numbers don't matter
        s = re.sub(r"<start>-?\d+</start>", f"<start>{start}</start>", s, count=1)
        s = re.sub(r"<end>-?\d+</end>", f"<end>{end}</end>", s, count=1)
        s = re.sub(r"<in>-?\d+</in>", f"<in>{GFX_IN}</in>", s, count=1)
        s = re.sub(r"<out>-?\d+</out>", f"<out>{out}</out>", s, count=1)
        tpf = int(round(254016000000 / (fps or 60.0)))
        s = re.sub(r"<pproTicksIn>-?\d+</pproTicksIn>",
                   f"<pproTicksIn>{GFX_IN*tpf}</pproTicksIn>", s, count=1)
        s = re.sub(r"<pproTicksOut>-?\d+</pproTicksOut>",
                   f"<pproTicksOut>{out*tpf}</pproTicksOut>", s, count=1)
        # the word (effect name) + its Source Text blob
        s = s.replace(f"<name>{self.tmpl_word}</name>", f"<name>{_xml_escape(word)}</name>", 1)
        s = s.replace(self.tmpl_blob, blob, 1)
        return s


if __name__ == "__main__":
    sb = SubtitleBuilder()
    print("template word:", repr(sb.tmpl_word), "blob len:", len(sb.tmpl_blob))
    import xml.etree.ElementTree as ET
    for i, (w, a, b) in enumerate([("ПРИВЕТ", 0, 20), ("мир", 20, 35), ("ТЕСТ&<", 35, 50)]):
        c = sb.clip(w, a, b, 500 + i, 1000 + i)
        ET.fromstring(c)  # must be well-formed
        eff = re.search(r'<name>([^<]*)</name>\s*<effectid>GraphicAndType', c).group(1)
        st = re.search(r'<start>(\d+)</start>', c).group(1)
        en = re.search(r'<end>(\d+)</end>', c).group(1)
        print(f"  ok word={w!r} effectname={eff!r} start={st} end={en}")
    print("subs.py self-test OK")
