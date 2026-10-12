# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук превью шага 1 после пересборки клипа под тем же именем XML.

Баг (2026-10-12): рилс 01 удалили в корзину вместе с исходником, поменяли тайминги и
нажали «в рилсы», потом ИИ-нарезку. Новая нарезка легла под тем же именем `01_…_01.xml`.
В превью видео было новое (261,8 с), а звук — от удалённого рилса (249,3 с): `eaOpen`
не спрашивал /api/preview_audio повторно, раз XML тот же, и держал старый буфер.

Теперь звук спрашивается всегда (сервер отдаёт url `pa_<hash>.wav`, где hash уже учитывает
путь, mtime и размер исходника), а перекачка пропускается, только если url совпал.

Стенд гоняет БОЕВЫЕ `eaOpen`, `eaDecode`, `eaStop` и объявление `EA` из
`static/app/60-preview.js` — тела берутся из файла, копий в тесте нет.

Запуск:  py -3.10 -m pytest tests/test_preview_audio_reopen.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки."""
    m = re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"не нашлась функция {name}"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


def _decl(src: str, name: str) -> str:
    """Объявление `let NAME={…};` из файла — литерал на несколько строк целиком."""
    m = re.search(r"^(?:const|let) %s=" % re.escape(name), src, re.M)
    assert m is not None, f"нет объявления {name}"
    depth = 0
    for i in range(m.end(), len(src)):
        c = src[i]
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif c == ";" and depth == 0:
            return src[m.start():i + 1]
    raise AssertionError(f"не нашёлся конец объявления {name}")


# Стенд: заглушки звукового графа, тостов и фетча. Боевые тела — из файла.
STAND_HEAD = r"""
const served = [];           // пути (или {error}), которые отдаст /api/preview_audio по очереди
let onAudioReq = null;       // хук ВО ВРЕМЯ запроса звука: так приходит голос в гонке
const calls = [];            // все fetch-вызовы по порядку
let DEC = 0;                 // счётчик декодирований: каждый буфер — новый объект
let AUDIO = { decodeAudioData: async (ab) => ({ id: ++DEC, bytes: ab.byteLength }) };
function audioGraph() {}
function uiLog() {}
function toast() {}
function errText(d) { return String(d && d.error); }
globalThis.fetch = async (url, opt) => {
  calls.push(url);
  if (url === '/api/preview_audio') {
    if (onAudioReq) await onAudioReq();
    const item = served.shift();
    if (item && item.error) return { json: async () => ({ error: item.error }) };
    return { json: async () => ({ ok: true, path: item }) };
  }
  if (url.startsWith('/api/media')) {
    return { arrayBuffer: async () => new ArrayBuffer(8) };
  }
  throw new Error('неожиданный fetch ' + url);
};
"""


def _stand_body(preview: str) -> str:
    parts = [_decl(preview, "EA")]
    parts += [_func_src(preview, n) for n in ("eaStop", "eaDecode", "eaOpen")]
    return "\n".join(parts)


def _run_node(script: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="ea_reopen_") as d:
        path = Path(d) / "stand.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    last = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
    return json.loads(last)


@node
def test_same_xml_new_source_reloads_sound_and_drops_voice(tmp_path):
    """Тот же XML, сервер вернул ДРУГОЙ path (исходник пересобран): звук заменён, голос сброшен.

    На старом коде `eaOpen` выходил до запроса (`EA.xml===xml&&EA.rawUrl`), так что второго
    /api/preview_audio не было, буфер остался прежним — этот тест на нём краснеет."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    script = STAND_HEAD + _stand_body(preview) + r"""
