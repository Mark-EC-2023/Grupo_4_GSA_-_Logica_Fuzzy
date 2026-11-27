"""Publicação dos comandos fuzzy via MQTT."""

import json
import logging
from time import time_ns
from typing import Optional

from paho.mqtt import publish as mqtt_publish

from config.settings import MQTT_COMMAND_TOPIC, MQTT_HOST, MQTT_PORT

logger = logging.getLogger(__name__)


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
