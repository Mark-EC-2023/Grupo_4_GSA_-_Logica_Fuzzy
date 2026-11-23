#!/usr/bin/env python3
"""Gera e, opcionalmente, envia dados do epluszsz.csv ao InfluxDB."""

import argparse
import csv
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


INFLUX_URL = os.getenv("INFLUX_URL", "http://3.135.246.204:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "minha_org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "teste")

CSV_PATH = Path(__file__).resolve().parents[1] / "Data" / "epluszsz.csv"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "Data" / "epluszsz.lineprotocol"
CSV_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "Data" / "epluszsz_import.csv"

COLUNA_TEMPERATURA = (
    "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:"
    "Cooling Zone Temperature [C]"
)
COLUNA_UMIDADE = (
    "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:"
    "Cooling Zone Relative Humidity [%]"
)
COLUNA_CARGA = (
    "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:"
    "Des Sens Cool Load [W]"
)
COLUNA_VAZAO = (
    "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:"
    "Des Cool Mass Flow [kg/s]"
)


def converter_timestamp(hora: str, data_base: datetime) -> datetime:
    """Converte HH:MM:SS em timestamp UTC; 24:00:00 avança um dia."""
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", hora):
        raise ValueError(f"horário inválido: {hora}")
    horas, minutos, segundos = map(int, hora.split(":"))
    if horas == 24 and minutos == 0 and segundos == 0:
        return data_base + timedelta(days=1)
    if horas > 23 or minutos > 59 or segundos > 59:
        raise ValueError(f"horário fora da faixa: {hora}")
    return data_base.replace(
        hour=horas,
        minute=minutos,
        second=segundos,
        microsecond=0,
    )


def formatar_decimal(valor: str) -> str:
    """Valida valores científicos/decimais antes de inserir no line protocol."""
    numero = float(valor)
    if numero != numero or numero in (float("inf"), float("-inf")):
        raise ValueError(f"valor numérico inválido: {valor}")
    return format(numero, ".12g")


def escape_tag(valor: str) -> str:
    """Escapa espaços, vírgulas e sinais iguais usados em tags InfluxDB."""
    return valor.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def gerar_linha(row: dict[str, str], data_base: datetime) -> str:
    """Cria uma linha válida no formato line protocol."""
    timestamp = converter_timestamp(row["Time"], data_base)
    timestamp_ns = int(timestamp.timestamp()) * 1_000_000_000

    temperatura = formatar_decimal(row[COLUNA_TEMPERATURA])
    umidade = formatar_decimal(row[COLUNA_UMIDADE])
    carga = formatar_decimal(row[COLUNA_CARGA])
    vazao = formatar_decimal(row[COLUNA_VAZAO])

    tags = (
        f"origem={escape_tag('grupo2_eplus')}"
        f",cenario={escape_tag('quixeramobim_summer')}"
    )
    fields = (
        f"temp_resfriamento_c={temperatura}"
        f",umid_resfriamento_pct={umidade}"
        f",carga_sensivel_cool_w={carga}"
        f",vazao_cool_kgs={vazao}"
    )
    return f"simulacao_zona1,{tags} {fields} {timestamp_ns}"


def gerar_arquivo(
    csv_path: Path = CSV_PATH,
    output_path: Path = OUTPUT_PATH,
    data_base: datetime | None = None,
) -> tuple[Path, int]:
    """Lê o CSV EnergyPlus e cria o arquivo InfluxDB line protocol."""
    if not csv_path.is_file():
        raise SystemExit(f"Arquivo CSV não encontrado: {csv_path}")

    if data_base is None:
        data_base = datetime(2026, 11, 23, tzinfo=timezone.utc)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        colunas = set(reader.fieldnames or [])
        obrigatorias = {
            "Time",
            COLUNA_TEMPERATURA,
            COLUNA_UMIDADE,
            COLUNA_CARGA,
            COLUNA_VAZAO,
        }
        ausentes = sorted(obrigatorias - colunas)
        if ausentes:
            raise SystemExit(
                "Colunas ausentes no CSV:\n- " + "\n- ".join(ausentes)
            )

        linhas = []
        for numero_linha, row in enumerate(reader, start=2):
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", row.get("Time", "")):
                continue
            try:
                linhas.append(gerar_linha(row, data_base))
            except (ValueError, KeyError) as error:
                raise SystemExit(
                    f"Erro na linha {numero_linha} do CSV: {error}"
                ) from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(linhas) + ("\n" if linhas else ""),
        encoding="utf-8",
    )
    return output_path, len(linhas)


