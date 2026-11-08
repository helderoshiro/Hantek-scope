import numpy as np
import pytest

from hantek_scope.processing.trigger import find_trigger


def sine(n=1000, period=100.0, phase=0.0):
    return np.sin(2 * np.pi * (np.arange(n) / period) + phase)


def test_rising_edge_is_found_with_subsample_precision():
    # Seno com período de 100 amostras, deslocado para cruzar o zero em 12,3.
    samples = sine(phase=-2 * np.pi * 12.3 / 100)
    assert find_trigger(samples, 0.0, True, 0.1, 0, 1000) == pytest.approx(12.3, abs=0.05)


def test_falling_edge():
    samples = sine(phase=-2 * np.pi * 12.3 / 100)
    assert find_trigger(samples, 0.0, False, 0.1, 0, 1000) == pytest.approx(62.3, abs=0.05)


def test_level_away_from_zero():
    samples = sine()
    # sin(2*pi*t/100) = 0,5 em t = 100/12.
    assert find_trigger(samples, 0.5, True, 0.1, 0, 1000) == pytest.approx(100 / 12, abs=0.05)


def test_search_starts_at_start():
    samples = sine()
    assert find_trigger(samples, 0.0, True, 0.1, 150, 1000) == pytest.approx(200, abs=0.05)


def test_edge_at_or_after_stop_is_ignored():
    samples = sine()
    assert find_trigger(samples, 0.0, True, 0.1, 150, 200) is None


def test_level_outside_the_signal_does_not_trigger():
    assert find_trigger(sine(), 2.0, True, 0.1, 0, 1000) is None


def test_hysteresis_ignores_noise_around_the_level():
    # Ruído de ±0,05 em torno de zero nunca passa por -0,1, então não arma o disparo.
    noise = np.tile([0.05, -0.05], 500)
    assert find_trigger(noise, 0.0, True, 0.1, 0, 1000) is None
    assert find_trigger(noise, 0.0, True, 0.01, 0, 1000) is not None
