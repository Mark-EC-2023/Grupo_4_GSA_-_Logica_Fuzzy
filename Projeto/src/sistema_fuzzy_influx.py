#!/usr/bin/env python3
"""Executa o processamento fuzzy usando o InfluxDB, sem MQTT."""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fuzzy_system.config import InfluxSettings
from fuzzy_system.csv_input import CSV_PATH, process_csv
from fuzzy_system.fuzzy import temperature_centroid
from fuzzy_system.influx import InfluxRepository
from fuzzy_system.service import process_history, process_latest

LOGGER = logging.getLogger("sistema_fuzzy_influx")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Tipo não serializável: {type(value).__name__}")


def _data_base(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    settings = InfluxSettings.from_env()
    parser = argparse.ArgumentParser(
        description="Lê temperaturas do InfluxDB e grava o centroide fuzzy."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--temperature",
        type=float,
        help="calcula o centroide localmente, sem acessar o InfluxDB",
    )
    mode.add_argument(
        "--test-influx",
        action="store_true",
        help="lê e imprime as temperaturas atuais, sem gravar resultados",
    )
    mode.add_argument(
        "--csv",
        nargs="?",
        const=CSV_PATH,
        type=Path,
        help="processa o CSV do EnergyPlus e grava centroides",
    )
    mode.add_argument(
        "--history",
        action="store_true",
        help="processa temperaturas já salvas no InfluxDB",
    )
    parser.add_argument(
        "--start",
        type=_data_base,
        help="início UTC do intervalo histórico (ISO 8601)",
    )
    parser.add_argument(
        "--stop",
        type=_data_base,
        help="fim UTC do intervalo histórico (ISO 8601, exclusivo)",
    )
    parser.add_argument(
        "--csv-dry-run",
        action="store_true",
        help="com --csv, calcula sem gravar no InfluxDB",
    )
    parser.add_argument(
        "--data-base",
        default="2026-11-23T00:00:00Z",
        help="data UTC base dos horários do CSV",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="repete a leitura do InfluxDB até o encerramento",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.poll_seconds,
        help="intervalo entre leituras no modo --watch, em segundos",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.interval <= 0:
        parser.error("--interval deve ser positivo")
    if args.csv_dry_run and args.csv is None:
        parser.error("--csv-dry-run requer --csv")
    if args.history and (args.start is None or args.stop is None):
        parser.error("--history requer --start e --stop")
    if args.history and args.start >= args.stop:
        parser.error("--start deve ser anterior a --stop")
    if args.history and args.watch:
        parser.error("--watch não pode ser combinado com --history")
    if not args.history and (args.start is not None or args.stop is not None):
        parser.error("--start e --stop requerem --history")

    if args.temperature is not None:
        result = temperature_centroid(args.temperature)
        print(json.dumps({
            "temperature_c": result.temperature,
            "centroid_percent": round(result.centroid, 2),
            "cold": round(result.cold, 6),
            "warm": round(result.warm, 6),
            "hot": round(result.hot, 6),
        }, ensure_ascii=False))
        return 0

    if args.csv is not None:
        try:
            results = process_csv(
                args.csv,
                settings=settings,
                write_to_influx=not args.csv_dry_run,
                data_base=_data_base(args.data_base),
            )
        except (OSError, RuntimeError, ValueError) as error:
            LOGGER.error("falha ao processar o CSV: %s", error)
            return 1
        print(json.dumps({
            "csv": str(args.csv),
            "linhas_processadas": len(results),
            "gravado_no_influxdb": not args.csv_dry_run,
            "output_bucket": (
                settings.output_bucket if not args.csv_dry_run else None
            ),
            "primeiro": results[0] if results else None,
            "ultimo": results[-1] if results else None,
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        repository = InfluxRepository(settings)
    except RuntimeError as error:
        LOGGER.error("%s", error)
        return 1

    try:
        if args.test_influx:
            samples = repository.read_latest()
            print(json.dumps([
                {
                    "node_id": sample.node_id,
                    "temperature_c": sample.temperature,
                    "time": sample.timestamp.isoformat(),
                }
                for sample in samples
            ], ensure_ascii=False, indent=2))
            return 0

        if args.history:
            report = process_history(
                settings,
                repository,
                args.start,
                args.stop,
            )
            print(json.dumps(report, ensure_ascii=False))
            return 0

        while True:
            report = process_latest(settings, repository)
            print(json.dumps(report, ensure_ascii=False, default=_json_default))
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOGGER.info("encerrando...")
        return 0
    except Exception as error:
        LOGGER.error("falha na leitura ou gravação do InfluxDB: %s", error)
        return 1
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
