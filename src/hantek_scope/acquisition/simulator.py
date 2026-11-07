import time
from dataclasses import dataclass
from typing import Literal

import numpy as np

from hantek_scope.acquisition.base import CaptureConfig, Frame

SAMPLE_RATES = (1e6, 4e6, 8e6, 16e6, 24e6, 48e6)
FULL_SCALE_V = 5.0
ADC_LEVELS = 256
MIN_FRAME_INTERVAL_S = 1 / 30


@dataclass(frozen=True)
class SimulatedSignal:
    waveform: Literal["sine", "square"]
    frequency: float
    amplitude: float
    offset: float = 0.0
    noise_rms: float = 0.01


DEFAULT_SIGNALS = (
    SimulatedSignal("sine", frequency=1_000, amplitude=2.0),
    SimulatedSignal("square", frequency=2_500, amplitude=1.0),
)


class SimulatedScope:
    """Gera sinais contínuos no tempo, quantizados como em um ADC de 8 bits."""

    name = "Simulador"
    sample_rates = SAMPLE_RATES

    def __init__(
        self,
        signals: tuple[SimulatedSignal, ...] = DEFAULT_SIGNALS,
        realtime: bool = True,
        seed: int | None = None,
    ) -> None:
        self.n_channels = len(signals)
        self._signals = signals
        self._realtime = realtime
        self._rng = np.random.default_rng(seed)
        self._config: CaptureConfig | None = None
        self._time_s = 0.0
        self._last_read = 0.0

    def open(self) -> None:
        self._last_read = time.monotonic()

    def close(self) -> None:
        pass

    def configure(self, config: CaptureConfig) -> None:
        self._config = config

    def read_frame(self) -> Frame:
        if self._config is None:
            raise RuntimeError("configure() deve ser chamado antes de read_frame()")
        rate = self._config.sample_rate
        n = self._config.n_samples

        if self._realtime:
            self._wait_for_capture(n / rate)

        t = self._time_s + np.arange(n) / rate
        self._time_s += n / rate
        channels = np.stack([self._generate(s, t) for s in self._signals])
        return Frame(sample_rate=rate, channels=channels)

    def _wait_for_capture(self, capture_s: float) -> None:
        interval = max(capture_s, MIN_FRAME_INTERVAL_S)
        remaining = interval - (time.monotonic() - self._last_read)
        if remaining > 0:
            time.sleep(remaining)
        self._last_read = time.monotonic()

    def _generate(self, signal: SimulatedSignal, t: np.ndarray) -> np.ndarray:
        sine = np.sin(2 * np.pi * signal.frequency * t)
        wave = sine if signal.waveform == "sine" else np.where(sine >= 0, 1.0, -1.0)
        noise = self._rng.normal(0.0, signal.noise_rms, t.size)
        return _quantize(signal.amplitude * wave + signal.offset + noise)


def _quantize(volts: np.ndarray) -> np.ndarray:
    lsb = 2 * FULL_SCALE_V / ADC_LEVELS
    return np.round(np.clip(volts, -FULL_SCALE_V, FULL_SCALE_V) / lsb) * lsb
