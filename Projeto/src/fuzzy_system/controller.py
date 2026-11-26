"""Orquestra a leitura recente, o cálculo fuzzy e a publicação do resultado."""

import logging
from typing import Optional

from comunicacao.influxdb import (
    ler_ultimas_temperaturas_validas,
    salvar_centroide,
)
from comunicacao.mqtt import publicar_centroide
from config.settings import JANELA_VALIDADE_SEGUNDOS
from fuzzy_system.fuzzy import temperature_centroid

logger = logging.getLogger(__name__)


def calcular_centroide_fuzzy(temperatura: float, amostras: int = 1000) -> float:
    """Calcula o nível fuzzy de resfriamento entre 0 e 100."""
    return temperature_centroid(temperatura, amostras).centroid


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
