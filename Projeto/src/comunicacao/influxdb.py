"""Leitura de telemetria e gravação do resultado fuzzy no InfluxDB."""

import json
from datetime import datetime, timezone
from math import isfinite
from time import time_ns
from typing import Optional

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from config.settings import (
    INFLUX_BUCKET,
    INFLUX_NODE_TAG,
    INFLUX_ORG,
    INFLUX_TOKEN,
    INFLUX_URL,
    JANELA_VALIDADE_SEGUNDOS,
)

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
query_api = client.query_api()
write_api = client.write_api(write_options=SYNCHRONOUS)


def salvar_centroide(
    temperatura: float,
    centroide: float,
    umidade: Optional[float] = None,
    timestamp: Optional[datetime] = None,
) -> None:
    ts = timestamp or datetime.now(timezone.utc)
    ponto = (
        Point("fuzzy_result")
        .tag("grupo", "grupo4")
        .tag("origem", "api_python")
        .field("temperature_c", float(temperatura))
        .field("centroid_percent", float(centroide))
        .time(ts, WritePrecision.S)
    )
    if umidade is not None:
        ponto = ponto.field("humidity_pct", float(umidade))

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=ponto)


def ler_ultimas_temperaturas_validas(
    agora_ms: Optional[int] = None,
) -> list[tuple[str, float]]:
    """Retorna a última amostra recente de cada nó, validada pelo ts_ms."""
    agora_ms = agora_ms if agora_ms is not None else time_ns() // 1_000_000
    measurement_filter = " or ".join(
        f'r["_measurement"] == {json.dumps(measurement)}'
        for measurement in ("climate_data", "telemetry")
    )
    field_filter = " or ".join(
        f'r["_field"] == {json.dumps(field)}'
        for field in ("cooling_temperature_c", "temperature_c", "ts_ms")
    )
    query = f"""
    from(bucket: {json.dumps(INFLUX_BUCKET)})
      |> range(start: -{JANELA_VALIDADE_SEGUNDOS}s)
      |> filter(fn: (r) => {measurement_filter})
      |> filter(fn: (r) => {field_filter})
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    """
    tabelas = query_api.query(query)
    ultimas_por_no: dict[str, tuple[int, float]] = {}
    for tabela in tabelas:
        for record in tabela.records:
            valores = record.values
            no = valores.get(INFLUX_NODE_TAG)
            ts_ms = valores.get("ts_ms")
            temperatura = valores.get("cooling_temperature_c")
            if temperatura is None:
                temperatura = valores.get("temperature_c")

            if not isinstance(no, str) or not no.strip():
                continue
            if isinstance(ts_ms, bool) or not isinstance(ts_ms, int):
                continue
            idade_ms = agora_ms - ts_ms
            if not 0 <= idade_ms <= JANELA_VALIDADE_SEGUNDOS * 1000:
                continue
            if isinstance(temperatura, bool) or not isinstance(
                temperatura, (int, float)
            ):
                continue

            temperatura = float(temperatura)
            if not isfinite(temperatura):
                continue

            amostra_anterior = ultimas_por_no.get(no)
            if amostra_anterior is None or ts_ms > amostra_anterior[0]:
                ultimas_por_no[no] = (ts_ms, temperatura)

    return [(no, amostra[1]) for no, amostra in ultimas_por_no.items()]
