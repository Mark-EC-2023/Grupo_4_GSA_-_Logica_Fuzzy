"""Controlador fuzzy de temperatura com defuzzificação por centroide."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class FuzzyTemperatureResult:
    """Resultado da fuzzificação, das regras e do centroide."""

    temperature: float
    cold: float
    warm: float
    hot: float
    weak_activation: float
    medium_activation: float
    strong_activation: float
    centroid: float

    def to_mqtt_payload(self) -> dict[str, object]:
        """Converte o resultado para um payload JSON compatível com MQTT."""
        return {
            "temperature_c": self.temperature,
            "membership": {
                "cold": self.cold,
                "warm": self.warm,
                "hot": self.hot,
            },
            "activation": {
                "weak": self.weak_activation,
                "medium": self.medium_activation,
                "strong": self.strong_activation,
            },
            "centroid_percent": self.centroid,
        }


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
    if x < a or x > d:
        return 0.0
    if b == a and x <= b:
        return 1.0
    if c == d and x >= c:
        return 1.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c:
        return 1.0
    return (d - x) / (d - c)


def _centroid(
    weak_activation: float,
    medium_activation: float,
    strong_activation: float,
    samples: int,
) -> float:
    if samples < 2:
        raise ValueError("samples deve ser maior ou igual a 2")

    numerator = 0.0
    denominator = 0.0
    for index in range(samples):
        output = 100.0 * index / (samples - 1)
        weak = min(
            weak_activation,
            triangular(output, 0.0, 0.0, 40.0),
        )
        medium = min(
            medium_activation,
            triangular(output, 20.0, 50.0, 80.0),
        )
        strong = min(
            strong_activation,
            trapezoidal(output, 60.0, 85.0, 100.0, 100.0),
        )
        aggregated = max(weak, medium, strong)
        numerator += output * aggregated
        denominator += aggregated

    return numerator / denominator if denominator else 0.0


def temperature_centroid(temperature: float, samples: int = 1000) -> FuzzyTemperatureResult:
    """Retorna o centroide do nível de atuação para uma temperatura em °C.

    As funções e regras são as usadas no notebook:
    frio -> atuação fraca, morno -> média e quente -> forte.
    """
    if not isfinite(temperature):
        raise ValueError("temperature deve ser um número finito")

    cold = triangular(temperature, 15.0, 15.0, 25.0)
    warm = triangular(temperature, 20.0, 25.0, 32.0)
    hot = trapezoidal(temperature, 28.0, 35.0, 45.0, 45.0)
    centroid = _centroid(cold, warm, hot, samples)

    return FuzzyTemperatureResult(
        temperature=temperature,
        cold=cold,
        warm=warm,
        hot=hot,
        weak_activation=cold,
        medium_activation=warm,
        strong_activation=hot,
        centroid=centroid,
    )
