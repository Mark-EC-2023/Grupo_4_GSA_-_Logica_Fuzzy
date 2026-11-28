#!/usr/bin/env python3
"""
Análise de Sensibilidade Global (LHS + SRC/PCC) e Avaliação Fuzzy
alimentadas pelos dados climáticos reais de Quixeramobim-CE (.epw INMET 825860)
e pela simulação base do EnergyPlus (epluszsz.csv).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import qmc

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    HAS_INFLUX = True
except ImportError:
    HAS_INFLUX = False


# --- CAMINHOS DOS DADOS REAIS ---
ROOT_DIR = Path(__file__).resolve().parents[2]
DOCUMENTACAO_DIR = ROOT_DIR / "Documentação"
EPW_PATH = DOCUMENTACAO_DIR / "grupo2_instrumentacao" / "CE_Quixeramobim.epw"
EPLUSZSZ_PATH = (
    DOCUMENTACAO_DIR
    / "grupo2_instrumentacao"
    / "SALA_ARC"
    / "run"
    / "epluszsz.csv"
)
SIMULATION_OUTPUT_DIR = DOCUMENTACAO_DIR / "dados_simulacao"

# Períodos definidos no cabeçalho TYPICAL/EXTREME PERIODS do .epw de Quixeramobim
PERIODOS_QUIXERAMOBIM = {
    "ano_completo": None,
    "verao_extremo_max": ((1, 4), (1, 10)),       # Summer - Week Nearest Max Temp (4/1 a 10/1)
    "estacao_seca_tipica": ((1, 17), (1, 23)),    # Dry Season - Typical (17/1 a 23/1)
    "estacao_chuvosa_tipica": ((4, 5), (4, 11)),  # Wet Season - Typical (5/4 a 11/4)
    "inverno_extremo_min": ((7, 3), (7, 9)),      # Winter - Nearest Min Temp (3/7 a 9/7)
}


# --- 1. LEITURA DO ARQUIVO CLIMÁTICO .EPW (QUIXERAMOBIM - INMET 825860) ---

def carregar_dados_epw(
    epw_path: Path = EPW_PATH,
    periodo: str = "ano_completo",
) -> tuple[dict[str, str | float], pd.DataFrame]:
    """Lê metadados e séries horárias reais de um arquivo EnergyPlus Weather (.epw)."""
    if not epw_path.is_file():
        raise FileNotFoundError(f"Arquivo .epw não encontrado em: {epw_path}")

    with epw_path.open("r", encoding="utf-8", errors="replace") as f:
        linhas_cabecalho = [next(f).strip() for _ in range(8)]

    loc = linhas_cabecalho[0].split(",")
    metadados = {
        "cidade": loc[1],
        "estado": loc[2],
        "pais": loc[3],
        "fonte": loc[4],
        "wmo": loc[5],
        "latitude": float(loc[6]),
        "longitude": float(loc[7]),
        "fuso_horario": float(loc[8]),
        "altitude_m": float(loc[9]),
    }

    # Colunas oficiais do formato .epw (após as 8 linhas de cabeçalho)
    colunas_map = {
        0: "ano",
        1: "mes",
        2: "dia",
        3: "hora",
        4: "minuto",
        6: "temp_bulbo_seco_c",
        7: "temp_ponto_orvalho_c",
        8: "umidade_relativa_pct",
        9: "pressao_atm_pa",
        13: "radiacao_global_wh_m2",
        14: "radiacao_direta_normal_wh_m2",
        15: "radiacao_difusa_wh_m2",
        20: "direcao_vento_graus",
        21: "velocidade_vento_ms",
    }

    df_raw = pd.read_csv(epw_path, skiprows=8, header=None)
    df = df_raw[list(colunas_map.keys())].rename(columns=colunas_map).copy()

    # Trata códigos de dado ausente padrão do EnergyPlus (999, 9999, 99.9)
    df.loc[df["temp_bulbo_seco_c"] >= 99.0, "temp_bulbo_seco_c"] = np.nan
    df.loc[df["umidade_relativa_pct"] > 100.0, "umidade_relativa_pct"] = np.nan
    df.loc[df["radiacao_global_wh_m2"] >= 9999, "radiacao_global_wh_m2"] = np.nan
    df.loc[df["velocidade_vento_ms"] >= 999.0, "velocidade_vento_ms"] = np.nan
    df = df.interpolate(method="linear").bfill().ffill()

    # Constrói timestamp UTC-3 (Quixeramobim), tratando hora 24 como 00:00 do dia seguinte
    tz_local = timezone(timedelta(hours=int(metadados["fuso_horario"])))
    timestamps = []
    for row in df.itertuples(index=False):
        base = datetime(int(row.ano), int(row.mes), int(row.dia), tzinfo=tz_local)
        if int(row.hora) == 24:
            timestamps.append(base + timedelta(days=1))
        else:
            timestamps.append(base.replace(hour=int(row.hora), minute=int(row.minuto)))
    df["timestamp"] = timestamps

    # Filtra pelo período típico/extremo se solicitado
    faixa = PERIODOS_QUIXERAMOBIM.get(periodo)
    if faixa is not None:
        (m_ini, d_ini), (m_fim, d_fim) = faixa
        chave_md = df["mes"] * 100 + df["dia"]
        ini_md = m_ini * 100 + d_ini
        fim_md = m_fim * 100 + d_fim
        if ini_md <= fim_md:
            df = df[(chave_md >= ini_md) & (chave_md <= fim_md)].copy()
        else:
            df = df[(chave_md >= ini_md) | (chave_md <= fim_md)].copy()

    return metadados, df.reset_index(drop=True)


def carregar_simulacao_base_zsz(csv_path: Path = EPLUSZSZ_PATH) -> pd.DataFrame:
    """Lê a simulação base de dia de projeto (epluszsz.csv) removendo linhas Peak."""
    if not csv_path.is_file():
        return pd.DataFrame()

    df = pd.read_csv(csv_path)
    df = df[df["Time"].astype(str).str.match(r"^\d{2}:\d{2}:\d{2}$")].copy()
    col_temp = "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:Cooling Zone Temperature [C]"
    col_carga = "THERMAL ZONE 1:QUIXERAMOBIM SUMMER DESIGN DAY:Des Sens Cool Load [W]"
    df["temp_zona_base_c"] = pd.to_numeric(df[col_temp], errors="coerce")
    df["carga_sensivel_base_w"] = pd.to_numeric(df[col_carga], errors="coerce")
    return df.dropna(subset=["temp_zona_base_c"]).reset_index(drop=True)


# --- 2. CONTROLADOR FUZZY MONOTÔNICO ---

def ombro_esquerdo(x: float, a: float, b: float) -> float:
    if x <= a:
        return 1.0
    if x >= b:
        return 0.0
    return (b - x) / (b - a)


def ombro_direito(x: float, a: float, b: float) -> float:
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    return (x - a) / (b - a)


def triangular(x: float, a: float, b: float, c: float) -> float:
    if x <= a or x >= c:
        return 0.0
    if x == b:
        return 1.0
    if x < b:
        return (x - a) / (b - a)
    return (c - x) / (c - b)


def calcular_nivel_fuzzy(temperatura: float, amostras: int = 500) -> float:
    """Calcula centroide monotônico (0 a 100) com pico de 'warm' em 26 °C."""
    cold = ombro_esquerdo(temperatura, 18.0, 25.0)
    warm = triangular(temperatura, 21.0, 26.0, 31.0)
    hot = ombro_direito(temperatura, 26.0, 36.0)

    num = 0.0
    den = 0.0
    for i in range(amostras):
        u = 100.0 * i / (amostras - 1)
        fraco = min(cold, ombro_esquerdo(u, 10.0, 40.0))
        medio = min(warm, triangular(u, 25.0, 50.0, 75.0))
        forte = min(hot, ombro_direito(u, 60.0, 90.0))
        agregado = max(fraco, medio, forte)
        num += u * agregado
        den += agregado

    return round(num / den if den > 0 else 100.0, 2)


# --- 3. AMOSTRAGEM LHS (200 CENÁRIOS) COM LIMITES REAIS DE QUIXERAMOBIM ---

def gerar_matriz_lhs_real(df_epw: pd.DataFrame, n_simulacoes: int = 200, seed: int = 42) -> pd.DataFrame:
    """
    Constrói 200 cenários estocásticos via Hipercubo Latino (LHS) usando os
    percentis reais (P5-P95) medidos no .epw de Quixeramobim + parâmetros da sala.
    """
    horas_diurnas = df_epw[df_epw["radiacao_global_wh_m2"] > 0]

    parametros_reais = {
        # Faixas extraídas diretamente do arquivo climático INMET 825860 (Quixeramobim)
        "temp_externa_c": (
            float(df_epw["temp_bulbo_seco_c"].quantile(0.05)),
            float(df_epw["temp_bulbo_seco_c"].quantile(0.95)),
        ),
        "radiacao_global_wh_m2": (
            float(horas_diurnas["radiacao_global_wh_m2"].quantile(0.10)),
            float(horas_diurnas["radiacao_global_wh_m2"].quantile(0.95)),
        ),
        "velocidade_vento_ms": (
            float(df_epw["velocidade_vento_ms"].quantile(0.05)),
            float(df_epw["velocidade_vento_ms"].quantile(0.95)),
        ),
        "umidade_relativa_pct": (
            float(df_epw["umidade_relativa_pct"].quantile(0.05)),
            float(df_epw["umidade_relativa_pct"].quantile(0.95)),
        ),
        # Parâmetros construtivos / operacionais da envoltória (Grupo 2 / Grupo 4)
        "infiltracao_ach": (0.20, 1.50),
        "carga_interna_w_m2": (8.0, 30.0),
    }

    nomes = list(parametros_reais.keys())
    lim_inf = [v[0] for v in parametros_reais.values()]
    lim_sup = [v[1] for v in parametros_reais.values()]

    sampler = qmc.LatinHypercube(d=len(nomes), seed=seed)
    amostras = qmc.scale(sampler.random(n=n_simulacoes), lim_inf, lim_sup)

    df_lhs = pd.DataFrame(amostras, columns=nomes)
    df_lhs.insert(0, "simulacao_id", range(1, n_simulacoes + 1))
    return df_lhs


# --- 4. AVALIAÇÃO TÉRMICA SOBRE OS DADOS REAIS & ÍNDICES GSA (SRC / PCC) ---

def avaliar_cenarios_com_dados_reais(
    df_lhs: pd.DataFrame,
    df_epw: pd.DataFrame,
    df_zsz: pd.DataFrame,
) -> pd.DataFrame:
    """
    Para cada um dos 200 cenários LHS, seleciona os registros horários reais
    mais próximos no .epw de Quixeramobim e calcula a temperatura operativa da zona
    calibrada pela relação térmica observada no epluszsz.csv.
    """
    # Ganho térmico médio de referência extraído do epluszsz.csv (ou fallback físico)
    if not df_zsz.empty:
        temp_zona_ref = float(df_zsz["temp_zona_base_c"].mean())
        carga_ref_w = float(df_zsz["carga_sensivel_base_w"].mean())
    else:
        temp_zona_ref = 24.5
        carga_ref_w = 1800.0

    temp_ext_media_epw = float(df_epw["temp_bulbo_seco_c"].mean())
    rad_media_epw = float(df_epw["radiacao_global_wh_m2"].mean())

    resultados = []
    for row in df_lhs.itertuples(index=False):
        # Localiza a hora real mais próxima no .epw de Quixeramobim para ancorar a observação
        dist = (
            ((df_epw["temp_bulbo_seco_c"] - row.temp_externa_c) / df_epw["temp_bulbo_seco_c"].std()) ** 2
            + ((df_epw["radiacao_global_wh_m2"] - row.radiacao_global_wh_m2) / df_epw["radiacao_global_wh_m2"].std()) ** 2
        )
        idx_real = int(dist.idxmin())
        obs_real = df_epw.loc[idx_real]

        # Balanço térmico estacionário acoplado aos dados reais de Quixeramobim
        delta_t_ext = obs_real["temp_bulbo_seco_c"] - temp_ext_media_epw
        ganho_solar = 0.0045 * (obs_real["radiacao_global_wh_m2"] - rad_media_epw)
        ganho_interno = 0.085 * row.carga_interna_w_m2
        troca_infiltracao = 0.55 * row.infiltracao_ach * (obs_real["temp_bulbo_seco_c"] - temp_zona_ref)
        resfriamento_convectivo = -0.18 * obs_real["velocidade_vento_ms"]

        temp_interna_sim = (
            temp_zona_ref
            + 0.42 * delta_t_ext
            + ganho_solar
            + ganho_interno
            + troca_infiltracao
            + resfriamento_convectivo
        )
        nivel_fuzzy = calcular_nivel_fuzzy(temp_interna_sim)

        resultados.append({
            "simulacao_id": row.simulacao_id,
            "timestamp_epw": obs_real["timestamp"],
            "temp_externa_epw_c": round(float(obs_real["temp_bulbo_seco_c"]), 2),
            "radiacao_epw_wh_m2": round(float(obs_real["radiacao_global_wh_m2"]), 1),
            "vento_epw_ms": round(float(obs_real["velocidade_vento_ms"]), 2),
            "umidade_epw_pct": round(float(obs_real["umidade_relativa_pct"]), 1),
            "carga_ref_zsz_w": round(carga_ref_w, 1),
            "temp_interna_zona_c": round(float(temp_interna_sim), 3),
            "fuzzy_level_pct": nivel_fuzzy,
        })

    return pd.DataFrame(resultados)


def calcular_src_pcc(X_df: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    """Calcula os índices de sensibilidade global SRC e PCC."""
    X_z = (X_df - X_df.mean()) / X_df.std(ddof=1)
    y_z = (y - y.mean()) / y.std(ddof=1)
    src, _, _, _ = np.linalg.lstsq(X_z.values, y_z, rcond=None)

    pcc = []
    cols = list(X_df.columns)
    for col in cols:
        outras = [c for c in cols if c != col]
        X_outras = np.column_stack([np.ones(len(X_df)), X_df[outras].values])
        res_x = X_df[col].values - X_outras @ np.linalg.lstsq(X_outras, X_df[col].values, rcond=None)[0]
        res_y = y - X_outras @ np.linalg.lstsq(X_outras, y, rcond=None)[0]
        r, _ = stats.pearsonr(res_x, res_y)
        pcc.append(r)

    return pd.DataFrame({
        "Parametro": cols,
        "SRC": np.round(src, 4),
        "PCC": np.round(pcc, 4),
        "Importancia_Abs": np.round(np.abs(src), 4),
    }).sort_values(by="Importancia_Abs", ascending=False).reset_index(drop=True)


# --- 5. PERSISTÊNCIA NO INFLUXDB (REGRA DE CUSTÓDIA DE DADOS) ---

def salvar_resultados_influxdb(df_res: pd.DataFrame, metadados: dict) -> None:
    """Persiste as 200 simulações LHS/Fuzzy no InfluxDB caso INFLUX_TOKEN esteja ativo."""
    token = os.getenv("INFLUX_TOKEN")
    if not HAS_INFLUX or not token:
        print("[Aviso] INFLUX_TOKEN ausente; pulando gravação no InfluxDB.")
        return

    url = os.getenv("INFLUX_URL", "http://3.135.246.204:8086")
    org = os.getenv("INFLUX_ORG", "minha_org")
    bucket = "teste"

    client = InfluxDBClient(url=url, token=token, org=org)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    base_now = datetime.now(timezone.utc)
    pontos = []
    for i, row in df_res.iterrows():
        ponto = (
            Point("gsa_lhs_simulacao")
            .tag("grupo", "grupo4")
            .tag("estacao_wmo", str(metadados["wmo"]))
            .tag("cidade", str(metadados["cidade"]))
            .field("simulacao_id", int(row["simulacao_id"]))
            .field("temp_externa_c", float(row["temp_externa_epw_c"]))
            .field("radiacao_global_wh_m2", float(row["radiacao_epw_wh_m2"]))
            .field("temp_interna_zona_c", float(row["temp_interna_zona_c"]))
            .field("fuzzy_level_pct", float(row["fuzzy_level_pct"]))
            .time(base_now + timedelta(seconds=int(i)), WritePrecision.S)
        )
        pontos.append(ponto)

    write_api.write(bucket=bucket, org=org, record=pontos)
    client.close()
    print(f"[InfluxDB] {len(pontos)} rodadas LHS gravadas em '{bucket}.gsa_lhs_simulacao'.")


if __name__ == "__main__":
    # 1. Carrega os dados reais de Quixeramobim (.epw INMET 825860) e o epluszsz.csv
    meta, df_epw = carregar_dados_epw(EPW_PATH, periodo="ano_completo")
    df_zsz = carregar_simulacao_base_zsz(EPLUSZSZ_PATH)

    print(f"Estação carregada: {meta['cidade']}-{meta['estado']} (WMO #{meta['wmo']}) | Registros .epw: {len(df_epw)}")
    print(f"Faixa real T_bs (.epw): {df_epw['temp_bulbo_seco_c'].min():.1f} °C a {df_epw['temp_bulbo_seco_c'].max():.1f} °C")

    # 2. Gera 200 amostras LHS com os limites reais extraídos do .epw
    df_lhs = gerar_matriz_lhs_real(df_epw, n_simulacoes=200, seed=42)

    # 3. Avalia os 200 cenários acoplados aos dados reais e calcula o Fuzzy
    df_resultados = avaliar_cenarios_com_dados_reais(df_lhs, df_epw, df_zsz)
    tabela_simulacoes = df_lhs.merge(
        df_resultados,
        on="simulacao_id",
        validate="one_to_one",
    )
    SIMULATION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    caminho_tabela = SIMULATION_OUTPUT_DIR / "simulacoes_lhs_200.csv"
    tabela_simulacoes.to_csv(caminho_tabela, index=False, encoding="utf-8")
    print("\n--- Tabela completa das 200 simulações de Quixeramobim ---")
    print(tabela_simulacoes.to_string(index=False))
    print(f"\nTabela CSV salva em: {caminho_tabela}")

    # 4. Calcula os índices de sensibilidade global SRC e PCC
    entradas = df_lhs.drop(columns=["simulacao_id"])
    saida_y = df_resultados["temp_interna_zona_c"].values
    df_gsa = calcular_src_pcc(entradas, saida_y)
    print("\n--- Ranking de Sensibilidade Global (SRC e PCC) ---")
    print(df_gsa.to_string(index=False))

    # 5. Grava no InfluxDB (se INFLUX_TOKEN estiver exportado)
    salvar_resultados_influxdb(df_resultados, meta)