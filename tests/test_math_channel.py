import numpy as np
import pytest

from hantek_scope.processing.math_channel import combine

A = np.array([1.0, 2.0, -3.0])
B = np.array([0.5, -1.0, 0.001])


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("add", [1.5, 1.0, -2.999]),
        ("sub", [0.5, 3.0, -3.001]),
        ("mul", [0.5, -2.0, -0.003]),
    ],
)
def test_operations(operation, expected):
    assert combine(operation, A, B) == pytest.approx(expected)


def test_division_masks_divisors_near_zero():
    result = combine("div", A, B, min_divisor=0.01)
    assert result[:2] == pytest.approx([2.0, -2.0])
    assert np.isnan(result[2])


def test_division_by_exact_zero_is_nan():
    assert np.isnan(combine("div", np.array([1.0]), np.array([0.0]))).all()


def test_unknown_operation_fails():
    with pytest.raises(ValueError):
        combine("pow", A, B)
