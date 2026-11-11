#!/usr/bin/env python3
"""Lê uma temperatura e imprime o centroide do controlador fuzzy."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fuzzy_controller import temperature_centroid


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calcula o centroide do nível de atuação para uma temperatura."
    )
    parser.add_argument(
        "temperature",
        nargs="?",
        type=float,
        help="temperatura em graus Celsius; sem este valor, será lida do terminal",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("centroide.json"),
        help="arquivo JSON do payload MQTT (padrão: centroide.json)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="imprime somente o JSON, adequado para publicação MQTT",
    )
    args = parser.parse_args()

    temperature = args.temperature
    if temperature is None:
        try:
            temperature = float(input("Temperatura (°C): "))
        except ValueError:
            parser.error("informe uma temperatura numérica")

    try:
        result = temperature_centroid(temperature)
    except ValueError as error:
        parser.error(str(error))

    payload = json.dumps(result.to_mqtt_payload(), ensure_ascii=False, indent=2)
    try:
        args.output.write_text(f"{payload}\n", encoding="utf-8")
    except OSError as error:
        parser.error(f"não foi possível gravar o JSON em {args.output}: {error}")

    if args.json_only:
        print(payload)
        return 0

    print(f"Temperatura: {result.temperature:.2f} °C")
    print(
        "Pertinências [frio, morno, quente]: "
        f"[{result.cold:.3f}, {result.warm:.3f}, {result.hot:.3f}]"
    )
    print(f"Centroide: {result.centroid:.2f}%")
    print(f"Payload MQTT salvo em: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
