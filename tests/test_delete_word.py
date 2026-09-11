# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты удаления слова субтитров (delete_word) и переиндексации наборов.

  * delete_word убирает слово из XML: слов стало на одно меньше, остальные те же и в том же порядке;
  * delete_word на несуществующем индексе возвращает error, XML не тронут;
  * резервная копия создаётся тем же механизмом, что у edit_word;
  * функция сдвига индексов: набор {2,5,7}, удалён 5 -> {2,6}; удалён 2 -> {4,6};
    удалён 1 -> {1,4,6}; удалён 9 -> {2,5,7} без изменений;
  * поле from строки интро сдвигается так же, а при удалении ровно того слова обнуляется;
  * count строки интро уменьшается, а строка с count==0 исчезает;
  * ручка /api/delete_word отвечает и возвращает индекс.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402
from core.xml2ae.highlights import shift_indices, shift_intro_rows  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(scope="module")
def client():
    import webui
    return webui.app.test_client()


def test_delete_word_removes_from_xml(xml_subs):
    """delete_word убирает слово из XML: слов стало на одно меньше, остальные те же и в том же порядке."""
    meta_before, cams_before, subs_before, ins_before = xml2ae.parse_full(xml_subs)
    orig_count = len(subs_before)
    target_idx = 4
    target_word = subs_before[target_idx][2]

    res = xml2ae.delete_word(xml_subs, target_idx)
    assert res.get("ok") is True
    assert res.get("index") == target_idx
    assert res.get("word") == target_word

    meta_after, cams_after, subs_after, ins_after = xml2ae.parse_full(xml_subs)
    assert len(subs_after) == orig_count - 1

    # Слова до целевого индекса не изменились
    for i in range(target_idx):
        assert subs_after[i] == subs_before[i]

    # Слова после целевого индекса сдвинулись на 1 позицию, сохранив порядок и текст
    for i in range(target_idx, len(subs_after)):
        assert subs_after[i][2] == subs_before[i + 1][2]
        assert subs_after[i][0] == subs_before[i + 1][0]
        assert subs_after[i][1] == subs_before[i + 1][1]


def test_delete_word_bad_index_returns_error_and_xml_untouched(xml_subs):
    """delete_word на несуществующем индексе возвращает error, XML не тронут."""
    raw_before = open(xml_subs, encoding="utf-8").read()
    subs_before = xml2ae.parse_full(xml_subs)[2]

    # Отрицательный индекс
    r_neg = xml2ae.delete_word(xml_subs, -1)
    assert r_neg.get("error")

    # Слишком большой индекс
    r_huge = xml2ae.delete_word(xml_subs, 100500)
    assert r_huge.get("error")

    raw_after = open(xml_subs, encoding="utf-8").read()
    assert raw_after == raw_before

    subs_after = xml2ae.parse_full(xml_subs)[2]
    assert len(subs_after) == len(subs_before)


def test_delete_word_creates_backup_same_as_edit_word(xml_subs):
    """Резервная копия создаётся тем же механизмом, что у edit_word (_backup_once)."""
    bak_path = xml_subs + ".bak"
    assert not os.path.exists(bak_path)

    subs_orig = xml2ae.parse_full(xml_subs)[2]
    res = xml2ae.delete_word(xml_subs, 0)
    assert res.get("ok") is True

    # Бэкап создан рядом
    assert os.path.isfile(bak_path)
    # В бэкапе сохранился исходный набор слов
    subs_in_bak = xml2ae.parse_full(bak_path)[2]
    assert len(subs_in_bak) == len(subs_orig)

    # В модифицированном файле на 1 слово меньше
    subs_after = xml2ae.parse_full(xml_subs)[2]
    assert len(subs_after) == len(subs_orig) - 1

    # Вторая правка не перезаписывает .bak
    bak_mtime = os.path.getmtime(bak_path)
    xml2ae.delete_word(xml_subs, 0)
    assert os.path.getmtime(bak_path) == bak_mtime


