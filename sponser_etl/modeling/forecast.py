"""Pronóstico recursivo de demanda a 100 días por producto, usando una versión
entrenada del modelo (por defecto, la más reciente en versions/LATEST.txt).

Uso: python forecast.py (después de correr train.py al menos una vez).
Para cada día futuro (a partir del último día con datos reales), calcula
features de calendario/evento/rezago usando el historial real + lo ya
pronosticado, predice con el modelo guardado, y encadena el resultado
como insumo del siguiente día.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # sponser_etl/ (para load.py)

import lightgbm as lgb
import numpy as np
import pandas as pd

import features
import load
import versioning

OUT_DIR = features.PROCESSED_DIR
HORIZONTE_DIAS = 100


def predecir(modelo: dict, fila: pd.DataFrame) -> np.ndarray:
    """Predicción combinada, sin importar si `modelo` es hurdle o tweedie_directo,
    con la calibración por producto ya aplicada (si existe). `fila` debe incluir
    codigo_producto además de MODEL_FEATURES (la calibración es por producto)."""
    X = fila[features.MODEL_FEATURES]
    if modelo["tipo"] == "hurdle":
        p = modelo["clasificacion"].predict(X)
        q = modelo["regresion"].predict(X)
        pred = p * q
    else:
        pred = modelo["regresion"].predict(X)
    return features.aplicar_calibracion(pred, fila, modelo.get("calibracion"))


def forecast_recursivo(
    modelo: dict,
    encoders: dict,
    dias: int = HORIZONTE_DIAS,
    historial_inicial: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Pronostica `dias` días recursivamente a partir del último día del historial.

    Por defecto usa todo el historial real disponible (pronóstico "hacia el futuro").
    Se puede pasar `historial_inicial` truncado a una fecha de corte anterior para
    hacer un backtest: el modelo pronostica recursivamente sin ver los datos reales
    posteriores al corte, igual que pronosticaría hacia un futuro genuino.
    """
    historial = historial_inicial if historial_inicial is not None else features.cargar_ventas_diarias()
    eventos = features.cargar_eventos()
    ultimo_dia_real = historial["fecha"].max()

    predicciones = []
    for i in range(1, dias + 1):
        fecha_objetivo = ultimo_dia_real + pd.Timedelta(days=i)
        panel = features.construir_panel(ventas=historial, eventos=eventos, fecha_max=fecha_objetivo)
        panel = features.aplicar_encoders(panel, encoders)

        fila = panel[panel["fecha"] == fecha_objetivo].copy()
        pred = predecir(modelo, fila)
        fila["unidades_vendidas"] = pred

        predicciones.append(fila[["fecha", "codigo_producto", "unidades_vendidas"]].rename(
            columns={"unidades_vendidas": "unidades_pronosticadas"}
        ))
        historial = pd.concat(
            [historial, fila[["fecha", "codigo_producto", "unidades_vendidas"]]],
            ignore_index=True,
        )

        if i % 10 == 0 or i == dias:
            print(f"  pronosticado día {i}/{dias} ({fecha_objetivo.date()})")

    return pd.concat(predicciones, ignore_index=True)


def cargar_modelo(version: str | None = None):
    """Carga la versión pedida (o LATEST) y devuelve (version, modelo, encoders).

    `modelo` es un dict {"tipo": ..., "regresion": Booster} para tweedie_directo,
    o {"tipo": "hurdle", "clasificacion": Booster, "regresion": Booster} para hurdle
    — soporta versiones antiguas (v1-v4, sin config.json con tipo_modelo) asumiendo
    tweedie_directo con model_lightgbm.txt.
    """
    version = versioning.resolver_version(version)
    vdir = versioning.version_dir(version)

    config_path = os.path.join(vdir, "config.json")
    tipo = "tweedie_directo"
    if os.path.exists(config_path):
        import json
        with open(config_path, "r", encoding="utf-8") as f:
            tipo = json.load(f).get("tipo_modelo", "tweedie_directo")

    encoders = features.cargar_encoders(os.path.join(vdir, "encoders.json"))

    if tipo == "hurdle":
        clas_path = os.path.join(vdir, "model_clasificacion.txt")
        reg_path = os.path.join(vdir, "model_regresion.txt")
        if not (os.path.exists(clas_path) and os.path.exists(reg_path)):
            raise FileNotFoundError(f"Faltan archivos de modelo hurdle de la versión {version} en {vdir}.")
        modelo = {
            "tipo": "hurdle",
            "clasificacion": lgb.Booster(model_file=clas_path),
            "regresion": lgb.Booster(model_file=reg_path),
        }
    else:
        model_path = os.path.join(vdir, "model_lightgbm.txt")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"No se encontró el modelo de la versión {version} en {model_path}.")
        modelo = {"tipo": "tweedie_directo", "regresion": lgb.Booster(model_file=model_path)}

    modelo["calibracion"] = features.cargar_calibracion(os.path.join(vdir, "calibracion.json"))

    return version, modelo, encoders


def main() -> int:
    version, modelo, encoders = cargar_modelo()
    print(f"Usando versión: {version} (tipo: {modelo['tipo']})")

    print(f"Pronosticando {HORIZONTE_DIAS} días hacia adelante...")
    forecast_df = forecast_recursivo(modelo, encoders, dias=HORIZONTE_DIAS)

    resumen_sku = (
        forecast_df.groupby("codigo_producto")["unidades_pronosticadas"]
        .mean()
        .reset_index()
        .rename(columns={"unidades_pronosticadas": "daily_demand"})
        .sort_values("daily_demand", ascending=False)
    )

    load.guardar_tablas(
        {
            "forecast_demanda_producto": forecast_df,
            "demanda_promedio_sku": resumen_sku,
        },
        OUT_DIR,
    )

    print(f"\nforecast_demanda_producto.csv: {len(forecast_df):,} filas -> {OUT_DIR}")
    print(f"demanda_promedio_sku.csv: {len(resumen_sku):,} filas -> {OUT_DIR}")
    print(f"Rango pronosticado: {forecast_df['fecha'].min().date()} -> {forecast_df['fecha'].max().date()}")
    print(f"(generado con la versión del modelo: {version})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
