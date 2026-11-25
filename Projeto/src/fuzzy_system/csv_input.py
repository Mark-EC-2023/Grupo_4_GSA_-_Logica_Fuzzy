"""Processamento opcional do CSV do EnergyPlus."""

import csv
import re
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from .config import InfluxSettings
from .fuzzy import temperature_centroid
from .influx import InfluxRepository

CSV_PATH = Path(__file__).resolve().parents[1].parent / "Data" / "epluszsz.csv"
CSV_TEMPERATURE_COLUMN = (
    "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:"
    "Cooling Zone Temperature [C]"
)


def csv_timestamp(time_value: str, data_base: datetime) -> datetime:
    """Converte HH:MM:SS do CSV para timestamp UTC."""
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_value):
        raise ValueError(f"horário inválido: {time_value}")
    hour, minute, second = map(int, time_value.split(":"))
    if hour == 24 and minute == 0 and second == 0:
        return data_base + timedelta(days=1)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"horário fora da faixa: {time_value}")
    return data_base.replace(
        hour=hour,
        minute=minute,
        second=second,
        microsecond=0,
    )


def process_csv(
    csv_path: Path,
    settings: InfluxSettings,
    write_to_influx: bool,
    data_base: datetime,
) -> list[dict[str, Any]]:
    """Calcula os centroides do CSV e, se pedido, envia ao bucket de saída."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"Arquivo CSV não encontrado: {csv_path}")

    repository = InfluxRepository(settings) if write_to_influx else None
    results: list[dict[str, Any]] = []
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if CSV_TEMPERATURE_COLUMN not in (reader.fieldnames or []):
                raise ValueError(
                    f"Coluna ausente no CSV: {CSV_TEMPERATURE_COLUMN}"
                )

            for line_number, row in enumerate(reader, start=2):
                time_value = row.get("Time", "")
                if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_value):
                    continue
                try:
                    temperature = float(row[CSV_TEMPERATURE_COLUMN])
                    timestamp = csv_timestamp(time_value, data_base)
                except (TypeError, ValueError, KeyError) as error:
                    raise ValueError(
                        f"Erro na linha {line_number} do CSV: {error}"
                    ) from error
                if not isfinite(temperature):
                    raise ValueError(
                        f"Temperatura inválida na linha {line_number} do CSV"
                    )

                result = temperature_centroid(temperature)
                if repository is not None:
                    repository.write_centroid(timestamp, result, 1)
                results.append({
                    "time": timestamp.isoformat(),
                    "temperature_c": result.temperature,
                    "centroid_percent": round(result.centroid, 2),
                })
    finally:
        if repository is not None:
            repository.close()

    return results
