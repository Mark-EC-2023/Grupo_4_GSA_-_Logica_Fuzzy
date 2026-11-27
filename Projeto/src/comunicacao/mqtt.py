"""Publicação dos comandos fuzzy via MQTT."""

import json
import logging
from math import isfinite
from time import time_ns
from typing import Optional

from paho.mqtt import MQTTException
from paho.mqtt import publish as mqtt_publish

from config.settings import (
    MQTT_COMMAND_TOPIC,
    MQTT_HOST,
    MQTT_PASS,
    MQTT_PORT,
    MQTT_USER,
)

logger = logging.getLogger(__name__)


class MQTTPublishError(RuntimeError):
    """Falha explícita ao publicar um nível fuzzy no broker MQTT."""


def publicar_centroide(centroide: float, ts_ms: Optional[int] = None) -> None:
    """Publica o nível fuzzy para a ESP32 via MQTT."""
    level = float(centroide)
    if not isfinite(level) or not 0.0 <= level <= 100.0:
        raise ValueError("centroide deve ser um valor finito entre 0 e 100")
    if not MQTT_USER or not MQTT_PASS:
        raise MQTTPublishError("Configure MQTT_USER e MQTT_PASS no ambiente.")

    timestamp_ms = ts_ms if ts_ms is not None else time_ns() // 1_000_000
    payload = json.dumps(
        {"ts_ms": timestamp_ms, "level": round(level, 2)},
        separators=(",", ":"),
    )
    print(
        f"[MQTT debug] Enviando para {MQTT_HOST}:{MQTT_PORT} "
        f"no tópico {MQTT_COMMAND_TOPIC}: {payload}",
        flush=True,
    )
    try:
        mqtt_publish.single(
            topic=MQTT_COMMAND_TOPIC,
            payload=payload,
            qos=1,
            retain=False,
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            auth={"username": MQTT_USER, "password": MQTT_PASS},
        )
    except (MQTTException, OSError) as error:
        raise MQTTPublishError("Não foi possível publicar no broker MQTT.") from error
    logger.info("Nível fuzzy %.2f publicado em %s.", level, MQTT_COMMAND_TOPIC)
