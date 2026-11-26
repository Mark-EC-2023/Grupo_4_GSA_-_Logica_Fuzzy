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
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "teste")
INFLUX_NODE_TAG = os.getenv("INFLUX_NODE_TAG", "node_id")

JANELA_VALIDADE_SEGUNDOS = 150
INTERVALO_SEGUNDOS = 60

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "/esp32/command")
