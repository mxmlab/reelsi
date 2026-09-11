# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Майнинг ручных правок (tools/mine_edits.py): разница xml/xml.bak → термины и
плохие слова. Ключевое здесь — что майнинг НЕ льёт новые коллизии (защита terms.learn
по транслиту) и что `--apply` трогает ровно два личных файла и ничего больше.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import mine_edits  # noqa: E402


def _sub_clip(w, start, end, idx):
    return ('<clipitem id="s%d"><start>%d</start><end>%d</end>'
            '<filter><effect><name>%s</name><effectid>GraphicAndType</effectid>'
            '<effectcategory>graphic</effectcategory><effecttype>filter</effecttype>'
            '<mediatype>video</mediatype>'
            '<parameter><parameterid>1</parameterid><name>Source Text</name>'
            '<value>x</value></parameter></effect></filter></clipitem>'
            % (idx, start, end, w))


def write_xml(folder, words):
    """Минимальный XML, который parse_full читает как последовательность слов-субтитров."""
    subs = "".join(_sub_clip(w, 10 + i * 20, 30 + i * 20, i) for i, w in enumerate(words))
    body = ('<xmeml version="4"><sequence><name>t</name><duration>60000</duration>'
            '<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>'
            '<media><video><format><samplecharacteristics><width>1080</width>'
            '<height>1920</height><pixelaspectratio>square</pixelaspectratio>'
            '</samplecharacteristics></format><track>%s</track></video></media>'
            '</sequence></xmeml>') % subs
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "01_test.xml").write_text(body, encoding="utf-8")


def write_pair(tmp_path, bak_words, xml_words):
    """Папка результата с парой xml (.bak = версия ДО правок). -> --root."""
    out = tmp_path / "out"
    write_xml(out, bak_words)
    (out / "01_test.xml").rename(out / "01_test.xml.bak")
    write_xml(out, xml_words)
    return str(tmp_path)


def _repl(old, new, clip="01_test.xml"):
    """Замена по словам (как их отдаёт difflib по opcode replace)."""
    return dict(old=old.split(), new=new.split(), clip=clip,
                kind=mine_edits.classify(old, new))


def test_classify():
    """Три вида замены — три разных пути: звёздочка -> плохое слово, латиница/аббревиатура
    -> термин, цифры и обычные слова -> только счётчик."""
    assert mine_edits.classify("ТАДАЛОФИЛ", "ТАДА*ОФИЛ") == "bad"
    assert mine_edits.classify("ДСИП", "MOTS-C") == "term"
    assert mine_edits.classify("ЛП", "ЛПНП") == "term"          # кириллическая аббревиатура
    assert mine_edits.classify("3 4", "3-4") == "num"
    assert mine_edits.classify("000", "тысяч") == "num"
    assert mine_edits.classify("СОХРАНЯЮ", "СОХРАНЯЙ") == "grammar"


def test_report_disputes_colliding_variant_and_removes_old():
    """Главный кейс задания: вариант «модси» у «ДСИП» — это ослышка MOTS-C. Когда майнинг
    предлагает MOTS-C, вариант «ДСИП» под ним спорен (коллизия с существующим термином
    ДСИП), а «модси» у существующего ДСИП снимается (снято)."""
    terms_data = {"terms": [{"term": "ДСИП", "variants": ["модси"]}]}
    edits = [_repl("ДСИП", "MOTS-C"), _repl("MOT", "MOTS-C")]
    rep = mine_edits.build_report([], edits, [], terms_data)
    assert ("MOTS-C", "ДСИП", "ДСИП") in rep["disputed"]
    assert ("ДСИП", "модси", "MOTS-C") in rep["removed"]
    # MOTS-C принят с вариантом MOT, у ДСИП модси убран
    assert rep["accepted"][mine_edits.terms._norm_tight("MOTS-C")]["variants"] == ["MOT"]
    assert rep["final"][mine_edits.terms._norm_tight("ДСИП")]["variants"] == []


def test_report_keeps_variant_of_same_term():
    """«MOT» — родной вариант MOTS-C (три правки юзера), а не коллизия: чужой термин
    исключается, если это сам MOTS-C."""
    edits = [_repl("MOT", "MOTS-C")]
    rep = mine_edits.build_report([], edits, [], {"terms": []})
    assert not rep["disputed"]
    assert rep["accepted"][mine_edits.terms._norm_tight("MOTS-C")]["variants"] == ["MOT"]


def test_report_merges_by_tight_key():
    """«TB500» и «TB-500» — один препарат: предложенный термин вливается в существующий,
    а не заводит второй (иначе вариант «TV50» упрётся в чужую запись того же названия)."""
    terms_data = {"terms": [{"term": "TB-500", "variants": ["TG50"]}]}
    edits = [_repl("TV50", "TB500")]
    rep = mine_edits.build_report([], edits, [], terms_data)
    assert not rep["accepted"]                                # не новый термин — слияние
    tb = rep["final"][mine_edits.terms._norm_tight("TB-500")]
    assert tb["variants"] == ["TG50", "TV50"]


