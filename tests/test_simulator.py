import numpy as np
import pytest

from hantek_scope.acquisition.base import CaptureConfig
from hantek_scope.acquisition.simulator import FULL_SCALE_V, SimulatedScope, SimulatedSignal


def make_scope(*signals, rate=1e6, n=10_000):
    scope = SimulatedScope(signals, realtime=False, seed=1)
    scope.configure(CaptureConfig(sample_rate=rate, n_samples=n))
    return scope


def test_frame_has_one_row_per_channel():
    frame = make_scope(SimulatedSignal("sine", 1e3, 1.0), SimulatedSignal("square", 1e3, 1.0)).read_frame()
    assert frame.channels.shape == (2, 10_000)
    assert frame.sample_rate == 1e6


def test_read_before_configure_fails():
    with pytest.raises(RuntimeError):
        SimulatedScope(realtime=False).read_frame()


def test_sine_has_the_requested_frequency():
    frame = make_scope(SimulatedSignal("sine", 5e3, 2.0)).read_frame()
    spectrum = np.abs(np.fft.rfft(frame.channels[0]))
    freqs = np.fft.rfftfreq(frame.channels.shape[1], 1 / frame.sample_rate)
    assert freqs[spectrum.argmax()] == pytest.approx(5e3)


def test_signal_is_continuous_between_frames():
    scope = make_scope(SimulatedSignal("sine", 1e3, 2.0, noise_rms=0.0))
    joined = np.concatenate([scope.read_frame().channels[0], scope.read_frame().channels[0]])
    # Passo máximo de um seno de 2 V a 1 kHz amostrado a 1 MS/s, mais 1 LSB de folga.
    assert np.abs(np.diff(joined)).max() < 2 * 2 * np.pi * 1e3 / 1e6 + 10 / 256


def test_signal_saturates_at_full_scale():
    frame = make_scope(SimulatedSignal("sine", 1e3, 100.0, noise_rms=0.0)).read_frame()
    assert frame.channels.max() == pytest.approx(FULL_SCALE_V)
    assert frame.channels.min() == pytest.approx(-FULL_SCALE_V)
