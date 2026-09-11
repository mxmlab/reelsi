# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Feasibility analysis for generating Premiere 'Source Text' FlatBuffer blobs.
We have ~157 real, Premiere-validated blobs (one per subtitle word) in Timeline 2.xml.
Goal: prove we can build a blob for an arbitrary word and trust it.
Strategy tested here:
  1. Locate the embedded Source Text string in each blob (== effect <name>).
  2. Group blobs by text byte-length; verify same-length blobs are byte-identical
     outside the text region  -> proves pure substitution works for equal length.
  3. Test a relocation rule for different lengths and validate against real pairs.
Report written to analyze_report.txt (UTF-8).
"""
import re, base64, collections, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import paths   # noqa: E402

# Референсный XML из Premiere (с реальными блобами) — первым аргументом.
XML = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(paths.ROOT), "Timeline 2.xml")
OUT = open(paths.root("analyze_report.txt"), "w", encoding="utf-8")
def p(*a):
    print(*a, file=OUT)

txt = open(XML, encoding="utf-8").read()

# Pull each GraphicAndType effect block: its name + Source Text value, in order.
blocks = re.findall(
    r'<effect>\s*<name>(.*?)</name>\s*<effectid>GraphicAndType</effectid>.*?'
    r'<name>Source Text</name>\s*<hash>[0-9a-f-]+</hash>\s*<value>([A-Za-z0-9+/=]+)</value>',
    txt, re.S)
p(f"matched effect blocks: {len(blocks)}")

def find_str_region(b, word):
    """Return (start_of_len_prefix, byte_len, padded_total) for the FlatBuffer
    string whose content == word (utf-8)."""
    wb = word.encode("utf-8")
    L = len(wb)
    for i in range(0, len(b) - 4):
        if int.from_bytes(b[i:i+4], "little") == L and b[i+4:i+4+L] == wb:
            return i, L
    return None

recs = []
for name, val in blocks:
    word = name.strip()
    b = base64.b64decode(val)
    reg = find_str_region(b, word)
    recs.append((word, b, reg))

found = [r for r in recs if r[2]]
p(f"blobs where embedded text == effect name: {len(found)}/{len(recs)}")
missing = [r[0] for r in recs if not r[2]][:10]
if missing:
    p("  examples not matched:", missing)

# size prefix check: first 8 bytes little-endian uint64 == len(b)-8 ?
p("\n-- size-prefix check (first u32 vs len-8) --")
for word, b, reg in found[:5]:
    pre = int.from_bytes(b[0:4], "little")
    p(f"  word={word!r} blob={len(b)} prefix_u32={pre} len-8={len(b)-8}")

# Group by text byte length, check equal-length blobs identical outside text region
p("\n-- equal-length substitution test --")
bylen = collections.defaultdict(list)
for word, b, reg in found:
    bylen[reg[1]].append((word, b, reg))

ok_groups = 0
for L, items in sorted(bylen.items()):
    if len(items) < 2:
        continue
    w0, b0, r0 = items[0]
    base_blank = bytearray(b0); s0 = r0[0]
    base_blank[s0+4:s0+4+L] = b"\x00"*L  # zero out the text
    all_same = True
    for w, b, r in items[1:]:
        if len(b) != len(b0) or r[0] != s0:
            all_same = False; break
        bb = bytearray(b); bb[r[0]+4:r[0]+4+L] = b"\x00"*L
        if bytes(bb) != bytes(base_blank):
            all_same = False; break
    flag = "IDENTICAL-outside-text" if all_same else "DIFFERS"
    if all_same: ok_groups += 1
    p(f"  textlen={L:2d} n={len(items):2d} blobsize={len(b0)} -> {flag}  words={[it[0] for it in items[:4]]}")

p(f"\nequal-length groups that are pure-substitution: {ok_groups}")

# Relocation test: can we turn blob for word A into a valid blob for word B (diff len)?
# Hypothesis: blob = [8-byte sizeprefix][body]. Body has exactly one place where the
# text string lives. Changing text length shifts everything after the string end.
# The only external pointer to the string is one uoffset. We test empirically:
# take two real blobs of DIFFERENT text length but otherwise 'same template family'
# (same blobsize - textlen*? ) and see how they differ.
p("\n-- cross-length raw diff (two words, different byte length) --")
def show(word,b,reg):
    return f"word={word!r} blobsize={len(b)} textlen={reg[1]} text_at=0x{reg[0]:x}"
# pick samples of a few different lengths
seen={}
for word,b,reg in found:
    seen.setdefault(reg[1],(word,b,reg))
keys=sorted(seen)[:6]
for k in keys:
    p("  "+show(*seen[k]))

# For two adjacent lengths, compare prefix (up to text) and suffix (after text+pad).
if len(keys)>=2:
    (wa,ba,ra),(wb,bb,rb)=seen[keys[0]],seen[keys[1]]
    pa_end=ra[0]; pb_end=rb[0]
    p(f"\n  prefix-before-text equal up to min? "
      f"{ba[:pa_end]==bb[:pb_end] if pa_end==pb_end else 'len differ'} "
      f"(a_text_at={pa_end} b_text_at={pb_end})")

OUT.close()
print("report written")
