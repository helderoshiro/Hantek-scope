import numpy as np
import pytest

from hantek_scope.processing.channel import (
    ChannelSettings,
    ac_input_scale,
    can_remove_mains,
    condition,
    remove_mains,
)

RATE = 100e3


def tone(frequency, amplitude, duration=0.1, phase=0.0):
    t = np.arange(int(RATE * duration)) / RATE
    return amplitude * np.sin(2 * np.pi * frequency * t + phase)


def test_probe_and_inversion_scale_the_signal():
    samples = np.array([0.1, -0.2])
    assert condition(samples, RATE, ChannelSettings(probe=10)) == pytest.approx([1.0, -2.0])
    assert condition(samples, RATE, ChannelSettings(probe=10, inverted=True)) == pytest.approx([-1.0, 2.0])


def test_ac_removes_the_dc_level():
    samples = tone(1e3, 1.0) + 12.0
    result = condition(samples, RATE, ChannelSettings(ac=True))
    assert result.mean() == pytest.approx(0, abs=1e-9)
    assert result.max() == pytest.approx(1.0, abs=1e-3)


def test_mains_and_harmonics_are_removed_and_the_signal_kept():
    signal = tone(1e3, 1.0) + 0.5
    hum = tone(60, 0.4, phase=0.7) + tone(120, 0.1, phase=2.0) + tone(180, 0.05)
    result = remove_mains(signal + hum, RATE)
    assert np.abs(result - signal).max() < 1e-3


def test_mains_filter_keeps_the_dc_level():
    result = remove_mains(tone(60, 0.3) + 2.0, RATE)
    assert result == pytest.approx(np.full(result.size, 2.0), abs=1e-6)


def test_mains_filter_needs_two_mains_cycles():
    assert can_remove_mains(int(2 * RATE / 60) + 1, RATE)
    assert not can_remove_mains(int(2 * RATE / 60) - 1, RATE)
    short = tone(60, 0.3, duration=0.03)
    with pytest.raises(ValueError):
        remove_mains(short, RATE)
    # Em condition() a captura curta passa sem filtro, em vez de falhar.
    assert condition(short, RATE, ChannelSettings(mains_filter=True)) == pytest.approx(short)


@pytest.mark.parametrize("frequency", [333.3, 1234.5, 4321.0])
def test_mains_filter_does_not_distort_signals_without_whole_cycles(frequency):
    # 40 ms: dois ciclos e meio da rede e um número quebrado de ciclos do sinal.
    signal = tone(frequency, 1.0, duration=0.04, phase=0.5)
    hum = tone(60, 0.5, duration=0.04, phase=1.0) + tone(120, 0.1, duration=0.04)
    assert np.abs(remove_mains(signal + hum, RATE) - signal).max() < 1e-3


def test_mains_filter_tolerates_a_slightly_off_mains_frequency():
    signal = tone(1e3, 1.0)
    result = remove_mains(signal + tone(60.05, 0.5), RATE)
    assert np.abs(result - signal).max() < 0.01


SCALES = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)


def test_ac_input_scale_steps_up_when_the_signal_saturates():
    assert ac_input_scale(0.8, current=0.2, scales=SCALES, half_divs=4) == 0.5
    assert ac_input_scale(20.0, current=5.0, scales=SCALES, half_divs=4) == 5.0


def test_ac_input_scale_steps_down_straight_to_the_smallest_fit():
    assert ac_input_scale(0.1, current=5.0, scales=SCALES, half_divs=4) == 0.05
    assert ac_input_scale(0.5, current=5.0, scales=SCALES, half_divs=4) == 0.2


def test_ac_input_scale_holds_inside_the_hysteresis_band():
    # 0,7 V cabe em 0,2 V/div (0,8 V), mas sem a folga de 25%: fica em 0,5.
    assert ac_input_scale(0.7, current=0.5, scales=SCALES, half_divs=4) == 0.5
