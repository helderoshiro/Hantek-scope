import numpy as np


def find_trigger(
    samples: np.ndarray,
    level: float,
    rising: bool,
    hysteresis: float,
    start: int,
    stop: int,
) -> float | None:
    """Posição, em amostras e com fração, da primeira borda em [start, stop).

    A histerese exige que o sinal passe por level - hysteresis antes de cruzar
    o nível (ou level + hysteresis, na descida), para o ruído não disparar.
    """
    v = samples if rising else -samples
    lvl = level if rising else -level
    state = np.where(v < lvl - hysteresis, -1, np.where(v >= lvl, 1, 0))

    # Propaga o último estado definido pelas amostras dentro da faixa de histerese.
    last_known = np.where(state != 0, np.arange(state.size), 0)
    np.maximum.accumulate(last_known, out=last_known)
    filled = state[last_known]

    edges = np.flatnonzero((filled[:-1] == -1) & (filled[1:] == 1)) + 1
    edges = edges[(edges >= max(start, 1)) & (edges < stop)]
    if edges.size == 0:
        return None

    i = int(edges[0])
    before, after = v[i - 1], v[i]
    fraction = (lvl - before) / (after - before) if after != before else 0.0
    return i - 1 + float(np.clip(fraction, 0.0, 1.0))
