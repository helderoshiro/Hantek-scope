"""Condicionamento de cada canal: ponteira, inversão, filtro de rede e AC."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

MAINS_HZ = 60.0
# A interferência da rede também aparece nas harmônicas (120 Hz e 180 Hz),
# principalmente perto de fontes com retificador.
MAINS_HARMONICS = 3
# Com menos de dois ciclos da rede, o ajuste confunde parte do sinal com ela.
MIN_MAINS_CYCLES = 2


@dataclass(frozen=True)
class ChannelSettings:
    probe: float = 1.0  # Atenuação da ponteira: 1 ou 10.
    inverted: bool = False
    mains_filter: bool = False
    ac: bool = False


def can_remove_mains(n_samples: int, sample_rate: float, frequency: float = MAINS_HZ) -> bool:
    """Separar a rede do sinal exige alguns ciclos dela na captura."""
    return n_samples / sample_rate >= MIN_MAINS_CYCLES / frequency


def remove_mains(
    samples: np.ndarray,
    sample_rate: float,
    frequency: float = MAINS_HZ,
    harmonics: int = MAINS_HARMONICS,
) -> np.ndarray:
    """Remove a componente da rede e de suas harmônicas.

    Ajusta, por mínimos quadrados, senos e cossenos nessas frequências (mais
    uma constante, para o nível contínuo não vazar no ajuste) e subtrai só a
    parte senoidal. Diferente de um filtro IIR, não deixa transitório no
    começo da captura. Um sinal legítimo nessas frequências também é removido.

    O ajuste é ponderado por uma janela de Hann. Sem ela, um sinal que não
    completa ciclos inteiros na captura vaza alguns por cento para o ajuste e
    sai distorcido; com ela, o vazamento fica abaixo de 0,01% a partir de
    dois ciclos da rede.
    """
    if not can_remove_mains(samples.size, sample_rate, frequency):
        raise ValueError("a captura tem menos ciclos da rede que o necessário")
    basis, solver = _mains_fit(samples.size, sample_rate, frequency, harmonics)
    coefficients = solver @ samples
    return samples - basis[:, 1:] @ coefficients[1:]


@lru_cache(maxsize=2)
def _mains_fit(n: int, sample_rate: float, frequency: float, harmonics: int) -> tuple[np.ndarray, np.ndarray]:
    """Base do ajuste e a matriz que dá os coeficientes, reaproveitadas entre
    capturas do mesmo tamanho: montá-las custa mais que o ajuste em si."""
    t = np.arange(n) / sample_rate
    columns = [np.ones(n)]
    for k in range(1, harmonics + 1):
        f = k * frequency
        if f < sample_rate / 2:
            columns += [np.cos(2 * np.pi * f * t), np.sin(2 * np.pi * f * t)]
    basis = np.column_stack(columns)
    weighted = basis * np.hanning(n)[:, None] ** 2
    # Equações normais: com alguns ciclos da rede, a base é bem condicionada.
    solver = np.linalg.solve(basis.T @ weighted, weighted.T)
    return basis, solver


def condition(samples: np.ndarray, sample_rate: float, settings: ChannelSettings) -> np.ndarray:
    """Converte a tensão na entrada do aparelho na tensão na ponta da ponteira.

    O filtro de rede só é aplicado se a captura tiver ao menos um ciclo dela.
    """
    gain = -settings.probe if settings.inverted else settings.probe
    values = samples * gain
    if settings.mains_filter and can_remove_mains(values.size, sample_rate):
        values = remove_mains(values, sample_rate)
    if settings.ac:
        values = values - values.mean()
    return values


def ac_input_scale(peak: float, current: float, scales: tuple[float, ...], half_divs: float) -> float:
    """Escolhe os volts/div que definem a faixa de entrada no modo AC.

    No AC por software, o nível contínuo ainda passa pelo conversor, então a
    faixa de entrada é escolhida pelo pico do sinal (com o contínuo), não pela
    escala da tela. Sobe um degrau quando o pico encosta no limite (um sinal
    saturado não diz quanto falta) e desce direto ao menor degrau com folga.
    A folga entre subir e descer evita alternar entre duas faixas.
    """
    if peak > 0.95 * half_divs * current:
        larger = [s for s in scales if s > current]
        return min(larger) if larger else current
    smaller = [s for s in scales if s < current and peak < 0.75 * half_divs * s]
    return min(smaller) if smaller else current
