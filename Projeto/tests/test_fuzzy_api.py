import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import teste_API as legacy_api
from api.app import (
    LeituraManual,
    app,
    calcular_manual,
    health,
    processar_ultimo_do_influx,
)
from comunicacao import influxdb, mqtt
from config import settings
from fastapi import HTTPException
from fuzzy_system import controller
from fuzzy_system.fuzzy import temperature_centroid


class ApiTests(unittest.TestCase):
    def test_api_routes_are_registered_and_legacy_entry_point_works(self):
        routes = {
            route.path for route in app.routes if hasattr(route, "path")
        }
        self.assertTrue(
            {"/health", "/fuzzy/calcular", "/fuzzy/processar-recente"}.issubset(
                routes
            )
        )
        self.assertIs(legacy_api.app, app)

    def test_api_endpoint_results_keep_the_existing_contract(self):
        health_result = health()
        calculation = calcular_manual(
            LeituraManual(temperatura=15.0, salvar_no_influx=False)
        )

        self.assertEqual(health_result["status"], "online")
        self.assertEqual(health_result["source_bucket"], "wsn")
        self.assertEqual(health_result["bucket"], "teste")
        self.assertEqual(settings.INFLUX_NODE_TAG, "node")
        self.assertEqual(settings.INTERVALO_SEGUNDOS, 150)
        self.assertEqual(settings.JANELA_CONSULTA_SEGUNDOS, 150)
        self.assertEqual(calculation["centroid_percent"], 0.0)
        self.assertFalse(calculation["salvo_no_influx"])

    def test_recent_endpoint_returns_404_when_no_valid_samples_exist(self):
        with patch("api.app.processar_media_temperaturas_recentes", return_value=None):
            with self.assertRaises(HTTPException) as error:
                processar_ultimo_do_influx()

        self.assertEqual(error.exception.status_code, 404)

    def test_recent_endpoint_reports_mqtt_publish_failure(self):
        with patch(
            "api.app.processar_media_temperaturas_recentes",
            side_effect=mqtt.MQTTPublishError("publish failed"),
        ):
            with self.assertLogs("api.app", level="ERROR"):
                with self.assertRaises(HTTPException) as error:
                    processar_ultimo_do_influx()

        self.assertEqual(error.exception.status_code, 502)


class FuzzyControllerTests(unittest.TestCase):
    def test_centroid_is_non_decreasing_and_saturates(self):
        levels = [
            temperature_centroid(temperature / 10).centroid
            for temperature in range(150, 601)
        ]

        self.assertEqual(temperature_centroid(15.0).centroid, 0.0)
        self.assertEqual(temperature_centroid(45.0).centroid, 100.0)
        self.assertEqual(temperature_centroid(60.0).centroid, 100.0)
        self.assertTrue(
            all(previous <= current for previous, current in zip(levels, levels[1:]))
        )

    def test_level_rises_between_25_and_28_degrees(self):
        level_at_25 = temperature_centroid(25.0).centroid
        level_at_28 = temperature_centroid(28.0).centroid

        self.assertGreater(level_at_28, level_at_25)