(async () => {
  served.push('C:/out/_tmp/pa_old.wav');
  await eaOpen('C:/out/01_a_01.xml');
  const oldRaw = EA.raw;
  EA.proc = { id: 'voice' }; EA.procUrl = '/api/media?path=voice.wav';
  calls.length = 0;

  served.push('C:/out/_tmp/pa_new.wav');
  await eaOpen('C:/out/01_a_01.xml');
  console.log(JSON.stringify({
    asked: calls.filter(c => c === '/api/preview_audio').length,
    fetchedMedia: calls.filter(c => c.startsWith('/api/media')).length,
    rawReplaced: EA.raw !== null && EA.raw !== oldRaw,
    rawUrlNew: EA.rawUrl.indexOf('pa_new.wav') >= 0,
    procCleared: EA.proc === null && EA.procUrl === '',
  }));
})();
"""
    out = _run_node(script)
    assert out["asked"] == 1, "звук у сервера не спрошен при повторном открытии того же XML"
    assert out["fetchedMedia"] == 1, "новый url звука не перекачан"
    assert out["rawReplaced"] is True, "старый буфер звука остался у плеера"
    assert out["rawUrlNew"] is True
    assert out["procCleared"] is True, "обработанный голос старого исходника не сброшен"


@node
def test_same_xml_same_source_keeps_buffer_and_skips_refetch(tmp_path):
    """Тот же XML и тот же path: «Сохранить» не перекачивает звук, буфер и голос — те же объекты."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    script = STAND_HEAD + _stand_body(preview) + r"""
(async () => {
  served.push('C:/out/_tmp/pa_same.wav');
  await eaOpen('C:/out/01_a_01.xml');
  const raw = EA.raw, rawUrl = EA.rawUrl;
  const voice = { id: 'voice' };
  EA.proc = voice; EA.procUrl = '/api/media?path=voice.wav';
  calls.length = 0;

  served.push('C:/out/_tmp/pa_same.wav');
  await eaOpen('C:/out/01_a_01.xml');
  console.log(JSON.stringify({
    asked: calls.filter(c => c === '/api/preview_audio').length,
    fetchedMedia: calls.filter(c => c.startsWith('/api/media')).length,
    rawSame: EA.raw === raw,
    rawUrlSame: EA.rawUrl === rawUrl,
    procSame: EA.proc === voice,
  }));
})();
"""
    out = _run_node(script)
    assert out["asked"] == 1
    assert out["fetchedMedia"] == 0, "тот же звук перекачан — «Сохранить» стал грузить буфер заново"
    assert out["rawSame"] is True and out["rawUrlSame"] is True
    assert out["procSame"] is True, "обработанный голос сброшен при том же исходнике"


@node
def test_voice_arriving_during_a_new_source_request_is_kept(tmp_path):
    """Голос нового клипа пришёл, пока шёл запрос звука при смене url: его не выкидываем.

    eaVoice ставит EA.proc и поднимает EA.pid. Сброс голоса по смене url — только если за
    запрос pid не менялся; иначе сбросили бы свежий голос, который уже готов."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    script = STAND_HEAD + _stand_body(preview) + r"""
(async () => {
  served.push('C:/out/_tmp/pa_old.wav');
  await eaOpen('C:/out/01_a_01.xml');
  EA.proc = { id: 'old-voice' }; EA.procUrl = '/api/media?path=old.wav';
  const fresh = { id: 'new-voice' };
  served.push('C:/out/_tmp/pa_new.wav');
  onAudioReq = () => { EA.proc = fresh; EA.procUrl = '/api/media?path=new.wav'; EA.pid++; };
  await eaOpen('C:/out/01_a_01.xml');
  console.log(JSON.stringify({
    rawUrlNew: EA.rawUrl.indexOf('pa_new.wav') >= 0,
    procKept: EA.proc === fresh,
  }));
})();
"""
    out = _run_node(script)
    assert out["rawUrlNew"] is True, "новый звук не загружен"
    assert out["procKept"] is True, "голос, пришедший во время запроса, сброшен"


@node
def test_same_xml_server_error_silences_the_old_sound(tmp_path):
    """Тот же XML, сервер ответил ошибкой: старый звук не играем под новым видео — тишина.

    Иначе звук удалённого рилса продолжал бы играть поверх нарезки, которая уже не его."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    script = STAND_HEAD + _stand_body(preview) + r"""
(async () => {
  served.push('C:/out/_tmp/pa_old.wav');
  await eaOpen('C:/out/01_a_01.xml');
  const hadRaw = EA.raw !== null;
  served.push({ error: 'исходник не найден' });
  await eaOpen('C:/out/01_a_01.xml');
  console.log(JSON.stringify({
    hadRaw: hadRaw,
    rawNull: EA.raw === null,
    rawUrlEmpty: EA.rawUrl === '',
  }));
})();
"""
    out = _run_node(script)
    assert out["hadRaw"] is True, "стенд: звук до ошибки должен был загрузиться"
    assert out["rawNull"] is True, "старый буфер звука остался при ошибке сервера"
    assert out["rawUrlEmpty"] is True
