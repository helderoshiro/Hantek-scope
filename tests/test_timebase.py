import pytest

from hantek_scope.acquisition.simulator import SAMPLE_RATES
from hantek_scope.timebase import (
    CAPTURE_DIVS,
    MAX_SAMPLES,
    TIME_PER_DIV,
    capture_for,
    max_offscreen_divs,
)


def test_fast_timebase_uses_the_highest_rate():
    config = capture_for(1e-6, SAMPLE_RATES)
    assert config.sample_rate == 48e6
    assert config.n_samples == 961


def test_slowest_timebase_lowers_the_rate():
    config = capture_for(10e-3, SAMPLE_RATES)
    assert config.sample_rate == 500e3
    assert config.n_samples == 100_001


@pytest.mark.parametrize("time_per_div", TIME_PER_DIV)
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
