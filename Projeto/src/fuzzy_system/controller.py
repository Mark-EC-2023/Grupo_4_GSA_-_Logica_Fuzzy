"""Orquestra a leitura recente, o cálculo fuzzy e a publicação do resultado."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from comunicacao.influxdb import (
    ler_ultimas_temperaturas_validas,
    ler_ultimas_umidades_validas,
    salvar_centroide,
)
from comunicacao.mqtt import publicar_centroide
from config.settings import INFLUX_NODE_IDS, VERIFICAR_IDADE_AMOSTRAS
from fuzzy_system.fuzzy import temperature_centroid

logger = logging.getLogger(__name__)

IDADE_MAXIMA_AMOSTRA = timedelta(hours=1)


def calcular_centroide_fuzzy(temperatura: float, amostras: int = 1000) -> float:
    """Calcula o nível fuzzy de resfriamento entre 0 e 100."""
    return temperature_centroid(temperatura, amostras).centroid


def processar_media_temperaturas_recentes() -> Optional[dict[str, float | int | bool]]:
    """Calcula e publica usando temperaturas recentes ou as últimas disponíveis."""
    temperaturas = ler_ultimas_temperaturas_validas()
    if not temperaturas:
        logger.info(
            "Nenhuma temperatura armazenada para os nós configurados; nada enviado."
        )
        return None

    limite_antiguidade = datetime.now(timezone.utc) - IDADE_MAXIMA_AMOSTRA
    if VERIFICAR_IDADE_AMOSTRAS and all(
        (
            leitura.timestamp
            if leitura.timestamp.tzinfo is not None
            else leitura.timestamp.replace(tzinfo=timezone.utc)
        )
        < limite_antiguidade
        for leitura in temperaturas
    ):
        logger.info(
            "Todas as últimas temperaturas têm mais de uma hora; "
            "centroide não calculado nem enviado."
        )
        return None

    umidades = ler_ultimas_umidades_validas()
    if umidades:
        umidade_media = sum(leitura.valor for leitura in umidades) / len(umidades)
        print(
            "[WSN debug] Média de umidade dos nós "
            f"{', '.join(leitura.node_id for leitura in umidades)}: "
            f"{umidade_media:.2f}%",
            flush=True,
        )
    else:
        print(
            "[WSN debug] Nenhuma umidade válida encontrada para calcular a média.",
            flush=True,
        )

    nodes_found = {leitura.node_id for leitura in temperaturas}
    missing_nodes = set(INFLUX_NODE_IDS) - nodes_found
    if missing_nodes:
        logger.warning(
            "Sem histórico de temperatura para os nós %s; calculando com os nós disponíveis.",
            ", ".join(sorted(missing_nodes)),
        )

    if any(leitura.fallback for leitura in temperaturas):
        amostras_fallback = [
            f"{leitura.node_id}={leitura.valor:.2f}°C em {leitura.timestamp.isoformat()}"
            for leitura in temperaturas
            if leitura.fallback
        ]
        print(
            "[WSN debug] Reutilizando últimas temperaturas armazenadas: "
            + ", ".join(amostras_fallback),
            flush=True,
        )

    temperatura_media = (
        sum(leitura.valor for leitura in temperaturas) / len(temperaturas)
    )
    centroide = calcular_centroide_fuzzy(temperatura_media)
    timestamp_dados = min(leitura.timestamp for leitura in temperaturas)
    timestamp_dados_ms = int(timestamp_dados.timestamp() * 1000)
    salvar_centroide(temperatura_media, centroide)
    publicar_centroide(centroide, ts_ms=timestamp_dados_ms)
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
        "usou_fallback": any(leitura.fallback for leitura in temperaturas),
        "timestamp_dados_ms": timestamp_dados_ms,
    }
