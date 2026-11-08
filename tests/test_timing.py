import numpy as np
import pytest

from hantek_scope.processing.timing import levels, measure_delay, measure_timing

RATE = 10e6


def pulses(frequency=10e3, duty=0.25, rise=2e-6, n=50_000, delay=0.0, low=0.0, high=5.0):
    """Trem de pulsos trapezoidais, com subida e descida lineares."""
    t = np.arange(n) / RATE - delay
    phase = (t * frequency) % 1.0 / frequency  # Tempo dentro do período.
    width = duty / frequency
    up = np.clip(phase / rise, 0, 1)
    down = np.clip((phase - width) / rise, 0, 1)
    return low + (high - low) * (up - down)


def test_levels_ignore_overshoot():
    signal = pulses()
    signal[np.argmax(signal)] = 7.0  # Um pico de sobressinal.
    base, top = levels(signal)
    assert base == pytest.approx(0, abs=0.06)
    assert top == pytest.approx(5, abs=0.06)


def test_pulse_widths_and_duty_cycle():
    timing = measure_timing(pulses(duty=0.25), RATE)
    assert timing.width_positive == pytest.approx(25e-6, rel=0.01)
    assert timing.width_negative == pytest.approx(75e-6, rel=0.01)
    assert timing.duty_cycle == pytest.approx(0.25, abs=0.005)


def test_rise_and_fall_times_from_10_to_90_percent():
    timing = measure_timing(pulses(rise=2e-6), RATE)
    # Rampa linear de 2 µs: de 10% a 90% leva 80% dela.
    assert timing.rise_time == pytest.approx(1.6e-6, rel=0.02)
    assert timing.fall_time == pytest.approx(1.6e-6, rel=0.02)


def test_flat_signal_has_no_timing():
    timing = measure_timing(np.full(1000, 1.0), RATE)
    assert timing.duty_cycle is None and timing.rise_time is None


def test_small_signal_below_the_minimum_is_ignored():
    timing = measure_timing(pulses(high=0.01), RATE, min_amplitude=0.1)
    assert timing.width_positive is None


def test_delay_and_phase_between_channels():
    period = 1 / 10e3
    reference = pulses()
    delayed = pulses(delay=12.5e-6)  # Um oitavo do período: 45°.
    delay, phase = measure_delay(reference, delayed, RATE, period)
    assert delay == pytest.approx(12.5e-6, rel=0.01)
    assert phase == pytest.approx(45, abs=0.5)
    # Ao contrário, o atraso fica negativo.
    delay, phase = measure_delay(delayed, reference, RATE, period)
    assert phase == pytest.approx(-45, abs=0.5)


def test_delay_of_sines_in_quadrature():
    t = np.arange(50_000) / RATE
    sine = np.sin(2 * np.pi * 1e3 * t)
    cosine = np.cos(2 * np.pi * 1e3 * t)
    _, phase = measure_delay(sine, cosine, RATE, 1e-3)
    # O cosseno se adianta 90° em relação ao seno.
    assert phase == pytest.approx(-90, abs=0.5)


def test_delay_needs_signal_on_both_channels():
    assert measure_delay(pulses(), np.zeros(50_000), RATE, 1e-4) is None
