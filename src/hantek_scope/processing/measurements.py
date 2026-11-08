import math
from dataclasses import dataclass

import numpy as np

from hantek_scope.processing.trigger import rising_edges

EDGE_HYSTERESIS = 0.1  # Fração do Vpp.


@dataclass(frozen=True)
class Measurements:
    vmax: float
    vmin: float
    vpp: float
    vmean: float
    vrms: float
    frequency: float | None

    @property
    def period(self) -> float | None:
        return 1 / self.frequency if self.frequency else None


def measure(samples: np.ndarray, sample_rate: float, min_vpp: float = 0.0) -> Measurements:
    """Mede um trecho de sinal em volts.

    A frequência só é calculada se o Vpp chegar a min_vpp, para o ruído de um
    canal sem sinal não virar uma frequência. Com ao menos um período completo,
    a média e o RMS usam só períodos inteiros, o que evita o erro da sobra.
    """
    vmax = float(samples.max())
    vmin = float(samples.min())
    vpp = vmax - vmin
    frequency = None
    whole_periods = samples

    if vpp > 0 and vpp >= min_vpp:
        middle = (vmax + vmin) / 2
        rising = rising_edges(samples, middle, EDGE_HYSTERESIS * vpp)
        falling = rising_edges(-samples, -middle, EDGE_HYSTERESIS * vpp)
        # Se as bordas de subida caem nas pontas da tela, as de descida ainda
        # podem fechar um período inteiro.
        edges = rising if rising.size >= falling.size else falling
        if edges.size >= 2:
            frequency = (edges.size - 1) * sample_rate / (edges[-1] - edges[0])
            whole_periods = samples[math.ceil(edges[0]) : math.ceil(edges[-1])]

    return Measurements(
        vmax=vmax,
        vmin=vmin,
        vpp=vpp,
        vmean=float(whole_periods.mean()),
        vrms=float(np.sqrt(np.mean(whole_periods**2))),
        frequency=frequency,
    )
