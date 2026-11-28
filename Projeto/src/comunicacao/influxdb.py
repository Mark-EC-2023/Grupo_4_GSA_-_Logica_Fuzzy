"""Leitura de telemetria e gravação do resultado fuzzy no InfluxDB."""

import json
from dataclasses import dataclass
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


@dataclass(frozen=True)
class LeituraSensor:
    node_id: str
    valor: float
    timestamp: datetime
    fallback: bool = False


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
    if INFLUX_SOURCE_BUCKET != INFLUX_OUTPUT_BUCKET:
        ponto_wsn = (
            Point("fuzzy_result")
            .tag("grupo", "grupo4")
            .tag("origem", "api_python")
            .field("centroid_percent", float(centroide))
            .time(ts, WritePrecision.S)
        )
        write_api.write(
            bucket=INFLUX_SOURCE_BUCKET,
            org=INFLUX_ORG,
            record=ponto_wsn,
        )


def _ler_ultimos_valores_validos(
    campo: str,
    descricao: str,
    range_start: str = f"-{JANELA_CONSULTA_SEGUNDOS}s",
    node_ids: Optional[tuple[str, ...]] = None,
    fallback: bool = False,
) -> list[LeituraSensor]:
    filtro_nos = ""
    if node_ids:
        condicoes_nos = " or ".join(
            f"r[{json.dumps(INFLUX_NODE_TAG)}] == {json.dumps(node_id)}"
            for node_id in node_ids
        )
        filtro_nos = f'|> filter(fn: (r) => {condicoes_nos})'

    query = f"""
    from(bucket: {json.dumps(INFLUX_SOURCE_BUCKET)})
      |> range(start: {range_start})
      |> filter(fn: (r) => r["_measurement"] == {json.dumps(INFLUX_SOURCE_MEASUREMENT)})
      |> filter(fn: (r) => r["_field"] == {json.dumps(campo)})
      {filtro_nos}
      |> group(columns: [{json.dumps(INFLUX_NODE_TAG)}, {json.dumps(INFLUX_TOPIC_TAG)}])
      |> last()
    """
    tabelas = query_api.query(query)
    ultimas_por_no: dict[str, LeituraSensor] = {}
    registros_encontrados = 0
    for tabela in tabelas:
        for record in tabela.records:
            valores = record.values
            registros_encontrados += 1
            no = valores.get(INFLUX_NODE_TAG)
            topico = valores.get(INFLUX_TOPIC_TAG)
            valor = valores.get("_value")
            timestamp = valores.get("_time")
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
            if not isinstance(timestamp, datetime):
                continue

            valor = float(valor)
            if not isfinite(valor):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            ultimas_por_no[no] = LeituraSensor(
                node_id=no,
                valor=valor,
                timestamp=timestamp,
                fallback=fallback,
            )

    if registros_encontrados == 0:
        print(
            "[WSN debug] Nenhum registro retornado: "
            f"bucket={INFLUX_SOURCE_BUCKET!r}, "
            f"measurement={INFLUX_SOURCE_MEASUREMENT!r}, "
            f"field={campo!r}, "
            f"range={range_start}.",
            flush=True,
        )
    print(
        f"[WSN debug] {descricao} aceitas: {ultimas_por_no!r}",
        flush=True,
    )

    return [
        ultimas_por_no[node_id]
        for node_id in INFLUX_NODE_IDS
        if node_id in ultimas_por_no
    ]


def ler_ultimas_temperaturas_validas() -> list[LeituraSensor]:
    """Lê temperaturas recentes e completa os nós ausentes pelo último histórico."""
    recentes = _ler_ultimos_valores_validos(
        INFLUX_TEMPERATURE_FIELD,
        "Temperaturas",
    )
    recentes_por_no = {leitura.node_id: leitura for leitura in recentes}
    nos_sem_leitura = tuple(
        node_id for node_id in INFLUX_NODE_IDS if node_id not in recentes_por_no
    )
    if nos_sem_leitura:
        print(
            "[WSN debug] Sem temperatura na janela de "
            f"{JANELA_CONSULTA_SEGUNDOS}s para {nos_sem_leitura}; "
            "buscando a última leitura armazenada.",
            flush=True,
        )
        historicas = _ler_ultimos_valores_validos(
            INFLUX_TEMPERATURE_FIELD,
            "Últimas temperaturas armazenadas",
            range_start="1970-01-01T00:00:00Z",
            node_ids=nos_sem_leitura,
            fallback=True,
        )
        recentes_por_no.update(
            {
                leitura.node_id: leitura
                for leitura in historicas
                if leitura.node_id not in recentes_por_no
            }
        )

    return [
        recentes_por_no[node_id]
        for node_id in INFLUX_NODE_IDS
        if node_id in recentes_por_no
    ]


def ler_ultimas_umidades_validas() -> list[LeituraSensor]:
    """Lê a última umidade recente dos nós configurados no bucket WSN."""
    return _ler_ultimos_valores_validos(
        INFLUX_HUMIDITY_FIELD,
        "Umidades",
    )
