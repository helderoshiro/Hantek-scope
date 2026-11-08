"""Espectro de amplitude (FFT) de um trecho de sinal."""

from dataclasses import dataclass

import numpy as np

# Coeficientes das janelas de cossenos somados: w = Σ (-1)^k a_k cos(2πkn/N).
WINDOWS = {
    "hann": (0.5, 0.5),
    "flattop": (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368),
    "blackmanharris": (0.35875, 0.48829, 0.14128, 0.01168),
    "rect": (1.0,),
}
# Bins vizinhos ao DC ignorados na busca do pico: o lóbulo principal da janela.
MAIN_LOBE_BINS = {"hann": 2, "flattop": 5, "blackmanharris": 4, "rect": 1}


@dataclass(frozen=True)
class Spectrum:
    frequencies: np.ndarray  # Hz, de 0 até a metade da taxa.
    vrms: np.ndarray  # Amplitude RMS de uma senoide em cada frequência, em volts.
    resolution: float  # Espaço entre bins, em Hz.

    def dbv(self) -> np.ndarray:
        """Amplitude em dBV (0 dBV = 1 V RMS)."""
        return 20 * np.log10(np.maximum(self.vrms, 1e-12))


@dataclass(frozen=True)
class Peak:
    frequency: float
    vrms: float


def window(name: str, n: int) -> np.ndarray:
    phase = 2 * np.pi * np.arange(n) / n
    return sum((-1) ** k * a * np.cos(k * phase) for k, a in enumerate(WINDOWS[name]))


def spectrum(samples: np.ndarray, sample_rate: float, window_name: str = "hann") -> Spectrum:
    """Espectro de um lado, corrigido pelo ganho da janela.

    Uma senoide de amplitude A aparece no seu bin com A/√2 V RMS (exato com a
    janela flat top; com as outras, até alguns dB abaixo se a frequência cair
    entre dois bins).
    """
    n = samples.size
    w = window(window_name, n)
    magnitude = np.abs(np.fft.rfft(samples * w)) / w.sum()
    vrms = magnitude * np.sqrt(2)  # Dobra pelo lado negativo e divide por √2.
    vrms[0] = magnitude[0]  # O nível contínuo não tem lado negativo nem pico.
    if n % 2 == 0:
        vrms[-1] = magnitude[-1]
    frequencies = np.fft.rfftfreq(n, 1 / sample_rate)
    return Spectrum(frequencies, vrms, sample_rate / n)


def find_peak(spec: Spectrum, window_name: str = "hann") -> Peak | None:
    """Maior componente fora do DC, com a frequência refinada entre bins.

    Ajusta uma parábola ao logaritmo do bin máximo e dos dois vizinhos, o que
    acerta a frequência bem abaixo da resolução.
    """
    skip = MAIN_LOBE_BINS[window_name] + 1
    if spec.vrms.size <= skip + 2:
        return None
    k = skip + int(np.argmax(spec.vrms[skip:]))
    if k >= spec.vrms.size - 1:
        return Peak(float(spec.frequencies[k]), float(spec.vrms[k]))
    left, center, right = np.log(np.maximum(spec.vrms[k - 1 : k + 2], 1e-15))
    denominator = left - 2 * center + right
    offset = 0.5 * (left - right) / denominator if denominator else 0.0
    return Peak(float(spec.frequencies[k] + offset * spec.resolution), float(spec.vrms[k]))
