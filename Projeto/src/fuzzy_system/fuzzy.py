"""Fuzzificação da temperatura e cálculo do centroide."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class FuzzyTemperatureResult:
    temperature: float
    cold: float
    warm: float
    hot: float
    weak_activation: float
    medium_activation: float
    strong_activation: float
    centroid: float


def triangular(x: float, a: float, b: float, c: float) -> float:
    """Calcula a pertinência de x em uma função triangular."""
    if not all(isfinite(value) for value in (x, a, b, c)):
        raise ValueError("x e os parâmetros da função devem ser finitos")
    if a > b or b > c:
        raise ValueError("a, b e c devem estar em ordem crescente")
    if x < a or x > c:
        return 0.0
    if a == b and x == a:
        return 1.0
    if b == c and x == c:
        return 1.0
    if x == b:
        return 1.0
    if x < b:
        return (x - a) / (b - a)
    return (c - x) / (c - b)


def trapezoidal(x: float, a: float, b: float, c: float, d: float) -> float:
    """Calcula a pertinência de x em uma função trapezoidal."""
    if not all(isfinite(value) for value in (x, a, b, c, d)):
        raise ValueError("x e os parâmetros da função devem ser finitos")
    if a > b or b > c or c > d:
        raise ValueError("a, b, c e d devem estar em ordem crescente")
    if b == a and x <= b:
        return 1.0
    if c == d and x >= c:
        return 1.0
    if x < a or x > d:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c:
        return 1.0
    return (d - x) / (d - c)


def _output_centroid(cold: float, warm: float, hot: float, samples: int) -> float:
    numerator = 0.0
    denominator = 0.0
    for index in range(samples):
        output = 100.0 * index / (samples - 1)
        weak = min(cold, triangular(output, 0.0, 0.0, 40.0))
        medium = min(warm, triangular(output, 20.0, 50.0, 80.0))
        strong = min(
            hot,
            trapezoidal(output, 60.0, 85.0, 100.0, 100.0),
        )
        aggregated = max(weak, medium, strong)
        numerator += output * aggregated
        denominator += aggregated
    return numerator / denominator if denominator else 0.0


def temperature_centroid(
    temperature: float,
    samples: int = 1000,
) -> FuzzyTemperatureResult:
    """Calcula as pertinências fuzzy e o centroide da temperatura."""
    if not isfinite(temperature):
        raise ValueError("temperature deve ser um número finito")
    if samples < 2:
        raise ValueError("samples deve ser maior ou igual a 2")

    cold = triangular(temperature, 15.0, 15.0, 25.0)
    warm = triangular(temperature, 20.0, 26.0, 32.0)
    hot = trapezoidal(temperature, 25.0, 45.0, 45.0, 45.0)

    if temperature <= 15.0:
        centroid = 0.0
    elif temperature >= 45.0:
        centroid = 100.0
    else:
        raw_centroid = _output_centroid(cold, warm, hot, samples)
        minimum_centroid = _output_centroid(1.0, 0.0, 0.0, samples)
        maximum_centroid = _output_centroid(0.0, 0.0, 1.0, samples)
        centroid = 100.0 * (
            (raw_centroid - minimum_centroid)
            / (maximum_centroid - minimum_centroid)
        )
        centroid = min(100.0, max(0.0, centroid))

    return FuzzyTemperatureResult(
        temperature=temperature,
        cold=cold,
        warm=warm,
        hot=hot,
        weak_activation=cold,
        medium_activation=warm,
        strong_activation=hot,
        centroid=round(centroid, 2),
    )
