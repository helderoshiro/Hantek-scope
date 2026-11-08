import pytest

from hantek_scope.acquisition.simulator import SAMPLE_RATES
from hantek_scope.timebase import CAPTURE_DIVS, MAX_SAMPLES, TIME_PER_DIV, capture_for


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
