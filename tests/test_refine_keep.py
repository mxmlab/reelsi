# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подгон резов по звуку: паузы, вдохи/«кхе» и обрезанные слова.

Замер на реальных клипах (C1353/1355/1356) до правки: 6-8с тишины ОСТАВАЛОСЬ
внутри кусков, а резов, попадающих ВНУТРЬ слова, было 28-30 на клип. После —
резов внутри слова 3-7, постороннего звука вдвое меньше.

Здесь звук синтетический: точно известно, где речь, где пауза, а где «кхе».

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

import numpy as np
import pytest
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import gigaam_cut as gc  # noqa: E402

SR = 16000


def tone(dur, f=180, amp=0.3):
    """«Речь»: гармонический сигнал (низкий, как голос)."""
    t = np.arange(int(dur * SR)) / SR
    return amp * (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * 2 * f * t))


def noise(dur, amp=0.25):
    """«Кхе»/вдох: широкополосный шум."""
    return amp * np.random.RandomState(0).randn(int(dur * SR))


def hush(dur, amp=0.0005):
    return amp * np.random.RandomState(1).randn(int(dur * SR))


@pytest.fixture()
def clip(tmp_path):
    """слово(1с) · пауза(0.6с) · КХЕ(0.3с) · пауза(0.3с) · слово(1с)"""
    a = np.concatenate([tone(1.0), hush(0.6), noise(0.3), hush(0.3), tone(1.0)])
    path = str(tmp_path / "clip.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "раз", "start": 0.0, "end": 1.0},
             {"w": "два", "start": 2.2, "end": 3.2}]
    return path, words


def test_cuts_pause_and_cough(clip):
    """Пауза и «кхе» между словами уходят, оба слова остаются."""
    path, words = clip
    keep, parents = gc.refine_keep([(0.0, 3.2)], path, words=words,
                                   emit=lambda *a, **k: None)
    assert len(keep) == 2, keep
    assert keep[0][0] < 0.1 and 0.95 <= keep[0][1] <= 1.15
    # старт чуть раньше слова: WORD_PAD отдаёт речи 80мс, плюс HOLE_AIR-воздух
    assert 2.0 <= keep[1][0] <= 2.3 and keep[1][1] >= 3.05
    # «кхе» на 1.6-1.9с не должно попасть ни в один кусок
    assert not any(a <= 1.75 <= b for a, b in keep)
    assert parents == [0, 0]           # оба куска — из одного исходного


def test_parents_let_camera_stay(clip):
    """Разрез по вдоху НЕ должен менять камеру: подкуски наследуют родителя."""
    path, words = clip
    keep, parents = gc.refine_keep([(0.0, 3.2)], path, words=words,
                                   emit=lambda *a, **k: None)
    assign = [7]                        # какая бы камера ни стояла на исходном куске
    assert [assign[p] for p in parents] == [7] * len(keep)


