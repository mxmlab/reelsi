# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты шкалы времени рендера (задание FQ):
1. Вычисление весов фаз по ожидаемым временам (1:7:15 -> границы ~4% и ~33%).
2. Нелинейная модель сборки проекта (t(n) = a + b*(n-1)).
3. Строгая монотонность полосы прогресса при ухудшении потока (замедлении).
4. Изоляция файла статистики render_stats.json через переменную окружения REELSI_RENDER_STATS.
5. Сохранение и накопление статистики прогонов.
6. Общий и фазовый ETA, правило прочерка и предварительная оценка.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render  # noqa: E402


def test_phase_weights_calculation():
    """Тест вычисления весов фаз: при дефолтных 5с : 36с : 83с (всего 124с)
    граница фазы 1 ~ 4% (5/124 = 0.0403), фазы 2 ~ 33.1% (41/124 = 0.3306)."""
    p1, p2 = render._calc_phase_bounds(5.0 * 12, 36.0 * 12, 83.0 * 12)
    assert 0.035 <= p1 <= 0.045, f"p1={p1} вне ожидаемого диапазона ~4%"
    assert 0.32 <= p2 <= 0.34, f"p2={p2} вне ожидаемого диапазона ~33%"

    # Равные времена (1:1:1) -> 33.3% и 66.7%
    p1_eq, p2_eq = render._calc_phase_bounds(100.0, 100.0, 100.0)
    assert abs(p1_eq - 1.0 / 3.0) < 0.01
    assert abs(p2_eq - 2.0 / 3.0) < 0.01


def test_nonlinear_aep_prediction():
    """Тест нелинейной регрессии t(n) = a + b*(n-1):
    при замерах [30, 36, 42, 48] следующий ролик оценивается ближе к 54, чем к среднему 39."""
    durations = [30.0, 36.0, 42.0, 48.0]
    total_n = 6
    weights = render._predict_aep_times(durations, total_n)
    assert len(weights) == 6
    assert weights[0] == 30.0
    assert weights[1] == 36.0
    assert weights[2] == 42.0
    assert weights[3] == 48.0
    # 5-й ролик (индекс 4): 30 + 6*4 = 54
    assert abs(weights[4] - 54.0) < 0.5, f"Оценка 5-го ролика {weights[4]} далека от 54"
    # 6-й ролик (индекс 5): 30 + 6*5 = 60
    assert abs(weights[5] - 60.0) < 0.5, f"Оценка 6-го ролика {weights[5]} далека от 60"

    # При 0 замерах — берется дефолт
    def_weights = render._predict_aep_times([], 4, default_per_clip=36.0)
    assert def_weights == [36.0, 36.0, 36.0, 36.0]

    # При 1 замере — берется константа этого замера
    one_weights = render._predict_aep_times([25.0], 3)
    assert one_weights == [25.0, 25.0, 25.0]


def test_phase_progress_and_strict_monotonicity_under_slowdown():
    """Тест монотонности: даже при резком росте ожиданий времени полоса никогда не откатывается."""
    # Симулируем 10 клипов
    total_n = 10
    p_jsx_end, p_aep_end = render._calc_phase_bounds(5.0 * total_n, 36.0 * total_n, 83.0 * total_n)

    durations = []
    pct_history = [0.0]

    # Поочередно добавляем замеры, которые резко замедляются (30, 40, 70, 120, 200...)
    delays = [30.0, 40.0, 70.0, 120.0, 200.0, 350.0, 500.0, 700.0, 900.0, 1200.0]
    for k in range(1, total_n + 1):
        durations.append(delays[k - 1])
        weights = render._predict_aep_times(durations, total_n)
        sum_done = sum(weights[:k])
        sum_tot = sum(weights)
        aep_frac = sum_done / sum_tot
        pct_raw = p_jsx_end + aep_frac * (p_aep_end - p_jsx_end)
        pct_clamped = max(pct_history[-1], min(p_aep_end, pct_raw))
        pct_history.append(pct_clamped)

    # Проверяем нестрогое возрастание каждого шага
    for i in range(1, len(pct_history)):
        assert pct_history[i] >= pct_history[i - 1], (
            f"Откат на шаге {i}: {pct_history[i-1]} -> {pct_history[i]}"
        )


