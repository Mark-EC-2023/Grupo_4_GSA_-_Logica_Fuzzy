"""Configurações da API e dos adaptadores de comunicação."""

import os
from pathlib import Path

INFLUX_TOKEN_FILE = Path(__file__).resolve().parents[2] / "Data" / "token.txt"


def _load_influx_token() -> str | None:
    token = os.getenv("INFLUX_TOKEN")
    if token:
        return token
    if INFLUX_TOKEN_FILE.is_file():
        return INFLUX_TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    return None


INFLUX_URL = os.getenv("INFLUX_URL", "http://3.135.246.204:8086")
INFLUX_TOKEN = _load_influx_token()

INFLUX_ORG = os.getenv("INFLUX_ORG", "minha_org")

INFLUX_SOURCE_BUCKET = os.getenv("INFLUX_SOURCE_BUCKET", "wsn")
INFLUX_OUTPUT_BUCKET = os.getenv("INFLUX_OUTPUT_BUCKET", "teste")

INFLUX_SOURCE_MEASUREMENT = os.getenv("INFLUX_SOURCE_MEASUREMENT", "sensores")
INFLUX_TEMPERATURE_FIELD = os.getenv("INFLUX_TEMPERATURE_FIELD", "temp")

INFLUX_NODE_TAG = os.getenv("INFLUX_NODE_TAG", "node")

INFLUX_NODE_IDS = ("3", "4")
INFLUX_TOPIC_TAG = os.getenv("INFLUX_TOPIC_TAG", "topic")
INFLUX_SENSOR_TOPIC = os.getenv(
    "INFLUX_SENSOR_TOPIC",
    "wsn/central/sensores",
)

JANELA_CONSULTA_SEGUNDOS = 600
INTERVALO_SEGUNDOS = 150

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "/esp32/command")
