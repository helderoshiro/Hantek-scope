import math
from collections.abc import Iterable

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
    10e-3,
)  # fmt: skip


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
    return CaptureConfig(sample_rate=rate, n_samples=n_samples, volts_per_div=volts_per_div)
