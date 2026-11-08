import numpy as np
import pytest

from hantek_scope.processing.spectrum import WINDOWS, find_peak, spectrum, window

RATE = 100e3


def tone(frequency, amplitude, n=10_000, offset=0.0):
    t = np.arange(n) / RATE
    return amplitude * np.sin(2 * np.pi * frequency * t) + offset


def test_flat_top_reads_the_sine_amplitude_between_bins():
    # 1234,5 Hz cai entre bins (resolução de 10 Hz).
    spec = spectrum(tone(1234.5, 2.0), RATE, "flattop")
    assert spec.resolution == pytest.approx(10.0)
    assert spec.vrms.max() == pytest.approx(2.0 / np.sqrt(2), rel=0.002)


def test_dc_level_is_kept_as_is():
    spec = spectrum(tone(1000, 1.0, offset=0.5), RATE, "hann")
    assert spec.vrms[0] == pytest.approx(0.5, rel=1e-3)


@pytest.mark.parametrize("name", list(WINDOWS))
def test_peak_frequency_is_refined_below_the_resolution(name):
    spec = spectrum(tone(1234.5, 1.0, offset=3.0), RATE, name)
    peak = find_peak(spec, name)
    tolerance = 0.5 * spec.resolution if name == "rect" else 0.1 * spec.resolution
    assert peak.frequency == pytest.approx(1234.5, abs=tolerance)


def test_dbv_reference():
    spec = spectrum(tone(1000, np.sqrt(2)), RATE, "flattop")  # 1 V RMS.
    assert spec.dbv().max() == pytest.approx(0.0, abs=0.05)


def test_windows_are_normalized_to_one_at_the_center():
    for name in WINDOWS:
        assert window(name, 100)[50] == pytest.approx(1.0, abs=1e-6)


def test_too_short_signal_has_no_peak():
    assert find_peak(spectrum(np.zeros(6), RATE)) is None
