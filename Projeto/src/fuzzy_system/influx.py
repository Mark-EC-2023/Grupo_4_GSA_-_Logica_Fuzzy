"""Leitura de temperaturas e gravação do centroide no InfluxDB."""

import json
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from .config import InfluxSettings
from .fuzzy import FuzzyTemperatureResult

try:
    from influxdb_client import InfluxDBClient, Point
    from influxdb_client.client.write_api import SYNCHRONOUS
except ImportError:
    InfluxDBClient = None
    Point = None
    SYNCHRONOUS = None


@dataclass(frozen=True)
class TemperatureSample:
    node_id: str | None
    temperature: float
    timestamp: datetime


class InfluxRepository:
    """Encapsula as operações de leitura e gravação no InfluxDB."""

    def __init__(self, settings: InfluxSettings) -> None:
        if not settings.token:
            raise RuntimeError(
                "INFLUX_TOKEN não configurado; defina a variável de ambiente"
            )
        if InfluxDBClient is None or Point is None or SYNCHRONOUS is None:
            raise RuntimeError(
                "Dependência ausente: instale influxdb-client"
            )

        self.settings = settings
        self.client = InfluxDBClient(
            url=settings.url,
            token=settings.token,
            org=settings.org,
            timeout=10_000,
        )
        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(
            write_options=SYNCHRONOUS
        )

    def close(self) -> None:
        self.client.close()

    def read_latest(self) -> list[TemperatureSample]:
        """Lê a última temperatura disponível por série de origem."""
        settings = self.settings
        query = (
            f"from(bucket: {json.dumps(settings.source_bucket)})\n"
            f"  |> range(start: -{settings.lookback_seconds}s)\n"
            "  |> filter(fn: (r) => "
            f"r._measurement == {json.dumps(settings.source_measurement)})\n"
            "  |> filter(fn: (r) => "
            f"r._field == {json.dumps(settings.temperature_field)})\n"
            "  |> last()"
        )
        return self._read_samples(query)

    def read_history(
        self,
        start: datetime,
        stop: datetime,
    ) -> list[TemperatureSample]:
        """Lê todas as temperaturas armazenadas no intervalo informado."""
        settings = self.settings
        query = (
            f"from(bucket: {json.dumps(settings.source_bucket)})\n"
            "  |> range("
            f"start: time(v: {json.dumps(start.isoformat())}), "
            f"stop: time(v: {json.dumps(stop.isoformat())})"
            ")\n"
            "  |> filter(fn: (r) => "
            f"r._measurement == {json.dumps(settings.source_measurement)})\n"
            "  |> filter(fn: (r) => "
            f"r._field == {json.dumps(settings.temperature_field)})\n"
            '  |> sort(columns: ["_time"])'
        )
        return self._read_samples(query)

    def _read_samples(self, query: str) -> list[TemperatureSample]:
        settings = self.settings
        latest: list[TemperatureSample] = []
        for table in self.query_api.query(query):
            for record in table.records:
                node_id = record.values.get(settings.node_tag)
                timestamp = record.get_time()
                value = record.get_value()
                if timestamp is None or not isinstance(value, (int, float)):
                    continue
                temperature = float(value)
                if not isfinite(temperature):
                    continue
                latest.append(
                    TemperatureSample(
                        node_id=node_id if isinstance(node_id, str) else None,
                        temperature=temperature,
                        timestamp=timestamp,
                    )
                )
        return latest

    def write_centroid(
        self,
        timestamp: datetime,
        result: FuzzyTemperatureResult,
        sample_count: int,
        node_id: str | None = None,
    ) -> None:
        """Grava o centroide calculado no bucket de saída configurado."""
        if Point is None:
            raise RuntimeError(
                "Dependência ausente: instale influxdb-client"
            )
        point = (
            Point(self.settings.output_measurement)
            .tag("grupo", "grupo4")
            .tag("origem", self.settings.source_measurement)
            .field("temperature_c", result.temperature)
            .field("centroid_percent", result.centroid)
            .field("sample_count", sample_count)
            .time(timestamp)
        )
        if node_id is not None:
            point = point.tag(self.settings.node_tag, node_id)
        self.write_api.write(
            bucket=self.settings.output_bucket,
            org=self.settings.org,
            record=point,
        )
