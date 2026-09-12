# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Оркестратор нарезки: GigaAM -> решение 27b -> пост-проход -> черновик.

Модуль называется pipeline, а не run, хотя функция в нём одна и зовётся run():
`from .run import run` в фасаде затенил бы подмодуль функцией, и `gigaam_cut.run`
означало бы то одно, то другое в зависимости от порядка импортов.

Порядок работы с VRAM здесь не косметика: на 16 ГБ держим ОДНУ тяжёлую модель за раз,
поэтому GigaAM выгружается ДО обращения к 27b, а тот — до рендера.
"""
import os, shutil, tempfile
import soundfile as sf
from core import aicut
from core import xmlbuild
from core import align
from core import sync
from core import draftrender
from core import cutstages
from core.app_meta import env, wrap_emit
from core.app_meta import console_emit
from core.fileio import atomic_json_dump
from . import tune
from .asr import transcribe_words_for_cut, transcribe_words_whole
_orig_transcribe_words_whole = transcribe_words_whole
from .decide import decide_markup
from .takes import build_cutlog, postprocess
from .tune import (_cut_breaths, _silence_bounds, apply_speaker, keep_intervals,
                   refine_keep)


def _audio_file_diag(wav_path):
    """Факты о файле звука на момент сбоя: наличие, размер, каталог."""
    exists = os.path.exists(wav_path)
    if exists:
        try:
            sz = os.path.getsize(wav_path)
            file_info = f"существует, {sz} байт ({sz / (1024 * 1024):.2f} МБ)"
        except Exception as e:
            file_info = f"существует, размер неизвестен ({e})"
    else:
        file_info = "НЕ существует"

    parent = os.path.dirname(wav_path) or "."
    if os.path.exists(parent):
        try:
            items = os.listdir(parent)
            items_str = ", ".join(items) if items else "пусто"
            dir_info = f"в '{parent}': [{items_str}]"
        except Exception as e:
            dir_info = f"каталог '{parent}' недоступен ({e})"
    else:
        dir_info = f"каталог '{parent}' НЕ существует"

    return f"WAV '{wav_path}' ({file_info}; {dir_info})"


# --------------------------------------------------------------------------- #
# Оркестратор
# --------------------------------------------------------------------------- #
def run(wav_path, cams, offsets, out, scale, model=None,
        cam_return=2, no_draft=False, speaker=None, dedupe=None, stages=None, emit=console_emit,
        engine=None):
    """Полный GigaAM-путь. Возвращает (keep, cutlog, draft_path, info).

    wav_path  — 16кГц wav камеры 1 (уже извлечён sync'ом в omni_cut)
    cams       — список путей к камерам (для xmlbuild/рендера)
    offsets    — синхронизация камер
    out        — итоговый XML (xmlbuild пишет сюда каждую итерацию)
    scale      — вертикальный scale для xmlbuild
    model      — модель LM Studio для 27b (None -> активный профиль)
    speaker    — профиль спикера (speakers/*.json): свои пороги под его студию
                 и говор. None = калибровка по спикеру A, как было
    dedupe     — чистка дублей кодом (задание CA). None = профиль спикера
                 (`speakers.CUT_DEFAULTS.dedupe`) либо дефолт True.
    stages     — словарь ступеней нарезки (задание GE); если задан, draft/dedupe/etc
                 берутся из него.
    engine     — ASR-движок с пословными таймингами (None -> aicut.cut_asr_engine()).
    """
    emit = wrap_emit(emit)
    apply_speaker(speaker, emit=emit)
    raw_stages = dict(stages) if stages is not None else {}
    if stages is None:
        raw_stages["draft"] = not no_draft
        if dedupe is not None:
            raw_stages["dedupe"] = dedupe

    norm_stages, branch = cutstages.normalize(raw_stages)

    if branch == "gigaam" and not norm_stages.get("asr", True):
        raise ValueError("GigaAM-ветка требует распознавания речи (asr=True)")

    # Отличаем явное задание dedupe (галка/флаг) от дефолта:
    # если dedupe задан явно в сыром входе — перекрываем tune.DEDUPE и печатаем упоминание галки;
    # если не задан — tune.DEDUPE не трогаем (остаётся из apply_speaker), в postprocess передаём None.
    explicit_dedupe = raw_stages.get("dedupe")
    if explicit_dedupe is not None:
        dedupe_bool = bool(explicit_dedupe)
        tune.DEDUPE = dedupe_bool
        if dedupe_bool:
            emit("  чистка дублей: включена (галка шага 1)", flush=True)
        else:
            emit("  чистка дублей: ВЫКЛЮЧЕНА (галка шага 1)", flush=True)
        postprocess_dedupe = dedupe_bool
    else:
        postprocess_dedupe = None
    work = tempfile.mkdtemp(prefix="gigaamcut_")
    # Маркер для сервера (api/jobs.py): «Стоп» = taskkill /F, атекситы не идут, и
    # gigaamcut_* с черновыми/вырезочными WAV оставался в %TEMP% — _kill_curproc
    # чистит каталог по этому маркеру. finally закрывает штатный путь и SystemExit
    # («ИИ вырезал почти весь ролик»).
    print(f"WORK_DIR={work}", flush=True)
    try:
        return _run(wav_path, cams, offsets, out, scale, model=model,
                    cam_return=cam_return, no_draft=no_draft,
                    speaker=speaker, dedupe=postprocess_dedupe, stages=norm_stages, emit=emit,
                    engine=engine)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(wav_path, cams, offsets, out, scale, model=None,
         cam_return=2, no_draft=False, speaker=None, dedupe=None, stages=None, emit=console_emit,
         engine=None):

    emit = wrap_emit(emit)
    N = len(cams)
    if stages is None:
        raw = {}
        raw["draft"] = not no_draft
        if dedupe is not None:
            raw["dedupe"] = dedupe
        stages, _ = cutstages.normalize(raw)
    else:
        stages, _ = cutstages.normalize(stages)

    if engine is None:
        engine = aicut.cut_asr_engine(emit=emit)

    # --- Шаг 1-2: GigaAM/CTC целиком -> слова с РОДНЫМИ таймингами (без wav2vec2) ---
    emit("== GigaAM whole-file нарезка ==", flush=True)
    aicut.unload_ours(emit=emit)                    # VRAM под GigaAM / CTC
    aicut.warn_foreign_models(emit=emit)
    if transcribe_words_whole is not _orig_transcribe_words_whole:
        full_text, words = transcribe_words_whole(wav_path, emit=emit)
    else:
        full_text, words = transcribe_words_for_cut(wav_path, engine=engine, emit=emit)
    if not words:
        raise RuntimeError("GigaAM не дал ни одного слова — проверь аудио")
    if stages.get("pauses") != "off":
        silence_bounds = _silence_bounds(words)
        if silence_bounds:
            emit("  тишина: {count} жёстких границ реза (всегда вырезаются)",
                 count=len(silence_bounds), flush=True)
    else:
        silence_bounds = []

    # --- Шаг 4: 27b решает ДОСЛОВНО (по всем словам, с полным контекстом) ---
    if stages.get("sense", True):
        aicut.unload_ours(emit=emit)                    # VRAM под LM Studio
        kept, drop, _notes, cutlog = decide_markup(
            words, full_text, model=model, emit=emit, silence_bounds=silence_bounds)
        rule = {i: "decide_markup" for i in drop}
    else:
        kept = set(range(len(words)))
        drop = set()
        _notes = ""
        cutlog = []
        rule = {}

    # --- Шаг 4б: чистка решения кодом (повторы/обрубки/микро-островки) ---
    # Умная модель размечает текст сама; force_takes/dedupe_repeats режут
    # перечисления и ролевую речь, veto_unique_drops возвращает верно
    # вырезанный оффтопик (замер на 5 роликах: 6 из 6 вырезов кода ошибочны).
    # Поэтому при выключенном dedupe включаем light-режим (только drop_micro_keeps).
    # История: старый замер на C1353 показал, что со спором код страховал от
    # сноса всех заходов разом («чтобы не дать» ×3), поэтому при включённом
    # dedupe (галка «Правка нарезки кодом») спор с моделью по-прежнему доступен.
    # rule — атрибуция вырезов (задание CA): кто снял кусок, видно в .cuts.json.
    postprocess(words, kept, drop, silence_bounds,
                light=(env("LIGHT_POST") == "1"), emit=emit,
                dedupe=dedupe, rule=rule)

    # --- финал: пересобрать XML по итоговому keep (на всякий случай актуальный) ---
    keep = keep_intervals(words, kept, silence_bounds)
    keep = [(s, e) for s, e in keep if round(e * 60) - round(s * 60) > 0]
    # Санитарный гард (см. тот же в omni_cut): пустой keep оставлял last_info=None,
    # и вызывающий падал невнятным AttributeError уже ПОСЛЕ полного прогона
    # GigaAM+LLM. Плюс защищаем готовый out.xml от перезаписи пустышкой.
    # Гард проверяется ТОЛЬКО когда включён sense (задание GE).
    kept_s = sum(e - s for s, e in keep)
    src_s = (words[-1]["end"] - words[0]["start"]) if words else 0.0
    if stages.get("sense", True):
        if not keep or (src_s > 0 and kept_s < 0.25 * src_s):
            raise SystemExit(
                f"ИИ вырезал почти весь ролик: осталось {kept_s:.1f}с из {src_s:.1f}с "
                f"({len(keep)} сег.). Ничего не перезаписываю — прошлая нарезка цела. "
                f"Проверь модель и промпт в настройках ⚙ и запусти ещё раз.")
    # Камеры раскладываем по СМЫСЛОВЫМ кускам, и только потом подгоняем резы по
    # звуку: refine режет фразу на части (вдох/пауза внутри), а assign_cameras
    # обязан менять камеру на соседнем куске — без наследования картинка прыгала
    # бы прямо на вдохе посреди фразы.
    assign = (align.assign_cameras(keep, N, return_every=cam_return, big_chunk_sec=6.0)
              if N > 1 else None)

    def _apply_audio_stages(cur_keep, cur_assign):
        if stages.get("refine", True):
            k, parents = refine_keep(cur_keep, wav_path, words=words, emit=emit)
            a = [cur_assign[p] for p in parents] if cur_assign is not None else None
        else:
            k = cur_keep
            a = cur_assign
        if stages.get("breath", True):
            k, a, b_marks = _cut_breaths(k, a, wav_path, words, out, emit=emit)
        else:
            b_marks = []
        return k, a, b_marks

    if stages.get("refine", True) or stages.get("breath", True):
        try:
            keep, assign, breath_marks = _apply_audio_stages(keep, assign)
        except (sf.SoundFileError, FileNotFoundError, OSError) as ex:
            diag = _audio_file_diag(wav_path)
            emit("  сбой чтения аудио ({err_type}: {err}), пробую перевыпустить WAV: {diag}",
                 err_type=type(ex).__name__, err=str(ex), diag=diag, flush=True)
            try:
                if cams:
                    sync.extract_audio(cams[0], wav_path)
                else:
                    raise RuntimeError(f"нет камер для извлечения звука ({cams})")
                keep, assign, breath_marks = _apply_audio_stages(keep, assign)
                emit("  WAV успешно перевыпущен, подгон звука и вздохи выполнены", flush=True)
            except Exception as retry_ex:
                diag_after = _audio_file_diag(wav_path)
                raise SystemExit(
                    f"Не удалось прочитать аудио для подгона нарезки и вздохов: {retry_ex}. "
                    f"Повторное извлечение звука из {cams[0] if cams else 'камеры'} не помогло. "
                    f"Диагностика: {diag_after}. Прошлая нарезка цела.") from retry_ex
    else:
        breath_marks = []

    # --- зеркальный санитарный гард: речь > 30с одним куском — отказ ---
    # Речь длиннее 30с, вышедшая ОДНИМ куском, — признак того, что ступени подгона
    # по звуку и вздохов не отработали (HOLE_MIN=0.15с на живой речи всегда режет
    # несколько дыр). Защищаем готовый XML от перезаписи неразрезанным роликом.
    # Проверяется ТОЛЬКО когда включён refine или breath (задание GE).
    if stages.get("refine", True) or stages.get("breath", True):
        kept_s = sum(e - s for s, e in keep)
        if len(keep) == 1 and (kept_s > 30.0 or src_s > 30.0):
            raise SystemExit(
                f"Нарезка вернула весь ролик одним куском: {kept_s:.1f}с "
                f"({len(keep)} сег., исходная речь {src_s:.1f}с > 30с) — "
                f"подгон по звуку и детектор вздохов не разделили речь. "
                f"Ничего не перезаписываю — прошлая нарезка цела.")

    last_info = xmlbuild.build(cams, keep, offsets, out, assign=assign,
                               scale=scale, sub_words=None, music_path=None)
    # обновить cutlog под итоговый drop (после возможных возвратов). rule — имя
    # функции/источника, снявшего каждый кусок (задание CA): без него «кто виноват
    # в лишнем резе» не видно, всё помечено «GigaAM + 27b».
    cutlog = build_cutlog(words, drop, silence_bounds, breath_marks, rule=rule)

    # --- сайдкары пишем ДО чернового рендера, а не после (задание BC, 2026-08-13) ---
    # Раньше их писал omni_cut.py уже ПОСЛЕ возврата пайплайна — то есть после
    # рендера черновика. «Стоп»/крах на рендере (он минутный) оставлял XML и
    # breaths.json на месте, а project.json/cuts.json — нет: 11 роликов двух
    # спикеров (11-12.08) так и остались без cuts.json, и ручная доводка по ним
    # не превращается в разметку для train_breath. Пишем здесь, сразу после XML:
    # рендер может умереть когда угодно, разметка — уже на диске.
    proj = {"cams": cams, "offsets": offsets, "fps": 60, "cam_return": cam_return,
            "scale": scale, "keep": [[round(s, 3), round(e, 3)] for s, e in keep]}
    if speaker:
        proj["speaker"] = speaker
    atomic_json_dump(os.path.splitext(out)[0] + ".project.json", proj, indent=1)
    cutlog.sort(key=lambda c: c["t0"])
    atomic_json_dump(os.path.splitext(out)[0] + ".cuts.json", cutlog, indent=1)

    # --- финальный черновик по итоговому keep (консистентно с XML) ---
    # draft_path возвращается всегда, а присваивался только при черновике: при
    # --no-draft (или падении render_draft) в конце 5-10-минутного прогона
    # GigaAM+LLM падал UnboundLocalError. Инициализируем заранее.
    draft_path = None
    if stages.get("draft", False):
        try:
            draft_path = draftrender.render_draft(out, emit=emit)
        except Exception as ex:
            emit("  финальный черновик не собрался: {err}", err=str(ex), flush=True)

    # --- чистка временных файлов ---
    try:
        draftrender.clean_tmp(os.path.dirname(out) or ".", emit=lambda *x: None)
    except Exception:
        pass

    emit("\n-> GigaAM-cut: {out}  ({n_keep} интервалов, {n_drop} слов вырезано)",
         out=out, n_keep=len(keep), n_drop=len(drop), flush=True)
    return keep, cutlog, draft_path, last_info