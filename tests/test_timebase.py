import pytest

from hantek_scope.acquisition.hantek6022 import Hantek6022Backend
from hantek_scope.acquisition.simulator import SAMPLE_RATES
from hantek_scope.timebase import (
    CAPTURE_DIVS,
    MAX_SAMPLES,
    TIME_PER_DIV,
    ROLL_CHUNK_S,
    capture_for,
    is_roll,
    max_offscreen_divs,
    roll_plan,
)

TRIGGERED_TIMEBASES = [t for t in TIME_PER_DIV if not is_roll(t)]
HANTEK_RATES = Hantek6022Backend.sample_rates


def test_fast_timebase_uses_the_highest_rate():
    config = capture_for(1e-6, SAMPLE_RATES)
    assert config.sample_rate == 48e6
    assert config.n_samples == 961


def test_slowest_timebase_lowers_the_rate():
    config = capture_for(10e-3, SAMPLE_RATES)
    assert config.sample_rate == 500e3
    assert config.n_samples == 100_001


@pytest.mark.parametrize("time_per_div", TRIGGERED_TIMEBASES)
def test_every_timebase_captures_twice_the_screen_within_the_limit(time_per_div):
    config = capture_for(time_per_div, SAMPLE_RATES)
    assert config.n_samples <= MAX_SAMPLES + 1
    assert (config.n_samples - 1) / config.sample_rate == pytest.approx(
        CAPTURE_DIVS * time_per_div, rel=1e-2
    )


def test_off_screen_trigger_lengthens_the_capture():
    config = capture_for(1e-6, SAMPLE_RATES, offscreen_divs=3000)
    # 3,02 ms de captura: a 48 MS/s não cabe em MAX_SAMPLES, e o simulador cai para 24 MS/s.
    assert config.sample_rate == 24e6
    assert (config.n_samples - 1) / config.sample_rate == pytest.approx(3020e-6, rel=1e-3)


def test_max_offscreen_divs_fits_the_lowest_rate():
    limit = max_offscreen_divs(1e-3, SAMPLE_RATES)
    config = capture_for(1e-3, SAMPLE_RATES, offscreen_divs=limit)
    assert config.n_samples <= MAX_SAMPLES + 1


def test_roll_starts_at_100_ms_per_div():
    assert not is_roll(50e-3)
    assert is_roll(100e-3)
    assert TIME_PER_DIV[-1] == 10.0


@pytest.mark.parametrize("time_per_div", [t for t in TIME_PER_DIV if is_roll(t)])
def test_roll_plan_reads_short_chunks_of_whole_blocks(time_per_div):
    plan = roll_plan(time_per_div, HANTEK_RATES)
    config = plan.config
    assert config.n_samples % plan.decimation == 0
    assert config.n_samples / config.sample_rate == pytest.approx(ROLL_CHUNK_S, rel=0.5)
    # Cerca de 20 mil pontos na tela, sem passar muito disso.
    points = plan.output_rate * 10 * time_per_div
    assert 10_000 <= points <= 40_000


def test_slowest_roll_averages_blocks():
    plan = roll_plan(10.0, HANTEK_RATES)
    assert plan.config.sample_rate == 20e3
    assert plan.decimation == 100
    assert plan.output_rate == 200
