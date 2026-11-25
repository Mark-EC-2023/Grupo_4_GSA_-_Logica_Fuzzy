"""Orquestra a leitura, o cálculo fuzzy e a gravação no InfluxDB."""

from datetime import datetime, timezone
from typing import Protocol

from .config import InfluxSettings
from .fuzzy import FuzzyTemperatureResult, temperature_centroid
from .influx import TemperatureSample


class TemperatureRepository(Protocol):
    def read_latest(self) -> list[TemperatureSample]: ...

    def read_history(
        self,
        start: datetime,
        stop: datetime,
    ) -> list[TemperatureSample]: ...

    def write_centroid(
        self,
        timestamp: datetime,
        result: FuzzyTemperatureResult,
        sample_count: int,
        node_id: str | None = None,
    ) -> None: ...


def process_latest(
    settings: InfluxSettings,
    repository: TemperatureRepository,
    now: datetime | None = None,
) -> dict[str, object]:
    """Calcula um centroide agregado e devolve o resumo da execução."""
    current_time = now or datetime.now(timezone.utc)
    samples = repository.read_latest()
    valid_samples = [
        sample
        for sample in samples
        if 0 <= (current_time - sample.timestamp).total_seconds()
        <= settings.lookback_seconds
    ]
    if not valid_samples:
        return {
            "status": "no_data",
            "source_bucket": settings.source_bucket,
            "output_bucket": settings.output_bucket,
            "samples": 0,
        }

    average_temperature = sum(
        sample.temperature for sample in valid_samples
    ) / len(valid_samples)
    result = temperature_centroid(average_temperature)
    timestamp = max(sample.timestamp for sample in valid_samples)
    repository.write_centroid(timestamp, result, len(valid_samples))

    return {
        "status": "written",
        "source_bucket": settings.source_bucket,
        "output_bucket": settings.output_bucket,
        "measurement": settings.output_measurement,
        "time": timestamp.isoformat(),
        "samples": len(valid_samples),
        "temperature_c": result.temperature,
        "centroid_percent": round(result.centroid, 2),
    }


def process_history(
    settings: InfluxSettings,
    repository: TemperatureRepository,
    start: datetime,
    stop: datetime,
) -> dict[str, object]:
    """Calcula e grava um centroide para cada temperatura histórica."""
    if start >= stop:
        raise ValueError("O início do histórico deve ser anterior ao fim")

    samples = repository.read_history(start, stop)
    historical_samples = [
        sample for sample in samples if start <= sample.timestamp < stop
    ]
    for sample in historical_samples:
        result = temperature_centroid(sample.temperature)
        repository.write_centroid(
            sample.timestamp,
            result,
            1,
            node_id=sample.node_id,
        )

    return {
        "status": "written" if historical_samples else "no_data",
        "source_bucket": settings.source_bucket,
        "output_bucket": settings.output_bucket,
        "measurement": settings.output_measurement,
        "start": start.isoformat(),
        "stop": stop.isoformat(),
        "samples": len(historical_samples),
    }
