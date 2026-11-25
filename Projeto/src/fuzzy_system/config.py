"""Configuração do processamento e da conexão com o InfluxDB."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InfluxSettings:
    url: str
    token: str | None
    org: str
    source_bucket: str
    output_bucket: str
    source_measurement: str
    temperature_field: str
    node_tag: str
    output_measurement: str
    lookback_seconds: float
    poll_seconds: float

    @classmethod
    def from_env(cls) -> "InfluxSettings":
        source_bucket = os.getenv(
            "INFLUX_SOURCE_BUCKET",
            os.getenv("INFLUX_BUCKET", "teste"),
        )
        settings = cls(
            url=os.getenv("INFLUX_URL", "http://3.135.246.204:8086"),
            token=os.getenv("INFLUX_TOKEN"),
            org=os.getenv("INFLUX_ORG", "minha_org"),
            source_bucket=source_bucket,
            output_bucket=os.getenv("INFLUX_OUTPUT_BUCKET", "teste"),
            source_measurement=os.getenv(
                "INFLUX_MEASUREMENT",
                "telemetry",
            ),
            temperature_field=os.getenv(
                "INFLUX_TEMPERATURE_FIELD",
                "temperature_c",
            ),
            node_tag=os.getenv("INFLUX_NODE_TAG", "node_id"),
            output_measurement=os.getenv(
                "INFLUX_FUZZY_MEASUREMENT",
                "fuzzy_result",
            ),
            lookback_seconds=float(
                os.getenv("INFLUX_LOOKBACK_SECONDS", "150")
            ),
            poll_seconds=float(os.getenv("INFLUX_POLL_SECONDS", "30")),
        )
        if settings.lookback_seconds <= 0:
            raise ValueError("INFLUX_LOOKBACK_SECONDS deve ser positivo")
        if settings.poll_seconds <= 0:
            raise ValueError("INFLUX_POLL_SECONDS deve ser positivo")
        return settings