def gerar_csv_importacao(
    csv_path: Path = CSV_PATH,
    output_path: Path = CSV_OUTPUT_PATH,
    data_base: datetime | None = None,
) -> tuple[Path, int]:
    """Cria um CSV wide anotado para upload na interface do InfluxDB."""
    if not csv_path.is_file():
        raise SystemExit(f"Arquivo CSV não encontrado: {csv_path}")

    if data_base is None:
        data_base = datetime(2026, 11, 23, tzinfo=timezone.utc)

    cabecalho = (
        "_measurement",
        "origem",
        "cenario",
        "_time",
        "temp_resfriamento_c",
        "umid_resfriamento_pct",
        "carga_sensivel_cool_w",
        "vazao_cool_kgs",
    )
    linhas: list[list[str]] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        colunas = set(reader.fieldnames or [])
        obrigatorias = {
            "Time",
            COLUNA_TEMPERATURA,
            COLUNA_UMIDADE,
            COLUNA_CARGA,
            COLUNA_VAZAO,
        }
        ausentes = sorted(obrigatorias - colunas)
        if ausentes:
            raise SystemExit(
                "Colunas ausentes no CSV:\n- " + "\n- ".join(ausentes)
            )

        for numero_linha, row in enumerate(reader, start=2):
            hora = row.get("Time", "")
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", hora):
                continue
            try:
                timestamp = converter_timestamp(hora, data_base)
                timestamp_iso = timestamp.isoformat().replace("+00:00", "Z")
                linhas.append([
                    "simulacao_zona1",
                    "grupo2_eplus",
                    "quixeramobim_summer",
                    timestamp_iso,
                    formatar_decimal(row[COLUNA_TEMPERATURA]),
                    formatar_decimal(row[COLUNA_UMIDADE]),
                    formatar_decimal(row[COLUNA_CARGA]),
                    formatar_decimal(row[COLUNA_VAZAO]),
                ])
            except (ValueError, KeyError) as error:
                raise SystemExit(
                    f"Erro na linha {numero_linha} do CSV: {error}"
                ) from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file, lineterminator="\n")
        csv_file.write(
            "#datatype measurement,tag,tag,dateTime:RFC3339,"
            "double,double,double,double\n"
        )
        writer.writerow(cabecalho)
        writer.writerows(linhas)

    return output_path, len(linhas)


def enviar_arquivo(output_path: Path) -> int:
    """Envia o arquivo diretamente ao bucket usando influxdb-client."""
    if not INFLUX_TOKEN:
        raise SystemExit(
            "INFLUX_TOKEN não configurado. "
            "Defina a variável antes de usar --send."
        )

    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS
    except ImportError as error:
        raise SystemExit(
            "Instale as dependências: pip install -r requirements.txt"
        ) from error

    client = InfluxDBClient(
        url=INFLUX_URL,
        token=INFLUX_TOKEN,
        org=INFLUX_ORG,
    )
    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(
            bucket=INFLUX_BUCKET,
            org=INFLUX_ORG,
            record=output_path.read_text(encoding="utf-8"),
        )
        return sum(
            1
            for line in output_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera line protocol a partir de epluszsz.csv"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="também envia o arquivo ao InfluxDB",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help="arquivo line protocol de saída",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=CSV_OUTPUT_PATH,
        help="arquivo CSV anotado para importação pela interface",
    )
    args = parser.parse_args()

    output_path, quantidade = gerar_arquivo(output_path=args.output)
    csv_output, quantidade_csv = gerar_csv_importacao(
        output_path=args.csv_output
    )
    print(f"Arquivo gerado: {output_path}")
    print(f"Linhas válidas: {quantidade}")
    print(f"CSV para upload: {csv_output}")
    print(f"Linhas CSV válidas: {quantidade_csv}")

    if args.send:
        enviados = enviar_arquivo(output_path)
        print(
            f"Inseridos {enviados} registros no bucket "
            f"'{INFLUX_BUCKET}'."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