def test_shift_indices_set_and_list():
    """Функция сдвига индексов:
      набор {2,5,7}, удалён 5 -> {2,6};
      удалён 2 -> {4,6};
      удалён 1 -> {1,4,6};
      удалён 9 -> {2,5,7} без изменений.
    """
    # Удалён элемент из середины (5) -> 5 удалён, 7 уменьшилось до 6
    assert shift_indices({2, 5, 7}, 5) == {2, 6}

    # Удалён 2: равный удалённому убирается, большие уменьшаются на единицу -> {4, 6}
    assert shift_indices({2, 5, 7}, 2) == {4, 6}

    # Удалён элемент перед всеми (1) -> все больше 1 уменьшаются на единицу -> {1, 4, 6}
    assert shift_indices({2, 5, 7}, 1) == {1, 4, 6}

    # Удалён 9 (после всех) -> без изменений
    assert shift_indices({2, 5, 7}, 9) == {2, 5, 7}

    # Для списков то же самое
    assert shift_indices([2, 5, 7], 5) == [2, 6]
    assert shift_indices([2, 5, 7], 9) == [2, 5, 7]

    # Для отдельного числа / None
    assert shift_indices(5, 5) is None
    assert shift_indices(7, 5) == 6
    assert shift_indices(2, 5) == 2
    assert shift_indices(None, 5) is None


def test_shift_intro_rows_from_and_count():
    """Поле from строки интро сдвигается так же, а при удалении ровно того слова обнуляется.
    count строки интро уменьшается, а строка с count==0 исчезает.
    """
    # 1. Поле from больше del_idx -> уменьшается на 1
    rows1 = [{"from": 5, "count": 2, "color": "yellow"}]
    res1 = shift_intro_rows(rows1, 2)
    assert len(res1) == 1
    assert res1[0]["from"] == 4
    assert res1[0]["count"] == 2

    # 2. Удаление ровно того слова, на которое указывал from -> from обнуляется в None,
    #    а так как слово было в строке (range(5, 7)), count уменьшается с 2 до 1
    rows2 = [{"from": 5, "count": 2, "color": "yellow"}]
    res2 = shift_intro_rows(rows2, 5)
    assert len(res2) == 1
    assert res2[0]["from"] is None
    assert res2[0]["count"] == 1

    # 3. Удаление слова внутри строки интро (слово 6 в строке range(5, 7)) -> count уменьшается
    rows3 = [{"from": 5, "count": 2, "color": "yellow"}]
    res3 = shift_intro_rows(rows3, 6)
    assert len(res3) == 1
    assert res3[0]["from"] == 5
    assert res3[0]["count"] == 1

    # 4. Строка с count == 1 при удалении её единственного слова получает count == 0 и исчезает
    rows4 = [{"from": 5, "count": 1, "color": "yellow"}]
    res4 = shift_intro_rows(rows4, 5)
    assert len(res4) == 0

    # 5. Строка без from (from: None), потребляет с начала (0, 1)
    rows5 = [{"from": None, "count": 2, "color": "white"}]
    res5 = shift_intro_rows(rows5, 0)
    assert len(res5) == 1
    assert res5[0]["count"] == 1
    assert res5[0]["from"] is None


def test_api_delete_word(client, xml_subs):
    """Ручка /api/delete_word отвечает и возвращает индекс удалённого слова."""
    meta, cams, subs_before, ins = xml2ae.parse_full(xml_subs)
    first_word = subs_before[0][2]

    # Успешное удаление слова #0
    resp = client.post("/api/delete_word", json={"xml": xml_subs, "index": 0})
    data = resp.get_json()
    assert data.get("ok") is True
    assert data.get("index") == 0
    assert data.get("word") == first_word

    subs_after = xml2ae.parse_full(xml_subs)[2]
    assert len(subs_after) == len(subs_before) - 1

    # Ошибка: несуществующий XML
    resp_nofile = client.post("/api/delete_word", json={"xml": "not_exist.xml", "index": 0})
    data_nofile = resp_nofile.get_json()
    assert "error" in data_nofile

    # Ошибка: плохой индекс
    resp_badidx = client.post("/api/delete_word", json={"xml": xml_subs, "index": 99999})
    data_badidx = resp_badidx.get_json()
    assert "error" in data_badidx
