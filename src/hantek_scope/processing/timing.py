"""Medições de tempo de pulsos: largura, ciclo de trabalho, subida, descida e atraso."""

from dataclasses import dataclass

import numpy as np

from hantek_scope.processing.trigger import rising_edges

HISTOGRAM_BINS = 50
EDGE_HYSTERESIS = 0.1  # Fração da amplitude.
TRANSITION_LOW, TRANSITION_HIGH = 0.1, 0.9  # Subida e descida de 10% a 90%.


@dataclass(frozen=True)
class Timing:
    base: float  # Nível baixo mais frequente.
    top: float  # Nível alto mais frequente.
    width_positive: float | None
    width_negative: float | None
    rise_time: float | None
    fall_time: float | None

    @property
    def duty_cycle(self) -> float | None:
        """Fração do período em nível alto."""
        if self.width_positive is None or self.width_negative is None:
            return None
        return self.width_positive / (self.width_positive + self.width_negative)


def levels(samples: np.ndarray) -> tuple[float, float]:
    """Base e topo: o valor mais comum abaixo e acima do meio.

    Diferente do mínimo e do máximo, ignora o sobressinal e os picos de ruído
    de uma onda quadrada. Em um seno, os mais comuns são os próprios picos.
    """
    low, high = float(samples.min()), float(samples.max())
    middle = (low + high) / 2

    def mode(values: np.ndarray, start: float, stop: float) -> float:
        counts, edges = np.histogram(values, bins=HISTOGRAM_BINS, range=(start, stop))
        k = int(np.argmax(counts))
        return float((edges[k] + edges[k + 1]) / 2)

    if high == low:
        return low, high
    return mode(samples[samples < middle], low, middle), mode(samples[samples >= middle], middle, high)


def measure_timing(samples: np.ndarray, sample_rate: float, min_amplitude: float = 0.0) -> Timing:
    """Mede os pulsos de um trecho; cada valor é a média das bordas encontradas."""
    base, top = levels(samples)
    amplitude = top - base
    if amplitude <= 0 or amplitude < min_amplitude:
        return Timing(base, top, None, None, None, None)
    middle = (base + top) / 2
    hysteresis = EDGE_HYSTERESIS * amplitude
    rising = rising_edges(samples, middle, hysteresis)
    falling = rising_edges(-samples, -middle, hysteresis)

    def mean_seconds(values: np.ndarray) -> float | None:
        return float(np.mean(values)) / sample_rate if values.size else None

    return Timing(
        base=base,
        top=top,
        width_positive=mean_seconds(_spans(rising, falling)),
        width_negative=mean_seconds(_spans(falling, rising)),
        rise_time=mean_seconds(_transitions(samples, rising, base, amplitude)),
        fall_time=mean_seconds(_transitions(-samples, falling, -top, amplitude)),
    )


def measure_delay(
    reference: np.ndarray,
    other: np.ndarray,
    sample_rate: float,
    period: float | None,
    min_amplitude: float = 0.0,
) -> tuple[float, float | None] | None:
    """Atraso das bordas de subida de other em relação às de reference.

    Devolve (atraso em segundos, fase em graus de -180 a 180). Cada borda da
    referência é ligada à borda de other mais próxima, e o resultado é a
    mediana, que resiste a uma borda falsa.
    """
    edges = []
    for samples in (reference, other):
        base, top = levels(samples)
        amplitude = top - base
        if amplitude <= 0 or amplitude < min_amplitude:
            return None
        edges.append(rising_edges(samples, (base + top) / 2, EDGE_HYSTERESIS * amplitude))
    ref_edges, other_edges = edges
    if not ref_edges.size or not other_edges.size:
        return None
    nearest = np.clip(np.searchsorted(other_edges, ref_edges), 1, other_edges.size - 1)
    candidates = np.stack([other_edges[nearest - 1], other_edges[np.minimum(nearest, other_edges.size - 1)]])
    differences = candidates - ref_edges
    closest = differences[np.argmin(np.abs(differences), axis=0), np.arange(ref_edges.size)]
    delay = float(np.median(closest)) / sample_rate
    phase = None
    if period:
        phase = (delay / period * 360 + 180) % 360 - 180
    return delay, phase


def _spans(starts: np.ndarray, stops: np.ndarray) -> np.ndarray:
    """Distância de cada início até o fim seguinte."""
    k = np.searchsorted(stops, starts, side="right")
    valid = k < stops.size
    return stops[k[valid]] - starts[valid]


def _transitions(samples: np.ndarray, edges: np.ndarray, base: float, amplitude: float) -> np.ndarray:
    """Tempo, em amostras, de 10% a 90% da amplitude em cada borda de subida.

    Parte da última amostra abaixo de 10% antes da borda e vai até a primeira
    acima de 90% depois dela, interpolando os dois cruzamentos.
    """
    low = base + TRANSITION_LOW * amplitude
    high = base + TRANSITION_HIGH * amplitude
    below = np.flatnonzero(samples < low)
    above = np.flatnonzero(samples >= high)
    if not below.size or not above.size:
        return np.array([])
    k = np.searchsorted(below, edges) - 1
    m = np.searchsorted(above, edges)
    valid = (k >= 0) & (m < above.size)
    i = below[k[valid]]
    j = above[m[valid]]
    valid = (i + 1 < samples.size) & (j >= 1)
    i, j = i[valid], j[valid]
    start = i + (low - samples[i]) / (samples[i + 1] - samples[i])
    stop = j - 1 + (high - samples[j - 1]) / (samples[j] - samples[j - 1])
    return stop - start
