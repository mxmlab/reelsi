# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук глитча: ОДИН слой на ГРУППУ глитч-слов и огибающая слоя (ПРАВКА 1/2).

Словарь снят с эталона (1421/1422, ручная доводка пользователя):
  * подряд идущие глитч-слова накрыты одним растянутым звуком — границы прекомпов при
    группировке не учитываются, только пауза: слово входит в группу, пока начинается
    раньше конца звука группы (последнее слово + полка + спад + кадр);
  * startTime = время ПЕРВОГО слова группы − 0.567 (GLITCH_SFX_PRE_S);
  * огибающая Audio Levels: тишина до первого слова, нарастание 0.08 (ATTACK), полка на
    громкости glitch_db, спад 0.12 (RELEASE) до −48 dB; спад кончается за кадр до конца
    слоя (outPoint = конец спада + кадр).
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _glitch_wav(tmp_path):
    wav = str(tmp_path / "gltchgltch_24.wav")
    with open(wav, "wb") as f:
        f.write(b"RIFFdummy")
    return wav


def _build(xml, tmp_path, intro, wav, db=-3.5, name="out.jsx", intro_splits=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=intro_splits,
                                   style={"glitch": wav, "glitch_db": db, "intro_riser": False},
                                   intro_riser=False, disclaimer="", roto=False,
                                   emit=lambda *a: None)
    return path


def _read_jsx(path):
    return open(path, encoding="utf-8-sig").read()


def _layer_count(jsx):
    return jsx.count('var gl=main.layers.add(glitchItem); gl.name="Глитч";')


def test_три_глитч_слова_подряд_один_слой(xml_subs, tmp_path):
    """Три глитч-слова с шагом меньше длительности звука — ОДИН слой, растянутый на группу."""
    intro = [
        dict(words=["А", "Б", "В"], color="white", times=[2.0, 2.2, 2.45], anim="glitch"),
        dict(words=["ХВОСТ"], color="white", times=[8.3]),
    ]
    jsx_path = _build(xml_subs, tmp_path, intro, _glitch_wav(tmp_path), name="group.jsx")
    jsx = _read_jsx(jsx_path)

    assert _layer_count(jsx) == 1
    # startTime = ПЕРВОЕ слово группы − 0.567; слой живёт до последнего слова группы
    # (2.45) + полка 0.45 + спад 0.12 + кадр (1/60) = 3.0367
    assert "gl.startTime=1.433; gl.inPoint=2; gl.outPoint=3.0367;" in jsx


def test_пауза_длиннее_звука_два_слоя(xml_subs, tmp_path):
    """Глитч-слова, разделённые паузой длиннее звука группы, получают СВОИ слои."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0], anim="glitch"),
        dict(words=["ДАЛЕКО"], color="white", times=[5.0], anim="glitch"),
    ]
    jsx_path = _build(xml_subs, tmp_path, intro, _glitch_wav(tmp_path), name="two.jsx")
    jsx = _read_jsx(jsx_path)

    assert _layer_count(jsx) == 2
    assert "gl.startTime=1.433; gl.inPoint=2; gl.outPoint=2.5867;" in jsx
    assert "gl.startTime=4.433; gl.inPoint=5; gl.outPoint=5.5867;" in jsx


def test_граница_ровно_в_конец_звука_новый_слой(xml_subs, tmp_path):
    """Слово, начинающееся РОВНО в момент конца звука группы, — уже новая группа."""
    t_end = 2.0 + 0.45 + 0.12 + 1.0 / 60.0      # конец звука первого слова (с кадром)
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0], anim="glitch"),
        dict(words=["ВПЛОТНУЮ"], color="white", times=[t_end], anim="glitch"),
    ]
    jsx_path = _build(xml_subs, tmp_path, intro, _glitch_wav(tmp_path), name="edge.jsx")
    jsx = _read_jsx(jsx_path)

    assert _layer_count(jsx) == 2
    assert "gl.startTime=1.433; gl.inPoint=2; gl.outPoint=2.5867;" in jsx
    # второе слово: startTime = 2.5867 − 0.567 = 2.0197, outPoint = 2.5867 + 0.5867 = 3.1733
    assert "gl.startTime=2.0197; gl.inPoint=2.5867; gl.outPoint=3.1733;" in jsx


def test_четыре_ключа_audio_levels_огибающая(xml_subs, tmp_path):
    """У слоя звука глитча четыре ключа Audio Levels: −48 / db / db / −48.

    Нарастание 0.08 с, полка на громкости glitch_db (не от нуля), спад 0.12 с,
    конец спада — за кадр до конца слоя.
    """
    intro = [
        dict(words=["СЛОВО"], color="white", times=[3.0], anim="glitch"),
    ]
    wav = _glitch_wav(tmp_path)
    jsx_path = _build(xml_subs, tmp_path, intro, wav, db=-7.5, name="fade.jsx")
    jsx = _read_jsx(jsx_path)

    assert "gl.startTime=2.433; gl.inPoint=3; gl.outPoint=3.5867;" in jsx
    assert "glAlv.setValue([-7.5, -7.5]);" in jsx
    # тишина до первого слова, нарастание за 0.08, полка, спад до −48 за кадр до конца
    assert "glAlv.setValueAtTime(3, [-48, -48]);" in jsx
    assert "glAlv.setValueAtTime(3.08, [-7.5, -7.5]);" in jsx
    assert "glAlv.setValueAtTime(3.45, [-7.5, -7.5]);" in jsx
    assert "glAlv.setValueAtTime(3.57, [-48, -48]);" in jsx
    # спад кончается за кадр до конца слоя: 3.5867 − 3.57 = 1/60
    assert "glAlv.setValueAtTime(3.08, [-7.5, -7.5]);" in jsx


def test_громкость_полки_от_glitch_db_а_не_от_нуля(xml_subs, tmp_path):
    """Полка идёт от glitch_db: ноль в эталоне — это db=0, а не отдельный уровень."""
    intro = [
        dict(words=["СЛОВО"], color="white", times=[3.0], anim="glitch"),
    ]
    wav = _glitch_wav(tmp_path)
    jsx_path = _build(xml_subs, tmp_path, intro, wav, db=0.0, name="db0.jsx")
    jsx = _read_jsx(jsx_path)

    assert "glAlv.setValue([0, 0]);" in jsx
    assert "glAlv.setValueAtTime(3.08, [0, 0]);" in jsx
    assert "glAlv.setValueAtTime(3.45, [0, 0]);" in jsx


def test_звук_группируется_по_времени_а_не_по_прекомпам(xml_subs, tmp_path):
    """Глитч-слова из РАЗНЫХ прекомпов, идущие подряд по времени, — в одном звуке."""
    # Первый прекомп: глитч на 2.0. Второй прекомп (split после первой строки) —
    # глитч на 2.2: пауза меньше длительности звука первой группы (0.5867), поэтому
    # граница прекомпа не разрывает звук.
    intro = [
        dict(words=["ПЕРВЫЙ"], color="white", times=[2.0], anim="glitch"),
        dict(words=["СОСЕДНИЙ"], color="white", times=[2.2], anim="glitch"),
    ]
    jsx_path = _build(xml_subs, tmp_path, intro, _glitch_wav(tmp_path),
                         name="crossprecomp.jsx", intro_splits=[1])
    jsx = _read_jsx(jsx_path)

    assert _layer_count(jsx) == 1
    # один слой от первого слова первого прекомпа
    assert "gl.startTime=1.433; gl.inPoint=2; gl.outPoint=2.7867;" in jsx
