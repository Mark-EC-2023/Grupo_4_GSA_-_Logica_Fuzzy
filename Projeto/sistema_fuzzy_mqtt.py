#!/usr/bin/env python3
"""Sistema único de controle fuzzy e integração MQTT."""

import argparse
import json
import logging
import os
import time
from math import isfinite
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError as error:
    raise SystemExit(
        "Dependência ausente: instale com 'pip install -r requirements.txt'"
    ) from error


LOGGER = logging.getLogger("sistema_fuzzy_mqtt")

TELEMETRY_TOPIC = os.getenv("MQTT_TELEMETRY_TOPIC", "grupo4/esp32/telemetry")
LEVEL_TOPIC = os.getenv("MQTT_LEVEL_TOPIC", "grupo4/fuzzy/level")
COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "grupo4/ac/command")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
PUBLISH_INTERVAL_SECONDS = float(os.getenv("MQTT_PUBLISH_INTERVAL", "60"))
SAMPLE_MAX_AGE_SECONDS = float(os.getenv("MQTT_SAMPLE_MAX_AGE", "150"))


def triangular(x: float, a: float, b: float, c: float) -> float:
    """Calcula a pertinência em uma função triangular."""
    if not all(isfinite(value) for value in (x, a, b, c)):
        raise ValueError("x e os parâmetros da função devem ser finitos")
    if a > b or b > c:
        raise ValueError("a, b e c devem estar em ordem crescente")
    if x < a or x > c:
        return 0.0
    if a == b and x == a:
        return 1.0
    if b == c and x == c:
        return 1.0
    if x == b:
        return 1.0
    if x < b:
        return (x - a) / (b - a)
    return (c - x) / (c - b)


def trapezoidal(x: float, a: float, b: float, c: float, d: float) -> float:
    """Calcula a pertinência em uma função trapezoidal."""
    if not all(isfinite(value) for value in (x, a, b, c, d)):
        raise ValueError("x e os parâmetros da função devem ser finitos")
    if a > b or b > c or c > d:
        raise ValueError("a, b, c e d devem estar em ordem crescente")
    if b == a and x <= b:
        return 1.0
    if c == d and x >= c:
        return 1.0
    if x < a or x > d:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c:
        return 1.0
    return (d - x) / (d - c)


def calculate_fuzzy_level(temperature: float, samples: int = 1000) -> dict[str, float]:
    """Fuzzifica a temperatura e calcula o centroide entre 0 e 100."""
    if not isfinite(temperature):
        raise ValueError("temperature deve ser um número finito")
    if samples < 2:
        raise ValueError("samples deve ser maior ou igual a 2")

    cold = triangular(temperature, 15.0, 15.0, 25.0)
    warm = triangular(temperature, 20.0, 26.0, 32.0)
    hot = trapezoidal(temperature, 24.0, 35.0, 45.0, 45.0)

    numerator = 0.0
    denominator = 0.0
    for index in range(samples):
        output = 100.0 * index / (samples - 1)
        weak = min(cold, triangular(output, 0.0, 0.0, 40.0))
        medium = min(warm, triangular(output, 20.0, 50.0, 80.0))
        strong = min(hot, trapezoidal(output, 60.0, 85.0, 100.0, 100.0))
        aggregated = max(weak, medium, strong)
        numerator += output * aggregated
        denominator += aggregated

    centroid = numerator / denominator if denominator else 0.0
    return {
        "temperature": temperature,
        "cold": cold,
        "warm": warm,
        "hot": hot,
        "weak_activation": cold,
        "medium_activation": warm,
        "strong_activation": hot,
        "level": centroid,
    }


def parse_telemetry(payload: bytes | str) -> dict[str, Any]:
    """Valida e normaliza o JSON publicado pela ESP32."""
    data: Any = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("telemetria deve ser um objeto JSON")

    node_id = data.get("node_id")
    temperature = data.get("temperature_c", data.get("temperature"))
    ts_ms = data.get("ts_ms")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("node_id deve ser uma string não vazia")
    if not isinstance(temperature, (int, float)):
        raise ValueError("temperature_c deve ser numérica")
    if not isinstance(ts_ms, int) or ts_ms < 0:
        raise ValueError("ts_ms deve ser um inteiro não negativo")
    if not isfinite(float(temperature)):
        raise ValueError("temperature_c deve ser finita")

    return {
        "node_id": node_id,
        "temperature_c": float(temperature),
        "ts_ms": ts_ms,
    }


