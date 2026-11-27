"""Orquestra a leitura recente, o cálculo fuzzy e a publicação do resultado."""

import logging
from typing import Optional

from comunicacao.influxdb import (
    ler_ultimas_temperaturas_validas,
    salvar_centroide,
)
from config.settings import INFLUX_NODE_IDS, JANELA_CONSULTA_SEGUNDOS
from fuzzy_system.fuzzy import temperature_centroid

logger = logging.getLogger(__name__)


def calcular_centroide_fuzzy(temperatura: float, amostras: int = 1000) -> float:
    """Calcula o nível fuzzy de resfriamento entre 0 e 100."""
    return temperature_centroid(temperatura, amostras).centroid


def processar_media_temperaturas_recentes() -> Optional[dict[str, float | int]]:
    """Calcula e grava o centroide da média das últimas amostras válidas por nó."""
    temperaturas = ler_ultimas_temperaturas_validas()
    nodes_found = {node_id for node_id, _ in temperaturas}
    missing_nodes = set(INFLUX_NODE_IDS) - nodes_found
    if missing_nodes:
        logger.info(
            "Sem leitura recente para os nós %s nos últimos %s segundos; nada gravado.",
            ", ".join(sorted(missing_nodes)),
            JANELA_CONSULTA_SEGUNDOS,
        )
        return None

    temperatura_media = sum(valor for _, valor in temperaturas) / len(temperaturas)
    centroide = calcular_centroide_fuzzy(temperatura_media)
    salvar_centroide(temperatura_media, centroide)
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
