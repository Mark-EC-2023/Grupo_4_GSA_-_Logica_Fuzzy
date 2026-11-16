"""Ponte MQTT entre a telemetria da ESP32 e o controlador fuzzy."""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from fuzzy_controller import temperature_centroid

try:
    import paho.mqtt.client as mqtt
except ImportError as error:
    raise RuntimeError(
        "instale as dependências com 'pip install -r requirements.txt'"
    ) from error


LOGGER = logging.getLogger(__name__)
TELEMETRY_TOPIC = os.getenv("MQTT_TELEMETRY_TOPIC", "grupo4/esp32/telemetry")
LEVEL_TOPIC = os.getenv("MQTT_LEVEL_TOPIC", "grupo4/fuzzy/level")
COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "grupo4/ac/command")
PUBLISH_INTERVAL_SECONDS = float(os.getenv("MQTT_PUBLISH_INTERVAL", "60"))
SAMPLE_MAX_AGE_SECONDS = float(os.getenv("MQTT_SAMPLE_MAX_AGE", "150"))


@dataclass(frozen=True)
class SensorSample:
    node_id: str
    temperature_c: float
    ts_ms: int


def parse_sample(payload: bytes | str) -> SensorSample:
    """Valida a telemetria publicada por uma ESP32."""
    data: Any = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("telemetria deve ser um objeto JSON")

    node_id = data.get("node_id")
    temperature = data.get("temperature_c")
    ts_ms = data.get("ts_ms")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("node_id deve ser uma string não vazia")
    if not isinstance(temperature, (int, float)):
        raise ValueError("temperature_c deve ser numérica")
    if not isinstance(ts_ms, int) or ts_ms < 0:
        raise ValueError("ts_ms deve ser um inteiro não negativo")
    return SensorSample(node_id, float(temperature), ts_ms)


def valid_samples(
    samples: dict[str, SensorSample],
    now_ms: int,
    max_age_seconds: float = SAMPLE_MAX_AGE_SECONDS,
) -> list[SensorSample]:
    """Retorna somente amostras cuja idade é baseada no ts_ms recebido."""
    max_age_ms = max_age_seconds * 1000
    return [
        sample
        for sample in samples.values()
        if 0 <= now_ms - sample.ts_ms <= max_age_ms
    ]


def build_command(samples: list[SensorSample], now_ms: int) -> dict[str, int | float]:
    """Calcula um comando a partir da média das amostras válidas."""
    if not samples:
        raise ValueError("não há amostras válidas")
    temperature = sum(sample.temperature_c for sample in samples) / len(samples)
    return temperature_centroid(temperature).to_control_payload(now_ms)


class MqttFuzzyBridge:
    """Recebe telemetria, calcula o fuzzy e publica o comando de controle."""

    def __init__(self) -> None:
        self.samples: dict[str, SensorSample] = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.last_publish_ms = 0

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        if reason_code != 0:
            raise RuntimeError(f"falha ao conectar no broker: {reason_code}")
        client.subscribe(TELEMETRY_TOPIC, qos=1)
        LOGGER.info("escutando %s", TELEMETRY_TOPIC)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: Any) -> None:
        try:
            sample = parse_sample(message.payload)
            self.samples[sample.node_id] = sample
        except (ValueError, json.JSONDecodeError) as error:
            LOGGER.warning("telemetria descartada: %s", error)

    def publish_if_due(self, now_ms: int | None = None) -> bool:
        """Publica no máximo uma vez por intervalo, sem retain."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if now_ms - self.last_publish_ms < PUBLISH_INTERVAL_SECONDS * 1000:
            return False
        samples = valid_samples(self.samples, now_ms)
        if not samples:
            LOGGER.warning("nenhuma amostra válida; publicação omitida")
            return False
        payload = json.dumps(build_command(samples, now_ms), separators=(",", ":"))
        result = self.client.publish(LEVEL_TOPIC, payload, qos=1, retain=False)
        result.wait_for_publish()
        self.client.publish(COMMAND_TOPIC, payload, qos=1, retain=False)
        self.last_publish_ms = now_ms
        return True

    def run(self) -> None:
        host = os.getenv("MQTT_HOST", "localhost")
        port = int(os.getenv("MQTT_PORT", "1883"))
        self.client.connect(host, port)
        self.client.loop_start()
        try:
            while True:
                self.publish_if_due()
                time.sleep(1)
        finally:
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    MqttFuzzyBridge().run()
