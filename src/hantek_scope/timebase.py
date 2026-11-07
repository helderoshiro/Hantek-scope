import math
from collections.abc import Iterable

from hantek_scope.acquisition.base import CaptureConfig

HORIZONTAL_DIVS = 10
MAX_SAMPLES = 100_000
TIME_PER_DIV = (
    1e-6, 2e-6, 5e-6,
    10e-6, 20e-6, 50e-6,
    100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3,
    10e-3,
)  # fmt: skip


def capture_for(time_per_div: float, sample_rates: Iterable[float]) -> CaptureConfig:
    """Usa a maior taxa cuja janela completa cabe em MAX_SAMPLES."""
    window_s = HORIZONTAL_DIVS * time_per_div
    rates = sorted(sample_rates)
    fitting = [r for r in rates if round(r * window_s, 6) <= MAX_SAMPLES]
    rate = fitting[-1] if fitting else rates[0]
    # A amostra extra faz o traço alcançar a borda direita da tela.
    n_samples = math.ceil(round(rate * window_s, 6)) + 1
    return CaptureConfig(sample_rate=rate, n_samples=n_samples)