def test_apply_touches_only_terms(tmp_path):
    """`--apply` на копии: добавляет термины, снимает спорный вариант у ДСИП и НЕ
    трогает список плохих слов — тот правится только руками (⚙ → «Слова»)."""
    root = write_pair(tmp_path, ["ДСИП", "А", "MOT", "Б", "ТАДАЛОФИЛ", "В", "ТАДАЛОФИЛ", "НОРМА"],
                      ["MOTS-C", "А", "MOTS-C", "Б", "ТАДА*ОФИЛ", "В", "ТАДА*ОФИЛ", "НОРМА"])
    terms_p = str(tmp_path / "terms.json")
    bad_p = str(tmp_path / "badwords.user.txt")
    with open(terms_p, "w", encoding="utf-8") as f:
        json.dump({"terms": [{"term": "ДСИП", "variants": ["модси"]}]}, f)
    with open(bad_p, "w", encoding="utf-8") as f:
        f.write("убива\n")

    assert mine_edits.main(["--root", root, "--terms", terms_p,
                            "--badwords", bad_p, "--apply"]) == 0

    by = {t["term"]: t["variants"] for t in json.load(open(terms_p, encoding="utf-8"))["terms"]}
    assert by.get("MOTS-C") == ["MOT"]                  # термин добавлен с вариантом
    assert by["ДСИП"] == []                             # модси снят, диссип не было
    assert open(bad_p, encoding="utf-8").read() == "убива\n"   # список не тронут
    extras = [p.name for p in tmp_path.iterdir() if p.name not in
              ("terms.json", "badwords.user.txt", "out")]
    assert not extras, f"--apply наследил: {extras}"


def test_badwords_filters_short_stems():
    """Основы короче 4 букв (и, из, пк, тет) уходят в bad_short и не попадают в bad_ready."""
    edits = [
        _repl("И", "*", clip="01.xml"),
        _repl("И", "*", clip="02.xml"),
        _repl("ИЗ", "*", clip="01.xml"),
        _repl("ПК", "*", clip="01.xml"),
        _repl("ТЕТ", "*", clip="01.xml"),
        _repl("РЕТАТРУТИД", "РЕТА*РУТИД", clip="01.xml"),
        _repl("РЕТАТРУТИД", "РЕТА*РУТИД", clip="02.xml"),
    ]
    rep = mine_edits.build_report([], edits, [], {"terms": []}, ok_stems=[])
    assert "и" in rep["bad_short"]
    assert "из" in rep["bad_short"]
    assert "пк" in rep["bad_short"]
    assert "тет" in rep["bad_short"]
    assert "ретатрутид" in rep["bad_ready"]
    assert not any(len(s) < 4 for s in rep["bad_ready"])


def test_badwords_never_applied(tmp_path):
    """Плохие слова НЕ вносятся ни с `--apply`, ни с `--apply --all` — только отчёт.

    Пойманный баг: один такой прогон засыпал `badwords.user.txt` мусором выравнивания
    («и», «из», «тет»), и сверка по подстроке зацензурила 106 слов из 203 в клипе."""
    root = write_pair(
        tmp_path,
        ["ОДНОКРАТНОЕ", "А", "ПОВТОР", "Б", "ПОВТОР", "В", "ИГЗ", "Г", "ИГЗ"],
        ["ОДНОКРА*НОЕ", "А", "ПОВ*ОР", "Б", "ПОВ*ОР", "В", "И*З", "Г", "И*З"]
    )
    bad_p = str(tmp_path / "badwords.user.txt")
    terms_p = str(tmp_path / "terms.json")
    with open(terms_p, "w", encoding="utf-8") as f:
        json.dump({"terms": []}, f)
    with open(bad_p, "w", encoding="utf-8") as f:
        f.write("убива\n")

    for extra in ([], ["--all"]):
        assert mine_edits.main(["--root", root, "--terms", terms_p,
                                "--badwords", bad_p, "--apply"] + extra) == 0
        assert open(bad_p, encoding="utf-8").read() == "убива\n"


def test_badwords_filters_okwords():
    """Слова из okwords (белый список) в плохие не предлагаются вовсе."""
    edits = [
        _repl("СТРАХУЕТ", "СТРАХ*ЕТ", clip="01.xml"),
        _repl("СТРАХУЕТ", "СТРАХ*ЕТ", clip="02.xml"),
        _repl("БЛЯШКА", "БЛЯ*КА", clip="01.xml"),
        _repl("ХУЙНЯ", "ХУ*НЯ", clip="01.xml"),
        _repl("ХУЙНЯ", "ХУ*НЯ", clip="02.xml"),
    ]
    rep = mine_edits.build_report([], edits, [], {"terms": []}, ok_stems=["страх", "бляшк"])
    assert "страхует" not in rep["bad_ready"] and "страхует" not in rep["bad_single"] and "страхует" not in rep["bad_short"]
    assert "бляшка" not in rep["bad_ready"] and "бляшка" not in rep["bad_single"] and "бляшка" not in rep["bad_short"]
    assert "хуйня" in rep["bad_ready"]
