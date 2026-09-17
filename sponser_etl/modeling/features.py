"""Construcción del panel producto x fecha con features de calendario, eventos y rezagos.

Compartido por train.py (panel histórico) y forecast.py (panel extendido día a día
durante el pronóstico recursivo). Es código único (no versionado por archivo) —
lo que varía entre versiones del modelo queda registrado en versions/vN/config.json.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # sponser_etl/
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")
EVENTOS_PATH = os.path.join(BASE_DIR, "eventos.csv")

LAGS = [1, 2, 3, 7, 14, 28]
ROLLING_WINDOWS = [7, 28, 90]

NUMERIC_FEATURES = [
    "dia_mes", "semana_anio", "trimestre", "es_fin_de_semana",
    "es_evento", "dias_hasta_proximo_evento", "dias_desde_evento_anterior",
    *[f"lag_{n}" for n in LAGS],
    *[f"rolling_mean_{n}" for n in ROLLING_WINDOWS],
    "rolling_std_7",
    "historico_promedio_producto",
]
CATEGORICAL_ENC_FEATURES = ["codigo_producto_enc", "dia_semana", "tipo_evento_activo_enc"]
MODEL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_ENC_FEATURES
TARGET_COLUMN = "unidades_vendidas"


def cargar_ventas_diarias() -> pd.DataFrame:
    path = os.path.join(PROCESSED_DIR, "ventas_diarias_producto.csv")
    df = pd.read_csv(path, parse_dates=["fecha"], dtype={"codigo_producto": str})
    return df[["fecha", "codigo_producto", "unidades_vendidas"]].copy()


def cargar_eventos() -> pd.DataFrame:
    columnas = ["fecha_inicio", "fecha_fin", "tipo_evento", "nombre_evento"]
    if not os.path.exists(EVENTOS_PATH):
        return pd.DataFrame(columns=columnas)
    df = pd.read_csv(EVENTOS_PATH)
    if df.empty:
        return pd.DataFrame(columns=columnas)
    df["fecha_inicio"] = pd.to_datetime(df["fecha_inicio"], errors="coerce")
    df["fecha_fin"] = pd.to_datetime(df["fecha_fin"], errors="coerce")
    df = df.dropna(subset=["fecha_inicio", "fecha_fin"])
    df["tipo_evento"] = df["tipo_evento"].astype(str).str.strip().str.lower()
    return df


def construir_panel_base(ventas: pd.DataFrame, fecha_max: pd.Timestamp | None = None) -> pd.DataFrame:
    """Expande a grilla completa producto x fecha, rellenando ceros donde no hubo venta."""
    productos = ventas["codigo_producto"].unique()
    fecha_min = ventas["fecha"].min()
    fecha_max = fecha_max or ventas["fecha"].max()
    fechas = pd.date_range(fecha_min, fecha_max, freq="D")

    idx = pd.MultiIndex.from_product([productos, fechas], names=["codigo_producto", "fecha"])
    panel = pd.DataFrame(index=idx).reset_index()

    panel = panel.merge(ventas, on=["codigo_producto", "fecha"], how="left")
    panel["unidades_vendidas"] = panel["unidades_vendidas"].fillna(0.0)
    return panel.sort_values(["codigo_producto", "fecha"]).reset_index(drop=True)


def agregar_features_calendario(panel: pd.DataFrame) -> pd.DataFrame:
    panel["dia_semana"] = panel["fecha"].dt.dayofweek
    panel["mes"] = panel["fecha"].dt.month
    panel["dia_mes"] = panel["fecha"].dt.day
    panel["semana_anio"] = panel["fecha"].dt.isocalendar().week.astype(int)
    panel["trimestre"] = panel["fecha"].dt.quarter
    panel["es_fin_de_semana"] = panel["dia_semana"].isin([5, 6]).astype(int)
    return panel


def agregar_features_evento(panel: pd.DataFrame, eventos: pd.DataFrame) -> pd.DataFrame:
    fechas_unicas = panel["fecha"].drop_duplicates().sort_values().reset_index(drop=True)

    es_evento_por_fecha = {}
    tipo_por_fecha = {}
    for f in fechas_unicas:
        activos = eventos[(eventos["fecha_inicio"] <= f) & (eventos["fecha_fin"] >= f)]
        es_evento_por_fecha[f] = 1 if not activos.empty else 0
        tipo_por_fecha[f] = activos.iloc[0]["tipo_evento"] if not activos.empty else "ninguno"

    inicios = eventos["fecha_inicio"].sort_values().to_numpy() if not eventos.empty else np.array([], dtype="datetime64[ns]")
    fines = eventos["fecha_fin"].sort_values().to_numpy() if not eventos.empty else np.array([], dtype="datetime64[ns]")

    def _dias_hasta_proximo(f):
        futuros = inicios[inicios >= np.datetime64(f)]
        if len(futuros) == 0:
            return 999
        return int((futuros.min() - np.datetime64(f)) / np.timedelta64(1, "D"))

    def _dias_desde_anterior(f):
        pasados = fines[fines <= np.datetime64(f)]
        if len(pasados) == 0:
            return 999
        return int((np.datetime64(f) - pasados.max()) / np.timedelta64(1, "D"))

    dias_hasta = {f: _dias_hasta_proximo(f) for f in fechas_unicas}
    dias_desde = {f: _dias_desde_anterior(f) for f in fechas_unicas}

    panel["es_evento"] = panel["fecha"].map(es_evento_por_fecha).astype(int)
    panel["tipo_evento_activo"] = panel["fecha"].map(tipo_por_fecha)
    panel["dias_hasta_proximo_evento"] = panel["fecha"].map(dias_hasta).astype(int)
    panel["dias_desde_evento_anterior"] = panel["fecha"].map(dias_desde).astype(int)
    return panel


def agregar_features_rezago(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.sort_values(["codigo_producto", "fecha"]).reset_index(drop=True)
    grouped = panel.groupby("codigo_producto")["unidades_vendidas"]
    for n in LAGS:
        panel[f"lag_{n}"] = grouped.shift(n)
    for n in ROLLING_WINDOWS:
        panel[f"rolling_mean_{n}"] = grouped.transform(lambda s, n=n: s.shift(1).rolling(n).mean())
    panel["rolling_std_7"] = grouped.transform(lambda s: s.shift(1).rolling(7).std())
    # Nivel histórico del producto (promedio expandido, sin ver el día actual): ancla
    # explícita para que el pronóstico recursivo no derive lejos del nivel real del
    # producto solo por depender de que el árbol separe bien 175 categorías.
    panel["historico_promedio_producto"] = grouped.transform(lambda s: s.shift(1).expanding().mean())
    return panel


def construir_panel(
    ventas: pd.DataFrame | None = None,
    eventos: pd.DataFrame | None = None,
    fecha_max: pd.Timestamp | None = None,
) -> pd.DataFrame:
    if ventas is None:
        ventas = cargar_ventas_diarias()
    if eventos is None:
        eventos = cargar_eventos()
    panel = construir_panel_base(ventas, fecha_max=fecha_max)
    panel = agregar_features_calendario(panel)
    panel = agregar_features_evento(panel, eventos)
    panel = agregar_features_rezago(panel)
    return panel


# --- Codificación de variables categóricas (consistente entre train y forecast) ---

def construir_encoders(panel: pd.DataFrame) -> dict:
    codigo_map = {c: i for i, c in enumerate(sorted(panel["codigo_producto"].unique()))}
    tipo_map = {t: i for i, t in enumerate(sorted(panel["tipo_evento_activo"].unique()))}
    if "ninguno" not in tipo_map:
        tipo_map["ninguno"] = len(tipo_map)
    return {"codigo_producto_map": codigo_map, "tipo_evento_map": tipo_map}


def aplicar_encoders(panel: pd.DataFrame, encoders: dict) -> pd.DataFrame:
    codigo_map = encoders["codigo_producto_map"]
    tipo_map = encoders["tipo_evento_map"]
    fallback_tipo = tipo_map.get("ninguno", -1)

    panel = panel.copy()
    panel["codigo_producto_enc"] = panel["codigo_producto"].map(codigo_map)
    panel["tipo_evento_activo_enc"] = panel["tipo_evento_activo"].map(tipo_map).fillna(fallback_tipo).astype(int)
    return panel


def guardar_encoders(encoders: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(encoders, f, ensure_ascii=False, indent=2)


def cargar_encoders(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --- Calibración por producto (post-modelo, se calcula una vez en train.py a
# partir de un backtest recursivo y se aplica igual en forecast.py) ---


def aplicar_calibracion(pred: np.ndarray, fila: pd.DataFrame, calibracion: dict | None) -> np.ndarray:
    """`fila` debe incluir la columna codigo_producto (no solo MODEL_FEATURES).
    Sin calibración, solo recorta a >= 0 (retrocompatible con versiones sin
    calibracion.json)."""
    pred = np.asarray(pred, dtype=float).copy()
    if not calibracion:
        return np.clip(pred, 0, None)

    inactivos = set(calibracion["productos_inactivos"])
    tercil_por_producto = calibracion["tercil_por_producto"]
    factores = calibracion["factores_por_tercil"]

    codigos = fila["codigo_producto"].astype(str).to_numpy()
    factor_por_fila = np.array([
        0.0 if c in inactivos else factores.get(tercil_por_producto.get(c, ""), 1.0)
        for c in codigos
    ])
    return np.clip(pred * factor_por_fila, 0, None)


def guardar_calibracion(calibracion: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(calibracion, f, ensure_ascii=False, indent=2)


def cargar_calibracion(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
