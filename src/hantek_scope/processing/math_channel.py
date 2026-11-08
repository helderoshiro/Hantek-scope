"""Operações entre os dois canais (traço MATH)."""

import numpy as np

# Operação: (rótulo, unidade do resultado).
OPERATIONS = {
    "add": ("CH1 + CH2", "V"),
    "sub": ("CH1 − CH2", "V"),
    "mul": ("CH1 × CH2", "V²"),
    "div": ("CH1 ÷ CH2", "V/V"),
}


def combine(operation: str, a: np.ndarray, b: np.ndarray, min_divisor: float = 0.0) -> np.ndarray:
    """Calcula a operação amostra a amostra.

    Na divisão, onde |b| < min_divisor o resultado é NaN, em vez de um valor
    enorme causado pelo ruído perto de zero.
    """
    if operation == "add":
        return a + b
    if operation == "sub":
        return a - b
    if operation == "mul":
        return a * b
    if operation == "div":
        result = np.full(a.shape, np.nan)
        valid = np.abs(b) >= max(min_divisor, np.finfo(float).tiny)
        np.divide(a, b, out=result, where=valid)
        return result
    raise ValueError(f"operação desconhecida: {operation}")
