"""Aplicação FastAPI do controlador fuzzy."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException
from influxdb_client.client.exceptions import InfluxDBError
from paho.mqtt import MQTTException
from pydantic import BaseModel
from urllib3.exceptions import HTTPError

from config.settings import (
    INFLUX_BUCKET,
    INTERVALO_SEGUNDOS,
    JANELA_VALIDADE_SEGUNDOS,
)
from fuzzy_system.controller import (
    calcular_centroide_fuzzy,
    processar_media_ultimos_150s,
)
from comunicacao.influxdb import salvar_centroide

logger = logging.getLogger(__name__)


class LeituraManual(BaseModel):
    temperatura: float
    umidade: Optional[float] = None
    salvar_no_influx: bool = True


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "online", "bucket": INFLUX_BUCKET}


@app.post("/fuzzy/calcular")
def calcular_manual(dados: LeituraManual) -> dict[str, object]:
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
def processar_ultimo_do_influx() -> dict[str, object]:
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
