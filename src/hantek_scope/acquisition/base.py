from dataclasses import dataclass
from typing import Protocol

import numpy as np


class ScopeError(Exception):
    """Falha de aquisição, com mensagem própria para mostrar ao usuário."""


@dataclass(frozen=True)
class CaptureConfig:
    sample_rate: float
    n_samples: int
    volts_per_div: tuple[float, ...] = ()
    # Com 1, só o CH1 é amostrado, o que libera taxas maiores; o CH2 vem zerado.
    channels: int = 2
    # Leitura contínua: cada quadro continua o anterior, sem lacuna entre eles.
    stream: bool = False


@dataclass(frozen=True)
class Frame:
    sample_rate: float
    channels: np.ndarray  # Em volts, formato (n_canais, n_amostras).
    # Instante (time.monotonic) do fim da leitura, preenchido pela thread de aquisição.
    end_time: float | None = None


class ScopeBackend(Protocol):
    """Fonte de dados do osciloscópio, real ou simulada.

    Depois de criado, o backend só é usado pela thread de aquisição.
    """

    name: str
    n_channels: int
    sample_rates: tuple[float, ...]  # Com todos os canais.
    single_channel_rates: tuple[float, ...]  # Só com o CH1.

    def full_scale(self, volts_per_div: float) -> float:
        """Maior tensão de entrada, em módulo, que não satura nesta escala."""
        ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def configure(self, config: CaptureConfig) -> None: ...

    def read_frame(self) -> Frame:
        """Bloqueia até ter uma captura completa."""
        ...
