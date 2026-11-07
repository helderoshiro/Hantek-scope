from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class CaptureConfig:
    sample_rate: float
    n_samples: int


@dataclass(frozen=True)
class Frame:
    sample_rate: float
    channels: np.ndarray  # Em volts, formato (n_canais, n_amostras).


class ScopeBackend(Protocol):
    """Fonte de dados do osciloscópio, real ou simulada.

    Depois de criado, o backend só é usado pela thread de aquisição.
    """

    name: str
    n_channels: int
    sample_rates: tuple[float, ...]

    def open(self) -> None: ...

    def close(self) -> None: ...

    def configure(self, config: CaptureConfig) -> None: ...

    def read_frame(self) -> Frame:
        """Bloqueia até ter uma captura completa."""
        ...
