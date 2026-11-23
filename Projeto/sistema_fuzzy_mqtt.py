#!/usr/bin/env python3

import argparse
import json
import logging
import os
import time
from math import isfinite
from typing import Any


# Para caso falte bibliotecas
try:
    import paho.mqtt.client as mqtt
except ImportError as error:
    raise SystemExit(
        "Dependência ausente: instale com 'pip install -r requirements.txt'"
    ) from error

try:
    from influxdb_client import InfluxDBClient
except ImportError:
    InfluxDBClient = None


Porta_Mqtt = 1883
Host_Mqtt = "192.168.0.X" #exemplo
PUBLISH_INTERVAL_SECONDS = float(os.getenv("MQTT_PUBLISH_INTERVAL", "60"))
SAMPLE_MAX_AGE_SECONDS = float(os.getenv("MQTT_SAMPLE_MAX_AGE", "150"))



# Definições para o Mqtt
LOGGER = logging.getLogger("sistema_fuzzy_mqtt")

TELEMETRY_TOPIC = os.getenv("MQTT_TELEMETRY_TOPIC", "/esp32/telemetry")
LEVEL_TOPIC = os.getenv("MQTT_LEVEL_TOPIC", "/fuzzy/level")
COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "/esp32/command")

MQTT_HOST = os.getenv("MQTT_HOST", Host_Mqtt) # Definir IP local da sala
MQTT_PORT = int(os.getenv("MQTT_PORT", Porta_Mqtt))


#// Implementação do InfuxDb
INFLUX_URL = os.getenv("INFLUX_URL", "http://3.135.246.204:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")

INFLUX_ORG = os.getenv("INFLUX_ORG", "minha_org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "wsn")

INFLUX_MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "telemetry")

INFLUX_TEMPERATURE_FIELD = os.getenv(
    "INFLUX_TEMPERATURE_FIELD",
    "temperature_c",
)
INFLUX_HUMIDITY_FIELD = os.getenv(
    "INFLUX_HUMIDITY_FIELD",
    "humidity_percent",
)

INFLUX_NODE_TAG = os.getenv("INFLUX_NODE_TAG", "node_id")
INFLUX_POLL_SECONDS = float(os.getenv("INFLUX_POLL_SECONDS", "30"))


# Definições das areas fuzzy

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


# Montagem dos Jsons

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



# EM TESTES
class InfluxSource:
    """Consulta o último registro de temperatura de cada nó no InfluxDB."""

    def __init__(self) -> None:
        if not INFLUX_TOKEN:
            raise RuntimeError(
                "INFLUX_TOKEN não configurado; exporte o token por ambiente"
            )
        if InfluxDBClient is None:
            raise RuntimeError(
                "dependência ausente: instale influxdb-client"
            )
        self.client = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            timeout=10_000,
        )
        self.query_api = self.client.query_api()

    def close(self) -> None:
        self.client.close()

    def read_latest(self) -> dict[str, dict[str, Any]]:
        """Retorna uma amostra por nó usando a hora registrada pelo InfluxDB."""
        query = (
            'from(bucket: "' + INFLUX_BUCKET + '")\n'
            f'  |> range(start: -{int(SAMPLE_MAX_AGE_SECONDS)}s)\n'
            f'  |> filter(fn: (r) => r._measurement == "{INFLUX_MEASUREMENT}")\n'
            f'  |> filter(fn: (r) => r._field == "{INFLUX_TEMPERATURE_FIELD}" '
            f'|| r._field == "{INFLUX_HUMIDITY_FIELD}")\n'
            "  |> last()"
        )

        latest: dict[str, dict[str, Any]] = {}
        for table in self.query_api.query(query):
            for record in table.records:
                node_id = record.values.get(INFLUX_NODE_TAG)
                timestamp = record.get_time()
                field = record.get_field()
                value = record.get_value()
                if not isinstance(node_id, str) or timestamp is None:
                    continue
                if not isinstance(value, (int, float)):
                    continue

                ts_ms = int(timestamp.timestamp() * 1000)
                sample = latest.setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "temperature_c": None,
                        "humidity_percent": None,
                        "ts_ms": ts_ms,
                    },
                )
                if field == INFLUX_TEMPERATURE_FIELD:
                    sample["temperature_c"] = float(value)
                elif field == INFLUX_HUMIDITY_FIELD:
                    sample["humidity_percent"] = float(value)
                sample["ts_ms"] = max(sample["ts_ms"], ts_ms)

        return {
            node_id: sample
            for node_id, sample in latest.items()
            if sample["temperature_c"] is not None
        }


class FuzzyMqttSystem:
    """Recebe dados, calcula o conforto térmico e publica os comandos."""

    def __init__(self) -> None:
        self.samples: dict[str, dict[str, Any]] = {}
        self.influx_samples: dict[str, dict[str, Any]] = {}
        self.last_publish_ms = 0
        self.last_influx_poll_ms = 0
        self.influx_source: InfluxSource | None = None
        if INFLUX_TOKEN:
            try:
                self.influx_source = InfluxSource()
            except RuntimeError as error:
                LOGGER.error("InfluxDB não iniciado: %s", error)
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

    def valid_samples(
        self,
        now_ms: int,
        merged_samples: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Seleciona apenas leituras cuja idade é menor que 150 segundos."""
        valid = []
        max_age_ms = SAMPLE_MAX_AGE_SECONDS * 1000
        source = merged_samples if merged_samples is not None else self.samples
        for sample in source.values():
            age_ms = now_ms - sample["ts_ms"]
            if 0 <= age_ms <= max_age_ms:
                valid.append(sample)
        return valid

    def publish_if_due(self, now_ms: int | None = None) -> bool:
        """Calcula e publica o nível, quando necessário e seguro."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms

        if (
            self.influx_source is not None
            and now_ms - self.last_influx_poll_ms
            >= INFLUX_POLL_SECONDS * 1000
        ):
            try:
                self.influx_samples = self.influx_source.read_latest()
                self.last_influx_poll_ms = now_ms
                LOGGER.info(
                    "InfluxDB consultado: %d nó(s)",
                    len(self.influx_samples),
                )
            except Exception as error:
                LOGGER.warning("falha ao consultar o InfluxDB: %s", error)

        interval_ms = PUBLISH_INTERVAL_SECONDS * 1000
        if now_ms - self.last_publish_ms < interval_ms:
            return False

        merged_samples = dict(self.influx_samples)
        merged_samples.update(self.samples)
        samples = self.valid_samples(
            now_ms,
            merged_samples=merged_samples,
        )
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
            LOGGER.info("encerrando...")
        finally:
            if self.influx_source is not None:
                self.influx_source.close()
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


def test_influx_connection() -> int:
    """Consulta uma vez o InfluxDB e imprime os nós encontrados."""
    try:
        source = InfluxSource()
    except RuntimeError as error:
        LOGGER.error("%s", error)
        return 1

    try:
        samples = source.read_latest()
        print(json.dumps(samples, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        LOGGER.error("falha na consulta ao InfluxDB: %s", error)
        return 1
    finally:
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sistema fuzzy integrado ao MQTT"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="executa somente o cálculo e não inicia o broker",
    )
    parser.add_argument(
        "--test-influx",
        action="store_true",
        help="consulta o InfluxDB uma vez e encerra",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.temperature is not None:
        run_single_calculation(args.temperature)
        return 0
    if args.test_influx:
        return test_influx_connection()

    FuzzyMqttSystem().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