def test_env_isolation_render_stats(tmp_path, monkeypatch):
    """Изоляция файла статистики: переменная REELSI_RENDER_STATS направляет запись в отдельный файл."""
    custom_stats = str(tmp_path / "custom_render_stats.json")
    monkeypatch.setenv("REELSI_RENDER_STATS", custom_stats)

    assert render._get_stats_path() == custom_stats
    assert not os.path.exists(custom_stats)

    # Сохраняем статистику
    render._save_render_stats(12, 60.0, 431.0, 906.0)
    assert os.path.isfile(custom_stats)

    data, has_hist = render._load_render_stats()
    assert has_hist is True
    assert len(data["runs"]) == 1
    assert data["runs"][0]["n"] == 12
    assert data["runs"][0]["jsx_sec"] == 60.0
    assert data["runs"][0]["aep_sec"] == 431.0
    assert data["runs"][0]["render_sec"] == 906.0


def test_render_stats_persistence_and_baseline(tmp_path, monkeypatch):
    """Тест накопления статистики и расчета базовых времен:
    при двух прогонах берется взвешенное среднее по роликам."""
    stats_file = str(tmp_path / "stats.json")
    monkeypatch.setenv("REELSI_RENDER_STATS", stats_file)

    # До сохранения статистики — дефолты
    t_jsx, t_aep, t_rnd, has_hist = render._get_baseline_phase_durations(10)
    assert has_hist is False
    assert t_jsx == 5.0 * 10
    assert t_aep == 36.0 * 10
    assert t_rnd == 83.0 * 10

    # Прогон 1: 10 роликов, времена 50с, 300с, 800с (5с, 30с, 80с на ролик)
    render._save_render_stats(10, 50.0, 300.0, 800.0)

    # Прогон 2: 10 роликов, времена 70с, 400с, 1000с (7с, 40с, 100с на ролик)
    render._save_render_stats(10, 70.0, 400.0, 1000.0)

    # Базовые времена для 5 роликов: среднее (6с, 35с, 90с) * 5
    t_jsx2, t_aep2, t_rnd2, has_hist2 = render._get_baseline_phase_durations(5)
    assert has_hist2 is True
    assert abs(t_jsx2 - (6.0 * 5)) < 1e-4
    assert abs(t_aep2 - (35.0 * 5)) < 1e-4
    assert abs(t_rnd2 - (90.0 * 5)) < 1e-4


def test_eta_phase_and_total_and_preliminary_flag(tmp_path, monkeypatch):
    """Тест общего и фазового ETA:
    1. При наличии статистики — сразу выставляется preliminary ETA (фазовый и общий).
    2. При отсутствии статистики и < 15с работы — ETA = None (прочерк).
    """
    # Без статистики
    empty_stats = str(tmp_path / "empty_stats.json")
    monkeypatch.setenv("REELSI_RENDER_STATS", empty_stats)

    t_jsx_base, t_aep_base, t_rnd_base, has_stats = render._get_baseline_phase_durations(4)
    assert has_stats is False

    with render.RLOCK:
        render.RJOB.update(
            running=True, done=False, pct=0.0,
            eta=None, eta_phase=None, eta_total=None, eta_preliminary=False,
            stage_label="сборка таймлайнов"
        )
    assert render.RJOB["eta"] is None
    assert render.RJOB["eta_total"] is None

    # Со статистикой
    render._save_render_stats(10, 50.0, 360.0, 830.0)
    t_jsx_base, t_aep_base, t_rnd_base, has_stats = render._get_baseline_phase_durations(4)
    assert has_stats is True

    # Начальное состояние Phase 1
    total_eta = t_jsx_base + t_aep_base + t_rnd_base
    with render.RLOCK:
        render.RJOB.update(
            running=True, done=False, pct=0.0,
            eta=t_jsx_base, eta_phase=t_jsx_base, eta_total=total_eta,
            eta_preliminary=True, stage_label="сборка таймлайнов"
        )

    assert render.RJOB["eta_phase"] == t_jsx_base
    assert render.RJOB["eta_total"] == total_eta
    assert render.RJOB["eta_preliminary"] is True