class FuzzyMqttSystem:
    """Recebe dados, calcula o conforto térmico e publica os comandos."""

    def __init__(self) -> None:
        self.samples: dict[str, dict[str, Any]] = {}
        self.last_publish_ms = 0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if reason_code != 0:
            LOGGER.error("falha ao conectar ao broker: %s", reason_code)
            return
        client.subscribe(TELEMETRY_TOPIC, qos=1)
        LOGGER.info("conectado ao broker %s:%s", MQTT_HOST, MQTT_PORT)
        LOGGER.info("assinando %s com QoS 1", TELEMETRY_TOPIC)

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: Any,
    ) -> None:
        try:
            sample = parse_telemetry(message.payload)
            self.samples[sample["node_id"]] = sample
            LOGGER.info(
                "amostra recebida: nó=%s temperatura=%.2f ts_ms=%d",
                sample["node_id"],
                sample["temperature_c"],
                sample["ts_ms"],
            )
        except (json.JSONDecodeError, ValueError) as error:
            LOGGER.warning("telemetria descartada: %s", error)

    def valid_samples(self, now_ms: int) -> list[dict[str, Any]]:
        """Seleciona apenas leituras cuja idade é menor que 150 segundos."""
        valid = []
        max_age_ms = SAMPLE_MAX_AGE_SECONDS * 1000
        for sample in self.samples.values():
            age_ms = now_ms - sample["ts_ms"]
            if 0 <= age_ms <= max_age_ms:
                valid.append(sample)
        return valid

    def publish_if_due(self, now_ms: int | None = None) -> bool:
        """Calcula e publica o nível, quando necessário e seguro."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        interval_ms = PUBLISH_INTERVAL_SECONDS * 1000
        if now_ms - self.last_publish_ms < interval_ms:
            return False

        samples = self.valid_samples(now_ms)
        if not samples:
            LOGGER.warning(
                "nenhuma amostra válida na janela de %.0f s; mensagem omitida",
                SAMPLE_MAX_AGE_SECONDS,
            )
            return False

        average_temperature = sum(
            sample["temperature_c"] for sample in samples
        ) / len(samples)
        result = calculate_fuzzy_level(average_temperature)
        level = round(result["level"], 2)
        payload = json.dumps(
            {"ts_ms": now_ms, "level": level},
            separators=(",", ":"),
        )

        level_result = self.client.publish(
            LEVEL_TOPIC,
            payload,
            qos=1,
            retain=False,
        )
        level_result.wait_for_publish()
        self.client.publish(
            COMMAND_TOPIC,
            payload,
            qos=1,
            retain=False,
        )
        self.last_publish_ms = now_ms

        LOGGER.info(
            "publicado em %s e %s: %s",
            LEVEL_TOPIC,
            COMMAND_TOPIC,
            payload,
        )
        return True

    def run(self) -> None:
        """Inicia a ponte MQTT e o ciclo periódico."""
        self.client.connect(MQTT_HOST, MQTT_PORT)
        self.client.loop_start()
        try:
            while True:
                self.publish_if_due()
                time.sleep(1)
        except KeyboardInterrupt:
            LOGGER.info("encerrando o sistema")
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def run_single_calculation(temperature: float) -> None:
    """Permite testar o fuzzy sem iniciar o broker."""
    result = calculate_fuzzy_level(temperature)
    print(json.dumps({
        "temperature_c": temperature,
        "level": round(result["level"], 2),
        "cold": round(result["cold"], 6),
        "warm": round(result["warm"], 6),
        "hot": round(result["hot"], 6),
    }, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sistema fuzzy integrado ao MQTT"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="executa somente o cálculo e não inicia o broker",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.temperature is not None:
        run_single_calculation(args.temperature)
        return 0

    FuzzyMqttSystem().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
