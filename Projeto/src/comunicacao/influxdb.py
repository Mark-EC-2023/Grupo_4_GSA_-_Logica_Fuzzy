"""Leitura de telemetria e gravação do resultado fuzzy no InfluxDB."""

import json
from datetime import datetime, timezone
from math import isfinite
from typing import Optional

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from config.settings import (
    INFLUX_HUMIDITY_FIELD,
    INFLUX_NODE_IDS,
    INFLUX_NODE_TAG,
    INFLUX_ORG,
    INFLUX_OUTPUT_BUCKET,
    INFLUX_SENSOR_TOPIC,
    INFLUX_SOURCE_BUCKET,
    INFLUX_SOURCE_MEASUREMENT,
    INFLUX_TOKEN,
    INFLUX_TEMPERATURE_FIELD,
    INFLUX_TOPIC_TAG,
    INFLUX_URL,
    JANELA_CONSULTA_SEGUNDOS,
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

    write_api.write(bucket=INFLUX_OUTPUT_BUCKET, org=INFLUX_ORG, record=ponto)


def _ler_ultimos_valores_validos(
    campo: str,
    descricao: str,
) -> list[tuple[str, float]]:
    query = f"""
    from(bucket: {json.dumps(INFLUX_SOURCE_BUCKET)})
      |> range(start: -{JANELA_CONSULTA_SEGUNDOS}s)
      |> filter(fn: (r) => r["_measurement"] == {json.dumps(INFLUX_SOURCE_MEASUREMENT)})
      |> filter(fn: (r) => r["_field"] == {json.dumps(campo)})
      |> group(columns: [{json.dumps(INFLUX_NODE_TAG)}, {json.dumps(INFLUX_TOPIC_TAG)}])
      |> last()
    """
    tabelas = query_api.query(query)
    ultimas_por_no: dict[str, float] = {}
    registros_encontrados = 0
    for tabela in tabelas:
        for record in tabela.records:
            valores = record.values
            registros_encontrados += 1
            no = valores.get(INFLUX_NODE_TAG)
            topico = valores.get(INFLUX_TOPIC_TAG)
            valor = valores.get("_value")
            tags = {
                key: value
                for key, value in valores.items()
                if not key.startswith("_")
            }
            print(
                "[WSN debug] "
                f"tags={tags!r}, time={valores.get('_time')!r}, "
                f"measurement={valores.get('_measurement')!r}, "
                f"field={valores.get('_field')!r}, value={valor!r}",
                flush=True,
            )
            if no not in INFLUX_NODE_IDS or topico != INFLUX_SENSOR_TOPIC:
                continue
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                continue

            valor = float(valor)
            if not isfinite(valor):
                continue

            ultimas_por_no[no] = valor

    if registros_encontrados == 0:
        print(
            "[WSN debug] Nenhum registro retornado: "
            f"bucket={INFLUX_SOURCE_BUCKET!r}, "
            f"measurement={INFLUX_SOURCE_MEASUREMENT!r}, "
            f"field={campo!r}, "
            f"range={JANELA_CONSULTA_SEGUNDOS}s.",
            flush=True,
        )
    print(
        f"[WSN debug] {descricao} aceitas: {ultimas_por_no!r}",
        flush=True,
    )

    return list(ultimas_por_no.items())


def ler_ultimas_temperaturas_validas() -> list[tuple[str, float]]:
    """Lê a última temperatura recente dos nós 3 e 4 no bucket WSN."""
    return _ler_ultimos_valores_validos(
        INFLUX_TEMPERATURE_FIELD,
        "Temperaturas",
    )


def ler_ultimas_umidades_validas() -> list[tuple[str, float]]:
    """Lê a última umidade recente dos nós 3 e 4 no bucket WSN."""
    return _ler_ultimos_valores_validos(
        INFLUX_HUMIDITY_FIELD,
        "Umidades",
    )
