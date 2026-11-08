import numpy as np

from hantek_scope.export import write_csv


def test_standard_csv(tmp_path):
    path = tmp_path / "a.csv"
    write_csv(path, np.array([0.0, 1e-6]), {"CH1_V": np.array([0.5, -1.25])}, ["Fonte: teste"])
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# Fonte: teste",
        "tempo_s,CH1_V",
        "0,0.5",
        "1e-06,-1.25",
    ]


def test_excel_ptbr_csv(tmp_path):
    path = tmp_path / "b.csv"
    write_csv(
        path,
        np.array([0.0, 0.5]),
        {"CH1_V": np.array([0.5, 1.0]), "CH2_V": np.array([2.25, 0.0])},
        [],
        excel_ptbr=True,
    )
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "tempo_s;CH1_V;CH2_V",
        "0;0,5;2,25",
        "0,5;1;0",
    ]
