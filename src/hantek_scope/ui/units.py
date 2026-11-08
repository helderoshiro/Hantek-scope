def format_si(value: float, unit: str) -> str:
    """Valor com prefixo SI e 4 algarismos significativos, como "200 µs"."""
    if value == 0:
        return f"0 {unit}"
    for scale, prefix in ((1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ")):
        # Compara já arredondado, para 999,97 virar "1 k" e não "1000".
        if abs(float(f"{value / scale:.4g}")) >= 1:
            return f"{value / scale:.4g} {prefix}{unit}"
    return f"{value / 1e-9:.4g} n{unit}"