def test_edges_follow_words(tmp_path):
    """Рез, поставленный посреди слова, отодвигается за его конец — иначе слышно
    обрубленное окончание. Почерк снят с ручной нарезки юзера: ~25мс воздуха
    перед первым словом куска и ~100мс после последнего."""
    a = np.concatenate([hush(0.5), tone(1.0), hush(0.5)])
    path = str(tmp_path / "w.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "слово", "start": 0.5, "end": 1.5}]
    # просим рез на 1.42с — это ВНУТРИ слова
    keep, _ = gc.refine_keep([(0.55, 1.42)], path, words=words,
                             emit=lambda *a, **k: None)
    assert keep and keep[0][1] >= 1.5, keep      # граница ушла за конец слова


def _one_word_clip(tmp_path, tail, name):
    """Слово 0.5с, за ним `tail` — либо затухающий хвост, либо сразу тишина."""
    a = np.concatenate([tone(0.5), tail, hush(0.4)])
    path = str(tmp_path / name)
    sf.write(path, a.astype(np.float32), SR)
    return path, [{"w": "слово", "start": 0.0, "end": 0.5}]


def test_tail_is_added_only_where_word_trails(tmp_path):
    """Главное требование юзера: отступ не «везде по 100мс», а ТАМ ГДЕ НАДО.
    CTC обрывает конец слова у 62-83% слов (замер по трём клипам), поэтому от
    края слова идём по звуку: тянущуюся букву доберём, обрывистое слово нет."""
    trail = tone(0.25) * np.linspace(1.0, 0.05, int(0.25 * SR))   # «о-о-о» затухает
    p_long, words = _one_word_clip(tmp_path, trail, "long.wav")
    p_short, _ = _one_word_clip(tmp_path, np.zeros(0), "short.wav")

    long_end = gc.refine_keep([(0.0, 0.5)], p_long, words=words,
                              emit=lambda *a, **k: None)[0][0][1]
    short_end = gc.refine_keep([(0.0, 0.5)], p_short, words=words,
                               emit=lambda *a, **k: None)[0][0][1]
    assert long_end > 0.65, long_end          # хвост забрали
    assert short_end < 0.60, short_end        # лишнего не добавили
    assert long_end - short_end > 0.1         # разница именно из-за звука


def test_edge_waits_for_the_wave_to_end(tmp_path):
    """Рез — по ОКОНЧАНИЮ волны, а не по первому тихому кадру. Внутри слова есть
    провалы (смычка перед «п/т/к», стык слогов): по ним рез садился в середину
    волны, и на слух слово обрублено. Затишьем считаем только QUIET_RUN тишины
    подряд."""
    a = np.concatenate([tone(0.3), hush(0.04), tone(0.25), hush(0.4)])
    path = str(tmp_path / "dip.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "поп", "start": 0.0, "end": 0.3}]   # CTC оборвал слово на провале

    keep, _ = gc.refine_keep([(0.0, 0.3)], path, words=words,
                             emit=lambda *a, **k: None)
    assert keep and keep[0][1] >= 0.55, keep     # вторая половина волны осталась
    assert len(keep) == 1, keep                  # провал 40мс — не дыра, кусок целый


def test_edge_keeps_old_behaviour_without_a_lull(tmp_path):
    """Затишье есть не всегда: если после слова сразу звучит вырезанный дубль,
    добирать нечего — встаём в самое тихое место окна, как раньше, и чужой звук
    в кусок не тащим."""
    a = np.concatenate([tone(0.5), hush(0.05), tone(0.3), tone(0.5, f=220)])
    path = str(tmp_path / "nolull.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "первое", "start": 0.0, "end": 0.5},
             {"w": "второе", "start": 0.85, "end": 1.35}]

    keep, _ = gc.refine_keep([(0.0, 0.5)], path, words=words,
                             emit=lambda *a, **k: None)
    assert keep and 0.5 <= keep[0][1] <= 0.62, keep


def breath(dur, amp=0.004):
    """Вдох/шум комнаты: слышно, но это не голос (на реальных клипах 12-18 дБ
    над полом против 30-45 у речи)."""
    return amp * np.random.RandomState(2).randn(int(dur * SR))


def test_start_skips_the_breath_before_the_word(tmp_path):
    """Жалоба юзера: «начало обрезает не на полной тишине». На C1414 CTC-старт
    слова опережал звук на 130мс, и в это время шумел вдох: затишья в окне не
    находилось, запасной «самый тихий кадр» садился прямо на вдох. Настоящая
    тишина была ПОЗЖЕ CTC-старта — туда и должен встать рез."""
    a = np.concatenate([hush(1.0), breath(0.35), hush(0.25), tone(1.0), hush(0.5)])
    path = str(tmp_path / "breath.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "слово", "start": 1.30, "end": 2.60}]   # CTC-старт внутри вдоха

    keep, _ = gc.refine_keep([(1.30, 2.60)], path, words=words,
                             emit=lambda *a, **k: None)
    assert keep, keep
    assert 1.36 <= keep[0][0] <= 1.60, keep      # в тишине между вдохом и словом


def test_start_is_not_glued_to_the_attack(tmp_path):
    """Обратный промах CTC — старт ПОЗЖЕ звука (на C1414 «я» опоздало на 160мс).
    Окно EDGE_IN_MAX упиралось в сплошную волну, и рез садился на атаку слова —
    в монтаже это щелчок и полслога. Опора на вход волны уводит рез в тишину."""
    a = np.concatenate([hush(0.8), tone(1.0), hush(0.5)])
    path = str(tmp_path / "late.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "слово", "start": 0.95, "end": 1.80}]   # CTC опоздал на 150мс

    keep, _ = gc.refine_keep([(0.95, 1.80)], path, words=words,
                             emit=lambda *a, **k: None)
    assert keep, keep
    assert 0.70 <= keep[0][0] <= 0.80, keep      # перед волной, но без лишнего воздуха


def test_silence_inside_a_word_span_is_still_a_hole(tmp_path):
    """Пауза, накрытая границами слова из CTC, всё равно должна вырезаться.
    Сверка с ручной доводкой (6 клипов): половина участков, которые юзер убирал
    руками, была ТИШИНОЙ, попавшей под маску речи — CTC-конец слова уехал за край
    волны, и пауза переставала считаться дырой."""
    a = np.concatenate([tone(0.5), hush(0.35), tone(0.5), hush(0.3)])
    path = str(tmp_path / "span.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "раз", "start": 0.0, "end": 0.85},    # CTC накрыл и паузу
             {"w": "два", "start": 0.85, "end": 1.35}]

    keep, _ = gc.refine_keep([(0.0, 1.35)], path, words=words,
                             emit=lambda *a, **k: None)
    assert len(keep) == 2, keep                  # пауза разрезала кусок
    assert not any(a <= 0.70 <= b for a, b in keep), keep


def test_does_not_steal_neighbour_word(tmp_path):
    """Соседнее слово, которое юзер вырезал, подтягивать НЕЛЬЗЯ: слово принадлежит
    куску, только если его середина внутри куска (иначе границы уезжали на 600мс)."""
    a = np.concatenate([tone(0.6), hush(0.3), tone(0.6)])
    path = str(tmp_path / "n.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "выкинутое", "start": 0.0, "end": 0.6},
             {"w": "нужное", "start": 0.9, "end": 1.5}]
    keep, _ = gc.refine_keep([(0.88, 1.5)], path, words=words,
                             emit=lambda *a, **k: None)
    assert keep[0][0] > 0.6, keep      # начало не уехало в выкинутое слово


def test_no_words_falls_back_to_loudness(clip):
    """Без слов работаем по громкости — хуже, но тишину всё равно режем."""
    path, _ = clip
    keep, _ = gc.refine_keep([(0.0, 3.2)], path, emit=lambda *a, **k: None)
    assert len(keep) >= 2


def test_missing_wav_raises():
    """Нет звука — refine_keep больше не возвращает keep молча, а выбрасывает ошибку."""
    with pytest.raises((sf.SoundFileError, OSError)):
        gc.refine_keep([(0.0, 1.0)], "нет-такого.wav", emit=lambda *a, **k: None)


def test_log_line_reports_hole_price(tmp_path):
    """Цена порога видна в логе одной строкой: дыр, секунд и hole_min (BZ)."""
    a = np.concatenate([tone(0.5), hush(0.6), tone(0.5)])
    path = str(tmp_path / "gap.wav")
    sf.write(path, a.astype(np.float32), SR)
    words = [{"w": "раз", "start": 0.0, "end": 0.5},
             {"w": "два", "start": 1.1, "end": 1.6}]
    lines = []
    gc.refine_keep([(0.0, 1.6)], path, words=words,
                   emit=lambda l, **k: lines.append(l))
    line = [l for l in lines if "дыр вырезано" in l]
    assert line and "(hole_min=0.15)" in line[0], lines
    assert "дыр вырезано 0" not in line[0], lines      # пауза 0.6с — дыра при 0.15
    lines = []
    gc.refine_keep([(0.0, 1.6)], path, words=words, hole=0.95,
                   emit=lambda l, **k: lines.append(l))
    line = [l for l in lines if "дыр вырезано" in l]
    assert line and "(hole_min=0.95)" in line[0] and "дыр вырезано 0" in line[0], lines
