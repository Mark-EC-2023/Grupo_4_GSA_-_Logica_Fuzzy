import pandas as pd

from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
csv_path = BASE_DIR / "epluszsz_resfriamento_influx.csv"

df = pd.read_csv(csv_path, comment="#")
print(df.head())
campos = [
    "cooling_temperature_c",
    "relative_humidity_pct",
]

linhas = [
    "#group,false,false,true,true,false,false,true,true",
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string",
    "#default,,,,,,,,",
    ",result,table,_start,_stop,_time,_value,_field,_measurement",
]

for idx_tabela, campo in enumerate(campos):
  for _, row in df.iterrows():
    linhas.append(
        f",,{idx_tabela},2026-11-17T00:00:00Z,2026-11-18T00:10:00Z,"
        f"{row['time']},{float(row[campo])},{campo},{row['measurement']}"
    )

with open(
    "epluszsz_resfriamento_influx_corrigido.csv",
    "w",
    encoding="utf-8",
    newline="\n",
) as f:
  f.write("\n".join(linhas) + "\n")

print(
    "Arquivo 'epluszsz_resfriamento_influx_corrigido.csv' gerado com"
    " sucesso!"
)