import numpy as np
import pytest

from hantek_scope.processing.measurements import measure

RATE = 1e6


def wave(kind, frequency=1e3, n=10_000, phase=0.3):
    t = np.arange(n) / RATE
    sine = np.sin(2 * np.pi * frequency * t + phase)
    return sine if kind == "sine" else np.where(sine >= 0, 1.0, 0.0)


def test_sine():
    m = measure(2 * wave("sine"), RATE)
    assert m.frequency == pytest.approx(1e3, rel=1e-3)
    assert m.period == pytest.approx(1e-3, rel=1e-3)
    assert m.vpp == pytest.approx(4, abs=1e-3)
    assert m.vrms == pytest.approx(2 / np.sqrt(2), rel=1e-3)
    assert m.vmean == pytest.approx(0, abs=1e-3)


def test_square_from_zero_to_two_volts():
    m = measure(2 * wave("square", frequency=2.5e3), RATE)
    assert m.frequency == pytest.approx(2.5e3, rel=1e-3)
    assert m.vmin == 0 and m.vmax == 2
    assert m.vmean == pytest.approx(1, abs=0.01)
    assert m.vrms == pytest.approx(np.sqrt(2), abs=0.01)


def test_mean_uses_whole_periods_only():
    # 2,5 períodos de seno: a média do trecho todo não seria zero.
    m = measure(wave("sine", n=2_500, phase=0.0), RATE)
    assert m.vmean == pytest.approx(0, abs=2e-3)


def test_noise_below_min_vpp_has_no_frequency():
    noise = np.tile([0.0, 0.04], 5_000)
    assert measure(noise, RATE, min_vpp=0.2).frequency is None
    assert measure(noise, RATE).frequency is not None


def test_rising_edges_at_the_borders_still_measure_from_falling_edges():
    # Exatamente 2 períodos começando em zero: só há uma subida completa no meio.
    m = measure(wave("sine", n=2_001, phase=0.0), RATE)
    assert m.frequency == pytest.approx(1e3, rel=1e-3)


def test_less_than_one_period_has_no_frequency():
    m = measure(wave("sine", n=500), RATE)
    assert m.frequency is None
    assert m.period is None


def test_flat_signal():
    m = measure(np.full(100, 1.5), RATE)
    assert m.vpp == 0 and m.frequency is None and m.vmean == 1.5
