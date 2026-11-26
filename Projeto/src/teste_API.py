#!/usr/bin/env python3

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from time import time_ns
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.write_api import SYNCHRONOUS
from paho.mqtt import MQTTException, publish as mqtt_publish
from pydantic import BaseModel
from urllib3.exceptions import HTTPError

from fuzzy_system.fuzzy import temperature_centroid

# --- CONFIGURAÇÕES DO INFLUXDB ---
INFLUX_URL = os.getenv("INFLUX_URL", "http://3.135.246.204:8086")

INFLUX_TOKEN_FILE = Path(__file__).resolve().parents[1] / "Data" / "token.txt"

INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")

if not INFLUX_TOKEN and INFLUX_TOKEN_FILE.is_file():
    INFLUX_TOKEN = INFLUX_TOKEN_FILE.read_text(encoding="utf-8").strip() or None

INFLUX_ORG = os.getenv("INFLUX_ORG", "minha_org")

INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "teste")

INFLUX_NODE_TAG = os.getenv("INFLUX_NODE_TAG", "node_id")

JANELA_VALIDADE_SEGUNDOS = 150
INTERVALO_SEGUNDOS = 60

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "/esp32/command")

logger = logging.getLogger(__name__)

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
query_api = client.query_api()
write_api = client.write_api(write_options=SYNCHRONOUS)


# --- MODELOS DE ENTRADA ---
class LeituraManual(BaseModel):
    temperatura: float
    umidade: Optional[float] = None
    salvar_no_influx: bool = True


# --- LÓGICA FUZZY E CÁLCULO DO CENTROIDE ---
def calcular_centroide_fuzzy(temperatura: float, amostras: int = 1000) -> float:
    """Calcula o nível fuzzy de resfriamento entre 0 e 100."""
    return temperature_centroid(temperatura, amostras).centroid


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


def publicar_centroide(centroide: float, ts_ms: Optional[int] = None) -> None:
    """Publica o nível fuzzy para a ESP32 via MQTT."""
    timestamp_ms = ts_ms if ts_ms is not None else time_ns() // 1_000_000
    payload = json.dumps(
        {"ts_ms": timestamp_ms, "level": round(float(centroide), 2)},
        separators=(",", ":"),
    )
    mqtt_publish.single(
        topic=MQTT_COMMAND_TOPIC,
        payload=payload,
        qos=1,
        retain=False,
        hostname=MQTT_HOST,
        port=MQTT_PORT,
    )
    logger.info("Centroide %.2f publicado em %s.", centroide, MQTT_COMMAND_TOPIC)


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
            if isinstance(temperatura, bool) or not isinstance(temperatura, (int, float)):
                continue

            temperatura = float(temperatura)
            if not isfinite(temperatura):
                continue

            amostra_anterior = ultimas_por_no.get(no)
            if amostra_anterior is None or ts_ms > amostra_anterior[0]:
                ultimas_por_no[no] = (ts_ms, temperatura)

    return [(no, amostra[1]) for no, amostra in ultimas_por_no.items()]


def processar_media_ultimos_150s(
    agora_ms: Optional[int] = None,
) -> Optional[dict[str, float | int]]:
    """Calcula e grava o centroide da média das últimas amostras válidas por nó."""
    temperaturas = ler_ultimas_temperaturas_validas(agora_ms)
    if not temperaturas:
        logger.info(
            "Nenhuma amostra com ts_ms válido nos últimos %s segundos; nada gravado.",
            JANELA_VALIDADE_SEGUNDOS,
        )
        return None

    temperatura_media = sum(valor for _, valor in temperaturas) / len(temperaturas)
    centroide = calcular_centroide_fuzzy(temperatura_media)
    salvar_centroide(temperatura_media, centroide)
    publicar_centroide(centroide)
    logger.info(
        "Média de %s nó(s) gravada: temperatura=%.2f °C, centroid_percent=%.2f",
        len(temperaturas),
        temperatura_media,
        centroide,
    )
    return {
        "temperatura_c": temperatura_media,
        "centroid_percent": centroide,
        "nos_validos": len(temperaturas),
    }


async def processar_periodicamente() -> None:
    loop = asyncio.get_running_loop()
    proxima_execucao = loop.time() + INTERVALO_SEGUNDOS
    while True:
        await asyncio.sleep(max(0.0, proxima_execucao - loop.time()))
        try:
            await asyncio.to_thread(processar_media_ultimos_150s)
        except (InfluxDBError, HTTPError, MQTTException, OSError):
            logger.exception("Falha no processamento periódico do InfluxDB/MQTT.")
        proxima_execucao += INTERVALO_SEGUNDOS
        if proxima_execucao <= loop.time():
            proxima_execucao = loop.time() + INTERVALO_SEGUNDOS


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    tarefa = asyncio.create_task(processar_periodicamente())
    try:
        yield
    finally:
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass


app = FastAPI(title="API Controlador Fuzzy - InfluxDB", lifespan=lifespan)


# --- ENDPOINTS DA API ---
@app.get("/health")
def health():
    return {"status": "online", "bucket": INFLUX_BUCKET}


@app.post("/fuzzy/calcular")
def calcular_manual(dados: LeituraManual):
    """Recebe temperatura (e umidade opcional), calcula o centroide e grava no InfluxDB."""
    centroide = calcular_centroide_fuzzy(dados.temperatura)
    if dados.salvar_no_influx:
        salvar_centroide(dados.temperatura, centroide, dados.umidade)
    return {
        "temperatura_c": dados.temperatura,
        "umidade_pct": dados.umidade,
        "centroid_percent": centroide,
        "salvo_no_influx": dados.salvar_no_influx,
    }


@app.post("/fuzzy/processar-recente")
def processar_ultimo_do_influx():
    """Lê a última amostra válida de cada nó e grava o centroide da média."""
    resultado = processar_media_ultimos_150s()
    if resultado is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Nenhuma amostra com ts_ms válido nos últimos "
                f"{JANELA_VALIDADE_SEGUNDOS} segundos."
            ),
        )

    return {
        "bucket": INFLUX_BUCKET,
        **resultado,
    }