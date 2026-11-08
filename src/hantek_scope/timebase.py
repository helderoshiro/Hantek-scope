import math
from collections.abc import Iterable
from dataclasses import dataclass

from hantek_scope.acquisition.base import CaptureConfig

HORIZONTAL_DIVS = 10
VERTICAL_DIVS = 8
# Captura o dobro da tela para o trigger achar a borda e ainda encher a tela.
CAPTURE_DIVS = 2 * HORIZONTAL_DIVS
MAX_SAMPLES = 100_000
TIME_PER_DIV = (
    1e-6, 2e-6, 5e-6,
    10e-6, 20e-6, 50e-6,
    100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3,
    10e-3, 20e-3, 50e-3,
    100e-3, 200e-3, 500e-3,
    1.0, 2.0, 5.0,
    10.0,
)  # fmt: skip
# A partir daqui o traço corre pela tela (modo roll), sem trigger.
ROLL_MIN_TIME_PER_DIV = 100e-3
ROLL_POINTS_PER_SCREEN = 20_000
ROLL_CHUNK_S = 0.05  # Duração de cada leitura no modo roll.


@dataclass(frozen=True)
class RollPlan:
    """Leitura contínua em trechos curtos, reduzidos por média de blocos."""

    config: CaptureConfig
    decimation: int  # Amostras do aparelho por ponto mostrado.

    @property
    def output_rate(self) -> float:
        return self.config.sample_rate / self.decimation


def is_roll(time_per_div: float) -> bool:
    return time_per_div >= ROLL_MIN_TIME_PER_DIV * (1 - 1e-9)


def roll_plan(
    time_per_div: float,
    sample_rates: Iterable[float],
    volts_per_div: tuple[float, ...] = (),
    channels: int = 2,
) -> RollPlan:
    """Escolhe taxa e redução para a tela ter cerca de ROLL_POINTS_PER_SCREEN pontos.

    Amostrar acima do necessário e tirar a média de blocos filtra o ruído e
    evita que frequências altas apareçam como falsas ondas lentas.
    """
    target = ROLL_POINTS_PER_SCREEN / (HORIZONTAL_DIVS * time_per_div)
    rates = sorted(sample_rates)
    above = [r for r in rates if r >= target]
    rate = above[0] if above else rates[-1]
    decimation = max(1, int(rate // target))
    n_samples = max(1, round(rate * ROLL_CHUNK_S / decimation)) * decimation
    config = CaptureConfig(rate, n_samples, volts_per_div, channels, stream=True)
    return RollPlan(config, decimation)


def max_offscreen_divs(time_per_div: float, sample_rates: Iterable[float]) -> float:
    """Quantas divisões o disparo pode ficar fora da tela.

    A captura precisa cobrir também o trecho entre o disparo e a tela, e o
    limite é o que cabe em MAX_SAMPLES na menor taxa.
    """
    longest_s = MAX_SAMPLES / min(sample_rates)
    return max(0.0, longest_s / time_per_div - CAPTURE_DIVS)


def capture_for(
    time_per_div: float,
    sample_rates: Iterable[float],
    volts_per_div: tuple[float, ...] = (),
    offscreen_divs: float = 0.0,
    channels: int = 2,
) -> CaptureConfig:
    """Usa a maior taxa cuja captura completa cabe em MAX_SAMPLES.

    offscreen_divs é a distância do disparo até a tela, quando ele está fora
    dela; a captura cresce nessa medida.
    """
    window_s = (CAPTURE_DIVS + offscreen_divs) * time_per_div
    rates = sorted(sample_rates)
    fitting = [r for r in rates if round(r * window_s, 6) <= MAX_SAMPLES]
    rate = fitting[-1] if fitting else rates[0]
    # A amostra extra faz a captura cobrir a janela inteira.
    n_samples = math.ceil(round(rate * window_s, 6)) + 1
    return CaptureConfig(rate, n_samples, volts_per_div, channels)
