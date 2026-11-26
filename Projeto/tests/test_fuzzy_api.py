import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import teste_API as api
from fuzzy_system.fuzzy import temperature_centroid


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
    def test_uses_latest_fresh_sample_per_node_by_ts_ms(self):
        now_ms = 1_000_000
        records = [
            {"node_id": "a", "temperature_c": 20.0, "ts_ms": now_ms - 20_000},
            {"node_id": "a", "temperature_c": 22.0, "ts_ms": now_ms - 10_000},
            {"node_id": "b", "temperature_c": 40.0, "ts_ms": now_ms - 150_001},
            {"node_id": "c", "temperature_c": 30.0, "ts_ms": now_ms + 1},
            {"node_id": "d", "temperature_c": 25.0, "ts_ms": now_ms - 30_000},
            {"node_id": "e", "temperature_c": 18.0, "ts_ms": now_ms - 150_000},
        ]
        table = SimpleNamespace(
            records=[SimpleNamespace(values=record) for record in records]
        )

        with patch.object(api.query_api, "query", return_value=[table]) as query:
            samples = api.ler_ultimas_temperaturas_validas(now_ms)

        self.assertEqual(dict(samples), {"a": 22.0, "d": 25.0, "e": 18.0})
        query_text = query.call_args.args[0]
        self.assertIn("range(start: -150s)", query_text)
        self.assertIn('"ts_ms"', query_text)
        self.assertIn("pivot(", query_text)

    def test_does_not_write_when_no_node_has_a_valid_sample(self):
        with (
            patch.object(api, "ler_ultimas_temperaturas_validas", return_value=[]),
            patch.object(api, "salvar_centroide") as save,
            patch.object(api, "publicar_centroide") as publish,
        ):
            result = api.processar_media_ultimos_150s(agora_ms=1_000_000)

        self.assertIsNone(result)
        save.assert_not_called()
        publish.assert_not_called()

    def test_writes_centroid_from_mean_of_latest_valid_nodes(self):
        with (
            patch.object(
                api,
                "ler_ultimas_temperaturas_validas",
                return_value=[("a", 20.0), ("b", 30.0)],
            ),
            patch.object(api, "salvar_centroide") as save,
            patch.object(api, "publicar_centroide") as publish,
        ):
            result = api.processar_media_ultimos_150s(agora_ms=1_000_000)

        self.assertEqual(result["temperatura_c"], 25.0)
        self.assertEqual(result["nos_validos"], 2)
        save.assert_called_once_with(25.0, api.calcular_centroide_fuzzy(25.0))
        publish.assert_called_once_with(api.calcular_centroide_fuzzy(25.0))

    def test_publishes_required_payload_with_qos_one_and_no_retain(self):
        with (
            patch.object(api.mqtt_publish, "single") as publish,
            patch.object(api, "time_ns", return_value=1_731_590_400_123_000_000),
        ):
            api.publicar_centroide(57.3)

        args, kwargs = publish.call_args
        self.assertEqual(kwargs["topic"], "/esp32/level")
        self.assertEqual(
            json.loads(kwargs["payload"]),
            {"ts_ms": 1_731_590_400_123, "level": 57.3},
        )
        self.assertEqual(kwargs["qos"], 1)
        self.assertFalse(kwargs["retain"])


if __name__ == "__main__":
    unittest.main()
