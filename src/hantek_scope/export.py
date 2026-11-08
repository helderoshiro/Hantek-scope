from pathlib import Path

import numpy as np


def write_csv(
    path: Path,
    time_s: np.ndarray,
    channels: dict[str, np.ndarray],
    header: list[str],
    excel_ptbr: bool = False,
) -> None:
    """Grava tempo e tensões, com o cabeçalho em linhas iniciadas por '#'.

    No formato do Excel em português, as colunas são separadas por ';', a
    vírgula é o separador decimal e o arquivo leva BOM, sem o qual o Excel
    lê acentos e "µ" errado.
    """
    separator = ";" if excel_ptbr else ","
    names = ["tempo_s", *(f"{name}_V" for name in channels)]
    columns = [time_s, *channels.values()]
    formats = ["{:.9g}"] + ["{:.6g}"] * len(channels)

    lines = [f"# {line}" for line in header]
    lines.append(separator.join(names))
    for row in zip(*columns):
        text = separator.join(fmt.format(value) for fmt, value in zip(formats, row))
        lines.append(text.replace(".", ",") if excel_ptbr else text)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig" if excel_ptbr else "utf-8")
