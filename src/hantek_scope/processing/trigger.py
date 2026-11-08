import numpy as np


def rising_edges(samples: np.ndarray, level: float, hysteresis: float) -> np.ndarray:
    """Posições, em amostras e com fração, de todas as bordas de subida.

    A histerese exige que o sinal passe por level - hysteresis antes de cruzar
    o nível de novo, para o ruído não gerar bordas falsas.
    """
    state = np.where(samples < level - hysteresis, -1, np.where(samples >= level, 1, 0))

    # Propaga o último estado definido pelas amostras dentro da faixa de histerese.
    last_known = np.where(state != 0, np.arange(state.size), 0)
    np.maximum.accumulate(last_known, out=last_known)
    filled = state[last_known]

    edges = np.flatnonzero((filled[:-1] == -1) & (filled[1:] == 1)) + 1
    before, after = samples[edges - 1], samples[edges]
    span = np.where(after != before, after - before, 1.0)
    fraction = np.clip((level - before) / span, 0.0, 1.0)
    return edges - 1 + fraction


def find_trigger(
    samples: np.ndarray,
    level: float,
    rising: bool,
    hysteresis: float,
    start: int,
    stop: int,
) -> float | None:
    """Posição, em amostras e com fração, da primeira borda em [start, stop)."""
    if rising:
        edges = rising_edges(samples, level, hysteresis)
    else:
        edges = rising_edges(-samples, -level, hysteresis)
    index = np.ceil(edges)
    edges = edges[(index >= max(start, 1)) & (index < stop)]
    return float(edges[0]) if edges.size else None