class InfluxInputTests(unittest.TestCase):
    def test_environment_token_overrides_file_and_file_is_fallback(self):
        with TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "token.txt"
            token_file.write_text("file-token\n", encoding="utf-8")
            with (
                patch.object(settings, "INFLUX_TOKEN_FILE", token_file),
                patch.dict(os.environ, {"INFLUX_TOKEN": "environment-token"}),
            ):
                self.assertEqual(
                    settings._load_influx_token(),
                    "environment-token",
                )

            with (
                patch.object(settings, "INFLUX_TOKEN_FILE", token_file),
                patch.dict(os.environ, {"INFLUX_TOKEN": ""}),
            ):
                self.assertEqual(settings._load_influx_token(), "file-token")

    def test_queries_latest_recent_temperature_for_nodes_three_and_four(self):
        sample_time = datetime.now(timezone.utc)
        records = [
            {
                "node": "3",
                "topic": "wsn/central/sensores",
                "_value": 20.0,
                "_time": sample_time,
            },
            {
                "node": "4",
                "topic": "wsn/central/sensores",
                "_value": 22.0,
                "_time": sample_time,
            },
            {
                "node": "4",
                "topic": "wrong/topic",
                "_value": 99.0,
                "_time": sample_time,
            },
            {
                "node": "5",
                "topic": "wsn/central/sensores",
                "_value": 99.0,
                "_time": sample_time,
            },
        ]
        table = SimpleNamespace(
            records=[SimpleNamespace(values=record) for record in records]
        )

        with (
            patch.object(influxdb.query_api, "query", return_value=[table]) as query,
            redirect_stdout(StringIO()) as output,
        ):
            samples = influxdb.ler_ultimas_temperaturas_validas()

        self.assertEqual(
            {sample.node_id: sample.valor for sample in samples},
            {"3": 20.0, "4": 22.0},
        )
        query_text = query.call_args.args[0]
        self.assertIn('from(bucket: "wsn")', query_text)
        self.assertIn("range(start: -150s)", query_text)
        self.assertIn('r["_measurement"] == "sensores"', query_text)
        self.assertIn('r["_field"] == "temp"', query_text)
        self.assertIn('group(columns: ["node", "topic"])', query_text)
        self.assertIn("|> last()", query_text)
        self.assertIn("node", output.getvalue())
        self.assertIn("wrong/topic", output.getvalue())
        self.assertIn("value=20.0", output.getvalue())

    def test_falls_back_to_history_only_for_nodes_without_recent_temperature(self):
        recent_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        old_time = recent_time - timedelta(days=2)
        recent_table = SimpleNamespace(
            records=[
                SimpleNamespace(
                    values={
                        "node": "3",
                        "topic": "wsn/central/sensores",
                        "_value": 23.0,
                        "_time": recent_time,
                    }
                )
            ]
        )
        history_table = SimpleNamespace(
            records=[
                SimpleNamespace(
                    values={
                        "node": "4",
                        "topic": "wsn/central/sensores",
                        "_value": 29.0,
                        "_time": old_time,
                    }
                )
            ]
        )

        with (
            patch.object(
                influxdb.query_api,
                "query",
                side_effect=[[recent_table], [history_table]],
            ) as query,
            redirect_stdout(StringIO()),
        ):
            samples = influxdb.ler_ultimas_temperaturas_validas()

        self.assertEqual(
            [(sample.node_id, sample.valor) for sample in samples],
            [("3", 23.0), ("4", 29.0)],
        )
        self.assertFalse(samples[0].fallback)
        self.assertTrue(samples[1].fallback)
        self.assertEqual(samples[1].timestamp, old_time)
        self.assertIn("range(start: -150s)", query.call_args_list[0].args[0])
        history_query = query.call_args_list[1].args[0]
        self.assertIn("range(start: 1970-01-01T00:00:00Z)", history_query)
        self.assertIn('r["node"] == "4"', history_query)

    def test_reads_latest_stored_temperatures_when_recent_window_is_empty(self):
        old_time = datetime.now(timezone.utc) - timedelta(days=1)
        empty_table = SimpleNamespace(records=[])
        history_table = SimpleNamespace(
            records=[
                SimpleNamespace(
                    values={
                        "node": node_id,
                        "topic": "wsn/central/sensores",
                        "_value": value,
                        "_time": old_time,
                    }
                )
                for node_id, value in (("3", 24.0), ("4", 28.0))
            ]
        )

        with (
            patch.object(
                influxdb.query_api,
                "query",
                side_effect=[[empty_table], [history_table]],
            ) as query,
            redirect_stdout(StringIO()),
        ):
            samples = influxdb.ler_ultimas_temperaturas_validas()

        self.assertEqual(
            [(sample.node_id, sample.valor) for sample in samples],
            [("3", 24.0), ("4", 28.0)],
        )
        self.assertTrue(all(sample.fallback for sample in samples))
        self.assertTrue(
            all(
                "range(start: 1970-01-01T00:00:00Z)" in call.args[0]
                for call in query.call_args_list[1:]
            )
        )

    def test_reads_recent_humidity_for_both_nodes(self):
        sample_time = datetime.now(timezone.utc)
        records = [
            {
                "node": "3",
                "topic": "wsn/central/sensores",
                "_value": 62.0,
                "_time": sample_time,
            },
            {
                "node": "4",
                "topic": "wsn/central/sensores",
                "_value": 68.0,
                "_time": sample_time,
            },
        ]
        table = SimpleNamespace(
            records=[SimpleNamespace(values=record) for record in records]
        )

        with (
            patch.object(influxdb.query_api, "query", return_value=[table]) as query,
            redirect_stdout(StringIO()),
        ):
            samples = influxdb.ler_ultimas_umidades_validas()

        self.assertEqual(
            {sample.node_id: sample.valor for sample in samples},
            {"3": 62.0, "4": 68.0},
        )
        self.assertIn('r["_field"] == "umid"', query.call_args.args[0])

    def test_processes_temperature_from_a_single_available_node(self):
        sample_time = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
        with (
            patch.object(
                controller,
                "ler_ultimas_temperaturas_validas",
                return_value=[
                    influxdb.LeituraSensor("3", 22.0, sample_time, fallback=True)
                ],
            ),
            patch.object(controller, "ler_ultimas_umidades_validas", return_value=[]),
            patch.object(controller, "salvar_centroide") as save,
            patch.object(controller, "publicar_centroide") as publish,
            redirect_stdout(StringIO()),
        ):
            result = controller.processar_media_temperaturas_recentes()

        self.assertEqual(result["temperatura_c"], 22.0)
        self.assertEqual(result["nos_validos"], 1)
        self.assertTrue(result["usou_fallback"])
        save.assert_called_once_with(22.0, result["centroid_percent"])
        publish.assert_called_once_with(
            result["centroid_percent"],
            ts_ms=int(sample_time.timestamp() * 1000),
        )

    def test_does_not_process_when_no_temperature_has_ever_been_stored(self):
        with (
            patch.object(controller, "ler_ultimas_temperaturas_validas", return_value=[]),
            patch.object(controller, "ler_ultimas_umidades_validas", return_value=[]),
            patch.object(controller, "salvar_centroide") as save,
            patch.object(controller, "publicar_centroide") as publish,
            redirect_stdout(StringIO()),
        ):
            result = controller.processar_media_temperaturas_recentes()

        self.assertIsNone(result)
        save.assert_not_called()
        publish.assert_not_called()

    def test_writes_centroid_from_mean_of_latest_valid_nodes(self):
        node3_time = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
        node4_time = node3_time + timedelta(seconds=10)
        with (
            patch.object(
                controller,
                "ler_ultimas_temperaturas_validas",
                return_value=[
                    influxdb.LeituraSensor("3", 20.0, node3_time),
                    influxdb.LeituraSensor("4", 30.0, node4_time),
                ],
            ),
            patch.object(
                controller,
                "ler_ultimas_umidades_validas",
                return_value=[
                    influxdb.LeituraSensor("3", 62.0, node3_time),
                    influxdb.LeituraSensor("4", 68.0, node4_time),
                ],
            ),
            patch.object(controller, "salvar_centroide") as save,
            patch.object(controller, "publicar_centroide") as publish,
            redirect_stdout(StringIO()) as output,
        ):
            result = controller.processar_media_temperaturas_recentes()

        self.assertEqual(result["temperatura_c"], 25.0)
        self.assertEqual(result["nos_validos"], 2)
        save.assert_called_once_with(25.0, controller.calcular_centroide_fuzzy(25.0))
        self.assertFalse(result["usou_fallback"])
        publish.assert_called_once_with(
            result["centroid_percent"],
            ts_ms=int(node3_time.timestamp() * 1000),
        )
        self.assertIn("Média de umidade dos nós 3, 4: 65.00%", output.getvalue())

    def test_mqtt_publisher_uses_authenticated_server_level_payload(self):
        with (
            patch.object(mqtt, "MQTT_PASS", "unit-test-secret"),
            patch.object(mqtt.mqtt_publish, "single") as publish,
            redirect_stdout(StringIO()) as output,
        ):
            mqtt.publicar_centroide(40.33, ts_ms=1731590400123)

        arguments = publish.call_args.kwargs
        self.assertEqual(arguments["topic"], "/server/level")
        self.assertEqual(
            json.loads(arguments["payload"]),
            {"ts_ms": 1731590400123, "level": 40.33},
        )
        self.assertEqual(arguments["hostname"], "172.31.4.252")
        self.assertEqual(arguments["port"], 1883)
        self.assertEqual(
            arguments["auth"],
            {"username": "adm_mqtt", "password": "unit-test-secret"},
        )
        self.assertEqual(arguments["qos"], 1)
        self.assertFalse(arguments["retain"])
        self.assertIn(
            '[MQTT debug] Enviando para 172.31.4.252:1883 '
            'no tópico /server/level: {"ts_ms":1731590400123,"level":40.33}',
            output.getvalue(),
        )
        self.assertNotIn("unit-test-secret", output.getvalue())

    def test_mqtt_publisher_rejects_missing_password(self):
        with patch.object(mqtt, "MQTT_PASS", None):
            with self.assertRaises(mqtt.MQTTPublishError):
                mqtt.publicar_centroide(40.33)

    def test_mqtt_publisher_surfaces_broker_connection_failure(self):
        with (
            patch.object(mqtt, "MQTT_PASS", "unit-test-secret"),
            patch.object(
                mqtt.mqtt_publish,
                "single",
                side_effect=OSError("broker unavailable"),
            ),
        ):
            with self.assertRaises(mqtt.MQTTPublishError):
                mqtt.publicar_centroide(40.33)

    def test_writes_fuzzy_result_to_output_bucket(self):
        class FakePoint:
            def __init__(self, measurement):
                self.measurement = measurement
                self.tags = {}
                self.fields = {}
                self.timestamp = None

            def tag(self, key, value):
                self.tags[key] = value
                return self

            def field(self, key, value):
                self.fields[key] = value
                return self

            def time(self, timestamp, _precision):
                self.timestamp = timestamp
                return self

        with (
            patch.object(influxdb, "Point", FakePoint),
            patch.object(influxdb.write_api, "write") as write,
        ):
            influxdb.salvar_centroide(25.0, 57.3)

        self.assertEqual(
            [call.kwargs["bucket"] for call in write.call_args_list],
            ["teste", "wsn"],
        )
        self.assertEqual(
            write.call_args_list[0].kwargs["record"].measurement,
            "fuzzy_result",
        )
        self.assertEqual(
            write.call_args_list[0].kwargs["record"].fields["centroid_percent"],
            57.3,
        )
        self.assertEqual(
            write.call_args_list[1].kwargs["record"].measurement,
            "fuzzy_result",
        )
        self.assertEqual(
            write.call_args_list[1].kwargs["record"].fields,
            {"centroid_percent": 57.3},
        )

    def test_legacy_entry_point_exports_modular_app(self):
        self.assertIs(legacy_api.app, app)


if __name__ == "__main__":
    unittest.main()
